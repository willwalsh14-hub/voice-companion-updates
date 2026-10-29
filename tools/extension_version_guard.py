"""Fail when the browser extension changed but its manifest version did not go up.

The Chrome Web Store refuses a package whose version is not higher, so users'
Chrome would never get the change. Files Chrome does not receive (notes and
tests, see chrome_store_publish.NOT_PACKAGED) need no new version.

Usage (from the repository root):
  python tools/extension_version_guard.py --base origin/main
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

from chrome_store_publish import NOT_PACKAGED, newer

FOLDER = 'voice_companion/regular_browser_extension'


class GuardError(Exception):
    pass


def git(root, *args):
    result = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True)
    if result.returncode:
        raise GuardError(f'git {" ".join(args)} failed: {result.stderr.strip()}')
    return result.stdout


def packaged(path):
    relative = PurePosixPath(path).relative_to(FOLDER)
    return relative.as_posix() not in NOT_PACKAGED and not relative.name.startswith(('test', '.'))


def problem(root, base):
    """None when all is well, or a plain message naming what changed."""
    git(root, 'rev-parse', '--verify', '-q', base + '^{commit}')
    changed = set(git(root, 'diff', '--name-only', base, '--', FOLDER).split())
    changed |= set(git(root, 'ls-files', '--others', '--exclude-standard', '--', FOLDER).split())
    changed = sorted(path for path in changed if packaged(path))
    if not changed:
        return None
    try:
        before = json.loads(git(root, 'show', f'{base}:{FOLDER}/manifest.json'))['version']
    except GuardError:
        return None  # The extension is new on this branch.
    now = json.loads((Path(root) / FOLDER / 'manifest.json').read_text(encoding='utf-8'))['version']
    if newer(now, before):
        return None
    names = ', '.join(PurePosixPath(path).name for path in changed)
    return (f'The browser extension changed ({names}) but its version is still {now}, '
            f'and {base} has {before}. Raise "version" in {FOLDER}/manifest.json above {before}, '
            'or the Chrome Web Store will refuse it and Chrome will not update.')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--base', default='origin/main')
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    try:
        message = problem(args.root, args.base)
    except GuardError as error:
        message = f'The extension version could not be checked against {args.base}: {error}'
    if message is None:
        print('Browser extension version check passed.')
        return 0
    if os.environ.get('GITHUB_ACTIONS'):
        clean = message.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
        print('::error title=Browser extension version not raised::' + clean)
    else:
        print(message)
    return 1


if __name__ == '__main__':
    sys.exit(main())
