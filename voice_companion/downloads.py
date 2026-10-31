"""One download list and queue for embedded Edge and the user's own Chrome.

Design: each browser does the transfer itself. It already has the page's
sign-in, cookies, form posts and files a page builds in memory (blob: and
data: links), and it keeps its own safety checks. Fetching the address again
from Companion would break all of those, and the Chrome extension is not
allowed to read cookies. So this model mirrors what the browser reports and
asks the browser that owns a download to pause, resume or cancel it. Both
browsers report into the same DownloadManager, so the list, the queue, the
spoken messages and the keys are the same whichever browser started it.

Browser messages arrive on their own threads. Every change happens under one
lock, and spoken messages wait in `notices` for the main loop to say them.
Control requests to a browser never wait for an answer: the answer arrives as
a later report on the same thread that would otherwise be waiting for it.
"""
import json
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path, PureWindowsPath

ACTIVE_LIMIT = 3          # downloads that run at once, like a mainstream browser
HISTORY_LIMIT = 200       # records kept in download-history.json
REQUEST_WAIT = 10         # seconds a pause, resume or cancel request may take
UNFINISHED = ('queued', 'downloading', 'paused', 'blocked')
DONE = ('finished', 'failed', 'cancelled')
BROWSER_NAMES = {'edge': 'Embedded Edge', 'chrome': 'Chrome'}
# Files that run as programs. Enter asks before running one.
PROGRAM_TYPES = {'.exe', '.msi', '.msix', '.msixbundle', '.appx', '.appxbundle', '.appinstaller', '.bat', '.cmd', '.com',
                 '.scr', '.pif', '.ps1', '.psm1', '.vbs', '.vbe', '.js', '.jse', '.wsf', '.wsh', '.hta', '.jar',
                 '.lnk', '.reg', '.cpl', '.msc', '.msp', '.application', '.url'}
REASONS = {
    'network': 'The internet connection was lost.',
    'server': 'The website stopped sending the file.',
    'file': 'The file could not be saved. The folder may be full or locked.',
    'blocked': 'Windows or the browser blocked this file as unsafe.',
    'closed': 'Voice Companion or the browser closed before it finished.',
    'unknown': 'The download stopped.',
}
# chrome.downloads danger values that need no answer: the file is safe, the user
# kept it, a policy or a deep scan allowed it, or Chrome is still checking it.
NO_ANSWER_NEEDED = ('safe', 'accepted', 'allowlistedByPolicy', 'deepScannedSafe', 'deepScannedOpenedDangerous',
                    'asyncScanning', 'asyncLocalPasswordScanning')
DOWNLOADS_FOLDER_ID = '{374DE290-123F-4565-9164-39C4925E467B}'
KNOWN_FOLDERS = {'desktop': '{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}', 'documents': '{FDD39AD0-238F-46AF-ADB4-6C85480369C7}',
                 'downloads': DOWNLOADS_FOLDER_ID}


# Folders ---------------------------------------------------------------------
def known_folder(guid, fallback):
    """A Windows known folder (Downloads, Desktop, Documents), wherever the user moved it."""
    if os.name == 'nt':
        try:
            import ctypes
            import uuid
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD), ('Data3', wintypes.WORD),
                            ('Data4', ctypes.c_ubyte * 8)]
            value = uuid.UUID(guid)
            ident = GUID(value.fields[0], value.fields[1], value.fields[2], (ctypes.c_ubyte * 8).from_buffer_copy(value.bytes[8:]))
            found = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(ident), 0, None, ctypes.byref(found)) == 0:
                try:
                    if found.value:
                        return Path(found.value)
                finally:
                    ctypes.windll.ole32.CoTaskMemFree(found)
        except (OSError, AttributeError, ValueError):
            pass
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
                return Path(os.path.expandvars(winreg.QueryValueEx(key, guid)[0]))
        except (OSError, ValueError):
            pass
    return Path.home() / fallback


def default_folder():
    """The user's own Downloads folder (C:\\Users\\<name>\\Downloads unless they moved it)."""
    return known_folder(DOWNLOADS_FOLDER_ID, 'Downloads')


def spoken_path(value):
    """A typed or spoken folder path, with speech words such as "backslash" made into marks."""
    text = str(value or '').strip().strip('"').strip()
    text = re.sub(r'[.!?]+$', '', text).strip()
    text = re.sub(r'\s*\b(?:back ?slash)\b\s*', lambda _: '\\', text, flags=re.I)
    text = re.sub(r'\s*\b(?:forward slash|slash)\b\s*', '/', text, flags=re.I)
    text = re.sub(r'^([a-z])\s*(?:colon\b|:)\s*', lambda m: m[1].upper() + ':', text, flags=re.I)
    return text.strip()


def check_folder(path):
    """Raise a plain ValueError unless downloads can be saved in this folder."""
    path = Path(path)
    if not path.is_dir():
        raise ValueError('That folder does not exist. Check the folder path, or leave the box empty to use your Downloads folder.')
    try:
        with tempfile.NamedTemporaryFile(dir=path, prefix='.voice-companion-check-', delete=True):
            pass
    except OSError:
        raise ValueError('Voice Companion cannot save files in that folder. Choose another folder, or leave the box empty to use your Downloads folder.')
    return path


def folder_setting(value):
    """The download folder text to save: '' means the Windows Downloads folder.

    Accepts a full path, or the words Downloads, Desktop or Documents."""
    text = spoken_path(value)
    plain = re.sub(r'[^a-z ]+', '', text.casefold()).strip()
    plain = re.sub(r'^(?:my|the) ', '', plain).removesuffix(' folder').strip()
    if not text or plain in ('downloads', 'download', 'default', 'windows downloads'):
        return ''
    if plain in ('desktop', 'documents'):
        return str(check_folder(known_folder(KNOWN_FOLDERS[plain], plain.title())))
    path = Path(os.path.expandvars(os.path.expanduser(text)))
    if not (path.is_absolute() or PureWindowsPath(text).is_absolute()):
        raise ValueError('Type the full folder path, such as C:\\Users\\Will\\Music. Or leave the box empty to use your Downloads folder.')
    return str(check_folder(path))


def chosen_folder(setting):
    """The folder the user chose in Options, or None for the browser's usual Downloads folder."""
    text = str(setting or '').strip()
    if not text:
        return None
    path = Path(text)
    return path if path.is_dir() else None


def unique_path(folder, name):
    """A free file name in folder: report.pdf, then report (1).pdf, and so on."""
    folder = Path(folder)
    name = Path(str(name)).name or 'download'
    target = folder / name
    stem, suffix = Path(name).stem, Path(name).suffix
    number = 1
    while target.exists():
        target = folder / (stem + ' (' + str(number) + ')' + suffix)
        number += 1
    return target


def move_into(source, folder, attempts=6, wait=0.5):
    """Move a finished file into folder without replacing anything there.

    Windows may scan a new file for a moment; a locked file is tried again."""
    source = Path(source)
    for attempt in range(attempts):
        target = unique_path(folder, source.name)
        try:
            try:
                os.replace(source, target)
            except PermissionError:
                raise
            except OSError as exc:
                # 18 (Linux) and 17 (Windows): the folder is on another drive. Copy
                # beside the target first, then rename, so it is never half written.
                if exc.errno != 18 and getattr(exc, 'winerror', None) != 17:
                    raise
                partial = target.with_name(target.name + '.moving')
                try:
                    shutil.copy2(source, partial)
                    os.replace(partial, target)
                except OSError:
                    partial.unlink(missing_ok=True)
                    raise
                source.unlink()
            return target
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(wait)
    return source


def spoken_size(count):
    if not count or count < 0:
        return '0 bytes'
    for size, word in ((1 << 30, 'gigabytes'), (1 << 20, 'megabytes'), (1 << 10, 'kilobytes')):
        if count >= size:
            value = count / size
            text = str(round(value, 1)).removesuffix('.0')
            return text + ' ' + word
    return str(count) + ' bytes'


def file_name(path):
    """The file name from a Windows or Linux path."""
    text = str(path or '').rstrip('\\/')
    name = re.split(r'[\\/]', text)[-1] if text else ''
    return '' if name.endswith(':') else name


def address_name(url):
    """A stand-in name from a web address, used only until the browser names the file."""
    text = str(url or '')
    if not text.startswith(('http://', 'https://')):
        return ''
    from urllib.parse import unquote, urlsplit
    return file_name(unquote(urlsplit(text).path))


# Records ---------------------------------------------------------------------
@dataclass
class Download:
    id: str
    source: str            # 'edge' or 'chrome'
    ref: str               # the browser's own id for this download
    name: str = ''
    path: str = ''
    url: str = ''
    state: str = 'downloading'
    received: int = 0
    total: int = 0
    reason: str = ''
    can_resume: bool = False
    created: float = 0.0
    rank: object = None     # place in line while queued; lower starts first
    intent: object = None   # 'queued', 'paused' or 'cancel': what Companion or the user asked for
    announced: bool = False
    requested: object = field(default=None, repr=False)
    requested_at: float = field(default=0.0, repr=False)
    live: bool = field(default=True, repr=False)

    SAVED = ('id', 'source', 'ref', 'name', 'path', 'url', 'state', 'received', 'total', 'reason', 'can_resume',
             'created', 'rank', 'intent', 'announced')

    def saved(self):
        return {key: getattr(self, key) for key in self.SAVED}

    @classmethod
    def restore(cls, value):
        if not isinstance(value, dict) or not all(isinstance(value.get(key), str) and value.get(key) for key in ('id', 'source', 'ref')):
            return None
        known = {f.name for f in fields(cls)}
        record = cls(**{key: value[key] for key in cls.SAVED if key in value and key in known})
        if record.state not in UNFINISHED + DONE:
            return None
        try:
            record.received = max(0, int(record.received or 0))
            record.total = max(0, int(record.total or 0))
            record.created = float(record.created or 0)
        except (TypeError, ValueError):
            return None
        return record


class DownloadManager:
    def __init__(self, data_folder, folder_setting=None, limit=ACTIVE_LIMIT, clock=time.monotonic, background=True, mover=move_into):
        self.data_folder = Path(data_folder)
        self.path = self.data_folder / 'download-history.json'
        # Returns the saved Options text: '' for the Windows Downloads folder, or a full path.
        self.folder_setting = folder_setting or (lambda: '')
        self.limit = limit
        self.clock = clock
        self.background = background
        self.mover = mover
        self.lock = threading.RLock()
        self.notices = queue.Queue()
        self.items = []          # newest first
        self.controllers = {}
        self.revision = 0
        self.serial = 0
        self.pushed_folder = None
        self.load()

    # Saved history ------------------------------------------------------------
    def load(self):
        self.unread = False
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return
        except ValueError:
            # Keep a damaged file for a helper; start a fresh list.
            try:
                os.replace(self.path, self.path.with_name(self.path.name + '.damaged'))
            except OSError:
                pass
            return
        except OSError:
            # Windows can hold the file for a moment (an antivirus scan or OneDrive).
            # It is not damaged: read it again before it is ever written over.
            self.unread = True
            return
        self.restore(data)

    def restore(self, data):
        """Add saved records after the ones already listed (newest first)."""
        taken = {r.id for r in self.items}
        for value in data if isinstance(data, list) else []:
            record = Download.restore(value)
            if record is None:
                continue
            if record.state in UNFINISHED:
                # The browser that held it may report it again (Chrome keeps going
                # while Companion is closed). Until then it is stopped.
                record.state, record.reason, record.live = 'failed', 'closed', False
            else:
                record.live = False
            record.requested = None
            if record.id in taken:
                # Read late: this session already used the number.
                self.serial += 1
                record.id = 'd' + str(self.serial)
            taken.add(record.id)
            self.items.append(record)
            number = re.fullmatch(r'd(\d+)', record.id)
            if number:
                self.serial = max(self.serial, int(number[1]))
        self.items.sort(key=lambda r: r.created, reverse=True)

    def save(self):
        from storage import write_text_atomic
        if self.unread:
            # The saved list could not be read at start. Read it now, so it is
            # never replaced by only this session's downloads.
            current = [r for r in self.items]
            self.items = []
            self.load()
            if self.unread:
                self.items = current
                print('Could not read download history; it was not saved this time.', file=sys.stderr)
                return
            saved, self.items = self.items, current
            self.restore([r.saved() for r in saved])
        try:
            write_text_atomic(self.path, json.dumps([r.saved() for r in self.items[:HISTORY_LIMIT]], ensure_ascii=False, indent=1))
        except OSError as exc:
            print('Could not save download history:', exc, file=sys.stderr)

    def changed(self, save=True):
        self.revision += 1
        if len(self.items) > HISTORY_LIMIT:
            # Drop the oldest finished records; never one that is still going.
            keep = [r for r in self.items if r.state in UNFINISHED]
            done = [r for r in self.items if r.state not in UNFINISHED][:max(0, HISTORY_LIMIT - len(keep))]
            ids = {r.id for r in keep + done}
            self.items = [r for r in self.items if r.id in ids]
        if save:
            self.save()

    def say(self, text):
        self.notices.put(text)

    @staticmethod
    def display(record):
        """The name to speak: the file name, or until the browser names it, the address's last part."""
        return record.name or address_name(record.url) or 'Unnamed download'

    # Folders ------------------------------------------------------------------
    def chosen(self):
        try:
            return chosen_folder(self.folder_setting())
        except Exception:
            return None

    def folder(self):
        """Where new downloads are saved."""
        return self.chosen() or default_folder()

    def push_folder(self, force=False):
        """Tell browsers that save straight into a folder (embedded Edge) where to save."""
        folder = str(self.folder())
        with self.lock:
            if folder == self.pushed_folder and not force:
                return
            self.pushed_folder = folder
            controllers = list(self.controllers.values())
        for controller in controllers:
            setter = getattr(controller, 'set_folder', None)
            if setter:
                try:
                    setter(folder)
                except Exception:
                    pass

    # Browsers ------------------------------------------------------------------
    def attach(self, source, controller):
        with self.lock:
            self.controllers[source] = controller
        setter = getattr(controller, 'set_folder', None)
        if setter:
            try:
                setter(str(self.folder()))
            except Exception:
                pass

    def detach(self, source, controller=None, stopped=True):
        """A browser connection ended. Embedded Edge downloads end with it."""
        with self.lock:
            if controller is not None and self.controllers.get(source) is not controller:
                return
            self.controllers.pop(source, None)
            if not stopped:
                return
            lost = [r for r in self.items if r.source == source and r.live and r.state in UNFINISHED]
            for record in lost:
                record.state, record.reason, record.live, record.rank = 'failed', 'closed', False, None
                self.say(self.display(record) + ' stopped because ' + BROWSER_NAMES.get(source, 'the browser') + ' closed. Open the page again and choose the link to download it.')
            if lost:
                self.changed()
                self.pump()

    def busy(self, source=None):
        with self.lock:
            return any(r.live and r.state in UNFINISHED and (source is None or r.source == source) for r in self.items)

    def find(self, source, ref):
        return next((r for r in self.items if r.source == source and r.ref == str(ref)), None)

    def running(self):
        return [r for r in self.items if r.live and r.state == 'downloading']

    def waiting(self):
        return sorted((r for r in self.items if r.live and r.state == 'queued'), key=lambda r: (r.rank if r.rank is not None else 0, r.created))

    def next_rank(self):
        ranks = [r.rank for r in self.items if r.state == 'queued' and r.rank is not None]
        return (max(ranks) + 1) if ranks else 1

    def control(self, record, action):
        controller = self.controllers.get(record.source)
        if controller is None:
            raise ValueError(BROWSER_NAMES.get(record.source, 'The browser') + ' is not connected right now, so ' + self.display(record) + ' cannot be changed. '
                             + ('Open Chrome, wait for it to link, then try again.' if record.source == 'chrome' else 'Open the web page again, then try again.'))
        try:
            controller.control(record.ref, action)
        except Exception as exc:
            raise ValueError(BROWSER_NAMES.get(record.source, 'The browser') + ' did not accept that request. Try again.') from exc
        record.requested, record.requested_at = action, self.clock()

    def pump(self):
        """Start waiting downloads while fewer than the limit are running."""
        with self.lock:
            started = False
            while len(self.running()) < self.limit:
                waiting = self.waiting()
                if not waiting:
                    break
                record = waiting[0]
                try:
                    self.control(record, 'resume')
                except ValueError:
                    record.state, record.reason, record.live, record.rank = 'failed', 'closed', False, None
                    self.say(self.display(record) + ' could not start because ' + BROWSER_NAMES.get(record.source, 'the browser') + ' is not connected.')
                    started = True
                    continue
                record.state, record.intent, record.rank = 'downloading', None, None
                started = True
            if started:
                self.changed()

    # Reports from browsers ---------------------------------------------------------
    def report(self, source, ref, new=False, state=None, name=None, path=None, url=None, received=None, total=None,
               reason=None, can_resume=None, refused=False):
        """News from a browser about one download.

        state is one of downloading, paused, finished, failed, cancelled or blocked.
        refused means the browser could not do the last pause, resume or cancel
        request; this report is then the real state."""
        ref = str(ref)
        with self.lock:
            record = self.find(source, ref)
            created = False
            if record is None:
                # A browser's list can hold old downloads; only new or running ones join.
                if not new and state not in ('downloading', 'paused', 'blocked'):
                    return
                self.serial += 1
                record = Download('d' + str(self.serial), source, ref, created=time.time())
                self.items.insert(0, record)
                created = True
            elif not record.live and state in ('downloading', 'paused', 'blocked'):
                record.live = True   # Chrome kept going while Companion was closed
            before = (record.state, record.name, record.path)
            if refused:
                record.requested = None
                if record.intent == 'cancel':
                    record.intent = None
            if path:
                record.path = str(path)
            # Only the browser's own file name is announced. A name taken from the
            # address ("download?id=7") is a stand-in until the browser decides.
            if file_name(path):
                record.name = file_name(path)
            elif name:
                record.name = file_name(name) or str(name)
            if url:
                from privacy_controls import saved_address
                record.url = saved_address(url) or ''
            if received is not None and received >= 0:
                record.received = int(received)
            if total is not None and total >= 0:
                record.total = int(total)
            if can_resume is not None:
                record.can_resume = bool(can_resume)
            changed = created
            if created:
                if not new:
                    record.announced = True   # found while linking; it was not just clicked
                others = [r for r in self.running() if r is not record]
                if new and state in ('downloading', 'paused', None) and len(others) >= self.limit:
                    record.state, record.intent, record.rank = 'queued', 'queued', self.next_rank()
                    try:
                        self.control(record, 'pause')
                    except ValueError:
                        pass
                else:
                    # A file the browser flags at once starts as downloading, so the
                    # change to blocked below says what to do.
                    record.state = state if state in ('downloading', 'paused') else 'downloading'
                    if record.state == 'paused':
                        record.intent = 'paused'
            self.announce_start(record)
            if state and not (created and state in ('downloading', 'paused')):
                changed = self.apply(record, state, reason) or changed
            if refused and not created:
                self.say(BROWSER_NAMES.get(source, 'The browser') + ' could not change ' + self.display(record) + '. It is ' + self.status(record) + '.')
            self.revision += 1
            if changed or before != (record.state, record.name, record.path):
                self.changed()
            self.pump()

    def announce_start(self, record):
        """Say "Downloading <name>." once, as soon as the browser has named the file."""
        if record.name and not record.announced and record.live:
            record.announced = True
            if record.state in UNFINISHED:
                self.say('Downloading ' + record.name + ('. It is waiting in line.' if record.state == 'queued' else '.'))

    def apply(self, record, state, reason=None):
        """Bring a record to the state its browser reports. Returns True when it changed."""
        if record.requested and self.clock() - record.requested_at < REQUEST_WAIT:
            # The browser has not yet done what was asked; an older report is not news.
            if (record.requested == 'pause' and state == 'downloading' or record.requested == 'resume' and state == 'paused'
                    or record.requested == 'cancel' and state in ('downloading', 'paused')):
                return False
        record.requested = None
        old = record.state
        if state == 'paused':
            new = 'queued' if record.intent == 'queued' else 'paused'
            if record.intent is None:
                record.intent = 'paused'   # paused in the browser itself
        elif state == 'downloading':
            new = 'downloading'
            record.intent = None if record.intent != 'cancel' else record.intent
            record.rank = None
        elif state in ('finished', 'failed', 'cancelled', 'blocked'):
            new = state
        else:
            return False
        if new == old and not (new == 'failed' and reason and reason != record.reason):
            return False
        record.state = new
        if new != 'queued':
            record.rank = None
        if new == 'failed':
            record.reason = reason if reason in REASONS else 'unknown'
        if new == 'finished' and record.total < record.received:
            record.total = record.received
        if old not in UNFINISHED or not record.live:
            return True
        name = self.display(record)
        if new == 'finished':
            self.settle(record)
        elif new == 'failed':
            self.say(name + ' did not finish downloading. ' + REASONS[record.reason] + ' '
                     + ('To try again, open Downloads and press Space.' if record.can_resume else 'Go back to the web page and choose the link again.'))
        elif new == 'cancelled' and record.intent != 'cancel':
            self.say(name + ' was cancelled in the browser.')
        elif new == 'blocked':
            self.say(name + ' needs your answer in Chrome. Chrome thinks it may be unsafe. Press Control+J in Chrome to keep or delete it.')
        if new in DONE:
            record.intent = None
            record.announced = True
        return True

    def settle(self, record):
        """A finished file goes to the folder chosen in Options, then is announced."""
        chosen = self.chosen()
        source = Path(record.path) if record.path else None
        if chosen is None or source is None or not source.is_file() or os.path.normcase(str(source.parent.resolve())) == os.path.normcase(str(chosen.resolve())):
            self.say(self.display(record) + ' finished downloading.')
            return

        def move():
            try:
                target = self.mover(source, chosen)
            except OSError:
                self.say(self.display(record) + ' finished downloading. It could not be moved, so it is still in ' + source.parent.name + '.')
                return
            with self.lock:
                record.path, record.name = str(target), Path(target).name
                self.changed()
            self.say(record.name + ' finished downloading.')
        if self.background:
            threading.Thread(target=move, daemon=True, name='Download move').start()
        else:
            move()

    # What the user asks for ---------------------------------------------------------
    def place_in_line(self, record):
        waiting = self.waiting()
        return waiting.index(record) + 1 if record in waiting else None

    def percent(self, record):
        if record.total <= 0:
            return None
        value = int(record.received * 100 / record.total)
        return max(0, min(100 if record.state == 'finished' else 99, value))

    def status(self, record):
        """Short words after the name, as the list reads them."""
        percent = self.percent(record)
        if record.state == 'downloading':
            return (str(percent) + ' percent, ' if percent is not None else spoken_size(record.received) + ', ' if record.received else '') + 'downloading'
        if record.state == 'queued':
            place = self.place_in_line(record)
            return 'queued' + (', number ' + str(place) + ' in line' if place else '')
        if record.state == 'paused':
            return (str(percent) + ' percent, ' if percent is not None else '') + 'paused'
        if record.state == 'blocked':
            return 'waiting for your answer in Chrome'
        return record.state

    def label(self, record):
        return self.display(record) + ', ' + self.status(record)

    def labels(self):
        with self.lock:
            return [self.label(r) for r in self.items]

    def details(self, record):
        text = self.label(record) + '.'
        if record.total:
            text += ' ' + spoken_size(record.received) + ' of ' + spoken_size(record.total) + '.'
        elif record.received:
            text += ' ' + spoken_size(record.received) + '.'
        if record.state == 'failed':
            text += ' ' + REASONS.get(record.reason, REASONS['unknown'])
        if record.path:
            text += ' Folder: ' + (Path(record.path).parent.name or str(Path(record.path).parent)) + '.'
        if record.url:
            from urllib.parse import urlsplit
            host = urlsplit(record.url).hostname
            if host:
                text += ' From ' + host.removeprefix('www.') + '.'
        text += ' ' + BROWSER_NAMES.get(record.source, 'Browser') + '.'
        return text

    def next_step(self, record):
        """What the user can do with a download that is not finished, said after its details."""
        if record.state in ('downloading', 'paused', 'queued'):
            return 'Space pauses or resumes it.'
        if record.state == 'failed' and record.can_resume and record.live:
            return 'Space tries again.'
        if record.state == 'blocked':
            return 'In Chrome, press Control+J, then keep or delete the file.'
        if record.state in ('failed', 'cancelled'):
            return 'To get it, go back to the web page and choose the link again.'
        return ''

    def toggle(self, record):
        """Space: pause or resume. Returns what to say."""
        with self.lock:
            name = self.display(record)
            if record.state == 'downloading':
                self.control(record, 'pause')
                record.state, record.intent = 'paused', 'paused'
                self.changed(); self.pump()
                return 'Paused ' + name + '.'
            if record.state == 'queued':
                record.state, record.intent, record.rank = 'paused', 'paused', None
                self.changed()
                return 'Paused ' + name + '. It will not start until you resume it.'
            if record.state == 'paused' or record.state == 'failed' and record.can_resume and record.live:
                retry = record.state == 'failed'
                if len(self.running()) >= self.limit:
                    record.state, record.intent, record.rank = 'queued', 'queued', self.next_rank()
                    if retry:
                        record.reason = ''
                    self.changed()
                    return name + ' is queued. It starts when another download finishes. It is number ' + str(self.place_in_line(record)) + ' in line.'
                self.control(record, 'resume')
                record.state, record.intent, record.rank, record.reason = 'downloading', None, None, ''
                self.changed()
                return ('Trying ' if retry else 'Resuming ') + name + (' again.' if retry else '.')
            if record.state == 'failed':
                return name + ' cannot continue. Go back to the web page and choose the link again.'
            if record.state == 'blocked':
                return 'Answer Chrome about ' + name + ' first. In Chrome, press Control+J, then keep or delete the file.'
            if record.state == 'finished':
                return name + ' is finished. Press Enter to open it.'
            return name + ' was cancelled. To get it, go back to the web page and choose the link again.'

    def cancel(self, record):
        with self.lock:
            name = self.display(record)
            if record.state not in UNFINISHED:
                return name + ' is not downloading. Nothing was cancelled.'
            self.control(record, 'cancel')
            record.state, record.intent, record.rank = 'cancelled', 'cancel', None
            self.changed(); self.pump()
            return 'Cancelled ' + name + '.'

    def remove(self, record):
        with self.lock:
            if record.state in UNFINISHED:
                return self.cancel(record)
            if record in self.items:
                self.items.remove(record)
                self.changed()
            if record.state == 'finished':
                return 'Removed ' + self.display(record) + ' from the list. The file is still in its folder.'
            return 'Removed ' + self.display(record) + ' from the list.'

    def move(self, record, step):
        """Control+Up (step -1) or Control+Down (step 1): an earlier or later place in line."""
        with self.lock:
            name = self.display(record)
            if record.state != 'queued':
                return 'Only a queued download can move in line. ' + self.label(record) + '.'
            waiting = self.waiting()
            index = waiting.index(record)
            target = index + step
            if not 0 <= target < len(waiting):
                return name + ' is already ' + ('first' if step < 0 else 'last') + ' in line.'
            for number, item in enumerate(waiting):
                item.rank = number + 1
            other = waiting[target]
            record.rank, other.rank = other.rank, record.rank
            self.changed()
            return name + ' moved ' + ('up' if step < 0 else 'down') + '. It is number ' + str(self.place_in_line(record)) + ' in line.'

    def make_next(self, record):
        with self.lock:
            name = self.display(record)
            if record.state != 'queued':
                return 'Only a queued download can move in line. ' + self.label(record) + '.'
            waiting = self.waiting()
            if waiting and waiting[0] is record:
                return name + ' is already next in line.'
            record.rank = min(r.rank or 0 for r in waiting) - 1
            self.changed()
            return name + ' is next in line.'


# Browser reports -----------------------------------------------------------------
def edge_report(params):
    """Embedded Edge (WebView2 DownloadOperation) words, as report() keywords."""
    state = str(params.get('state') or '')
    reason = str(params.get('reason') or 'None')
    values = dict(name=params.get('name'), path=params.get('path'), url=params.get('url'), new=bool(params.get('new')),
                  received=_number(params.get('received')), total=_number(params.get('total')),
                  can_resume=params.get('canResume'), refused=bool(params.get('refused')))
    if state == 'Completed':
        values['state'] = 'finished'
    elif state == 'Interrupted':
        if reason == 'UserPaused':
            values['state'] = 'paused'
        elif reason == 'UserCanceled':
            values['state'] = 'cancelled'
        else:
            values['state'] = 'failed'
            values['reason'] = ('closed' if reason in ('UserShutdown', 'DownloadProcessCrashed')
                                else 'blocked' if reason in ('FileMalicious', 'FileBlockedByPolicy', 'FileSecurityCheckFailed')
                                else 'network' if reason.startswith('Network') else 'server' if reason.startswith('Server')
                                else 'file' if reason.startswith('File') else 'unknown')
    elif state == 'InProgress':
        values['state'] = 'downloading'
    return values


def chrome_report(item):
    """A chrome.downloads DownloadItem, as report() keywords."""
    state = str(item.get('state') or '')
    error = str(item.get('error') or '')
    values = dict(path=item.get('filename') or None, url=item.get('url'), new=bool(item.get('new')),
                  received=_number(item.get('received')), total=_number(item.get('total')),
                  can_resume=item.get('canResume'), refused=bool(item.get('refused')))
    if state == 'complete':
        values['state'] = 'finished'
    elif state == 'interrupted':
        if error == 'USER_CANCELED':
            values['state'] = 'cancelled'
        else:
            values['state'] = 'failed'
            values['reason'] = ('closed' if error in ('USER_SHUTDOWN', 'CRASH')
                                else 'blocked' if error in ('FILE_VIRUS_INFECTED', 'FILE_BLOCKED', 'FILE_SECURITY_CHECK_FAILED')
                                else 'network' if error.startswith('NETWORK') else 'server' if error.startswith('SERVER')
                                else 'file' if error.startswith('FILE') else 'unknown')
    elif str(item.get('danger') or 'safe') not in NO_ANSWER_NEEDED:
        values['state'] = 'blocked'
    elif item.get('paused'):
        values['state'] = 'paused'
    elif state == 'in_progress':
        values['state'] = 'downloading'
    return values


def _number(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


class EdgeDownloads:
    """Embedded Edge: requests go to the WebView2 host over its private pipe."""
    def __init__(self, pipe):
        self.pipe = pipe

    def control(self, ref, action):
        self.pipe.post('Host.downloadControl', {'id': ref, 'action': action})

    def set_folder(self, folder):
        self.pipe.post('Host.downloadFolder', {'path': folder})


class ChromeDownloads:
    """The user's Chrome: requests go to the extension over the browser link."""
    def __init__(self, relay):
        self.relay = relay

    def control(self, ref, action):
        self.relay.send_extension({'method': 'VoiceCompanion.downloadControl', 'params': {'id': ref, 'action': action}})


# The one manager for this app --------------------------------------------------------
_MANAGER = None
_MANAGER_LOCK = threading.Lock()


def manager(data_folder=None):
    """The shared manager. Browsers and the screen all reach the same one."""
    global _MANAGER
    with _MANAGER_LOCK:
        if data_folder is None:
            if _MANAGER is not None:
                return _MANAGER
            from regular_browser import data_folder as default_data
            data_folder = default_data()
        folder = Path(data_folder)
        if _MANAGER is None or os.path.normcase(str(_MANAGER.data_folder.resolve())) != os.path.normcase(str(folder.resolve())):
            _MANAGER = DownloadManager(folder)
        return _MANAGER


def current():
    return _MANAGER


def busy(source=None):
    return bool(_MANAGER is not None and _MANAGER.busy(source))
