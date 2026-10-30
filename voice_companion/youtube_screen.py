"""The YouTube screen: the real youtube.com, as lists, keys and voice commands.

Voice Companion drives youtube.com in its guided browser (embedded Edge or
Chrome, or your own browser linked with the extension) and shows it as a
simple screen, like Radio and Podcasts. The page is read and driven only by
youtube_page.py.

Opening YouTube lands on the YouTube menu (Search, Home, Subscriptions,
History, Watch later, Liked videos, My playlists, Sign in or Sign out), like
the app's other menus: arrows, Enter, first letters, and Escape back to the
Media menu (Will, October 2026). Escape from a list comes back to this menu.

Search asks for words in the command box. Typed words search at once; spoken
words are said back and wait for "confirm" or Enter, as a new document's name
does. Filters come from the results page itself when it lists them (YouTube
combines them in its own links), else from youtube_page.FILTERS.

Lists (Home, search results, subscriptions, history, Watch later, liked
videos, playlists, Up next, comments, chapters) use Up and Down, Home and
End, and Enter. Single letters control the video, as on YouTube itself
(K, J, L, M, C, F). Alt opens the YouTube actions menu with every action.

Videos play in a tab of their own, so the lists can still be browsed while a
video plays. If the browser cannot open a second tab, the video and the lists
share one tab, and opening a list stops the video; you hear that when it
happens.

Signing in is done by the user on Google's own page. Google does not allow
signing in inside embedded Edge, so account actions there offer to switch the
guided browser to Chrome. Nothing here types or sees a password.

companion.py calls into this module from a few small hooks. `m` is the
companion module.
"""
import re
import time
from pathlib import Path

import youtube_page as yp
from menu_navigation import announce_item, spoken_key
from youtube_page import YouTubeError

MODE = 'youtube'
SCREEN = None

NOT_HERE = ('help', 'settings', 'task_input', 'document_name', 'search', 'chrome_setup', 'sleep', 'tutorial', 'exit_confirm',
            'power_confirm', 'document_save', 'document_replace', 'update_offer', 'update_download', 'draft_leave')
OPEN = ('youtube', 'open youtube', 'go to youtube', 'start youtube', 'watch youtube', 'youtube app', 'youtube screen',
        'back to youtube', 'return to youtube', 'show youtube')
# Phrases that open the screen on a web page, where "YouTube" alone may be a link's name.
OPEN_ON_WEB = ('youtube screen', 'back to youtube', 'return to youtube', 'youtube app')
SEARCH = re.compile(r'(?:search youtube for|search youtube|youtube search for|youtube search|search on youtube for|'
                    r'find on youtube|look up on youtube) (.+)|(?:find|search for|look up|play) (.+?) on youtube')
YES = ('yes', 'yes please', 'okay', 'ok', 'confirm', 'sure', 'go ahead', 'do it')
NO = ('no', 'no thanks', 'cancel', 'escape', 'go back', 'back', 'not now', 'never mind')
PASS_THROUGH = ('help', 'options', 'open options', 'exit companion', 'go to sleep',
                'wake up', 'user guide', 'open user guide', 'manual', 'open manual', 'release notes', 'what mode',
                'what version', 'toggle audio', 'media', 'media menu', 'open media', 'radio', 'podcasts', 'media player')
HELP = ('YouTube. The YouTube menu has Search, Home, and your own lists. Up and Down Arrow move. Enter chooses. '
        'Control+F searches: type or say your words, then press Enter. Control+Shift+F changes the filters. '
        'On a list, Enter plays a video. Space or K plays and pauses. Left and Right Arrow, or J and L, skip back and forward. '
        'Alt opens the actions menu. Escape goes back.')

# Keys on the YouTube screen, as spoken commands (input_parity.SCREEN_KEYS uses this).
KEYS = {
    'F1': 'help with youtube', 'Ctrl+F': 'search youtube', 'Ctrl+Shift+F': 'search filters', 'F5': 'refresh youtube',
    'Space': 'play or pause video', 'K': 'play or pause video', 'J': 'skip back', 'L': 'skip forward',
    '0': 'restart video', 'M': 'mute or unmute', 'Ctrl+Up': 'video volume up', 'Ctrl+Down': 'video volume down',
    'comma': 'slower video', 'period': 'faster video', 'C': 'captions on or off', 'R': 'read caption', 'Q': 'video quality',
    'N': 'next video', 'P': 'previous video', 'A': 'autoplay on or off', 'F': 'video full screen',
    'H': 'list chapters', 'Ctrl+Left': 'previous chapter', 'Ctrl+Right': 'next chapter', 'S': 'skip ad',
    'I': 'video details', 'D': 'read description', 'O': 'read comments', 'G': 'go to channel', 'X': 'up next',
    'T': 'like video', 'U': 'subscribe or unsubscribe', 'W': 'save video',
}
KEYS.update({str(n): 'jump to ' + str(n * 10) + ' percent' for n in range(1, 10)})

HOME_EMPTY = ('YouTube shows no Home videos yet. It picks them from what you watch. '
              'Press Control+F to search, or say search for, and your words, such as search for jazz.')
# The YouTube menu: (name, key, action). '@account' is Sign in or Sign out.
# Lists read whole when they open (every subscribed channel); others load part by part.
COMPLETE_LISTS = ('channels',)
COMPLETE_LIMIT = 3000
PAGE = 10          # Page Down and Page Up move this many items
PREFETCH = 5       # the next part is fetched when this few items are left
MORE_WAITING = ', and more load as you move down. '
# Subscriptions is every channel you subscribe to (Will, October 29, 2026); their newest
# videos are their own item.
MENU = (('Search', 'Ctrl+F', 'search prompt'), ('Home', '', 'home'), ('Subscriptions', '', 'channels'),
        ('New from subscriptions', '', 'subscriptions'), ('History', '', 'history'), ('Watch later', '', 'watch_later'), ('Liked videos', '', 'liked'),
        ('My playlists', '', 'playlists'), ('@account', '', '@account'))
MENU_TITLE = 'YouTube menu'
MENU_KINDS = ('menu', 'filters', 'choices')   # lists where letters move to the next name
SEARCH_PROMPT = 'Search YouTube. Say or type what to search for, then press Enter or say confirm. Escape cancels.'
SEARCH_LABEL = 'YouTube search; type, then press Enter. Escape cancels:'
SEARCH_FIRST = 'Say or type what to search for first. Escape cancels.'
CONFIRM = ('confirm', 'confirm that', 'okay', 'ok', 'yes', 'yes please', 'search now', 'go ahead')
CANCEL = ('cancel', 'cancel search', 'escape', 'never mind', 'go back', 'back')
# "Search YouTube" with no words: open YouTube and ask for them.
ASK = ('search youtube', 'youtube search', 'search on youtube', 'search in youtube')
FILTER_KEY_HINT = ' Press Control+Shift+F for filters.'
DEFAULT_CHOICE = {'upload date': 'Any time', 'type': 'Any type', 'length': 'Any length', 'features': 'No features',
                  'sort': 'Relevance'}
UNEXPECTED = ('YouTube did not answer as expected, so that could not be done. '
              'Try again, or say open in browser to use the page itself.')
NO_VIDEO = 'No video is playing. Choose a video and press Enter.'
# "What's playing?" and F3 on this screen when nothing at all plays; not the radio hint.
NOTHING_PLAYING = 'Nothing is playing. Choose a video and press Enter to play it.'
SIGN_IN_NEEDED = ('You are not signed in to YouTube. To {action}, say sign in. '
                  'You type your own email and password on Google\'s page.')
EMBEDDED_EDGE = ('Google does not allow signing in inside embedded Edge, so YouTube is signed out here. '
                 'To {action}, switch to Chrome, where you can sign in. Web pages will also open in Chrome. '
                 'Switch to Chrome now? Say Yes or No.')
ACTIONS = {'like': 'like videos', 'subscribe': 'subscribe', 'unsubscribe': 'unsubscribe', 'save': 'save videos',
           'comment': 'post a comment', 'sign in': 'use your account', 'library': 'see your library'}


def screen(m):
    global SCREEN
    if SCREEN is None or SCREEN.m is not m:
        SCREEN = YouTubeScreen(m)
    return SCREEN


def normalized(text):
    words = re.sub(r"[^\w\s'.]", ' ', str(text).casefold())
    words = ' '.join(re.sub(r'(?<!\d)\.|\.(?!\d)', ' ', words).split())   # 1.5 keeps its point
    return re.sub(r'^(?:please |hey |ok |okay )|(?: please| now)$', '', words).replace("'", '')


def spoken_seconds(text):
    """Seconds in "30 seconds", "1 minute", "ninety seconds", "2 minutes 30 seconds"; None if not a time."""
    from document_editor import spoken_number
    words = normalized(text)
    words = re.sub(r'\b(a|an|one)\b (minute|second|hour)', r'1 \2', words)
    words = words.replace('half a minute', '30 seconds').replace('half an hour', '30 minutes')
    total, found = 0, False
    for amount, unit in re.findall(r'([\w.-]+(?: [\w-]+)?) (hours?|minutes?|seconds?|secs?|mins?)\b', words):
        number = None
        for candidate in (amount, amount.split()[-1]):
            try:
                number = float(candidate)
            except ValueError:
                try:
                    number = float(spoken_number(candidate))
                except (TypeError, ValueError):
                    number = None
            if number is not None:
                break
        if number is None:
            continue
        total += number * {'h': 3600, 'm': 60, 's': 1}[unit[0]]
        found = True
    if not found:
        try:
            return float(words) if words else None
        except ValueError:
            return None
    return total


class YouTubeScreen:
    def __init__(self, m):
        self.m = m
        self.views = []           # the stack of lists; Escape goes back one
        self.pending = None       # ('like'|'subscribe'|..., data): a Yes or No question
        self.choice = 'no'
        self.video = None         # the video loaded in the player: id, title, channel, url, live
        self.played = []          # videos played this session, for Previous video
        self.sp = ''              # the search filter code in use ('' for none); see youtube_page.filter_code
        self.page_groups = []     # the filters the last results page listed (youtube_page.page_filter_groups)
        self.query = ''
        self.asking = False       # the search box is open: typed and spoken words are search words
        self.spoken = None        # spoken search words that wait for "confirm" or Enter
        self.entry = None         # (stamp, text) for the command box: the window shows text there once
        self.hinted = False       # the filter key was said after a search
        # True or False once a page said so; remembered from the last time, until a page says again.
        self._signed_in = self.saved_sign_in()
        self.skip = 10
        self.list_page = None
        self.video_page = None
        self.owner = False        # True while YouTube, not radio or a podcast, is what plays
        self.hub_session = None
        self.last = None          # the player state at the last check, for news
        self.next_check = 0.0
        self.single_tab = False
        self.return_mode = 'media_menu'
        self.signing_in = False   # Google's sign-in page is open; coming back loads YouTube again
        self.opened_window = False  # a browser window or tab opened; the keyboard comes back to Companion
        self.lists = yp.YouTubePage(lambda: self.list_page)
        self.player = yp.YouTubePage(lambda: self.video_page)

    # ------------------------------------------------------------ signed in or not
    ACCOUNT_FILE = 'youtube-account.json'

    def saved_sign_in(self):
        try:
            import json
            value = json.loads((Path(self.m.APP) / self.ACCOUNT_FILE).read_text(encoding='utf-8')).get('signed_in')
        except Exception:
            return None
        return value if isinstance(value, bool) else None

    @property
    def signed_in(self):
        return self._signed_in

    @signed_in.setter
    def signed_in(self, value):
        value = value if isinstance(value, bool) else None
        changed = value != self._signed_in
        self._signed_in = value
        if value is None or not changed:
            return
        try:
            import json
            from storage import write_text_atomic
            write_text_atomic(Path(self.m.APP) / self.ACCOUNT_FILE, json.dumps({'signed_in': value}))
        except Exception:
            pass   # known again from the next page

    def check_signed_in(self):
        """Ask the page whether you are signed in. Loads Home first when no YouTube page is open."""
        page = self.list_page if self.alive(self.list_page) else self.video_page if self.alive(self.video_page) else None
        try:
            if page is None or not yp.is_youtube(page.url):
                self.go(yp.feed_url('home'))
                page = self.list_page
            signed = yp.YouTubePage(lambda: page).signed_in()
        except YouTubeError:
            return self.signed_in
        if signed is not None:
            self.signed_in = signed
        return self.signed_in

    # ------------------------------------------------------------ the browser
    def web(self):
        m = self.m
        if m.web_session is None:
            WebSession = getattr(m, 'WebSession', None) or __import__('web_assistant').WebSession
            m.web_session = WebSession(m.APP, progress=m.speak_status)
            m.web_session.auto_read = m.PREFERENCES.get('web_auto_read', True)
            m.web_session.search_engine = m.PREFERENCES.get('web_search_engine', 'google')
        return m.web_session

    def engine(self):
        """'webview2', 'chrome' or 'regular' (your own browser through the extension)."""
        web = self.web()
        if getattr(web.backend, 'regular', False):
            return 'regular'
        return web.running_browser()

    @staticmethod
    def alive(page):
        try:
            return page is not None and not page.is_closed()
        except Exception:
            return False

    def go(self, url):
        """Load a list address in the list tab. Returns a notice to say first ('' usually)."""
        from web_assistant import WebError
        web = self.web()
        notice = ''
        if not self.alive(self.list_page):
            if self.single_tab and self.alive(self.video_page):
                self.list_page = self.video_page
            elif self.alive(self.video_page):
                # The list tab was closed but the video plays on: a new tab for the lists.
                try:
                    self.list_page = self.video_page.context.new_page()
                    self.list_page.set_default_timeout(10000)
                except Exception:
                    self.list_page = self.video_page
                    self.single_tab = True
            else:
                try:
                    if not self.injected(web):
                        web.adopt_connected_browser()
                except Exception:
                    pass
                try:
                    # The first page goes through the web session, so a browser that cannot
                    # start is replaced by the other guided browser, and you are told.
                    notice = web._navigate(url)
                except WebError as exc:
                    raise YouTubeError(str(exc)) from exc
                except Exception as exc:
                    raise YouTubeError(yp.NOT_LOADED) from exc
                self.list_page = web.backend.page
                self.opened_window = True
                web.snapshot = None   # the web screen reads the page again when you open it
                self.lists.wait_ready()
                return notice
        if self.single_tab and self.list_page is self.video_page and self.video and self.owner:
            notice = 'The video stopped, because this browser shows one page at a time. '
            self.stopped()
        try:
            self.list_page.goto(url, wait_until='domcontentloaded', timeout=30000)
        except Exception as exc:
            if not self.alive(self.list_page):
                self.list_page = None
                return self.go(url)
            raise YouTubeError(yp.NOT_LOADED) from exc
        web.snapshot = None
        self.lists.wait_ready()
        return notice

    @staticmethod
    def injected(web):
        return bool(getattr(web, 'injected_backend', False))

    def open_video_page(self, url):
        """Load a video in the video tab, opening that tab first when needed."""
        if not self.alive(self.video_page):
            self.video_page = None
            if not self.alive(self.list_page):
                # The browser was closed (or never opened): open the list tab first, then the video's own tab.
                view = self.view()
                self.go(view['url'] if view and view.get('url') else yp.feed_url('home'))
            try:
                self.video_page = self.list_page.context.new_page()
                self.video_page.set_default_timeout(10000)
                self.single_tab = False
                self.opened_window = True
            except Exception:
                self.video_page = self.list_page
                self.single_tab = True
        try:
            self.video_page.goto(url, wait_until='domcontentloaded', timeout=30000)
        except Exception as exc:
            raise YouTubeError(yp.NOT_LOADED) from exc
        self.web().snapshot = None
        self.player.wait_for_player()

    # ------------------------------------------------------------ lists
    def view(self):
        return self.views[-1] if self.views else None

    def labels(self, view=None):
        view = view or self.view()
        if not view:
            return []
        return [self.label(view, item) for item in view['items']]

    def label(self, view, item):
        kind = view['kind']
        if kind == 'list':
            return yp.spoken_item(item)
        if kind == 'menu':
            if item[0] == '@account':
                return {True: 'Sign out', False: 'Sign in'}.get(self.signed_in, 'Sign in or sign out')
            return item[0]
        if kind == 'comments':
            return yp.spoken_comment(item)
        if kind == 'chapters':
            return item['title'] + '. ' + yp.spoken_length(item['time'])
        if kind == 'filters':
            if item == 'clear':
                return 'Clear filters'
            return item['title'] + ': ' + ', '.join(c['label'] for c in item['choices'] if c['on'])
        if kind == 'choices':
            return item['label'] + (', on' if item['on'] else '') + (', not offered with the filters that are on' if item['disabled'] else '')
        if kind == 'quality':
            return yp.quality_name(item) + (', current' if item == view.get('current') else '')
        if kind == 'save':
            return item['label'] + (', saved here' if item['checked'] else ', not saved here')
        return str(item)

    def describe(self, lead=''):
        view = self.view()
        if not view:
            return lead + 'No list is open. Press Control+F to search, or Escape for the YouTube menu.'
        items = view['items']
        if not items:
            return lead + (view.get('empty') or 'This list is empty.')
        index = max(0, min(view['index'], len(items) - 1))
        view['index'] = index
        said = lead + announce_item(self.label(view, items[index]), index, len(items), hotkey=view['kind'] in MENU_KINDS)
        if view['kind'] == 'menu' and items[index][1]:
            said += ' ' + spoken_key(items[index][1]) + '.'
        return said

    def push(self, view, lead=''):
        """Show a list. A list of the same kind and name replaces the one on top."""
        top = self.view()
        if top and top['kind'] == view['kind'] and top.get('name') == view.get('name') and view['kind'] != 'list':
            self.views.pop()
        if top and top['kind'] == 'list' and view['kind'] == 'list' and top.get('name') == view.get('name'):
            self.views.pop()
        self.views.append(dict({'index': 0, 'items': [], 'empty': 'This list is empty.'}, **view))
        count = len(view.get('items') or [])
        intro = view['title'] + '. '
        if view['kind'] == 'list' and count:
            intro += str(count) + (' item' if count == 1 else ' items') + (MORE_WAITING if view.get('token') else '. ')
        return self.describe(lead + intro)

    # ------------------------------------------------------------ the YouTube menu
    def ensure_menu(self):
        if not self.views or self.views[0]['kind'] != 'menu':
            self.views.insert(0, {'kind': 'menu', 'name': 'menu', 'title': MENU_TITLE, 'items': list(MENU), 'index': 0,
                                  'empty': ''})

    def to_menu(self):
        """Close every list over the YouTube menu, before Home, a library list or new results open."""
        self.ensure_menu()
        del self.views[1:]

    def show_menu(self, lead=''):
        self.to_menu()
        if self.alive(self.list_page):
            try:
                signed = self.lists.signed_in()   # Sign in or Sign out, as the page says now
                if signed is not None:
                    self.signed_in = signed
            except Exception:
                pass
        return self.describe(lead + MENU_TITLE + '. ')

    def menu_choice(self, item):
        action = item[2]
        if action == 'search prompt':
            return self.ask_search()
        if action == '@account':
            if self.engine() != 'webview2':
                self.check_signed_in()   # what the page says now, not what was known before
            return self.sign_out() if self.signed_in else self.sign_in()
        self.m.speak_status('Loading ' + yp.FEEDS[action][0] + '.')
        return self.load(action)

    def note_signed(self, facts):
        if isinstance(facts, dict) and facts.get('signedIn') is not None:
            self.signed_in = bool(facts['signedIn'])

    def load(self, name, lead=''):
        """Open Home, a library list or search results."""
        if name in yp.ACCOUNT_FEEDS and self.engine() == 'webview2':
            return self.ask_switch('library')
        title, _ = yp.FEEDS[name]
        notice = self.go(yp.feed_url(name))
        items, facts = self.lists.read_list()
        self.note_signed(facts)
        view = {'kind': 'list', 'name': name, 'title': title, 'url': yp.feed_url(name), 'items': items,
                'token': facts.get('token') or ''}
        if not items:
            if facts.get('promoSignIn') or name in yp.ACCOUNT_FEEDS and facts.get('signedIn') is False:
                return notice + SIGN_IN_NEEDED.format(action='see ' + title.lower())
            if name == 'home' and (facts.get('empty') or facts.get('promo')):
                # Signed out with no watch history, YouTube shows no Home videos, only a hint to search.
                view['empty'] = HOME_EMPTY
            elif facts.get('empty') and name != 'home':
                view['empty'] = title + ' is empty.'
            else:
                return notice + yp.CHANGED
        if name in COMPLETE_LISTS:
            # Your subscribed channels: every one, so the list is whole and first letters find any channel.
            while view['token'] and len(view['items']) < COMPLETE_LIMIT and self.more(view):
                pass
        self.to_menu()
        return self.push(view, notice + lead)

    # ------------------------------------------------------------ search
    def set_entry(self, text):
        self.entry = (time.monotonic_ns(), text)

    def ask_search(self):
        """Control+F, Search in the menu, or "Search YouTube": the command box takes the words."""
        self.asking, self.spoken = True, None
        self.set_entry('')
        return SEARCH_PROMPT

    def end_asking(self):
        self.asking, self.spoken = False, None
        self.set_entry('')

    def search_answer(self, raw, command, typed):
        """Words while the search box is open. Typed words search at once. Spoken words are said
        back and wait, so a misheard word never searches by itself; "confirm" or Enter searches."""
        if raw == '@youtube:search:cancel' or command in CANCEL:
            self.end_asking()
            view = self.view()
            if not view or view['kind'] == 'menu':
                return self.show_menu('Search canceled. ')
            return self.describe('Search canceled. ' + view['title'] + '. ')
        if raw == '@youtube:search:enter' or not typed and command in CONFIRM:
            if not self.spoken:
                return SEARCH_FIRST
            words = self.spoken
            self.end_asking()
            self.m.speak_status('Searching YouTube.')
            return self.search(words)
        words = ' '.join(raw.split())
        if not typed:
            words = re.sub(r'^(?:search youtube for|search for)\s+', '', words, flags=re.I).strip().rstrip('.!?,;:').strip()
        if not words:
            return SEARCH_FIRST
        if typed:
            self.end_asking()
            self.m.speak_status('Searching YouTube.')
            return self.search(words)
        self.spoken = words
        self.set_entry(words)
        return words + '. Press Enter or say confirm to search.'

    def search(self, query=None):
        query = ' '.join(str(self.query if query is None else query).split()).strip(' .')
        if not query:
            return self.ask_search()
        self.query = query
        url = yp.search_url(query, code=self.sp)
        notice = self.go(url)
        items, facts = self.lists.read_list()
        self.note_signed(facts)
        try:
            self.page_groups = self.lists.read_filters()
        except YouTubeError:
            self.page_groups = []
        summary = self.filter_summary()
        if not items:
            if facts.get('empty'):
                if summary:
                    return notice + ('No results for ' + query + ' with these filters: ' + summary +
                                     '. Say clear filters, or press Control+Shift+F to change them.')
                return notice + 'No results for ' + query + '. Try other words.'
            return notice + yp.CHANGED
        # New results replace any list, so Escape goes back to the YouTube menu.
        self.to_menu()
        title = 'Results for ' + query + ('. Filters: ' + summary if summary else '')
        said = self.push({'kind': 'list', 'name': 'search', 'title': title, 'url': url, 'items': items,
                          'token': facts.get('token') or '', 'search': True}, notice)
        if not self.hinted:
            self.hinted = True
            said += FILTER_KEY_HINT
        return said

    # ------------------------------------------------------------ search filters
    def filter_groups(self):
        """The filter groups to offer: the ones the results page listed, or the screen's own."""
        if self.page_groups:
            known = [g for name in yp.FILTER_ORDER for g in self.page_groups if g['name'] == name]
            groups = known + [g for g in self.page_groups if g not in known]
            shaped = []
            for group in groups:
                choices = [dict(c) for c in group['choices']]
                default = DEFAULT_CHOICE.get(group['name'], 'Any')
                if not any(c['label'].casefold() == default.casefold() for c in choices):
                    # YouTube takes a filter away with the link of the filter that is on.
                    on = next((c for c in choices if c['on']), None)
                    choices.insert(0, {'label': default, 'sp': on['sp'] if on else self.sp, 'on': on is None, 'disabled': False})
                elif not any(c['on'] for c in choices):
                    for c in choices:
                        c['on'] = c['label'].casefold() == default.casefold()
                title = yp.FILTERS[group['name']][0] if group['name'] in yp.FILTERS else group['title']
                shaped.append({'name': group['name'], 'title': title, 'choices': choices})
            return shaped
        filters = yp.decode_filters(self.sp)
        return [{'name': name, 'title': yp.FILTERS[name][0],
                 'choices': [{'label': label, 'key': key, 'on': filters[name] == key, 'disabled': False}
                             for key, label, _ in yp.FILTERS[name][1]]} for name in yp.FILTER_ORDER]

    def filter_summary(self):
        """'This week, Videos, sorted by popularity': the filters that are on, or ''."""
        if not self.page_groups:
            return yp.filter_summary(yp.decode_filters(self.sp))
        parts = []
        for group in self.filter_groups():
            for choice in group['choices']:
                if not choice['on'] or choice['label'].casefold() == DEFAULT_CHOICE.get(group['name'], 'Any').casefold():
                    continue
                parts.append('sorted by ' + choice['label'].lower() if group['name'] == 'sort' else choice['label'])
        text = ', '.join(parts)
        return text[:1].upper() + text[1:]

    def open_filters(self):
        if not self.query:
            return 'Search for something first. Then press Control+Shift+F for filters.'
        summary = self.filter_summary()
        self.push({'kind': 'filters', 'name': 'filters', 'title': 'Filters', 'items': self.filter_groups() + ['clear']})
        return self.describe('Filters. ' + ('On now: ' + summary + '. ' if summary else 'No filters are on. '))

    def open_choices(self, group):
        choices = group['choices']
        index = next((i for i, c in enumerate(choices) if c['on']), 0)
        self.push({'kind': 'choices', 'name': 'choices', 'title': group['title'], 'items': choices, 'index': index,
                   'group': group['name']})
        return self.describe(group['title'] + '. ')

    def choose_filter(self, group_name, choice):
        if choice['disabled']:
            return (choice['label'] + ' is not offered with the filters that are on. '
                    'Choose another, or say clear filters.')
        if choice['on']:
            return choice['label'] + ' is already chosen.'
        if 'sp' in choice:
            self.sp = choice['sp'] or ''
        else:
            filters = yp.decode_filters(self.sp)
            filters[group_name] = choice['key']
            self.sp = yp.filter_code(filters)
        if not self.query:
            label = yp.FILTERS[group_name][0] if group_name in yp.FILTERS else group_name
            return label + ': ' + choice['label'] + '. Now press Control+F, or say search for, and your words.'
        self.m.speak_status('Searching YouTube.')
        said = self.search()
        self.refresh_filter_lists()
        return said

    def refresh_filter_lists(self):
        """After a search that found nothing, the filter lists stay open: show them as they are now,
        so the next choice combines with the filters that are on, not the ones before."""
        groups = None
        for view in self.views:
            if view['kind'] not in ('filters', 'choices'):
                continue
            groups = groups if groups is not None else self.filter_groups()
            if view['kind'] == 'filters':
                view['items'] = groups + ['clear']
            else:
                group = next((g for g in groups if g['name'] == view.get('group')), None)
                if group is not None:
                    view['items'] = group['choices']

    def spoken_filter(self, name, key):
        """A filter said aloud, such as "This week": the page's own link when it lists one."""
        if name == 'gone':
            return yp.GONE[key]
        label = yp.filter_label(name, key)
        for group in self.filter_groups() if self.page_groups else []:
            if group['name'] == name:
                choice = next((c for c in group['choices'] if c['label'].casefold() == label.casefold()), None)
                if choice is not None:
                    return self.choose_filter(name, choice)
        return self.choose_filter(name, {'label': label, 'key': key, 'on': yp.decode_filters(self.sp)[name] == key,
                                         'disabled': False})

    def clear_filters(self):
        self.sp = ''
        if not self.query:
            return 'Filters cleared.'
        self.m.speak_status('Searching YouTube.')
        return self.search()

    def move(self, step=None, to=None):
        view = self.view()
        if not view or not view['items']:
            return self.describe()
        old = view['index']
        if to is not None:
            if to == 'last' and view['kind'] == 'list' and view.get('token') and old == len(view['items']) - 1:
                return self.move(1)   # End again at the end of what is loaded: the next part
            view['index'] = 0 if to == 'first' else len(view['items']) - 1
            if view['index'] == old:
                return ('Start of list. ' if to == 'first' else 'End of list. ') + self.describe()
            return self.describe()
        target = old + step
        loaded = False
        if target >= len(view['items']) and view['kind'] == 'list' and view.get('url'):
            loaded = self.more(view)   # said by the new count: "6 of 26"
        last = len(view['items']) - 1
        if target < 0:
            if old == 0:
                return 'Start of list. ' + self.describe()
            target = 0
        elif target > last:
            if old == last:
                return 'End of list. ' + self.describe()
            target = last
        view['index'] = target
        # Near the end of a long list, the next part is fetched before it is needed.
        if not loaded and view['kind'] == 'list' and view.get('token') and last - target < PREFETCH:
            self.more(view)
        return self.describe()

    def page_move(self, direction):
        """Page Down and Page Up: ten items at a time. At the end of what is loaded, the next part loads."""
        return self.move(PAGE * direction)

    def more(self, view):
        """At the end of a long list, ask YouTube for its next part. True when more came."""
        if view.get('token'):
            page = self.list_page if self.alive(self.list_page) else self.video_page if self.alive(self.video_page) else None
            if page is None or not yp.is_youtube(getattr(page, 'url', '')):
                return False
            reader = yp.YouTubePage(lambda: page)
            known = {yp.item_key(i) for i in view['items']}
            for _ in range(3):   # a part may hold only items already listed (or only ads)
                try:
                    items, token = reader.next_part(view['token'], view.get('search'))
                except YouTubeError:
                    return False
                view['token'] = token
                added = [i for i in items if yp.item_key(i) not in known]
                if added:
                    view['items'].extend(added)
                    return True
                if not token:
                    return False
            return False
        page = self.list_page
        if not self.alive(page) or not yp.same_address(page.url, view['url']):
            return False   # the tab shows another list now; its videos must not join this one
        try:
            if not self.lists.load_more():
                return False
            items, _ = self.lists.read_list()
        except YouTubeError:
            return False
        known = {yp.item_key(i) for i in view['items']}
        added = [i for i in items if yp.item_key(i) not in known]
        view['items'].extend(added)
        return bool(added)

    def current(self):
        view = self.view()
        if not view or not view['items']:
            return None
        return view['items'][max(0, min(view['index'], len(view['items']) - 1))]

    def enter(self):
        view = self.view()
        item = self.current()
        if item is None:
            return self.describe()
        kind = view['kind']
        if kind == 'menu':
            return self.menu_choice(item)
        if kind == 'filters':
            if item == 'clear':
                return self.clear_filters() if self.sp else 'No filters are on. ' + self.describe()
            return self.open_choices(item)
        if kind == 'choices':
            return self.choose_filter(view['group'], item)
        if kind == 'chapters':
            return self.seek_to(item['time'], item['title'] + '.')
        if kind == 'quality':
            return self.set_quality(item)
        if kind == 'save':
            verb = 'Remove this video from ' if item['checked'] else 'Save this video to '
            return self.ask(('save', item), verb + item['label'] + '? Say Yes or No.')
        if kind == 'comments':
            return self.describe()
        if item['kind'] == 'channel':
            return self.open_list(item['url'].rstrip('/') + '/videos', 'Channel: ' + item['title'], 'channel')
        if item['kind'] == 'playlist':
            url = yp.BASE + '/playlist?list=' + item['list'] if item.get('list') else item['url']
            return self.open_list(url, 'Playlist: ' + item['title'], 'playlist')
        if item['kind'] == 'ad':
            return 'This is an ad. Ads open in the web browser. Say open in browser to see it.'
        return self.play(item)

    def open_list(self, url, title, name):
        notice = self.go(url)
        items, facts = self.lists.read_list()
        if not items:
            return notice + (title + ' has no videos to show.' if facts.get('empty') else yp.CHANGED)
        return self.push({'kind': 'list', 'name': name, 'title': title, 'url': url, 'items': items,
                          'token': facts.get('token') or ''}, notice)

    # ------------------------------------------------------------ playing
    def play(self, item, lead=''):
        if not item.get('id'):
            return 'This item cannot be played. Say open in browser to use the page itself.'
        self.pause_other_media()
        # Shorts play in the normal player too, so every key works the same.
        self.open_video_page(yp.watch_url(item['id']))
        self.video = {'id': item['id'], 'title': item['title'], 'channel': item.get('channel', ''), 'live': item.get('live')}
        if not self.played or self.played[-1]['id'] != item['id']:
            self.played.append(dict(self.video))
        self.owner = True
        try:
            state = self.player.state()
            if state.get('error'):
                return lead + 'YouTube says: ' + state['error'].rstrip('.') + '.'
            if not state.get('ad', {}).get('showing') and state.get('paused') is not False:
                self.player.play()
                state = self.player.state()
        except YouTubeError as exc:
            return lead + str(exc)
        self.last = state
        self.video['title'] = state.get('title') or self.video['title']
        self.video['channel'] = state.get('channel') or self.video['channel']
        said = lead + 'Playing ' + self.video['title'] + '.'
        if state.get('ad', {}).get('showing'):
            said += ' ' + self.ad_words(state, first=True)
        elif state.get('paused'):
            said = lead + self.video['title'] + ' is ready, but did not start. Press Space to play it.'
        return said + self.other_media_notice()

    def pause_other_media(self):
        """Radio, podcasts and media files pause when a video starts."""
        hub = getattr(self.m, 'MEDIA_HUB', None)
        self.paused_other = None
        if hub is None:
            return
        player = hub.player
        try:
            kind = player.kind
            if kind and not player.status().get('paused'):
                player.control('pause')
                self.paused_other = kind
        except Exception:
            pass
        self.hub_session = getattr(player, 'session', None)

    def other_media_notice(self):
        kind = getattr(self, 'paused_other', None)
        self.paused_other = None
        return {'radio': ' The radio is paused.', 'podcast': ' The podcast is paused.',
                'local': ' The media file is paused.'}.get(kind, '')

    def has_video(self):
        return self.video is not None and self.alive(self.video_page)

    def need_video(self):
        if not self.has_video():
            if self.video is not None:
                self.stopped()
            return NO_VIDEO
        return None

    def stopped(self):
        self.owner = False
        self.last = None
        self.video = None

    def ad_words(self, state, first=False):
        ad = state.get('ad') or {}
        text = (ad.get('text') or '').strip()
        said = 'An ad is playing' + (' first' if first else '') + '.'
        if re.search(r'\bAd \d+ of \d+', text):
            said += ' ' + re.search(r'Ad \d+ of \d+', text)[0] + '.'
        if ad.get('skippable'):
            said += ' Skip is ready. Press S to skip it.'
        return said

    def toggle(self):
        problem = self.need_video()
        if problem:
            return problem
        state = self.player.state()
        if state.get('paused'):
            self.pause_other_media()
            self.owner = True
            return ('Playing.' if self.player.play() else 'The video did not start. Press Space to try again.') + self.other_media_notice()
        self.player.pause()
        return 'Paused.'

    def set_playing(self, playing):
        problem = self.need_video()
        if problem:
            return problem
        state = self.player.state()
        if bool(state.get('paused')) != playing:
            return 'Already playing.' if playing else 'Already paused.'
        return self.toggle()

    def position_words(self, state):
        time_, duration = state.get('time'), state.get('duration')
        if time_ is None:
            return ''
        if state.get('live') or not duration:
            return yp.spoken_length(time_)
        return yp.spoken_length(time_) + ' of ' + yp.spoken_length(duration)

    def skip_by(self, seconds):
        problem = self.need_video()
        if problem:
            return problem
        state = self.player.state()
        if state.get('ad', {}).get('showing'):
            return 'An ad is playing. You cannot move in an ad.' + (' Press S to skip it.' if state['ad'].get('skippable') else '')
        now = state.get('time') or 0
        duration = state.get('duration')
        target = max(0, now + seconds)
        if duration and not state.get('live'):
            target = min(duration, target)
        state = self.player.seek(target)
        jump = ('Back ' if seconds < 0 else 'Forward ') + yp.spoken_length(abs(seconds)) + '.'
        if seconds < 0 and target == 0:
            jump = 'Start of the video.'
        elif seconds > 0 and duration and target >= duration:
            jump = 'End of the video.'
        where = self.position_words(state)
        return jump + (' ' + where + '.' if where and not jump.startswith(('Start', 'End')) else '')

    def seek_to(self, seconds, lead=''):
        problem = self.need_video()
        if problem:
            return problem
        state = self.player.seek(seconds)
        return lead + (' ' if lead else '') + 'Now at ' + self.position_words(state) + '.'

    def jump_percent(self, percent):
        problem = self.need_video()
        if problem:
            return problem
        state = self.player.state()
        if state.get('live') or not state.get('duration'):
            return 'This video has no set length, so you cannot jump to a part of it.'
        return self.seek_to(state['duration'] * percent / 100, str(percent) + ' percent.')

    def restart(self):
        problem = self.need_video()
        if problem:
            return problem
        self.player.seek(0)
        return 'Start of the video.'

    def speed(self, request):
        problem = self.need_video()
        if problem:
            return problem
        state = self.player.state()
        current = state.get('rate') or 1
        rates = self.player._player_call('getAvailablePlaybackRates').get('value') or [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2]
        rates = sorted(float(r) for r in rates if isinstance(r, (int, float)))
        if request in ('faster', 'slower'):
            choices = [r for r in rates if (r > current + 0.01 if request == 'faster' else r < current - 0.01)]
            if not choices:
                return ('Fastest speed. ' if request == 'faster' else 'Slowest speed. ') + self.speed_words(current)
            target = choices[0] if request == 'faster' else choices[-1]
        else:
            target = request
        rate, _ = self.player.set_rate(target)
        return self.speed_words(rate)

    @staticmethod
    def speed_words(rate):
        rate = float(rate or 1)
        return 'Normal speed.' if abs(rate - 1) < 0.01 else 'Speed ' + ('%g' % rate) + '.'

    def volume(self, change=None, to=None):
        problem = self.need_video()
        if problem:
            return problem
        state = self.player.state()
        current = state.get('volume') if state.get('volume') is not None else 100
        target = to if to is not None else current + change
        if target > 100 and current >= 100:
            return 'Loudest. Volume 100 percent.'
        if target < 0 and current <= 0:
            return 'Volume 0 percent.'
        value = self.player.set_volume(target)
        return 'Volume ' + str(int(round(value if value is not None else target))) + ' percent.'

    def mute(self, wanted=None):
        problem = self.need_video()
        if problem:
            return problem
        state = self.player.state()
        wanted = not state.get('muted') if wanted is None else wanted
        muted = self.player.set_muted(wanted)
        return 'Sound off.' if muted else 'Sound on.'

    def captions(self, wanted=None):
        problem = self.need_video()
        if problem:
            return problem
        state = self.player.state()
        captions = state.get('captions') or {}
        if not captions.get('available'):
            return 'This video has no captions.'
        if wanted is not None and captions.get('on') == wanted:
            return 'Captions are already ' + ('on.' if wanted else 'off.')
        captions = self.player.toggle_captions()
        return 'Captions on. Press R to read the current caption.' if captions.get('on') else 'Captions off.'

    def read_caption(self):
        problem = self.need_video()
        if problem:
            return problem
        captions = self.player.state().get('captions') or {}
        if not captions.get('on'):
            return 'Captions are off. Press C to turn them on.' if captions.get('available') else 'This video has no captions.'
        return captions.get('text') or 'No caption is showing right now.'

    def quality_list(self):
        problem = self.need_video()
        if problem:
            return problem
        levels = self.player.qualities()
        if not levels:
            return 'This video has no quality choices.'
        current = self.player.state().get('quality')
        self.push({'kind': 'quality', 'name': 'quality', 'title': 'Video quality', 'items': levels, 'current': current,
                   'index': levels.index(current) if current in levels else 0})
        return 'Video quality. Now ' + yp.quality_name(current) + '. ' + self.describe() + ' Press Enter to choose.'

    def set_quality(self, code):
        problem = self.need_video()
        if problem:
            return problem
        result = self.player.set_quality(code)
        if self.view() and self.view()['kind'] == 'quality':
            self.views.pop()
        return 'Quality ' + yp.quality_name(result or code) + '.'

    def next_video(self):
        problem = self.need_video()
        if problem:
            return problem
        before = self.player.state().get('id')
        self.player.next_video()
        for _ in range(20):
            self.player.settle(250)
            state = self.player.state()
            if state.get('id') and state.get('id') != before:
                return self.now_on(state, 'Next video. ')
        return 'There is no next video.'

    def now_on(self, state, lead=''):
        self.video = {'id': state.get('id'), 'title': state.get('title') or 'Untitled video', 'channel': state.get('channel', ''),
                      'live': state.get('live')}
        if not self.played or self.played[-1]['id'] != self.video['id']:
            self.played.append(dict(self.video))
        self.last = state
        said = lead + 'Playing ' + self.video['title'] + '.'
        if state.get('ad', {}).get('showing'):
            said += ' ' + self.ad_words(state, first=True)
        return said

    def previous_video(self):
        problem = self.need_video()
        if problem:
            return problem
        if len(self.played) < 2:
            return 'There is no previous video.'
        self.played.pop()
        item = dict(self.played.pop(), kind='video')
        return self.play(item, 'Previous video. ')

    def autoplay(self, wanted=None):
        problem = self.need_video()
        if problem:
            return problem
        state = self.player.state()
        if state.get('autoplay') is None:
            return 'Autoplay cannot be changed on this video.'
        if wanted is not None and state['autoplay'] == wanted:
            return 'Autoplay is already ' + ('on.' if wanted else 'off.')
        on = self.player.toggle_autoplay()
        return 'Autoplay on. The next video starts by itself.' if on else 'Autoplay off. Playback stops when the video ends.'

    def fullscreen(self):
        problem = self.need_video()
        if problem:
            return problem
        on = self.player.toggle_fullscreen()
        return 'Video full screen on. Press F again to turn it off.' if on else 'Video full screen off.'

    def skip_ad(self):
        problem = self.need_video()
        if problem:
            return problem
        state = self.player.state()
        ad = state.get('ad') or {}
        if not ad.get('showing'):
            return 'No ad is playing.'
        if not ad.get('skippable'):
            return 'This ad cannot be skipped yet. You will hear when Skip is ready.'
        if not self.player.skip_ad():
            return 'The Skip button could not be pressed. Press S to try again.'
        self.player.settle(500)
        self.last = self.player.state()
        return 'Ad skipped.' if not self.last.get('ad', {}).get('showing') else 'Another ad is playing.'

    def chapter_items(self):
        details = self.player.details()
        chapters = [{'title': c['title'], 'time': yp.clock_seconds(c['time'])} for c in details.get('chapters') or []
                    if yp.clock_seconds(c.get('time')) is not None]
        return chapters or yp.chapters_from_description(details.get('description'))

    def chapters(self):
        problem = self.need_video()
        if problem:
            return problem
        items = self.chapter_items()
        if not items:
            return 'This video has no chapters.'
        now = self.player.state().get('time') or 0
        index = max([i for i, c in enumerate(items) if c['time'] <= now + 1] or [0])
        self.push({'kind': 'chapters', 'name': 'chapters', 'title': 'Chapters', 'items': items, 'index': index})
        return 'Chapters. ' + self.describe() + ' Press Enter to go there.'

    def chapter_step(self, step):
        problem = self.need_video()
        if problem:
            return problem
        items = self.chapter_items()
        if not items:
            return 'This video has no chapters.'
        now = self.player.state().get('time') or 0
        if step > 0:
            target = next((c for c in items if c['time'] > now + 1), None)
        else:
            started = [c for c in items if c['time'] <= now + 1]
            # Back from early in a chapter goes to the one before, as in YouTube.
            target = started[-2] if len(started) > 1 and now - started[-1]['time'] < 3 else started[-1] if started else None
            if target is items[0] and now < 3:
                target = None
        if target is None:
            return 'There is no ' + ('next' if step > 0 else 'previous') + ' chapter.'
        return self.seek_to(target['time'], 'Chapter: ' + target['title'] + '.')

    def details(self, part='all'):
        problem = self.need_video()
        if problem:
            return problem
        details = self.player.details()
        state = self.player.state()
        if part == 'description':
            text = ' '.join((details.get('description') or '').split())
            return ('Description. ' + text) if text else 'This video has no description.'
        lines = [details.get('title') or self.video['title']]
        channel = details.get('channel') or self.video.get('channel')
        if channel:
            lines.append('Channel: ' + channel + ('. ' + yp.spoken_count(details['subscribers']) if details.get('subscribers') else ''))
        if state.get('duration') and not state.get('live'):
            lines.append('Length: ' + yp.spoken_length(state['duration']))
        if state.get('live'):
            lines.append('Live now')
        if details.get('info'):
            lines.append(yp.spoken_count('. '.join(details['info'][:2])))
        likes = yp.like_count(details)
        if likes:
            lines.append(likes + ('. You like this video' if details.get('liked') else ''))
        subscribed = yp.subscribed_state(details)
        if subscribed is not None and details.get('signedIn'):
            lines.append('You are subscribed' if subscribed else 'You are not subscribed')
        return '. '.join(line.rstrip('.') for line in lines if line) + '. Press D for the description.'

    def comments(self):
        problem = self.need_video()
        if problem:
            return problem
        found = self.player.comments(40)
        if found.get('off'):
            return found['off'].rstrip('.') + '.'
        threads = found.get('threads') or []
        if not threads:
            return 'No comments could be read for this video. Say open in browser to use the page itself.'
        count = found.get('count') or ''
        self.push({'kind': 'comments', 'name': 'comments', 'title': 'Comments' + (', ' + count if count else ''), 'items': threads})
        return self.describe('Comments' + (', ' + yp.spoken_count(count) if count else '') + '. ')

    def up_next(self):
        problem = self.need_video()
        if problem:
            return problem
        try:
            items, _ = self.player.read_list('related')
        except YouTubeError as exc:
            return str(exc)
        if not items:
            return 'No videos are listed to watch next.'
        return self.push({'kind': 'list', 'name': 'related', 'title': 'Up next', 'items': items})

    def channel(self):
        problem = self.need_video()
        if problem:
            return problem
        details = self.player.details()
        href = details.get('channelHref')
        if not href:
            return 'The channel could not be found on this page.'
        return self.open_list(yp.absolute(href).rstrip('/') + '/videos', 'Channel: ' + (details.get('channel') or 'videos'), 'channel')

    # ------------------------------------------------------------ account actions
    def account_check(self, action):
        """None when the account action can go ahead, else what to say (it may ask a question)."""
        problem = self.need_video()
        if problem:
            return problem
        if self.engine() == 'webview2':
            return self.ask_switch(action)
        try:
            signed = self.player.signed_in()
        except YouTubeError as exc:
            return str(exc)
        if signed is False:
            return SIGN_IN_NEEDED.format(action=ACTIONS.get(action, action))
        return None

    def ask_switch(self, action):
        return self.ask(('switch', action), EMBEDDED_EDGE.format(action=ACTIONS.get(action, action)))

    def ask(self, pending, question):
        self.pending, self.choice = pending, 'no'
        return question

    def like(self):
        problem = self.account_check('like')
        if problem:
            return problem
        details = self.player.details()
        if details.get('liked'):
            return 'You already like this video. To take your like back, say remove like.'
        return self.ask(('like', True), 'Like ' + self.video['title'] + '? Say Yes or No.')

    def unlike(self):
        problem = self.account_check('like')
        if problem:
            return problem
        if not self.player.details().get('liked'):
            return 'You have not liked this video.'
        return self.ask(('like', False), 'Take back your like on ' + self.video['title'] + '? Say Yes or No.')

    def subscription(self, wanted=None):
        problem = self.account_check('subscribe' if wanted is not False else 'unsubscribe')
        if problem:
            return problem
        details = self.player.details()
        now = yp.subscribed_state(details)
        channel = details.get('channel') or self.video.get('channel') or 'this channel'
        if wanted is None:
            wanted = not now
        if now is wanted:
            return ('You are already subscribed to ' if wanted else 'You are not subscribed to ') + channel + '.'
        return self.ask(('subscribe', wanted), ('Subscribe to ' if wanted else 'Unsubscribe from ') + channel + '? Say Yes or No.')

    def save_list(self):
        problem = self.account_check('save')
        if problem:
            return problem
        options = self.player.save_options()
        self.push({'kind': 'save', 'name': 'save', 'title': 'Save to', 'items': options})
        return 'Save to. ' + self.describe() + ' Press Enter to save to the list you hear, or to remove it.'

    def watch_later(self):
        problem = self.account_check('save')
        if problem:
            return problem
        options = self.player.save_options()
        option = next((o for o in options if o['label'].casefold() == 'watch later'), None)
        if option is None:
            self.player.close_dialog()
            return 'Watch later is not in the list. Press W to choose a playlist.'
        if option['checked']:
            self.player.close_dialog()
            return 'This video is already in Watch later.'
        return self.ask(('save', option), 'Save ' + self.video['title'] + ' to Watch later? Say Yes or No.')

    def comment(self, text):
        text = ' '.join(str(text).split())
        if not text:
            return 'Say or type your comment.'
        problem = self.account_check('comment')
        if problem:
            return problem
        return self.ask(('comment', text), 'Post this comment on ' + self.video['title'] + '? Your comment: ' + text +
                        '. Everyone can see it. Say Yes or No.')

    def answer(self, yes):
        pending, self.pending = self.pending, None
        action, data = pending
        if not yes:
            if action == 'save' and self.has_video():
                self.player.close_dialog()
                if self.view() and self.view()['kind'] == 'save':
                    self.views.pop()
            return {'switch': 'YouTube stays in embedded Edge, signed out.', 'comment': 'Your comment was not posted.'}.get(
                action, 'Nothing changed.') + ' ' + self.where()
        if action == 'switch':
            return self.switch_to_chrome()
        if action == 'sign out':
            return self.finish_sign_out()
        problem = self.need_video()
        if problem:
            return problem
        if action == 'like':
            liked = self.player.like()
            if data:
                return 'Liked.' if liked else 'YouTube did not save your like. Press I to check, or say open in browser.'
            return 'Like removed.' if not liked else 'YouTube did not remove your like. Press I to check, or say open in browser.'
        if action == 'subscribe':
            state = self.player.subscribe(data)
            if state is data:
                return 'Subscribed.' if data else 'Unsubscribed.'
            return 'YouTube did not confirm the change. Press I to check, or say open in browser.'
        if action == 'save':
            checked = self.player.toggle_option(data['label'])
            self.player.close_dialog()
            if self.view() and self.view()['kind'] == 'save':
                self.views.pop()
            if checked is None:
                return 'YouTube did not confirm the change. Press W to check.'
            return ('Saved to ' if checked else 'Removed from ') + data['label'] + '.'
        if action == 'comment':
            posted = self.player.post_comment(data)
            return 'Comment posted.' if posted else 'YouTube did not show your comment yet. Press O to check the comments.'
        return 'Nothing changed.'

    def switch_to_chrome(self):
        m = self.m
        web = self.web()
        import chrome_setup
        missing = chrome_setup.missing_for_web(m, 'chrome', False)
        if missing:
            return ('@chrome_setup', missing)
        resume = self.video
        self.close_pages()
        notice = web.choose_browser('chrome')
        self.list_page = self.video_page = None
        self.single_tab = False
        said = 'YouTube now uses Chrome. ' if notice.startswith('Selected') else notice + ' '
        view = self.view()
        try:
            # The list first, in the new browser's first tab; then the video in a tab of its own.
            result = self.go(view['url'] if view and view.get('url') else yp.feed_url('home'))
            if resume:
                result += self.play(dict(resume, kind='video'))
        except YouTubeError as exc:
            return said + str(exc)
        return said + 'To sign in, say sign in. ' + result

    def sign_in(self):
        """Open Google's sign-in page on the web screen, where you type your own email and password."""
        if self.engine() == 'webview2':
            return self.ask_switch('sign in')
        web = self.web()
        page = self.list_page if self.alive(self.list_page) else None
        if page is not None:
            web.backend.page = page
        result = web.open(yp.SIGN_IN_URL)
        self.signing_in = True
        return ('@web', 'Google sign-in page. Type your own email and password. Voice Companion does not save them. '
                'When you are done, say back to YouTube. ' + result)

    def sign_out(self):
        if self.engine() == 'webview2':
            return 'You are not signed in. Embedded Edge always uses YouTube signed out.'
        if self.signed_in is False:
            return 'You are not signed in to YouTube. To sign in, say sign in.'
        return self.ask(('sign out', None), 'Sign out of YouTube? This browser is signed out of your Google account too. '
                                            'Say Yes or No.')

    def finish_sign_out(self):
        """Google's own sign-out page, then Home again to check."""
        self.go(yp.SIGN_OUT_URL)
        self.go(yp.feed_url('home'))
        try:
            self.signed_in = self.lists.signed_in()
        except YouTubeError:
            self.signed_in = None
        if self.signed_in:
            return 'YouTube did not sign you out. Say open in browser to sign out on the page.'
        return 'You are signed out of YouTube. ' + self.where()

    def after_sign_in(self):
        """Back from Google's page: load the list and the video again, so they use your account."""
        self.signing_in = False
        said = ''
        if self.has_video():
            try:
                at = int(self.player.state().get('time') or 0)
                self.video_page.goto(yp.watch_url(self.video['id']) + '&t=' + str(at) + 's', wait_until='domcontentloaded', timeout=30000)
                self.player.wait_for_player()
            except Exception:
                pass
        view = self.view()
        if view and view['kind'] == 'list' and view.get('name') in yp.FEEDS:
            said = self.load(view['name'], 'YouTube. ')
        elif view and view['kind'] != 'menu':
            said = self.describe('YouTube, ' + view['title'] + '. ')
        else:
            said = self.show_menu()
        try:
            signed = self.lists.signed_in()
        except YouTubeError:
            signed = None
        if signed is not None:
            self.signed_in = signed
        return ('You are signed in. ' if signed else 'You are still signed out. ' if signed is False else '') + said

    # ------------------------------------------------------------ leaving and the browser view
    def open_in_browser(self):
        web = self.web()
        view = self.view()
        about_video = view is None or view['kind'] in ('comments', 'chapters', 'quality', 'save') or view.get('name') == 'related'
        page = self.video_page if self.has_video() and about_video else None
        page = page or (self.list_page if self.alive(self.list_page) else self.video_page if self.alive(self.video_page) else None)
        if page is None:
            return None
        web.backend.page = page
        try:
            page.bring_to_front()
        except Exception:
            pass
        web.snapshot = None
        return web._refresh()

    def close_pages(self):
        for page in {id(p): p for p in (self.video_page, self.list_page) if self.alive(p)}.values():
            if page is self.list_page and page is getattr(self.web().backend, 'page', None) and self.video_page is not page:
                continue
            try:
                page.close()
            except Exception:
                pass
        self.stopped()

    def stop(self):
        """Stop the video: close its tab, or pause it when it shares the tab with the lists."""
        if not self.has_video():
            self.stopped()
            return 'No video is playing.'
        if self.video_page is self.list_page:
            try:
                self.player.pause()
            except YouTubeError:
                pass
        else:
            try:
                self.video_page.close()
            except Exception:
                pass
            self.video_page = None
        self.stopped()
        return 'Video stopped.'

    def where(self):
        view = self.view()
        playing = ''
        if self.has_video():
            playing = ' Playing ' + self.video['title'] + '.' if self.owner else ''
        if not view:
            return ('YouTube.' + playing).strip()
        if view['kind'] == 'menu':
            return self.describe(MENU_TITLE + '. ').rstrip() + playing
        return self.describe('YouTube, ' + view['title'] + '. ').rstrip() + playing

    # ------------------------------------------------------------ news while a video plays
    def check(self, mode):
        """One look at the player: ads, Skip ready, a new video, the end, an error.
        Returns words to say on the YouTube screen, or ''."""
        if self.video is None:
            return ''
        if not self.alive(self.video_page):
            self.stopped()
            return 'The browser closed, so the video stopped.' if mode == MODE else ''
        hub = getattr(self.m, 'MEDIA_HUB', None)
        if self.owner and hub is not None:
            session = getattr(hub.player, 'session', None)
            if hub.player.kind and session != self.hub_session:
                # Radio, a podcast or a file started after the video: the video pauses.
                self.owner = False
                try:
                    self.player.pause()
                except YouTubeError:
                    pass
                return 'YouTube paused.' if mode == MODE else ''
        if not self.owner:
            return ''   # paused for other audio; nothing to say until you play it again
        try:
            state = self.player.state()
        except YouTubeError:
            return ''
        before, self.last = self.last or {}, state
        if not self.owner or mode != MODE:
            return ''
        news = []
        ad, old_ad = state.get('ad') or {}, before.get('ad') or {}
        if state.get('id') and before.get('id') and state['id'] != before['id']:
            news.append(self.now_on(state, 'Next video. '))
        elif ad.get('showing') and not old_ad.get('showing'):
            news.append(self.ad_words(state))
        elif ad.get('showing') and ad.get('skippable') and not old_ad.get('skippable'):
            news.append('Skip is ready. Press S to skip the ad.')
        elif old_ad.get('showing') and not ad.get('showing'):
            news.append('The ad ended. Back to ' + (self.video or {}).get('title', 'the video') + '.')
        if state.get('ended') and not before.get('ended') and not ad.get('showing'):
            news.append('The video ended.' + (' Press N for the next video.' if state.get('autoplay') is False else ''))
        if state.get('error') and state['error'] != before.get('error'):
            news.append('YouTube says: ' + state['error'].rstrip('.') + '.')
        return ' '.join(news)


# ---------------------------------------------------------------- hooks from companion.py
def context(m, value):
    """app_context: on a video list, letters are actions; in the YouTube menu and the filter
    lists they move to the next name; while the search box is open they are typed there."""
    s = screen(m)
    view = s.view()
    value['menu_letters'] = bool(view and view['kind'] in MENU_KINDS and s.pending is None and not s.asking)
    if s.pending is not None:
        value['confirmation'] = s.choice
    if s.asking:
        value['task_input'] = True
        value['naming_label'] = SEARCH_LABEL
    if s.entry is not None:
        value['entry_request'] = s.entry
    return value


def typing(m):
    """True while the YouTube search box is open: words there are search words, not requests."""
    s = SCREEN
    return s is not None and s.m is m and s.asking


def menu_state(m):
    s = screen(m)
    view = s.view()
    if s.pending is not None or s.asking or not view or not view['items']:
        return None
    labels = s.labels(view)
    def select(index):
        view['index'] = index
    return labels, max(0, min(view['index'], len(labels) - 1)), select


def title_detail(m):
    s = screen(m)
    view = s.view()
    return view['title'] if view else ''


def navigation_key(m, key):
    """The command for a key on the YouTube screen, or None when the key does nothing here."""
    s = screen(m)
    if s.pending is not None:
        if key.lower() in ('y', 'alt+y', 'letter:y'):
            return 'yes'
        if key.lower() in ('n', 'alt+n', 'letter:n'):
            return 'no'
        if key in ('Up', 'Left', 'ShiftTab', 'Home', 'Down', 'Right', 'Tab', 'End'):
            choice = 'yes' if key in ('Up', 'Left', 'ShiftTab', 'Home') else 'no'
            if choice == s.choice:
                m.speak('Start of list.' if choice == 'yes' else 'End of list.')
            else:
                s.choice = choice
                m.speak(choice.capitalize() + '.')
                m.sync_app_context(MODE)
            return None
        if key in ('Enter', 'Space'):
            return s.choice
        if key == 'Escape':
            return 'no'
        return None
    if s.asking:
        # The search box: typed letters stay in the box (the window keeps them there).
        if key == 'Enter':
            return '@youtube:search:enter'
        if key == 'Escape':
            return '@youtube:search:cancel'
        if key == 'F1':
            return 'help with youtube'
        if key in ('Up', 'Down', 'Left', 'Right', 'Home', 'End', 'Tab', 'ShiftTab'):
            m.speak('Type what to search for, then press Enter. Escape cancels.')
        return None
    if key.startswith('Letter:'):
        m.choose_menu_letter(key[7:], MODE)
        return None
    from input_parity import screen_command
    direct = screen_command(key, MODE)
    if direct:
        return direct
    view = s.view()
    if view and view['kind'] in MENU_KINDS and key in ('Left', 'Right'):
        return 'previous' if key == 'Left' else 'next'   # as in the app's other menus
    return {'Up': 'previous', 'Down': 'next', 'Home': 'first item', 'End': 'last item', 'Enter': 'enter',
            'Escape': 'go back', 'Left': 'skip back', 'Right': 'skip forward', 'Next': 'page down', 'Prior': 'page up'}.get(key)


def open_screen(m, mode, lead=''):
    s = screen(m)
    if mode not in (MODE, 'web'):
        s.return_mode = 'media_menu' if mode in ('media_menu', 'awake', 'media') else mode
    if getattr(s, 'signing_in', False):
        return s.after_sign_in()
    s.end_asking()
    if mode == 'web' and len(s.views) > 1:
        # Back from the page itself ("open in browser"): the list you were on.
        return s.describe('YouTube, ' + s.view()['title'] + '. ')
    return s.show_menu(lead)


COMMANDS = {
    # playback
    'play or pause video': 'toggle', 'play or pause': 'toggle', 'toggle video': 'toggle',
    'play': 'play', 'play video': 'play', 'resume video': 'play', 'continue video': 'play',
    'pause': 'pause', 'pause video': 'pause', 'pause youtube': 'pause', 'stop video': 'stop', 'stop youtube': 'stop',
    'skip back': 'skip back', 'rewind': 'skip back', 'skip forward': 'skip forward', 'fast forward': 'skip forward',
    'restart video': 'restart', 'restart': 'restart', 'start over': 'restart', 'play from the start': 'restart',
    # Faster, slower, louder and volume alone change Companion's own voice; these name the video.
    'faster video': 'faster', 'video faster': 'faster', 'slower video': 'slower', 'video slower': 'slower',
    'normal speed': 'normal speed', 'reset speed': 'normal speed', 'normal video speed': 'normal speed',
    'video volume up': 'volume up', 'louder video': 'volume up', 'video louder': 'volume up',
    'video volume down': 'volume down', 'quieter video': 'volume down', 'video quieter': 'volume down',
    'mute or unmute': 'mute', 'mute': 'mute on', 'mute video': 'mute on', 'unmute': 'mute off', 'unmute video': 'mute off',
    'captions on or off': 'captions', 'toggle captions': 'captions', 'captions on': 'captions on', 'turn on captions': 'captions on',
    'subtitles on': 'captions on', 'captions off': 'captions off', 'turn off captions': 'captions off', 'subtitles off': 'captions off',
    'read caption': 'read caption', 'read captions': 'read caption', 'read the caption': 'read caption',
    'what did they say': 'read caption', 'current caption': 'read caption',
    'video quality': 'quality', 'quality': 'quality', 'change quality': 'quality',
    'next video': 'next', 'play the next video': 'next', 'play next video': 'next', 'skip video': 'next',
    'previous video': 'previous', 'play the previous video': 'previous', 'last video': 'previous',
    'autoplay on or off': 'autoplay', 'toggle autoplay': 'autoplay', 'autoplay on': 'autoplay on', 'turn on autoplay': 'autoplay on',
    'autoplay off': 'autoplay off', 'turn off autoplay': 'autoplay off',
    'video full screen': 'fullscreen', 'full screen video': 'fullscreen', 'make the video full screen': 'fullscreen',
    'list chapters': 'chapters', 'chapters': 'chapters', 'next chapter': 'next chapter', 'previous chapter': 'previous chapter',
    'skip ad': 'skip ad', 'skip the ad': 'skip ad', 'skip this ad': 'skip ad', 'skip ads': 'skip ad',
    # the video
    'video details': 'details', 'details': 'details', 'about this video': 'details', 'video info': 'details',
    'read description': 'description', 'description': 'description', 'read the description': 'description',
    'read comments': 'comments', 'comments': 'comments', 'list comments': 'comments', 'show comments': 'comments',
    'go to channel': 'channel', 'open channel': 'channel', 'channel videos': 'channel',
    'up next': 'up next', 'related videos': 'up next', 'what is next': 'up next',
    # account
    'like video': 'like', 'like': 'like', 'like this video': 'like', 'thumbs up': 'like',
    'remove like': 'unlike', 'unlike': 'unlike', 'unlike video': 'unlike',
    'subscribe or unsubscribe': 'subscription', 'subscribe': 'subscribe', 'subscribe to this channel': 'subscribe',
    'unsubscribe': 'unsubscribe', 'unsubscribe from this channel': 'unsubscribe',
    'save video': 'save', 'save to playlist': 'save', 'save to a playlist': 'save', 'add to playlist': 'save',
    'save to watch later': 'watch later save', 'add to watch later': 'watch later save',
    'comment': 'comment prompt', 'add a comment': 'comment prompt', 'post a comment': 'comment prompt', 'write a comment': 'comment prompt',
    'sign in': 'sign in', 'sign in to youtube': 'sign in', 'log in': 'sign in',
    'sign out': 'sign out', 'sign out of youtube': 'sign out', 'log out': 'sign out',
    # lists
    'home': 'home', 'youtube home': 'home', 'home feed': 'home', 'go home': 'home', 'recommended': 'home',
    'subscriptions': 'channels', 'my subscriptions': 'channels', 'subscribed channels': 'channels', 'my channels': 'channels',
    'new from subscriptions': 'subscriptions', 'subscription videos': 'subscriptions', 'new from my subscriptions': 'subscriptions',
    'new subscription videos': 'subscriptions', 'latest from subscriptions': 'subscriptions',
    'history': 'history', 'watch history': 'history', 'my history': 'history',
    'watch later': 'watch_later', 'my watch later': 'watch_later', 'watch later list': 'watch_later',
    'liked videos': 'liked', 'my liked videos': 'liked',
    'playlists': 'playlists', 'my playlists': 'playlists', 'your playlists': 'playlists',
    'search youtube': 'search prompt', 'search': 'search prompt', 'youtube search': 'search prompt',
    'search on youtube': 'search prompt', 'search for a video': 'search prompt', 'find a video': 'search prompt',
    'search filters': 'filters', 'filters': 'filters', 'filter': 'filters', 'show filters': 'filters', 'change filters': 'filters',
    'clear filters': 'clear filters', 'remove filters': 'clear filters', 'no filters': 'clear filters',
    'youtube menu': 'menu', 'back to youtube menu': 'menu', 'go to youtube menu': 'menu', 'open youtube menu': 'menu',
    'refresh youtube': 'refresh', 'refresh': 'refresh', 'reload': 'refresh', 'load more': 'more', 'more videos': 'more',
    'open in browser': 'browser', 'show the page': 'browser', 'use the page': 'browser', 'open the page': 'browser',
    'where am i': 'where', 'what list is this': 'where',
    'page down': 'page down', 'next page': 'page down', 'page up': 'page up', 'previous page': 'page up',
    'next': 'next item', 'previous': 'previous item', 'first item': 'first', 'last item': 'last', 'start of list': 'first',
    'end of list': 'last', 'top of list': 'first', 'bottom of list': 'last', 'next item': 'next item', 'previous item': 'previous item',
    'enter': 'enter', 'that one': 'enter', 'play that one': 'enter', 'open': 'enter', 'open it': 'enter', 'play it': 'enter',
    'go back': 'back', 'back': 'back', 'escape': 'back', 'leave youtube': 'leave', 'close youtube': 'leave', 'exit youtube': 'leave',
}
# Commands that work from other screens while a YouTube video is what plays.
ANYWHERE = ('pause video', 'pause youtube', 'resume video', 'play video', 'stop video', 'stop youtube', 'next video',
            'play the next video', 'play next video', 'previous video', 'play the previous video', 'skip ad', 'skip the ad',
            'skip this ad', 'read caption', 'read captions')


def claims(text):
    """True for words that name YouTube, so "Play cats on YouTube" is not taken as a radio station."""
    command = normalized(text)
    return bool(SEARCH.fullmatch(command)) or command in ANYWHERE or 'youtube' in command.split()


def keep_keyboard(m, s):
    """A new browser window or tab may take the keyboard; give it back to Companion's window."""
    if s.opened_window:
        s.opened_window = False
        window = getattr(m, 'APP_WINDOW', None)
        if window is not None and hasattr(window, 'reclaim_focus'):
            try:
                window.reclaim_focus()
            except Exception:
                pass


def handle(m, text, mode, typed=False):
    """Commands for the YouTube screen, and the phrases that open it. None: not ours."""
    try:
        return _handle(m, text, mode, typed)
    finally:
        if SCREEN is not None and SCREEN.m is m:
            keep_keyboard(m, SCREEN)


def _handle(m, text, mode, typed=False):
    raw = text.strip()
    command = normalized(raw)
    s = screen(m)
    if mode != MODE:
        if mode in NOT_HERE or m.writing_dictation(mode, typed) or getattr(m, 'NAMED_LIST', None):
            return None
        if mode == 'browser_menu' and getattr(getattr(m.web_session, 'menu', None), 'level', 'menu') != 'menu':
            return None
        if mode == 'web' and getattr(m.web_session, 'keyboard_field', None):
            return None
        match = SEARCH.fullmatch(command)
        ask = command in ASK and mode not in ('web', 'browser_menu')
        # On a web page and the Web browser menu, "YouTube" alone may be a link or a favorite's name.
        if command in (OPEN_ON_WEB if mode in ('web', 'browser_menu') else OPEN) or match and mode != 'web' or ask:
            try:
                if match:
                    s.return_mode = 'media_menu' if mode in ('media_menu', 'awake') else mode
                    s.end_asking()
                    s.ensure_menu()
                    m.speak_status('Searching YouTube.')
                    m.speak(s.search(match[1] or match[2]))
                elif ask:
                    open_screen(m, mode)
                    m.speak(s.ask_search())
                else:
                    m.speak(open_screen(m, mode))
            except Exception as exc:
                m.speak(str(exc) if isinstance(exc, YouTubeError) else UNEXPECTED)
                if not s.views:
                    return mode
            return MODE
        if command in ANYWHERE and s.owner and s.has_video() and mode != 'web':
            try:
                m.speak(run(m, s, COMMANDS[command], raw, command))
            except Exception as exc:
                m.speak(str(exc) if isinstance(exc, YouTubeError) else UNEXPECTED)
            return mode
        return None
    try:
        return here(m, s, raw, command, typed)
    except YouTubeError as exc:
        m.speak(str(exc))
        return MODE
    except Exception:
        # The page did something unexpected. Say so plainly; never stop the app.
        import sys
        import traceback
        traceback.print_exc(file=sys.stderr)
        m.speak(UNEXPECTED)
        return MODE


def here(m, s, raw, command, typed=False):
    if s.pending is not None:
        if command in YES:
            result = s.answer(True)
        elif command in NO:
            result = s.answer(False)
        elif command in ('help', 'what mode', 'toggle audio') or command.startswith(('help with ', 'help for ')):
            return None
        else:
            m.speak('Say Yes or No.')
            return MODE
        if isinstance(result, tuple):
            return special(m, s, result)
        m.speak(result)
        return MODE
    if s.asking:
        # The search box: every word is a search word, except help, Companion's voice and the main menu.
        if command in ('help', 'help with youtube', 'youtube help', 'what mode') or m.speech_control(raw) or m.spoken_control(raw):
            return None   # help, Companion's voice, sleep and exit work here, as in the document name box
        if command in ('main menu', 'back to main menu'):
            s.end_asking()
            return 'awake'
        m.speak(s.search_answer(raw, command, typed))
        return MODE
    if raw.startswith('@youtube:search:'):
        m.speak(s.describe())   # the box closed before this key arrived
        return MODE
    if command.startswith('help with youtube') or command in ('youtube help', 'help with this screen'):
        return None
    if command in ('stop', 'resume') and s.has_video():
        # As with radio: while a video is loaded, "Stop" and "Resume" mean the video,
        # not Companion's voice ("Stop talking" still silences the voice).
        m.speak(run(m, s, 'stop' if command == 'stop' else 'play', raw, command))
        return MODE
    if command in PASS_THROUGH or command.startswith(('help with ', 'help for ')) or m.speech_control(raw) \
            or m.speech_setting_request(raw) is not None or command in m.VOICE_LIST_COMMANDS:
        return None
    if command in ('main menu', 'back to main menu'):
        return 'awake'   # handle() says Main menu, and stops the video unless audio keeps playing
    action = COMMANDS.get(command)
    if action is None:
        action, raw = parse(command, raw)
    if action is None:
        if command in OPEN:
            m.speak(s.where())
            return MODE
        m.speak('YouTube. Say search for and words to search, or press Alt for the YouTube menu. Press F1 for help.')
        return MODE
    if action == 'leave':
        return leave(m, s)
    result = run(m, s, action, raw, command)
    if isinstance(result, tuple):
        return special(m, s, result)
    if result is None:
        return MODE
    if isinstance(result, str) and result.startswith('@mode:'):
        return result[6:]
    m.speak(result)
    return MODE


def special(m, s, result):
    kind, value = result
    if kind == '@chrome_setup':
        import chrome_setup
        return chrome_setup.offer(m, MODE, value)
    if kind == '@web':
        m.speak(value)
        return 'web'
    return MODE


def parse(command, raw):
    """Commands with a value: "search for cats", "forward 30 seconds", "speed 1.5", "volume 40",
    "jump to 50 percent", "skip amount 30 seconds", "post comment Great video", filter words."""
    match = re.fullmatch(r'(?:search for|search youtube for|search|find|look up) (.+)', command)
    if match:
        return 'search', match[1]
    match = re.fullmatch(r'(?:skip |go |move )?(back|forward|ahead|rewind|fast forward) (.+)', command)
    if match and yp_seconds(match[2]) is not None:
        seconds = yp_seconds(match[2])
        return 'seek', str(-seconds if match[1] in ('back', 'rewind') else seconds)
    match = re.fullmatch(r'(?:set )?(?:the )?skip (?:amount|time|length|seconds)(?: to)? (.+)', command)
    if match:
        return 'skip amount', match[1]
    match = re.fullmatch(r'(?:set )?(?:the )?(?:video|playback) speed(?: to)? (.+)', command)
    if match:
        return 'speed', match[1]
    match = re.fullmatch(r'(?:set )?(?:the )?video volume(?: to)? (\d+)(?: percent)?', command)
    if match:
        return 'volume to', match[1]
    match = re.fullmatch(r'(?:jump|go|skip|move) to (\d+) percent', command)
    if match:
        return 'percent', match[1]
    match = re.fullmatch(r'(?:jump|go|skip|move) to (.+)', command)
    if match and yp_seconds(match[1]) is not None:
        return 'seek to', str(yp_seconds(match[1]))
    match = re.fullmatch(r'(?:set )?(?:video )?quality(?: to)? (.+)', command)
    if match:
        return 'quality to', match[1]
    match = re.match(r'(?:post|add|write) (?:a )?comment[ :,]+(.+)', raw, re.I)
    if match:
        return 'comment', match[1]
    if yp.filter_request(command):
        return 'filter', command
    return None, raw


def yp_seconds(text):
    value = spoken_seconds(text)
    return int(value) if value is not None and value > 0 else None


def run(m, s, action, raw, command):
    """Do one action. Returns words to say, a ('@kind', value) request, or None."""
    simple = {
        'toggle': s.toggle, 'play': lambda: s.set_playing(True), 'pause': lambda: s.set_playing(False), 'stop': s.stop,
        'skip back': lambda: s.skip_by(-s.skip), 'skip forward': lambda: s.skip_by(s.skip), 'back': lambda: go_back(m, s), 'restart': s.restart, 'faster': lambda: s.speed('faster'),
        'slower': lambda: s.speed('slower'), 'normal speed': lambda: s.speed(1.0),
        'volume up': lambda: s.volume(10), 'volume down': lambda: s.volume(-10),
        'mute': s.mute, 'mute on': lambda: s.mute(True), 'mute off': lambda: s.mute(False),
        'captions': s.captions, 'captions on': lambda: s.captions(True), 'captions off': lambda: s.captions(False),
        'read caption': s.read_caption, 'quality': s.quality_list, 'next': s.next_video, 'previous': s.previous_video,
        'autoplay': s.autoplay, 'autoplay on': lambda: s.autoplay(True), 'autoplay off': lambda: s.autoplay(False),
        'fullscreen': s.fullscreen, 'chapters': s.chapters, 'next chapter': lambda: s.chapter_step(1),
        'previous chapter': lambda: s.chapter_step(-1), 'skip ad': s.skip_ad, 'details': s.details,
        'description': lambda: s.details('description'), 'comments': s.comments, 'channel': s.channel, 'up next': s.up_next,
        'like': s.like, 'unlike': s.unlike, 'subscription': s.subscription, 'subscribe': lambda: s.subscription(True),
        'unsubscribe': lambda: s.subscription(False), 'save': s.save_list, 'watch later save': s.watch_later,
        'sign in': s.sign_in, 'sign out': s.sign_out, 'search prompt': s.ask_search, 'filters': s.open_filters,
        'menu': s.show_menu, 'clear filters': s.clear_filters, 'where': s.where,
        'next item': lambda: s.move(1), 'previous item': lambda: s.move(-1), 'first': lambda: s.move(to='first'),
        'last': lambda: s.move(to='last'), 'enter': s.enter,
        'page down': lambda: s.page_move(1), 'page up': lambda: s.page_move(-1),
    }
    if action == 'play' and not s.has_video():
        # "Play" on a chosen video, before any video plays: play it, as Enter does.
        view, item = s.view(), s.current()
        if view and view['kind'] == 'list' and item and item.get('kind') in ('video', 'short') and item.get('id'):
            return s.enter()
    if action in simple:
        return simple[action]()
    if action in yp.FEEDS:
        m.speak_status('Loading ' + yp.FEEDS[action][0] + '.')
        return s.load(action)
    if action == 'refresh':
        view = s.view()
        if view and view.get('name') == 'related':
            s.views.pop()
            return s.up_next()
        if view and view['kind'] in MENU_KINDS:
            return 'There is no list here to load again. ' + s.describe()
        if not view or view['kind'] != 'list' or not view.get('url'):
            return s.load('home')
        if view.get('name') in yp.FEEDS:
            return s.load(view['name'])
        if view.get('name') == 'search':
            return s.search()
        return s.open_list(view['url'], view['title'], view['name'])
    if action == 'more':
        view = s.view()
        if not view or view['kind'] != 'list' or not s.more(view):
            return 'No more videos could be loaded.'
        return s.describe(str(len(view['items'])) + ' items now. ')
    if action == 'search':
        m.speak_status('Searching YouTube.')
        return s.search(raw)
    if action == 'filter':
        return s.spoken_filter(*yp.filter_request(raw))
    if action == 'seek':
        return s.skip_by(int(raw))
    if action == 'seek to':
        return s.seek_to(int(raw))
    if action == 'skip amount':
        seconds = yp_seconds(raw)
        if seconds is None or not 1 <= seconds <= 3600:
            return 'Say a time from 1 second to 1 hour, such as 30 seconds.'
        s.skip = seconds
        return 'J, L and the arrows now skip ' + yp.spoken_length(seconds) + '.'
    if action == 'speed':
        try:
            from task_navigation import spoken_speed
            value = spoken_speed(raw)
        except ValueError as exc:
            return str(exc)
        if not 0.25 <= value <= 2:
            return 'Choose a speed from 0.25 to 2.'
        return s.speed(value)
    if action == 'volume to':
        return s.volume(to=int(raw))
    if action == 'percent':
        value = int(raw)
        if not 0 <= value <= 100:
            return 'Say a number from 0 to 100.'
        return s.jump_percent(value)
    if action == 'quality to':
        problem = s.need_video()
        if problem:
            return problem
        wanted = re.sub(r'\D', '', raw)
        levels = s.player.qualities()
        code = next((q for q in levels if wanted and wanted in yp.quality_name(q)), None) or ('auto' if 'auto' in raw else None)
        if code is None:
            return 'That quality is not offered. ' + s.quality_list()
        return s.set_quality(code)
    if action == 'comment prompt':
        problem = s.account_check('comment')
        if problem:
            return problem
        return '@mode:' + m.task_navigation().action('post comment {value}', MODE)
    if action == 'comment':
        return s.comment(raw)
    if action == 'browser':
        result = s.open_in_browser()
        if result is None:
            return 'YouTube is not open in the browser yet.'
        return ('@web', 'Web page. ' + result + ' Say back to YouTube to return.')
    return None


def go_back(m, s):
    view = s.view()
    if view and view['kind'] == 'save' and s.has_video():
        s.player.close_dialog()
    if not view or view['kind'] == 'menu':
        return '@mode:' + leave(m, s)
    s.views.pop()
    if not s.views or s.view()['kind'] == 'menu':
        return s.show_menu()   # Escape from a list comes back to the YouTube menu
    return s.describe('Back to ' + s.view()['title'] + '. ')


def leave(m, s):
    """Escape on the YouTube menu: back to the Media menu (or where YouTube was opened from).
    The video keeps playing, like radio; going to the main menu stops it."""
    s.pending = None
    s.end_asking()
    target = s.return_mode or 'media_menu'
    if target == 'media_menu':
        return m.task_navigation().enter('media_menu', 'Media menu', 3)
    if target == 'web' and not (m.web_session and m.web_session.snapshot):
        target = 'awake'
    if target != 'awake':
        m.speak(m.options_return_notice(target))
    return target


# ---------------------------------------------------------------- now playing, Control+P, main menu, polling
def active(m):
    s = SCREEN
    return s is not None and s.m is m and s.owner and s.has_video()


def now_playing(m):
    """"What's playing?" and F3 while a YouTube video is what plays; None otherwise."""
    if not active(m):
        return None
    s = SCREEN
    try:
        state = s.player.state()
    except YouTubeError as exc:
        return str(exc)
    title = state.get('title') or s.video['title']
    channel = state.get('channel') or s.video.get('channel')
    said = ('YouTube live: ' if state.get('live') else 'YouTube: ') + title.rstrip('.') + '.'
    if channel:
        said += ' Channel: ' + channel.rstrip('.') + '.'
    if state.get('ad', {}).get('showing'):
        return said + ' ' + s.ad_words(state)
    said += ' Paused.' if state.get('paused') else ' Playing.'
    where = s.position_words(state)
    if where and not state.get('live'):
        said += ' Position ' + where + '.'
    return said


def toggle_audio(m):
    """Control+P while a YouTube video is what plays; None otherwise."""
    if not active(m):
        return None
    try:
        result = SCREEN.toggle()
    except YouTubeError as exc:
        return str(exc)
    return 'Video ' + result[0].lower() + result[1:] if result in ('Playing.', 'Paused.') else result


def main_menu(m):
    """Going back to the main menu stops the video, unless audio keeps playing in the background."""
    if SCREEN is None or SCREEN.m is not m or not SCREEN.has_video():
        return
    if m.PREFERENCES.get('keep_audio_playing', False) and SCREEN.owner:
        return
    try:
        SCREEN.stop()
    except Exception:
        SCREEN.stopped()
    web = getattr(m, 'web_session', None)
    if web is not None and SCREEN.alive(SCREEN.list_page) and not m.PREFERENCES.get('web_keep_open', False):
        # As when you leave a web page: the browser window closes unless Keep browser open is on.
        try:
            web.leave(False)
        except Exception:
            pass
        SCREEN.list_page = SCREEN.video_page = None
        SCREEN.single_tab = False


def poll(m, mode):
    """Main loop: about once a second while a video plays, say ad news and a new video."""
    s = SCREEN
    if s is None or s.m is not m or s.video is None:
        return mode
    now = time.monotonic()
    if now < s.next_check:
        return mode
    s.next_check = now + 1.0
    logical = mode if mode != 'sleep' else None
    try:
        news = s.check(logical)
    except Exception:
        news = ''
    if news and mode == MODE:
        m.speak(news)
    return mode
