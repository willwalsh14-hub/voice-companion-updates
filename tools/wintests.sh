#!/bin/bash
# Run real Tk window tests on Linux, one test per process, under a virtual screen.
# usage: tools/wintests.sh <outfile> <test names...>   (run from the repository root)
# The screen size can be changed with XRES, for example XRES=800x600x24.
set -u
root=$(cd "$(dirname "$0")/.." && pwd); out=$1; shift
venv=/tmp/vc-tkvenv
if [ ! -x "$venv/bin/python" ]; then
  uv python install 3.12 >/dev/null 2>&1 || pip install -q --break-system-packages uv >/dev/null 2>&1 && uv python install 3.12 >/dev/null 2>&1
  "$(uv python find 3.12)" -m venv "$venv"
fi
cd "$root/voice_companion" || exit 2
PYTHONPATH="$root/tools/linux-test-stubs:." timeout 900 xvfb-run -a -s "-screen 0 ${XRES:-1024x768x24}" "$venv/bin/python" "$root/tools/run_window.py" "$@" < /dev/null > "$out" 2>&1
grep -E "^FAIL:|^ERROR:|^OK|FAILED|^Ran" "$out" | cut -c1-220
