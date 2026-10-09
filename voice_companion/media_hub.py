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
from media_player import MediaPlayer

RADIO_API = 'https://de1.api.radio-browser.info/json/stations/search'
RADIO_ROOTS = ('https://all.api.radio-browser.info','https://de1.api.radio-browser.info',
               'https://nl1.api.radio-browser.info')
IPRD_CATALOG = 'https://iprd-org.github.io/iprd/site_data/metadata/catalog.json'
APPLE_API = 'https://itunes.apple.com/search'
MAX_DOWNLOAD = 500 * 1024 * 1024
PODCAST_REFRESH_SECONDS = 6 * 3600


def _safe(name):
    return re.sub(r'[^\w .-]', '', name).strip(' .')[:90] or 'Untitled'


def _http(url, limit=4_000_000):
    with request.urlopen(request.Request(url, headers={'User-Agent':'VoiceCompanion/0.2'}), timeout=20) as response:
        data = response.read(limit+1)
    if len(data)>limit: raise ValueError('The directory response is too large.')
    return data


def _json(url): return json.loads(_http(url, 30_000_000 if url==IPRD_CATALOG else 4_000_000))


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

    def _path(self, name): return self.folder / name

    def _load(self, name, default):
        try: return json.loads(self._path(name).read_text(encoding='utf-8'))
        except (OSError, ValueError): return default

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

    def radio_preset_command(self, cmd):
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
            name = (save[1] or station['name']).strip()
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
        selected=self._load('radio-preferences.json',{}).get('source','all')
        return selected if selected in self.SOURCES.values() else 'all'

    def choose_source(self, name):
        selected=self.SOURCES.get(name.strip().casefold())
        if selected is None:
            return ('Available station databases are All, Radio Browser, IPRD, and RadioSure file. '
                    'TuneIn and ooTunes are not connected directory sources.')
        if selected=='radiosure' and not self._load('radiosure-stations.json',[]):
            return 'Import a RadioSure station file first. Your station database is still '+self.source()+'.'
        self._save('radio-preferences.json',{'source':selected})
        self.stations=[]
        labels={'all':'All','radio-browser':'Radio Browser','iprd':'IPRD','radiosure':'RadioSure file'}
        return 'Station database set to '+labels[selected]+'.'

    def refresh_iprd(self, force=False):
        stamp=self._load('radio-iprd-status.json',{})
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
        bad=self._load('radio-failures.json',{})
        bad[station['url']] = time.time()
        self._save('radio-failures.json',bad)

    def _usable(self, station):
        if station.get('lastcheckok') in (0,False,'0'): return False
        bad=self._load('radio-failures.json',{})
        return time.time()-bad.get(station.get('url',''),0)>24*3600

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
        options={param:query,'limit':60,'hidebroken':'true','order':'votes','reverse':'true'}
        online_error = ''
        source=self.source()
        online=[]
        if source in ('all','radio-browser'):
            try: online = self._radio_query(options)
            except (OSError, ValueError, TimeoutError):
                online_error = 'The live directory is unavailable. '
        local=[]
        if source in ('all','radiosure'): local += self._load('radiosure-stations.json', [])
        if source in ('all','iprd'): local += self._load('radio-iprd-stations.json', [])
        key = {'name':'name','tag':'tags','countrycode':'country','state':'state'}[param]
        country_aliases = {'US':'united states', 'CA':'canada', 'GB':'united kingdom',
                           'AU':'australia', 'DE':'germany', 'FR':'france'}
        local = [s for s in local if any(candidate in str(s.get(key,'')).casefold()
                 for candidate in ([query.casefold(),country_aliases.get(query,'')]
                                   if param=='countrycode' else [query.casefold()]) if candidate)]
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
        return (online_error + 'Found '+str(len(self.stations))+' stations. Station 1: '+self.stations[0]['name']+'. '
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
        self.current_station = station
        return 'Playing ' + station['name'] + '. Say pause or stop.'

    def play_station_named(self, phrase):
        if not self.stations:
            return 'Search for stations first.'
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
        jobs.append({'station':station,'when':when.isoformat(timespec='seconds'),'minutes':minutes,
                     'repeat':repeat,'id':str(time.time_ns())})
        self._save('radio-schedule.json',jobs)
        self.start_scheduler()
        return ('Scheduled ' + station['name'] + ' for ' + when.strftime('%A %B %d at %I:%M %p') +
                f' for {minutes} minutes. Keep Voice Companion running and the computer awake.')

    def tick(self):
        now=self.clock()
        with self._lock:
            jobs=self._load('radio-schedule.json', [])
            changed=False
            kept=[]
            for job in jobs:
                due=datetime.fromisoformat(job['when'])
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
            while True:
                try: self.tick()
                except Exception: self.notify('The radio schedule could not be checked. Ask your helper to inspect it.')
                time.sleep(10)
        self._scheduler=threading.Thread(target=run,daemon=True)
        self._scheduler.start()

    def search_podcasts(self, term):
        term=term.strip()
        if not term: return 'What podcast would you like to find? Say its name.'
        url=APPLE_API+'?'+parse.urlencode({'media':'podcast','entity':'podcast','limit':20,'term':term})
        results=self.fetch_json(url).get('results',[])
        self.shows=[{'name':r.get('collectionName',''), 'feed':r.get('feedUrl',''),
                     'genre':r.get('primaryGenreName','')} for r in results if _http_url(r.get('feedUrl',''))]
        self.show_index=0 if self.shows else None
        self.browsing_episodes=False
        self.episode_index=None
        self.episodes=[]
        self.current_feed=None
        if not self.shows: return 'No podcast feeds found. Try another name or category.'
        return ('Found '+str(len(self.shows))+' podcasts. Podcast 1: '+self.shows[0]['name']+'. '
                'Say next or previous to browse. Say that one to open episodes, or subscribe to save this feed.')

    def _feed_episodes(self, show):
        if not _http_url(show['feed']): raise ValueError('The podcast feed address is invalid.')
        root=ET.fromstring(_http(show['feed'],5_000_000))
        episodes=[]
        for item in root.findall('.//channel/item'):
            title=item.findtext('title') or 'Untitled episode'
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
            cached=next((s.get('episodes',[]) for s in self._load('podcast-subscriptions.json',[])
                         if s.get('feed')==show['feed']),[])
            if not cached: return 'The podcast feed is unavailable. Try again later.'
            episodes=cached
        self.episodes=episodes
        self.current_feed=show['feed']
        self.current_episode_index=None
        self.browsing_episodes=True
        self.episode_index=0 if episodes else None
        self.chapters=[]
        if not episodes: return 'No playable episodes were found in this podcast feed.'
        return (show['name']+'. Found '+str(len(episodes))+' episodes. Episode 1: '+episodes[0]['title']+
                '. Say next or previous to browse, that one to play, or download this episode.')

    def subscribe(self, number):
        i=int(number)-1
        if not 0<=i<len(self.shows): return 'That podcast number is unavailable.'
        self.show_index=i
        show=self.shows[i]
        self.current_feed=show['feed']
        with self._lock:
            subs=self._load('podcast-subscriptions.json',[])
            if not any(s['feed']==show['feed'] for s in subs):
                subs.append({**show,'download_limit':'manual','episodes':[]})
                self._save('podcast-subscriptions.json',subs)
        self.start_podcast_refresh()
        return ('Subscribed to '+show['name']+'. The feed refreshes automatically while Companion runs. '
                'Downloads are manual until you choose an auto download limit.')

    def subscriptions(self):
        subs=self._load('podcast-subscriptions.json',[])
        self.shows=subs
        self.show_index=0 if subs else None
        self.browsing_episodes=False
        self.episode_index=None
        return ('Found '+str(len(subs))+' subscriptions. Subscription 1: '+subs[0]['name']+
                ', automatic downloads '+str(subs[0].get('download_limit','manual'))+
                '. Say next or previous, that one to open episodes, or subscribe.' if subs else 'No podcast subscriptions yet.')

    def set_auto_download(self, limit, number=None):
        policy=_episode_count(limit)
        if policy is None: return 'Choose manual, all, or a number from 1 to 10,000.'
        with self._lock:
            subs=self._load('podcast-subscriptions.json',[])
            if number is not None:
                i=int(number)-1
                if not 0<=i<len(self.shows): return 'That podcast number is unavailable. Say list subscriptions first.'
                feed=self.shows[i]['feed']
            else: feed=self.current_feed
            show=next((s for s in subs if s.get('feed')==feed),None)
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
        saved=self._load('podcast-subscriptions.json',[])
        failures=0
        pending=[]
        for show in saved:
            try: episodes=self._feed_episodes(show)
            except (OSError,ValueError,ET.ParseError,TimeoutError):
                failures+=1
                continue  # Keep the last known list and retry at the next refresh.
            with self._lock:
                current=self._load('podcast-subscriptions.json',[])
                entry=next((s for s in current if s.get('feed')==show['feed']),None)
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
                selected=next((s.get('download_limit','manual') for s in current
                               if s.get('feed')==feed),'manual')
                if selected!=policy: return
                manifest=self._load('podcast-auto-files.json',{})
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
                    manifest=self._load('podcast-auto-files.json',{})
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
            manifest=self._load('podcast-auto-files.json',{})
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
        action={'play':'play','resume':'play','pause':'pause','stop':'stop',
                'play radio':'play','resume radio':'play','pause radio':'pause','stop radio':'stop',
                'play podcast':'play','resume podcast':'play','pause podcast':'pause','stop podcast':'stop',
                'play audio':'play','resume audio':'play','pause audio':'pause','stop audio':'stop'}.get(cmd)
        if action:
            if not self.player.kind: return 'No audio is playing. Choose a station or episode first.'
            if 'radio' in cmd and self.player.kind!='radio': return 'A podcast is selected. Say '+action+' podcast.'
            if 'podcast' in cmd and self.player.kind!='podcast': return 'A station is selected. Say '+action+' radio.'
            try: self.player.control(action)
            except Exception: return 'The audio player could not respond. Try opening that audio again.'
            return {'play':'Playing audio.','pause':'Audio paused.','stop':'Audio stopped.'}[action]
        match=re.fullmatch(r'(rewind|back|go back|fast forward|forward|skip ahead) (.+)',cmd)
        if match:
            amount=_interval_seconds(match[2])
            if amount is None: return 'Say a time such as 15 seconds, 30 minutes, or one hour.'
            direction=-amount if match[1] in ('rewind','back','go back') else amount
            try: position=self.player.control('seek',seconds=direction)
            except Exception: position=None
            return ('Moved '+('back' if direction<0 else 'forward')+' '+match[2]+'.'
                    if position is not None else 'This audio cannot seek.')
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

    def download_episode(self, number):
        episode=self.episodes[int(number)-1]
        folder=self._path('Podcast Downloads'); folder.mkdir(parents=True,exist_ok=True)
        extension=Path(urlsplit(episode['url']).path).suffix.lower()
        if extension not in ('.mp3','.m4a','.aac','.ogg','.opus'): extension='.mp3'
        target=folder/(_safe(episode['show']+' - '+episode['title'])+extension)
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
        files=sorted((p for p in folder.iterdir() if p.is_file()),key=lambda p:p.name.casefold()) if folder.exists() else []
        return files

    def play_download(self, number):
        files=self.downloads()
        i=int(number)-1
        if not 0<=i<len(files): return 'That download number is unavailable.'
        try: self.player.open(files[i].as_uri(),files[i].stem,'podcast')
        except Exception: return 'The downloaded episode could not play.'
        self.current_episode_index=None
        self.chapters=[]
        return 'Playing downloaded episode '+files[i].stem+'.'

    def play_recording(self, number):
        folder=self._path('Radio Recordings')
        files=sorted((p for p in folder.iterdir() if p.is_file()),key=lambda p:p.name.casefold()) if folder.exists() else []
        i=int(number)-1
        if not 0<=i<len(files): return 'That recording number is unavailable.'
        try: self.player.open(files[i].as_uri(),files[i].stem,'radio')
        except Exception: return 'The saved radio recording could not play.'
        return 'Playing recording '+files[i].stem+'. Say pause audio or stop audio.'

    def command(self, spoken, section=None):
        text=spoken.strip().rstrip('.!?').strip(); cmd=text.casefold().replace('auto-download','auto download')
        if section == 'radio':
            preset_response = self.radio_preset_command(cmd)
            if preset_response is not None: return preset_response
        if section == 'radio':
            cmd=re.sub(r'^(?:please\s+)|(?:\s+please)$','',cmd).strip()
            text=cmd
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
                return self.play_station_named(named[1])
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
                if cmd in ('next','next episode','go forward','forward','previous','previous episode','go back','back'):
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
            if cmd in ('browse podcasts','browse podcast feeds','browse feeds'):
                return (name_first('Podcast '+str((self.show_index or 0)+1)+' of '+str(len(self.shows))+': '+
                        self.shows[self.show_index or 0]['name']+'. Say next or previous to browse.'
                        if self.shows else 'Say browse podcasts category science, or search followed by a podcast name.'))
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
            return 'Scheduled: '+ '; '.join(f'{i}. {j["station"]["name"]} at {j["when"]}' for i,j in enumerate(jobs,1)) if jobs else 'No recordings are scheduled.'
        match=re.fullmatch(r'cancel scheduled recording (\d+)',cmd)
        if match:
            jobs=self._load('radio-schedule.json',[]);i=int(match[1])-1
            if not 0<=i<len(jobs): return 'That schedule number is unavailable.'
            name=jobs.pop(i)['station']['name'];self._save('radio-schedule.json',jobs)
            return 'Canceled scheduled recording of '+name+'.'
        if cmd in ('list recordings','list radio recordings'):
            folder=self._path('Radio Recordings')
            names=sorted(p.name for p in folder.iterdir() if p.is_file()) if folder.exists() else []
            return 'Recordings: '+ '; '.join(f'{i}. {n}' for i,n in enumerate(names[:30],1)) if names else 'No radio recordings yet.'
        match=re.fullmatch(r'play recording (\d+)',cmd)
        if match: return self.play_recording(match[1])
        match=re.fullmatch(r'(?:search|find) podcasts? (?:for|called|about|in category) (.+)',text,re.I)
        if match: return self.search_podcasts(match[1])
        match=re.fullmatch(r'browse podcasts? (?:by )?category (.+)',text,re.I)
        if match: return self.search_podcasts(match[1])
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
            return 'Say search podcasts for a name, list podcasts, or list subscriptions.'
        return 'Say radio for radio stations, or podcasts for podcast shows.'
