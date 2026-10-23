"""TRIAL ONLY (claude/try-numlock-alt-2). Not for any release.

Which synthetic key releases does plain Tk deliver on Windows? Runs Tk in a
child process so no Tk object is shared with the window tests.
"""
import json
import subprocess
import sys
import unittest

SCRIPT = r'''
import json, tkinter as tk
r = tk.Tk(); t = tk.Text(r); t.pack(); r.update(); t.focus_force(); r.update()
seen = []
r.bind_all('<KeyRelease>', lambda e: seen.append(['all-release', e.keysym, hex(e.state)]), add='+')
t.bind('<KeyRelease>', lambda e: seen.append(['widget-release', e.keysym, hex(e.state)]), add='+')
t.bind('<KeyPress>', lambda e: seen.append(['widget-press', e.keysym, hex(e.state)]), add='+')
for keysym in ('Left', 'Alt_L', 'a'):
    for state in (0, 0x8, 0x20000, 0x20008):
        seen.append(['send', keysym, hex(state)])
        t.event_generate('<KeyPress>', keysym=keysym, state=state)
        t.event_generate('<KeyRelease>', keysym=keysym, state=state)
r.update()
print(json.dumps(seen))
r.destroy()
'''


@unittest.skipUnless(sys.platform == 'win32', 'Windows Tk only')
class TrialKeyReleaseTests(unittest.TestCase):
    def test_every_release_reaches_widget_and_all(self):
        out = subprocess.run([sys.executable, '-c', SCRIPT], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        seen = json.loads(out.stdout.strip().splitlines()[-1])
        # Always fail so the full record reaches the annotation.
        self.fail('TRIAL RECORD ' + json.dumps(seen))


if __name__ == '__main__':
    unittest.main()
