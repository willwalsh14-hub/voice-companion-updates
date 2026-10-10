"""Publish only a checked Windows build using the trainer's GitHub CLI session."""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from app_updates import version_key
from update_publisher import release_manifest
from guide_files import GUIDE_NAMES, guide_sources, published_name
from release_documents import load_notes, validate, STEM

ROOT = Path(__file__).parent


LOG_PATH = None
PROGRESS_INTERVAL = 20


def progress(message):
    print(message, flush=True)
    if LOG_PATH is not None:
        with Path(LOG_PATH).open('a', encoding='utf-8') as log:
            log.write(time.strftime('%H:%M:%S') + ' ' + message + '\n')


def operation_label(args):
    if args[0] == 'api': return 'Checking GitHub repository or release status'
    action = args[1]
    if action == 'upload': return 'Uploading ' + Path(args[3]).name
    if action == 'download': return 'Downloading for verification: ' + args[args.index('--pattern')+1]
    return {'create':'Creating the draft release', 'edit':'Publishing the verified release as latest'}.get(action, 'Contacting GitHub')


def gh_call(gh, *args, missing_ok=False):
    label = operation_label(args)
    progress(label + '.')
    started = time.monotonic()
    with subprocess.Popen([str(gh), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding='utf-8', errors='replace') as process:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=PROGRESS_INTERVAL)
                break
            except subprocess.TimeoutExpired:
                elapsed = int(time.monotonic() - started)
                progress(f'Still waiting: {label}. {elapsed} seconds elapsed. Keep this window open.')
        if process.returncode:
            if missing_ok and 'HTTP 404' in stderr: return None
            raise RuntimeError(label + ' failed. Check sign-in, repository permissions, internet access, and retry publishing.')
    progress(label + ' completed.')
    return stdout


def publish(setup, gh='gh', root=ROOT):
    root = Path(root)
    version = re.search(r"APP_VERSION = '([^']+)'", (root/'companion.py').read_text())[1]
    feed = json.loads((root/'update-settings.json').read_text())['manifest_url']
    match = re.fullmatch(r'https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/releases/latest/download/voice-companion-update\.json', feed)
    if not match:
        raise ValueError('Automatic publishing requires a configured GitHub release feed.')
    repo = match[1]; tag = 'v' + version
    url = f'https://github.com/{repo}/releases/download/{tag}/VoiceCompanion-Setup.exe'
    progress('Preparing release '+version+'. Checking the local installer and successful-build record.')
    manifest = release_manifest(setup, version, url)
    receipt = json.loads((root/'VoiceCompanion-Build-Passed.json').read_text(encoding='utf-8-sig'))
    if receipt != {'version':version, 'sha256':manifest['sha256'], 'size':manifest['size']}:
        raise ValueError('This installer does not match the successful Windows build checks. Rebuild it before publishing.')
    load_notes(root)
    validate(root)
    guide_sources([root], expected_version=version)
    documentation = [root/name for name in GUIDE_NAMES]
    if not all(path.is_file() for path in documentation):
        raise ValueError('All manual and release-note formats are required before publishing.')
    metadata = json.loads(gh_call(gh, 'api', f'repos/{repo}'))
    # Workflow installation tokens may omit the permissions object. GitHub
    # still enforces write authorization when creating or uploading the draft.
    permissions = metadata.get('permissions')
    workflow_token = os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('GITHUB_REPOSITORY') == repo
    if metadata.get('private') is not False:
        raise ValueError('Publishing requires a public repository.')
    if not workflow_token and permissions is not None and not permissions.get('push'):
        raise ValueError('Publishing requires repository write permission.')
    if workflow_token:
        progress('Using the repository workflow token. GitHub will enforce release write permission on the draft upload.')
    latest = gh_call(gh, 'api', f'repos/{repo}/releases/latest', missing_ok=True)
    if latest:
        latest_tag = json.loads(latest)['tag_name']
        if version_key(latest_tag.removeprefix('v')) > version_key(version):
            raise ValueError('A newer release is already published. Increase the version before building.')
    existing = gh_call(gh, 'api', f'repos/{repo}/releases/tags/{tag}', missing_ok=True)
    if existing and not json.loads(existing)['draft']:
        raise ValueError('This version is already published. Published installers are never overwritten; increase the version and rebuild.')
    output = root/'release-upload'; output.mkdir(exist_ok=True)
    installer = output/'VoiceCompanion-Setup.exe'; shutil.copy2(setup, installer)
    manifest_path = output/'voice-companion-update.json'
    manifest_path.write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    source = root/'espeak'/'espeak-ng-1.52.0-source.tar.gz'
    license_path = root/'espeak'/'COPYING'
    if not source.is_file() or not license_path.is_file():
        raise ValueError('The bundled eSpeak source and license are required for distribution.')
    notes = output/'release-notes.txt'
    notes.write_text((root/(STEM+'.txt')).read_text(encoding='utf-8'), encoding='utf-8')
    if not existing:
        gh_call(gh, 'release', 'create', tag, '--repo', repo, '--draft', '--title', 'Voice Companion '+version, '--notes-file', str(notes))
    else:
        gh_call(gh, 'release', 'edit', tag, '--repo', repo, '--notes-file', str(notes))
    assets = [installer, manifest_path, source, license_path]
    for document in documentation:
        safe_name = re.sub('-+', '-', re.sub(r'[^A-Za-z0-9._-]+', '-', published_name(document.name,version)))
        copy = output/safe_name
        shutil.copy2(document, copy)
        assets.append(copy)
    for asset in assets:
        progress(f'Upload file: {asset.name}. Size: {asset.stat().st_size / (1024*1024):.1f} MB.')
        gh_call(gh, 'release', 'upload', tag, str(asset), '--repo', repo, '--clobber')
    # Verify every uploaded byte while still a draft. Incomplete releases stay invisible to clients.
    with tempfile.TemporaryDirectory() as folder:
        for asset in assets:
            gh_call(gh, 'release', 'download', tag, '--repo', repo, '--pattern', asset.name, '--dir', folder)
            downloaded = Path(folder)/asset.name
            progress('Checking downloaded checksum: '+asset.name+'.')
            with asset.open('rb') as local, downloaded.open('rb') as remote:
                if hashlib.file_digest(local, 'sha256').digest() != hashlib.file_digest(remote, 'sha256').digest():
                    raise ValueError('Uploaded files did not pass verification. The release remains a draft; retry publishing.')
    gh_call(gh, 'release', 'edit', tag, '--repo', repo, '--draft=false', '--prerelease=false', '--latest')
    visible = json.loads(gh_call(gh, 'api', f'repos/{repo}/releases/latest'))
    if visible['tag_name'] != tag or visible['draft'] or visible['prerelease']:
        raise RuntimeError('Could not verify this release as latest. Check GitHub before retrying.')
    confirmation = {'version':version, 'sha256':manifest['sha256'], 'url':visible['html_url']}
    temporary = root/'VoiceCompanion-Published.tmp'
    temporary.write_text(json.dumps(confirmation), encoding='utf-8')
    temporary.replace(root/'VoiceCompanion-Published.json')
    return visible['html_url']


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--setup', required=True);parser.add_argument('--gh', default='gh')
    args=parser.parse_args()
    global LOG_PATH
    LOG_PATH = ROOT/'VoiceCompanion-Publish-Log.txt'
    LOG_PATH.write_text('Voice Companion publishing progress\n', encoding='utf-8')
    try:
        progress('PUBLISHED AND VERIFIED: '+publish(args.setup, args.gh))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        progress('BUILD PASSED, PUBLISHING FAILED: '+str(exc))
        progress('The local installer remains available. Run the installer builder again to retry; it includes sign-in and publishing.')
        return 1

if __name__ == '__main__': raise SystemExit(main())

