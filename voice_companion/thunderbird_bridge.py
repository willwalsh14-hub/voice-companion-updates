"""Local connection between Voice Companion and the Thunderbird mail add-on.

Thunderbird starts VoiceCompanion-BrowserHost.exe through native messaging
(registered as com.voicecompanion.mail). The host connects to Companion over a
private, key-protected Windows pipe, then forwards requests and answers in both
directions. Thunderbird keeps the email sign-in; nothing here sees a password.
No message content is written to logs.
"""
import itertools
import json
import threading
from multiprocessing.connection import Client, Listener
from pathlib import Path
import secrets

from regular_browser import address

HOST_NAME = 'com.voicecompanion.mail'
ADDON_ID = 'voice-companion-mail@willwalsh14-hub'
PROTOCOL = 3
# One request or answer. A message is sent whole, and a message bigger than this
# ended the connection to Thunderbird (8 MB was too small for some newsletters).
MAX_FRAME = 64 * 1024 * 1024
KEY_FILE = 'mail-bridge.key'


class ThunderbirdUnavailable(OSError):
    """Thunderbird or its Voice Companion add-on is not connected right now."""


class Dropped(ThunderbirdUnavailable):
    """The request was sent, then the connection ended before the answer. It may have been done."""


class NoAnswer(ThunderbirdUnavailable):
    """Thunderbird got the request but did not answer in time. It may still do what was asked."""


class ThunderbirdError(Exception):
    """Thunderbird answered, but could not do what was asked."""


def encode(message):
    if not isinstance(message, dict): raise ValueError('Invalid mail message.')
    return json.dumps(message, ensure_ascii=False).encode('utf-8')


def decode(data):
    message = json.loads(data)
    if not isinstance(message, dict): raise ValueError('Invalid mail message.')
    return message


class MailRelay:
    """Companion's end: waits for Thunderbird's host and sends it requests."""

    def __init__(self, folder, pipe_endpoint=None, family='AF_PIPE'):
        self.folder = Path(folder); self.folder.mkdir(parents=True, exist_ok=True)
        self.key = secrets.token_bytes(32)
        self.listener = Listener(pipe_endpoint or address(folder) + '-mail', family=family, authkey=self.key)
        key_file = self.folder / KEY_FILE
        key_file.write_bytes(self.key)
        if family != 'AF_PIPE': key_file.chmod(0o600)
        self.pipe = None
        self.version = ''
        self.connected = threading.Event()
        self.state_lock = threading.Lock(); self.send_lock = threading.Lock()
        self.pending = {}
        self.ids = itertools.count(1)
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try: pipe = self.listener.accept()
            except (EOFError, OSError): return
            except Exception: continue
            with self.state_lock:
                old, self.pipe = self.pipe, pipe
            # A restarted Thunderbird replaces the old connection.
            if old is not None:
                try: old.close()
                except OSError: pass
            threading.Thread(target=self._read, args=(pipe,), daemon=True).start()

    def _read(self, pipe):
        try:
            while True:
                message = decode(pipe.recv_bytes(MAX_FRAME))
                if message.get('command') == 'mail' and message.get('protocol') == PROTOCOL:
                    self.version = str(message.get('version', ''))
                    self.connected.set(); continue
                waiter = self.pending.get(message.get('id'))
                if waiter is not None:
                    waiter['answer'] = message; waiter['event'].set()
        except Exception:
            pass
        finally:
            with self.state_lock:
                if self.pipe is pipe: self.pipe = None; self.connected.clear()
            # Wake only the requests sent on this connection. After Thunderbird restarts, the
            # old connection ends while new requests already wait on the new one.
            for waiter in list(self.pending.values()):
                if waiter.get('pipe') is pipe: waiter['event'].set()
            try: pipe.close()
            except OSError: pass

    def request(self, method, params=None, timeout=60, progress=None, every=15):
        """Ask the add-on and wait for its answer. progress(), when given, is called
        every `every` seconds while waiting, so a slow step is never silent."""
        if not self.connected.is_set():
            raise ThunderbirdUnavailable('Thunderbird is not connected to Voice Companion.')
        ident = next(self.ids)
        waiter = {'event': threading.Event(), 'answer': None}
        self.pending[ident] = waiter
        try:
            with self.send_lock:
                pipe = self.pipe
                if pipe is None: raise ThunderbirdUnavailable('Thunderbird is not connected to Voice Companion.')
                waiter['pipe'] = pipe
                pipe.send_bytes(encode({'id': ident, 'method': method, 'params': params or {}}))
            import user_stop
            waited, answered = 0, False
            while not answered and waited < timeout:
                step = min(every if progress else timeout, timeout - waited)
                answered = user_stop.wait(waiter['event'], step)
                waited += step
                if not answered and waited < timeout and progress: progress()
            if not answered:
                raise NoAnswer('Thunderbird did not answer.')
            if waiter['answer'] is None:
                raise Dropped('Thunderbird did not answer.')
        finally:
            self.pending.pop(ident, None)
        answer = waiter['answer']
        if 'error' in answer:
            error = answer['error'] if isinstance(answer['error'], dict) else {}
            raise ThunderbirdError(str(error.get('message') or 'Thunderbird could not complete that request.'))
        return answer.get('result')

    def close(self):
        self.connected.clear()
        if self.pipe is not None:
            try: self.pipe.close()
            except OSError: pass
        self.listener.close()


_RELAY = None


def prepare(folder):
    """Start listening for Thunderbird. Safe to call more than once."""
    global _RELAY
    if _RELAY is None:
        _RELAY = MailRelay(folder)
    return _RELAY


def current():
    return _RELAY


def connected():
    return _RELAY is not None and _RELAY.connected.is_set()


# How often the host looks for Voice Companion while it is closed or starting again.
HOST_RETRY_SECONDS = 0.5


def native_stream(first, input_stream, output_stream, pipe_endpoint=None, family='AF_PIPE', retry=None):
    """Host side: forward between Thunderbird (stdin/stdout) and Companion's pipe.

    Thunderbird keeps this host for as long as its add-on runs. When Voice Companion is
    closed, or starts again, the host waits for it and connects again by itself, every
    half second. It never relies on Thunderbird to try again: Thunderbird slows its own
    timers while its window is minimized or off the taskbar, and the user then had to
    press Alt+Tab before mail would connect (Will, October 29, 2026).
    The host ends only when Thunderbird closes the connection."""
    from browser_bridge_host import read_message, write_message
    from regular_browser import data_folder
    folder = data_folder()
    endpoint = pipe_endpoint or address(folder) + '-mail'
    wait = HOST_RETRY_SECONDS if retry is None else retry
    hello = encode({'protocol': PROTOCOL, 'command': 'mail', 'version': str(first.get('version', ''))})
    write_lock, state_lock = threading.Lock(), threading.Lock()
    ended = threading.Event()
    state = {'pipe': None}

    def open_pipe():
        while not ended.is_set():
            try:
                # The key is read again each time: Voice Companion makes a new one when it starts.
                pipe = Client(endpoint, family=family, authkey=(folder / KEY_FILE).read_bytes())
            except Exception:
                ended.wait(wait)
                continue
            try:
                pipe.send_bytes(hello)
                return pipe
            except Exception:
                try: pipe.close()
                except Exception: pass
                ended.wait(wait)
        return None

    def from_companion():
        while not ended.is_set():
            pipe = open_pipe()
            if pipe is None:
                return
            with state_lock:
                if ended.is_set():
                    pipe.close(); return
                state['pipe'] = pipe
            try:
                while True:
                    message = decode(pipe.recv_bytes(MAX_FRAME))
                    with write_lock: write_message(output_stream, message)
            except (OSError, EOFError, ValueError):
                pass   # Voice Companion closed: wait for it to start again.
            except Exception:
                pass
            finally:
                with state_lock:
                    if state['pipe'] is pipe: state['pipe'] = None
                try: pipe.close()
                except Exception: pass
            try:
                output_stream.flush()
            except Exception:
                # Thunderbird is gone too: nothing left to forward to.
                ended.set()

    reader = threading.Thread(target=from_companion, daemon=True)
    reader.start()
    try:
        while True:
            try:
                message = read_message(input_stream, max_size=MAX_FRAME)
            except (ValueError, OSError):
                return   # Thunderbird closed or sent something invalid: end this connection.
            if message is None: return
            with state_lock:
                pipe = state['pipe']
            if pipe is None:
                continue   # an answer for a Voice Companion that has closed: nobody waits for it
            try:
                pipe.send_bytes(encode(message))
            except Exception:
                pass       # Voice Companion just closed; the reader connects again
    finally:
        ended.set()
        with state_lock:
            pipe, state['pipe'] = state['pipe'], None
        if pipe is not None:
            try: pipe.close()
            except Exception: pass
        # Closing stdout tells Thunderbird that this connection ended.
        try: output_stream.close()
        except Exception: pass


def host_manifest(host_path):
    """Thunderbird's native host manifest: only this add-on may start the host."""
    return {'name': HOST_NAME, 'description': 'Voice Companion mail connection',
            'path': str(host_path), 'type': 'stdio', 'allowed_extensions': [ADDON_ID]}


def register(folder, host_path):
    (Path(folder) / 'mail-native-host.json').write_text(json.dumps(host_manifest(host_path), indent=2), encoding='utf-8')
