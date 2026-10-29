"""Run Voice Companion's tests on Linux the way the Windows build runs them.

Usage (from the repository root):
  python3 tools/run_checks.py                 # release checks in ONE process, like build-windows.ps1
  python3 tools/run_checks.py --each          # every test file in its own process
  python3 tools/run_checks.py --live          # also the real-browser tests, in Chromium
  python3 tools/run_checks.py --compare main  # same checks on origin/main; report only NEW failures

Every run also checks command routing against tools/routing-snapshot.json
(see tools/routing_snapshot.py): any phrase that now behaves differently fails.
It also runs the repository checks in tools/test_*.py, for example
tools/test_no_tables.py, which keeps tables out of every document.

Windows-only tests skip themselves on Linux, and a few fail here for environment
reasons (Windows fonts, eSpeak binaries, missing packages). --compare separates
those from real regressions: only failures that main does not have are reported.
The Windows build on GitHub remains the final word before anything is published.
"""
import argparse
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUBS = Path(__file__).resolve().parent / 'linux-test-stubs'
FAILURE = re.compile(r'^(?:FAIL|ERROR): (\S+) \((\S+)\)', re.M)
def release_modules(source):
    """The test files build-windows.ps1 runs together as its release checks."""
    script = (source / 'build-windows.ps1').read_text(encoding='utf-8')
    match = re.search(r"-ArgumentList @\('-m','unittest','-q',(.*?)\)", script, re.S)
    if not match:
        raise SystemExit('Could not find the release check list in build-windows.ps1.')
    return re.findall(r"'([\w.]+)'", match[1])


def environment(live):
    env = os.environ.copy()
    paths = []
    # Stand-ins only for packages that are not really installed.
    if not importlib.util.find_spec('vosk') or not importlib.util.find_spec('websockets'):
        paths.append(str(STUBS))
    env['PYTHONPATH'] = os.pathsep.join(paths + [env.get('PYTHONPATH', '')]).strip(os.pathsep)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['VOICE_COMPANION_DATA_DIR'] = tempfile.mkdtemp(prefix='vc-test-data-')
    return env


def run(source, args):
    source = Path(source) / 'voice_companion'
    env = environment(args.live)
    failures, totals = set(), []
    groups = ([[m] for m in sorted(p.stem for p in source.glob('test_*.py'))] + [['smoke_tests']]
              if args.each else [release_modules(source)])
    if args.live:
        groups.append(['test_web_live', 'test_consumer_web', 'test_web_semantics', 'test_web_fixes', 'test_web_modes_live', 'test_web_history', 'test_web_fields', 'test_web_media_live', 'test_youtube_live'])
    for modules in groups:
        result = subprocess.run([sys.executable, '-m', 'unittest', '-q', *modules], cwd=source, env=env,
                                capture_output=True, text=True, timeout=3600)
        output = result.stdout + result.stderr
        failures.update(f'{test} ({where})' for test, where in FAILURE.findall(output))
        ran = re.search(r'^Ran (\d+) tests?', output, re.M)
        totals.append(int(ran[1]) if ran else 0)
    return failures, sum(totals)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--each', action='store_true', help='run each test file in its own process')
    parser.add_argument('--live', action='store_true', help='include real-browser tests (Chromium)')
    parser.add_argument('--compare', metavar='BRANCH', help='compare with this branch on origin, e.g. main')
    args = parser.parse_args()
    failures, total = run(ROOT, args)
    print(f'Ran {total} tests. {len(failures)} failing.')
    # Command routing: every phrase on every screen must behave as recorded.
    routing = subprocess.run([sys.executable, str(Path(__file__).with_name('routing_snapshot.py')), '--check'],
                             cwd=ROOT, env=environment(False), capture_output=True, text=True, timeout=1800)
    print(routing.stdout.strip().splitlines()[-1] if routing.stdout.strip() else 'Routing snapshot did not run.')
    if routing.returncode:
        print(routing.stdout[-4000:] + routing.stderr[-2000:])
        print('Command routing changed. If that is intended, run tools/routing_snapshot.py --update '
              'and explain the changed records in the pull request.')
        sys.exit(1)
    # Repository checks (tools/test_*.py), such as no tables in any document.
    tools = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tools', '-p', 'test_*.py'],
                           cwd=ROOT, env=environment(False), capture_output=True, text=True, timeout=600)
    tools_output = (tools.stdout + tools.stderr).strip()
    print('Repository checks: ' + ('passed.' if not tools.returncode else 'FAILED.'))
    if tools.returncode:
        print(tools_output[-6000:])
        sys.exit(1)
    if not args.compare:
        for name in sorted(failures): print('  ' + name)
        sys.exit(1 if failures else 0)
    with tempfile.TemporaryDirectory(prefix='vc-compare-') as folder:
        subprocess.run(['git', 'fetch', '-q', 'origin', args.compare], cwd=ROOT, check=True)
        subprocess.run(['git', 'worktree', 'add', '-q', '--detach', folder, 'origin/' + args.compare], cwd=ROOT, check=True)
        try:
            baseline, _ = run(folder, args)
        finally:
            subprocess.run(['git', 'worktree', 'remove', '--force', folder], cwd=ROOT)
    new = sorted(failures - baseline)
    fixed = sorted(baseline - failures)
    print(f'{args.compare} has {len(baseline)} failing here (environment-only on Linux).')
    for name in fixed: print('  now passing: ' + name)
    for name in new: print('  NEW FAILURE: ' + name)
    print('No new failures.' if not new else f'{len(new)} new failure(s). Fix these before opening a pull request.')
    sys.exit(1 if new else 0)


if __name__ == '__main__':
    main()
