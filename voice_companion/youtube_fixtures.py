"""Saved pages shaped like the real youtube.com, for the YouTube tests.

Live youtube.com cannot be reached by the tests. These pages copy the parts
of the real site that youtube_page.py reads: the custom element names, ids,
classes and attributes, in both the older layout (ytd-rich-grid-media,
ytd-video-renderer) and the newer one (yt-lockup-view-model). The player
copies the real #movie_player calls (getPlayerState, playVideo, seekTo and so
on). With window.blockScriptPlay set it refuses to start from a script, as a
browser may, and starts only from a real click on its Play button.

The tests serve these pages at https://www.youtube.com/ with Playwright routes.
"""

MASTHEAD_SIGNED_OUT = ('<div id="masthead"><ytd-masthead><a href="https://accounts.google.com/ServiceLogin?service=youtube" '
                       'aria-label="Sign in">Sign in</a></ytd-masthead></div>')
MASTHEAD_SIGNED_IN = '<div id="masthead"><ytd-masthead><button id="avatar-btn" aria-label="Account menu">W</button></ytd-masthead></div>'

STYLE = '<style>[hidden]{display:none!important} body{margin:0} .tile{display:block;min-height:40px}</style>'


def rich_media(ident, title, channel, views, age, length, overlay='DEFAULT'):
    """The older home layout: ytd-rich-item-renderer > ytd-rich-grid-media."""
    aria = f'{title} by {channel} {views} {age}'
    return (f'<ytd-rich-item-renderer class="tile"><div id="content"><ytd-rich-grid-media>'
            f'<a id="thumbnail" href="/watch?v={ident}"><ytd-thumbnail-overlay-time-status-renderer overlay-style="{overlay}">'
            f'<div class="thumbnail-overlay-badge-shape"><badge-shape class="badge-shape-wiz"><div class="badge-shape-wiz__text">{length}</div></badge-shape></div>'
            f'</ytd-thumbnail-overlay-time-status-renderer></a>'
            f'<div id="details"><h3><a id="video-title-link" href="/watch?v={ident}" aria-label="{aria}" title="{title}">'
            f'<yt-formatted-string id="video-title">{title}</yt-formatted-string></a></h3>'
            f'<ytd-channel-name><div id="text-container"><yt-formatted-string id="text"><a href="/@{channel.replace(" ", "")}">{channel}</a></yt-formatted-string></div></ytd-channel-name>'
            f'<div id="metadata-line"><span class="inline-metadata-item">{views}</span><span class="inline-metadata-item">{age}</span></div>'
            f'</div></ytd-rich-grid-media></div></ytd-rich-item-renderer>')


def lockup(ident, title, channel, views, age, length, live=False, playlist=None):
    """The newer layout: yt-lockup-view-model with metadata rows and a badge."""
    href = f'/playlist?list={playlist}' if playlist else f'/watch?v={ident}'
    badge = ('<yt-collection-thumbnail-view-model><div class="yt-badge-shape__text">' + length + '</div></yt-collection-thumbnail-view-model>'
             if playlist else
             '<yt-thumbnail-overlay-badge-view-model><badge-shape class="yt-badge-shape' + (' yt-badge-shape--thumbnail-live' if live else '') +
             '"><div class="yt-badge-shape__text">' + length + '</div></badge-shape></yt-thumbnail-overlay-badge-view-model>')
    second = (f'<span class="yt-core-attributed-string">{views}</span><span class="yt-content-metadata-view-model__delimiter"> • </span>'
              f'<span class="yt-core-attributed-string">{age}</span>') if views else ''
    return (f'<ytd-rich-item-renderer class="tile"><div id="content"><yt-lockup-view-model><div class="yt-lockup-view-model">'
            f'<a class="yt-lockup-view-model__content-image" href="{href}">{badge}</a>'
            f'<div class="yt-lockup-view-model__metadata"><yt-lockup-metadata-view-model><h3 class="yt-lockup-metadata-view-model__heading-reset">'
            f'<a class="yt-lockup-metadata-view-model__title" href="{href}" aria-label="{title}"><span class="yt-core-attributed-string">{title}</span></a></h3>'
            f'<yt-content-metadata-view-model><div class="yt-content-metadata-view-model__metadata-row">'
            f'<span class="yt-core-attributed-string"><a href="/@{channel.replace(" ", "")}">{channel}</a></span></div>'
            f'<div class="yt-content-metadata-view-model__metadata-row">{second}</div>'
            f'</yt-content-metadata-view-model></yt-lockup-metadata-view-model></div></div></yt-lockup-view-model></div></ytd-rich-item-renderer>')


def short(ident, title, views):
    return (f'<ytm-shorts-lockup-view-model-v2 class="tile"><ytm-shorts-lockup-view-model><a href="/shorts/{ident}" class="shortsLockupViewModelHostEndpoint"></a>'
            f'<div class="shortsLockupViewModelHostOutsideMetadata"><h3 class="shortsLockupViewModelHostOutsideMetadataTitle"><a href="/shorts/{ident}" title="{title}">'
            f'<span>{title}</span></a></h3><div class="shortsLockupViewModelHostOutsideMetadataSubhead"><span>{views}</span></div></div>'
            f'</ytm-shorts-lockup-view-model></ytm-shorts-lockup-view-model-v2>')


AD = ('<ytd-rich-item-renderer class="tile"><div id="content"><ytd-ad-slot-renderer><ytd-in-feed-ad-layout-renderer>'
      '<a href="https://www.googleadservices.com/pagead/aclk?x=1"><h3 id="title">Try Brand Coffee today</h3></a>'
      '<div id="byline">Sponsored · Brand Coffee</div><ad-badge-view-model>Sponsored</ad-badge-view-model>'
      '</ytd-in-feed-ad-layout-renderer></ytd-ad-slot-renderer></div></ytd-rich-item-renderer>')

MORE_ON_SCROLL = '''<script>
let added=false;
addEventListener('scroll',()=>{
  if(added||innerHeight+scrollY<document.documentElement.scrollHeight-5)return;
  added=true;
  setTimeout(()=>{document.querySelector('#contents').insertAdjacentHTML('beforeend',MORE);},150);
});
</script>'''


def page(body, signed_in=False, title='YouTube', script=''):
    return ('<!doctype html><html><head><meta charset="utf-8"><title>' + title + '</title>' + STYLE + '</head><body><ytd-app>' +
            (MASTHEAD_SIGNED_IN if signed_in else MASTHEAD_SIGNED_OUT) +
            '<div id="content"><ytd-page-manager id="page-manager">' + body + '</ytd-page-manager></div></ytd-app>' + script + '</body></html>')


def home(signed_in=False):
    items = (rich_media('vid00000001', 'Never Gonna Give You Up', 'Rick Astley', '1.6B views', '15 years ago', '3:33') +
             lockup('vid00000002', 'How to bake bread', 'Kitchen Basics', '12K views', '2 days ago', '10:05') +
             lockup('vid00000003', 'Morning news', 'News Now', '5.2K watching', '', 'LIVE', live=True) + AD +
             '<ytd-rich-section-renderer><ytd-rich-shelf-renderer is-shorts><div id="contents-shelf">' +
             short('short000001', 'Cat jumps', '2.1M views') + '</div></ytd-rich-shelf-renderer></ytd-rich-section-renderer>')
    more = rich_media('vid00000004', 'Garden tour', 'Green Thumb', '800 views', '1 month ago', '1:02:03').replace('`', '')
    body = ('<ytd-browse page-subtype="home"><ytd-rich-grid-renderer><div id="contents">' + items +
            '</div></ytd-rich-grid-renderer></ytd-browse>'
            # A page YouTube keeps hidden after moving on. It must never be read.
            '<ytd-search hidden><ytd-video-renderer><a id="video-title" href="/watch?v=hidden00001" title="Old hidden result">Old hidden result</a></ytd-video-renderer></ytd-search>')
    return page(body, signed_in, script='<script>const MORE=`' + more + '`;</script>' + MORE_ON_SCROLL)


def video_renderer(ident, title, channel, views, age, length):
    aria = f'{title} by {channel} {views} {age} {length}'
    return (f'<ytd-video-renderer class="tile"><div id="dismissible"><ytd-thumbnail><a id="thumbnail" href="/watch?v={ident}">'
            f'<ytd-thumbnail-overlay-time-status-renderer overlay-style="DEFAULT"><span id="text">{length.split(" ")[0] if ":" in length else "4:01"}</span>'
            f'</ytd-thumbnail-overlay-time-status-renderer></a></ytd-thumbnail><div class="text-wrapper"><div id="meta"><div id="title-wrapper"><h3>'
            f'<a id="video-title" class="yt-simple-endpoint" href="/watch?v={ident}" title="{title}" aria-label="{aria}"><yt-formatted-string>{title}</yt-formatted-string></a>'
            f'</h3></div><ytd-video-meta-block><div id="metadata"><div id="metadata-line"><span class="inline-metadata-item">{views}</span>'
            f'<span class="inline-metadata-item">{age}</span></div></div></ytd-video-meta-block></div>'
            f'<div id="channel-info"><ytd-channel-name><div id="text-container"><yt-formatted-string id="text"><a href="/@x">{channel}</a></yt-formatted-string></div></ytd-channel-name></div>'
            f'</div></div></ytd-video-renderer>')


CHANNEL = ('<ytd-channel-renderer class="tile"><a id="main-link" href="/@KitchenBasics"><div id="info"><ytd-channel-name id="channel-title">'
           '<div id="text-container"><yt-formatted-string id="text">Kitchen Basics</yt-formatted-string></div></ytd-channel-name>'
           '<div id="metadata"><span id="subscribers">1.2M subscribers</span><span id="video-count">300 videos</span></div></div></a></ytd-channel-renderer>')


def search(query, sp=''):
    body = ('<ytd-search><div id="contents"><ytd-section-list-renderer><ytd-item-section-renderer><div id="contents">' +
            video_renderer('vid00000010', 'Bread for beginners', 'Kitchen Basics', '1,234 views', '3 weeks ago', '12:00') + CHANNEL +
            lockup('', 'Bread recipes', 'Kitchen Basics', '', '', '25 videos', playlist='PLbread01') +
            '</div></ytd-item-section-renderer></ytd-section-list-renderer></div></ytd-search>')
    return page(body, title=query + ' - YouTube', script='<script>window.searchFilter=' + repr(sp) + ';</script>')


# YouTube's search filters since January 2026, as the results page lists them in ytInitialData:
# a "Search filters" dialog with groups. Each filter's link already combines it with the filters
# that are on; the link of a filter that is on takes it away. Features may be combined; the
# other groups hold one choice. Type Channels disables the Duration choices, as on YouTube.
# The codes here are made up on purpose ("t=shorts;d=week"), so the tests show that the screen
# uses the page's own links and names, and never builds codes when the page lists its filters.
FILTER_GROUPS = (('Type', 't', (('videos', 'Videos'), ('shorts', 'Shorts'), ('channels', 'Channels'), ('playlists', 'Playlists'),
                                ('movies', 'Movies'))),
                 ('Duration', 'l', (('under3', 'Under 3 minutes'), ('3to20', '3 - 20 minutes'), ('over20', 'Over 20 minutes'))),
                 ('Upload date', 'd', (('today', 'Today'), ('week', 'This week'), ('month', 'This month'), ('year', 'This year'))),
                 ('Features', 'f', (('live', 'Live'), ('4k', '4K'), ('hd', 'HD'), ('cc', 'Subtitles/CC'), ('vr180', 'VR180'))),
                 ('Prioritize', 's', (('relevance', 'Relevance'), ('popularity', 'Popularity'))))


def filter_state(sp):
    import base64
    try:
        text = base64.urlsafe_b64decode(sp.encode() + b'=' * (-len(sp) % 4)).decode()
    except Exception:
        return {}
    state = {}
    for part in filter(None, text.split(';')):
        key, _, value = part.partition('=')
        state[key] = value.split(',') if key == 'f' else value
    return state


def filter_sp(state):
    import base64
    text = ';'.join(k + '=' + (','.join(v) if isinstance(v, list) else v) for k, v in sorted(state.items()) if v)
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip('=') if text else ''


def filter_data(query, sp):
    """window.ytInitialData for a results page, with the filter dialog as YouTube lists it."""
    import json
    from urllib.parse import quote, quote_plus
    state = filter_state(sp)
    groups = []
    for title, key, choices in FILTER_GROUPS:
        filters = []
        for value, label in choices:
            on = value in state.get(key, []) if key == 'f' else state.get(key) == value
            after = {k: (list(v) if isinstance(v, list) else v) for k, v in state.items()}
            if key == 'f':
                after['f'] = [x for x in after.get('f', []) if x != value] if on else after.get('f', []) + [value]
            else:
                after[key] = '' if on else value
            if key == 's' and value == 'relevance':
                after['s'] = ''
            code = filter_sp(after)
            renderer = {'label': {'simpleText': label}, 'tooltip': ('Remove ' if on else 'Search for ') + label}
            if key == 'l' and state.get('t') == 'channels':
                renderer['status'] = 'FILTER_STATUS_DISABLED'
            else:
                url = '/results?search_query=' + quote_plus(query) + ('&sp=' + quote(quote(code, safe=''), safe='') if code else '')
                renderer['navigationEndpoint'] = {'commandMetadata': {'webCommandMetadata': {'url': url}},
                                                  'searchEndpoint': {'query': query, 'params': quote(code, safe='')}}
                if on:
                    renderer['status'] = 'FILTER_STATUS_SELECTED'
            filters.append({'searchFilterRenderer': renderer})
        groups.append({'searchFilterGroupRenderer': {'title': {'simpleText': title}, 'filters': filters}})
    data = {'contents': {'twoColumnSearchResultsRenderer': {'primaryContents': {}}},
            'header': {'searchHeaderRenderer': {'searchFilterButton': {'buttonRenderer': {'text': {'runs': [{'text': 'Filters'}]},
                'command': {'openPopupAction': {'popup': {'searchFilterOptionsDialogRenderer': {
                    'title': {'runs': [{'text': 'Search filters'}]}, 'groups': groups}}}}}}}}}
    return '<script>var ytInitialData = ' + json.dumps(data) + ';</script>'


def search_listing_filters(query, sp=''):
    """Results whose page lists its own filters (YouTube since 2025). Movies finds nothing."""
    state = filter_state(sp)
    if state.get('t') == 'movies':
        body = ('<ytd-search><div id="contents"><ytd-background-promo-renderer><div id="title">No results found</div>'
                '<div id="body">Try different keywords or remove search filters</div></ytd-background-promo-renderer></div></ytd-search>')
    else:
        items = [CHANNEL] if state.get('t') == 'channels' else [
            video_renderer('vid00000010', 'Bread for beginners', 'Kitchen Basics', '1,234 views', '3 weeks ago', '12:00'),
            video_renderer('vid00000011', 'Quick bread', 'Fast Food', '90 views', '1 day ago', '2:10')]
        body = ('<ytd-search><div id="contents"><ytd-section-list-renderer><ytd-item-section-renderer><div id="contents">' +
                ''.join(items) + '</div></ytd-item-section-renderer></ytd-section-list-renderer></div></ytd-search>')
    return page(body, title=query + ' - YouTube', script=filter_data(query, sp) + '<script>window.searchFilter=' + repr(sp) + ';</script>')


def no_results(query):
    return page('<ytd-search><div id="contents"><ytd-background-promo-renderer><div id="title">No results found</div>'
                '<div id="body">Try different keywords or remove search filters</div></ytd-background-promo-renderer></div></ytd-search>',
                title=query + ' - YouTube')


def history_signed_out():
    return page('<ytd-browse page-subtype="history"><ytd-background-promo-renderer><div id="title">Keep track of what you watch</div>'
                '<div id="body">Watch history isn\'t viewable when signed out.</div>'
                '<a href="https://accounts.google.com/ServiceLogin?service=youtube" aria-label="Sign in">Sign in</a>'
                '</ytd-background-promo-renderer></ytd-browse>')


def watch_later():
    rows = ''.join(
        f'<ytd-playlist-video-renderer class="tile"><a id="thumbnail" href="/watch?v={i}&list=WL"><ytd-thumbnail-overlay-time-status-renderer overlay-style="DEFAULT">'
        f'<span id="text">{length}</span></ytd-thumbnail-overlay-time-status-renderer></a><div id="meta"><h3>'
        f'<a id="video-title" href="/watch?v={i}&list=WL" title="{t}">{t}</a></h3><ytd-video-meta-block><ytd-channel-name><div id="text-container">'
        f'<yt-formatted-string id="text"><a href="/@c">{c}</a></yt-formatted-string></div></ytd-channel-name>'
        f'<div id="video-info"><span>{v}</span><span>•</span><span>{a}</span></div></ytd-video-meta-block></div></ytd-playlist-video-renderer>'
        for i, t, c, v, a, length in (('vid00000020', 'Saved talk', 'Speaker', '9K views', '1 year ago', '45:10'),
                                      ('vid00000021', 'Saved song', 'Singer', '3M views', '5 years ago', '4:12')))
    return page('<ytd-browse page-subtype="playlist"><ytd-playlist-video-list-renderer><div id="contents">' + rows +
                '</div></ytd-playlist-video-list-renderer></ytd-browse>', signed_in=True)


def home_empty():
    """Signed out with watch history off: YouTube shows no videos on Home, only a hint."""
    return page('<ytd-browse page-subtype="home"><ytd-rich-grid-renderer><div id="contents"><ytd-feed-nudge-renderer>'
                '<div id="title">Try searching to get started</div><div id="subtitle">Start watching videos to help us build a feed of videos you\'ll love.</div>'
                '</ytd-feed-nudge-renderer></div></ytd-rich-grid-renderer></ytd-browse>')


def changed():
    """YouTube after a redesign this code does not know: no list can be read."""
    return page('<ytd-browse page-subtype="home"><new-grid-of-the-future><div class="card">Something new</div></new-grid-of-the-future></ytd-browse>')


# ------------------------------------------------------------------ the watch page
PLAYER_SCRIPT = r'''<script>
const p=document.getElementById('movie_player');const v=p.querySelector('video');
const VIDEOS=VIDEO_LIST;
let i=0;
const s={state:-1,time:0,rate:1,vol:100,muted:false,q:'hd720'};
window.playerCalls=[];
function current(){return VIDEOS[i];}
function show(){
  p.classList.toggle('paused-mode',s.state!==1);
  document.querySelector('.ytp-play-button').setAttribute('aria-label',s.state===1?'Pause (k)':'Play (k)');
  document.querySelector('ytd-watch-metadata h1 yt-formatted-string').textContent=current().title;
  document.querySelector('ytd-watch-metadata ytd-channel-name a').textContent=current().author;
  document.title=current().title+' - YouTube';
}
function start(real){
  // A browser may refuse to start sound from a script. Playwright's own page
  // scripts count as a user gesture, so the tests set blockScriptPlay to copy that refusal.
  if(!real&&window.blockScriptPlay){window.playerCalls.push('blocked');return;}
  s.state=1;show();
}
p.getPlayerState=()=>s.state;
p.playVideo=()=>{window.playerCalls.push('playVideo');start();};
p.pauseVideo=()=>{window.playerCalls.push('pauseVideo');if(s.state===1){s.state=2;show();}};
p.seekTo=(t)=>{window.playerCalls.push('seekTo '+t);s.time=Math.max(0,Math.min(current().duration,t));};
p.getCurrentTime=()=>s.time;
p.getDuration=()=>current().duration;
p.getPlaybackRate=()=>s.rate;
p.setPlaybackRate=(r)=>{s.rate=r;};
p.getAvailablePlaybackRates=()=>[0.25,0.5,0.75,1,1.25,1.5,1.75,2];
p.getVolume=()=>s.vol;p.setVolume=(n)=>{s.vol=n;};
p.isMuted=()=>s.muted;p.mute=()=>{s.muted=true;};p.unMute=()=>{s.muted=false;};
p.getPlaybackQuality=()=>s.q;
p.getAvailableQualityLevels=()=>['hd1080','hd720','large','medium','small','tiny','auto'];
p.setPlaybackQualityRange=(a)=>{s.q=a;};
p.getVideoData=()=>({video_id:current().id,title:current().title,author:current().author,isLive:false});
p.nextVideo=()=>{window.playerCalls.push('nextVideo');i=Math.min(VIDEOS.length-1,i+1);s.time=0;history.pushState({},'','/watch?v='+current().id);show();};
document.querySelector('.ytp-play-button').addEventListener('click',()=>{if(s.state===1){s.state=2;show();}else start(true);});
document.querySelector('.ytp-subtitles-button').addEventListener('click',e=>{const b=e.currentTarget;
  const on=b.getAttribute('aria-pressed')!=='true';b.setAttribute('aria-pressed',String(on));
  document.querySelector('.ytp-caption-window-container').innerHTML=on?'<div class="caption-window"><span class="ytp-caption-segment">Hello and welcome</span> <span class="ytp-caption-segment">to the show.</span></div>':'';});
document.querySelector('button[data-tooltip-target-id="ytp-autonav-toggle-button"]').addEventListener('click',()=>{const t=document.querySelector('.ytp-autonav-toggle-button');
  t.setAttribute('aria-checked',String(t.getAttribute('aria-checked')!=='true'));});
document.querySelector('.ytp-fullscreen-button').addEventListener('click',()=>{p.classList.toggle('ytp-fullscreen');});
document.querySelector('.ytp-skip-ad-button').addEventListener('click',()=>{window.endAd();window.adSkipped=true;});
window.startAd=(skippable)=>{p.classList.add('ad-showing');document.querySelector('.ytp-ad-badge__text').textContent='Ad 1 of 2';
  document.querySelector('.ytp-ad-module').hidden=false;document.querySelector('.ytp-skip-ad-button').hidden=!skippable;};
window.allowSkip=()=>{document.querySelector('.ytp-skip-ad-button').hidden=false;};
window.endAd=()=>{p.classList.remove('ad-showing');document.querySelector('.ytp-ad-module').hidden=true;document.querySelector('.ytp-skip-ad-button').hidden=true;};
window.endVideo=()=>{s.state=0;s.time=current().duration;};
// Like, subscribe, save and comments behave as on the site for a signed-in account.
const like=document.querySelector('like-button-view-model button');
like.addEventListener('click',()=>{const on=like.getAttribute('aria-pressed')!=='true';like.setAttribute('aria-pressed',String(on));});
const sub=document.querySelector('#subscribe-button button');
sub.addEventListener('click',()=>{
  if(sub.textContent.trim()==='Subscribe'){sub.textContent='Subscribed';sub.setAttribute('aria-label','Unsubscribe from Rick Astley.');return;}
  document.querySelector('yt-confirm-dialog-renderer').hidden=false;});
document.querySelector('yt-confirm-dialog-renderer #confirm-button button').addEventListener('click',()=>{
  sub.textContent='Subscribe';sub.setAttribute('aria-label','Subscribe to Rick Astley.');document.querySelector('yt-confirm-dialog-renderer').hidden=true;});
document.querySelector('button[aria-label="Save to playlist"]').addEventListener('click',()=>{document.querySelector('ytd-add-to-playlist-renderer').hidden=false;});
for(const o of document.querySelectorAll('ytd-playlist-add-to-option-renderer'))o.addEventListener('click',()=>{const b=o.querySelector('#checkbox');
  if(b.hasAttribute('checked'))b.removeAttribute('checked');else b.setAttribute('checked','');b.setAttribute('aria-checked',String(b.hasAttribute('checked')));});
document.querySelector('ytd-add-to-playlist-renderer #close-button button').addEventListener('click',()=>{document.querySelector('ytd-add-to-playlist-renderer').hidden=true;});
document.querySelector('#description-inline-expander #expand').addEventListener('click',()=>{
  document.querySelector('#description-inline-expander #expanded').hidden=false;document.querySelector('#description-inline-expander #snippet').hidden=true;});
// Comments load once they are scrolled into view, as on the site.
let loaded=false;
new IntersectionObserver(es=>{if(loaded||!es.some(e=>e.isIntersecting))return;loaded=true;setTimeout(()=>{
  document.querySelector('ytd-comments #contents').innerHTML=COMMENTS;},200);}).observe(document.querySelector('ytd-comments'));
document.querySelector('#simplebox-placeholder').addEventListener('click',()=>{
  const box=document.querySelector('ytd-commentbox');box.hidden=false;document.querySelector('#contenteditable-root').focus();});
document.querySelector('ytd-commentbox #submit-button button').addEventListener('click',()=>{
  const text=document.querySelector('#contenteditable-root').textContent;
  document.querySelector('ytd-comments #contents').insertAdjacentHTML('afterbegin','<ytd-comment-thread-renderer><a id="author-text"><span>@will</span></a><span id="published-time-text"><a>now</a></span><yt-attributed-string id="content-text">'+text+'</yt-attributed-string></ytd-comment-thread-renderer>');
  window.posted=text;});
show();
</script>'''

COMMENT = ('<ytd-comment-thread-renderer><ytd-comment-view-model><div id="header-author"><a id="author-text"><span>{author}</span></a>'
           '<span id="published-time-text"><a>{time}</a></span></div><yt-attributed-string id="content-text"><span>{text}</span></yt-attributed-string>'
           '<span id="vote-count-middle">{likes}</span></ytd-comment-view-model><div id="replies"><div id="more-replies"><button>{replies}</button></div></div>'
           '</ytd-comment-thread-renderer>')


def watch(chapters=True, signed_in=True, comments_off=False):
    videos = [{'id': 'vid00000001', 'title': 'Never Gonna Give You Up', 'author': 'Rick Astley', 'duration': 213},
              {'id': 'vid00000002', 'title': 'How to bake bread', 'author': 'Kitchen Basics', 'duration': 605}]
    chapter_list = ''.join(f'<ytd-macro-markers-list-item-renderer><a id="endpoint"><div id="details"><h4 class="macro-markers">{t}</h4>'
                           f'<div id="time">{c}</div></div></a></ytd-macro-markers-list-item-renderer>'
                           for t, c in (('Intro', '0:00'), ('First verse', '0:43'), ('Chorus', '1:25'))) if chapters else ''
    comments = ''.join(COMMENT.format(author=a, time=t, text=x, likes=l, replies=r) for a, t, x, l, r in (
        ('@fan1', '2 days ago', 'A classic.', '1.2K', '3 replies'), ('@fan2', '1 week ago', 'Still great.', '', ''),
    )).replace('`', '')
    comments_area = ('<ytd-message-renderer><yt-formatted-string id="message">Comments are turned off.</yt-formatted-string></ytd-message-renderer>'
                     if comments_off else '')
    body = f'''<ytd-watch-flexy><div id="columns"><div id="primary">
<div id="player"><div id="movie_player" class="html5-video-player" tabindex="-1">
<video class="html5-main-video"></video>
<div class="ytp-caption-window-container"></div>
<div class="ytp-ad-module" hidden><span class="ytp-ad-badge__text"></span><div class="ytp-preview-ad__text">Video will play after ad</div></div>
<button class="ytp-skip-ad-button" hidden>Skip</button>
<div class="ytp-chrome-bottom"><button class="ytp-play-button ytp-button" aria-label="Play (k)">Play</button>
<a class="ytp-next-button ytp-button" aria-label="Next (SHIFT+n)">Next</a>
<button class="ytp-button" data-tooltip-target-id="ytp-autonav-toggle-button" aria-label="Autoplay is on"><div class="ytp-autonav-toggle-button" aria-checked="true"></div></button>
<button class="ytp-subtitles-button ytp-button" aria-pressed="false" aria-label="Subtitles/closed captions (c)">CC</button>
<button class="ytp-settings-button ytp-button" aria-label="Settings">Settings</button>
<button class="ytp-fullscreen-button ytp-button" aria-label="Full screen (f)">Full screen</button>
<div class="ytp-chapter-title-content">Intro</div></div>
</div></div>
<ytd-watch-metadata><div id="title"><h1><yt-formatted-string>Never Gonna Give You Up</yt-formatted-string></h1></div>
<div id="owner"><ytd-channel-name><div id="text-container"><yt-formatted-string id="text"><a href="/@RickAstleyYT">Rick Astley</a></yt-formatted-string></div></ytd-channel-name>
<yt-formatted-string id="owner-sub-count">4.2M subscribers</yt-formatted-string>
<div id="subscribe-button"><ytd-subscribe-button-renderer><button aria-label="Subscribe to Rick Astley.">Subscribe</button></ytd-subscribe-button-renderer></div></div>
<div id="actions"><segmented-like-dislike-button-view-model><like-button-view-model><button aria-pressed="false" aria-label="like this video along with 18,000,000 other people">18M</button></like-button-view-model></segmented-like-dislike-button-view-model>
<button aria-label="Save to playlist">Save</button></div>
<div id="description"><ytd-watch-info-text><div id="info"><span>1.6B views</span><span>15 years ago</span></div></ytd-watch-info-text>
<ytd-text-inline-expander id="description-inline-expander"><div id="snippet"><yt-attributed-string>The official video…</yt-attributed-string></div>
<tp-yt-paper-button id="expand">...more</tp-yt-paper-button>
<div id="expanded" hidden><yt-attributed-string>The official video for Never Gonna Give You Up.
0:00 Intro
0:43 First verse
1:25 Chorus</yt-attributed-string></div></ytd-text-inline-expander></div>
</ytd-watch-metadata>
<div style="height:1200px"></div>
<ytd-comments id="comments"><ytd-comments-header-renderer><h2 id="count"><span>2 Comments</span></h2></ytd-comments-header-renderer>
<ytd-comment-simplebox-renderer><div id="simplebox-placeholder">Add a comment...</div></ytd-comment-simplebox-renderer>
<ytd-commentbox hidden><div id="contenteditable-root" contenteditable="true"></div><ytd-button-renderer id="submit-button"><button>Comment</button></ytd-button-renderer></ytd-commentbox>
<div id="contents">{comments_area}</div></ytd-comments>
</div>
<div id="secondary"><div id="related">{lockup('vid00000002', 'How to bake bread', 'Kitchen Basics', '12K views', '2 days ago', '10:05')}</div></div>
</div></ytd-watch-flexy>
<ytd-engagement-panel-section-list-renderer target-id="engagement-panel-macro-markers-description-chapters">{chapter_list}</ytd-engagement-panel-section-list-renderer>
<ytd-popup-container>
<yt-confirm-dialog-renderer hidden><div id="main">Unsubscribe from Rick Astley?</div><yt-button-renderer id="confirm-button"><button>Unsubscribe</button></yt-button-renderer></yt-confirm-dialog-renderer>
<ytd-add-to-playlist-renderer hidden><div id="header">Save video to...</div>
<ytd-playlist-add-to-option-renderer><tp-yt-paper-checkbox id="checkbox" aria-checked="false"><div id="checkbox-label"><yt-formatted-string id="label">Watch later</yt-formatted-string></div></tp-yt-paper-checkbox></ytd-playlist-add-to-option-renderer>
<ytd-playlist-add-to-option-renderer><tp-yt-paper-checkbox id="checkbox" checked aria-checked="true"><div id="checkbox-label"><yt-formatted-string id="label">Favorites</yt-formatted-string></div></tp-yt-paper-checkbox></ytd-playlist-add-to-option-renderer>
<div id="close-button"><button aria-label="Close">X</button></div></ytd-add-to-playlist-renderer>
</ytd-popup-container>'''
    script = ('<script>const VIDEO_LIST=' + __import__('json').dumps(videos) + ';const COMMENTS=`' + comments + '`;</script>' +
              PLAYER_SCRIPT.replace('const VIDEOS=VIDEO_LIST;', 'const VIDEOS=VIDEO_LIST;'))
    return page(body, signed_in, title='Never Gonna Give You Up - YouTube', script=script)


def routes(context, overrides=None, listed_filters=False):
    """Serve the saved pages for https://www.youtube.com/ in a Playwright context.
    overrides maps a path start (such as '/feed/history') to the page to serve.
    listed_filters: results pages list their own filters, as YouTube's do now."""
    from urllib.parse import parse_qs, urlsplit
    overrides = dict(overrides or {})
    served = []

    def answer(route):
        url = urlsplit(route.request.url)
        served.append(route.request.url)
        query = parse_qs(url.query)
        body = None
        for start, value in overrides.items():
            if (url.path + ('?' + url.query if url.query else '')).startswith(start):
                body = value() if callable(value) else value
        if body is None:
            if url.path == '/results':
                body = (search_listing_filters if listed_filters else search)(query.get('search_query', [''])[0], query.get('sp', [''])[0])
            elif url.path == '/watch':
                body = watch()
            elif url.path == '/feed/history':
                body = history_signed_out()
            elif url.path == '/playlist':
                body = watch_later()
            else:
                body = home()
        route.fulfill(status=200, content_type='text/html; charset=utf-8', body=body)
    context.route('https://www.youtube.com/**', answer)
    return served


# ---------------------------------------------------------------- pages with YouTube's own data
# Shaped like youtube.com since 2025: the list is in ytInitialData, drawn later by the app.
# The drawn page here is empty on purpose, so only the data can be read, as in a browser
# that draws little in the background. Continuations are answered at /youtubei/v1/.
import json as _json


def d_text(value):
    return {'simpleText': value}


def d_lockup(ident, title, channel, views, age, length, kind='VIDEO'):
    rows = [{'metadataParts': [{'text': {'content': channel}}]},
            {'metadataParts': [{'text': {'content': views}}, {'text': {'content': age}}]}]
    badge = {'thumbnailOverlayBadgeViewModel': {'thumbnailBadges': [{'thumbnailBadgeViewModel': {'text': length}}]}}
    command = ({'watchEndpoint': {'videoId': ident}, 'commandMetadata': {'webCommandMetadata': {'url': '/watch?v=' + ident}}}
               if kind == 'VIDEO' else
               {'browseEndpoint': {'browseId': 'VL' + ident}, 'commandMetadata': {'webCommandMetadata': {'url': '/playlist?list=' + ident}}})
    image = ({'thumbnailViewModel': {'overlays': [badge]}} if kind == 'VIDEO' else
             {'collectionThumbnailViewModel': {'primaryThumbnail': {'thumbnailViewModel': {'overlays': [badge]}}}})
    return {'lockupViewModel': {'contentId': ident, 'contentType': 'LOCKUP_CONTENT_TYPE_' + kind, 'contentImage': image,
                                'metadata': {'lockupMetadataViewModel': {'title': {'content': title},
                                             'metadata': {'contentMetadataViewModel': {'metadataRows': rows}}}},
                                'rendererContext': {'commandContext': {'onTap': {'innertubeCommand': command}}}}}


def d_video(ident, title, channel, views, age, length):
    return {'videoRenderer': {'videoId': ident, 'title': {'runs': [{'text': title}]}, 'ownerText': {'runs': [{'text': channel}]},
                              'viewCountText': d_text(views), 'publishedTimeText': d_text(age), 'lengthText': d_text(length),
                              'navigationEndpoint': {'commandMetadata': {'webCommandMetadata': {'url': '/watch?v=' + ident}},
                                                     'watchEndpoint': {'videoId': ident}}}}


def d_channel(ident, name, handle, subscribers):
    # YouTube now puts the handle where the subscriber count was, and the count where the video count was.
    return {'channelRenderer': {'channelId': ident, 'title': d_text(name), 'subscriberCountText': d_text(handle),
                                'videoCountText': d_text(subscribers),
                                'navigationEndpoint': {'browseEndpoint': {'browseId': ident, 'canonicalBaseUrl': '/' + handle},
                                                       'commandMetadata': {'webCommandMetadata': {'url': '/' + handle}}}}}


def d_more(token):
    return {'continuationItemRenderer': {'continuationEndpoint': {'continuationCommand': {'token': token}}}}


def d_home_videos(start, count):
    return [{'richItemRenderer': {'content': d_lockup('home%07d' % n, 'Home video %d' % n, 'Channel %d' % n,
                                                    '%d views' % (n * 10), '%d day%s ago' % (n, '' if n == 1 else 's'), '%d:0%d' % (n, n % 10))}}
            for n in range(start, start + count)]


def data_home():
    contents = d_home_videos(1, 3) + [
        {'richSectionRenderer': {'content': {'richShelfRenderer': {'title': d_text('Shorts'), 'contents': [
            {'richItemRenderer': {'content': {'shortsLockupViewModel': {
                'overlayMetadata': {'primaryText': {'content': 'Dog surfs'}, 'secondaryText': {'content': '3M views'}},
                'onTap': {'innertubeCommand': {'reelWatchEndpoint': {'videoId': 'shortdata1'}}}}}}}]}}}},
        {'richItemRenderer': {'content': {'adSlotRenderer': {'fulfillmentContent': {'fulfilledLayout': {'inFeedAdLayoutRenderer': {
            'renderingContent': {'promotedVideoRenderer': {'title': d_text('Try Brand Tea'), 'longBylineText': d_text('Brand Tea')}}}}}}}}},
        d_more('home-2')]
    return {'contents': {'twoColumnBrowseResultsRenderer': {'tabs': [{'tabRenderer': {'selected': True, 'content': {
        'richGridRenderer': {'contents': contents}}}}]}}}


def data_search():
    section = [d_video('srch000001', 'Bread for beginners', 'Kitchen Basics', '1.2M views', '2 years ago', '12:01'),
               d_channel('UCbread', 'Bread Channel', '@breadchannel', '45.6K subscribers'),
               d_lockup('PLbread', 'Bread recipes', 'Kitchen Basics', '', '', '12 videos', kind='PLAYLIST'),
               d_video('srch000002', 'Sourdough starter', 'Baker', '3K views', '1 month ago', '8:15')]
    return {'contents': {'twoColumnSearchResultsRenderer': {'primaryContents': {'sectionListRenderer': {'contents': [
        {'itemSectionRenderer': {'contents': section}}, d_more('search-2')]}}}}}


def data_history():
    days = [('Today', ['hist000001', 'hist000002']), ('Yesterday', ['hist000003']), ('Monday', ['hist000004', 'hist000005'])]
    sections = [{'itemSectionRenderer': {'header': {'itemSectionHeaderRenderer': {'title': d_text(day)}},
                                         'contents': [d_lockup(v, 'Watched ' + v[-1], 'Somebody', '5 views', '', '1:00') for v in ids]}}
                for day, ids in days]
    return {'contents': {'twoColumnBrowseResultsRenderer': {'tabs': [{'tabRenderer': {'selected': True, 'content': {
        'sectionListRenderer': {'contents': sections + [d_more('hist-2')]}}}}]}}}


def data_channels():
    channels = [d_channel('UC%03d' % n, 'Channel %03d' % n, '@channel%03d' % n, '%d subscribers' % n) for n in range(1, 4)]
    return {'contents': {'twoColumnBrowseResultsRenderer': {'tabs': [{'tabRenderer': {'selected': True, 'content': {
        'sectionListRenderer': {'contents': [{'itemSectionRenderer': {'contents': [{'shelfRenderer': {'content': {
            'expandedShelfContentsRenderer': {'items': channels}}}}]}}, d_more('ch-2')]}}}}]}}}


def data_signed_out_library():
    return {'contents': {'twoColumnBrowseResultsRenderer': {'tabs': [{'tabRenderer': {'selected': True, 'content': {
        'sectionListRenderer': {'contents': [{'itemSectionRenderer': {'contents': [{'backgroundPromoRenderer': {
            'title': {'runs': [{'text': 'Keep track of what you watch'}]},
            'bodyText': {'runs': [{'text': 'Watch history isn\'t viewable when signed out. Sign in to see it.'}]}}}]}}]}}}}]}}}


def d_append(items):
    return {'onResponseReceivedActions': [{'appendContinuationItemsAction': {'continuationItems': items}}]}


# The continuation answers: token -> the next part.
PARTS = {
    'home-2': lambda: d_append(d_home_videos(4, 20) + [d_more('home-3')]),
    'home-3': lambda: d_append(d_home_videos(24, 5)),
    'search-2': lambda: {'onResponseReceivedCommands': [{'appendContinuationItemsAction': {'continuationItems': [
        {'itemSectionRenderer': {'contents': [d_video('srch000003', 'Rye bread', 'Baker', '900 views', '3 days ago', '4:44')]}}]}}]},
    'hist-2': lambda: d_append([{'itemSectionRenderer': {'header': {'itemSectionHeaderRenderer': {'title': d_text('Last week')}},
                                                       'contents': [d_lockup('hist000006', 'Watched 6', 'Somebody', '5 views', '', '1:00')]}}]),
    'ch-2': lambda: d_append([d_channel('UC%03d' % n, 'Channel %03d' % n, '@channel%03d' % n, '%d subscribers' % n)
                            for n in range(4, 54)] + [d_more('ch-3')]),
    'ch-3': lambda: d_append([d_channel('UC%03d' % n, 'Channel %03d' % n, '@channel%03d' % n, '%d subscribers' % n)
                            for n in range(54, 61)]),
}


def data_page(data, signed_in=False, title='YouTube'):
    config = {'LOGGED_IN': signed_in, 'INNERTUBE_API_KEY': 'test-key', 'INNERTUBE_CLIENT_VERSION': '2.20261029',
              'INNERTUBE_CONTEXT_CLIENT_NAME': 1, 'INNERTUBE_CONTEXT': {'client': {'clientName': 'WEB', 'clientVersion': '2.20261029'}},
              'SESSION_INDEX': 0}
    return ('<!doctype html><html lang="en"><head><title>' + title + '</title></head><body><ytd-app></ytd-app><script>'
            'window.ytcfg={d:' + _json.dumps(config) + ',get(k){return this.d[k];}};'
            'var ytInitialData=' + _json.dumps(data) + ';</script></body></html>')


def data_routes(context, signed_in=True, pages=None):
    """youtube.com with data pages. Returns the continuation requests made: (path, body, headers)."""
    from urllib.parse import urlsplit
    pages = dict({'/': data_home, '/results': data_search, '/feed/history': data_history, '/feed/channels': data_channels},
                 **(pages or {}))
    asked = []

    def answer(route):
        request = route.request
        url = urlsplit(request.url)
        if url.path.startswith('/youtubei/v1/'):
            body = _json.loads(request.post_data or '{}')
            asked.append((url.path, body, dict(request.headers)))
            part = PARTS.get(body.get('continuation'))
            if part is None:
                route.fulfill(status=400, content_type='application/json', body='{}')
            else:
                route.fulfill(status=200, content_type='application/json', body=_json.dumps(part()))
            return
        make = pages.get(url.path) or data_home
        route.fulfill(status=200, content_type='text/html; charset=utf-8', body=data_page(make(), signed_in))
    context.route('https://www.youtube.com/**', answer)
    return asked
