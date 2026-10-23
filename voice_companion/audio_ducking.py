"""Audio ducking: lower browser and media sound while Companion speaks.

Browser sound does not come from Companion's own process. Embedded Edge plays
through msedgewebview2.exe child processes, Chrome and Edge through their own
audio service processes, and the radio player through a Firefox child. Each of
those has its own Windows audio session, so lowering Companion's own volume
does nothing to them. This module lowers those sessions' volume
(ISimpleAudioVolume) while Companion speaks and brings it back smoothly.

Windows remembers an app's session volume, so a crash while sound is lowered
would leave the browser quiet for good. Original volumes are therefore written
to a small file while anything is lowered; the next start puts them back.

The Windows layer is reached through a "backend" with two calls, so tests can
use a fake that behaves like the real one:
  sessions()   -> objects with key, pid, name, get_volume(), set_volume(v)
                  (volumes 0.0 to 1.0; a session that has ended raises OSError)
  processes()  -> {pid: (parent_pid, exe_name)}
  created(pid) -> process start time as a number, or None when unknown
"""
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

# Choices shown in Voice options. Each says what it means.
LEVELS = (('light, 25 percent quieter', 0.75),
          ('medium, 50 percent quieter', 0.5),
          ('strong, 75 percent quieter', 0.25))
LEVEL_CHOICES = tuple(name for name, _ in LEVELS)
DEFAULT_LEVEL = 'strong, 75 percent quieter'

# Browsers are lowered even when the user started them (Chrome with the
# Voice Companion extension, Edge, Brave, Firefox).
BROWSERS = frozenset({'msedgewebview2.exe', 'chrome.exe', 'msedge.exe', 'brave.exe', 'firefox.exe'})
# Screen readers and magnifiers are never lowered, even with "other apps" on.
NEVER = frozenset({'nvda.exe', 'nvda_slave.exe', 'nvdahelperremotelauncher.exe', 'jfw.exe', 'fsreader.exe',
                   'narrator.exe', 'zt.exe', 'ztvoice.exe', 'zoomtext.exe', 'fusion.exe', 'snova.exe',
                   'dolphinscreenreader.exe', 'audiodg.exe'})

ATTACK_SECONDS = 0.08   # how fast sound goes down when speech starts
HOLD_SECONDS = 0.6      # stay lowered through short pauses between phrases
RELEASE_SECONDS = 0.5   # how long sound takes to come back up
SCAN_SECONDS = 0.25     # look for new sessions this often while lowered
FADE_STEP_SECONDS = 0.03


def level_factor(value):
    """Volume multiplier for a saved level; unknown values use the default."""
    choice = spoken_level(value) or DEFAULT_LEVEL
    return dict(LEVELS)[choice]


_WORD_NUMBERS = {'twenty five': '25', 'twenty-five': '25', 'fifty': '50', 'seventy five': '75', 'seventy-five': '75'}


def spoken_level(value):
    """Match a spoken or typed level ("Strong.", "50 percent", "light, 25 percent quieter")."""
    text = re.sub(r'[^0-9a-z ]+', ' ', str(value or '').casefold().replace('%', ' percent'))
    text = re.sub(r'\s+', ' ', text).strip()
    for words, digits in _WORD_NUMBERS.items():
        text = text.replace(words.replace('-', ' '), digits)
    if not text:
        return None
    for name in LEVEL_CHOICES:
        if text == re.sub(r'[^0-9a-z ]+', '', name).replace('  ', ' '):
            return name
    first = text.split(' ')[0]
    names = {'light': 0, 'low': 0, 'little': 0, '25': 0, 'medium': 1, 'middle': 1, 'half': 1, '50': 1,
             'strong': 2, 'high': 2, 'heavy': 2, '75': 2}
    if text in ('a little', 'a little quieter', 'a lot', 'a lot quieter'):
        return LEVEL_CHOICES[0 if 'little' in text else 2]
    index = names.get(first)
    if index is None:
        return None
    rest = text[len(first):].strip()
    if rest and not re.fullmatch(r'(?:percent ?)?(?:quieter|lower|softer)?|percent', rest):
        return None
    return LEVEL_CHOICES[index]


class _Record:
    __slots__ = ('original', 'last_set', 'name')

    def __init__(self, original, name):
        self.original = original
        self.last_set = original
        self.name = name


class AudioDucker:
    """Keeps matching audio sessions lowered while Companion speaks."""

    def __init__(self, backend_factory, state_path=None, own_pid=None, clock=time.monotonic, log=None):
        self.backend_factory = backend_factory
        self.backend = None
        self.state_path = Path(state_path) if state_path else None
        self.own_pid = os.getpid() if own_pid is None else own_pid
        self.clock = clock
        self.log = log or (lambda text: print(text, file=sys.stderr))
        self.lock = threading.RLock()       # short: speech requests and the fade level
        self.work = threading.RLock()       # held while talking to Windows; speech never waits on it
        self.options_changed = False
        self.wake = threading.Event()
        self.thread = None
        self.closed = False
        self.available = None      # None until the first pass; False when Windows audio could not be reached
        self.problem = None
        self.active = False
        self.factor = level_factor(DEFAULT_LEVEL)
        self.other_apps = False
        self.gain = 1.0
        self.last_active = None
        self.last_step = None
        self.records = {}          # session key -> _Record
        self.decisions = {}        # session key -> whether it is lowered
        self.pending = {}          # exe name -> original volume, for sessions that ended while lowered
        self.saved_state = {}
        self.recovering = False
        self._load_state()

    # ----- requests from Companion -----
    def request(self, active, factor=None, other_apps=None):
        """Called whenever speech starts or the speech state is polled. Cheap; never waits on Windows."""
        with self.lock:
            now = self.clock()
            changed = False
            if factor is not None and abs(float(factor) - self.factor) > 1e-6:
                self.factor = max(0.0, min(1.0, float(factor)));changed = True
            if other_apps is not None and bool(other_apps) != self.other_apps:
                self.other_apps = bool(other_apps);self.options_changed = True;changed = True
            if active:
                self.last_active = now
                if not self.active:changed = True
            elif self.active:
                changed = True
            self.active = bool(active)
        # Only a change wakes the worker; while lowered it checks on its own timer,
        # so the speech poll loop never causes extra Windows audio calls.
        if changed:
            self.wake.set()

    def target(self, now):
        if self.active:
            return self.factor
        if self.last_active is not None and now - self.last_active < HOLD_SECONDS and self.gain < 1.0:
            return self.factor
        return 1.0

    def _busy(self, now):
        return self.gain < 1.0 or self.target(now) < 1.0 or bool(self.records) or self.recovering

    # ----- one pass; the worker thread calls this, tests call it directly -----
    def step(self):
        with self.work:
            with self.lock:
                now = self.clock()
                elapsed = 0.0 if self.last_step is None else max(0.0, now - self.last_step)
                self.last_step = now
                target = self.target(now)
                # Move a small step at a time, even after a long wait, so changes are smooth.
                moved = min(max(elapsed, FADE_STEP_SECONDS), 2 * FADE_STEP_SECONDS)
                span = max(0.05, 1.0 - self.factor)
                if self.gain > target:
                    self.gain = max(target, self.gain - moved * span / ATTACK_SECONDS)
                elif self.gain < target:
                    self.gain = min(target, self.gain + moved * span / RELEASE_SECONDS)
                if self.gain >= 0.999:
                    self.gain = 1.0
                gain = self.gain
                if self.options_changed:
                    self.options_changed = False;self.decisions.clear()
            if gain == 1.0 and not self.records and not self.recovering:
                self._save_state()
                return False
            self._apply(gain)
            self.recovering = False
            self._save_state()
            return gain < 1.0 or bool(self.records)

    def _backend(self):
        if self.backend is None:
            self.backend = self.backend_factory()
        return self.backend

    def _apply(self, gain):
        try:
            sessions = list(self._backend().sessions())
        except Exception as exc:
            if self.available is not False:
                self.log('Audio ducking unavailable: ' + str(exc))
            self.available = False;self.problem = str(exc);self.backend = None
            return
        self.available = True;self.problem = None
        seen = set()
        try:
            for session in sessions:
                try:
                    key = session.key
                    seen.add(key)
                    record = self.records.get(key)
                    if record is None and not self._wanted(session, key):
                        continue
                    if record is not None and not self._wanted(session, key):
                        # "Other apps" was turned off while this one was lowered.
                        self._restore(session, record);del self.records[key];continue
                    current = float(session.get_volume())
                    if record is None:
                        name = str(session.name or '').lower()
                        original = current
                        remembered = self.pending.pop(name, None)
                        if remembered is not None and current < remembered - 0.005:
                            # Windows kept a lowered volume from a session that ended,
                            # or from a crash; its real level is the remembered one.
                            original = remembered
                        if gain >= 1.0 and original <= current + 0.005:
                            continue
                        record = self.records[key] = _Record(original, name)
                        record.last_set = current
                    elif abs(current - record.last_set) > 0.02:
                        # Someone moved this app's slider while it was lowered: their level
                        # becomes the one to come back to; it is never raised above it.
                        record.original = current
                        record.last_set = current
                    wanted = max(0.0, min(1.0, record.original * gain))
                    if abs(wanted - current) > 0.0005:
                        session.set_volume(wanted)
                    record.last_set = wanted
                    if gain >= 1.0:
                        del self.records[key]
                except Exception:
                    # The session ended or its process closed during the pass.
                    self._forget(getattr(session, 'key', None))
            for key in [k for k in self.records if k not in seen]:
                self._forget(key)
            for key in [k for k in self.decisions if k not in seen]:
                del self.decisions[key]
        finally:
            for session in sessions:
                close = getattr(session, 'close', None)
                if close:
                    try:close()
                    except Exception:pass

    def _forget(self, key):
        record = self.records.pop(key, None)
        if record is not None and record.last_set < record.original - 0.005 and record.name:
            # Windows may keep the lowered level for this app's next session.
            self.pending[record.name] = max(record.original, self.pending.get(record.name, 0.0))

    def _restore(self, session, record):
        try:
            if abs(float(session.get_volume()) - record.last_set) <= 0.02:
                session.set_volume(record.original)
        except Exception:
            self._forget(getattr(session, 'key', None))

    def _wanted(self, session, key):
        if key in self.decisions:
            return self.decisions[key]
        decision = self._decide(session)
        self.decisions[key] = decision
        return decision

    def _decide(self, session):
        pid = int(session.pid or 0)
        name = str(session.name or '').lower()
        if pid in (0, self.own_pid) or name in NEVER or name.startswith('voicecompanion'):
            return False
        if name in BROWSERS:
            return True
        if self.other_apps:
            return True
        return self._descendant(pid)

    def _descendant(self, pid):
        """True when pid was started by Companion (embedded Edge, the radio player)."""
        try:
            processes = self._backend().processes()
        except Exception:
            return False
        created = getattr(self._backend(), 'created', lambda pid: None)
        child, depth = pid, 0
        while depth < 16:
            entry = processes.get(child)
            if entry is None:
                return False
            parent = int(entry[0] or 0)
            if parent in (0, child):
                return False
            # Windows reuses process numbers: a parent must have started before its child.
            try:
                parent_time, child_time = created(parent), created(child)
            except Exception:
                parent_time = child_time = None
            if parent_time is not None and child_time is not None and parent_time > child_time:
                return False
            if parent == self.own_pid:
                return True
            child, depth = parent, depth + 1
        return False

    # ----- safety -----
    def restore_all(self):
        """Put every lowered session back right away (exit, crash handler, ducking turned off)."""
        with self.lock:
            self.active = False;self.last_active = None;self.gain = 1.0
        with self.work:
            if self.records or self.recovering:
                self.recovering = False
                self._apply(1.0)
                # Sessions that could not be reached stay in pending for the next start.
                for key in list(self.records):
                    self._forget(key)
            self._save_state()

    def recover(self):
        """At startup, schedule putting back volumes a crash left lowered."""
        with self.lock:
            if self.pending:
                self.recovering = True
        if self.pending:
            self.wake.set()

    def _load_state(self):
        if not self.state_path:
            return
        try:
            data = json.loads(self.state_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            for name, value in data.items():
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue
                if 0.0 <= value <= 1.0:
                    self.pending[str(name).lower()] = value
            self.saved_state = dict(self.pending)

    def _state(self):
        state = dict(self.pending)
        for record in self.records.values():
            if record.name and record.last_set < record.original - 0.005:
                state[record.name] = max(record.original, state.get(record.name, 0.0))
        return state

    def _save_state(self):
        if not self.state_path:
            return
        state = self._state()
        if state == self.saved_state:
            return
        try:
            if state:
                from storage import write_text_atomic
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                write_text_atomic(self.state_path, json.dumps(state))
            else:
                self.state_path.unlink(missing_ok=True)
            self.saved_state = state
        except OSError as exc:
            self.log('Audio ducking state could not be saved: ' + str(exc))

    # ----- worker thread -----
    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name='audio-ducking', daemon=True)
            self.thread.start()
        return self

    def _run(self):
        initialize_com()
        try:
            while not self.closed:
                with self.lock:
                    now = self.clock()
                    busy = self._busy(now)
                    fading = abs(self.gain - self.target(now)) > 1e-6
                self.wake.wait(FADE_STEP_SECONDS if fading else SCAN_SECONDS if busy else None)
                self.wake.clear()
                if self.closed:
                    break
                try:
                    self.step()
                except Exception as exc:
                    self.log('Audio ducking problem: ' + str(exc))
            self.restore_all()
        except Exception as exc:
            self.log('Audio ducking could not restore volume: ' + str(exc))

    def close(self, timeout=2.0):
        """Stop and put every lowered sound back. Safe to call more than once."""
        if self.closed:
            return
        self.closed = True
        self.wake.set()
        thread = self.thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout)
            if not thread.is_alive():
                return
        # No worker (or it is stuck): restore from this thread with a fresh connection.
        if not self.work.acquire(timeout=timeout):
            self.log('Audio ducking could not restore volume: Windows audio did not answer.')
            return  # The saved originals are put back at the next start.
        try:
            self.backend = None
            initialize_com()
            self.restore_all()
        except Exception as exc:
            self.log('Audio ducking could not restore volume: ' + str(exc))
        finally:
            self.work.release()


def initialize_com():
    if os.name != 'nt':
        return
    try:
        import ctypes
        ctypes.windll.ole32.CoInitializeEx(None, 0)  # Multithreaded; "already initialized" is fine.
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Windows Core Audio through ctypes. No extra packages are needed.
# ---------------------------------------------------------------------------

class WindowsAudioSessions:
    """Audio sessions on every active playback device, with their process ids."""

    CLSID_MMDeviceEnumerator = '{BCDE0395-E52F-467C-8E3D-C4579291692E}'
    IID_IMMDeviceEnumerator = '{A95664D2-9614-4F35-A746-DE8DB63617E6}'
    IID_IAudioSessionManager2 = '{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}'
    IID_IAudioSessionControl2 = '{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}'
    IID_ISimpleAudioVolume = '{87CE5498-68D6-44E5-9215-6DA47EF883D8}'

    def __init__(self):
        if os.name != 'nt':
            raise OSError('Audio ducking of other programs needs Windows.')
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        self.wintypes = wintypes

        class GUID(ctypes.Structure):
            _fields_ = [('Data1', ctypes.c_uint32), ('Data2', ctypes.c_uint16), ('Data3', ctypes.c_uint16),
                        ('Data4', ctypes.c_ubyte * 8)]
        import uuid
        self.guid = lambda text: GUID.from_buffer_copy(uuid.UUID(text).bytes_le)
        self.GUID = GUID
        # Our own event context, so a later version can tell its own changes apart.
        self.context = self.guid('{6F2C8E1A-3D47-4B59-9A0E-5C1D2B7E8F43}')
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        self.kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self.kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
        self.kernel.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
        self.kernel.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
        self.kernel.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
        self.ole = ctypes.OleDLL('ole32')
        self.ole.CoTaskMemFree.restype = None
        self.ole.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
        initialize_com()
        self.enumerator = ctypes.c_void_p()
        self.ole.CoCreateInstance(ctypes.byref(self.guid(self.CLSID_MMDeviceEnumerator)), None, 23,
                                  ctypes.byref(self.guid(self.IID_IMMDeviceEnumerator)), ctypes.byref(self.enumerator))
        self.times = {}

    # COM calls through the interface's function table.
    def _call(self, pointer, index, argtypes, *args, restype=None):
        ctypes = self.ctypes
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        function = ctypes.WINFUNCTYPE(restype or ctypes.HRESULT, ctypes.c_void_p, *argtypes)(table[index])
        return function(pointer, *args)

    def _release(self, pointer):
        if pointer:
            try:self._call(pointer, 2, (), restype=self.ctypes.c_ulong)
            except Exception:pass

    def _query(self, pointer, iid):
        out = self.ctypes.c_void_p()
        self._call(pointer, 0, (self.ctypes.POINTER(self.GUID), self.ctypes.POINTER(self.ctypes.c_void_p)),
                   self.ctypes.byref(self.guid(iid)), self.ctypes.byref(out))
        return out

    def sessions(self):
        ctypes, wintypes = self.ctypes, self.wintypes
        found = []
        collection = ctypes.c_void_p()
        # eRender = 0, DEVICE_STATE_ACTIVE = 1
        self._call(self.enumerator, 3, (ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)),
                   0, 1, ctypes.byref(collection))
        try:
            count = ctypes.c_uint()
            self._call(collection, 3, (ctypes.POINTER(ctypes.c_uint),), ctypes.byref(count))
            for device_index in range(count.value):
                device, manager, sessions = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
                try:
                    self._call(collection, 4, (ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)), device_index, ctypes.byref(device))
                    self._call(device, 3, (ctypes.POINTER(self.GUID), wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)),
                               ctypes.byref(self.guid(self.IID_IAudioSessionManager2)), 23, None, ctypes.byref(manager))
                    self._call(manager, 5, (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(sessions))
                    total = ctypes.c_int()
                    self._call(sessions, 3, (ctypes.POINTER(ctypes.c_int),), ctypes.byref(total))
                    for index in range(total.value):
                        session = self._session(sessions, index)
                        if session is not None:
                            found.append(session)
                except OSError:
                    continue  # A device that was just unplugged.
                finally:
                    for pointer in (sessions, manager, device):
                        self._release(pointer)
        finally:
            self._release(collection)
        return found

    def _session(self, sessions, index):
        ctypes, wintypes = self.ctypes, self.wintypes
        control = control2 = volume = None
        try:
            control = ctypes.c_void_p()
            self._call(sessions, 4, (ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)), index, ctypes.byref(control))
            state = ctypes.c_int()
            self._call(control, 3, (ctypes.POINTER(ctypes.c_int),), ctypes.byref(state))
            if state.value == 2:  # AudioSessionStateExpired
                return None
            control2 = self._query(control, self.IID_IAudioSessionControl2)
            if self._call(control2, 15, ()) == 0:  # IsSystemSoundsSession: S_OK means system sounds
                return None
            pid = wintypes.DWORD()
            self._call(control2, 14, (ctypes.POINTER(wintypes.DWORD),), ctypes.byref(pid))
            text = ctypes.c_void_p()
            self._call(control2, 13, (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(text))
            try:key = ctypes.wstring_at(text.value) if text.value else 'session %d %d' % (pid.value, index)
            finally:self.ole.CoTaskMemFree(text)
            volume = self._query(control, self.IID_ISimpleAudioVolume)
            session = _WindowsSession(self, key, pid.value, self.process_name(pid.value), volume)
            volume = None  # Owned by the session now.
            return session
        except OSError:
            return None
        finally:
            for pointer in (volume, control2, control):
                if pointer is not None:
                    self._release(pointer)

    def process_name(self, pid):
        return self.processes().get(pid, (0, ''))[1] if pid else ''

    def processes(self):
        ctypes, wintypes = self.ctypes, self.wintypes
        now = time.monotonic()
        cached = getattr(self, '_process_cache', None)
        if cached and now - cached[0] < 0.5:
            return cached[1]

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                        ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wintypes.DWORD),
                        ('cntThreads', wintypes.DWORD), ('th32ParentProcessID', wintypes.DWORD),
                        ('pcPriClassBase', ctypes.c_long), ('dwFlags', wintypes.DWORD), ('szExeFile', ctypes.c_wchar * 260)]
        result = {}
        snapshot = self.kernel.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
        if not snapshot or snapshot == wintypes.HANDLE(-1).value:
            raise OSError('The process list could not be read.')
        try:
            entry = PROCESSENTRY32W();entry.dwSize = ctypes.sizeof(entry)
            more = self.kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                result[int(entry.th32ProcessID)] = (int(entry.th32ParentProcessID), entry.szExeFile.lower())
                more = self.kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            self.kernel.CloseHandle(snapshot)
        self._process_cache = (now, result)
        return result

    def created(self, pid):
        if pid in self.times:
            return self.times[pid]
        ctypes, wintypes = self.ctypes, self.wintypes
        handle = self.kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not self.kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                return None
            value = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        finally:
            self.kernel.CloseHandle(handle)
        if len(self.times) > 2000:
            self.times.clear()
        self.times[pid] = value
        return value


class _WindowsSession:
    def __init__(self, owner, key, pid, name, volume):
        self.owner, self.key, self.pid, self.name, self.volume = owner, key, pid, name, volume

    def get_volume(self):
        ctypes = self.owner.ctypes
        value = ctypes.c_float()
        self.owner._call(self.volume, 4, (ctypes.POINTER(ctypes.c_float),), ctypes.byref(value))
        return max(0.0, min(1.0, float(value.value)))

    def set_volume(self, value):
        ctypes = self.owner.ctypes
        self.owner._call(self.volume, 3, (ctypes.c_float, ctypes.POINTER(self.owner.GUID)),
                         ctypes.c_float(max(0.0, min(1.0, float(value)))), ctypes.byref(self.owner.context))

    def close(self):
        volume, self.volume = self.volume, None
        if volume:
            self.owner._release(volume)
