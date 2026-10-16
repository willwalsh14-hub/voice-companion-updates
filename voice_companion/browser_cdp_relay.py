"""Authenticated, loopback-only CDP transport for the user-selected browser tab.

The extension owns the debugger attachment. Playwright uses this transport to
run the existing browser backend; it never launches or relaunches that browser.
No page content, commands, input text or transport tokens are written to logs.
"""
import hmac
from http import HTTPStatus
import json
import logging
from multiprocessing.connection import Listener, Client
from pathlib import Path
import secrets
import threading
from regular_browser import address

MAX_FRAME = 16 * 1024 * 1024
MAX_TO_EXTENSION = 1024 * 1024


def encode(message):
    if not isinstance(message, dict): raise ValueError('Invalid browser message.')
    return json.dumps(message, ensure_ascii=False).encode('utf-8')


def decode(data):
    message = json.loads(data)
    if not isinstance(message, dict): raise ValueError('Invalid browser message.')
    return message


class BrowserRelay:
    def __init__(self, folder, pipe_endpoint=None, family='AF_PIPE'):
        from websockets.sync.server import serve
        self.folder = Path(folder); self.folder.mkdir(parents=True, exist_ok=True)
        self.key = secrets.token_bytes(32)
        self.listener = Listener(pipe_endpoint or address(folder)+'-cdp', family=family, authkey=self.key)
        key_file = self.folder/'browser-cdp.key'; key_file.write_bytes(self.key)
        if family != 'AF_PIPE': key_file.chmod(0o600)
        self.token = secrets.token_urlsafe(32)
        self.browser = 'chrome'
        self.generation = 0
        self.pipe = self.websocket = None
        self.connected = threading.Event()
        self.client_closed = threading.Event();self.client_closed.set()
        self.pipe_lock = threading.Lock(); self.state_lock = threading.Lock()
        silent_logger=logging.Logger('VoiceCompanionBrowserTransport');silent_logger.disabled=True
        self.server = serve(self._websocket, '127.0.0.1', 0, process_request=self._authenticate,
                            max_size=MAX_FRAME, compression=None, logger=silent_logger)
        self.endpoint = 'ws://127.0.0.1:'+str(self.server.socket.getsockname()[1])+'/'+self.token
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        threading.Thread(target=self._accept, daemon=True).start()

    def _authenticate(self, connection, request):
        # A web page cannot open this socket: browser requests carry Origin,
        # and the unguessable path is supplied only to this process's driver.
        if request.headers.get_all('Origin') or not hmac.compare_digest(request.path, '/'+self.token):
            return connection.respond(HTTPStatus.FORBIDDEN, 'Browser connection denied.\n')

    def _accept(self):
        while True:
            try: pipe = self.listener.accept()
            except (EOFError, OSError): return
            except Exception: continue
            with self.state_lock:
                if self.pipe is not None:
                    pipe.close(); continue
                self.pipe = pipe
            threading.Thread(target=self._read_pipe, args=(pipe,), daemon=True).start()

    def _read_pipe(self, pipe):
        try:
            while True:
                message = decode(pipe.recv_bytes(MAX_FRAME))
                if message.get('method') == 'VoiceCompanion.ready':
                    if message.get('browser') in ('chrome','edge','brave'):self.browser=message['browser']
                    self.generation += 1
                    self.connected.set(); continue
                websocket = self.websocket
                if websocket is not None:
                    try:websocket.send(json.dumps(message,ensure_ascii=False))
                    except Exception:pass
        except Exception:
            # Never include protocol parameters in an exception or a log.
            pass
        finally:
            with self.state_lock:
                closing = self.websocket if self.pipe is pipe else None
                if self.pipe is pipe: self.pipe = None; self.connected.clear()
            try:pipe.close()
            except OSError:pass
            if closing is not None:
                try:closing.close(1011, 'Browser extension disconnected.')
                except Exception:pass

    def _websocket(self, websocket):
        with self.state_lock:
            if not self.connected.is_set() or self.websocket is not None:
                websocket.close(1013, 'Connect a browser tab first.'); return
            self.websocket = websocket;self.client_closed.clear()
        try:
            # Reinitializing the client forgets virtual session IDs, but leaves
            # the user's tab open and restores its real debugger session.
            self._send({'method':'VoiceCompanion.reset'})
            for data in websocket:
                message = decode(data)
                if not isinstance(message.get('id'),int) or not isinstance(message.get('method'),str):
                    raise ValueError('Invalid browser command.')
                self._send(message)
        except Exception:pass
        finally:
            with self.state_lock:
                if self.websocket is websocket:self.websocket = None;self.client_closed.set()

    def _send(self, message):
        data = encode(message)
        if len(data) > MAX_TO_EXTENSION: raise ValueError('Browser command is too large.')
        with self.pipe_lock:
            if self.pipe is None: raise OSError('The browser extension disconnected.')
            self.pipe.send_bytes(data)

    def close(self):
        self.connected.clear()
        if self.websocket is not None:
            try:self.websocket.close()
            except Exception:pass
        if self.pipe is not None:
            try:self.pipe.close()
            except Exception:pass
        self.server.shutdown(); self.listener.close()


_RELAY = None


def prepare(folder):
    global _RELAY
    if _RELAY is None: _RELAY = BrowserRelay(folder)
    return _RELAY


def current():
    return _RELAY if _RELAY is not None and _RELAY.connected.is_set() else None


def native_stream(first, input_stream, output_stream):
    """Full duplex; no polling delay on keystrokes or page updates."""
    from browser_bridge_host import read_message, write_message
    from regular_browser import data_folder
    folder = data_folder()
    pipe = Client(address(folder)+'-cdp', family='AF_PIPE', authkey=(folder/'browser-cdp.key').read_bytes())
    write_lock = threading.Lock()
    def outgoing():
        try:
            while True:
                message=decode(pipe.recv_bytes(MAX_TO_EXTENSION))
                with write_lock:write_message(output_stream,message)
        except Exception:
            # Closing stdout tells Chrome that this connection ended.
            try:output_stream.close()
            except Exception:pass
    threading.Thread(target=outgoing,daemon=True).start()
    try:
        pipe.send_bytes(encode({'method':'VoiceCompanion.ready','browser':first.get('browser','chrome')}))
        while True:
            message=read_message(input_stream,max_size=MAX_FRAME)
            if message is None:return
            pipe.send_bytes(encode(message))
    finally:pipe.close()
