"""Send a new Voice Companion browser extension to the Chrome Web Store.

Runs in the release workflow after a release is published on main, and in the
daily store check. It:
  1. signs in to Google with no stored key (GitHub OIDC and Workload Identity
     Federation), or with a service account key kept in a GitHub secret;
  2. reads the version the store has (published and in review);
  3. uploads the extension only when its manifest version is higher;
  4. submits it for review, so Chrome updates every copy on its own.

It always exits 0: the release is already public when it runs. Each result is
a GitHub annotation: submitted for review, skipped and why, or not sent and why.
One-time setup: tools/chrome-store-publishing.md.

Chrome Web Store API v2 (v1 ended on October 15, 2026):
https://developer.chrome.com/docs/webstore/api/reference/rest

Usage:
  python tools/chrome_store_publish.py --extension voice_companion/regular_browser_extension

Settings (GitHub repository variables, and one optional secret):
  CWS_PUBLISHER_ID                 Chrome Web Store publisher ID
  CWS_WORKLOAD_IDENTITY_PROVIDER   projects/NUMBER/locations/global/workloadIdentityPools/POOL/providers/PROVIDER
  CWS_SERVICE_ACCOUNT              service account email added on the store's Account page
  CWS_SERVICE_ACCOUNT_KEY          (secret, fallback only) the service account's JSON key
"""
import argparse
import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

STORE_EXTENSION_ID = 'ijojiipjhcfaajafdbinnbehcenfchcc'
SCOPE = 'https://www.googleapis.com/auth/chromewebstore'
SETUP_GUIDE = 'tools/chrome-store-publishing.md'
# Files in the extension folder that are for people or tests, not for Chrome.
NOT_PACKAGED = {'README.md', 'SETUP.txt', 'extension-id.txt', 'test-worker.cjs'}
IN_PROGRESS = {'IN_PROGRESS', 'UPLOAD_IN_PROGRESS'}  # The reference pages use both names.
TIMEOUT = 120


@dataclass
class Endpoints:
    store: str = 'https://chromewebstore.googleapis.com'
    upload: str = 'https://chromewebstore.googleapis.com'
    sts: str = 'https://sts.googleapis.com/v1/token'
    iam: str = 'https://iamcredentials.googleapis.com'
    oauth: str = 'https://oauth2.googleapis.com/token'


class StoreError(Exception):
    """A step that did not work, said plainly. Never contains a token or key."""


class Skip(Exception):
    """Nothing to do, or not set up yet."""


# Versions ----------------------------------------------------------------------
def version_key(text):
    """Chrome versions are one to four whole numbers separated by dots."""
    parts = str(text).strip().split('.')
    if not 1 <= len(parts) <= 4 or not all(part.isdigit() for part in parts):
        raise ValueError(f'"{text}" is not a Chrome extension version.')
    return tuple(int(part) for part in parts) + (0,) * (4 - len(parts))


def newer(candidate, than):
    return version_key(candidate) > version_key(than)


# Package -----------------------------------------------------------------------
def package_files(folder):
    """The files Chrome needs, as paths relative to the extension folder."""
    folder = Path(folder)
    files = []
    for path in sorted(folder.rglob('*')):
        relative = path.relative_to(folder)
        if not path.is_file() or any(part.startswith('.') for part in relative.parts):
            continue
        if relative.as_posix() in NOT_PACKAGED or relative.name.startswith('test'):
            continue
        files.append(relative)
    return files


def read_manifest(folder):
    return json.loads((Path(folder) / 'manifest.json').read_text(encoding='utf-8'))


def build_package(folder):
    """A zip of the extension. The store refuses a manifest "key", so it is left out here only."""
    folder = Path(folder)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as package:
        for relative in package_files(folder):
            data = (folder / relative).read_bytes()
            if relative.as_posix() == 'manifest.json':
                manifest = json.loads(data.decode('utf-8'))
                manifest.pop('key', None)
                data = (json.dumps(manifest, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
            info = zipfile.ZipInfo(relative.as_posix(), date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            package.writestr(info, data)
    return buffer.getvalue()


# HTTP --------------------------------------------------------------------------
def call(what, method, url, token=None, body=None, content_type=None):
    headers = {'Accept': 'application/json', 'User-Agent': 'VoiceCompanionRelease'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if isinstance(body, (dict, list)):
        body, content_type = json.dumps(body).encode('utf-8'), 'application/json'
    if content_type:
        headers['Content-Type'] = content_type
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            text = response.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as error:
        raise StoreError(f'{what} failed: HTTP {error.code}. {error_text(error)}'.strip()) from None
    except (urllib.error.URLError, OSError) as error:
        reason = getattr(error, 'reason', error)
        raise StoreError(f'{what} failed: {urllib.parse.urlparse(url).netloc} could not be reached ({reason}).') from None
    try:
        return json.loads(text) if text.strip() else {}
    except ValueError:
        raise StoreError(f'{what} failed: the answer was not JSON.') from None


def error_text(error):
    try:
        data = json.loads(error.read().decode('utf-8', 'replace'))
    except (ValueError, OSError):
        return ''
    if isinstance(data.get('error'), dict):
        return str(data['error'].get('message', ''))[:500]
    return str(data.get('error_description') or data.get('message') or data.get('error') or '')[:500]


# Sign-in -----------------------------------------------------------------------
def missing_setup(env):
    """None when sign-in can be tried, or the reason it cannot."""
    if not env.get('CWS_PUBLISHER_ID'):
        return 'CWS_PUBLISHER_ID is not set'
    provider, account = env.get('CWS_WORKLOAD_IDENTITY_PROVIDER'), env.get('CWS_SERVICE_ACCOUNT')
    if provider or account:
        if not provider:
            return 'CWS_SERVICE_ACCOUNT is set but CWS_WORKLOAD_IDENTITY_PROVIDER is not'
        if not account:
            return 'CWS_WORKLOAD_IDENTITY_PROVIDER is set but CWS_SERVICE_ACCOUNT is not'
        if env.get('ACTIONS_ID_TOKEN_REQUEST_URL') and env.get('ACTIONS_ID_TOKEN_REQUEST_TOKEN'):
            return None
        if not env.get('CWS_SERVICE_ACCOUNT_KEY'):
            return 'GitHub gave no sign-in token. The workflow job needs "id-token: write" permission'
        return None
    if env.get('CWS_SERVICE_ACCOUNT_KEY'):
        return None
    return 'no sign-in is set: set CWS_WORKLOAD_IDENTITY_PROVIDER and CWS_SERVICE_ACCOUNT, or the CWS_SERVICE_ACCOUNT_KEY secret'


def federated_token(env, endpoints):
    """GitHub OIDC token -> Google STS -> short-lived token for the service account."""
    provider, account = env['CWS_WORKLOAD_IDENTITY_PROVIDER'].strip(), env['CWS_SERVICE_ACCOUNT'].strip()
    audience = 'https://iam.googleapis.com/' + provider  # The provider's default allowed audience.
    separator = '&' if '?' in env['ACTIONS_ID_TOKEN_REQUEST_URL'] else '?'
    github = call('Getting the GitHub sign-in token', 'GET',
                  env['ACTIONS_ID_TOKEN_REQUEST_URL'] + separator + 'audience=' + urllib.parse.quote(audience, safe=''),
                  token=env['ACTIONS_ID_TOKEN_REQUEST_TOKEN'])
    if not github.get('value'):
        raise StoreError('Getting the GitHub sign-in token failed: GitHub sent no token.')
    exchanged = call('Signing in to Google with the GitHub token (Workload Identity Federation)', 'POST', endpoints.sts, body={
        'audience': '//iam.googleapis.com/' + provider,
        'grantType': 'urn:ietf:params:oauth:grant-type:token-exchange',
        'requestedTokenType': 'urn:ietf:params:oauth:token-type:access_token',
        'scope': 'https://www.googleapis.com/auth/cloud-platform',
        'subjectTokenType': 'urn:ietf:params:oauth:token-type:jwt',
        'subjectToken': github['value']})
    if not exchanged.get('access_token'):
        raise StoreError('Signing in to Google failed: Google sent no token.')
    url = f'{endpoints.iam}/v1/projects/-/serviceAccounts/{urllib.parse.quote(account, safe='@')}:generateAccessToken'
    issued = call('Signing in as the store service account', 'POST', url, token=exchanged['access_token'],
                  body={'scope': [SCOPE], 'lifetime': '1800s'})
    if not issued.get('accessToken'):
        raise StoreError('Signing in as the store service account failed: Google sent no token.')
    return issued['accessToken']


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def sign_rs256(private_key_pem, message):
    """RSA SHA-256 signature, with the cryptography package or the openssl program."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        pass
    else:
        try:
            key = serialization.load_pem_private_key(private_key_pem.encode('utf-8'), password=None)
            return key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        except (ValueError, TypeError):
            raise StoreError('The service account key could not be read. Make a new JSON key and save it again.') from None
    openssl = shutil.which('openssl') or next((str(path) for path in (
        Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Git' / 'usr' / 'bin' / 'openssl.exe',
        Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Git' / 'mingw64' / 'bin' / 'openssl.exe')
        if path.is_file()), None)
    if not openssl:
        raise StoreError('The service account key cannot be used here: neither the cryptography package nor openssl is available.')
    with tempfile.TemporaryDirectory(prefix='vc-cws-') as folder:
        key_path = Path(folder) / 'key.pem'
        descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(private_key_pem)
        result = subprocess.run([openssl, 'dgst', '-sha256', '-sign', str(key_path)], input=message, capture_output=True, timeout=60)
    if result.returncode or not result.stdout:
        raise StoreError('The service account key could not be read. Make a new JSON key and save it again.')
    return result.stdout


def key_token(env, endpoints):
    """Fallback: a JWT signed with the service account's JSON key."""
    try:
        key = json.loads(env['CWS_SERVICE_ACCOUNT_KEY'])
        email, private_key = key['client_email'], key['private_key']
        if not isinstance(email, str) or not isinstance(private_key, str):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        raise StoreError('The CWS_SERVICE_ACCOUNT_KEY secret is not a complete service account JSON key. '
                         'Paste the whole key file into the secret again.') from None
    audience = key.get('token_uri') or 'https://oauth2.googleapis.com/token'
    now = int(time.time())
    header = {'alg': 'RS256', 'typ': 'JWT'}
    if isinstance(key.get('private_key_id'), str):
        header['kid'] = key['private_key_id']
    unsigned = b64(json.dumps(header).encode()) + '.' + b64(json.dumps(
        {'iss': email, 'scope': SCOPE, 'aud': audience, 'iat': now, 'exp': now + 3600}).encode())
    assertion = unsigned + '.' + b64(sign_rs256(private_key, unsigned.encode('ascii')))
    form = urllib.parse.urlencode({'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer', 'assertion': assertion}).encode()
    issued = call('Signing in with the service account key', 'POST', endpoints.oauth, body=form,
                  content_type='application/x-www-form-urlencoded')
    if not issued.get('access_token'):
        raise StoreError('Signing in with the service account key failed: Google sent no token.')
    return issued['access_token']


def access_token(env, endpoints):
    if env.get('CWS_WORKLOAD_IDENTITY_PROVIDER') and env.get('CWS_SERVICE_ACCOUNT') and env.get('ACTIONS_ID_TOKEN_REQUEST_URL'):
        return federated_token(env, endpoints)
    return key_token(env, endpoints)


# Store -------------------------------------------------------------------------
def channel_versions(revision):
    if not isinstance(revision, dict):
        return []
    channels = revision.get('distributionChannels') or []
    return [channel['crxVersion'] for channel in channels if isinstance(channel, dict) and channel.get('crxVersion')]


def highest(versions):
    valid = []
    for version in versions:
        try:
            version_key(version)
        except ValueError:
            continue
        valid.append(version)
    return max(valid, key=version_key) if valid else None


def run(extension, env, endpoints=None, output=print, poll_seconds=5):
    """Returns (status, message). status: submitted, uploaded, skipped or failed. Never raises."""
    endpoints = endpoints or Endpoints()
    try:
        status, message = _run(Path(extension), env, endpoints, output, poll_seconds)
    except Skip as reason:
        status, message = 'skipped', str(reason)
    except StoreError as error:
        status, message = 'failed', str(error)
    except Exception as error:  # A surprise must still name itself, and never fail the release.
        status, message = 'failed', f'The store step stopped unexpectedly: {type(error).__name__}: {error}'
    if status == 'failed':
        message += ' The release itself is published. The next release, or the daily store check, tries again.'
    title = {'submitted': 'submitted for review', 'uploaded': 'uploaded', 'skipped': 'skipped', 'failed': 'not sent'}[status]
    annotate(output, 'warning' if status == 'failed' else 'notice', 'Chrome Web Store - ' + title, message)
    summary = env.get('GITHUB_STEP_SUMMARY')
    if summary:
        try:
            with open(summary, 'a', encoding='utf-8') as handle:
                handle.write(f'## Chrome Web Store: {title}\n\n{message}\n')
        except OSError:
            pass
    return status, message


def _run(extension, env, endpoints, output, poll_seconds):
    manifest = read_manifest(extension)
    local = manifest.get('version', '')
    version_key(local)
    reason = missing_setup(env)
    if reason:
        raise Skip(f'Extension {local} was not sent to the Chrome Web Store: {reason}. '
                   f'Chrome keeps the version the store already has. One-time setup: {SETUP_GUIDE}.')
    publisher = urllib.parse.quote(env['CWS_PUBLISHER_ID'].strip(), safe='')
    item = urllib.parse.quote(env.get('CWS_EXTENSION_ID', '').strip() or STORE_EXTENSION_ID, safe='')
    token = access_token(env, endpoints)
    output('::add-mask::' + token)
    name = f'publishers/{publisher}/items/{item}'

    status = call('Reading the version in the Chrome Web Store', 'GET', f'{endpoints.store}/v2/{name}:fetchStatus', token=token)
    published = highest(channel_versions(status.get('publishedItemRevisionStatus')))
    submitted_revision = status.get('submittedItemRevisionStatus') or {}
    submitted = highest(channel_versions(submitted_revision))
    submitted_state = submitted_revision.get('state') if isinstance(submitted_revision, dict) else None
    output(f'Extension here: {local}. Store published: {published or "none"}. '
           f'Store submitted: {submitted or "none"} ({submitted_state or "none"}).')
    if published and not newer(local, published):
        raise Skip(f'The store already has extension {published}. This release has {local}, so nothing was uploaded.')
    if submitted and submitted_state in ('PENDING_REVIEW', 'STAGED'):
        waiting = 'waiting for Chrome Web Store review' if submitted_state == 'PENDING_REVIEW' else 'approved and waiting to be published'
        if not newer(local, submitted):
            raise Skip(f'Extension {submitted} is already {waiting}. Nothing was uploaded.')
        # Cancelling it would restart review on every release; wait for this one to finish instead.
        raise Skip(f'Extension {submitted} is still in Chrome Web Store review. {local} will be uploaded '
                   'by the daily store check, or the next release, once that review ends.')

    package = build_package(extension)
    output(f'Uploading extension {local} ({len(package)} bytes).')
    uploaded = call('Uploading the extension', 'POST', f'{endpoints.upload}/upload/v2/{name}:upload', token=token,
                    body=package, content_type='application/zip')
    state = uploaded.get('uploadState')
    for _ in range(36):
        if state not in IN_PROGRESS:
            break
        time.sleep(poll_seconds)
        # Not reported yet counts as still processing.
        state = call('Checking the upload', 'GET', f'{endpoints.store}/v2/{name}:fetchStatus',
                     token=token).get('lastAsyncUploadState') or 'IN_PROGRESS'
    if state != 'SUCCEEDED':
        raise StoreError(f'The store did not accept the upload of extension {local} (upload state: {state or "none"}).')

    published_now = call('Submitting the extension for review', 'POST', f'{endpoints.store}/v2/{name}:publish', token=token, body={})
    result = published_now.get('state')
    for warning in ((published_now.get('warningInfo') or {}).get('warnings') or []):
        if isinstance(warning, dict):
            output(f"Store warning: {warning.get('reason', '')}: {warning.get('description', '')}")
    if result == 'PENDING_REVIEW':
        return 'submitted', (f'Extension {local} was uploaded and submitted for review. After Google approves it, '
                             'Chrome updates every copy on its own.')
    if result in ('PUBLISHED', 'PUBLISHED_TO_TESTERS', 'STAGED'):
        return 'submitted', f'Extension {local} was uploaded and the store says {result}.'
    return 'uploaded', (f'Extension {local} was uploaded, but the store answered {result or "nothing"} to the review request. '
                        'Check the item in the Chrome Web Store developer dashboard.')


def annotate(output, level, title, message):
    escape = lambda text: text.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
    title = escape(title).replace(':', '%3A').replace(',', '%2C')
    output(f'::{level} title={title}::{escape(message)}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--extension', default=str(Path(__file__).resolve().parents[1] / 'voice_companion' / 'regular_browser_extension'))
    args = parser.parse_args(argv)
    run(args.extension, os.environ, output=lambda line: print(line, flush=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
