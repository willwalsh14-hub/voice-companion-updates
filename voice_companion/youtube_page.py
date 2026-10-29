"""Reading and driving the real youtube.com page, in one place.

The YouTube screen (youtube_screen.py) never touches the page itself. It asks
this module for lists, the player's state and account actions. Everything here
works on a Playwright page, so it is the same in embedded Edge (WebView2),
guided Chrome and the user's own browser linked by the extension.

YouTube changes its page often. Each reading below tries several known shapes
of the page, newest first, and then a plain fallback (any link to a video,
read from its accessible name). When nothing can be read, a YouTubeError with
CHANGED is raised; the screen says it and offers the page itself.

The player is driven through the page's own player object (#movie_player,
the same calls YouTube's embedded player API documents), then the <video>
element, then the player's buttons. A button is pressed with a real click,
because a browser starts sound only after a real key press or click.

Nothing here hides, blocks or skips an ad by itself. Skip is pressed only when
the user asks for it.
"""
import base64
import re
from urllib.parse import parse_qs, quote_plus, urlsplit

BASE = 'https://www.youtube.com'
HOSTS = ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com')
CHANGED = ('YouTube changed its page, and this list could not be read. '
           'Say open in browser to use the page itself.')
PLAYER_CHANGED = ('YouTube changed its player, so that could not be done. '
                  'Say open in browser to use the page itself.')
NOT_LOADED = 'YouTube did not finish loading. Check your internet connection, then press F5 to try again.'
SIGN_IN_URL = 'https://accounts.google.com/ServiceLogin?service=youtube&continue=' + quote_plus(BASE + '/')

# The lists on the screen, and where they are on the site.
FEEDS = {
    'home': ('Home', '/'),
    'subscriptions': ('New from your subscriptions', '/feed/subscriptions'),
    'channels': ('Your subscriptions', '/feed/channels'),
    'history': ('History', '/feed/history'),
    'watch_later': ('Watch later', '/playlist?list=WL'),
    'liked': ('Liked videos', '/playlist?list=LL'),
    'playlists': ('Your playlists', '/feed/playlists'),
}
ACCOUNT_FEEDS = ('subscriptions', 'channels', 'history', 'watch_later', 'liked', 'playlists')


class YouTubeError(Exception):
    """A plain message for the user. Never a crash."""


# ---------------------------------------------------------------- search filters
# YouTube keeps search filters in the "sp" part of the address. It is a small
# protocol-buffer message: field 1 is the sort order; field 2 holds the filters.
# Building it here lets filters combine, and needs no clicks in the filter panel.
FILTERS = {
    'upload date': ('Upload date', (('any', 'Any time', None), ('hour', 'Last hour', 1), ('today', 'Today', 2),
                                    ('week', 'This week', 3), ('month', 'This month', 4), ('year', 'This year', 5))),
    'type': ('Type', (('any', 'Any type', None), ('video', 'Videos', 1), ('channel', 'Channels', 2),
                      ('playlist', 'Playlists', 3), ('movie', 'Movies', 4))),
    'length': ('Length', (('any', 'Any length', None), ('short', 'Under 4 minutes', 1),
                          ('medium', '4 to 20 minutes', 3), ('long', 'Over 20 minutes', 2))),
    'features': ('Features', (('any', 'No feature', None), ('subtitles', 'Subtitles', 5), ('live', 'Live', 8),
                              ('4k', '4K', 14), ('hd', 'HD', 4), ('creative commons', 'Creative Commons', 6),
                              ('360', '360 degrees', 15), ('hdr', 'HDR', 25))),
    'sort': ('Sort by', (('relevance', 'Relevance', 0), ('upload date', 'Upload date', 2),
                         ('view count', 'View count', 3), ('rating', 'Rating', 1))),
}
FILTER_ORDER = ('upload date', 'type', 'length', 'features', 'sort')
NO_FILTERS = {'upload date': 'any', 'type': 'any', 'length': 'any', 'features': 'any', 'sort': 'relevance'}


def _varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7f
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _field(number, value):
    return _varint(number << 3) + _varint(value)


def filter_code(filters):
    """The "sp" value for a set of filters, or '' when none is chosen."""
    filters = dict(NO_FILTERS, **(filters or {}))
    values = {name: dict((key, number) for key, _, number in FILTERS[name][1]) for name in FILTERS}
    inner = b''
    for name, number in (('upload date', 1), ('type', 2), ('length', 3)):
        chosen = values[name].get(filters[name])
        if chosen:
            inner += _field(number, chosen)
    feature = values['features'].get(filters['features'])
    if feature:
        inner += _field(feature, 1)
    data = b''
    sort = values['sort'].get(filters['sort']) or 0
    if sort:
        data += _field(1, sort)
    if inner:
        data += _varint((2 << 3) | 2) + _varint(len(inner)) + inner
    return base64.b64encode(data).decode('ascii') if data else ''


def search_url(query, filters=None):
    url = BASE + '/results?search_query=' + quote_plus(' '.join(str(query).split()))
    code = filter_code(filters)
    return url + ('&sp=' + quote_plus(code) if code else '')


def filter_label(name, key):
    return next(label for value, label, _ in FILTERS[name][1] if value == key)


def filter_summary(filters):
    """'This week, Videos, Sorted by upload date', or '' with no filters."""
    filters = dict(NO_FILTERS, **(filters or {}))
    parts = [filter_label(name, filters[name]) for name in ('upload date', 'type', 'length', 'features')
             if filters[name] != 'any']
    if filters['sort'] != 'relevance':
        parts.append('sorted by ' + filter_label('sort', filters['sort']).lower())
    text = ', '.join(parts)
    return text[:1].upper() + text[1:]


def filter_request(spoken):
    """(filter name, value) for words such as "this week", "only channels", "sort by view count"."""
    words = ' '.join(re.sub(r'[^\w\s]', ' ', str(spoken).casefold()).split())
    words = re.sub(r'^(?:filter (?:by |to )?|only show |show only |only |set |choose |use )', '', words)
    words = re.sub(r'^(?:upload date |uploaded |date |type |length |duration |feature |features )', '', words)
    if re.fullmatch(r'(?:sort(?:ed)? by |order by )?(?:relevance|most relevant)', words):
        return 'sort', 'relevance'
    match = re.fullmatch(r'(?:sort(?:ed)? by |order by )(upload date|date|newest|view count|views|most viewed|rating|top rated)', words)
    if match:
        return 'sort', {'date': 'upload date', 'newest': 'upload date', 'views': 'view count',
                        'most viewed': 'view count', 'top rated': 'rating'}.get(match[1], match[1])
    table = {'last hour': ('upload date', 'hour'), 'this hour': ('upload date', 'hour'), 'today': ('upload date', 'today'),
             'this week': ('upload date', 'week'), 'this month': ('upload date', 'month'), 'this year': ('upload date', 'year'),
             'any time': ('upload date', 'any'), 'any date': ('upload date', 'any'),
             'videos': ('type', 'video'), 'video': ('type', 'video'), 'channels': ('type', 'channel'), 'channel': ('type', 'channel'),
             'playlists': ('type', 'playlist'), 'playlist': ('type', 'playlist'), 'movies': ('type', 'movie'),
             'any type': ('type', 'any'),
             'short videos': ('length', 'short'), 'short': ('length', 'short'), 'under 4 minutes': ('length', 'short'),
             'under four minutes': ('length', 'short'), 'medium length': ('length', 'medium'), 'medium': ('length', 'medium'),
             '4 to 20 minutes': ('length', 'medium'), 'four to twenty minutes': ('length', 'medium'),
             'long videos': ('length', 'long'), 'long': ('length', 'long'), 'over 20 minutes': ('length', 'long'),
             'over twenty minutes': ('length', 'long'), 'any length': ('length', 'any'),
             'subtitles': ('features', 'subtitles'), 'with subtitles': ('features', 'subtitles'), 'captions': ('features', 'subtitles'),
             'with captions': ('features', 'subtitles'), 'closed captions': ('features', 'subtitles'),
             'live': ('features', 'live'), 'live videos': ('features', 'live'), '4k': ('features', '4k'), 'four k': ('features', '4k'),
             'hd': ('features', 'hd'), 'high definition': ('features', 'hd'), 'creative commons': ('features', 'creative commons'),
             '360': ('features', '360'), 'hdr': ('features', 'hdr'), 'no feature': ('features', 'any')}
    return table.get(words)


# ---------------------------------------------------------------- addresses
def is_youtube(url):
    host = (urlsplit(str(url or '')).hostname or '').lower()
    return host in HOSTS or host.endswith('.youtube.com')


def video_id(url):
    """The video id in a watch, shorts or youtu.be address, else ''."""
    parts = urlsplit(str(url or ''))
    host = (parts.hostname or '').lower()
    if host == 'youtu.be':
        return parts.path.strip('/').split('/')[0]
    if not is_youtube(url):
        return ''
    if parts.path.startswith('/shorts/'):
        return parts.path.split('/')[2] if len(parts.path.split('/')) > 2 else ''
    return (parse_qs(parts.query).get('v') or [''])[0]


def same_address(first, second):
    """True when two YouTube addresses show the same list (spelling of the search words aside)."""
    a, b = urlsplit(str(first or '')), urlsplit(str(second or ''))
    return (a.path.rstrip('/') == b.path.rstrip('/') and
            {k: sorted(v) for k, v in parse_qs(a.query).items()} == {k: sorted(v) for k, v in parse_qs(b.query).items()})


def watch_url(ident):
    return BASE + '/watch?v=' + quote_plus(ident)


def feed_url(name):
    return BASE + FEEDS[name][1]


def absolute(href):
    href = str(href or '').strip()
    if href.startswith('/'):
        return BASE + href
    return href


# ---------------------------------------------------------------- spoken words
NUMBER_WORDS = {'K': 'thousand', 'M': 'million', 'B': 'billion'}


def spoken_count(text):
    """'1.2M views' is '1.2 million views'; '15K watching' is '15 thousand watching'."""
    return re.sub(r'(\d+(?:\.\d+)?)\s?([KMB])\b', lambda m: m[1] + ' ' + NUMBER_WORDS[m[2]], ' '.join(str(text or '').split()))


def clock_seconds(text):
    """Seconds in a clock time such as '10:05' or '1:02:03', else None."""
    match = re.fullmatch(r'\s*(?:(\d+):)?(\d{1,2}):(\d{2})\s*', str(text or ''))
    if not match:
        return None
    return int(match[1] or 0) * 3600 + int(match[2]) * 60 + int(match[3])


def worded_seconds(text):
    """Seconds in words such as '10 minutes, 5 seconds', else None."""
    total, found = 0, False
    for amount, unit in re.findall(r'(\d+)\s*(hour|minute|second)s?', str(text or ''), re.I):
        total += int(amount) * {'hour': 3600, 'minute': 60, 'second': 1}[unit.lower()]
        found = True
    return total if found else None


def spoken_length(seconds):
    from media_player import spoken_time
    return spoken_time(seconds)


VIEWS = re.compile(r'^(?:[\d,.]+\s?[KMB]?|No)\s+views?$', re.I)
WATCHING = re.compile(r'^[\d,.]+\s?[KMB]?\s+(?:watching|waiting)(?: now)?$', re.I)
AGE = re.compile(r'(?:\bago$|^Streamed |^Premiere|^Scheduled|^Premieres|^Updated |^Last updated)', re.I)
ARIA = re.compile(r'^(?P<title>.+?) by (?P<channel>.+?) (?P<views>(?:[\d,.]+\s?[KMB]?|No) views?) '
                  r'(?P<age>(?:Streamed )?\d+ \w+ ago)(?: (?P<duration>.+?))?$')


def _clean(text):
    text = ' '.join(str(text or '').replace('​', ' ').split())
    return text.strip(' •·|')


def _parts(raw):
    """Every metadata text of an item, one part each, in page order."""
    parts = []
    for value in list(raw.get('meta') or []) + [part for row in (raw.get('rows') or []) for part in row]:
        for piece in re.split(r'\s+[•·]\s+', str(value or '')):
            piece = _clean(piece)
            if piece and piece not in parts:
                parts.append(piece)
    return parts


def item_from_raw(raw):
    """One list item in plain fields, from what the page reader found. None when it has no title."""
    title = _clean(raw.get('title'))
    aria = _clean(raw.get('aria'))
    href = absolute(raw.get('href'))
    parsed = ARIA.match(aria) if aria else None
    if not title and parsed:
        title = parsed['title']
    if not title:
        return None
    parts = _parts(raw)
    channel = _clean(raw.get('channel'))
    views = next((p for p in parts if VIEWS.match(p)), '')
    watching = next((p for p in parts if WATCHING.match(p)), '')
    age = next((p for p in parts if AGE.search(p)), '')
    if not channel:
        # The first part that is not views, age or a count is the channel in the newer layout.
        channel = next((p for p in parts if p not in (views, watching, age) and not re.search(r'\d+\s+(?:videos?|subscribers?)$', p, re.I)
                        and p.casefold() not in ('live', 'new', 'cc', '4k', 'hd', 'premiere', 'sponsored', 'verified')), '')
    if parsed:
        channel = channel or parsed['channel']
        views = views or parsed['views']
        age = age or parsed['age']
    badge = _clean(raw.get('badge')).upper()
    overlay = _clean(raw.get('overlay')).upper()
    duration = clock_seconds(raw.get('badge'))
    if duration is None and parsed and parsed['duration']:
        duration = worded_seconds(parsed['duration'])
    path = urlsplit(href).path
    query = parse_qs(urlsplit(href).query)
    container = str(raw.get('container') or '').lower()
    kind = 'video'
    if raw.get('ad'):
        kind = 'ad'
    elif 'channel' in container or path.startswith(('/@', '/channel/', '/c/', '/user/')) and not query.get('v'):
        kind = 'channel'
    elif path.startswith('/shorts/') or 'shorts' in container or overlay == 'SHORTS' or badge == 'SHORTS':
        kind = 'short'
    elif 'playlist' in container and 'playlist-video' not in container or path == '/playlist' or raw.get('collection'):
        kind = 'playlist'
    live = kind == 'video' and (bool(raw.get('live')) or overlay == 'LIVE' or badge in ('LIVE', 'LIVE NOW') or bool(watching))
    upcoming = kind == 'video' and (overlay == 'UPCOMING' or badge == 'UPCOMING' or bool(re.match(r'(?:Premieres|Scheduled)', age)))
    item = {'kind': kind, 'title': title, 'channel': channel, 'url': href, 'id': video_id(href),
            'duration': None if live or upcoming else duration, 'views': views, 'watching': watching, 'age': age,
            'live': live, 'upcoming': upcoming}
    if kind == 'ad':
        item['channel'] = _clean(re.sub(r'^(?:Sponsored|Ad)\s*[·•:]?\s*', '', channel, flags=re.I))
    if kind == 'channel':
        item['subscribers'] = _clean(raw.get('subscribers')) or next((p for p in parts if re.search(r'subscribers?$', p, re.I)), '')
        item['videos'] = _clean(raw.get('videos')) or next((p for p in parts if re.search(r'\d\s+videos?$', p, re.I)), '')
        item['channel'] = ''
    if kind == 'playlist':
        item['count'] = _clean(raw.get('count')) or next((p for p in parts if re.search(r'\d\s+(?:videos?|lessons?|episodes?)$', p, re.I)), '')
        item['list'] = (query.get('list') or [''])[0]
    return item


def spoken_item(item):
    """What a list says for one item: title, channel, length, views and age, in that order.
    Live videos, Shorts, channels, playlists and ads say what they are first."""
    def say(*parts):
        return '. '.join(p.rstrip('.') for p in parts if p)
    kind = item.get('kind')
    if kind == 'channel':
        return say('Channel: ' + item['title'], spoken_count(item.get('subscribers')), spoken_count(item.get('videos')))
    if kind == 'playlist':
        return say('Playlist: ' + item['title'], item.get('channel'), item.get('count'))
    if kind == 'ad':
        return say('Ad: ' + item['title'], item.get('channel'))
    lead = 'Live now. ' if item.get('live') else 'Upcoming. ' if item.get('upcoming') else 'Short. ' if kind == 'short' else ''
    length = spoken_length(item['duration']) if item.get('duration') else ''
    return lead + say(item['title'], item.get('channel'), length,
                      spoken_count(item.get('watching') or item.get('views')), item.get('age'))


# ---------------------------------------------------------------- page reading
# Shared helpers for the scripts below.
HELPERS = r'''
const txt=e=>e?String(e.textContent||'').replace(/\s+/g,' ').trim():'';
const shown=e=>!!e&&e.getClientRects().length>0&&getComputedStyle(e).visibility!=='hidden';
const first=(root,sels)=>{for(const s of sels){for(const e of root.querySelectorAll(s)){if(txt(e)||e.getAttribute('title')||e.getAttribute('aria-label'))return e;}}return null;};
const textOf=(root,sels)=>{const e=first(root,sels);return e?(e.getAttribute('title')||txt(e)||e.getAttribute('aria-label')||'').trim():'';};
const call=(p,name,...args)=>{try{return p&&typeof p[name]==='function'?p[name](...args):undefined;}catch(e){return undefined;}};
'''

# The parts of an item in each known layout, newest layout first.
LIST_JS = HELPERS + r'''
(which)=>{
  const host=location.hostname;
  const app=!!document.querySelector('ytd-app');
  const signedIn=document.querySelector('#avatar-btn,button#avatar-btn,ytd-topbar-menu-button-renderer #avatar-btn')?true:
    (document.querySelector('ytd-masthead a[href*="ServiceLogin"],#masthead a[href*="ServiceLogin"],ytd-masthead a[aria-label="Sign in"],#masthead a[aria-label="Sign in"],ytd-masthead [aria-label="Sign in"]')?false:null);
  let scope;
  if(which==='related'){scope=document.querySelector('#secondary #related,#secondary,#related,ytd-watch-next-secondary-results-renderer');}
  else{scope=[...document.querySelectorAll('ytd-page-manager > *')].find(e=>!e.hasAttribute('hidden')&&shown(e))||document.querySelector('ytd-page-manager')||document.querySelector('#content,#contents,main')||document.body;}
  const result={host,url:location.href,app,signedIn,title:document.title,items:[],promo:'',empty:'',consent:false};
  if(/^consent\./.test(host)||/consent/.test(location.pathname)){result.consent=true;return result;}
  if(!scope)return result;
  // A page that asks you to sign in instead of showing your list.
  const promo=[...scope.querySelectorAll('ytd-background-promo-renderer,ytd-message-renderer,yt-empty-state-view-model,ytd-feed-nudge-renderer')].find(shown);
  if(promo){result.promo=txt(promo);result.promoSignIn=!!promo.querySelector('a[href*="ServiceLogin"],a[href*="accounts.google.com"],[aria-label="Sign in"]')||/sign in/i.test(txt(promo));}
  const CONTAINERS='ytd-rich-item-renderer,ytd-video-renderer,ytd-grid-video-renderer,ytd-compact-video-renderer,ytd-playlist-video-renderer,'+
    'ytd-playlist-panel-video-renderer,ytd-channel-renderer,ytd-grid-channel-renderer,ytd-playlist-renderer,ytd-grid-playlist-renderer,'+
    'ytd-radio-renderer,ytd-compact-radio-renderer,ytd-compact-playlist-renderer,ytd-movie-renderer,yt-lockup-view-model,'+
    'ytm-shorts-lockup-view-model,ytm-shorts-lockup-view-model-v2,ytd-reel-item-renderer,ytd-ad-slot-renderer,ytd-in-feed-ad-layout-renderer,'+
    'ytd-promoted-video-renderer,ytd-search-pyv-renderer';
  const all=[...scope.querySelectorAll(CONTAINERS)];
  const top=all.filter(e=>{const up=e.parentElement&&e.parentElement.closest(CONTAINERS);return !up||!scope.contains(up);});
  for(const el of top){
    if(!shown(el))continue;
    const tag=el.tagName.toLowerCase();
    const inner=el.querySelector('yt-lockup-view-model,ytm-shorts-lockup-view-model,ytm-shorts-lockup-view-model-v2,ytd-rich-grid-media,ytd-ad-slot-renderer,ytd-in-feed-ad-layout-renderer')||el;
    const container=(tag+' '+inner.tagName.toLowerCase());
    const ad=/ad-slot|in-feed-ad|promoted|pyv/.test(container)||!!el.querySelector('ytd-ad-slot-renderer,ytd-in-feed-ad-layout-renderer,ad-badge-view-model,.ytd-ad-slot-renderer');
    const titleEl=first(el,['#video-title','a#video-title-link','.yt-lockup-metadata-view-model__title','.yt-lockup-metadata-view-model-wiz__title',
      '.shortsLockupViewModelHostOutsideMetadataTitle','.shortsLockupViewModelHostMetadataTitle','#channel-title #text','#channel-title',
      'ytd-channel-name #text','#title','h3 a','h3','#headline']);
    let title=titleEl?(titleEl.getAttribute('title')||txt(titleEl)):'';
    let aria=titleEl?(titleEl.getAttribute('aria-label')||''):'';
    const link=(titleEl&&titleEl.closest('a[href]'))||el.querySelector('a#video-title-link,a#video-title,a#thumbnail[href],a.yt-lockup-view-model__content-image,a.yt-lockup-view-model-wiz__content-image,a[href*="/watch"],a[href*="/shorts/"],a[href*="/playlist"],a#main-link,a[href^="/@"],a[href*="/channel/"],a[href]');
    if(!aria&&link)aria=link.getAttribute('aria-label')||'';
    const href=link?link.getAttribute('href')||'':'';
    const meta=[...el.querySelectorAll('#metadata-line span.inline-metadata-item,#metadata-line > span,#video-info > span,#byline-container #video-info span,.shortsLockupViewModelHostOutsideMetadataSubhead,.shortsLockupViewModelHostMetadataSubhead')].map(txt).filter(Boolean);
    const rows=[...el.querySelectorAll('.yt-content-metadata-view-model__metadata-row,.yt-content-metadata-view-model-wiz__metadata-row')]
      .map(r=>{const spans=[...r.querySelectorAll('span.yt-core-attributed-string,.yt-content-metadata-view-model__metadata-text,.yt-content-metadata-view-model-wiz__metadata-text')].map(txt).filter(Boolean);return spans.length?spans:[txt(r)];});
    const channel=textOf(el,['ytd-channel-name #text','#channel-name #text','#channel-name a','#byline a','#byline']);
    const overlayEl=el.querySelector('ytd-thumbnail-overlay-time-status-renderer[overlay-style]');
    const badge=textOf(el,['ytd-thumbnail-overlay-time-status-renderer #text','ytd-thumbnail-overlay-time-status-renderer .badge-shape-wiz__text',
      'yt-thumbnail-overlay-badge-view-model .yt-badge-shape__text','yt-thumbnail-badge-view-model .yt-badge-shape__text','.yt-badge-shape__text',
      '.badge-shape-wiz__text','ytd-thumbnail-overlay-time-status-renderer','#time-status #text']);
    const live=!!el.querySelector('.badge-style-type-live-now-alternate,.badge-style-type-live-now,[aria-label="LIVE"],.yt-badge-shape--thumbnail-live,.badge-shape-wiz--thumbnail-live');
    const subscribers=textOf(el,['#subscribers','#subscriber-count']);
    const videos=textOf(el,['#video-count']);
    const count=textOf(el,['ytd-thumbnail-overlay-side-panel-renderer','#video-count-text','yt-collection-thumbnail-view-model .yt-badge-shape__text','yt-collection-thumbnail-view-model .badge-shape-wiz__text']);
    const collection=!!el.querySelector('yt-collection-thumbnail-view-model,yt-collections-stack');
    result.items.push({container,title,aria,href,meta,rows,channel,badge,overlay:overlayEl?overlayEl.getAttribute('overlay-style'):'',live,subscribers,videos,count,collection,ad});
  }
  if(!result.items.length){
    // The plain fallback: any link to a video, named by its accessible name.
    const seen=new Set();
    for(const a of scope.querySelectorAll('a[href*="/watch?v="],a[href*="/shorts/"]')){
      if(!shown(a))continue;
      const label=a.getAttribute('aria-label')||a.getAttribute('title')||txt(a);
      const key=(a.getAttribute('href')||'').split('&')[0];
      if(!label||seen.has(key))continue;seen.add(key);
      result.items.push({container:'link',title:a.getAttribute('title')||'',aria:a.getAttribute('aria-label')||'',href:a.getAttribute('href'),meta:[],rows:[],
        channel:'',badge:'',overlay:'',live:false,subscribers:'',videos:'',count:'',collection:false,ad:false,fallback:label});
    }
  }
  const empty=[...scope.querySelectorAll('ytd-background-promo-renderer,ytd-message-renderer,yt-empty-state-view-model')].find(shown);
  if(empty)result.empty=txt(empty);
  return result;
}
'''

# True once a list, a message or the player is on the page, or it is not YouTube.
READY_JS = r'''
()=>{
  if(!/youtube\.com$/.test(location.hostname))return true;
  const page=[...document.querySelectorAll('ytd-page-manager > *')].find(e=>!e.hasAttribute('hidden'))||document;
  if(page.querySelector('ytd-rich-item-renderer,ytd-video-renderer,ytd-playlist-video-renderer,ytd-channel-renderer,ytd-grid-video-renderer,yt-lockup-view-model,ytd-reel-item-renderer,ytm-shorts-lockup-view-model,ytm-shorts-lockup-view-model-v2'))return true;
  if(page.querySelector('ytd-background-promo-renderer,ytd-message-renderer,yt-empty-state-view-model,ytd-feed-nudge-renderer'))return true;
  if(location.pathname==='/watch'&&document.querySelector('#movie_player'))return true;
  return false;
}
'''

PLAYER_JS = HELPERS + r'''
()=>{
  const p=document.getElementById('movie_player');
  const v=document.querySelector('#movie_player video,video.html5-main-video,video');
  const data=call(p,'getVideoData')||{};
  const num=x=>typeof x==='number'&&isFinite(x)?x:null;
  const skip=[...document.querySelectorAll('.ytp-skip-ad-button,.ytp-ad-skip-button,.ytp-ad-skip-button-modern,.ytp-ad-skip-button-slot button,button[id^="skip-button"]')].find(b=>shown(b)&&!b.disabled);
  const adShowing=!!p&&(p.classList.contains('ad-showing')||p.classList.contains('ad-interrupting'));
  const adText=[...document.querySelectorAll('.ytp-ad-badge__text,.ytp-ad-simple-ad-badge,.ytp-ad-text.ytp-ad-preview-text,.ytp-preview-ad__text,.ytp-ad-preview-text-modern,.ytp-ad-duration-remaining')].filter(shown).map(txt).filter(Boolean);
  const ccButton=document.querySelector('.ytp-subtitles-button');
  const autoplay=document.querySelector('.ytp-autonav-toggle-button');
  const error=[...document.querySelectorAll('.ytp-error .ytp-error-content-wrap-reason,.ytp-error-content-wrap-reason,yt-playability-error-supported-renderers #reason,#error-screen #reason')].find(e=>shown(e)&&txt(e));
  const title=data.title||textOf(document,['ytd-watch-metadata h1 yt-formatted-string','ytd-watch-metadata h1','#title h1','h1.title'])||document.title.replace(/ - YouTube$/,'');
  const state=call(p,'getPlayerState');
  return {
    url:location.href, watch:location.pathname==='/watch'||location.pathname.startsWith('/shorts/'), player:!!p, video:!!v,
    id:data.video_id||new URLSearchParams(location.search).get('v')||'', title,
    channel:data.author||textOf(document,['ytd-watch-metadata ytd-channel-name #text','#owner ytd-channel-name a','#upload-info #channel-name a']),
    state:typeof state==='number'?state:null,
    time:num(call(p,'getCurrentTime'))??(v?num(v.currentTime):null),
    duration:num(call(p,'getDuration'))??(v?num(v.duration):null),
    paused:typeof state==='number'?!(state===1||state===3):(v?v.paused:null),
    ended:state===0||(v?v.ended:false),
    rate:num(call(p,'getPlaybackRate'))??(v?v.playbackRate:null),
    volume:num(call(p,'getVolume'))??(v?Math.round(v.volume*100):null),
    muted:typeof call(p,'isMuted')==='boolean'?call(p,'isMuted'):(v?v.muted:null),
    live:!!data.isLive||!!(p&&p.classList.contains('ytp-live'))||shown(document.querySelector('.ytp-live-badge[disabled],.ytp-live')),
    quality:call(p,'getPlaybackQuality')||'',
    ad:{showing:adShowing,skippable:!!skip,text:[...new Set(adText)].join('. ')},
    captions:{available:shown(ccButton)&&ccButton.getAttribute('aria-disabled')!=='true',on:ccButton?ccButton.getAttribute('aria-pressed')==='true':false,
      text:[...document.querySelectorAll('.ytp-caption-segment')].map(txt).filter(Boolean).join(' ')},
    autoplay:autoplay&&shown(autoplay)?autoplay.getAttribute('aria-checked')==='true':null,
    fullscreen:!!document.fullscreenElement||!!(p&&p.classList.contains('ytp-fullscreen')),
    chapter:textOf(document,['.ytp-chapter-title-content']),
    error:error?txt(error):''
  };
}
'''

DETAILS_JS = HELPERS + r'''
()=>{
  const more=document.querySelector('#description-inline-expander #expand,ytd-text-inline-expander #expand,#description #expand,tp-yt-paper-button#expand');
  if(more&&shown(more)){try{more.click();}catch(e){}}
  // Most exact place first: the full text, then the short one, then older layouts. Line breaks are kept.
  const lines=e=>String(e.innerText||e.textContent||'').split('\n').map(l=>l.replace(/\s+/g,' ').trim()).filter(Boolean).join('\n');
  let description='';
  for(const sel of ['#description-inline-expander #expanded yt-attributed-string,ytd-text-inline-expander #expanded yt-attributed-string',
                    '#description-inline-expander yt-attributed-string,ytd-text-inline-expander yt-attributed-string',
                    '#description .content,#description yt-formatted-string#description-text,#description yt-formatted-string']){
    const found=[...document.querySelectorAll(sel)].map(lines).filter(Boolean).sort((a,b)=>b.length-a.length);
    if(found.length){description=found[0];break;}
  }
  description=description.replace(/\s*(?:…|\.\.\.)\s*more\s*$/i,'').replace(/\s*Show less\s*$/i,'');
  if(!description){const meta=document.querySelector('meta[name="description"]');description=meta?meta.content:'';}
  const info=[...document.querySelectorAll('ytd-watch-info-text #info span,#info-container #info span,#info-strings yt-formatted-string,#count .view-count,ytd-video-view-count-renderer span')].map(txt).filter(Boolean);
  const like=document.querySelector('like-button-view-model button,#segmented-like-button button,ytd-toggle-button-renderer#like-button button,button[aria-label^="like this video"],button[aria-label^="Like this video"]');
  const sub=document.querySelector('ytd-watch-metadata #subscribe-button button,#owner #subscribe-button button,ytd-subscribe-button-renderer button,yt-subscribe-button-view-model button');
  const subRenderer=document.querySelector('ytd-watch-metadata ytd-subscribe-button-renderer,#owner ytd-subscribe-button-renderer');
  const chapterItems=[...document.querySelectorAll('ytd-macro-markers-list-item-renderer')].filter(e=>txt(e.querySelector('h4,#details h4,.macro-markers')));
  const chapters=chapterItems.map(e=>({title:textOf(e,['h4','#details h4','.macro-markers']),time:textOf(e,['#time','#details #time'])}));
  const unique=[];const seen=new Set();for(const c of chapters){const k=c.title+'|'+c.time;if(!seen.has(k)){seen.add(k);unique.push(c);}}
  const channelLink=document.querySelector('ytd-watch-metadata ytd-channel-name a,#owner ytd-channel-name a,#upload-info #channel-name a');
  return {
    title:textOf(document,['ytd-watch-metadata h1 yt-formatted-string','ytd-watch-metadata h1','#title h1','h1.title'])||document.title.replace(/ - YouTube$/,''),
    channel:channelLink?txt(channelLink):'', channelHref:channelLink?channelLink.getAttribute('href'):'',
    subscribers:textOf(document,['#owner-sub-count','ytd-watch-metadata #owner-sub-count']),
    info:[...new Set(info)], description,
    likeLabel:like?(like.getAttribute('aria-label')||''):'', likeText:like?txt(like):'', liked:like?like.getAttribute('aria-pressed')==='true':null,
    subscribeText:sub?txt(sub):'', subscribeLabel:sub?(sub.getAttribute('aria-label')||''):'',
    subscribed:subRenderer&&subRenderer.hasAttribute('subscribed')?true:null,
    chapters:unique,
    signedIn:document.querySelector('#avatar-btn,button#avatar-btn')?true:(document.querySelector('ytd-masthead a[href*="ServiceLogin"],#masthead a[href*="ServiceLogin"],ytd-masthead [aria-label="Sign in"]')?false:null)
  };
}
'''

COMMENTS_JS = HELPERS + r'''
async (wanted)=>{
  const box=document.querySelector('ytd-comments#comments,#comments');
  if(!box)return {found:false,threads:[],off:''};
  box.scrollIntoView({block:'start'});
  const end=Date.now()+8000;
  let threads=[];
  while(Date.now()<end){
    threads=[...box.querySelectorAll('ytd-comment-thread-renderer')];
    const off=[...box.querySelectorAll('ytd-message-renderer,yt-empty-state-view-model')].find(shown);
    if(threads.length>=wanted||off)break;
    if(threads.length){window.scrollBy(0,600);}
    await new Promise(r=>setTimeout(r,250));
  }
  const off=[...box.querySelectorAll('ytd-message-renderer,yt-empty-state-view-model')].find(shown);
  const count=textOf(box,['ytd-comments-header-renderer #count','#count']);
  return {found:true,off:off?txt(off):'',count,threads:threads.slice(0,wanted).map(t=>({
    author:textOf(t,['#author-text','#header-author #author-text','#author-comment-badge']),
    text:textOf(t,['#content-text','#comment-content #content-text']),
    time:textOf(t,['#published-time-text','#header-author #published-time-text']),
    likes:textOf(t,['#vote-count-middle']),
    replies:textOf(t,['#more-replies button','#replies #more-replies','ytd-comment-replies-renderer #more-replies']),
    pinned:!!t.querySelector('#pinned-comment-badge ytd-pinned-comment-badge-renderer,#pinned-comment-badge:not([hidden]) *')
  }))};
}
'''


def spoken_comment(comment):
    parts = [comment.get('author') or 'Unknown author', comment.get('time'), comment.get('text') or 'No text']
    likes = _clean(comment.get('likes'))
    if likes and likes != '0':
        parts.append(spoken_count(likes) + (' like' if likes == '1' else ' likes'))
    replies = _clean(comment.get('replies'))
    if replies and re.search(r'\d', replies):
        parts.append(spoken_count(replies))
    text = '. '.join(_clean(p).rstrip('.') for p in parts if _clean(p))
    return ('Pinned. ' if comment.get('pinned') else '') + text


def chapters_from_description(description):
    """Chapters written in a description, one per line ("0:00 Intro"). YouTube needs at least
    three, starting at 0:00, to show them; the same rule is used here."""
    found = []
    for line in str(description or '').splitlines() or [description]:
        for match in re.finditer(r'(?:^|\s)((?:\d+:)?\d{1,2}:\d{2})\s+[-–:]?\s*([^\n]+?)(?=\s+(?:\d+:)?\d{1,2}:\d{2}\s|$)', line):
            seconds = clock_seconds(match[1])
            if seconds is not None:
                found.append({'title': _clean(match[2]), 'time': seconds})
    if len(found) < 3 or found[0]['time'] != 0:
        return []
    return found


QUALITY_NAMES = {'highres': 'Highest', 'hd2880': '2880p', 'hd2160': '2160p, 4K', 'hd1440': '1440p', 'hd1080': '1080p, HD',
                 'hd720': '720p, HD', 'large': '480p', 'medium': '360p', 'small': '240p', 'tiny': '144p', 'auto': 'Auto'}


def quality_name(code):
    return QUALITY_NAMES.get(str(code or ''), str(code or 'Unknown'))


class YouTubePage:
    """The youtube.com page in the guided browser. `page` is a function that returns the
    current Playwright page (the browser may restart, or move to another tab)."""

    def __init__(self, page):
        self._page = page

    @property
    def page(self):
        page = self._page()
        if page is None or page.is_closed():
            raise YouTubeError('The browser closed. Press Enter to start YouTube again.')
        return page

    def evaluate(self, script, argument=None):
        try:
            return self.page.evaluate(script, argument) if argument is not None else self.page.evaluate(script)
        except YouTubeError:
            raise
        except Exception as exc:
            if 'closed' in str(exc).casefold() or 'target' in str(exc).casefold() and 'crash' in str(exc).casefold():
                raise YouTubeError('The browser closed. Press Enter to start YouTube again.') from exc
            raise

    # ---------------- lists
    def wait_ready(self, timeout=15000):
        try:
            self.page.wait_for_function(READY_JS, timeout=timeout, polling=200)
        except YouTubeError:
            raise
        except Exception:
            pass   # read whatever is there; read_list says what went wrong

    def read_list(self, which='main'):
        """(items, page facts). Raises YouTubeError with a plain message when nothing can be read."""
        try:
            raw = self.evaluate(LIST_JS, which)
        except YouTubeError:
            raise
        except Exception as exc:
            raise YouTubeError(CHANGED) from exc
        if not isinstance(raw, dict):
            raise YouTubeError(CHANGED)
        if raw.get('consent'):
            raise YouTubeError('YouTube asks you to accept or reject cookies first. Say open in browser to answer it. '
                               'Then say YouTube to come back.')
        if not is_youtube(raw.get('url')):
            raise YouTubeError('The browser is not on YouTube now. Press F5 to load the list again.')
        items, seen = [], set()
        for entry in raw.get('items') or []:
            if not isinstance(entry, dict):
                continue
            if entry.get('fallback') and not entry.get('title') and not entry.get('aria'):
                entry = dict(entry, title=entry['fallback'])
            try:
                item = item_from_raw(entry)
            except Exception:
                item = None
            if item is None:
                continue
            key = (item['kind'], item['id'] or item['url'] or item['title'])
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
        return items, raw

    def load_more(self):
        """Scroll to the bottom so the site adds the next part of a long list."""
        try:
            before = self.evaluate("()=>document.querySelectorAll('ytd-rich-item-renderer,ytd-video-renderer,ytd-playlist-video-renderer,ytd-grid-video-renderer,yt-lockup-view-model,ytd-channel-renderer').length")
            # The site adds more when its loading marker or the page bottom comes into view.
            self.evaluate("()=>{const s=document.scrollingElement||document.documentElement;window.scrollTo(0,s.scrollHeight);"
                          "const m=document.querySelector('ytd-continuation-item-renderer');if(m)m.scrollIntoView();"
                          "window.dispatchEvent(new Event('scroll'));}")
            self.page.wait_for_function("(n)=>document.querySelectorAll('ytd-rich-item-renderer,ytd-video-renderer,ytd-playlist-video-renderer,ytd-grid-video-renderer,yt-lockup-view-model,ytd-channel-renderer').length>n",
                                        arg=before, timeout=6000, polling=250)
            return True
        except YouTubeError:
            raise
        except Exception:
            return False

    # ---------------- player
    def state(self):
        try:
            value = self.evaluate(PLAYER_JS)
        except YouTubeError:
            raise
        except Exception as exc:
            raise YouTubeError(PLAYER_CHANGED) from exc
        if not isinstance(value, dict):
            raise YouTubeError(PLAYER_CHANGED)
        return value

    def wait_for_player(self, timeout=15000):
        try:
            self.page.wait_for_function("()=>!!document.querySelector('#movie_player video,#movie_player')||!!document.querySelector('.ytp-error,yt-playability-error-supported-renderers')",
                                        timeout=timeout, polling=200)
        except YouTubeError:
            raise
        except Exception:
            pass

    def _player_call(self, name, *args):
        """Call the page's player object; None when it has no such call."""
        return self.evaluate("""([name,args])=>{const p=document.getElementById('movie_player');
            if(!p||typeof p[name]!=='function')return {missing:true};
            try{const value=p[name](...args);return {value:value===undefined?null:value};}catch(e){return {missing:true};}}""", [name, list(args)])

    def _video(self, script, argument=None):
        """Run a script on the <video> element: (v, arg) => value. None when there is no video."""
        return self.evaluate("""([script,arg])=>{const v=document.querySelector('#movie_player video,video.html5-main-video,video');
            if(!v)return null;return (new Function('v','arg',script))(v,arg);}""", [script, argument])

    def click(self, selectors, timeout=2500):
        """A real click on the first shown element; False when none is shown."""
        page = self.page
        for selector in selectors:
            try:
                locator = page.locator(selector)
                count = locator.count()
            except Exception:
                continue
            for index in range(min(count, 5)):
                target = locator.nth(index)
                try:
                    if not target.is_visible():
                        continue
                    target.click(timeout=timeout)
                    return True
                except Exception:
                    continue
        return False

    def press(self, key):
        """A real key press on the player, for a site key such as k, c or f."""
        page = self.page
        try:
            page.evaluate("()=>{const p=document.getElementById('movie_player');if(p){p.setAttribute('tabindex',p.getAttribute('tabindex')||'-1');p.focus();}}")
            page.keyboard.press(key)
            return True
        except Exception:
            return False

    def settle(self, ms=350):
        try:
            self.page.wait_for_timeout(ms)
        except YouTubeError:
            raise
        except Exception:
            pass

    def play(self):
        """Start playback. A browser starts sound only after a real click or key, so after
        the player's own call the Play button is clicked if the video is still paused."""
        if self._player_call('playVideo').get('missing'):
            self._video('const r=v.play();if(r&&r.catch)r.catch(()=>{});return true;')
        self.settle(400)
        if self.state().get('paused'):
            self.click(['.ytp-play-button', '.ytp-large-play-button']) or self.press('k')
            self.settle(400)
        return not self.state().get('paused')

    def pause(self):
        if self._player_call('pauseVideo').get('missing'):
            self._video('v.pause();return true;')
        self.settle(200)
        if not self.state().get('paused'):
            self.click(['.ytp-play-button']) or self.press('k')
            self.settle(250)
        return bool(self.state().get('paused'))

    def seek(self, seconds):
        if self._player_call('seekTo', float(seconds), True).get('missing'):
            if self._video('v.currentTime=arg;return true;', float(seconds)) is None:
                raise YouTubeError(PLAYER_CHANGED)
        self.settle(250)
        return self.state()

    def set_rate(self, rate):
        rates = self._player_call('getAvailablePlaybackRates').get('value') or [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2]
        rates = sorted(float(r) for r in rates if isinstance(r, (int, float)))
        rate = min(rates, key=lambda r: abs(r - rate)) if rates else rate
        if self._player_call('setPlaybackRate', rate).get('missing'):
            self._video('v.playbackRate=arg;return true;', rate)
        self.settle(150)
        return self.state().get('rate') or rate, rates

    def set_volume(self, percent):
        percent = max(0, min(100, int(round(percent))))
        if self._player_call('setVolume', percent).get('missing'):
            self._video('v.volume=arg/100;v.muted=false;return true;', percent)
        else:
            self._player_call('unMute')
        self.settle(150)
        return self.state().get('volume')

    def set_muted(self, muted):
        if self._player_call('mute' if muted else 'unMute').get('missing'):
            self._video('v.muted=arg;return true;', bool(muted))
        self.settle(150)
        return self.state().get('muted')

    def toggle_captions(self):
        if not (self.click(['.ytp-subtitles-button']) or self.press('c')):
            raise YouTubeError(PLAYER_CHANGED)
        self.settle(300)
        return self.state().get('captions') or {}

    def qualities(self):
        levels = self._player_call('getAvailableQualityLevels').get('value')
        return [q for q in (levels or []) if isinstance(q, str)]

    def set_quality(self, code):
        result = self._player_call('setPlaybackQualityRange', code, code)
        if result.get('missing'):
            result = self._player_call('setPlaybackQuality', code)
        if result.get('missing'):
            raise YouTubeError(PLAYER_CHANGED)
        self.settle(300)
        return self.state().get('quality')

    def next_video(self):
        if self._player_call('nextVideo').get('missing') and not self.click(['.ytp-next-button']):
            raise YouTubeError(PLAYER_CHANGED)

    def toggle_autoplay(self):
        if not self.click(['button[data-tooltip-target-id="ytp-autonav-toggle-button"]', '.ytp-autonav-toggle', '.ytp-autonav-toggle-button']):
            raise YouTubeError('Autoplay cannot be changed on this video.')
        self.settle(300)
        return self.state().get('autoplay')

    def toggle_fullscreen(self):
        if not (self.click(['.ytp-fullscreen-button']) or self.press('f')):
            raise YouTubeError(PLAYER_CHANGED)
        self.settle(500)
        return self.state().get('fullscreen')

    def skip_ad(self):
        return self.click(['.ytp-skip-ad-button', '.ytp-ad-skip-button-modern', '.ytp-ad-skip-button',
                           '.ytp-ad-skip-button-slot button', 'button[id^="skip-button"]'])

    # ---------------- details and account
    def details(self):
        value = self.evaluate(DETAILS_JS)
        if not isinstance(value, dict):
            raise YouTubeError(PLAYER_CHANGED)
        return value

    def comments(self, wanted=40):
        value = self.evaluate(COMMENTS_JS, wanted)
        if not isinstance(value, dict):
            raise YouTubeError(CHANGED)
        return value

    def signed_in(self):
        return self.evaluate("()=>document.querySelector('#avatar-btn,button#avatar-btn')?true:"
                             "(document.querySelector('ytd-masthead a[href*=\"ServiceLogin\"],#masthead a[href*=\"ServiceLogin\"],ytd-masthead [aria-label=\"Sign in\"]')?false:null)")

    LIKE = ['like-button-view-model button', '#segmented-like-button button', 'ytd-toggle-button-renderer#like-button button',
            'button[aria-label^="like this video"]', 'button[aria-label^="Like this video"]']

    def like(self):
        """Press Like. Returns the new pressed state."""
        if not self.click(self.LIKE):
            raise YouTubeError('The Like button could not be found. Say open in browser to use the page itself.')
        self.settle(500)
        return self.details().get('liked')

    def subscribe(self, wanted):
        """Subscribe (wanted True) or unsubscribe. The site may ask to confirm; that is answered here,
        because you already said Yes in Voice Companion. Returns the new state, or None if unknown."""
        buttons = ['ytd-watch-metadata #subscribe-button button', '#owner #subscribe-button button',
                   'ytd-subscribe-button-renderer button', 'yt-subscribe-button-view-model button']
        if not self.click(buttons):
            raise YouTubeError('The Subscribe button could not be found. Say open in browser to use the page itself.')
        self.settle(500)
        if not wanted:
            # Newer pages open a menu with Unsubscribe; then a question with an Unsubscribe button.
            self.click(['tp-yt-paper-listbox ytd-menu-service-item-renderer:has-text("Unsubscribe")',
                        'yt-list-item-view-model:has-text("Unsubscribe")', 'tp-yt-paper-item:has-text("Unsubscribe")'], timeout=1500)
            self.settle(400)
            self.click(['yt-confirm-dialog-renderer #confirm-button button', 'yt-confirm-dialog-renderer #confirm-button',
                        'tp-yt-paper-dialog button:has-text("Unsubscribe")'], timeout=1500)
            self.settle(500)
        return subscribed_state(self.details())

    SAVE = ['ytd-watch-metadata button[aria-label="Save to playlist"]', '#actions button[aria-label="Save to playlist"]',
            'ytd-watch-metadata button[aria-label^="Save"]', '#actions button:has-text("Save")']
    OPTIONS_JS = HELPERS + r'''
    ()=>{
      const dialog=[...document.querySelectorAll('ytd-add-to-playlist-renderer,yt-sheet-view-model,tp-yt-paper-dialog,ytd-popup-container yt-contextual-sheet-layout')].find(shown);
      if(!dialog)return null;
      const old=[...dialog.querySelectorAll('ytd-playlist-add-to-option-renderer')].map(o=>{const box=o.querySelector('#checkbox,tp-yt-paper-checkbox');
        return {label:textOf(o,['#label','#checkbox-label','yt-formatted-string']),checked:!!box&&(box.hasAttribute('checked')||box.getAttribute('aria-checked')==='true')};});
      if(old.length)return old;
      return [...dialog.querySelectorAll('toggleable-list-item-view-model,yt-list-item-view-model')].filter(shown).map(o=>{
        const target=o.querySelector('[aria-pressed],[aria-checked]')||o;
        return {label:textOf(o,['.yt-list-item-view-model__title','.yt-list-item-view-model-wiz__title','.yt-core-attributed-string']),
                checked:target.getAttribute('aria-pressed')==='true'||target.getAttribute('aria-checked')==='true'};}).filter(o=>o.label);
    }
    '''

    def save_options(self):
        """Open the site's Save list and read it: [{'label', 'checked'}]."""
        if not self.click(self.SAVE):
            if self.click(['ytd-watch-metadata #actions ytd-menu-renderer button[aria-label="More actions"]', '#actions button[aria-label="More actions"]']):
                self.settle(400)
                self.click(['ytd-menu-service-item-renderer:has-text("Save")', 'yt-list-item-view-model:has-text("Save")',
                            'tp-yt-paper-item:has-text("Save")'])
            else:
                raise YouTubeError('The Save button could not be found. Say open in browser to use the page itself.')
        try:
            self.page.wait_for_function('()=>!!document.querySelector("ytd-playlist-add-to-option-renderer,toggleable-list-item-view-model")', timeout=6000)
        except YouTubeError:
            raise
        except Exception:
            pass
        options = self.evaluate(self.OPTIONS_JS)
        if not options:
            self.close_dialog()
            raise YouTubeError('The list of playlists could not be read. Say open in browser to use the page itself.')
        return options

    def toggle_option(self, label):
        """Check or uncheck one playlist in the open Save list. Returns its new state."""
        quoted = label.replace('\\', '\\\\').replace('"', '\\"')
        if not self.click(['ytd-playlist-add-to-option-renderer:has-text("' + quoted + '") #checkbox',
                           'ytd-playlist-add-to-option-renderer:has-text("' + quoted + '")',
                           'toggleable-list-item-view-model:has-text("' + quoted + '")',
                           'yt-list-item-view-model:has-text("' + quoted + '")']):
            raise YouTubeError(label + ' could not be found in the Save list.')
        self.settle(400)
        options = self.evaluate(self.OPTIONS_JS) or []
        return next((o['checked'] for o in options if o.get('label') == label), None)

    def close_dialog(self):
        try:
            if not self.click(['ytd-add-to-playlist-renderer #close-button button', 'ytd-add-to-playlist-renderer #close-button',
                               'tp-yt-paper-dialog [aria-label="Close"]'], timeout=1000):
                self.page.keyboard.press('Escape')
        except YouTubeError:
            raise
        except Exception:
            pass

    def post_comment(self, text):
        """Type and post a comment. The user has already said Yes."""
        page = self.page
        comments = self.comments(1)
        if comments.get('off'):
            raise YouTubeError('Comments are turned off for this video.')
        if not self.click(['#simplebox-placeholder', 'ytd-comment-simplebox-renderer #placeholder-area']):
            raise YouTubeError('The comment box could not be found. Say open in browser to use the page itself.')
        try:
            page.wait_for_selector('#contenteditable-root[contenteditable="true"],ytd-commentbox #contenteditable-root', timeout=5000)
            page.click('#contenteditable-root', timeout=2000)
            page.keyboard.insert_text(text)
        except Exception as exc:
            raise YouTubeError('The comment box did not open. You may need to sign in. Say open in browser to check.') from exc
        self.settle(300)
        if not self.click(['ytd-commentbox #submit-button button', '#submit-button button', 'ytd-commentbox #submit-button']):
            raise YouTubeError('The Comment button could not be pressed. Your comment was not posted.')
        try:
            page.wait_for_function('(t)=>[...document.querySelectorAll("ytd-comment-thread-renderer #content-text")].some(e=>e.textContent.includes(t))',
                                   arg=text[:60], timeout=6000)
        except Exception:
            return False
        return True


def subscribed_state(details):
    if details.get('subscribed') is True:
        return True
    words = (str(details.get('subscribeText') or '') + ' ' + str(details.get('subscribeLabel') or '')).casefold()
    if 'unsubscribe' in words or re.search(r'\bsubscribed\b', words):
        return True
    if 'subscribe' in words:
        return False
    return None


def like_count(details):
    label = str(details.get('likeLabel') or '')
    match = re.search(r'([\d,]+)\s+other', label)
    if match:
        return match[1] + ' likes'
    text = _clean(details.get('likeText'))
    return spoken_count(text) + ' likes' if re.search(r'\d', text) else ''
