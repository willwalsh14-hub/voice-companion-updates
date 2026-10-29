"""Spoken radio and podcast catalog, subscriptions, downloads, and timed recordings.

RadioSure is supported as a user-supplied RSD export. Its original live directory
is no longer a dependable service. Audio playback uses the included audio window;
recording accepts direct MP3/AAC/OGG streams, not HLS or playlist manifests.
"""
from list_announcements import name_first
import json
import hashlib
import re
import shutil
import threading
import time
import webbrowser
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib import request, parse
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from media_player import MediaPlayer, StreamWatch, seek_message, spoken_time as playback_time, _number
from time_date import DAYS, MONTHS, spoken_time

RADIO_API = 'https://de1.api.radio-browser.info/json/stations/search'
RADIO_ROOTS = ('https://all.api.radio-browser.info','https://de1.api.radio-browser.info',
               'https://nl1.api.radio-browser.info')
IPRD_CATALOG = 'https://iprd-org.github.io/iprd/site_data/metadata/catalog.json'
APPLE_API = 'https://itunes.apple.com/search'
# Apple's free podcast charts. The first takes a category (genre) number; the second
# is Apple's newer chart of all podcasts, used when the first does not answer. A chart
# names each show only by its Apple number, so APPLE_LOOKUP gives each show's feed.
APPLE_CHART = 'https://itunes.apple.com/us/rss/toppodcasts/limit={limit}{genre}/json'
APPLE_TOP_CHART = 'https://rss.marketingtools.apple.com/api/v2/us/podcasts/top/{limit}/podcasts.json'
APPLE_LOOKUP = 'https://itunes.apple.com/lookup'
CHART_SIZE = 25
TOP_PODCASTS = 'Top podcasts'
# Apple Podcasts categories with their Apple genre numbers (podcasts.apple.com/us/genre/<number>).
PODCAST_CATEGORIES = (('News', 1489), ('Comedy', 1303), ('True crime', 1488), ('Sports', 1545),
                      ('History', 1487), ('Health and fitness', 1512), ('Business', 1321),
                      ('Education', 1304), ('Religion and spirituality', 1314), ('Kids and family', 1305),
                      ('Music', 1310), ('Society and culture', 1324), ('Technology', 1318), ('Arts', 1301),
                      ('Science', 1533), ('TV and film', 1309))
# Other words people use for a category.
CATEGORY_WORDS = {'top': TOP_PODCASTS, 'top podcasts': TOP_PODCASTS, 'popular': TOP_PODCASTS, 'most popular': TOP_PODCASTS,
                  'all': TOP_PODCASTS, 'crime': 'True crime', 'health': 'Health and fitness', 'fitness': 'Health and fitness',
                  'religion': 'Religion and spirituality', 'spirituality': 'Religion and spirituality', 'faith': 'Religion and spirituality',
                  'kids': 'Kids and family', 'family': 'Kids and family', 'children': 'Kids and family',
                  'society': 'Society and culture', 'culture': 'Society and culture', 'tech': 'Technology',
                  'tv': 'TV and film', 'television': 'TV and film', 'film': 'TV and film', 'movies': 'TV and film',
                  'sport': 'Sports', 'business news': 'Business', 'art': 'Arts', 'politics': 'News'}
MAX_DOWNLOAD = 500 * 1024 * 1024
AUDIO_EXTENSIONS = ('.mp3', '.m4a', '.aac', '.ogg', '.opus')
PODCAST_REFRESH_SECONDS = 6 * 3600


def _safe(name):
    return re.sub(r'[^\w .-]', '', name).strip(' .')[:90] or 'Untitled'


def _http(url, limit=4_000_000):
    with request.urlopen(request.Request(url, headers={'User-Agent':'VoiceCompanion/0.2'}), timeout=20) as response:
        data = response.read(limit+1)
    if len(data)>limit: raise ValueError('The directory response is too large.')
    return data


def _json(url): return json.loads(_http(url, 30_000_000 if url==IPRD_CATALOG else 4_000_000))


def _counted(count, word):
    """'1 station', '2 stations'."""
    return str(count) + ' ' + word + ('' if count == 1 else 's')


def _spoken_when(when):
    """'Tuesday, October 13 at eight PM', as people say it; never 08:00 PM or an ISO date."""
    return DAYS[when.weekday()] + ', ' + MONTHS[when.month-1] + ' ' + str(when.day) + ' at ' + spoken_time(when)


def _http_url(url): return urlsplit(url).scheme in ('http','https') and bool(urlsplit(url).hostname)


def _episode_count(value):
    value=value.casefold().replace('-',' ').strip()
    if value in ('manual','all'): return value
    if value.isdecimal(): return int(value) if 1<=int(value)<=10000 else None
    units=dict(zip('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(),range(20)))
    units.update({'twenty':20,'thirty':30,'forty':40,'fifty':50,'sixty':60,
                  'seventy':70,'eighty':80,'ninety':90})
    number=0
    for word in value.replace(' and ',' ').split():
        if word=='hundred': number=max(1,number)*100
        elif word=='thousand': number=max(1,number)*1000
        elif word in units: number+=units[word]
        else: return None
    return number if 1<=number<=10000 else None


def _radio_number(value):
    """A station result number, including common spoken ordinals."""
    value = value.casefold().strip()
    value = re.sub(r'^(?:the|station|radio station|number)\s+', '', value)
    value = re.sub(r'\s+(?:one|station)$', '', value) if value.startswith(('first ', 'second ')) else value
    ordinals = dict(zip('first second third fourth fifth sixth seventh eighth ninth tenth'.split(), range(1, 11)))
    ordinals.update({'won':1,'to':2,'too':2,'for':4,'ate':8})
    number = ordinals.get(value)
    if number is None:
        number = _episode_count(value)
    return number if isinstance(number,int) and 1 <= number <= 30 else None


def _station_words(value):
    """Normalize call letters and spoken FM decimals for result matching."""
    value = value.casefold().replace(' point ', '.').replace(' dot ', '.')
    value = re.sub(r'(?<=\d)\s*\.\s*(?=\d)', '.', value)
    tokens = re.findall(r'\d+\.\d+|[a-z]+|\d+', value)
    joined = []
    for token in tokens:
        if len(token) == 1 and token.isalpha() and joined and len(joined[-1]) <= 4 and joined[-1].isalpha():
            joined[-1] += token
        else:
            joined.append(token)
    return joined


NOTHING_PLAYING = ('Nothing is playing. To start, say Play and a station name, such as Play WGN. '
                   'Or choose Media on the main menu for radio, podcasts, and your own files.')
# "What's playing?" in the words people use. Ambiguous ones ("What is this?") count only
# while something is loaded; the app decides that.
NOW_PLAYING_PHRASES = ('what s playing', 'whats playing', 'what is playing', 'what s playing now', 'whats playing now',
                       'what is playing now', 'what am i listening to', 'what station is this',
                       'what station is playing', 'what station am i listening to', 'which station is this',
                       'what episode is this', 'what episode is playing', 'which episode is this',
                       'what podcast is this', 'what podcast is playing', 'what file is playing', 'what song is this',
                       'what video is this', 'what is this video', 'what video is playing', 'which video is this')
NOW_PLAYING_AMBIGUOUS = ('what is this', 'what s this', 'whats this')


def now_playing_request(spoken):
    """'clear' for "What's playing?" and the like, 'ambiguous' for "What is this?", else None.
    Recognizer punctuation and capitals do not matter."""
    words = ' '.join(re.sub(r'[^\w\s]', ' ', str(spoken).casefold()).split())
    words = re.sub(r'^(?:please |hey |so |and )|(?: please| now please)$', '', words)
    if words in NOW_PLAYING_PHRASES:
        return 'clear'
    if words in NOW_PLAYING_AMBIGUOUS:
        return 'ambiguous'
    return None


# "Play WGN", "Play station WGN", "Play WGN radio": a station by name from any screen
# where words are commands. These words after Play mean something else.
NOT_STATION_NAMES = ('audio', 'radio', 'podcast', 'podcasts', 'the podcast', 'media', 'it', 'this', 'that',
                     'this one', 'that one', 'or pause', 'pause', 'next', 'previous', 'next one', 'previous one',
                     'next episode', 'previous episode', 'the next episode', 'the previous episode', 'station', 'a station',
                     'the station', 'radio station', 'a radio station', 'something', 'again', 'music', 'file', 'the file',
                     'a file', 'media file', 'a media file', 'my file', 'video', 'the video', 'all')
STATION_FILLER_WORDS = ('the', 'a', 'an', 'my', 'some', 'it', 'again', 'me', 'this', 'that', 'something', 'anything')
NOT_STATION_STARTS = ('episode', 'download', 'recording', 'preset', 'favorite', 'podcast ', 'the podcast ', 'number ',
                      'station number ', 'file ', 'the file ', 'media file ', 'chapter', 'next ', 'previous ')


def station_request(spoken):
    """The station name in "Play WGN", "Play station WGN" or "Play WGN radio.", else None."""
    text = ' '.join(re.sub(r'[,;:!?]+', ' ', str(spoken)).split()).rstrip('.').strip()
    match = re.fullmatch(r'(?:please )?play (?:(?:the )?(?:radio station|station|radio) )?(.+?)(?: radio station| station| radio| on the radio)?(?: please)?', text, re.I)
    if not match:
        return None
    # Recognizers and typists may quote the name: "Play "WGN"".
    name = match[1].strip(' .\'"\u201c\u201d\u2018\u2019')
    folded = name.casefold()
    # "Play the radio", "Play it again": words that name no station.
    if all(word in STATION_FILLER_WORDS for word in folded.split()):
        return None
    if (not name or folded in NOT_STATION_NAMES or folded.startswith(NOT_STATION_STARTS)
            or _radio_number(folded) is not None or folded.isdigit()):
        return None
    return name


# Spoken changes to the audio itself. "Louder", "faster" and "volume up" alone change
# Companion's voice, so these always name the audio (as YouTube's say "video").
MEDIA_LEVEL_WORDS = {}
for _words, _change in ((('audio volume up', 'audio louder', 'louder audio', 'media volume up', 'turn the audio up'), ('volume', 0.1)),
                        (('audio volume down', 'audio quieter', 'quieter audio', 'audio softer', 'media volume down', 'turn the audio down'), ('volume', -0.1)),
                        (('faster audio', 'audio faster', 'speed up audio', 'speed up the audio'), ('speed', 0.25)),
                        (('slower audio', 'audio slower', 'slow down audio', 'slow down the audio'), ('speed', -0.25)),
                        (('normal audio speed', 'audio normal speed', 'normal speed audio'), ('speed', 0))):
    for _word in _words:
        MEDIA_LEVEL_WORDS[_word] = _change

# "Browse podcasts" lists the categories; these name the overall chart.
BROWSE_CATEGORIES = ('browse podcasts', 'browse podcast', 'browse podcast categories', 'browse categories',
                     'podcast categories', 'list podcast categories', 'browse for a podcast', 'browse for podcasts')
TOP_PODCAST_PHRASES = ('top podcasts', 'top podcast', 'top podcast chart', 'top podcast charts', 'podcast charts',
                       'popular podcasts', 'most popular podcasts', 'browse top podcasts')


def _category_words(text):
    words = str(text or '').casefold().replace('&', ' and ')
    words = ' '.join(re.sub(r'[^\w\s]', ' ', words).split())
    return re.sub(r'^(?:the )|(?: podcasts?| category| charts?| shows?)+$', '', words).strip()


def podcast_category(spoken):
    """The category named in speech or typing ("True Crime.", "health", "Kids & Family"),
    TOP_PODCASTS for the overall chart, or None when it is not a category."""
    words = _category_words(spoken)
    if not words:
        return None
    for name, _ in PODCAST_CATEGORIES:
        if words == _category_words(name):
            return name
    return CATEGORY_WORDS.get(words)


def chart_ids(answer):
    """Apple show numbers, in chart order, from either chart's JSON. Damaged parts are skipped."""
    feed = answer.get('feed') if isinstance(answer, dict) else None
    if not isinstance(feed, dict):
        return []
    entries = feed.get('entry', feed.get('results'))
    if isinstance(entries, dict):
        entries = [entries]   # a chart with one show
    found = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        number = entry.get('id')
        if isinstance(number, dict):   # the category chart: {"attributes": {"im:id": "123"}}
            attributes = number.get('attributes')
            number = attributes.get('im:id') if isinstance(attributes, dict) else None
        number = str(number or '').strip()
        if number.isdigit() and number not in found:
            found.append(number)
    return found


def _shows(results):
    """Shows with a working feed, from Apple search or lookup results."""
    shows = []
    for r in results if isinstance(results, list) else []:
        if isinstance(r, dict) and isinstance(r.get('feedUrl'), str) and _http_url(r['feedUrl']):
            name = str(r.get('collectionName') or r.get('trackName') or '').strip() or 'Untitled podcast'
            shows.append({'name': name, 'feed': r['feedUrl'], 'genre': str(r.get('primaryGenreName') or ''),
                          'apple_id': str(r.get('collectionId') or '')})
    return shows


def _sentence(text):
    text = str(text).strip()
    return text if text.endswith(('.', '!', '?')) else text + '.'


def _interval_seconds(value):
    """Understand spoken durations without changing playback when ambiguous."""
    words={'a':1,'an':1,'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,
           'seven':7,'eight':8,'nine':9,'ten':10,'eleven':11,'twelve':12,
           'thirteen':13,'fourteen':14,'fifteen':15,'sixteen':16,'seventeen':17,
           'eighteen':18,'nineteen':19,'twenty':20,'thirty':30,'forty':40,
           'fifty':50,'sixty':60,'seventy':70,'eighty':80,'ninety':90}
    text=value.casefold().replace('-',' ').replace('half an hour','30 minutes').replace('half hour','30 minutes')
    text=re.sub(r'\b(?:for|by|and)\b',' ',text)
    number_word='|'.join(sorted((re.escape(word) for word in (*words,'hundred')),key=len,reverse=True))
    pattern=rf'((?:\d+|{number_word})(?:\s+(?:\d+|{number_word}))*)\s+(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b'
    total=0
    for match in re.finditer(pattern,text):
        phrase=match[1].split()
        number=0
        for word in phrase:
            if word.isdecimal(): number+=int(word)
            elif word=='hundred': number=max(1,number)*100
            elif word in words: number+=words[word]
            else: return None
        unit=match[2]
        total+=number*(3600 if unit.startswith(('hour','hr')) else 60 if unit.startswith(('minute','min')) else 1)
    if not total or total>7*86400: return None
    leftover=re.sub(pattern,' ',text)
    return total if not leftover.strip() else None


class MediaHub:
    SOURCES = {'all':'all','automatic':'all','radio browser':'radio-browser',
               'iprd':'iprd','radio sure':'radiosure','radiosure':'radiosure'}
    def __init__(self, folder, opener=None, fetch_json=None, notify=None, clock=None, player=None):
        self.folder = Path(folder)
        self.opener = opener or webbrowser.open
        self.player = player or MediaPlayer(self.folder/'Audio Player')
        self.fetch_json = fetch_json or _json
        self.notify = notify or (lambda text: None)
        self.clock = clock or datetime.now
        self.stations = []
        self.station_index = None
        self.station_choices = []
        self.station_choice_index = 0
        self.shows = []
        self.show_index = None
        self.awaiting_podcast_search = False
        # "Browse podcasts": the list of categories, and which one is chosen.
        self.browsing_categories = False
        self.category_index = 0
        # The category whose chart is in self.shows, so Escape can go back to the categories.
        self.shows_category = None
        self.episode_index = None
        self.browsing_episodes = False
        self.download_errors = {}
        self.episodes = []
        self.current_station = None
        self.current_feed = None
        self.current_episode_index = None
        self.chapters = []
        self.recording = None
        self.record_stop = threading.Event()
        self._lock = threading.RLock()
        self._scheduler = None
        self._last_refresh_attempt = 0
        self._last_podcast_refresh_attempt = 0
        self._podcast_worker = None
        self._podcast_refresh_pending = False
        self._downloads = set()
        self.awaiting_radio_search = False
        self.preset_index = None
        self.preset_delete_pending = None
        self.preset_browsing = False
        # What was opened, for "What's playing?": (address, details).
        self._playing = None
        self._watch = StreamWatch()

    def _path(self, name): return self.folder / name

    def _load(self, name, default):
        try: return json.loads(self._path(name).read_text(encoding='utf-8'))
        except (OSError, ValueError): return default

    def _load_as(self, name, default):
        """Like _load, but a damaged file of the wrong shape (a list where settings
        belong) reads as the default, so it never stops the app."""
        value = self._load(name, default)
        return value if isinstance(value, type(default)) else default

    def _stations_file(self, name):
        """A saved station list, without damaged entries."""
        return [s for s in self._load_as(name, []) if isinstance(s, dict)]

    def _save(self, name, value):
        self.folder.mkdir(parents=True, exist_ok=True)
        temp = self._path(name+'.writing')
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(self._path(name))

    def presets(self):
        saved = self._load('radio-presets.json', [])
        if not isinstance(saved, list): return []
        return [entry for entry in saved if isinstance(entry, dict) and
                isinstance(entry.get('name'), str) and isinstance(entry.get('url'), str) and
                _http_url(entry['url'])][:100]

    def selected_preset(self, spoken=''):
        saved = self.presets()
        if not saved: return None, None
        name = spoken.strip().casefold()
        if name.isdecimal():
            index = int(name)-1
        elif name:
            index = next((i for i, station in enumerate(saved) if station['name'].casefold() == name), -1)
        else:
            index = self.preset_index if self.preset_index is not None else 0
        return (saved, index) if 0 <= index < len(saved) else (saved, None)

    def radio_preset_command(self, cmd, text=None):
        if self.preset_delete_pending:
            name = self.preset_delete_pending
            if cmd in ('no', 'no thanks', 'cancel', 'cancel delete'):
                self.preset_delete_pending = None
                return 'Preset deletion canceled.'
            if cmd in ('yes', 'yes delete', 'yes delete preset', 'confirm delete preset'):
                self.preset_delete_pending = None
                saved = self.presets()
                remaining = [station for station in saved if station['name'].casefold() != name.casefold()]
                if len(remaining) == len(saved): return 'That preset changed. Nothing was deleted.'
                self._save('radio-presets.json', remaining)
                self.preset_index = min(self.preset_index or 0, len(remaining)-1) if remaining else None
                return 'Deleted preset ' + name + '.'
            self.preset_delete_pending = None
        if cmd in ('list presets', 'radio presets', 'my presets', 'preset list',
                   'list favorites', 'favorites list', 'favorite list', 'list radio favorites'):
            saved = self.presets()
            if not saved: return 'No radio presets yet. Select a station and say save preset.'
            self.preset_index = 0
            self.preset_browsing = True
            return (name_first('Preset 1 of ' + str(len(saved)) + ': ' + saved[0]['name'] +
                    '. Say next or previous, then that one to play. Say delete preset to remove it.'))
        if cmd in ('next preset', 'previous preset') or self.preset_browsing and cmd in ('next', 'previous'):
            saved = self.presets()
            if not saved: return 'No radio presets yet.'
            forward = cmd in ('next preset', 'next')
            current = self.preset_index if self.preset_index is not None else (0 if forward else len(saved)-1)
            index = current + (1 if forward else -1)
            if self.preset_index is None: index = current
            if not 0 <= index < len(saved): return 'That is the ' + ('last' if index >= len(saved) else 'first') + ' preset.'
            self.preset_index = index
            return name_first('Preset ' + str(index+1) + ' of ' + str(len(saved)) + ': ' + saved[index]['name'] + '. Say play preset or delete preset.')
        if self.preset_browsing and cmd in ('that one', 'confirm', 'confirm that', 'okay', 'ok'):
            return self.radio_preset_command('play preset')
        save = re.fullmatch(r'(?:save|add) (?:preset|favorite)(?: as (.+))?', cmd)
        if save:
            station = (self.stations[self.station_index] if self.stations and self.station_index is not None
                       else self.current_station)
            if not station or not _http_url(station.get('url', '')):
                return 'Select or play a radio station first, then say save preset.'
            # The name as said or typed, with its capitals ("Jazz", not "jazz").
            spoken = re.fullmatch(r'(?:save|add) (?:preset|favorite)(?: as (.+))?', ' '.join((text or '').split()), re.I)
            name = ((spoken[1] if spoken else save[1]) or station['name']).strip()
            if not name or len(name) > 80 or any(char in name for char in '\r\n'):
                return 'Choose a shorter preset name without line breaks.'
            saved = self.presets()
            if any(item['name'].casefold() == name.casefold() for item in saved):
                return 'Preset ' + name + ' already exists. Nothing changed.'
            if len(saved) >= 100: return 'The preset list is full. Delete an old preset first.'
            saved.append({'name':name, 'url':station['url']})
            self._save('radio-presets.json', saved)
            self.preset_index = len(saved)-1
            return 'Saved preset ' + name + '. Say play preset ' + name + ' later.'
        match = re.fullmatch(r'(?:play|open) preset(?: (.+))?', cmd)
        if match:
            saved, index = self.selected_preset(match[1] or '')
            if index is None: return 'I could not find that preset. Say list presets.'
            station = saved[index]
            try: self.player.open(station['url'], station['name'], 'radio')
            except Exception: return 'Preset ' + station['name'] + ' could not play. The stream may have changed.'
            self._remember(station['url'], what='station', name=station['name'])
            self.preset_index = index
            self.station_index = None
            self.current_station = station
            self.preset_browsing = True
            return 'Playing preset ' + station['name'] + '. Say pause or stop.'
        match = re.fullmatch(r'(?:delete|remove) preset(?: (.+))?', cmd)
        if match:
            saved, index = self.selected_preset(match[1] or '')
            if index is None: return 'I could not find that preset. Say list presets.'
            self.preset_index = index
            self.preset_delete_pending = saved[index]['name']
            return 'Delete preset ' + self.preset_delete_pending + '? Say yes or no.'
        return None

    def _radio_query(self, query):
        errors=[]
        for root in RADIO_ROOTS:
            try:
                return self.fetch_json(root+'/json/stations/search?'+parse.urlencode(query))
            except (OSError,ValueError,TimeoutError) as exc:
                errors.append(str(exc))
        raise OSError('Radio Browser mirrors are unavailable.')

    def source(self):
        selected=self._load_as('radio-preferences.json',{}).get('source','all')
        return selected if selected in self.SOURCES.values() else 'all'

    def choose_source(self, name):
        selected=self.SOURCES.get(name.strip().casefold())
        if selected is None:
            return ('Available station databases are All, Radio Browser, IPRD, and RadioSure file. '
                    'TuneIn and ooTunes are not connected directory sources.')
        if selected=='radiosure' and not self._stations_file('radiosure-stations.json'):
            return 'Import a RadioSure station file first. Your station database is still '+self.source()+'.'
        self._save('radio-preferences.json',{'source':selected})
        self.stations=[]
        labels={'all':'All','radio-browser':'Radio Browser','iprd':'IPRD','radiosure':'RadioSure file'}
        return 'Station database set to '+labels[selected]+'.'

    def refresh_iprd(self, force=False):
        stamp=self._load_as('radio-iprd-status.json',{})
        if not force and time.time()-stamp.get('checked',0)<24*3600:
            return 'IPRD catalog was checked in the last 24 hours.'
        catalog=self.fetch_json(IPRD_CATALOG)
        if not isinstance(catalog,dict) or not isinstance(catalog.get('stations'),list):
            raise ValueError('The IPRD catalog format was not recognized.')
        normalized=[]
        for item in catalog['stations']:
            if not isinstance(item,dict): continue
            for stream in item.get('streams',[]):
                if not isinstance(stream,dict) or not _http_url(stream.get('url','')): continue
                normalized.append({'name':item.get('name',''),'url':stream['url'],
                    'country':item.get('country',''),'tags':', '.join(item.get('genres',[])),
                    'codec':stream.get('format',''),'source':'IPRD'})
                break
        if not normalized: raise ValueError('The IPRD catalog contained no usable streams.')
        self._save('radio-iprd-stations.json',normalized[:100000])
        self._save('radio-iprd-status.json',{'checked':time.time(),'count':len(normalized)})
        return f'IPRD station catalog refreshed with {len(normalized)} stations.'

    def _mark_bad(self, station):
        bad=self._load_as('radio-failures.json',{})
        bad[station['url']] = time.time()
        self._save('radio-failures.json',bad)

    def _usable(self, station):
        if station.get('lastcheckok') in (0,False,'0'): return False
        bad=self._load_as('radio-failures.json',{})
        try: return time.time()-float(bad.get(station.get('url',''),0))>24*3600
        except (TypeError,ValueError): return True

    def import_radiosure(self, path):
        source = Path(path)
        if source.suffix.lower() != '.rsd' or source.stat().st_size > 15_000_000:
            raise ValueError('Choose a RadioSure .rsd station file smaller than 15 MB.')
        rows = []
        for line in source.read_text(encoding='utf-8-sig', errors='replace').splitlines():
            parts = line.split('\t')
            if len(parts)<6: continue
            name, _, genre, country, language = parts[:5]
            url = next((part.strip() for part in parts[5:] if _http_url(part.strip())), '')
            if name.strip() and url:
                rows.append({'name':name.strip(), 'url':url, 'tags':genre, 'country':country,
                             'language':language, 'source':'RadioSure file'})
        if not rows: raise ValueError('No usable stations were found in that RadioSure file.')
        self._save('radiosure-stations.json', rows[:50000])
        return f'Imported {min(len(rows),50000)} RadioSure stations. Search radio to use both directories.'

    def search_radio(self, kind, value):
        value = value.strip()
        if not value: return 'Say a station name, genre, country, state, or city.'
        self.preset_browsing = False
        param = {'station':'name','name':'name','genre':'tag','country':'countrycode',
                 'state':'state','location':'state','city':'state'}.get(kind,'name')
        countries = {'united states':'US','usa':'US','canada':'CA','united kingdom':'GB',
                     'uk':'GB','australia':'AU','germany':'DE','france':'FR'}
        query = countries.get(value.casefold(), value.upper() if param=='countrycode' and len(value)==2 else value)
        # Radio Browser keeps its tags in small letters ("jazz"); a typed "Jazz" finds the same.
        if param=='tag': query=query.casefold()
        options={param:query,'limit':60,'hidebroken':'true','order':'votes','reverse':'true'}
        online_error = ''
        source=self.source()
        online=[]
        if source in ('all','radio-browser'):
            try: online = self._radio_query(options)
            except (OSError, ValueError, TimeoutError):
                online_error = 'The live directory is unavailable. '
        local=[]
        if source in ('all','radiosure'): local += self._stations_file('radiosure-stations.json')
        if source in ('all','iprd'): local += self._stations_file('radio-iprd-stations.json')
        key = {'name':'name','tag':'tags','countrycode':'country','state':'state'}[param]
        country_aliases = {'US':'united states', 'CA':'canada', 'GB':'united kingdom',
                           'AU':'australia', 'DE':'germany', 'FR':'france'}
        def matches(value):
            value=str(value).casefold()
            if param!='countrycode':
                return query.casefold() in value
            # Match a country code or name as whole words, so 'US' does not match
            # Russia or Australia.
            return any(re.search(r'(?<!\w)'+re.escape(candidate)+r'(?!\w)',value)
                       for candidate in (query.casefold(),country_aliases.get(query,'')) if candidate)
        local = [s for s in local if matches(s.get(key,''))]
        if kind=='city' and not online and not local:
            return 'The directory does not reliably label stations by city. Try a station name or state.'
        seen = set()
        self.stations = []
        self.station_index = None
        self.station_choices = []
        for entry in list(online or []) + local:
            stream = entry.get('url_resolved') or entry.get('url','')
            identity = (entry.get('name','').casefold(),stream.rstrip('/').casefold())
            if not entry.get('name') or not _http_url(stream) or identity in seen or not self._usable({**entry,'url':stream}): continue
            seen.add(identity)
            self.stations.append({'name':entry['name'], 'url':stream, 'country':entry.get('country',''),
                                  'state':entry.get('state',''), 'tags':entry.get('tags',''),
                                  'codec':entry.get('codec',''), 'hls':bool(entry.get('hls')),
                                  'uuid':entry.get('stationuuid',''), 'source':entry.get('source','Radio Browser')})
            if len(self.stations)>=30: break
        if not self.stations: return online_error + 'No matching stations. Try another name or genre.'
        self.station_index = 0
        return (online_error + 'Found '+_counted(len(self.stations),'station')+'. Station 1: '+self.stations[0]['name']+'. '
                'Say next or previous to browse, that one to play, or play followed by a number.')

    def station(self, number):
        i = int(number)-1
        if not 0<=i<len(self.stations): raise ValueError('That station number is unavailable. Search radio again.')
        return self.stations[i]

    def play_station(self, number):
        station = self.station(number)
        self.station_choices = []
        self.station_index = int(number)-1
        try: self.player.open(station['url'], station['name'], 'radio')
        except Exception:
            return station['name']+' could not start in the audio player. Say next station or choose another result.'
        self._remember(station['url'], what='station', name=station['name'])
        self.current_station = station
        return 'Playing ' + station['name'] + '. Say pause or stop.'

    # Words that do not tell stations apart: "WGN" and "WGN Radio" are the same request.
    STATION_FILLER = ('the', 'radio', 'station', 'fm', 'am')

    def play_by_name(self, name):
        """"Play WGN" from any screen: search as Radio search does, then play the only
        match or the one with exactly that name, or offer a short list of up to five."""
        found = self.search_radio('name', name)
        if not self.stations:
            unavailable = 'The live directory is unavailable. ' if found.startswith('The live directory') else ''
            return unavailable + 'I could not find a station called ' + name + '. Check the name, or say Radio to search for stations.'
        wanted = [word for word in _station_words(name) if word not in self.STATION_FILLER]
        def words(station): return [word for word in _station_words(station['name']) if word not in self.STATION_FILLER]
        exact = [i for i, station in enumerate(self.stations) if words(station) == wanted]
        if len(exact) == 1: return self.play_station(exact[0]+1)
        close = [i for i, station in enumerate(self.stations) if all(word in words(station) for word in wanted)]
        picks = (exact or close or list(range(len(self.stations))))[:5]
        if len(picks) == 1: return self.play_station(picks[0]+1)
        self.station_choices = picks
        self.station_choice_index = 0
        return ('I found ' + _counted(len(picks), 'station') + ' for ' + name + '. Choice 1 of ' + str(len(picks)) + ': ' +
                self.stations[picks[0]]['name'] + '. Say next or previous to hear another, then that one to play it. Say cancel to stop.')

    def play_station_named(self, phrase):
        if not self.stations:
            # No results yet: search for it, as "Play WGN" does on other screens.
            return self.play_by_name(phrase)
        wanted = _station_words(phrase)
        if not wanted:
            return 'Say a station name or a result number.'
        matches=[]
        for index, station in enumerate(self.stations):
            name = _station_words(station['name'])
            if all(token in name for token in wanted):
                matches.append(index)
        if len(matches)==1:
            return self.play_station(matches[0]+1)
        if len(matches)>1:
            self.station_choices = matches
            self.station_choice_index=0
            return ('I found '+str(len(matches))+' matching stations. Choice 1: '+self.stations[matches[0]]['name']+
                    '. Say next or previous to hear a choice, then that one to play it.')
        return 'I did not find '+phrase+' in these results. Say search followed by the station name to search again.'

    def _remember(self, url, **details):
        self._playing = (url, details)

    def _details(self):
        """What the hub opened, if it is still what the player has loaded."""
        if not self._playing: return {}
        url, details = self._playing
        return details if getattr(self.player, 'url', url) == url else {}

    def now_playing(self):
        """"What's playing?": the station, the podcast and episode, or the file, and
        whether it is paused, with the place and length when they are known."""
        player = self.player
        kind = getattr(player, 'kind', None)
        if not kind: return NOTHING_PLAYING
        details = self._details()
        what = details.get('what')
        title = details.get('name') or details.get('title') or getattr(player, 'title', None) or 'Unnamed audio'
        streaming = getattr(player, 'streaming', None)
        live = kind == 'radio' and (streaming() if callable(streaming) else what == 'station')
        if kind == 'radio':
            label = ('Station: ' if live else 'Radio recording: ') + _sentence(title)
        elif what == 'episode':
            label = ('Podcast: ' + _sentence(details['show']) + ' ' if details.get('show') else '') + 'Episode: ' + _sentence(details['title'])
        elif kind == 'podcast':
            label = 'Podcast episode: ' + _sentence(title)
        else:
            label = 'File: ' + _sentence(title)
        try: status = player.status()
        except Exception: status = None
        if not isinstance(status, dict): return label
        if status.get('failed'):
            return label + ' Stopped, because the stream failed. Say Play or press Control+P to try again, or choose another station.'
        state = 'Paused.' if status.get('paused') else 'Playing.'
        position, duration = _number(status.get('position')), _number(status.get('duration'))
        if live or position is None: return label + ' ' + state
        return (label + ' ' + state + ' Position ' + playback_time(position) +
                (' of ' + playback_time(duration) if duration else '') + '.')

    def check_stream(self, now):
        """One regular check of a playing radio stream. Returns (play number, message)
        the first time a stream that had started stops, errors or stalls; else None.
        Runs on the thread that opened the player."""
        player = self.player
        streaming = getattr(player, 'streaming', None)
        if not callable(streaming) or not streaming(): return None
        try: sample = player.health()
        except Exception: sample = None
        reason = self._watch.check(getattr(player, 'session', None), sample, now)
        if not reason: return None
        try: player.mark_failed()
        except Exception: pass
        name = self._details().get('name') or getattr(player, 'title', None) or 'The station'
        if reason == 'gone':
            message = name + ' stopped, because the audio player closed. Choose the station again to play it.'
        else:
            message = name + ' stopped playing. The station or the internet connection may be down. Say Play or press Control+P to try again, or choose another station.'
        return getattr(player, 'session', None), message

    def _record_worker(self, station, minutes, stop):
        url = station['url']
        if station.get('hls') or urlsplit(url).path.lower().endswith(('.m3u','.m3u8','.pls','.asx')):
            self.notify('That station uses a playlist or HLS stream. This recorder needs a direct audio stream.')
            return
        codec = str(station.get('codec','')).lower()
        ext = '.aac' if 'aac' in codec else '.ogg' if 'ogg' in codec else '.mp3'
        folder = self._path('Radio Recordings')
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / (_safe(station['name']) + ' ' + self.clock().strftime('%Y-%m-%d %H-%M-%S') + ext)
        deadline = time.monotonic()+minutes*60
        size = 0
        try:
            with request.urlopen(request.Request(url, headers={'User-Agent':'VoiceCompanion/0.2','Icy-MetaData':'0'}),timeout=20) as stream, target.open('wb') as output:
                content_type = stream.headers.get('Content-Type','').lower()
                if not any(t in content_type for t in ('audio/','application/octet-stream')):
                    raise ValueError('The station did not provide a direct audio stream.')
                while not stop.is_set() and time.monotonic()<deadline and size<MAX_DOWNLOAD:
                    chunk = stream.read(16384)
                    if not chunk: break
                    output.write(chunk); size += len(chunk)
            if size:
                self.notify('Saved radio recording ' + target.name + '.')
            else:
                target.unlink(missing_ok=True)
                self.notify('The recording had no audio. Nothing was saved.')
        except (OSError, ValueError, TimeoutError):
            if not size: target.unlink(missing_ok=True)
            self._mark_bad(station)
            self.notify('Radio recording stopped because the stream failed. Check saved recordings before retrying.')
        finally:
            with self._lock:
                if self.recording is threading.current_thread(): self.recording = None

    def record_now(self, station, minutes):
        if not 1<=minutes<=240: return 'Choose a recording length from 1 to 240 minutes.'
        with self._lock:
            if self.recording and self.recording.is_alive(): return 'A radio recording is already running. Say stop recording first.'
            self.record_stop = threading.Event()
            self.recording = threading.Thread(target=self._record_worker,args=(station,minutes,self.record_stop),daemon=True)
            self.recording.start()
        return f'Recording {station["name"]} for up to {minutes} minutes. Say stop recording to finish early.'

    def schedule(self, station, when, minutes, repeat=False):
        if not 1<=minutes<=240 or when<=self.clock(): return 'Choose a future time and a length from 1 to 240 minutes.'
        jobs = self._load('radio-schedule.json', [])
        if not isinstance(jobs, list):
            # Kept for a helper to inspect; never replaced by a new list.
            return 'The radio schedule is damaged, so nothing was scheduled. Ask your helper to check it.'
        jobs.append({'station':station,'when':when.isoformat(timespec='seconds'),'minutes':minutes,
                     'repeat':repeat,'id':str(time.time_ns())})
        self._save('radio-schedule.json',jobs)
        self.start_scheduler()
        return ('Scheduled ' + station['name'] + ' for ' + _spoken_when(when) +
                f' for {minutes} minutes' + (', every day' if repeat else '') + '. Keep Voice Companion running and the computer awake.')

    @staticmethod
    def _scheduled_when(job):
        """When a schedule entry records, in words, and whether it repeats."""
        try: when=_spoken_when(datetime.fromisoformat(job['when']))
        except (TypeError,ValueError): when='at a time that could not be read'
        return when+(', every day' if job.get('repeat') else '')

    def has_scheduled_recordings(self):
        jobs=self._load('radio-schedule.json',[])
        return isinstance(jobs,list) and bool(jobs)

    @staticmethod
    def _scheduled_name(job):
        """The station name of a schedule entry, or None if the entry is damaged."""
        station=job.get('station') if isinstance(job,dict) else None
        name=station.get('name') if isinstance(station,dict) else None
        return name if isinstance(name,str) and name and isinstance(job.get('when'),str) else None

    def _damaged_schedule_is_new(self, job):
        """True the first time a damaged entry is seen; the scheduler checks every 10 seconds."""
        try: key=json.dumps(job,sort_keys=True,default=str)
        except (TypeError,ValueError): key=repr(job)
        reported=self.__dict__.setdefault('_damaged_schedule_entries',set())
        if key in reported: return False
        reported.add(key)
        return True

    def tick(self):
        now=self.clock()
        with self._lock:
            jobs=self._load('radio-schedule.json', [])
            damaged=0
            if not isinstance(jobs,list):
                damaged+=self._damaged_schedule_is_new(jobs);jobs=[]
            changed=False
            kept=[]
            for job in jobs:
                try:
                    due=datetime.fromisoformat(job['when'])
                    due<=now
                    if not isinstance(job['station'],dict) or not isinstance(job['station'].get('name'),str): raise TypeError
                    int(job['minutes'])
                except (TypeError,KeyError,ValueError,AttributeError,OverflowError):
                    # Kept in the file for a helper to inspect, but never retried as an error.
                    damaged+=self._damaged_schedule_is_new(job);kept.append(job);continue
                if due<=now:
                    if now-due<=timedelta(minutes=5):
                        result=self.record_now(job['station'],int(job['minutes']))
                        self.notify(result)
                    else: self.notify('A scheduled radio recording was missed while the app was closed or asleep.')
                    changed=True
                    if job.get('repeat'):
                        while due<=now: due+=timedelta(days=1)
                        job['when']=due.isoformat(timespec='seconds')
                        kept.append(job)
                else: kept.append(job)
            if changed: self._save('radio-schedule.json',kept)
            if damaged:
                self.notify(('One scheduled radio recording is' if damaged==1 else f'{damaged} scheduled radio recordings are')+
                            ' damaged and will be skipped. Other recordings still work. Ask your helper to check the radio schedule.')
        if time.time()-self._last_refresh_attempt>24*3600:
            self._last_refresh_attempt=time.time()
            try: self.refresh_iprd()
            except (OSError,ValueError,TimeoutError): pass
        if time.time()-self._last_podcast_refresh_attempt>PODCAST_REFRESH_SECONDS:
            self._last_podcast_refresh_attempt=time.time()
            self.start_podcast_refresh()

    def start_scheduler(self):
        if self._scheduler and self._scheduler.is_alive(): return
        def run():
            failing=False
            while True:
                try: self.tick();failing=False
                except Exception:
                    # Say it once, not every 10 seconds, until a check works again.
                    if not failing: self.notify('The radio schedule could not be checked. Ask your helper to inspect it.')
                    failing=True
                time.sleep(10)
        self._scheduler=threading.Thread(target=run,daemon=True)
        self._scheduler.start()

    def search_podcasts(self, term):
        term=term.strip()
        if not term: return 'What podcast would you like to find? Say its name.'
        url=APPLE_API+'?'+parse.urlencode({'media':'podcast','entity':'podcast','limit':20,'term':term})
        try: answer=self.fetch_json(url)
        except (OSError,ValueError,TimeoutError):
            return 'Podcast search is not available right now. Check the internet connection, then try again.'
        results=answer.get('results',[]) if isinstance(answer,dict) else []
        self.shows=[{'name':str(r.get('collectionName') or 'Untitled podcast'), 'feed':r['feedUrl'],
                     'genre':str(r.get('primaryGenreName') or '')} for r in (results if isinstance(results,list) else [])
                    if isinstance(r,dict) and isinstance(r.get('feedUrl'),str) and _http_url(r['feedUrl'])]
        self.show_index=0 if self.shows else None
        self.shows_category=None
        self.browsing_categories=False
        self.browsing_episodes=False
        self.episode_index=None
        self.episodes=[]
        self.current_feed=None
        if not self.shows: return 'No podcast feeds found. Try another name or category.'
        return ('Found '+_counted(len(self.shows),'podcast')+'. Podcast 1: '+self.shows[0]['name']+'. '
                'Say next or previous to browse. Say that one to open episodes, or subscribe to save this feed.')

    def podcast_categories(self):
        """The Browse podcasts list: the overall chart first, then each category."""
        return [TOP_PODCASTS]+[name for name,_ in PODCAST_CATEGORIES]

    def category_choice(self):
        """Say the chosen category, as the list does."""
        names=self.podcast_categories()
        self.category_index=max(0,min(self.category_index or 0,len(names)-1))
        from menu_navigation import announce_item
        return announce_item(names[self.category_index],self.category_index,len(names))

    def browse_categories(self):
        self.browsing_categories=True
        self.browsing_episodes=False
        return ('Browse podcasts. Choose a category to hear its top podcasts. '+self.category_choice()+
                ' Say next or previous to move, and that one to open it.')

    def _chart(self, genre):
        """Top shows from Apple's chart, in chart order, each with its feed. Raises OSError or
        ValueError when the chart cannot be read."""
        url=APPLE_CHART.format(limit=CHART_SIZE,genre='/genre='+str(genre) if genre else '')
        try: ids=chart_ids(self.fetch_json(url))
        except (OSError,ValueError,TimeoutError):
            if genre: raise
            ids=[]
        if not ids and not genre:
            ids=chart_ids(self.fetch_json(APPLE_TOP_CHART.format(limit=CHART_SIZE)))
        if not ids: return []
        answer=self.fetch_json(APPLE_LOOKUP+'?'+parse.urlencode({'id':','.join(ids[:CHART_SIZE]),'entity':'podcast'}))
        found={show['apple_id']:show for show in _shows(answer.get('results') if isinstance(answer,dict) else None)}
        return [found[number] for number in ids if number in found]

    def browse_category(self, spoken):
        """Top podcasts in a category, or in all of Apple Podcasts. Speech that names no
        category searches for it by name, as before."""
        name=podcast_category(spoken)
        if name is None: return self.search_podcasts(spoken)
        names=self.podcast_categories()
        self.category_index=names.index(name)
        genre=dict(PODCAST_CATEGORIES).get(name)
        try: shows=self._chart(genre)
        except (OSError,ValueError,TimeoutError): shows=[]
        if not shows and genre:
            # The chart did not answer: Apple's search within the same category still lists its shows.
            url=APPLE_API+'?'+parse.urlencode({'media':'podcast','entity':'podcast','limit':CHART_SIZE,
                                               'genreId':genre,'term':name.split(' and ')[0]})
            try:
                answer=self.fetch_json(url)
                shows=_shows(answer.get('results') if isinstance(answer,dict) else None)
            except (OSError,ValueError,TimeoutError): shows=[]
        if not shows:
            return ('The '+name.lower().replace('tv ','TV ')+' list is not available right now. Check the internet connection, '
                    'then try again. You can also search for a podcast by name.') if name!=TOP_PODCASTS else (
                    'The top podcasts list is not available right now. Check the internet connection, then try again. '
                    'You can also search for a podcast by name.')
        self.shows=shows
        self.show_index=0
        self.shows_category=name
        self.browsing_categories=False
        self.browsing_episodes=False
        self.episode_index=None
        self.episodes=[]
        self.current_feed=None
        return (name+'. '+_counted(len(shows),'top podcast')+'. Podcast 1: '+shows[0]['name']+'. '
                'Say next or previous to browse. Say that one to open episodes, or subscribe.')

    def _feed_episodes(self, show):
        if not _http_url(show['feed']): raise ValueError('The podcast feed address is invalid.')
        root=ET.fromstring(_http(show['feed'],5_000_000))
        # Some feeds declare a default namespace (e.g. xmlns="http://backend.userland.com/rss2"),
        # which hides channel/item/title from plain lookups. Drop only that default namespace.
        if root.tag.startswith('{'):
            default=root.tag[:root.tag.index('}')+1]
            for element in root.iter():
                if isinstance(element.tag,str) and element.tag.startswith(default):
                    element.tag=element.tag[len(default):]
        episodes=[]
        for item in root.findall('.//channel/item'):
            title=(item.findtext('title') or '').strip() or 'Untitled episode'
            enclosure=item.find('enclosure')
            url=enclosure.get('url','') if enclosure is not None else ''
            if not _http_url(url): continue
            chapter_tag=item.find('{https://podcastindex.org/namespace/1.0}chapters')
            chapter_url=chapter_tag.get('url','') if chapter_tag is not None else ''
            published=item.findtext('pubDate') or ''
            try: timestamp=parsedate_to_datetime(published).timestamp()
            except (ValueError,TypeError,OverflowError): timestamp=0
            episodes.append({'title':title.strip(),'url':url,'show':show['name'],
                             'feed':show['feed'],
                             'chapters_url':chapter_url if _http_url(chapter_url) else '',
                             'published':timestamp})
        # Newest first, preserving feed order for shows without publication dates.
        if any(e['published'] for e in episodes):
            episodes.sort(key=lambda e:e['published'],reverse=True)
        return episodes

    def open_podcast(self, number, feed=None):
        index=int(number)-1
        if feed is None and not 0<=index<len(self.shows): return 'That podcast number is unavailable.'
        show = self.shows[index] if feed is None else feed
        if feed is None: self.show_index=index
        try: episodes=self._feed_episodes(show)
        except (OSError,ValueError,ET.ParseError,TimeoutError):
            cached=next((s.get('episodes',[]) for s in self.saved_subscriptions()
                         if s['feed']==show['feed']),[])
            if not cached: return 'The podcast feed is unavailable. Try again later.'
            episodes=cached
        self.episodes=episodes
        self.current_feed=show['feed']
        self.current_episode_index=None
        self.browsing_episodes=True
        self.episode_index=0 if episodes else None
        self.chapters=[]
        if not episodes: return 'No playable episodes were found in this podcast feed.'
        return (show['name']+'. Found '+_counted(len(episodes),'episode')+'. Episode 1: '+episodes[0]['title']+
                '. Say next or previous to browse, that one to play, or download this episode.')

    def subscribe(self, number):
        i=int(number)-1
        if not 0<=i<len(self.shows): return 'That podcast number is unavailable.'
        self.show_index=i
        show=self.shows[i]
        self.current_feed=show['feed']
        with self._lock:
            subs=self._load('podcast-subscriptions.json',[])
            if not isinstance(subs,list):
                # Kept for a helper to inspect; never replaced by a new list.
                return 'Your podcast subscriptions file is damaged, so '+show['name']+' was not added. Ask your helper to check it.'
            if not any(isinstance(s,dict) and s.get('feed')==show['feed'] for s in subs):
                subs.append({**show,'download_limit':'manual','episodes':[]})
                self._save('podcast-subscriptions.json',subs)
        self.start_podcast_refresh()
        return ('Subscribed to '+show['name']+'. The feed refreshes automatically while Companion runs. '
                'Downloads are manual until you choose an auto download limit.')

    def saved_subscriptions(self):
        """Subscriptions with a name and a feed address. A damaged entry stays in
        the file for a helper, but is never listed, refreshed or played."""
        saved=self._load('podcast-subscriptions.json',[])
        return [s for s in (saved if isinstance(saved,list) else []) if isinstance(s,dict)
                and isinstance(s.get('name'),str) and s['name'].strip() and isinstance(s.get('feed'),str) and _http_url(s['feed'])]

    def subscriptions(self):
        subs=self.saved_subscriptions()
        self.shows=subs
        self.show_index=0 if subs else None
        self.shows_category=None
        self.browsing_categories=False
        self.browsing_episodes=False
        self.episode_index=None
        return ('Found '+_counted(len(subs),'subscription')+'. Subscription 1: '+subs[0]['name']+
                ', automatic downloads '+str(subs[0].get('download_limit','manual'))+
                '. Say next or previous, that one to open episodes, or subscribe.' if subs else 'No podcast subscriptions yet.')

    def set_auto_download(self, limit, number=None):
        policy=_episode_count(limit)
        if policy is None: return 'Choose manual, all, or a number from 1 to 10,000.'
        with self._lock:
            subs=self._load('podcast-subscriptions.json',[])
            if not isinstance(subs,list): subs=[]
            if number is not None:
                i=int(number)-1
                if not 0<=i<len(self.shows): return 'That podcast number is unavailable. Say list subscriptions first.'
                feed=self.shows[i]['feed']
            else: feed=self.current_feed
            show=next((s for s in subs if isinstance(s,dict) and s.get('feed')==feed and isinstance(s.get('name'),str)),None)
            if show is None: return 'Open and subscribe to the show first, or say list subscriptions and give its number.'
            show['download_limit']=policy
            self._save('podcast-subscriptions.json',subs)
        if policy!='manual': self.start_podcast_refresh()
        return (show['name']+' will '+('download episodes manually.' if policy=='manual' else
                'automatically keep '+('all episodes available in its feed.' if policy=='all' else
                f'the newest {policy} downloaded episodes. Older automatic downloads may be removed.')))

    def start_podcast_refresh(self):
        with self._lock:
            if self._podcast_worker and self._podcast_worker.is_alive():
                self._podcast_refresh_pending=True
                return False
            def run():
                while True:
                    try: self.refresh_subscriptions()
                    except Exception: self.notify('Podcast feeds could not be checked. They will be retried later.')
                    with self._lock:
                        if not self._podcast_refresh_pending: break
                        self._podcast_refresh_pending=False
            self._podcast_worker=threading.Thread(target=run,daemon=True)
            self._podcast_worker.start()
        return True

    def refresh_subscriptions(self):
        saved=self.saved_subscriptions()
        failures=0
        pending=[]
        for show in saved:
            try: episodes=self._feed_episodes(show)
            except (OSError,ValueError,ET.ParseError,TimeoutError):
                failures+=1
                continue  # Keep the last known list and retry at the next refresh.
            with self._lock:
                current=self._load('podcast-subscriptions.json',[])
                entry=next((s for s in (current if isinstance(current,list) else []) if isinstance(s,dict) and s.get('feed')==show['feed']),None)
                if entry is None: continue
                old={e.get('url') for e in entry.get('episodes',[])}
                new=sum(e['url'] not in old for e in episodes) if old else 0
                entry['episodes']=episodes
                entry['last_checked']=time.time()
                policy=entry.get('download_limit','manual')
                self._save('podcast-subscriptions.json',current)
            if new: self.notify(f'{new} new episodes in {show["name"]}.')
            if policy!='manual': pending.append((show,episodes,policy))
        if failures: self.notify(f'{failures} subscribed podcast feeds could not refresh. Their saved lists remain available.')
        for show,episodes,policy in pending: self._auto_download(show,episodes,policy)
        return f'Checked {len(saved)-failures} of {len(saved)} subscribed podcast feeds.'

    def _auto_download(self, show, episodes, policy):
        desired=episodes if policy=='all' else episodes[:int(policy)]
        feed=show['feed']
        folder=self._path('Podcast Downloads');folder.mkdir(parents=True,exist_ok=True)
        completed=0
        failed=0
        first_error=None
        for episode in desired:
            url=episode['url']
            with self._lock:
                current=self._load('podcast-subscriptions.json',[])
                selected=next((s.get('download_limit','manual') for s in (current if isinstance(current,list) else [])
                               if isinstance(s,dict) and s.get('feed')==feed),'manual')
                if selected!=policy: return
                manifest=self._load_as('podcast-auto-files.json',{})
                existing=manifest.get(feed,{})
                if url in existing and (folder/Path(existing[url]).name).is_file(): continue
            if shutil.disk_usage(folder).free<MAX_DOWNLOAD+100_000_000:
                self.notify('Automatic podcast downloads paused because disk space is low.')
                failed+=1
                break
            ext=Path(urlsplit(url).path).suffix.lower()
            if ext not in ('.mp3','.m4a','.aac','.ogg','.opus'):ext='.mp3'
            target=folder/(_safe(show['name']+' - '+episode['title'])[:70]+'-'+hashlib.sha256(url.encode()).hexdigest()[:12]+ext)
            if target.exists(): success=True
            else: success=self._download_worker(episode,target,target.with_suffix(ext+'.part'),announce=False)
            if success:
                with self._lock:
                    manifest=self._load_as('podcast-auto-files.json',{})
                    manifest.setdefault(feed,{})[url]=target.name
                    self._save('podcast-auto-files.json',manifest)
                completed+=1
            else: failed+=1
            if not success and first_error is None:
                with self._lock: first_error=self.download_errors.pop(url,None)
        if failed:
            self.notify(f'{failed} episodes from {show["name"]} could not download. '+
                        (first_error+' ' if first_error else '')+'Companion will try again later.')
        if completed: self.notify(f'{completed} episodes from {show["name"]} downloaded for offline listening.')
        if not failed: self._prune_auto(feed,{e['url'] for e in desired})

    def _prune_auto(self, feed, desired):
        with self._lock:
            manifest=self._load_as('podcast-auto-files.json',{})
            entries=manifest.get(feed,{})
            folder=self._path('Podcast Downloads')
            for url,name in list(entries.items()):
                if url in desired: continue
                (folder/Path(name).name).unlink(missing_ok=True)
                del entries[url]
            if entries: manifest[feed]=entries
            else: manifest.pop(feed,None)
            self._save('podcast-auto-files.json',manifest)

    def play_episode(self, number):
        i=int(number)-1
        if not 0<=i<len(self.episodes): return 'That episode number is unavailable.'
        episode=self.episodes[i]
        try: self.player.open(episode['url'],episode['title'],'podcast')
        except Exception: return 'This episode could not start. Try another episode.'
        self._remember(episode['url'], what='episode', show=episode.get('show',''), title=episode['title'])
        self.current_episode_index=i
        self.episode_index=i
        self.chapters=self._chapters(episode)
        return 'Playing '+episode['title']+'. Say pause, back 15 seconds, or next episode.'

    def _chapters(self, episode):
        url=episode.get('chapters_url','')
        if not _http_url(url): return []
        try:
            data=self.fetch_json(url)
            chapters=[]
            for entry in data.get('chapters',[])[:500]:
                seconds=float(entry.get('startTime',-1))
                if seconds>=0 and seconds<86400:
                    chapters.append({'time':seconds,'title':str(entry.get('title','Chapter'))[:150]})
            return sorted(chapters,key=lambda c:c['time'])
        except (OSError,ValueError,TypeError,AttributeError,TimeoutError): return []

    def chapter(self, direction):
        if not self.chapters: return 'This episode does not provide supported chapter markers.'
        current=self.player.control('position')
        if current is None: return 'Play the episode first.'
        if direction=='next':
            item=next((c for c in self.chapters if c['time']>current+1),None)
        else:
            item=next((c for c in reversed(self.chapters) if c['time']<current-2),None)
        if not item: return 'There is no '+direction+' chapter.'
        if self.player.control('seek',position=item['time']) is None: return 'This audio cannot seek.'
        return item['title']+'.'

    def audio_command(self, cmd):
        action={'toggle audio':'toggle','play':'play','resume':'play','pause':'pause','stop':'stop',
                'play radio':'play','resume radio':'play','pause radio':'pause','stop radio':'stop',
                'play podcast':'play','resume podcast':'play','pause podcast':'pause','stop podcast':'stop',
                'play audio':'play','resume audio':'play','pause audio':'pause','stop audio':'stop'}.get(cmd)
        if action:
            if not self.player.kind: return 'No audio is playing. Choose a station or episode first.'
            if 'radio' in cmd and self.player.kind!='radio' or 'podcast' in cmd and self.player.kind!='podcast':
                # Name what is really loaded: a media file is neither a station nor a podcast.
                if self.player.kind=='local': return 'A media file is selected. Say '+action+' audio.'
                if self.player.kind=='podcast': return 'A podcast is selected. Say '+action+' podcast.'
                return 'A station is selected. Say '+action+' radio.'
            try: state=self.player.control(action)
            except Exception: return 'The audio player could not respond. Try opening that audio again.'
            if action=='toggle':return 'Audio paused.' if state else 'Playing audio.'
            return {'play':'Playing audio.','pause':'Audio paused.','stop':'Audio stopped.'}[action]
        change=MEDIA_LEVEL_WORDS.get(cmd)
        if change:
            # "Louder" and "faster" alone change Companion's own voice; these name the audio.
            if not self.player.kind: return 'No audio is playing. Choose a station or episode first.'
            return self.change_level(*change)
        match=re.fullmatch(r'(rewind|back|go back|fast forward|forward|skip ahead) (.+)',cmd)
        if match:
            amount=_interval_seconds(match[2])
            if amount is None: return 'Say a time such as 15 seconds, 30 minutes, or one hour.'
            direction=-amount if match[1] in ('rewind','back','go back') else amount
            noun={'podcast':'episode','local':'track'}.get(getattr(self.player,'kind',None),'audio')
            try: message=seek_message(self.player,direction,noun)
            except Exception: message=None
            return message or 'This audio cannot seek.'
        if cmd in ('next chapter','previous chapter'): return self.chapter(cmd.split()[0])
        if cmd in ('next episode','previous episode'):
            if self.current_episode_index is None: return 'Play an episode first.'
            index=self.current_episode_index+(1 if cmd.startswith('next') else -1)
            return self.play_episode(index+1) if 0<=index<len(self.episodes) else 'There is no '+cmd+'.'
        if cmd in ('next podcast','previous podcast','next show','previous show'):
            if not self.shows: return 'Search for podcasts or list subscriptions first.'
            current=next((i for i,s in enumerate(self.shows) if s.get('feed')==getattr(self,'current_feed',None)),0)
            index=current+(1 if cmd.startswith('next') else -1)
            return self.open_podcast(index+1) if 0<=index<len(self.shows) else 'There is no '+cmd+'.'
        return None

    def change_level(self, setting, step):
        """Audio volume up or down by a tenth, or speed by a quarter (0.5 to 3)."""
        player=self.player
        try:
            if setting=='volume':
                before=getattr(player,'volume',None)
                if step>0 and isinstance(before,(int,float)) and before>=1: return 'Loudest. Volume 100 percent.'
                if step<0 and isinstance(before,(int,float)) and before<=0: return 'Volume 0 percent. The audio is silent.'
                value=player.adjust('volume',step)
                return 'Volume '+str(int(round(float(value)*100)))+' percent.'
            if player.kind=='radio' and self.player.streaming():
                return 'A live station always plays at normal speed.'
            current=getattr(player,'rate',1)
            current=float(current) if isinstance(current,(int,float)) and not isinstance(current,bool) else 1.0
            if step==0: target=1.0
            else:
                target=round((current+step)*4)/4
                if target>3: return 'Fastest speed. Speed 3.'
                if target<.5: return 'Slowest speed. Speed 0.5.'
            rate=player.adjust('speed',target)
            return 'Normal speed.' if abs(float(rate)-1)<.01 else 'Speed '+('%g' % float(rate))+'.'
        except ValueError as exc:
            return str(exc)
        except Exception:
            return 'The audio player could not respond. Try opening that audio again.'

    def download_episode(self, number):
        i=int(number)-1
        if not 0<=i<len(self.episodes): return 'That episode number is unavailable.'
        episode=self.episodes[i]
        folder=self._path('Podcast Downloads'); folder.mkdir(parents=True,exist_ok=True)
        extension=Path(urlsplit(episode['url']).path).suffix.lower()
        if extension not in ('.mp3','.m4a','.aac','.ogg','.opus'): extension='.mp3'
        name=episode['show']+' - '+episode['title']
        stem=_safe(name)
        # Long names are cut to 90 characters; add a short URL hash so episodes that
        # differ only after the cut (Part One / Part Two) do not collide.
        if len(re.sub(r'[^\w .-]', '', name).strip(' .'))>90:
            stem=stem[:70]+'-'+hashlib.sha256(episode['url'].encode()).hexdigest()[:12]
        target=folder/(stem+extension)
        temp=target.with_suffix(target.suffix+'.part')
        with self._lock:
            if target in self._downloads: return 'That episode is already downloading.'
            if target.exists(): return 'That episode is already downloaded. Say list downloads.'
            self._downloads.add(target)
        threading.Thread(target=self._download_worker,args=(episode,target,temp),daemon=True).start()
        return 'Downloading '+episode['title']+'. I will announce when it finishes.'

    def _download_worker(self, episode, target, temp, announce=True):
        try:
            headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) VoiceCompanion/0.2',
                     'Accept':'audio/*,*/*;q=0.8'}
            if _http_url(episode.get('feed','')): headers['Referer']=episode['feed']
            try:
                stream=request.urlopen(request.Request(episode['url'],headers=headers),timeout=30)
            except HTTPError as exc:
                if exc.code!=403 or 'Referer' not in headers: raise
                # Some media hosts allow the feed's referrer; others reject it.
                alternate={key:value for key,value in headers.items() if key!='Referer'}
                stream=request.urlopen(request.Request(episode['url'],headers=alternate),timeout=30)
            with stream, temp.open('wb') as output:
                if 'text/html' in getattr(stream,'headers',{}).get('Content-Type','').lower():
                    raise ValueError('The podcast host returned a web page instead of audio.')
                total=0
                while chunk:=stream.read(65536):
                    total+=len(chunk)
                    if total>MAX_DOWNLOAD: raise ValueError('The episode exceeds the 500 MB download limit.')
                    output.write(chunk)
            if not total: raise ValueError('The episode returned no audio.')
            temp.replace(target)
            with self._lock: self.download_errors.pop(episode['url'],None)
            if announce: self.notify('Downloaded '+episode['title']+'. It is available offline in Podcast Downloads.')
            return True
        except (OSError,ValueError,TimeoutError) as exc:
            temp.unlink(missing_ok=True)
            reason=('Podcast host refused the download with HTTP '+str(exc.code)+'.' if isinstance(exc,HTTPError)
                    else 'Podcast host could not be reached.' if isinstance(exc,URLError)
                    else str(exc) if isinstance(exc,ValueError) else 'The network or disk interrupted the download.')
            with self._lock: self.download_errors[episode['url']]=reason
            print('Podcast download failed:',repr(exc),file=sys.stderr)
            if announce: self.notify('The episode download did not finish. '+reason+' Try again later.')
            return False
        finally:
            with self._lock: self._downloads.discard(target)

    def downloads(self):
        folder=self._path('Podcast Downloads')
        # Unfinished .part files (download running or interrupted) are not playable episodes.
        files=sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS),key=lambda p:p.name.casefold()) if folder.exists() else []
        return files

    def recordings(self):
        """Saved radio recordings in the one order used for listing, keyboard browsing, and playing."""
        folder=self._path('Radio Recordings')
        return sorted((p for p in folder.iterdir() if p.is_file()),key=lambda p:p.name.casefold()) if folder.exists() else []

    def play_download(self, number):
        files=self.downloads()
        i=int(number)-1
        if not 0<=i<len(files): return 'That download number is unavailable.'
        try: self.player.open(files[i].as_uri(),files[i].stem,'podcast')
        except Exception: return 'The downloaded episode could not play.'
        self._remember(files[i].as_uri(), what='download', name=files[i].stem)
        self.current_episode_index=None
        self.chapters=[]
        return 'Playing downloaded episode '+files[i].stem+'.'

    def play_recording(self, number):
        files=self.recordings()
        i=int(number)-1
        if not 0<=i<len(files): return 'That recording number is unavailable.'
        try: self.player.open(files[i].as_uri(),files[i].stem,'radio')
        except Exception: return 'The saved radio recording could not play.'
        self._remember(files[i].as_uri(), what='recording', name=files[i].stem)
        return 'Playing recording '+files[i].stem+'. Say pause audio or stop audio.'

    def play_chosen(self, section):
        """"Play" with nothing loaded yet: play the station, preset or episode the
        lists have chosen, as "that one" does. None when nothing is chosen."""
        if getattr(self.player, 'kind', None): return None
        if section == 'radio':
            if self.preset_browsing and self.presets(): return self.radio_preset_command('play preset')
            if self.station_choices: return self.play_station(self.station_choices[self.station_choice_index]+1)
            if self.stations and self.station_index is not None: return self.play_station(self.station_index+1)
        if section == 'podcast':
            if self.browsing_episodes and self.episodes and self.episode_index is not None:
                return self.play_episode(self.episode_index+1)
            if self.shows and self.show_index is not None:
                return (self.shows[self.show_index]['name']+' is chosen. Say that one to hear its episodes, '
                        'then choose one to play.')
        return None

    def command(self, spoken, section=None):
        text=spoken.strip().rstrip('.!?').strip(); cmd=text.casefold().replace('auto-download','auto download')
        if section in ('radio', 'podcast') and cmd in ('play', 'play it', 'play this'):
            chosen = self.play_chosen(section)
            if chosen is not None: return chosen
        if section == 'radio':
            preset_response = self.radio_preset_command(cmd, text)
            if preset_response is not None: return preset_response
        if section == 'radio':
            cmd=re.sub(r'^(?:please\s+)|(?:\s+please)$','',cmd).strip()
            # Station names keep their capitals in what is said and shown ("WQXR", not "wqxr").
            text=re.sub(r'^(?:please\s+)|(?:\s+please)$','',text,flags=re.I).strip()
            if self.station_choices:
                if cmd in ('next','next station','go forward','forward','previous','previous station','go back','back'):
                    step=-1 if cmd.startswith(('previous','go back','back')) else 1
                    index=self.station_choice_index+step
                    if not 0<=index<len(self.station_choices):
                        return 'That is the '+('first' if index<0 else 'last')+' matching station.'
                    self.station_choice_index=index
                    return (name_first('Choice '+str(index+1)+' of '+str(len(self.station_choices))+': '+
                            self.stations[self.station_choices[index]]['name']+'. Say that one to play it.'))
                if cmd in ('that one','confirm','confirm that','okay','ok'):
                    return self.play_station(self.station_choices[self.station_choice_index]+1)
                choice = re.fullmatch(r'(?:(?:play|open|choose|select)\s+)?(?:(?:the|station|radio station|station number|number|choice)\s+)*(\d+|[a-z]+)(?:\s+(?:one|station))?', cmd)
                number = _radio_number(choice[1]) if choice else None
                if number is not None:
                    if 1 <= number <= len(self.station_choices):
                        return self.play_station(self.station_choices[number-1]+1)
                    return 'There are only '+str(len(self.station_choices))+' matching choices. Say a choice number from one to '+str(len(self.station_choices))+'.'
                if cmd in ('cancel','never mind'):
                    self.station_choices=[]
                    return 'Station choice canceled.'
            if cmd in ('next','next station','go forward','forward','previous','previous station','go back','back') and self.stations:
                step = -1 if cmd in ('previous','previous station','go back','back') else 1
                index = (self.station_index if self.station_index is not None else 0) + step
                if not 0 <= index < len(self.stations):
                    return 'That is the '+('last' if step > 0 else 'first')+' station in these results.'
                self.station_index = index
                return name_first('Station '+str(index+1)+' of '+str(len(self.stations))+': '+self.stations[index]['name']+'. Say that one to play it.')
            if cmd in ('that one','confirm','confirm that','okay','ok','play this one','play that one'):
                return self.play_station(self.station_index+1) if self.stations and self.station_index is not None else 'Search for stations first.'
            match=re.fullmatch(r'(?:play|open)\s+(?:(?:the|station|radio station|station number|number)\s+)*(\d+|[a-z]+)(?:\s+(?:one|station))?',cmd)
            if match:
                number=_radio_number(match[1])
                if number is not None:
                    return self.play_station(number) if self.stations else 'Search for stations first.'
            named=re.fullmatch(r'(?:play|open|listen to|tune to)\s+(?:station\s+)?(.+)',text,re.I)
            if named and cmd not in ('play radio','play audio','play podcast') and not cmd.startswith(('play recording ','play download ','play episode ')):
                wanted = station_request(text) or named[1]
                if not all(word in STATION_FILLER_WORDS for word in re.sub(r'\b(?:radio|station)\b', ' ', wanted.casefold()).split()):
                    return self.play_station_named(wanted)
            if cmd in ('search','search for','search stations','search radio','find stations','find radio'):
                self.awaiting_radio_search=True
                return 'What station would you like to find? Say its name.'
            match=re.fullmatch(r'(?:search radio for|search stations? for|search stations?|search for|search|find station|find) (.+)',text,re.I)
            if match and not match[1].casefold().startswith(('podcast','episode')):
                self.awaiting_radio_search=False
                name=re.sub(r'^(?:station|for)\s+', '', match[1], flags=re.I)
                return self.search_radio('name',name)
            if cmd in ('list subscriptions','subscription list','subscriptions list',
                       'podcast list','podcasts list','list podcast','list podcasts','my podcasts') or 'podcast' in cmd or 'episode' in cmd:
                return 'Say podcasts to switch to podcasts.'
        if section == 'podcast':
            if self.browsing_episodes:
                if cmd in ('next','next episode','go forward','forward','previous','previous episode','go back','back') and not (
                        cmd in ('next episode','previous episode') and self.current_episode_index is not None and getattr(self.player,'kind',None)=='podcast'):
                    # While an episode plays, "Next episode" plays the next one, as its action says.
                    if not self.episodes: return 'This feed has no playable episodes.'
                    step=-1 if cmd.startswith(('previous','go back','back')) else 1
                    index=(self.episode_index if self.episode_index is not None else 0)+step
                    if not 0<=index<len(self.episodes):
                        return 'That is the '+('last' if step>0 else 'first')+' episode in this feed.'
                    self.episode_index=index
                    return (name_first('Episode '+str(index+1)+' of '+str(len(self.episodes))+': '+self.episodes[index]['title']+
                            '. Say that one to play it, or download this episode.'))
                if cmd in ('that one','confirm','confirm that','okay','ok','play that one','play this one'):
                    return self.play_episode(self.episode_index+1) if self.episode_index is not None else 'This feed has no playable episodes.'
                match=re.fullmatch(r'(?:play|stream)\s+(?:(?:episode|number|the)\s+)*(\d+|[a-z]+)(?:\s+one)?',cmd)
                if match:
                    number=_radio_number(match[1])
                    if number is not None: return self.play_episode(number)
                if cmd in ('download this episode','download that one','download selected episode'):
                    return self.download_episode(self.episode_index+1) if self.episode_index is not None else 'This feed has no playable episodes.'
                if cmd in ('list episodes','browse episodes','read episodes'):
                    if self.episode_index is None: return 'This feed has no playable episodes.'
                    return (name_first('Episode '+str(self.episode_index+1)+' of '+str(len(self.episodes))+': '+
                            self.episodes[self.episode_index]['title']+'. Say next or previous, then that one to play.'))
                if cmd in ('back to podcasts','back to feeds','show podcasts'):
                    self.browsing_episodes=False
                    return ('Podcast '+str(self.show_index+1)+': '+self.shows[self.show_index]['name']+
                            '. Say next or previous to browse feeds.') if self.shows and self.show_index is not None else 'Search for podcasts first.'
            if cmd in ('search','search for','search podcast','search podcasts','find podcast','find podcasts'):
                self.awaiting_podcast_search=True
                return 'What podcast would you like to find? Say its name.'
            match=re.fullmatch(r'(?:search podcasts? for|search podcasts?|search for|search|find podcasts? for|find podcasts?|find) (.+)',text,re.I)
            if match:
                self.awaiting_podcast_search=False
                return self.search_podcasts(match[1])
            if self.awaiting_podcast_search:
                self.awaiting_podcast_search=False
                if cmd in ('cancel','never mind','back','go back'):
                    return 'Podcast search canceled.'
                return self.search_podcasts(text)
            if cmd in BROWSE_CATEGORIES: return self.browse_categories()
            if cmd in TOP_PODCAST_PHRASES: return self.browse_category(TOP_PODCASTS)
            if self.browsing_categories:
                names=self.podcast_categories()
                if cmd in ('next','next category','go forward','forward','previous','previous category','go back','back'):
                    step=-1 if cmd.startswith(('previous','go back','back')) else 1
                    index=(self.category_index or 0)+step
                    if not 0<=index<len(names):
                        return 'That is the '+('last' if step>0 else 'first')+' category.'
                    self.category_index=index
                    return self.category_choice()+' Say that one to open it.'
                if cmd in ('that one','confirm','confirm that','okay','ok','open','open that one','open this one','open category'):
                    return self.browse_category(names[max(0,min(self.category_index or 0,len(names)-1))])
                if podcast_category(text): return self.browse_category(text)
            if cmd in ('back to categories','categories','list categories') and self.shows_category:
                return self.browse_categories()
            if cmd in ('next','next podcast','next show','go forward','forward',
                       'previous','previous podcast','previous show','go back','back'):
                if not self.shows: return 'Search for podcasts or list subscriptions first.'
                step=-1 if cmd.startswith(('previous','go back','back')) else 1
                index=(self.show_index if self.show_index is not None else 0)+step
                if not 0<=index<len(self.shows):
                    return 'That is the '+('last' if step>0 else 'first')+' podcast in these results.'
                self.browsing_episodes=False
                self.show_index=index
                return name_first('Podcast '+str(index+1)+' of '+str(len(self.shows))+': '+self.shows[index]['name']+'. Say that one to open episodes, or subscribe.')
            if cmd in ('that one','confirm','confirm that','okay','ok','open','open that one','open this one',
                       'open feed','open selected feed','open this feed','open podcast','browse episodes'):
                return self.open_podcast(self.show_index+1) if self.shows and self.show_index is not None else 'Search for podcasts first.'
            if cmd in ('subscribe','subscribe to that one','subscribe to this one','subscribe to this podcast',
                       'subscribe to selected podcast','subscribe to selected feed','subscribe to this feed'):
                return self.subscribe(self.show_index+1) if self.shows and self.show_index is not None else 'Search for podcasts first.'
            if cmd in ('browse podcast feeds','browse feeds'):
                return (name_first('Podcast '+str((self.show_index or 0)+1)+' of '+str(len(self.shows))+': '+
                        self.shows[self.show_index or 0]['name']+'. Say next or previous to browse.'
                        if self.shows else 'Say browse podcasts to choose a category, or search followed by a podcast name.'))
        if cmd in ('search radio','find radio','find a station','find stations'):
            self.awaiting_radio_search=True
            return 'What station would you like to find? Say its name, or say cancel.'
        if self.awaiting_radio_search:
            self.awaiting_radio_search=False
            if cmd in ('cancel','never mind','go back','back'):
                return 'Radio search canceled.'
            if cmd in ('radio','internet radio'):
                return 'Say a station name, such as WGN, or say browse genre jazz.'
            if cmd.startswith(('browse genre ','browse country ','browse state ','browse city ',
                               'search radio for ','find station ')):
                return self.command(text)
            return self.search_radio('name',text)
        audio=self.audio_command(cmd)
        if audio is not None: return audio
        if cmd in ('which station database','which radio source','station database','list station databases'):
            return ('Station database is '+self.source()+'. Choices: All, Radio Browser, IPRD, '
                    'or an imported RadioSure file. Say choose station database followed by its name.')
        match=re.fullmatch(r'(?:choose|set|use|switch to) (?:station database|radio source|radio database) (.+)',text,re.I)
        if match: return self.choose_source(match[1])
        match=re.fullmatch(r'(?:search |find |browse )(?:radio |stations? )?(?:by )?(station|name|genre|country|state|city|location) (.+)',text,re.I)
        if match: return self.search_radio(match[1].casefold(),match[2])
        match=re.fullmatch(r'(?:search radio for|find radio station|find station|radio station) (.+)',text,re.I)
        if match: return self.search_radio('name',match[1])
        match=re.fullmatch(r'(?:play|open) (?:station|radio station) (?:number )?(\d+|one|two|three|four|five|six|seven|eight|nine|ten|first|second|third)',cmd)
        if match:
            number={'one':1,'first':1,'two':2,'second':2,'three':3,'third':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'ten':10}.get(match[1],match[1])
            return self.play_station(number)
        if cmd in ('list stations','next stations'):
            if not self.stations: return 'Search for radio stations first.'
            if cmd=='next stations': self.station_index=min((self.station_index or 0)+1,len(self.stations)-1)
            i=self.station_index or 0
            return name_first('Station '+str(i+1)+' of '+str(len(self.stations))+': '+self.stations[i]['name']+'. Say that one to play it.')
        if cmd in ('refresh stations','update radio stations','refresh radio directories'):
            try: return self.refresh_iprd(force=True)+' Radio Browser searches always use current directory results.'
            except (OSError,ValueError,TimeoutError): return 'IPRD refresh failed. The last saved catalog remains available; Radio Browser still searches live.'
        match=re.fullmatch(r'(?:record station (\d+)|record radio)(?: for (\d+) minutes?)?',cmd)
        if match:
            station=self.station(match[1]) if match[1] else self.current_station
            if not station: return 'Play or select a station first.'
            return self.record_now(station,int(match[2] or 30))
        if cmd in ('stop recording','stop radio recording'):
            self.record_stop.set(); return 'Stopping the current radio recording.'
        match=re.fullmatch(r'(?:schedule|record) station (\d+) (today|tomorrow) at (\d{1,2})(?::(\d\d))?\s*(am|pm) for (\d+) minutes?( every day)?',cmd)
        if match:
            number,day,hour,minute,period,duration,repeat=match.groups()
            hour=int(hour)
            if not 1<=hour<=12 or int(minute or 0)>59: return 'Say a time such as 8 PM or 8:30 AM.'
            date=self.clock().date()+(timedelta(days=1) if day=='tomorrow' else timedelta())
            when=datetime.combine(date,datetime.min.time()).replace(hour=hour%12+(12 if period=='pm' else 0),minute=int(minute or 0))
            return self.schedule(self.station(number),when,int(duration),bool(repeat))
        if cmd in ('list scheduled recordings','what recordings are scheduled'):
            jobs=self._load('radio-schedule.json',[])
            if not isinstance(jobs,list):
                return 'The radio schedule is damaged. Ask your helper to check it.'
            return 'Scheduled: '+ ' '.join(f'{i}. '+(name+', '+self._scheduled_when(j) if (name:=self._scheduled_name(j)) else 'A damaged schedule entry')+'.'
                                           for i,j in enumerate(jobs,1)) if jobs else 'No recordings are scheduled.'
        match=re.fullmatch(r'cancel scheduled recording (\d+)',cmd)
        if match:
            with self._lock:
                jobs=self._load('radio-schedule.json',[]);i=int(match[1])-1
                if not isinstance(jobs,list) or not 0<=i<len(jobs): return 'That schedule number is unavailable.'
                name=self._scheduled_name(jobs.pop(i));self._save('radio-schedule.json',jobs)
            return 'Canceled scheduled recording of '+name+'.' if name else 'Canceled a damaged schedule entry.'
        if cmd in ('list recordings','list radio recordings'):
            names=[p.name for p in self.recordings()]
            return 'Recordings: '+ '; '.join(f'{i}. {n}' for i,n in enumerate(names[:30],1)) if names else 'No radio recordings yet.'
        match=re.fullmatch(r'play recording (\d+)',cmd)
        if match: return self.play_recording(match[1])
        match=re.fullmatch(r'(?:search|find) podcasts? (?:for|called|about|in category) (.+)',text,re.I)
        if match: return self.search_podcasts(match[1])
        match=re.fullmatch(r'browse podcasts? (?:by |in )?(?:category|categories) (.+)',text,re.I)
        if match: return self.browse_category(match[1])
        if cmd in BROWSE_CATEGORIES: return self.browse_categories()
        if cmd in TOP_PODCAST_PHRASES: return self.browse_category(TOP_PODCASTS)
        match=re.fullmatch(r'open podcast (\d+)',cmd)
        if match: return self.open_podcast(match[1])
        match=re.fullmatch(r'subscribe(?: to)? podcast (\d+)',cmd)
        if match: return self.subscribe(match[1])
        if cmd=='subscribe' and self.shows: return self.subscribe(1)
        if cmd in ('list subscriptions','subscription list','subscriptions list',
                   'podcast list','podcasts list','list podcast','list podcasts','my podcasts'):
            return self.subscriptions()
        if cmd in ('list podcast shows','list shows'):
            if self.shows:
                self.browsing_episodes=False
                i=self.show_index or 0
                return name_first('Podcast '+str(i+1)+' of '+str(len(self.shows))+': '+self.shows[i]['name']+'. Say next or previous, then that one to open episodes.')
            return self.subscriptions()
        if cmd in ('refresh subscriptions','check podcasts','update subscribed podcasts'):
            return ('Checking subscribed feeds in the background.' if self.start_podcast_refresh()
                    else 'Subscribed feeds are already being checked.')
        match=re.match(r'^(?:auto download|automatically download) (.+)$',cmd)
        if match:
            value=match[1]
            target=re.search(r' for (?:podcast|show) (\d+)$',value)
            if target: value=value[:target.start()]
            else: value=re.sub(r' for this (?:podcast|show)$','',value)
            value=re.sub(r'^(?:the )?(?:latest|newest) ','',value)
            value=re.sub(r' episodes?$','',value).strip()
            return self.set_auto_download(value,target[1] if target else None)
        match=re.fullmatch(r'(?:set )?(?:podcast|show) (\d+) (?:auto download|automatic downloads) (?:to )?(all|manual|[\w -]+)',cmd)
        if match: return self.set_auto_download(match[2],match[1])
        match=re.fullmatch(r'(?:manual downloads|turn off auto downloads)(?: for (?:podcast|show) (\d+))?',cmd)
        if match: return self.set_auto_download('manual',match[1])
        match=re.fullmatch(r'(?:play|stream) episode (\d+)',cmd)
        if match: return self.play_episode(match[1])
        match=re.fullmatch(r'download episode (\d+)',cmd)
        if match: return self.download_episode(match[1])
        if cmd in ('list downloads','downloaded podcasts'):
            files=self.downloads()
            return 'Downloads: '+ '; '.join(f'{i}. {p.stem}' for i,p in enumerate(files[:30],1)) if files else 'No downloaded episodes yet.'
        match=re.fullmatch(r'play download (\d+)',cmd)
        if match: return self.play_download(match[1])
        match=re.fullmatch(r'import radiosure (.+\.rsd)',text,re.I)
        if match: return self.import_radiosure(match[1])
        if section == 'radio':
            return 'Say search followed by a station name, browse genre jazz, list stations, or play station followed by its number.'
        if section == 'podcast':
            return 'Say browse podcasts to choose a category, search podcasts for a name, or list subscriptions.'
        return 'Say radio for radio stations, or podcasts for podcast shows.'
