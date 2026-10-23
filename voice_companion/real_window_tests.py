"""Marks tests that drive the real Companion window with Windows key presses.

The Windows build runs these classes in their own fresh process, one class
per process, after the other release tests (build-windows.ps1). In the long
shared run, windows froze before their first update in some builds while
earlier tests had left COM apartments and threads behind; a fresh process
keeps that out and lets a failure report only its own window."""
import os
import sys
import unittest

APART = 'VOICE_COMPANION_WINDOW_TESTS_APART'


def real_window(cls):
    if sys.platform != 'win32':
        return unittest.skip('Windows Tk keyboard integration')(cls)
    if os.environ.get(APART):
        return unittest.skip('runs in its own process after the release tests')(cls)
    return cls
