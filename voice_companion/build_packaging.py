"""Run PyInstaller with an argument vector; PowerShell never flattens its options."""
import json
from pathlib import Path
import subprocess
import sys


def package(options_file, output_log, error_log, runner=subprocess.run):
    options = json.loads(Path(options_file).read_text(encoding='utf-8-sig'))
    if not isinstance(options, list) or not all(isinstance(x, str) for x in options):
        raise ValueError('Packaging options must be an array of strings')
    with Path(output_log).open('w', encoding='utf-8') as output, Path(error_log).open('w', encoding='utf-8') as errors:
        result = runner([sys.executable, '-m', 'PyInstaller', *options, 'companion.py'],
                        cwd=Path(__file__).resolve().parent, stdout=output, stderr=errors, shell=False)
    return result.returncode


if __name__ == '__main__':
    try:
        raise SystemExit(package(*sys.argv[1:]))
    except Exception as exc:
        print('Packaging launcher failed: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
