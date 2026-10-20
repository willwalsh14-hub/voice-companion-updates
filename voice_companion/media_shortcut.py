"""A shared Windows hotkey for radio and podcast playback."""
import sys
import threading


class MediaShortcut:
    HOTKEY_ID = 0x5643
    MESSAGE = 0x0312

    def __init__(self, callback):
        self.callback = callback
        self.registered = False
        self.ready = threading.Event()
        self.thread = None
        self.thread_id = None

    def register(self):
        if sys.platform != 'win32':
            return False
        self.thread = threading.Thread(target=self._run, name='Media shortcut', daemon=True)
        self.thread.start()
        self.ready.wait(2)
        return self.registered

    def _run(self):
        import ctypes
        from ctypes import wintypes
        api = ctypes.windll.user32
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        try:
            # Control + P; NOREPEAT gives one toggle per press.
            self.registered = bool(api.RegisterHotKey(None, self.HOTKEY_ID, 0x4002, ord('P')))
            self.ready.set()
            if not self.registered:
                return
            message = wintypes.MSG()
            while api.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == self.MESSAGE and message.wParam == self.HOTKEY_ID:
                    self.callback()
        finally:
            if self.registered:
                api.UnregisterHotKey(None, self.HOTKEY_ID)
            self.registered = False
            self.ready.set()

    def close(self):
        if self.thread_id and self.thread and self.thread.is_alive():
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
            self.thread.join(timeout=2)
