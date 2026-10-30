"""An opt-in transcript of everything Voice Companion says, and the keys it receives.

Off by default. Options, Startup and exit options, "Keep a transcript of everything
Voice Companion says", or say "start transcript" and "stop transcript". Lines go to
speech-transcript.txt in the app data folder, so a helper can drive the app by keys
and read exactly what a blind user heard, in order, with the key that caused it:

    2026-10-27 16:30:01.123 [awake] [key] Down
    2026-10-27 16:30:01.140 [awake] Word processor, 2 of 15.

Rules:
- Each line is written and flushed at once (line buffering).
- At about 2 MB the file is moved to speech-transcript-old.txt and a new one starts.
- Private values (passwords, private web fields, account secrets) are never written.
  Speech for them is already masked ("Hidden character."); as a second guard, every
  private value the app receives is remembered only as a hash, and any text that
  contains one is masked before it is written.
- Writing can never break speech: every error is swallowed and the transcript
  stops quietly for that line.
"""
import hashlib
import os
import threading
import time
from pathlib import Path

NAME = 'speech-transcript.txt'
OLD_NAME = 'speech-transcript-old.txt'
LIMIT = 2 * 1024 * 1024
HIDDEN = '[hidden]'


def _digest(text):
    return hashlib.sha256(text.encode('utf-8', 'surrogatepass')).hexdigest()


class Transcript:
    def __init__(self, limit=LIMIT, name=NAME, old_name=OLD_NAME):
        self.lock = threading.RLock()
        self.limit = limit
        self.name, self.old_name = name, old_name
        self.folder = None
        self.enabled = False
        self.mode = ''
        self.file = None
        self.private = set()        # (length, sha256) of each private value seen
        self.problem = None         # the last write error, for tests and support

    # Turning it on and off.

    def start(self, folder):
        with self.lock:
            self.folder = Path(folder)
            self.enabled = True

    def stop(self):
        with self.lock:
            self.enabled = False
            self._close()

    @property
    def path(self):
        return None if self.folder is None else self.folder / self.name

    # Private values.

    def remember_private(self, value):
        """A password or private field value: never written, even inside other text."""
        value = str(value or '')
        if len(value) >= 3:
            with self.lock:
                self.private.add((len(value), _digest(value)))

    def mask(self, text):
        text = str(text)
        if not self.private:
            return text
        lengths = sorted({length for length, _ in self.private}, reverse=True)
        for length in lengths:
            if length > len(text):
                continue
            index = 0
            while index + length <= len(text):
                if (length, _digest(text[index:index + length])) in self.private:
                    text = text[:index] + HIDDEN + text[index + length:]
                    index += len(HIDDEN)
                else:
                    index += 1
        return text

    # Writing.

    def said(self, text):
        """Something the app spoke."""
        self._write(text)

    def key(self, name):
        """A key or other input the app received."""
        self._write('[key] ' + str(name))

    def _write(self, text):
        if not self.enabled or self.folder is None:
            return
        try:
            line = ' '.join(self.mask(text).split())
            if not line:
                return
            now = time.time()
            stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now)) + '.%03d' % int(now % 1 * 1000)
            with self.lock:
                if not self.enabled:
                    return
                line = stamp + ' [' + (str(self.mode or '') or 'none') + '] ' + line + '\n'
                self._open()
                if self.file.tell() and self.file.tell() + len(line.encode('utf-8')) > self.limit:
                    self._rotate()
                    self._open()
                self.file.write(line)
            self.problem = None
        except Exception as exc:     # Never break speech.
            self.problem = exc
            with self.lock:
                self._close()

    def _open(self):
        if self.file is None:
            self.folder.mkdir(parents=True, exist_ok=True)
            self.file = open(self.path, 'a', encoding='utf-8', buffering=1, newline='\n')

    def _rotate(self):
        self._close()
        os.replace(self.path, self.folder / self.old_name)

    def _close(self):
        file, self.file = self.file, None
        if file is not None:
            try:
                file.close()
            except Exception:
                pass


TRANSCRIPT = Transcript()
# Always on, but written only while Thunderbird is being set up, started or an account added
# (companion.thunderbird_busy). It names what went wrong when an account would not add
# (Will, October 30, 2026: the speech transcript was off, so nothing said what happened).
# The same private-value rules apply: passwords are never written.
MAIL_LOG = Transcript(limit=1024 * 1024, name='thunderbird-log.txt', old_name='thunderbird-log-old.txt')


def key_label(command):
    """How an input from the window is written: key names as they are, private values hidden."""
    if isinstance(command, str):
        return 'typed command: ' + command
    if not isinstance(command, tuple) or not command:
        return None
    kind = command[0]
    value = command[1] if len(command) > 1 else None
    if kind == 'keyboard':
        return str(value)
    if kind in ('account_secret', 'web_private_value'):
        return 'private entry ' + HIDDEN
    if kind == 'text_edit':
        return None     # typing is heard as its echo, which is written as speech
    if kind == 'web_search_query':
        return 'search entered: ' + str(value)
    if kind == 'email_field_tab':
        return 'email field, ' + ('Shift+Tab' if isinstance(value, dict) and value.get('backward') else 'Tab')
    return str(kind).replace('_', ' ')
