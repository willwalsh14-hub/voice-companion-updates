"""Guided, self-voicing browsing in a separate browser profile.

The browser is opened only after a user command. Page text is untrusted and is
never treated as an instruction to the assistant. No credentials are stored by
this module; the browser retains its own session in its dedicated profile.
"""
from list_announcements import name_first
from menu_navigation import announce_item
from field_selection import FieldSelection, selection_command
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, quote, unquote
from privacy_controls import saved_address

from browser_navigation import prepare_address
from web_browse import BrowseCursor,QUICK_KEYS,SEMANTICS,choice_field,describe,editable_combo,kind,text_field,voice_browse_key
from web_modes import APP_MODE,BROWSE_MODE
from reading_navigation import ReadingCursor, reading_request
from storage import write_text_atomic
from dictation_text import clean_dictation
from ai_dictation import prepare_dictation


class WebError(Exception):
    pass


def _plain(text):
    return re.sub(r'\s+', ' ', str(text or '')).strip()


def _tokens(text):
    words = re.findall(r'[a-z0-9]+', str(text).lower())
    return {w for w in words if w not in {'the', 'a', 'an', 'to', 'for', 'on', 'of', 'my', 'i', 'me',
                                          'how', 'do', 'can', 'want', 'would', 'like', 'please',
                                          'uh', 'um', 'that', 'this', 'about', 'find', 'look',
                                          'looking', 'where', 'is', 'there', 'button', 'link'}}


def _label(item):
    return _plain(item.get('label') or item.get('text') or item.get('placeholder') or item.get('name'))


def _voice_value(item,spoken,literal=False):
    if literal or item.get('type')=='password':return spoken
    if editable_combo(item):
        # Searches and suggestions: a recognizer's closing period is not part of the query.
        return re.sub(r'(?<![.])[.!]$','',clean_dictation(spoken)).rstrip()
    if item.get('type')=='email':
        from email_draft import normalize_recipient
        return normalize_recipient(spoken)
    if item.get('type')=='url':return prepare_address(spoken)[0]
    if item.get('type') in ('number','tel','search','date','time','datetime-local','month','week'):
        return clean_dictation(spoken)
    # Names, codes and account identifiers are not prose.
    identity = ' '.join(str(item.get(k) or '') for k in ('label','name','autocomplete','placeholder'))
    if re.search(r'(?i)\b(?:name|address|user|username|login|code|pin|account|zip|postal)\b',identity):
        return clean_dictation(spoken)
    return prepare_dictation(spoken)


def match_choices(items, request):
    intent = _tokens(request)
    if not intent:
        return []
    aliases = {'login': {'login', 'log', 'sign', 'signin'},
               'transfer': {'transfer', 'transferring', 'move'},
               'account': {'account', 'accounts'}}
    expanded = set(intent)
    for word in intent:
        for group in aliases.values():
            if word in group: expanded.update(group)
    scored = []
    for item in items:
        label = _label(item)
        overlap = len(expanded & _tokens(label))
        if overlap:
            scored.append((overlap, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:5]]


class Favorites:
    def __init__(self, folder):
        self.path = Path(folder) / 'web-favorites.json'

    def all(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data,dict):return {}
            clean={}
            for key,item in data.items():
                if not isinstance(item,dict) or not item.get('name'):continue
                address=saved_address(item.get('url'))
                if address:clean[key]=dict(item,url=address)
            if clean!=data:
                temporary=self.path.with_suffix('.tmp');temporary.write_text(json.dumps(clean,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(self.path)
            return clean
        except (OSError, ValueError):
            return {}

    def add(self, name, url):
        name = _plain(name)
        if not name or len(name) > 60 or any(c in name for c in '\r\n'):
            raise WebError('Use a short name for the favorite.')
        parsed = urlsplit(url)
        if parsed.scheme not in ('http','https') or not parsed.hostname:
            raise WebError('Only HTTP and HTTPS pages can be saved.')
        if saved_address(url)!=url:
            raise WebError('This address contains private sign-in data. Open and save the site main page instead.')
        data = self.all()
        data[name.casefold()] = {'name': name, 'url': url}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def get(self, name):
        data=self.all();key=_plain(name).casefold()
        if key in data:return data[key]
        matches=[item for stored,item in data.items() if stored.rstrip('.!?')==key.rstrip('.!?')]
        return matches[0] if len(matches)==1 else None

    def delete(self, name):
        data = self.all()
        if data.pop(_plain(name).casefold(), None) is None:
            raise ValueError('That favorite no longer exists.')
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def rename(self, name, replacement):
        item = self.get(name)
        if not item:
            raise ValueError('That favorite no longer exists.')
        if self.get(replacement) and replacement.casefold() != name.casefold():
            raise ValueError('That favorite name already exists. Choose another name.')
        self.add(replacement, item['url'])
        if replacement.casefold() != name.casefold():
            self.delete(name)


def navigation_interrupted(error):
    message=str(error).casefold()
    return any(text in message for text in ('execution context was destroyed',
        'cannot find context with specified id','cannot find context with id'))


DEFAULT_BROWSER = 'webview2'
# Guided browsing offers two browsers: embedded Edge (WebView2, the default,
# owned by Companion) and Chrome. Edge, Brave and Firefox were removed as
# choices; a saved choice of one of them becomes embedded Edge, said once.
GUIDED_BROWSERS = ('webview2', 'chrome')
RETIRED_BROWSERS = ('edge', 'brave', 'firefox')
BROWSER_NAMES = {'webview2': 'embedded Edge', 'chrome': 'Chrome', 'edge': 'Edge', 'brave': 'Brave', 'firefox': 'Firefox'}
GOOGLE_SIGN_IN_NOTICE = ('Google does not allow signing in inside embedded Edge. To use a Google account on this site, '
                         'say use Chrome, or use your regular browser with the Voice Companion extension')
BROWSER_CHOICES = 'Guided websites can use embedded Edge or Chrome. Say use embedded Edge, or say use Chrome.'


def browser_name(browser):
    """How a browser is named when spoken: 'webview2' is embedded Edge."""
    return BROWSER_NAMES.get(str(browser), str(browser).capitalize())


def retired_browser_notice(name):
    """What is said when someone asks for a browser that is no longer a guided choice."""
    own = (' To use your own ' + browser_name(name) + ' browser, connect it with the Voice Companion extension.'
           if name in ('edge', 'brave') else '')
    return browser_name(name) + ' is no longer a guided browser. ' + BROWSER_CHOICES + own


def browser_key(value):
    """The stored name (webview2 or chrome) for a spoken or shown browser name."""
    return {'embedded edge': 'webview2', 'webview2': 'webview2', 'chrome': 'chrome', 'google chrome': 'chrome'}.get(
        _plain(value).lower(), DEFAULT_BROWSER)


def browser_setting(folder):
    """The Guided browser option as Options shows it. Nothing is rewritten here,
    so the Web browser screen still says once that an old choice changed."""
    try:
        choice = (Path(folder) / 'browser-choice.txt').read_text(encoding='utf-8').strip().lower()
    except OSError:
        choice = DEFAULT_BROWSER
    return 'Chrome' if choice == 'chrome' else 'embedded Edge'


def guided_backend(folder, browser):
    """A guided browser owned by Companion (never the user's connected browser)."""
    if browser == 'webview2':
        from webview2_backend import WebView2Backend
        return WebView2Backend(folder)
    return BrowserBackend(folder, browser)


def saved_browser_choice(folder):
    """The saved guided browser, and a notice to say once when an old choice was replaced.

    A saved choice of Edge, Brave or Firefox becomes embedded Edge. The file is
    rewritten, so the notice is given only once."""
    path = Path(folder) / 'browser-choice.txt'
    try:
        choice = path.read_text(encoding='utf-8').strip().lower()
    except OSError:
        return None, ''
    if choice in GUIDED_BROWSERS:
        return choice, ''
    if choice in RETIRED_BROWSERS:
        try:
            write_text_atomic(path, DEFAULT_BROWSER)
        except OSError:
            pass
        return DEFAULT_BROWSER, ('Your guided browser was ' + browser_name(choice) + '. Guided websites now use embedded Edge or Chrome, '
                                 'so Companion now uses embedded Edge. To use Chrome instead, say use Chrome.')
    return None, ''


class BrowserBackend:
    """Playwright uses a dedicated profile, never the user's normal one.

    Guided Chrome is the installed Google Chrome, started with its own profile.
    The connected regular browser (connected_browser.py) reuses this class for
    the user's own Chrome, Edge or Brave tab."""
    BROWSERS = GUIDED_BROWSERS

    def __init__(self, folder, browser='chrome', headless=False):
        self.folder = Path(folder)
        self.browser = browser.lower()
        self.headless = headless
        if self.browser not in self.BROWSERS and not getattr(self, 'regular', False):
            raise WebError(BROWSER_CHOICES)
        self.playwright = self.context = self.page = None

    def start(self):
        if self.browser=='webview2':
            from webview2_backend import WebView2Backend
            if not getattr(self,'_embedded',None):self._embedded=WebView2Backend(self.folder,self.headless)
            self._embedded.start();self.page=self._embedded.page;self.context=self._embedded.context;self.playwright=self._embedded.playwright;return
        if self.page and not self.page.is_closed(): return
        if self.context or self.playwright:self.close()
        try:
            from playwright.sync_api import sync_playwright
            self.folder.mkdir(parents=True, exist_ok=True)
            from browser_process import browser_process_environment
            with browser_process_environment(self.folder):
                self.playwright = sync_playwright().start()
            with browser_process_environment(self.folder):
                self.context = self.playwright.chromium.launch_persistent_context(
                    str(self.folder), headless=self.headless, accept_downloads=False, channel='chrome')
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.page.set_default_timeout(10000)
        except Exception as exc:
            self.close()
            import traceback
            try:(self.folder/'browser-error.txt').write_text(traceback.format_exc(),encoding='utf-8')
            except OSError:pass
            raise WebError('The guided ' + browser_name(self.browser) +
                           ' browser could not start. If Chrome is not installed, say set up Chrome.') from exc

    def close(self):
        if getattr(self,'_embedded',None):
            embedded=self._embedded;self._embedded=None;self.page=self.context=self.playwright=None;embedded.close();return
        context,playwright=self.context,self.playwright
        self.page=self.context=self.playwright=None
        for action in (context.close if context else None,playwright.stop if playwright else None):
            if action:
                try:action()
                except Exception:pass

    def refocus(self):
        if getattr(self,'_embedded',None):return self._embedded.refocus()
        if not self.page or self.page.is_closed():return False
        self.page.bring_to_front()
        return True

    def scroll_to(self,item):
        frame=self.page.frames[item['frame_index']] if 'frame_index' in item else self.page
        element=frame.locator('[data-voice-companion-key="'+item['key']+'"]')
        if element.count()==1:element.evaluate("e=>e.scrollIntoView({block:'nearest',inline:'nearest'})",timeout=300)

    def goto(self, url):
        self.start()
        self.page.goto(url, wait_until='domcontentloaded', timeout=30000)

    def back(self):
        if not self.history_step(-1):raise WebError('There is no previous page.')

    def _history_identity(self):
        """The document showing now: its address and when it was created."""
        try:
            value=self.page.evaluate('()=>[location.href,String(performance.timeOrigin)]')
            return (value[0],value[1])
        except Exception:return None

    HISTORY_WAIT=30

    def history_step(self,delta):
        """Go back (delta -1) or forward (delta 1) one page, as Alt+Left and Alt+Right do.

        Returns True when another page shows, False when there is none that way.
        Three things made this unreliable:
        - The driver waits for the new page's load events. A page the browser
          kept in its back/forward cache (embedded Edge and everyday browsers
          keep one) shows again without them, so Back waited 30 seconds and
          then reported a failure, although the browser had gone back.
        - An ad frame that moves to another address adds a history entry for
          that frame only. Back then changed the ad, not the page, and an ad
          that keeps moving could stop Back from ever leaving the page.
        - The first page opened sits on top of the browser's blank start page,
          which Back would open."""
        from playwright.sync_api import TimeoutError as BrowserTimeout
        page=self.page
        start=self._history_identity() or (page.url,None)
        moves=[]
        def request(item):
            try:
                if item.is_navigation_request():moves.append('main' if item.frame==page.main_frame else 'frame')
            except Exception:pass
        def navigated(frame):
            try:
                if frame!=page.main_frame:moves.append('frame')
            except Exception:pass
        page.on('request',request);page.on('framenavigated',navigated)
        try:
            # First choice: go straight to the page's own previous entry.
            plan=self._history_jump(delta)
            if plan=='none':return False
            if plan=='started':return self._history_arrival(start,delta,moves,True,direct=True)=='page'
            # Otherwise step through the browser's history one entry at a time.
            for hop in range(6):
                moves.clear()
                try:
                    (page.go_back if delta<0 else page.go_forward)(wait_until='commit',timeout=800)
                    answered=True
                except BrowserTimeout:answered=False
                except Exception as exc:
                    if not navigation_interrupted(exc):raise
                    answered=False
                outcome=self._history_arrival(start,delta,moves,answered)
                if outcome!='frame':return outcome=='page'
            return False
        finally:
            for name,handler in (('request',request),('framenavigated',navigated)):
                try:page.remove_listener(name,handler)
                except Exception:pass

    CDP_HISTORY=True

    def _history_jump(self,delta):
        """Start going to the previous (or next) page itself, skipping entries made
        only by frames. Returns 'started', 'none' when there is no such page, or
        None when this browser cannot say (then history is stepped through)."""
        try:listing=self._navigation_history()
        except Exception:listing=None
        if listing:
            history,go,close=listing
            try:
                entries,index=history['entries'],history['currentIndex']
                # Every entry carries the address of the page it belongs to;
                # entries made by a frame repeat the page's own address.
                here=entries[index]['url'];position=index+delta
                while 0<=position<len(entries) and entries[position]['url']==here:position+=delta
                if not 0<=position<len(entries) or not str(entries[position]['url']).startswith(('http://','https://')):return 'none'
                go(entries[position]['id']);return 'started'
            except Exception:pass
            finally:close()
        # The page's own navigation list holds only its own entries, never a
        # frame's. It covers pages on the same site.
        try:return self.page.evaluate(NAVIGATION_JUMP,delta) or None
        except Exception:return None

    def _navigation_history(self):
        """The browser's history entries for this tab, and a way to go to one."""
        if not self.CDP_HISTORY or self.context is None or not hasattr(self.context,'new_cdp_session'):return None
        session=self.context.new_cdp_session(self.page)
        def close():
            try:session.detach()
            except Exception:pass
        try:history=session.send('Page.getNavigationHistory')
        except Exception:
            close();raise
        return history,lambda entry:session.send('Page.navigateToHistoryEntry',{'entryId':entry}),close

    def _history_arrival(self,start,delta,moves,answered,direct=False):
        """Wait for the step to show a page. Returns 'page', 'none' or 'frame'.

        After a direct jump a page is known to be coming, so only the page counts."""
        import time
        deadline=time.monotonic()+self.HISTORY_WAIT;checks=0;page=self.page
        while True:
            now=self._history_identity()
            if now is not None and now!=start:
                if not now[0].startswith(('http://','https://')) and start[0].startswith(('http://','https://')):
                    # The blank start page: return to the page the user was on.
                    self._return_to(start,-delta)
                    return 'none'
                if now[1]!=start[1] and 'main' not in moves:
                    # Shown again from the back/forward cache. The driver saw
                    # no load events and lost the page's frames, so later waits
                    # would hang and frames could not be read. Loading it again
                    # gives a complete, current page.
                    page.reload(wait_until='domcontentloaded',timeout=30000)
                return 'page'
            checks+=1
            if direct:pass
            elif answered and 'main' not in moves and 'frame' not in moves and checks>=3:return 'none'
            elif 'frame' in moves and 'main' not in moves and (answered or checks>=6):return 'frame'
            if time.monotonic()>deadline:
                raise WebError(('The previous' if delta<0 else 'The next')+' page did not finish opening. Press F5 to refresh the page.')
            page.wait_for_timeout(50)

    def _return_to(self,place,delta):
        import time
        try:(self.page.go_back if delta<0 else self.page.go_forward)(wait_until='commit',timeout=5000)
        except Exception:pass
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            now=self._history_identity()
            if now is not None and now[0]==place[0]:return
            self.page.wait_for_timeout(50)

    def document_stamp(self):
        stamp=self.page.evaluate(DOCUMENT_STAMP)
        stamp['frames']=[frame.url for frame in self.page.frames]
        stamp['frame_states']=[];stamp['frame_identity']=[]
        for frame in self.page.frames[1:]:
            try:
                if _frame_shown(frame) is None:continue
                state=frame.evaluate(DOCUMENT_STAMP)
                stamp['frame_states'].append({key:state.get(key) for key in ('url','epoch','revision','ready','busy')})
                stamp['frame_identity'].append((state.get('url'),state.get('epoch')))
            except Exception:continue
        return stamp

    def wait_ready(self):
        for attempt in range(3):
            try:return self._wait_ready()
            except Exception as exc:
                if not navigation_interrupted(exc) or attempt==2:raise
                self.page.wait_for_timeout(100)

    def _wait_ready(self):
        from playwright.sync_api import TimeoutError as BrowserTimeout
        # CDP-attached profile tabs can restore from the back/forward cache
        # without replaying lifecycle events. Inspect the restored document.
        regular=getattr(self,'regular',False)
        if regular:self.page.wait_for_function("()=>document.readyState!=='loading'",timeout=15000)
        else:self.page.wait_for_load_state('domcontentloaded',timeout=15000)
        self.page_ready=True
        try:
            if regular:self.page.wait_for_function("()=>document.readyState==='complete'",timeout=5000)
            else:self.page.wait_for_load_state('load',timeout=5000)
        except BrowserTimeout:self.page_ready=False
        self.document_stamp()
        try:self.page.wait_for_function("()=>Date.now()-window.__voiceCompanionDocument.changed>=200&&!document.querySelector('[aria-busy=\"true\"]')",timeout=4000)
        except BrowserTimeout:pass
        stamp=self.document_stamp();self.page_ready=stamp['ready']=='complete' and not stamp.get('busy')

    def settle(self):
        # Some sites populate links after the first page paint.
        self.page.wait_for_timeout(500)

    def snapshot(self):
        self.start()
        self._watch_dialogs()
        script = '''() => {const accessibleLabel=/*ACCESSIBLE_LABEL*/;return accessibleLabel.withCachedLabels(()=>{
          const visibility=new WeakMap();const layoutVisible=e=>{if(visibility.has(e))return visibility.get(e);const parent=e.assignedSlot||e.parentElement||e.getRootNode()?.host;const result=getComputedStyle(e).display!=='none'&&!e.hidden&&!e.inert&&e.getAttribute('aria-hidden')!=='true'&&(!parent||layoutVisible(parent));visibility.set(e,result);return result;};const visible=(e,descend=false)=>layoutVisible(e)&&(descend||!['hidden','collapse'].includes(getComputedStyle(e).visibility));
          const text = e => (e?.innerText || e?.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 180);
          // Include open shadow roots used by modern sites and search widgets.
          const selector='a,button,input,textarea,select,[role="button"],[role="link"],[role="checkbox"],[role="radio"],[role="textbox"],[role="combobox"],[role="listbox"],[contenteditable="true"]';
          const typable=n=>!!n&&n.nodeType===1&&(n.isContentEditable||n.tagName==='TEXTAREA'||n.tagName==='INPUT'&&['text','search','email','url','tel'].includes((n.type||'').toLowerCase())||(n.getAttribute('role')||'').trim()==='textbox');
          const comboText=e=>{
            if(e.tagName==='SELECT')return null;
            const role=(e.getAttribute('role')||'').trim().split(/\\s+/)[0];
            if(e.tagName==='INPUT'&&!role&&e.list&&typable(e))return e;
            if(role!=='combobox')return null;
            if(typable(e))return e;
            const root=e.getRootNode(),owned=(e.getAttribute('aria-owns')||'').split(/\\s+/).filter(Boolean).map(id=>(root.getElementById?root.getElementById(id):null)||document.getElementById(id)).filter(Boolean);
            return [...e.querySelectorAll('input,textarea,[contenteditable="true"],[role="textbox"]'),...owned].find(n=>typable(n)&&visible(n))||null;
          };
          const allCache=new WeakMap();
          // Changes inside open shadow roots are watched too (a video player's Skip ad button).
          const watchRoot=window.__voiceCompanionDocument?.watch||(()=>{});
          const all = root => {if(allCache.has(root))return allCache.get(root);if(root.host)watchRoot(root);const nodes=[...root.querySelectorAll('*')].flatMap(e =>
            [e, ...(e.shadowRoot ? all(e.shadowRoot) : []), ...(e.tagName==='SLOT'?e.assignedElements({flatten:true}).flatMap(n=>[n,...all(n)]):[])]);allCache.set(root,nodes);return nodes;};
          const modal=[...document.querySelectorAll('[role="dialog"][aria-modal="true"],dialog[open]')].filter(e=>visible(e)&&!e.closest('[aria-hidden="true"],[inert]')).at(-1);
          const readingRoot=modal||document.body||document.documentElement;
          const priority=modal||document.querySelector('#search,#main,main,[role="main"],article');
          const nodes=[...new Set([...(priority ? all(priority).filter(e=>e.matches(selector)) : []),
                                   ...all(modal||document).filter(e=>e.matches(selector))])];
          let controlSerial=1+all(document).reduce((m,e)=>Math.max(m,/^n\\d+$/.test(e.getAttribute('data-voice-companion-key')||'')?Number(e.getAttribute('data-voice-companion-key').slice(1)):0),0);
          const controlKeyOwners=new Map();const controlKey=e=>{const existing=e.getAttribute('data-voice-companion-key');if(!/^n\\d+$/.test(existing||'')||controlKeyOwners.has(existing)&&controlKeyOwners.get(existing)!==e)e.setAttribute('data-voice-companion-key','n'+controlSerial++);const key=e.getAttribute('data-voice-companion-key');controlKeyOwners.set(key,e);return key;};
          const controls = [];
          // Editable combo boxes (a site's search box with suggestions) are typed
          // into. ARIA 1.0 puts the text box inside a combobox container; that
          // container is the one control, and typing goes to its text box.
          const comboInputs=new Map();
          for(const e of all(modal||document).filter(n=>n.matches('[role="combobox"]'))){const t=comboText(e);if(t&&t!==e&&visible(e))comboInputs.set(t,e);}
          for (let i=0; i<nodes.length && controls.length<500; i++) {
            const e=nodes[i]; if (!visible(e) || e.closest('[aria-hidden="true"],[inert]') || comboInputs.has(e)) continue;
            const tag=e.tagName.toLowerCase(), type=(e.type || '').toLowerCase();
            if (['hidden','submit-image'].includes(type)) continue;
            let label=accessibleLabel(e);
            label=(label || '').trim().replace(/\\s+/g,' ').slice(0,1000);
            if (!label) continue;
            const role=(tag==='a' && e.href ? 'link' : e.getAttribute('role')) || (tag==='button'||tag==='input'&&['submit','button','reset','image'].includes(type)?'button':
                e.isContentEditable?'textbox':['input','textarea','select'].includes(tag)?'field':'');
            if (!role) continue;
            const identity=controlKey(e);
            controls.push({key:identity, id:e.id||'', role, label, tag, type,
                in_form:!!e.closest('form'),
                required:!!e.required||e.getAttribute('aria-required')==='true', disabled:!!e.disabled||e.getAttribute('aria-disabled')==='true', readonly:!!e.readOnly||e.getAttribute('aria-readonly')==='true', radio_group:e.name ? [...document.forms].indexOf(e.form)+':'+e.name : '', checked:accessibleLabel.checked(e),
                tabindex:e.tabIndex,size:e.size||0,multiple:!!e.multiple,options:tag==='select'?[...e.options].map(o=>({label:o.text.trim().slice(0,120),value:o.value,disabled:o.disabled,selected:o.selected})): [...(e.querySelectorAll('[role="option"]')), ...((e.getAttribute('aria-controls')||e.getAttribute('aria-owns')||'').split(/\\s+/).filter(Boolean).flatMap(id=>[...(document.getElementById(id)?.querySelectorAll('[role="option"]')||[])]))].filter((o,i,a)=>a.indexOf(o)===i).map(o=>({label:accessibleLabel(o),value:o.getAttribute('data-value')||accessibleLabel(o),disabled:o.getAttribute('aria-disabled')==='true',selected:o.getAttribute('aria-selected')==='true'})),
                selected_label:type==='password'?'':tag==='select'?(e.selectedOptions[0]?.text.trim().slice(0,120) || ''):(e.getAttribute('aria-valuetext')||e.querySelector('[role="option"][aria-selected="true"]')?.innerText||e.value||''),
                href:tag==='a'?e.href:'', value:type==='password'?'':
                (e.isContentEditable?e.innerText.slice(0,2000):['input','textarea','select'].includes(tag)?String(e.value || '').slice(0,2000):''),
                value_truncated:type!=='password' && String(e.value || '').length>2000,
                filled:type==='password'?!!e.value:false});
            const control=controls[controls.length-1];if(!control.selected_label)control.selected_label=control.options.find(option=>option.selected)?.label||'';
            const comboInput=comboText(e);
            if(control.role==='combobox'||comboInput){
              control.combo_edit=!!comboInput;
              if(comboInput&&comboInput!==e){control.combo_input_key=controlKey(comboInput);control.value=comboInput.isContentEditable?comboInput.innerText.slice(0,2000):String(comboInput.value||'').slice(0,2000);
                control.selected_label=control.value;control.tabindex=comboInput.tabIndex;control.required=control.required||!!comboInput.required||comboInput.getAttribute('aria-required')==='true';control.readonly=control.readonly||!!comboInput.readOnly;}
              if(comboInput===e&&tag==='input'&&e.list){control.datalist=true;control.options=[...e.list.options].filter(o=>(o.value||o.label).trim()).map(o=>({label:(o.value||o.label).trim().slice(0,120),value:o.value,disabled:o.disabled,selected:false}));}
            }
          }
          // Read the full visible page. Sites may put most content outside main.
          /*SEMANTICS*/
          const bodyText=output.trimEnd().slice(0,200000);
          const challenge=(['www.google.com','google.com'].includes(location.hostname)&&(location.pathname==='/sorry'||location.pathname.startsWith('/sorry/'))) ||
              (/^captcha$/i.test((document.title||'').trim())&&bodyText.length<6000) ||
              (/^(?:just a moment|checking your browser|verify you are human|human verification|security check)\\b/i.test(document.title||'')&&bodyText.length<6000) ||
              (/our systems have detected unusual traffic from your computer network|complete the security check|checking your browser before accessing/i.test(bodyText.slice(0,1200))&&bodyText.length<6000);
          return {title:document.title || location.hostname, url:location.href,
                  text:bodyText, modal_key:modal?controlKey(modal):'', anchors, document_epoch:String(performance.timeOrigin)+':'+window.__voiceCompanionDocument.epoch, elements:elements.filter(e=>e.start<200000), elements_truncated:elements.length>=5000, text_truncated:(readingRoot?.innerText || '').length>200000, controls, challenge,
                  controls_truncated:nodes.length>500,
                  visible_frames:[...document.querySelectorAll('iframe')].filter(e=>visible(e)&&!e.closest('[aria-hidden="true"],[inert]')).length};
        });}'''
        script=script.replace('/*SEMANTICS*/',SEMANTICS).replace('/*ACCESSIBLE_LABEL*/',ACCESSIBLE_LABEL)
        # Return one JSON string. Letting the driver convert the large object
        # value by value took over a second on a page with 5,000 elements.
        script=SNAPSHOT_AS_TEXT.replace('/*SNAPSHOT*/',script)
        self.page.evaluate(DOCUMENT_STAMP)
        result = snapshot_value(self.page.evaluate(script))
        # Playwright can inspect cross-origin frames without asking page JS to
        # cross the browser's origin boundary.
        active_frames={self.page.main_frame}
        for index, frame in enumerate(self.page.frames):
            if frame == self.page.main_frame: continue
            if frame.parent_frame not in active_frames:continue
            try:
                frame_key=_frame_shown(frame,readable=True)
                if frame_key is None:continue
                parent_index=self.page.frames.index(frame.parent_frame) if frame.parent_frame!=self.page.main_frame else None
                container=next((e for e in result.get('elements',[]) if e.get('role')=='frame' and e.get('key')==frame_key and e.get('frame_index')==parent_index),None)
                if container is None:continue
                container['child_frame_index']=index
                frame.evaluate(DOCUMENT_STAMP)
                part = snapshot_value(frame.evaluate(script))
                active_frames.add(frame)
            except Exception:
                continue
            for control in part['controls']:
                control['frame_index'] = index
                control['frame_url'] = part['url']
            result['controls'].extend(part['controls'])
            result['challenge'] = result.get('challenge', False) or (not result.get('text','').strip() and part.get('challenge', False))
            if part['text']:
                insertion=container['end'];base=insertion+1
                extra='\n'+part['text'][:50000]+'\nEnd of frame '+container['label']+'.\n'
                for element in result.get('elements',[])+result.get('anchors',[]):
                    if element['start']>=insertion:element['start']+=len(extra)
                    if element['end']>=insertion:element['end']+=len(extra)
                part['elements']=[e for e in part.get('elements',[]) if e.get('end',0)<=50000]
                for bookmark in part.get('anchors',[]):
                    bookmark.update(frame_index=index,frame_url=part['url'],frame_epoch=part.get('document_epoch'));bookmark['start']+=base;bookmark['end']+=base
                result.setdefault('anchors',[]).extend(part.get('anchors',[]))
                for element in part.get('elements',[]):
                    element.update(frame_index=index,frame_url=part['url'],frame_epoch=part.get('document_epoch'));element['start']+=base;element['end']+=base
                    if element.get('table'):element['table']='frame'+str(index)+':'+element['table']
                    if element.get('role')=='table':element['key']='frame'+str(index)+':'+element['key']
                place=result['elements'].index(container)+1
                result['elements'][place:place]=part.get('elements',[])
                result['text']=result['text'][:insertion]+extra+result['text'][insertion:]
                result['text_truncated'] = result.get('text_truncated', False) or part.get('text_truncated', False) or len(part['text']) > 50000
        return result

    def focused_control(self):
        """Read current keyboard focus; never expose secret field contents."""
        self.start()
        script = '''() => {
          let e=document.activeElement;while(e?.shadowRoot?.activeElement)e=e.shadowRoot.activeElement;
          if (!e || e===document.body) return null;
          const accessibleLabel=/*ACCESSIBLE_LABEL*/;const label=accessibleLabel(e);
          return {label, role:accessibleLabel.role(e)||'', tag:e.tagName.toLowerCase(), type:(e.type || '').toLowerCase(),
            value:e.type==='password'?'':String(e.value || '').slice(0,300),
            checked:accessibleLabel.checked(e), selected:e.tagName==='SELECT'?(e.selectedOptions[0]?.text || ''):''};
        }'''
        script=script.replace('/*ACCESSIBLE_LABEL*/',ACCESSIBLE_LABEL)
        for frame in reversed(self.page.frames):
            try:
                item=frame.evaluate(script)
                if item and item.get("tag") not in ("iframe","frame"):return item
            except Exception:continue
        return None

    def dictate_focused(self, spoken, literal=False):
        self.last_dictated_text=None
        self.start()
        for frame in self.page.frames:
            try:
                target = frame.evaluate('''() => {
                  let e=document.activeElement;while(e?.shadowRoot?.activeElement)e=e.shadowRoot.activeElement;
                  if (!e || e===document.body) return null;
                  const tag=e.tagName.toLowerCase(), type=(e.type || '').toLowerCase();
                  return {tag, type, editable:!!e.isContentEditable,
                          label:(/*ACCESSIBLE_LABEL*/)(e)};
                }'''.replace('/*ACCESSIBLE_LABEL*/',ACCESSIBLE_LABEL))
            except Exception:
                continue
            if not target: continue
            if target['type'] in ('hidden','file','number','tel'):
                return 'That field needs private or precise keyboard entry. I did not type into it.'
            if not (target['editable'] or target['tag'] == 'textarea' or
                    (target['tag'] == 'input' and target['type'] in ('text','search','email','url','password',''))):
                continue
            # Browser keyboard inserts at the caret, retaining native input events.
            try:value=_voice_value(target,spoken,literal)
            except ValueError as exc:return str(exc)
            self.page.keyboard.insert_text(value)
            self.last_dictated_text='' if target['type']=='password' else value
            return ('Password entered.' if target['type']=='password' else 'Added text to ' + _plain(target['label']) + '. Review the field before submitting.')
        return 'No editable text field has focus. Select a field in the browser, or say normal mode.'

    def insert_line_break(self):
        self.start()
        for frame in self.page.frames:
            try:
                kind = frame.evaluate("() => {let e=document.activeElement;while(e?.shadowRoot?.activeElement)e=e.shadowRoot.activeElement;return e?.isContentEditable?'rich':e?.tagName==='TEXTAREA'?'text':null;}")
            except Exception: continue
            if kind == 'rich': self.page.keyboard.press('Enter'); return 'New line.'
            if kind == 'text': self.page.keyboard.insert_text('\n'); return 'New line.'
        return 'Focus a multiline text field to insert a new line. Single-line fields cannot contain line breaks.'

    def select_focused(self, command):
        self.start()
        if not hasattr(self, '_field_selections'): self._field_selections = {}
        for frame in self.page.frames:
            try:
                state = frame.evaluate("""() => {
                  let e=document.activeElement;while(e?.shadowRoot?.activeElement)e=e.shadowRoot.activeElement;
                  if (!e || !(e.isContentEditable || e.tagName==='TEXTAREA' || e.tagName==='INPUT')) return null;
                  if (e.tagName==='INPUT' && !['text','search','url','tel','email','password'].includes(e.type)) return null;
                  e.dataset.voiceSelectionKey ||= 'field-'+(window.voiceSelectionCounter=(window.voiceSelectionCounter||0)+1);
                  const text=e.isContentEditable ? e.textContent : e.value;
                  let start=e.selectionStart, end=e.selectionEnd;
                  if(e.isContentEditable){
                    const sel=window.getSelection();
                    if(sel.rangeCount && e.contains(sel.anchorNode) && e.contains(sel.focusNode)){
                      const r=sel.getRangeAt(0), prefix=document.createRange();
                      prefix.selectNodeContents(e); prefix.setEnd(r.startContainer,r.startOffset); start=prefix.toString().length;
                      prefix.setEnd(r.endContainer,r.endOffset); end=prefix.toString().length;
                    }
                  }
                  return {key:e.dataset.voiceSelectionKey,text,start,end,private:e.type==='password',email:e.type==='email'};
                }""")
            except Exception:
                continue
            if not state: continue
            selector = self._field_selections.setdefault(state['key'], FieldSelection())
            text = state['text']
            def python_offset(value):
                return len(text.encode('utf-16-le')[:value*2].decode('utf-16-le',errors='ignore')) if value is not None else None
            result, span = selector.select(text, command, python_offset(state['start']), python_offset(state['end']))
            def native_offset(value): return len(text[:value].encode('utf-16-le'))//2
            native = [native_offset(x) for x in span] if span else None
            status = frame.evaluate("""data => {
              let e=document.activeElement;while(e?.shadowRoot?.activeElement)e=e.shadowRoot.activeElement;
              if(!e || e.dataset.voiceSelectionKey!==data.key || (e.isContentEditable?e.textContent:e.value)!==data.text) return 'changed';
              if(data.delete) return 'delete';
              if(!data.span){ if(e.isContentEditable) window.getSelection().removeAllRanges(); else if(e.setSelectionRange && e.type!=='email') e.setSelectionRange(e.selectionStart,e.selectionStart); return 'ok'; }
              const [start,end]=data.span;
              if(e.isContentEditable){
                const walker=document.createTreeWalker(e,NodeFilter.SHOW_TEXT); let n,offset=0,a=null,b=null;
                while(n=walker.nextNode()){
                  if(a===null && start<=offset+n.length)a=[n,Math.max(0,start-offset)];
                  if(end<=offset+n.length){b=[n,Math.max(0,end-offset)];break;} offset+=n.length;
                }
                if(!a || !b)return 'unsupported';
                const range=document.createRange();range.setStart(...a);range.setEnd(...b);
                const sel=window.getSelection();sel.removeAllRanges();sel.addRange(range);
              } else if(e.type==='email') return 'keyboard';
              else e.setSelectionRange(start,end);
              return 'ok';
            }""", {'key':state['key'],'text':text,'span':native,'delete':selector.edited_text is not None})
            if status == 'delete':
                self.page.keyboard.press('Backspace')
            elif status == 'keyboard':
                self.page.keyboard.press('Home')
                for _ in range(span[0]): self.page.keyboard.press('ArrowRight')
                for _ in range(span[1]-span[0]): self.page.keyboard.press('Shift+ArrowRight')
            elif status != 'ok': return 'The field changed or cannot support this selection. Focus it and try again.'
            if state['private']: return 'Selection updated in the password field.'
            return result
        return 'No editable text field has focus. Focus a field in the browser first.'

    def _element(self,item):
        frame=self.page
        if 'frame_index' in item:
            frames=self.page.frames;index=item['frame_index']
            if index>=len(frames) or frames[index].url!=item['frame_url']:
                raise WebError('The frame changed. Refresh the page before continuing.')
            frame=frames[index]
            if item.get('frame_epoch') is not None and frame.evaluate(DOCUMENT_STAMP)['epoch']!=item['frame_epoch']:
                raise WebError('The frame document changed. Refresh the page before continuing.')
        element=frame.locator('[data-voice-companion-key="'+item['key']+'"]')
        if element.count()!=1:raise WebError('The page changed. Refresh it before continuing.')
        current=element.evaluate(LIVE_IDENTITY)
        if current['label'].casefold()!=_plain(item.get('identity_label') or _label(item)).casefold() or current['tag']!=item['tag'] or current['type']!=item.get('type','') or current['href']!=item.get('href',''):
            raise WebError('The control changed. Refresh the page before continuing.')
        if current['disabled']:raise WebError('That control is unavailable.')
        return element

    def field_exists(self,item):
        try:
            frame=self.page.frames[item['frame_index']] if 'frame_index' in item else self.page
            if 'frame_url' in item and frame.url!=item['frame_url']:return False
            if item.get('frame_epoch') is not None and frame.evaluate(DOCUMENT_STAMP)['epoch']!=item['frame_epoch']:return False
            element=frame.locator('[data-voice-companion-key="'+item['key']+'"]')
            return element.count()==1 and element.is_visible() and element.evaluate("(e,item)=>!e.closest('[aria-hidden=\"true\"],[inert]')&&e.tagName.toLowerCase()===item.tag&&(e.type||'').toLowerCase()===(item.type||'')",item)
        except Exception:return False

    def _watch_dialogs(self):
        if not hasattr(self.page,'on'):return
        if getattr(self,'dialog_page',None) is self.page:return
        self.dialog_page=self.page
        def dialog_notice(dialog):
            self.dialog_notice=('Page message: ' if dialog.type=='alert' else 'Website confirmation canceled: ')+dialog.message
            dialog.dismiss()
        self.page.on('dialog',dialog_notice)

    def submit_default(self,item):
        """Let the site's Enter handling select its native default submitter."""
        return self.activate(item,key='Enter')

    def activate(self,item,key=None,focused=False):
        import time
        self.last_action_started=False
        self._watch_dialogs()
        element=self._element(item)
        before_page=self.page
        frame=self.page.frames[item.get('frame_index',0)]
        before_url=frame.url;before_epoch=frame.evaluate('()=>performance.timeOrigin')
        before_text=frame.evaluate("()=>document.body?.innerText.slice(0,200000)||''")
        # Compare visible control identities, never field values. A disabled
        # submitter or an analytics/style mutation is not a new form step.
        controls_signature="()=>JSON.stringify([...document.querySelectorAll('input,textarea,select,button,[role],[contenteditable]')].filter(e=>e.getClientRects().length&&!e.closest('[aria-hidden=\"true\"],[inert]')&&getComputedStyle(e).visibility!=='hidden').map(e=>[e.tagName,e.type||'',e.id,e.getAttribute('name')||'',e.getAttribute('role')||'']))"
        before_controls=frame.evaluate(controls_signature)
        before=self.document_stamp();before_pages=list(self.context.pages) if self.context else []
        opened=[]
        request_errors=[]
        pending_requests=set()
        def new_page(page):opened.append(page)
        def started_request(request):
            try:
                if request.frame==frame and request.resource_type in ('document','xhr','fetch'):pending_requests.add(request)
            except Exception:pass
        def finished_request(request):pending_requests.discard(request)
        def failed_request(request):
            pending_requests.discard(request)
            try:
                if request.frame==frame and request.resource_type in ('document','xhr','fetch'):request_errors.append('The browser reported a network error.')
            except Exception:pass
        def failed_response(response):
            try:
                if response.frame==frame and response.request.resource_type in ('document','xhr','fetch') and response.status>=400:
                    request_errors.append('The website returned an error (HTTP '+str(response.status)+').')
            except Exception:pass
        if self.context:self.context.on('page',new_page)
        before_page.on('request',started_request);before_page.on('requestfinished',finished_request)
        before_page.on('requestfailed',failed_request);before_page.on('response',failed_response)
        try:
            self.last_action_started=True
            if key and focused:self.page.keyboard.press(key)
            elif key:element.press(key,timeout=10000)
            else:element.click(timeout=10000)
            if kind(item)=='button' or key:
                try:
                    # A button that removes itself when pressed (Skip ad, Close,
                    # Accept cookies) has no form to check. Asking the driver
                    # about it anyway waited the full ten-second timeout.
                    invalid=None if element.count()!=1 else element.evaluate('''e=>{if(!e.matches('button[type=submit],button:not([type]),input[type=submit],input[type=image]') && !('''+str(key=='Enter').lower()+''' && e.matches('input:not([type=button]):not([type=reset]),select')))return null;const bad=[...(e.form?.elements||[])].find(n=>n.willValidate&&!n.validity.valid);return bad?{label:('''+ACCESSIBLE_LABEL+''')(bad),message:bad.validationMessage}:null;}''',timeout=5000)
                    if invalid:return {'validation':invalid['label']+'. '+invalid['message']}
                except Exception:pass
            clicked_at=time.monotonic()
            deadline=clicked_at+8
            changed=False
            while time.monotonic()<deadline:
                candidates=[p for p in opened if not p.is_closed()]
                if self.context:
                    candidates.extend(p for p in self.context.pages if p not in before_pages and p not in candidates and not p.is_closed())
                # Do not adopt tabs that were already open before the click.
                candidates=[p for p in candidates if p.opener() is before_page]
                if candidates:
                    self.page=candidates[-1]
                    self.page.wait_for_url(lambda url:str(url)!='about:blank',timeout=15000,wait_until='domcontentloaded')
                    changed=True;break
                try:
                    now=self.document_stamp()
                    navigation_changed=frame.url!=before_url or any(now.get(k)!=before.get(k) for k in ('url','epoch','frames','frame_identity'))
                    # A link may open a dialog or update content through
                    # JavaScript without changing its URL.
                    needs_navigation=kind(item)=='link' and item.get('href')!=before_url
                    dom_changed=any(now.get(k)!=before.get(k) for k in ('revision','frame_states'))
                    modal_ready=needs_navigation and dom_changed and frame.evaluate('()=>[...document.querySelectorAll(\'[role="dialog"][aria-modal="true"],dialog[open]\')].some(e=>!e.hidden&&getComputedStyle(e).display!=="none"&&getComputedStyle(e).visibility!=="hidden")')
                    content_changed=dom_changed and (frame.evaluate("()=>document.body?.innerText.slice(0,200000)||''")!=before_text or frame.evaluate(controls_signature)!=before_controls)
                    changed=navigation_changed or content_changed and (not needs_navigation or modal_ready)
                    if changed and (navigation_changed or not pending_requests or time.monotonic()-clicked_at>=3):break
                except Exception:
                    # Navigation can briefly destroy the old JavaScript context.
                    pass
                if kind(item)!='link' and not item.get('href') and time.monotonic()-clicked_at>=.35:
                    if not (key or item.get('in_form')) or time.monotonic()-clicked_at>=3:break
                before_page.wait_for_timeout(50)
            if not changed:
                try:changed=frame.evaluate("()=>document.body?.innerText.slice(0,200000)||''")!=before_text
                except Exception:pass
            if not changed:
                if kind(item)=='link':raise WebError('The link did not finish opening. The current page remains available; the link was not clicked again.')
                dialog_notice=getattr(self,'dialog_notice','')
                return {'unchanged':True,'notice':(dialog_notice+'. ' if isinstance(dialog_notice,str) and dialog_notice else '')+(request_errors[-1]+' ' if request_errors else '')+'The website has not moved to another step.'}
            if request_errors and self.page is before_page and frame.url==before_url:
                try:
                    if frame.evaluate("()=>document.body?.innerText.slice(0,200000)||''")==before_text:
                        return {'unchanged':True,'notice':request_errors[-1]+' The website has not moved to another step.'}
                except Exception:pass
            fragment=None
            if self.page is before_page:
                try:
                    frame.wait_for_load_state('domcontentloaded',timeout=15000)
                    after_url=frame.url
                    same_document=frame.evaluate('()=>performance.timeOrigin')==before_epoch
                    fragment=unquote(urlsplit(after_url).fragment) if same_document and urlsplit(before_url)._replace(fragment='')==urlsplit(after_url)._replace(fragment='') and after_url!=before_url else None
                    if same_document and not fragment and after_url!=before_url:
                        from playwright.sync_api import TimeoutError as BrowserTimeout
                        try:frame.wait_for_function("old=>(document.body?.innerText.slice(0,200000)||'')!==old",arg=before_text,timeout=5000)
                        except BrowserTimeout:pass
                except Exception:pass
            document_changed=self.page is not before_page
            if not document_changed:
                try:document_changed=frame.url!=before_url or frame.evaluate('()=>performance.timeOrigin')!=before_epoch
                except Exception as exc:
                    if not frame.is_detached() and not navigation_interrupted(exc):raise
                    document_changed=True
            if document_changed:self.wait_ready()
            return {'fragment':fragment,'frame_index':item.get('frame_index')}
        finally:
            if self.context:self.context.remove_listener('page',new_page)
            before_page.remove_listener('request',started_request);before_page.remove_listener('requestfinished',finished_request)
            before_page.remove_listener('requestfailed',failed_request);before_page.remove_listener('response',failed_response)

    def focus(self,item):
        if editable_combo(item):self._text_element(item).focus();return
        element=self._element(item);element.focus()
        if kind(item) in ('combobox','listbox') and item.get('tag')!='select' and element.get_attribute('aria-expanded')=='false':
            if not (item.get('tag')=='button' and item.get('type')=='submit' and item.get('in_form')):element.click(timeout=10000)

    def blur_field(self,item):
        if editable_combo(item):
            element=self._text_element(item)
            state=element.evaluate(COMBO_STATE,{})
            if state['expanded'] and (state['focus_in_box'] or state['focus_in_list']):self.page.keyboard.press('Escape')
            element.evaluate('e=>e.blur()');return
        element=self._element(item)
        if kind(item) in ('combobox','listbox') and element.get_attribute('aria-expanded')=='true':element.press('Escape')
        element.evaluate('e=>e.blur()')

    def field_value(self,item):
        return self._text_element(item).evaluate("e=>e.isContentEditable?e.innerText:String(e.value||'')")

    def fill(self,item,value,private=False):
        element=self._text_element(item)
        element.fill(value,timeout=10000)

    def _text_element(self,item):
        """The text box typed into. An ARIA 1.0 combo box owns a separate text box."""
        element=self._element(item)
        key=item.get('combo_input_key')
        if not key or key==item.get('key'):return element
        frame=self.page.frames[item['frame_index']] if 'frame_index' in item else self.page
        inner=frame.locator('[data-voice-companion-key="'+key+'"]')
        if inner.count()!=1:raise WebError('The page changed. Refresh it before continuing.')
        return inner

    def combo_state(self,item,typed=None,wait=False):
        """Suggestions the site shows for an editable combo box, and which one is active."""
        element=self._text_element(item)
        state=element.evaluate(COMBO_STATE,{'typed':typed})
        # Sites fetch suggestions after typing; give them a moment to appear.
        for _ in range(16 if wait and not state['options'] and not state.get('datalist') and state['value'].strip() else 0):
            self.page.wait_for_timeout(50)
            state=element.evaluate(COMBO_STATE,{'typed':typed})
            if state['options']:break
        return state

    def _combo_focus(self,element,state):
        # Keep keyboard focus where the site put it: in the box, or in its list.
        if not (state['focus_in_box'] or state['focus_in_list']):element.focus()

    def type_combo(self,item,value):
        """Type into the site's box with real keys, so it updates its suggestions."""
        element=self._text_element(item)
        # Called for each typed key, so read the box with one short script.
        current,focused,plain=element.evaluate("e=>{let a=document.activeElement;while(a?.shadowRoot?.activeElement)a=a.shadowRoot.activeElement;return [e.isContentEditable?e.innerText:String(e.value||''),!!a&&(a===e||e.contains(a)),!e.isContentEditable&&typeof e.setSelectionRange==='function'];}")
        if current==value:return False
        if not focused:element.focus()
        if plain and value.startswith(current) and '\n' not in value:
            element.evaluate("e=>e.setSelectionRange(e.value.length,e.value.length)")
            element.press_sequentially(value[len(current):],timeout=10000)
        elif plain and current.startswith(value) and len(current)-len(value)<=40:
            element.evaluate("e=>e.setSelectionRange(e.value.length,e.value.length)")
            for _ in range(len(current)-len(value)):element.press('Backspace',timeout=10000)
        else:element.fill(value,timeout=10000)
        return True

    def combo_move(self,item,key,typed=None):
        """Press Up or Down for the site, then report the suggestion it made active."""
        element=self._text_element(item)
        before=self.combo_state(item,typed,wait=True)
        self._combo_focus(element,before)
        self.page.keyboard.press(key)
        signature=lambda s:(s['active'],s['value'],s['focus_in_box'],s['focus_in_list'],tuple(s['options']))
        state=element.evaluate(COMBO_STATE,{'typed':typed})
        for _ in range(10):
            if signature(state)!=signature(before):break
            self.page.wait_for_timeout(30)
            state=element.evaluate(COMBO_STATE,{'typed':typed})
        return state

    def combo_close(self,item):
        """Escape closes the suggestions when they are open; the field stays active."""
        element=self._text_element(item)
        state=element.evaluate(COMBO_STATE,{})
        if not state['expanded']:return {'closed':False,'value':state['value']}
        self._combo_focus(element,state)
        self.page.keyboard.press('Escape')
        self.page.wait_for_timeout(50)
        return {'closed':True,'value':element.evaluate("e=>e.isContentEditable?e.innerText:String(e.value||'')")}

    def combo_enter(self,item):
        """Enter goes to the site where focus is: a highlighted suggestion or the box."""
        element=self._text_element(item)
        self._combo_focus(element,element.evaluate(COMBO_STATE,{}))
        return self.activate(item,key='Enter',focused=True)

    def page_key_state(self,drain=False):
        """The focused element and new page announcements, for app mode speech."""
        state=self.page.evaluate(PAGE_KEY_STATE,{'drain':drain})
        focus=state.get('focus')
        if focus and focus.get('tag') in ('iframe','frame'):
            # Keys typed into a frame go to the element focused inside it.
            for frame in self.page.frames[1:]:
                try:inner=frame.evaluate(PAGE_KEY_STATE,{'drain':False})
                except Exception:continue
                if inner.get('focus') and inner.get('has_focus') and inner['focus'].get('tag') not in ('iframe','frame'):
                    state['focus']=inner['focus'];break
        return state

    def press_page_key(self,key,focus=None):
        """App mode: one real key press for the page, and what it changed.

        Two quick page reads and the key itself; nothing waits. A site that
        answers a moment later is heard at the next check (WebSession.app_changes).
        focus is an element to give keyboard focus first (the browse cursor's
        element, for the first key after switching to app mode)."""
        self._watch_dialogs()
        if focus is not None:
            try:self.focus_for_keys(focus)
            except Exception:pass
        before=self.page_key_state(drain=True)
        self.page.keyboard.press(key)
        return before,self.page_key_state(drain=True)

    def focus_for_keys(self,item):
        """Give keyboard focus to an element without clicking it."""
        element=self._text_element(item) if editable_combo(item) else self._element(item)
        element.focus()

    def focused_key(self):
        """The Companion key of the element that has keyboard focus, if any."""
        return self.page.evaluate("()=>{let e=document.activeElement;while(e?.shadowRoot?.activeElement)e=e.shadowRoot.activeElement;return e&&e!==document.body?e.getAttribute('data-voice-companion-key'):null;}")

    def slider_key(self,item,key):
        """Press an arrow, Page or Home/End key on a slider and read its new value."""
        element=self._element(item)
        read="e=>({value_text:e.getAttribute('aria-valuetext')||'',value_now:e.getAttribute('aria-valuenow')??(['range','number'].includes((e.type||'').toLowerCase())?e.value:null)})"
        before=element.evaluate(read)
        element.press(key,timeout=5000)
        after=element.evaluate(read)
        for _ in range(8):
            if after!=before:break
            self.page.wait_for_timeout(25)
            after=element.evaluate(read)
        return after

    def activate_choice(self,item):
        """One user action sends one native activation, with no state retries."""
        element=self._element(item)
        if element.get_attribute('aria-busy')=='true':return 'This control is busy. Wait for its result.'
        # Native choices use trusted Space so overlapping labels and detail
        # buttons cannot intercept a center-point click. Activate only once.
        keyboard=element.evaluate("""e=>e.matches('input[type="radio"],input[type="checkbox"]')||(!e.matches('input,button')&&!!e.closest('[role="radio"],[role="checkbox"]')&&
          [e,...(function*(n){while(n.parentElement){n=n.parentElement;yield n;}})(e)].some(n=>typeof n.onkeydown==='function'||typeof n.onkeyup==='function'||n.hasAttribute('onkeydown')||n.hasAttribute('onkeyup')))""")
        before=element.evaluate("e=>({checked:'checked' in e?e.checked:e.getAttribute('aria-checked'),busy:e.getAttribute('aria-busy')})")
        if item.get('tag')=='input' and item.get('type')=='radio' and before['checked'] is True:return ''
        if keyboard:element.press('Space',timeout=2000)
        else:element.click(timeout=2000)
        # Wait for delayed rendering without activating the control again.
        try:
            delay=600 if re.search(r'robot|human|verif|captcha',_label(item),re.I) else 150
            element.evaluate("""(e,arg)=>new Promise(resolve=>{const start=Date.now();let last=JSON.stringify(arg.before),changed=false,changedAt=start;const poll=()=>{
              const state={checked:'checked' in e?e.checked:e.getAttribute('aria-checked'),busy:e.getAttribute('aria-busy')};
              const now=Date.now(),value=JSON.stringify(state);
              if(value!==last){last=value;changed=true;changedAt=now;}
              if(!e.isConnected||(changed&&now-start>=arg.delay&&now-changedAt>=120)||now-start>=1000)resolve();
              else setTimeout(poll,25);};poll();})""",{'before':before,'delay':delay})
        except Exception:pass # An activation may replace its frame or document.
        return ''

    def choose_form_option(self, item, answer):
        element=self._element(item)
        if item['tag'] == 'select':
            options = [o for o in item.get('options', []) if o['label'].casefold() == answer.casefold() and not o.get('disabled')]
            if len(options) != 1:
                raise WebError('I could not identify one option. Ask me to repeat the choices.')
            element.select_option(value=options[0]['value'], timeout=10000)
        elif item.get('role') in ('combobox','listbox'):
            candidates=element.locator('[role="option"]')
            owns=element.get_attribute('aria-controls') or element.get_attribute('aria-owns') or ''
            if owns:
                frame=self.page.frames[item['frame_index']] if 'frame_index' in item else self.page
                candidates=frame.locator(','.join('[id='+json.dumps(name,ensure_ascii=False)+'] [role="option"]' for name in owns.split()))
            matches=[candidates.nth(i) for i in range(candidates.count()) if candidates.nth(i).inner_text().strip().casefold()==answer.casefold() and candidates.nth(i).get_attribute('aria-disabled')!='true']
            if len(matches)!=1:raise WebError('I could not identify one option.')
            matches[0].click(timeout=10000)
        elif item['type'] in ('checkbox', 'radio'):
            if answer not in ('yes', 'no'):
                raise WebError('Say yes or no for this choice.')
            if item['type'] == 'radio' and answer == 'no':
                return
            if element.is_checked() != (answer == 'yes'):self.activate_choice(item)
        elif item.get('role') in ('checkbox','radio'):
            if answer not in ('yes','no'):raise WebError('Say yes or no for this choice.')
            if item.get('role')=='radio' and answer=='no':return
            checked=element.get_attribute('aria-checked')=='true'
            if checked!=(answer=='yes'):self.activate_choice(item)
        else:
            raise WebError('That is not a choice field.')


EdgeBackend = BrowserBackend


# "Use Chrome", "switch to embedded Edge", and the removed browsers, which are explained.
BROWSER_REQUEST=(r'(?:use|switch to|choose) (?:the )?(?:browser )?(embedded edge|edge webview2|edge webview|webview2|webview 2|web view 2|web view two|webview two|'
                 r'google chrome|chrome|microsoft edge|edge|brave browser|brave|mozilla firefox|fire fox|firefox)(?: browser)?')


SKIP_AD=re.compile(r'skip (?:the |this )?(?:ads?|advert|advertisement)s?')


def skip_ad_button(snapshot):
    """The video ad's Skip button, when the page shows one."""
    host=(urlsplit(str((snapshot or {}).get('url',''))).hostname or '').lower()
    video_site=host=='youtube.com' or host.endswith('.youtube.com')
    for item in (snapshot or {}).get('elements',[]):
        if kind(item)!='button' or item.get('disabled'):continue
        label=re.sub(r'\s+',' ',str(item.get('label',''))).strip().rstrip('.!').lower()
        if SKIP_AD.fullmatch(label) or video_site and label=='skip':return item
    return None


SKIP_AD_READY='Skip ad button ready. Say skip ad to press it.'


class RefreshPacer:
    """When the main loop next looks for page changes.

    A quick check repeats every 0.3 seconds. Reading a busy page again (a
    video page with a moving clock) can take a large part of a second, and keys
    and speech wait while it runs. So after a slow read, wait three times as
    long before the next one (at most two seconds), and leave room after a
    key press.
    """
    def __init__(self,interval=.3,factor=3,after_key=.12,longest=2.0):
        self.interval,self.factor,self.after_key,self.longest=interval,factor,after_key,longest
        self.next_at=0.0;self.cost=0.0

    def due(self,now,last_navigation=0.0):
        return now>=self.next_at and now-last_navigation>max(self.after_key,min(self.longest,2*self.cost))

    def done(self,started,finished):
        self.cost=max(0.0,finished-started)
        self.next_at=max(started+self.interval,finished+min(self.longest,self.factor*self.cost))


# Elements that take keys at once when app mode starts on them.
APP_FOCUS_KINDS=('link','button','edit','combobox','listbox','checkbox','radio','slider','spinbutton','switch','tab',
                 'menuitem','menuitemcheckbox','menuitemradio','treeitem','option')


class WebSession:
    def __init__(self, folder, backend=None, progress=None):
        self.progress=progress or (lambda text:None)
        self.favorites = Favorites(folder)
        from browser_menu import BrowserMenu
        self.menu = BrowserMenu(folder)
        self.folder = Path(folder)
        self.browser = self._browser_preference()
        from connected_browser import create_backend
        self.backend = backend or create_backend(self.folder / ('Browser Profile ' + self.browser), self.browser)
        if getattr(self.backend,'regular',False):self.browser=self.backend.browser
        self.injected_backend = backend is not None
        self.page_revision = 0
        self.navigation_succeeded = False
        self.snapshot = None
        self.offset = 0
        self.choices = []
        self.pending = None
        self.suggestion_index = None
        self.form_fields = []
        self.form_index = None
        self.form_answers = []
        self.form_page = None
        self.submit_pending = None
        self.private_fields_confirmed = False
        self.review_parts = []
        self.review_cursor = 0
        self.review_submission = None
        self.last_search = ''
        self.last_focus = None
        self.link_offset = 0
        self.link_position = None
        self.list_focus = None
        self.favorite_items = []
        self.favorite_index = 0
        self.field_list_index = 0
        self.input_mode = 'mixed'
        self.reading = ReadingCursor()
        self.browse=None;self.keyboard_field=None;self.auto_read=True;self.document_state=None;self.cursor_revision=0;self.search_engine="google"
        # App mode is remembered per site until leaving the web screen.
        self.app_mode_hosts=set();self.slider_field=None;self.mode_announced=False
        self.app_focus_pending=None;self.app_state=None;self.refresh_now=False

    def address_allowed(self,url):
        try:
            parsed=urlsplit(url)
            return parsed.scheme in ('http','https') and bool(parsed.hostname) and not parsed.username and not parsed.password
        except ValueError:return False

    def cursor_changed(self,scroll=False):
        self.cursor_revision+=1;self.selection=None
        item=self.browse.current() if self.browse else None
        if scroll and item:self.pending_scroll=item

    def flush_scroll(self):
        item=getattr(self,'pending_scroll',None);self.pending_scroll=None
        if item and hasattr(self.backend,'scroll_to'):
            try:self.backend.scroll_to(item)
            except Exception:pass

    def read_key(self,key):
        control=key.startswith('Control:');key=key.split(':')[-1]
        text=self.reading.text;position=self.reading.position
        direction=-1 if key in ('Up','Left','Prior') else 1
        if key in ('Home','End'):
            start=text.rfind('\n',0,position)+1;end=text.find('\n',position)
            if end<0:end=len(text)
            self.reading.position=start if key=='Home' else end
        elif key in ('Left','Right'):
            if control:
                starts=[m.start() for m in re.finditer(r'\S+',text)]
                candidates=[x for x in starts if x<position] if direction<0 else [x for x in starts if x>position]
                self.reading.position=(candidates[-1] if direction<0 else candidates[0]) if candidates else 0 if direction<0 else len(text)
            else:self.reading.position=max(0,min(len(text),position+direction))
        elif control:
            from keyboard_text import paragraph_destination
            self.reading.position=paragraph_destination(text,position,direction)
        else:
            from bisect import bisect_right
            if 'web:line_starts' not in self.reading._cache:self.reading._cache['web:line_starts']=[0]+[m.end() for m in re.finditer('\n',text)]
            starts=self.reading._cache['web:line_starts']
            index=max(0,bisect_right(starts,position)-1)
            column=getattr(self,'preferred_column',None)
            if column is None:column=position-starts[index]
            self.preferred_column=column
            index=max(0,min(len(starts)-1,index+direction*(20 if key in ('Prior','Next') else 1)))
            end=text.find('\n',starts[index]);end=len(text) if end<0 else end
            # Browse blocks start at their first character, so a short control
            # remains the active target after a longer paragraph.
            self.reading.position=starts[index]
        if key not in ('Up','Down','Prior','Next') or control:self.preferred_column=None
        self.reading.continuation=self.reading.position
        self.browse.set_position(self.reading.position);self.cursor_changed()
        from keyboard_text import caret_feedback
        message=caret_feedback(text,self.reading.position,key,control)[0] or 'Blank.'
        if key in ('Up','Down','Prior','Next'):
            item=self.browse.current()
            if item and item.get('start',0)<=self.reading.position<item.get('end',0):
                role=kind(item)
                if role=='link':message='Link '+message
                elif role=='heading':message+=' heading level '+str(item.get('level',1))+'.'
            self.auto_focus_position()
        return message

    def focus_current(self):
        item=self.browse.current() if self.browse else None
        if item and not item.get('disabled') and kind(item)=='slider':
            # Tab onto a slider: its arrow keys work at once, as for a text box.
            self.slider_field=None;self.begin_slider(item)
            return self.slider_field is not None
        if not item or item.get('disabled') or kind(item) not in ('edit','combobox','listbox'):return False
        self.backend.focus(item)
        if choice_field(item) and item.get('tag')!='select':
            self._refresh_at(item);item=self.browse.current() or item
        field=dict(item);field['_page_url']=self.snapshot.get('url');field['_document_epoch']=(self.document_state or {}).get('epoch')
        self.combo_choice=None
        if text_field(item):
            value=self.backend.field_value(item) if hasattr(self.backend,'field_value') else item.get('value','')
            if not isinstance(value,str):
                if item.get('value_truncated'):raise WebError('This field is too long for this browser integration.')
                value=item.get('value','')
            field['value']=value;field['_original_value']=value
        else:field['value']=item.get('selected_label','')
        self.keyboard_field=field;self.web_edit_caret=0;self.selection=None;self._web_field_selection=None;self.combo_seen=()
        return True

    def auto_focus_position(self):
        item=self.browse.current() if self.browse else None
        if not item or kind(item)!='edit' or item.get('disabled') or item.get('readonly'):return False
        if not item.get('start',0)<=self.reading.position<item.get('end',0):return False
        return self.focus_current()

    def page_boundary(self,bottom=False):
        if self.keyboard_field and self.keyboard_field.get('type')=='password':return 'Save the private field with Tab, or cancel field editing before moving to the page.'
        if self.keyboard_field:self.commit_keyboard_field()
        self.list_focus=None;self.pending=None;self.submit_pending=None;self.form_index=None;self.preferred_column=None
        self.reading.position=max(0,len(self.reading.text)-1) if bottom else 0
        self.reading.continuation=self.reading.position
        self.browse.set_position(self.reading.position)
        message=self.reading.move('line',0)
        self.reading.position=len(self.reading.text) if bottom else 0;self.reading.continuation=self.reading.position
        self.browse.set_position(self.reading.position);self.cursor_changed()
        return ('Bottom of page. ' if bottom else 'Top of page. ')+message

    def leave_field(self,direction):
        item=self.keyboard_field
        if not item:return self.keyboard(direction)
        self.commit_keyboard_field()
        item=self.browse.current() or item
        self.reading.position=item.get('start',0) if direction in ('Up','Left') else max(item.get('start',0),item.get('end',0)-1)
        self.reading.continuation=self.reading.position
        message=self.reading.move('character' if direction in ('Left','Right') else 'line',-1 if direction in ('Up','Left') else 1)
        self.browse.set_position(self.reading.position)
        self.auto_focus_position()
        return message

    def choice_key(self,key):
        item=self.keyboard_field;options=[o for o in item.get('options',[]) if not o.get('disabled')]
        if not options:return 'No available options. Tab moves to the next control.'
        current=next((i for i,o in enumerate(options) if o['label']==item.get('selected_label')),-1)
        if key.startswith('ChoiceLetter:'):
            letter=key.split(':',1)[1].casefold()
            matches=[i for i,o in enumerate(options) if o['label'].casefold().startswith(letter)]
            index=matches[min(matches.index(current)+1,len(matches)-1)] if current in matches else matches[0] if matches else None
            if index==current:return 'End of matching items.'
            if index is None:return 'End of matching items.' if any(o['label'].casefold().startswith(letter) for o in options) else 'No option starts with '+letter.upper()+'.'
        elif key=='Home':index=0
        elif key=='End':index=len(options)-1
        elif key in ('Up','Down','Left','Right'):index=max(0,min(len(options)-1,current+(-1 if key in ('Up','Left') else 1)))
        else:return item.get('selected_label','')+'.'
        label=options[index]['label']
        if index==current and key in ('Up','Down','Left','Right','Home','End'):return 'Start of list.' if key in ('Up','Left','Home') else 'End of list.'
        from menu_navigation import boundary_notice
        boundary=boundary_notice(index,len(options))
        if index!=current:
            self.backend.choose_form_option(item,label)
            self._refresh_at(item);self.focus_current()
        return label+'.'

    def combo_typed(self):
        """Send an editable combo box's text to the site, so it shows its suggestions.

        Returns a spoken problem, or an empty string when the site has the text."""
        item=self.keyboard_field
        if not editable_combo(item) or item.get('readonly') or not hasattr(self.backend,'type_combo'):return ''
        try:
            if self.backend.type_combo(item,item.get('value','')):
                self.combo_choice=None;self.combo_typed_at=__import__('time').monotonic()
        except WebError as exc:return str(exc)
        except Exception:return 'The website did not get that text. Press F5 to refresh the page.'
        return ''

    def _combo_value(self,value):
        item=self.keyboard_field
        if isinstance(value,str) and item is not None and value!=item.get('value'):
            item['value']=value;self.web_edit_caret=len(value);self.selection=None;self._web_field_selection=None

    def _combo_entered(self):
        try:options=tuple(self.backend.combo_state(self.keyboard_field,wait=True).get('options') or ())
        except Exception:options=()
        self.combo_seen=options;count=len(options)
        if not count:return 'Text entered. Press Enter to submit, or Tab to move on.'
        return 'Text entered. '+str(count)+(' suggestion' if count==1 else ' suggestions')+'. Press Down arrow or say next suggestion to hear them. Press Enter to submit.'

    def combo_key(self,key):
        """Up and Down move through the suggestions of an editable combo box."""
        item=self.keyboard_field
        notice=self.combo_typed()
        if notice:return notice
        if item.get('datalist'):return self._datalist_key(key)
        state=self.backend.combo_move(item,'ArrowDown' if key=='Down' else 'ArrowUp',item.get('value',''))
        self._combo_value(state.get('value'))
        options=state.get('options') or [];active=state.get('active');self.combo_seen=tuple(options)
        if isinstance(active,int) and 0<=active<len(options):return options[active]+', '+str(active+1)+' of '+str(len(options))+'.'
        if not options:return 'No suggestions. Keep typing, or press Tab to move on.'
        return 'No suggestion selected. Field text: '+(item.get('value') or 'blank')+'.'

    def _datalist_key(self,key):
        # A page's datalist is drawn by the browser, not the page, so its
        # options are offered here; the field changes only when one is chosen.
        options=self.backend.combo_state(self.keyboard_field).get('options') or []
        if not options:return 'No suggestions. Keep typing, or press Tab to move on.'
        index=getattr(self,'combo_choice',None)
        if index is None or index>=len(options):index=0 if key=='Down' else len(options)-1
        else:
            index+=1 if key=='Down' else -1
            if not 0<=index<len(options):return 'End of list.' if key=='Down' else 'Start of list.'
        self.combo_choice=index;self.combo_options=options
        return options[index]+', '+str(index+1)+' of '+str(len(options))+'.'

    def combo_escape(self):
        item=self.keyboard_field
        if item.get('datalist'):
            if getattr(self,'combo_choice',None) is not None:self.combo_choice=None;return 'Suggestions closed.'
        else:
            notice=self.combo_typed()
            if notice:return notice
            state=self.backend.combo_close(item)
            if state.get('closed'):
                self._combo_value(state.get('value'));return 'Suggestions closed.'
        # Canceling puts back the text the field had before editing.
        try:
            self.backend.type_combo(item,item.get('_original_value',''))
            self.backend.blur_field(item)
        except Exception:pass
        self.keyboard_field=None;self.combo_choice=None
        return 'Field editing canceled. Press Escape again for the web browser menu.'

    def combo_done(self):
        """Enter: the site submits the typed text or uses the highlighted suggestion."""
        item=self.keyboard_field
        notice=self.combo_typed()
        if notice:return notice
        if item.get('datalist') and getattr(self,'combo_choice',None) is not None:
            label=self.combo_options[self.combo_choice];self.combo_choice=None
            self.backend.fill(item,label);self._combo_value(label)
            return label+' entered. Press Enter again to submit, or Tab to move on.'
        before=self._stamp();page=getattr(self.backend,'page',None)
        self.progress('Loading page.')
        action=self.backend.combo_enter(item)
        if isinstance(action,dict) and action.get('validation'):return action['validation']
        after=self._stamp()
        same=bool(before and after) and getattr(self.backend,'page',None) is page and all(before.get(k)==after.get(k) for k in ('url','epoch'))
        if same and hasattr(self.backend,'field_exists') and self.backend.field_exists(item):
            # The site used the suggestion in the box and stayed on this page.
            value=self.backend.field_value(item)
            self._refresh_at(item);self._combo_value(value)
            item['_original_value']=item.get('value','')
            return (value or 'Blank')+'.'
        self.keyboard_field=None
        return self._refresh(wait=False)

    def _host(self):
        try:return (urlsplit((self.snapshot or {}).get('url','')).hostname or '').lower()
        except ValueError:return ''

    @property
    def app_mode(self):
        """App mode sends keys to the page. Each site keeps its own choice."""
        return bool(self.snapshot) and self._host() in getattr(self,'app_mode_hosts',())

    def set_app_mode(self,request):
        """Switch to 'app' or 'browse' mode, or 'toggle' between them."""
        if not self.snapshot:return 'Open a website first.'
        if self.form_index is not None:return 'Finish the form questions first, or say cancel form.'
        self.list_focus=None;self.pending=None
        interacting=bool(self.keyboard_field or getattr(self,'slider_field',None))
        if request=='toggle':request='browse' if self.app_mode or interacting else 'app'
        if self.keyboard_field:
            try:self.commit_keyboard_field()
            except WebError as exc:return str(exc)
        self.slider_field=None
        if request=='browse':
            was_app=self.app_mode
            self.app_mode_hosts.discard(self._host());self.mode_announced=False
            return BROWSE_MODE+(self._browse_at_focus() if was_app else '')
        self.app_mode_hosts.add(self._host());self.mode_announced=True
        # Keys go where the browse cursor is, as they would for a mouse user.
        # Focus moves with the first key, so switching modes itself is instant.
        item=self.browse.current() if self.browse else None
        self.app_focus_pending=item if item and not item.get('disabled') and kind(item) in APP_FOCUS_KINDS else None
        self.app_state=None
        return APP_MODE

    def _browse_at_focus(self):
        """After app mode, put the browse cursor where keyboard focus ended.

        The element is found in the page view Companion already has, so this
        is one quick question to the page. The view itself catches up at the
        next check (refresh_now asks for that check at once)."""
        self.app_focus_pending=None;self.app_state=None;self.refresh_now=True
        try:key=self.backend.focused_key()
        except Exception:key=None
        if not key:return ''
        find=lambda:next((e for e in self.browse.elements if e.get('key')==key and 'frame_index' not in e),None) if self.browse else None
        item=find()
        if not item:
            # An element the page made while keys went to it: read the page now.
            try:self._refresh(read=False,preserve=True,wait=False)
            except Exception:return ''
            item=find()
            if not item:return ''
        self.browse.index=self.browse.elements.index(item);self.browse.position=item.get('start',0)
        self.reading.position=self.reading.continuation=self.browse.position;self.preferred_column=None;self.cursor_changed(True)
        return ' '+describe(item)

    def pass_key(self,key):
        """App mode: the key goes to the page; say what changed."""
        if not self.app_mode:return ''  # A key typed just before leaving app mode.
        focus=getattr(self,'app_focus_pending',None);self.app_focus_pending=None
        try:before,after=self.backend.press_page_key(key,focus) if focus else self.backend.press_page_key(key)
        except Exception:return 'The page did not take that key. Press F5 to refresh the page.'
        self.app_state=after
        from web_modes import page_key_feedback
        notice=page_key_feedback(key,before,after)
        dialog=getattr(self.backend,'dialog_notice','')
        if isinstance(dialog,str) and dialog:self.backend.dialog_notice='';notice=(dialog+'. '+notice).strip()
        return notice

    def app_changes(self):
        """App mode check between keys: what the page said or where its focus went.

        The page view is not read again while keys go to the page; a busy page
        (a video's clock, new comments) would hold up every key."""
        if not hasattr(self.backend,'page_key_state'):return ''
        try:state=self.backend.page_key_state(drain=True)
        except Exception:return ''
        before=getattr(self,'app_state',None);self.app_state=state
        from web_modes import page_change_feedback
        return page_change_feedback(before,state)

    def begin_slider(self,item):
        """Arrow keys now change this slider on the page, until Escape or Tab."""
        try:self.backend.focus_for_keys(item)
        except WebError as exc:return str(exc)
        except Exception:return 'The slider could not be used. Press F5 to refresh the page.'
        self.slider_field=dict(item)
        return describe(item)

    def slider_key(self,key):
        from web_modes import SLIDER_KEYS,slider_value
        item=self.slider_field
        try:item.update(self.backend.slider_key(item,SLIDER_KEYS[key]))
        except WebError as exc:self.slider_field=None;return str(exc)
        except Exception:self.slider_field=None;return 'The slider changed. Press F5 to refresh the page.'
        current=self.browse.current() if self.browse else None
        if current and current.get('key')==item.get('key'):current.update(value_text=item.get('value_text'),value_now=item.get('value_now'))
        return slider_value(item)+'.'

    def running_browser(self):
        """The browser actually showing pages, which differs from the preference after a fallback."""
        running = getattr(self.backend, 'browser', None)
        return running if isinstance(running, str) and running else self.browser

    def _sync_browser_tab(self):
        if not self.injected_backend and not getattr(self.backend,'regular',False):
            self.adopt_connected_browser()
        sync=getattr(type(self.backend),'sync_active_tab',None)
        if sync and sync(self.backend):
            self.keyboard_field=None;self.form_index=None;self.pending=None;self.submit_pending=None
            self.list_focus=None;self.browse=None
            self._refresh(read=False)

    def keyboard(self,key,direct_submit=False):
        if key=='Refresh':
            # Reload is the recovery route: it must not first capture the same
            # changing document that caused the failed action.
            if not self.snapshot:return 'Open a website first.'
            address=self.snapshot.get('url','')
            old_page=getattr(self.backend,'page',None)
            self.pending=None;self.submit_pending=None;self.review_submission=None
            try:
                if hasattr(self.backend,'start'):self.backend.start()
                sync=getattr(type(self.backend),'sync_active_tab',None)
                if sync:sync(self.backend)
                page=self.backend.page
                if page is not old_page and page.url=='about:blank' and self.address_allowed(address):
                    page.goto(address,wait_until='domcontentloaded',timeout=30000)
                else:page.reload(wait_until='domcontentloaded',timeout=30000)
                return self._refresh()
            except Exception:
                return 'The page reload did not finish. Wait a moment, then press F5 again, or return to the browser menu.'
        try:self._sync_browser_tab()
        except Exception:return 'The browser connection is not ready. Keep Voice Companion open; it will reconnect automatically.'
        if not self.snapshot:return 'Open a website first.'
        if self.browse is None:self.browse=BrowseCursor(self.snapshot)
        try:
            if key in ('ToggleAppMode','AppMode','BrowseMode'):return self.set_app_mode({'ToggleAppMode':'toggle','AppMode':'app','BrowseMode':'browse'}[key])
            if key.startswith('Pass:'):return self.pass_key(key[5:])
            if getattr(self,'slider_field',None):
                if key.startswith('Slider:'):return self.slider_key(key[7:])
                if key=='Escape':self.slider_field=None;return BROWSE_MODE
                if key in ('Activate','Space','Done'):return describe(self.slider_field)
                self.slider_field=None
            elif key.startswith('Slider:'):key='Read:'+key[7:]  # Typed just after leaving the slider.
            if key.startswith('ListText:'):return self.list_name_input(key[9:])
            if key=='ListBackspace':return self.list_name_input('',backspace=True)
            if key.startswith('Read:'):return self.read_key(key[5:])
            if key in ('PageTop','PageBottom'):return self.page_boundary(key=='PageBottom')
            if key.startswith('Leave:'):return self.leave_field(key.split(':')[1])
            if self.keyboard_field and editable_combo(self.keyboard_field) and key in ('Up','Down'):return self.combo_key(key)
            if self.keyboard_field and choice_field(self.keyboard_field) and (key in ('Up','Down','Left','Right','Home','End','Activate','Space','Done') or key.startswith('ChoiceLetter:')):return self.choice_key(key)
            if self.keyboard_field and key.startswith(('Quick:','Table:')):self.commit_keyboard_field()
            if key.startswith('Quick:'):
                parts=key.split(':');old_index=self.browse.index;message=self.browse.move(parts[-1],len(parts)>2)
                if parts[-1]=='p' and self.browse.index!=old_index:
                    item=self.browse.current();message=self.snapshot.get('text','')[item.get('start',0):item.get('end',0)].strip() or message
            elif key.startswith('Table:'):message=self.browse.table_move(key.split(':')[-1])
            elif key in ('Tab','ShiftTab'):
                if self.keyboard_field:self.commit_keyboard_field()
                old_index=self.browse.index;item=self.browse.current()
                message=self.browse.move('',key=='ShiftTab',tab=True)
                if self.browse.index==old_index:
                    self.reading.position=item.get('start',0) if item else 0
                    text=self.reading.move('line',-1 if key=='ShiftTab' else 1)
                    self.browse.set_position(self.reading.position)
                    return message+' '+text
            elif key=='Back':return self.history(-1)
            elif key=='Forward':return self.history(1)
            elif key=='Done':
                field=self.keyboard_field
                if field and editable_combo(field):return self.combo_done()
                if field:
                    self.commit_keyboard_field()
                    if field.get('tag')!='textarea' and not field.get('editable') and hasattr(self.backend,'submit_default'):
                        self.progress('Loading page.')
                        action=self.backend.submit_default(field)
                        message=self._refresh(wait=False)
                        return action.get('validation') or action.get('notice') or message
                return 'Page view.'
            elif key=='Escape':
                if self.keyboard_field and editable_combo(self.keyboard_field):return self.combo_escape()
                if self.keyboard_field:self.keyboard_field=None;return 'Field editing canceled. Press Escape again for the web browser menu.'
                if (self.snapshot or {}).get('modal_key'):
                    self.backend.page.keyboard.press('Escape')
                    result=self._refresh(read=False,wait=False)
                    return ('Dialog remains open. Move to its Close button.' if self.snapshot.get('modal_key') else 'Dialog closed. '+result)
                self.pending=None;self.submit_pending=None;return 'Leave website.'
            elif key in ('Activate','Space'):
                if self.keyboard_field:return describe(self.keyboard_field)
                item=self.browse.current()
                if not item:return 'Move to a link or control first.'
                role=kind(item)
                if role=='heading':
                    linked=next((e for e in self.browse.elements if e.get('key')==item.get('activation_key') and e.get('frame_index')==item.get('frame_index') and kind(e)=='link'),None)
                    if linked:item=linked;role='link'
                    links=[e for e in self.browse.elements if kind(e)=='link' and e.get('start',0)<=item.get('start',0)<e.get('end',0)]
                    if role=='heading' and links:item=min(links,key=lambda e:e.get('end',0)-e.get('start',0));role='link'
                if role=='slider' and not item.get('disabled'):return self.begin_slider(item)
                if role in ('edit','combobox','listbox'):
                    self.focus_current();return describe(item)
                if role in ('checkbox','radio'):
                    if hasattr(self.backend,'activate_choice'):
                        notice=self.backend.activate_choice(item)
                        if notice:return notice
                    else:self.backend.choose_form_option(item,'no' if role=='checkbox' and item.get('checked') else 'yes')
                    return self._refresh_at(item)
                if role=='combobox':return describe(item)+' Use Up or Down to change the selection.'
                if role=='button' and skip_ad_button({'url':self.snapshot.get('url',''),'elements':[item]}):return self.skip_ad()
                if role in ('button','menuitem','menuitemradio','menuitemcheckbox'):
                    self.progress('Loading page.')
                    action=self.backend.activate(item)
                    message=self._refresh(wait=False,read=False if isinstance(action,dict) and action.get('validation') else None)
                    return (action.get('validation') or action.get('notice') or message) if isinstance(action,dict) else message
                if role in ('link','button'):
                    if item.get('href') and not self.address_allowed(item['href']):return 'That link does not use an HTTP or HTTPS page address.'
                    if role=='link':self.pending=item;return self.confirm()
                    self.choices=[item];return self.choose(1)
                return describe(item)
            else:return self.command({'Up':'previous line','Down':'next line','Left':'previous character','Right':'next character','Home':'read from beginning','End':'read current line'}.get(key,'read current line'))
            self.reading.position=self.browse.position;self.reading.continuation=self.browse.position;self.preferred_column=None;self.cursor_changed(True)
            if key in ('Tab','ShiftTab') or key.startswith('Quick:') and key.split(':')[-1]=='e':self.focus_current()
            return message
        except Exception as exc:
            self.cursor_changed()
            return str(exc) if isinstance(exc,WebError) else 'The page changed or the control could not be used. Press F5 to refresh it.'

    def _refresh_at(self,item):
        previous={(e.get('frame_url'),e.get('key'),e.get('label')) for e in (self.snapshot or {}).get('elements',[])}
        snapshot,state=self._capture()
        if snapshot.get('text','')!=self.reading.text:self.page_revision=getattr(self,'page_revision',0)+1
        self.snapshot=snapshot;self.reading.set_text(snapshot.get('text',''))
        self.document_state=state
        self.browse=BrowseCursor(snapshot)
        target=next((e for e in self.browse.elements if e.get('key')==item.get('key') and e.get('frame_index')==item.get('frame_index') and e.get('tag')==item.get('tag')),None)
        if target:
            self.browse.index=self.browse.elements.index(target);self.browse.position=target.get('start',0);self.reading.position=self.browse.position;self.cursor_changed()
            notice=describe(target)
            if kind(item)=='checkbox' and re.search(r'robot|human|verif|captcha',_label(item),re.I):
                new_controls=[e for e in snapshot.get('elements',[]) if kind(e) in ('edit','button','checkbox','radio','combobox') and (e.get('frame_url'),e.get('key'),e.get('label')) not in previous]
                if new_controls:notice+=' Verification options opened. Use Tab to reach the next control.'
            return notice
        return 'The page changed. Press F5 to refresh.'

    def commit_keyboard_field(self,value=None):
        item=self.keyboard_field
        if item is None:return
        state=self._stamp()
        if state and (state.get('url')!=item.get('_page_url') or item.get('_document_epoch') is not None and state.get('epoch')!=item['_document_epoch']):
            self.keyboard_field=None;raise WebError('The page changed. The old field edit was not saved or submitted.')
        text=item.get('value','') if value is None else value
        if item.get('readonly') and text!=item.get('_original_value'):raise WebError('This field is read-only. Its contents were not changed.')
        if kind(item)=='edit' and text!=item.get('_original_value'):
            self.backend.fill(item,text,private=item.get('type')=='password')
        elif editable_combo(item) and hasattr(self.backend,'type_combo'):self.backend.type_combo(item,text)
        if hasattr(self.backend,'blur_field'):self.backend.blur_field(item)
        self.keyboard_field=None;self.submit_pending=None;self.review_submission=None;self._refresh_at(item)

    def insert_line_break(self):
        if self.keyboard_field:
            item=self.keyboard_field
            if item.get('readonly'):return 'This field is read-only.'
            if item.get('type')=='password' or editable_combo(item) or not (item.get('tag')=='textarea' or item.get('editable')):return 'This field is single-line.'
            self._insert_field_text('\n')
            return 'New line.'
        if not self.snapshot: return 'Open a website first.'
        try: return self.backend.insert_line_break()
        except Exception: return 'I could not insert the line break. Focus a multiline field and try again.'

    def select_focused(self, spoken):
        if not self.snapshot: return 'Open a website first.'
        if self.keyboard_field and text_field(self.keyboard_field) and self.keyboard_field.get('type')!='password':
            if self.keyboard_field.get('readonly') and spoken.lower().strip() in ('scratch','scratch that'):return 'This field is read-only. Its contents were not changed.'
            model=getattr(self,'_web_field_selection',None)
            if model is None:self._web_field_selection=model=FieldSelection()
            value=self.keyboard_field.get('value','')
            span=getattr(self,'selection',None) or (getattr(self,'web_edit_caret',0),)*2
            result,selected=model.select(value,spoken,*span)
            if model.edited_text is not None:
                self.keyboard_field['value']=model.edited_text
                notice=self.combo_typed()
                if notice:return notice
            self.selection=selected if selected and selected[0]!=selected[1] else None
            if selected:self.web_edit_caret=selected[1]
            return result
        try: return self.backend.select_focused(spoken)
        except Exception: return 'I could not select text. Focus the browser field and try again.'

    def _insert_field_text(self,text):
        value=self.keyboard_field.get('value','')
        caret=max(0,min(len(value),getattr(self,'web_edit_caret',len(value))))
        start,end=getattr(self,'selection',None) or (caret,caret)
        start=max(0,min(len(value),start));end=max(start,min(len(value),end))
        self.keyboard_field['value']=value[:start]+text+value[end:]
        self.web_edit_caret=start+len(text);self.selection=None

    def dictate_to_focus(self, spoken, literal=False):
        self.last_dictated_text=None
        if not self.snapshot: return 'Open a website first.'
        self.submit_pending = None
        self.review_submission = None
        if not self.keyboard_field and self.browse and self.browse.current():
            if not text_field(self.browse.current()):return 'No text field is active. Move to a field and press Enter, or say edit current field.'
            self.focus_current()
        if self.keyboard_field:
            if self.keyboard_field.get('readonly'):return 'This field is read-only. Move to another field to enter text.'
            try:value=_voice_value(self.keyboard_field,spoken,literal)
            except ValueError as exc:return str(exc)
            self._insert_field_text(value)
            self.last_dictated_text='' if self.keyboard_field.get('type')=='password' else value
            if editable_combo(self.keyboard_field):return self.combo_typed() or self._combo_entered()
            return 'Text entered. Say save field or next control.'
        try: return self.backend.dictate_focused(spoken,literal=True) if literal else self.backend.dictate_focused(spoken)
        except Exception: return 'I could not enter that text. Check the browser field before trying again.'

    def leave(self,keep_open=False):
        self.keyboard_field=None;self.pending=None;self.submit_pending=None;self.list_focus=None;self.form_index=None
        # Leaving the web screen always returns to browse mode.
        self.app_mode_hosts=set();self.slider_field=None;self.mode_announced=False
        if not keep_open:
            self.backend.close()
            self.snapshot=None;self.browse=None;self.document_state=None
            self.reading.set_text('',reset=True)

    def resume(self):
        if not self.snapshot or not hasattr(self.backend,'refocus') or not self.backend.refocus():
            self.snapshot=None;return ''
        return self._refresh(read=False,preserve=True)

    def adopt_connected_browser(self):
        # Only a saved choice of embedded Edge ignores a tab linked through the
        # extension. As the default, embedded Edge gives way to the user's own browser.
        saved=self.saved_choice if hasattr(self,'saved_choice') else self._saved_browser()
        if self.injected_backend or (self.browser=='webview2' and saved=='webview2'):return False
        from browser_cdp_relay import current
        from connected_browser import ConnectedBrowserBackend
        relay=current()
        if relay is None:return False
        if not isinstance(self.backend,ConnectedBrowserBackend):
            self.backend.close()
            self.backend=ConnectedBrowserBackend(self.folder/('Browser Profile '+self.browser),self.browser,relay)
            self.browser=self.backend.browser
            self.snapshot=None;self.pending=None;self.submit_pending=None;self.keyboard_field=None;self.form_index=None;self.list_focus=None
        if self.snapshot is None:
            self.backend.start()
            self._refresh(read=False)
        return True

    def _saved_browser(self):
        choice,notice=saved_browser_choice(self.folder)
        if notice:self.browser_notice=notice
        # Kept in memory: the extension check runs before every key and command.
        self.saved_choice=choice
        return choice

    def _browser_preference(self):
        # Embedded Edge (WebView2) is the default: it is owned by Companion and needs
        # no browser extension. A saved choice is always kept.
        return self._saved_browser() or DEFAULT_BROWSER

    def take_browser_notice(self):
        """Said once: a saved choice of a removed browser now uses embedded Edge."""
        notice=getattr(self,'browser_notice','');self.browser_notice=''
        return notice

    BROWSER_ALIASES={'embedded edge':'webview2','edge webview2':'webview2','edge webview':'webview2','webview2':'webview2',
                     'webview 2':'webview2','web view 2':'webview2','web view two':'webview2','webview two':'webview2',
                     'chrome':'chrome','google chrome':'chrome','edge':'edge','microsoft edge':'edge',
                     'brave':'brave','brave browser':'brave','firefox':'firefox','fire fox':'firefox','mozilla firefox':'firefox'}

    def choose_browser(self, name):
        name=self.BROWSER_ALIASES.get(_plain(name).rstrip('.!?').lower(),_plain(name).rstrip('.!?').lower())
        if name in RETIRED_BROWSERS:return retired_browser_notice(name)
        if name not in GUIDED_BROWSERS:return BROWSER_CHOICES
        if self.browser != name or getattr(self.backend,'browser',name)!=name:
            if not self.injected_backend:
                try: self.backend.close()
                except Exception: pass
                from connected_browser import create_backend
                self.backend = create_backend(self.folder / ('Browser Profile ' + name), name)
            self.browser = name
            self.snapshot = None
            self.submit_pending = None
            self.review_submission = None
        if not self.injected_backend:
            self.folder.mkdir(parents=True, exist_ok=True)
            write_text_atomic(self.folder / 'browser-choice.txt', name)
            self.saved_choice=name
        if getattr(self.backend,'regular',False):return 'Browser preference saved. Your connected tab stays in its current browser. To switch browsers, connect a tab in '+browser_name(name)+'.'
        return 'Selected ' + browser_name(name) + '. Say open website followed by the address. This uses a separate browser profile.'

    def _stamp(self):
        state=self.backend.document_stamp() if hasattr(self.backend,'document_stamp') else None
        return state if isinstance(state,dict) else None

    def _capture(self):
        for attempt in range(5):
            try:before=self._stamp();snapshot=self.backend.snapshot();after=self._stamp()
            except Exception as exc:
                if not navigation_interrupted(exc):raise
                if hasattr(self.backend,'page'):self.backend.page.wait_for_timeout(100)
                continue
            if before and after:
                changed=any(before.get(k)!=after.get(k) for k in ('url','epoch'))
                changed=changed or bool(after.get('url') and snapshot.get('url')!=after['url'])
                if changed:
                    if hasattr(self.backend,'wait_ready'):self.backend.wait_ready()
                    continue
                if before.get('frame_identity')!=after.get('frame_identity'):
                    # Only an embedded frame came, went or reloaded, as ads do
                    # every few seconds on video sites. The page itself is
                    # unchanged, so do not wait for it to settle. If frames
                    # keep changing, keep this view; the stored state is the
                    # earlier one, so the next check reads the frames again.
                    if attempt<2:
                        if hasattr(self.backend,'page'):self.backend.page.wait_for_timeout(100)
                        continue
            # Store the pre-capture revision, so a mutation immediately after
            # extraction will trigger another refresh instead of being swallowed.
            return snapshot,before
        raise WebError('The page is still changing. Its virtual view will update when it settles.')

    def _refresh(self,read=None,preserve=False,wait=True):
        # In app mode the page is in use, not being read: say only its title.
        if read is None and self.app_mode:read=False
        self.slider_field=None
        result=self._refresh_page(read,preserve,wait)
        if self.app_mode!=self.mode_announced and self.snapshot:
            # Each site keeps its own mode; say which one this page uses.
            self.mode_announced=self.app_mode
            result=(APP_MODE if self.app_mode else BROWSE_MODE)+' '+result
        notice=self.take_browser_notice()
        return notice+' '+result if notice else result

    def _refresh_page(self,read=None,preserve=False,wait=True):
        old_position=self.reading.position;old_continuation=self.reading.continuation;old_anchor=self.browse.current() if self.browse else None
        if wait and hasattr(self.backend,'wait_ready'):
            for attempt in range(3):
                try:self.backend.wait_ready();break
                except Exception as exc:
                    if not navigation_interrupted(exc) or attempt==2:raise
                    if hasattr(self.backend,'page'):self.backend.page.wait_for_timeout(100)
        leaving=self.snapshot
        snapshot,captured_state = self._capture()
        if leaving and leaving.get('url')!=snapshot.get('url'):
            # Another page: keep the reading place here for Back and Forward.
            self._remember_place()
        self.snapshot=snapshot
        self.pending_scroll=None
        try:self.menu.record(self.snapshot)
        except OSError:pass
        self.preferred_column=None
        self.page_revision=getattr(self,'page_revision',0)+1
        self.cursor_changed()
        # DOMContentLoaded often precedes text inserted by the site's scripts.
        # Wait briefly before declaring a genuinely empty page.
        if not self.snapshot.get('challenge') and not str(self.snapshot.get('text', '')).strip():
            for _ in range(12):
                if not hasattr(self.backend, 'settle'): break
                self.backend.settle()
                self.snapshot,captured_state = self._capture()
                if self.snapshot.get('challenge') or str(self.snapshot.get('text', '')).strip(): break
        self.document_state=captured_state
        self.reading.set_text(self.snapshot.get('text', ''), reset=True)
        self.browse=BrowseCursor(self.snapshot);self.keyboard_field=None
        if preserve:
            self.browse.set_position(min(old_position,len(self.reading.text)))
            if old_anchor:
                index=next((i for i,e in enumerate(self.browse.elements) if e['key']==old_anchor['key'] and e.get('frame_index')==old_anchor.get('frame_index') and e.get('frame_url')==old_anchor.get('frame_url') and e.get('frame_epoch')==old_anchor.get('frame_epoch')),None)
                if index is not None:
                    self.browse.index=index;item=self.browse.current();self.browse.position=min(item.get('end',0),item.get('start',0)+max(0,old_position-old_anchor.get('start',0)))
            self.reading.position=self.browse.position;self.reading.continuation=max(0,min(len(self.reading.text),old_continuation+self.reading.position-old_position))
        self.link_offset = 0
        self.link_position = None
        self.offset = 0
        self.list_focus = None
        self.choices = []
        self.pending = None
        self.suggestion_index = None
        self.form_fields = []
        self.form_index = None
        self.form_answers = []
        self.form_page = None
        self.submit_pending = None
        self.private_fields_confirmed = False
        self.review_parts = []
        self.review_cursor = 0
        self.review_submission = None
        self.review_edit_active=False
        url = self.snapshot['url']
        if self.snapshot.get('challenge'):
            if getattr(self,'active_search_engine',None) and self.last_search and urlsplit(url).hostname in ('www.google.com','google.com','search.brave.com','www.bing.com','bing.com','html.duckduckgo.com','duckduckgo.com','www.duckduckgo.com'):
                self.engine_choices=[e for e in ('brave','bing','duckduckgo','google') if e!=self.active_search_engine]
                self.engine_index=0;self.list_focus='search_engines'
                return 'This search engine requires human verification. Choose another engine for the same search. '+self.engine_notice()+' Use arrows and Enter, or say Try Bing, Try Brave Search, or Try DuckDuckGo. Escape returns to the page. Your default stays unchanged.'
            return 'This site is asking for human verification. Use Tab and Shift Tab to reach its controls. Enter or Space activates the chosen control. Use the site’s accessibility challenge option when available. The website decides whether verification passes.'
        if not self.address_allowed(url):
            return 'Use an HTTP or HTTPS page address.'
        if not str(self.snapshot.get('text', '')).strip():
            return ('I found controls but no readable page text. Say list links or try read page again.'
                    if self.snapshot.get('controls') else
                    'I could not read the page content after waiting for it to load. Try read page again or ask your helper to inspect the browser.')
        dialog_notice=getattr(self.backend,'dialog_notice','')
        if not isinstance(dialog_notice,str):dialog_notice=''
        if dialog_notice:self.backend.dialog_notice=''
        if self.running_browser()=='webview2' and (urlsplit(url).hostname or '').lower()=='accounts.google.com':
            # Google blocks its own sign-in inside embedded browsers such as WebView2.
            dialog_notice=GOOGLE_SIGN_IN_NOTICE+(' '+dialog_notice if dialog_notice else '')
        return ((dialog_notice+'. ' if dialog_notice else '')+('Page loaded: ' if getattr(self.backend,'page_ready',True) else 'Page content ready: ') + _plain(self.snapshot['title']) + '. Site: ' +
                (urlsplit(url).hostname or 'unknown') + '.' + (' '+self.reading.read(True) if (self.auto_read if read is None else read) else ''))

    def refresh_changed(self):
        self.refresh_now=False
        suggestions=self.suggestions_notice()
        return self._refresh_changed() or suggestions

    def suggestions_notice(self,now=None):
        """'New suggestions available.' once when a site's suggestion list changes.

        It waits until typing pauses, so it is not said for every letter. An
        empty list is not announced; Down Arrow says there are no suggestions."""
        import time
        item=self.keyboard_field
        if not editable_combo(item) or item.get('datalist') or not hasattr(self.backend,'combo_state'):
            self.combo_seen=();return ''
        now=time.monotonic() if now is None else now
        if now-getattr(self,'combo_typed_at',0)<.6:return ''
        try:options=tuple(self.backend.combo_state(item).get('options') or ())
        except Exception:return ''
        seen=getattr(self,'combo_seen',())
        self.combo_seen=options
        return 'New suggestions available.' if options and options!=seen else ''

    def _refresh_changed(self):
        if not self.snapshot or not hasattr(self.backend,'document_stamp'):return ''
        notice=getattr(self.backend,'dialog_notice','')
        if not isinstance(notice,str):notice=''
        if notice:self.backend.dialog_notice='';return notice
        stamp=self._stamp()
        if not stamp:return ''
        previous=self.document_state or {}
        if stamp.get('url')!=previous.get('url'):return self._refresh(wait=False)
        if stamp.get('epoch')!=previous.get('epoch'):return self._refresh(read=False,wait=False)
        if self.app_mode:return self.app_changes()
        if self.form_index is not None or self.pending or self.submit_pending:return ''
        if not self.keyboard_field and not getattr(self.backend,'page_ready',True) and stamp.get('ready')=='complete' and not stamp.get('busy'):return self._refresh(read=False,preserve=True,wait=False)
        if all(stamp.get(key)==previous.get(key) for key in ('url','epoch','revision','ready','busy','frames','frame_states')):return ''
        if self.keyboard_field and hasattr(self.backend,'field_exists') and not self.backend.field_exists(self.keyboard_field):return self._refresh(wait=False)
        if not self.keyboard_field and previous.get('ready')!='complete' and stamp.get('ready')=='complete':return self._refresh(read=False,preserve=True,wait=False)
        snapshot,captured_state=self._capture()
        if snapshot.get('modal_key','')!=self.snapshot.get('modal_key',''):
            return self._refresh(read=False,wait=False)
        self.document_state=captured_state
        if not self.keyboard_field and snapshot.get('title')!=self.snapshot.get('title'):return self._refresh(read=False,preserve=True,wait=False)
        position=self.reading.position;continuation=self.reading.continuation
        anchor=self.browse.current() if self.browse else None
        changed=snapshot.get('text','')!=self.reading.text
        # Say once when a video ad's Skip button appears. It lasts only seconds.
        skip_ready=skip_ad_button(snapshot) is not None and skip_ad_button(self.snapshot) is None
        self.snapshot=snapshot;self.reading.set_text(snapshot.get('text',''))
        if changed:
            self.page_revision+=1
            self.list_focus=None;self.link_position=None
        self.browse=BrowseCursor(snapshot);self.browse.set_position(min(position,len(self.reading.text)))
        if anchor and anchor.get('start',0)<=position<=anchor.get('end',0):
            match=next((i for i,e in enumerate(self.browse.elements) if e['key']==anchor['key'] and e.get('frame_index')==anchor.get('frame_index') and e.get('frame_url')==anchor.get('frame_url') and e.get('frame_epoch')==anchor.get('frame_epoch')),None)
            if match is not None:
                self.browse.index=match;target=self.browse.current()
                self.browse.position=min(target.get('end',0),target.get('start',0)+position-anchor.get('start',0))
                continuation+=self.browse.position-position
        if changed and not self.keyboard_field:self.cursor_changed()
        self.reading.position=self.browse.position;self.reading.continuation=min(continuation,len(self.reading.text))
        return SKIP_AD_READY if skip_ready else ''

    def skip_ad(self):
        """Press the video ad's Skip button without moving the reading place."""
        if not self.snapshot:return 'Open a website first.'
        try:self.refresh_changed()
        except Exception:pass
        item=skip_ad_button(self.snapshot)
        if not item:return 'No Skip ad button is showing now.'
        try:action=self.backend.activate(item)
        except WebError:
            try:self._refresh(read=False,preserve=True,wait=False)
            except Exception:pass
            return 'The ad ended before it could be skipped.' if not skip_ad_button(self.snapshot) else 'The Skip ad button could not be pressed. Try again.'
        if isinstance(action,dict) and action.get('validation'):return action['validation']
        self._refresh(read=False,preserve=True,wait=False)
        return 'Ad skipped.' if not skip_ad_button(self.snapshot) else 'The Skip ad button is still showing. Say skip ad again.'

    def history(self,delta):
        """Back (delta -1) or Forward (delta 1): Alt+Left, Alt+Right, the menus and voice.

        The page that shows is read from the browser again, and the reading
        place returns to where it was on that page (the link that was followed)."""
        if not self.snapshot:return 'Open a website first.'
        if self.keyboard_field and self.keyboard_field.get('type')=='password':self.keyboard_field=None
        self.list_focus=None;self.pending=None;self.submit_pending=None;self.review_submission=None
        self.form_index=None;self.keyboard_field=None;self.slider_field=None
        self.progress('Going back.' if delta<0 else 'Going forward.')
        self._remember_place()
        try:
            step=getattr(self.backend,'history_step',None)
            if callable(step):moved=step(delta)
            else:
                (self.backend.back() if delta<0 else self.backend.forward());moved=True
        except WebError as exc:return str(exc)
        except Exception:
            return ('The previous' if delta<0 else 'The next')+' page did not open. Press F5 to refresh the page.'
        if not moved:
            notice='There is no previous page.' if delta<0 else 'There is no next page.'
            stamp=self._stamp();known=self.document_state or {}
            if stamp and any(stamp.get(k)!=known.get(k) for k in ('url','epoch')):
                # The browser showed its blank start page and came back: the
                # page was loaded again, so read it again at the same place.
                try:self._refresh(read=False);self._restore_place()
                except Exception:pass
            return notice
        try:result=self._refresh(read=False)
        except Exception:return 'The page could not be read. Press F5 to refresh the page.'
        place=self._restore_place()
        if place:return result+' '+place
        if self.auto_read and not self.app_mode and result.endswith('.') and 'Site: ' in result and self.reading.text.strip():
            result+=' '+self.reading.read(True)
        return result

    def _remember_place(self):
        """Keep the reading place on this page, to return to it with Back or Forward."""
        if not self.snapshot or not self.browse:return
        url=self.snapshot.get('url')
        item=self.browse.current()
        if not url or not item or not item.get('start',0)<=self.reading.position<=item.get('end',0):
            getattr(self,'places',{}).pop(url,None);return
        same=[e for e in self.browse.elements if e.get('label')==item.get('label') and kind(e)==kind(item) and e.get('frame_url')==item.get('frame_url')]
        places=self.__dict__.setdefault('places',{})
        places.pop(url,None)
        places[url]={'key':item.get('key'),'label':item.get('label'),'kind':kind(item),'frame_url':item.get('frame_url'),
                     'occurrence':next((i for i,e in enumerate(same) if e is item),0),'offset':self.reading.position-item.get('start',0)}
        while len(places)>50:places.pop(next(iter(places)))

    def _restore_place(self):
        """Put the reading place back where it was on this page. Returns what to say, or ''."""
        place=getattr(self,'places',{}).get((self.snapshot or {}).get('url'))
        if not place or not self.browse:return ''
        same=lambda e:e.get('label')==place['label'] and kind(e)==place['kind'] and e.get('frame_url')==place['frame_url']
        elements=self.browse.elements
        target=next((e for e in elements if e.get('key')==place['key'] and same(e)),None)
        if target is None:
            # The page was loaded again, so its elements have new keys.
            matches=[e for e in elements if same(e)]
            target=matches[min(place['occurrence'],len(matches)-1)] if matches else None
        if target is None:return ''
        self.browse.index=elements.index(target)
        self.browse.position=min(target.get('end',0),target.get('start',0)+max(0,place['offset']))
        self.reading.position=self.reading.continuation=self.browse.position;self.preferred_column=None
        self.cursor_changed(True)
        return describe(target)

    def _navigate(self,url):
        try:
            self.backend.goto(url)
            return ''
        except WebError as failure:
            if self.injected_backend or getattr(self.backend,'regular',False):raise
            # The other guided browser: Chrome when embedded Edge cannot start,
            # embedded Edge when Chrome cannot start (Chrome is not installed).
            failed=self.backend.browser
            other='chrome' if failed=='webview2' else 'webview2'
            try:self.backend.close()
            except Exception:pass
            self.backend=guided_backend(self.folder/('Browser Profile '+other),other)
            try:self.backend.goto(url)
            except WebError:
                try:self.backend.close()
                except Exception:pass
                if failed=='webview2':
                    raise WebError('Embedded Edge could not start, and Chrome is not installed. Say set up Chrome to install Chrome. '
                                   'Or ask your helper to check the Microsoft WebView2 component.') from failure
                raise WebError('Chrome could not start, and embedded Edge could not start either. Ask your helper to check the Microsoft WebView2 component.') from failure
            if failed=='webview2':
                return 'Embedded Edge could not start. Using Chrome for now. Ask your helper to check the Microsoft WebView2 component. '
            return 'Chrome could not start. Using embedded Edge for now. If Chrome is not installed, say set up Chrome. '

    def open(self, url):
        self.navigation_succeeded=False
        try:
            safe, _ = prepare_address(url)
            if not self.address_allowed(safe):return 'Use an HTTP or HTTPS page address.'
            notice=self._navigate(safe)
            result=self._refresh()
            self.navigation_succeeded=True
            return notice+result
        except WebError as exc:
            return str(exc)
        except ValueError as exc:
            return str(exc)
        except Exception:
            return 'The page did not open. Check the address and internet connection.'

    def search(self, query, provider=None):
        if not _plain(query): return 'What would you like to look up?'
        urls={'google':('Google','https://www.google.com/search?q='),'bing':('Bing','https://www.bing.com/search?q='),'brave':('Brave Search','https://search.brave.com/search?q='),'duckduckgo':('DuckDuckGo','https://html.duckduckgo.com/html/?q=')}
        engine=provider or (self.search_engine if self.search_engine in urls else 'google')
        if engine not in urls:return 'Choose Google, Bing, Brave Search or DuckDuckGo as the search engine.'
        name,base=urls[engine]
        try:
            self.last_search=query;self.active_search_engine=engine
            notice=self._navigate(base + quote(query))
            result = self._refresh()
            return notice+'Searching with ' + name + '. ' + result
        except Exception:
            return name + ' could not open. Your search engine was not changed. Try again or choose another search engine.'

    def engine_names(self):
        return [{'google':'Google','bing':'Bing','brave':'Brave Search','duckduckgo':'DuckDuckGo'}[e] for e in self.engine_choices]

    def engine_notice(self):
        return announce_item(self.engine_names()[self.engine_index],self.engine_index,len(self.engine_choices))

    def retry_search_engine(self,engine=None):
        engine=engine or self.engine_choices[self.engine_index]
        self.list_focus=None
        return self.search(self.last_search,provider=engine)

    def _search_readable(self):
        if not self.snapshot: return False
        links = [c for c in self.snapshot.get('controls', []) if c.get('role') == 'link'
                 and self.address_allowed(c.get('href',''))]
        return bool(links and _plain(self.snapshot.get('text')))

    def read(self):
        if not self.snapshot: return 'Open a website first.'
        if not str(self.snapshot.get('text', '')).strip():
            return self._refresh()
        result = self.reading.read()
        if self.snapshot.get('text_truncated') and result.endswith('End of text.'):
            return result + ' This page exceeded the browser reading limit; ask your helper to check the remainder.'
        return result

    def find(self, request):
        if not self.snapshot: return 'Open a website first.'
        self.pending = None
        self.suggestion_index = None
        self.link_position = None
        controls = [c for c in self.snapshot['controls'] if c['role'] in ('link','button','menuitem','menuitemradio','menuitemcheckbox')]
        self.choices = match_choices(controls, request)
        if not self.choices:
            # A result can arrive after the initial page paint.
            for _ in range(3):
                try:
                    if hasattr(self.backend, 'settle'): self.backend.settle()
                    self.snapshot = self.backend.snapshot()
                except Exception:
                    break
                controls = [c for c in self.snapshot['controls'] if c['role'] in ('link','button','menuitem','menuitemradio','menuitemcheckbox')]
                self.choices = match_choices(controls, request)
                if self.choices: break
        if not self.choices:
            return ('I could not identify a matching link or button. The site may have changed or hidden its controls. '
                    'Say list links to hear what I can find, or ask your helper to inspect the page.')
        self.suggestion_index = 0
        return self._suggest()

    def link_items(self):
        snapshot=self.snapshot or {}
        items=snapshot.get('elements',snapshot.get('controls',[]))
        from web_browse import focus_order
        links=[c for c in items if kind(c)=='link' and (not c.get('href') or self.address_allowed(c['href']))]
        return focus_order(links,items)

    def list_links(self, direction='first'):
        if not self.snapshot: return 'Open a website first.'
        if direction == 'first':
            self.list_prefix='';self.list_prefix_time=0
            self.link_offset = 0
            self.link_position = None
            self.pending = None
            self.choices = []
        links = self.link_items()
        if not links and direction == 'first':
            for _ in range(3):
                try:
                    if hasattr(self.backend, 'settle'): self.backend.settle()
                    self.snapshot = self.backend.snapshot()
                except Exception: break
                links = self.link_items()
                if links: break
        if not links:
            return 'I could not identify links on this page. They may be loading or inaccessible to this browser helper. Ask your helper to inspect the page.'
        if direction != 'first': return self.move_link(1 if direction == 'next' else -1)
        self.link_position = 0
        self.list_focus = 'links'
        self.pending = None
        return 'Links list. '+(_label(links[0])+'.')+' Type the first letter or more of a link name to find it. Enter opens it. Escape returns to the page.'

    def list_name_input(self,value,backspace=False,now=None):
        import time
        if self.list_focus not in ('links','headings'):return 'Open a links or headings list first.'
        now=time.monotonic() if now is None else now
        items=self.link_items() if self.list_focus=='links' else self.heading_items()
        names=[_label(item).casefold() for item in items]
        previous=getattr(self,'list_prefix','') if now-getattr(self,'list_prefix_time',0)<1.5 else ''
        prefix=previous[:-1] if backspace else previous+value.casefold()
        current=self.link_position if self.list_focus=='links' else self.heading_index
        cycle=not backspace and len(value)==1 and previous==value.casefold() and not any(n.startswith(prefix) for n in names)
        if cycle:prefix=value.casefold()
        self.list_prefix=prefix;self.list_prefix_time=now
        if not prefix:return 'Name search cleared.'
        matches=[i for i,name in enumerate(names) if name.startswith(prefix)]
        if not matches:return 'No item starts with '+prefix+'.'
        index=next((i for i in matches if current is None or i>current),None) if cycle else matches[0]
        if index is None:return 'End of matching items.'
        if self.list_focus=='links':self.link_position=index
        else:self.heading_index=index
        self.pending=None
        return (self.list_item_name(items[index])+'.')

    def open_link(self, number):
        if not self.snapshot: return 'Open a website first.'
        links = self.link_items()
        index = int(number)-1
        if index < 0 or index >= len(links): return 'That link number is not on this page. Say list links.'
        self.link_position = index
        self.pending = links[index]
        return self.confirm()

    def move_link(self, direction):
        self.list_prefix='';self.list_prefix_time=0
        if not self.snapshot: return 'Open a website first.'
        links = self.link_items()
        if not links: return 'No links are listed. Say list links.'
        position = self.link_position
        index = (0 if direction > 0 else len(links)-1) if position is None else position + direction
        if index < 0 or index >= len(links): return 'Start of list.' if index<0 else 'End of list.'
        self.link_position = index
        self.list_focus = 'links'
        self.pending = None
        return (_label(links[index])+'.')

    def _suggest_link(self, item):
        host = urlsplit(item.get('href', '')).hostname or 'unknown site'
        return 'Open link ' + _label(item) + ' on ' + host + '? Say yes, okay, confirm that, or no.'

    def _suggest(self):
        if self.suggestion_index is None or self.suggestion_index >= len(self.choices):
            self.pending = None
            self.suggestion_index = None
            return 'Those were all the matches I found. Describe what you want another way.'
        item = self.choices[self.suggestion_index]
        destination = item.get('href', '')
        if destination and not self.address_allowed(destination):
            self.suggestion_index += 1
            return self._suggest()
        self.pending = item
        host = urlsplit(destination).hostname if destination else None
        return ('I found ' + _label(item) + (', on ' + host if host else '') +
                '. Is that what you want? Say yes, no, or tell me more.')

    def answer_from_page(self, question):
        if not self.snapshot: return 'Open a website first.'
        words = _tokens(question)
        passages = re.split(r'(?<=[.!?])\s+|\n+', str(self.snapshot.get('text', '')))
        matches = []
        for passage in passages:
            score = len(words & _tokens(passage))
            if score and len(_plain(passage)) > 12:
                matches.append((score, _plain(passage)[:450]))
        matches.sort(key=lambda pair: pair[0], reverse=True)
        if not matches:
            return 'I did not find that in the readable page text. Try describing it differently or ask your helper.'
        return ('This page says: ' + ' '.join(text for _, text in matches[:2]) +
                '. Source: ' + _plain(self.snapshot['title']) + ', ' +
                (urlsplit(self.snapshot['url']).hostname or 'unknown site') +
                '. This is an excerpt; check the page for the full context.')

    def help_here(self):
        if not self.snapshot:
            return 'No website is open. Say open website followed by its address, or search the web for a topic.'
        links = [c for c in self.snapshot['controls'] if c['role'] in ('link','button','menuitem','menuitemradio','menuitemcheckbox')]
        if not links:
            try:
                if hasattr(self.backend, 'settle'): self.backend.settle()
                self.snapshot = self.backend.snapshot()
                links = [c for c in self.snapshot['controls'] if c['role'] in ('link','button','menuitem','menuitemradio','menuitemcheckbox')]
            except Exception: pass
        fields = [c for c in self.snapshot['controls'] if c['role'] == 'field']
        examples = ', '.join(_label(c) for c in links[:3])
        return ('You are on ' + _plain(self.snapshot['title']) + ', at ' +
                (urlsplit(self.snapshot['url']).hostname or 'an unknown site') + '. ' +
                (('I see choices such as ' + examples + '. ') if examples else 'I could not identify clickable controls on this page. ') +
                (('I found ' + str(len(fields)) + ' form fields. ') if fields else '') +
                'Tell me what you want to do in your own words. Say next or previous followed by link, heading, button, checkbox, combo box, edit field, form control, table, radio button, list, list item, landmark, graphic, or paragraph. Say next heading level two for a numbered heading. Say activate current element or edit current field. In tables say next row, previous row, next column, previous column, first row, last row, first column, or last column. Say read page to hear it, or leave website to return to the main menu.')

    def focus_notice(self):
        if not self.snapshot or not hasattr(self.backend, 'focused_control'): return ''
        try: item = self.backend.focused_control()
        except Exception: return ''
        if not item: return ''
        identity = (item.get('label'), item.get('tag'), item.get('type'), item.get('role'), item.get('checked'))
        if identity == self.last_focus: return ''
        self.last_focus = identity
        return self._describe_focus(item)

    @staticmethod
    def _describe_focus(item):
        kind = item.get('role') if item.get('role') in ('radio','checkbox') else item.get('type') or item.get('tag') or 'control'
        if kind == 'password': state = 'Password field. Dictation or keyboard entry is available; its value is masked.'
        elif kind in ('checkbox','radio'): state = 'Checked.' if item.get('checked') else 'Not checked.'
        elif item.get('tag') == 'select': state = 'Selected ' + _plain(item.get('selected')) + '.'
        elif item.get('tag') in ('input','textarea'): state = 'Current value ' + (_plain(item.get('value')) or 'blank') + '.'
        else: state = ''
        return 'Focused ' + _plain(item.get('label')) + ', ' + kind + '. ' + state

    def handoff(self):
        if not self.snapshot: return 'No website is open. Ask your helper to open the site with you.'
        url = urlsplit(self.snapshot['url'])
        return ('Ask your helper to look at the ' + browser_name(self.running_browser()) + ' window titled ' + _plain(self.snapshot['title']) +
                '. The site is ' + (url.hostname or 'unknown') + '. ' +
                ('The form has ' + str(len(self.form_answers)) + ' answers entered by voice. ' if self.form_answers else '') +
                'Review the site address, all form fields, and the final action together. I have not sent your answers to anyone.')

    def _choice(self, number):
        try: index = int(number) - 1
        except ValueError: index = -1
        if not 0 <= index < len(self.choices):
            raise WebError('That choice is not available. Say what you are looking for again.')
        return self.choices[index]

    def choose(self, number):
        try: item = self._choice(number)
        except WebError as exc: return str(exc)
        destination = item.get('href', '')
        if destination and not self.address_allowed(destination):
            return 'That link does not use an HTTP or HTTPS page address.'
        self.pending = item
        self.suggestion_index = int(number) - 1
        host = urlsplit(destination).hostname if destination else None
        return ('I heard open ' + _label(item) + (', on ' + host if host else '') +
                '. Is that right? Say yes or no.')

    def confirm(self):
        if not self.pending: return 'There is no web action waiting for confirmation.'
        item = self.pending
        self.pending = None
        if item.get('href') and not self.address_allowed(item['href']):return 'That link does not use an HTTP or HTTPS page address.'
        if item.get('role') == 'button' and re.search(r'\b(pay|purchase|transfer|send|submit|delete|apply|confirm|place order)\b', _label(item), re.I):
            return 'This action may submit information or move money. Ask your helper to review and complete it in the browser.'
        try:
            self.progress('Loading page.')
            navigation=self.backend.activate(item)
            fragment=navigation.get('fragment') if isinstance(navigation,dict) else None
            message=self._refresh(read=False if fragment else None)
            if fragment:
                target=next((e for e in self.browse.elements+self.snapshot.get('anchors',[]) if e.get('id')==fragment and e.get('frame_index')==navigation.get('frame_index')),None)
                if target:
                    self.browse.set_position(target['start']);self.reading.position=target['start'];self.reading.continuation=target['start'];self.preferred_column=None
                    text=self.reading.move('line',0);self.reading.position=target['start'];self.reading.continuation=target['start'];self.cursor_changed(True)
                    return 'Moved within the page. '+text
            return message
        except WebError as exc: return str(exc)
        except Exception:
            if getattr(self.backend,'last_action_started',False) is True:
                try:return 'The link request did not finish cleanly. Current page: '+self._refresh(read=False)
                except Exception:pass
            return 'The page changed or the action did not complete. Its virtual view will refresh when navigation settles.'

    def fields(self, guided=False):
        if not self.snapshot: return 'Open a website first.'
        self.choices = [c for c in self._form_controls(self.snapshot) if not c.get('disabled') and not c.get('readonly')]
        if not self.choices: return 'I did not find readable form fields on this page.'
        if guided:
            self.list_focus=None
            self.form_fields = list(self.choices)
            self.form_index = 0
            self.form_answers = []
            self.form_page = self.snapshot['url']
            self.private_fields_confirmed = False
            return self._ask_field()
        self.list_focus='fields'
        self.field_list_index=0
        self.pending=None
        return ('Found '+str(len(self.choices))+' fields. '+announce_item(_label(self.choices[0]),0,len(self.choices),hotkey=False)+
                ' Say next or previous, then that one to answer it.')

    def _ask_field(self):
        while self.form_index is not None and self.form_index < len(self.form_fields):
            item = self.form_fields[self.form_index]
            if item['type']=='file':
                self.form_index += 1
                continue
            if choice_field(item):
                options = [o['label'] for o in item.get('options', []) if o['label'] and not o.get('disabled')]
                return ('Field ' + str(self.form_index + 1) + ': ' + _label(item) +
                        '. Choices: ' + ', '.join(options) + '. Which one? Say skip to leave it unchanged.')
            if kind(item) in ('checkbox','radio'):
                return ('Field ' + str(self.form_index + 1) + ': ' + _label(item) +
                        '. Say yes or no, or skip to leave it unchanged.')
            return ('Field ' + str(self.form_index + 1) + ': ' + _label(item) +
                    '. What should I enter? Say skip to leave it blank, or cancel form to stop.')
        self.form_index = None
        manual = [_label(item) for item in self.form_fields if item['type']=='file']
        return ('That is the end of the text fields. ' +
                ('These need private or manual entry: ' + ', '.join(manual) + '. ' if manual else '') +
                'Say review form to hear the answers entered by voice. Review every answer and other controls in the browser with your helper before submitting.')

    def _answer_field(self, spoken):
        item = self.form_fields[self.form_index]
        if spoken.lower() in ('skip','skip field'):
            if getattr(self,'review_edit_active',False):
                self.review_edit_active=False;self.form_index=None
                return 'Field unchanged. '+self.prepare_submit()
            self.form_index += 1
            return 'Skipped ' + _label(item) + '. ' + self._ask_field()
        if spoken.lower() in ('repeat','repeat question','what field'):
            return self._ask_field()
        if spoken.lower() in ('cancel form','stop form'):
            self.review_edit_active=False
            self.form_index = None
            return 'Stopped filling the form. Entries already made remain on the page; nothing was submitted.'
        value = _plain(spoken)
        if kind(item) in ('checkbox','radio'):
            if value.casefold() not in ('yes','no'):
                return 'Say yes or no for ' + _label(item) + ', or say skip.'
            value = value.casefold()
        elif choice_field(item):
            options = [o['label'] for o in item.get('options', []) if o['label'].casefold() == value.casefold() and not o.get('disabled')]
            if len(options) != 1:
                return 'I could not identify one choice. ' + self._ask_field()
            value = options[0]
        else:
            try:value=_voice_value(item,spoken)
            except ValueError as exc:return str(exc)
        try:
            if choice_field(item) or kind(item) in ('checkbox','radio'):
                self.backend.choose_form_option(item, value)
            else:
                self.backend.fill(item, value)
        except Exception:
            self.form_index = None
            return 'The field changed. Stop and review the page before trying again.'
        self.form_index += 1
        self.form_answers.append({'label': _label(item), 'value': 'entered' if item.get('type')=='password' else value, 'key': item['key'],
                                  'tag': item['tag'], 'type': item['type']})
        try:
            latest=self.backend.snapshot()
            if latest['url']!=self.form_page:
                self.form_index=None;return 'The form moved to a different page. Your answer was entered; nothing was submitted. Say read page to continue.'
            fields=[c for c in self._form_controls(latest) if not c.get('disabled') and not c.get('readonly')]
            matches=[i for i,c in enumerate(fields) if c.get('frame_index')==item.get('frame_index') and
                     (c.get('id')==item['id'] if item.get('id') else _label(c)==_label(item) and c.get('tag')==item.get('tag') and c.get('type')==item.get('type'))]
            if len(matches)==1:self.form_fields=fields;self.form_index=matches[0]+1
            elif fields!=self.form_fields:
                self.form_index=None;return 'The form changed after your answer. Nothing was submitted. Say read page, then fill out this form to continue.'
        except Exception:
            self.form_index=None;return 'Your answer was entered, but the form could not be checked again. Nothing was submitted. Say read page to retry.'
        if getattr(self,'review_edit_active',False):
            self.review_edit_active=False;self.form_index=None
            self._refresh(read=False,wait=False)
            return 'Field updated. '+self.prepare_submit()
        return ('Password entered. ' if item.get('type')=='password' else 'Recorded ' + value + ' for ' + _label(item) + '. ') + self._ask_field()

    def review_form(self):
        if not self.snapshot: return 'Open a website first.'
        try: current = self.backend.snapshot()
        except Exception: return 'I could not read the current form. Ask your helper to look at the browser window.'
        if current['url'] != self.snapshot['url']:
            return 'The page changed. Say read page, then review form again.'
        self.review_submission = None
        self.review_parts = self._form_parts(current)
        self.review_cursor = 0
        return self._next_review()

    @staticmethod
    def _form_controls(current):
        return [c for c in current.get('controls', []) if kind(c) in ('edit','combobox','listbox','checkbox','radio')
                and c.get('type') not in ('hidden','submit','button','image')]

    @classmethod
    def _form_signature(cls, current):
        return (current.get('url'), tuple((c.get('key'),_label(c),c.get('tag'),c.get('type'),
                c.get('value') if c.get('type') != 'password' else bool(c.get('filled')),
                bool(c.get('checked')),c.get('selected_label'),bool(c.get('required')))
                for c in cls._form_controls(current)))

    @classmethod
    def _form_parts(cls, current):
        parts = []
        for c in cls._form_controls(current):
            label = _label(c)
            if c.get('type') == 'password': value = 'entered privately' if c.get('filled') else 'empty; enter privately'
            elif c.get('type') == 'file': value = 'file selected' if c.get('value') else 'no file selected'
            elif choice_field(c): value = c.get('selected_label') or 'nothing selected'
            elif kind(c) in ('checkbox','radio'): value = 'checked' if c.get('checked') else 'not checked'
            else: value = _plain(c.get('value')) or 'blank'
            parts.append(label + ': ' + value + (', required' if c.get('required') else ''))
        return parts

    def _next_review(self):
        if self.review_cursor >= len(self.review_parts):
            return 'End of form review.'
        end = min(self.review_cursor + 1, len(self.review_parts))
        piece = self.review_parts[self.review_cursor:end]
        self.review_cursor = end
        result = ('Form review, fields ' + str(end - len(piece) + 1) + ' through ' + str(end) +
                  ' of ' + str(len(self.review_parts)) + ': ' + '. '.join(piece) + '. ')
        if end < len(self.review_parts):
            return result + 'Say change this field to correct it, or continue review for the remaining fields. Submission is not ready yet.'
        if self.review_submission:
            pending = self.review_submission
            self.review_submission = None
            self.submit_pending = pending
            return (result + 'The button is ' + _label(pending['button']) + ' on ' +
                    (urlsplit(pending['url']).hostname or 'this site') +
                    '. Is every field right? Say change this field to correct it, yes to submit, or no to cancel.')
        return result + 'End of form review. Nothing has been submitted.'

    def edit_review_field(self,request='this field'):
        current=self.backend.snapshot()
        if current.get('url')!=self.snapshot.get('url'):return 'The page changed. Say read page before editing this form.'
        fields=self._form_controls(current)
        request=request.strip().rstrip('.!?').casefold()
        if request in ('this field','current field','field'):
            index=max(0,self.review_cursor-1)
            item=fields[index] if index<len(fields) else None
        else:
            number=re.fullmatch(r'(?:field )?(\d+|one|two|three|four|five|six|seven|eight|nine|ten)',request)
            if number:
                values=dict(zip(('one','two','three','four','five','six','seven','eight','nine','ten'),range(1,11)))
                index=int(number[1]) if number[1].isdigit() else values[number[1]]
                item=fields[index-1] if 1<=index<=len(fields) else None
            else:
                matches=[f for f in fields if request==_label(f).casefold() or request in _label(f).casefold()]
                item=matches[0] if len(matches)==1 else None
        if not item:return 'Name one field, such as change field two or change Subject.'
        if item.get('readonly') or item.get('disabled'):return 'That field cannot be edited.'
        self.review_submission=None;self.submit_pending=None;self.review_edit_active=True
        self.form_fields=[item];self.form_index=0;self.form_page=current['url']
        return 'Changing '+_label(item)+'. '+self._ask_field()

    def prepare_submit(self):
        if not self.snapshot: return 'Open a website first.'
        if self.form_index is not None:
            return 'Finish the remaining fields or say cancel form before reviewing submission.'
        try: current = self.backend.snapshot()
        except Exception: return 'I could not recheck this form. Do not submit it by voice.'
        if current['url'] != self.snapshot['url'] or urlsplit(current['url']).scheme != 'https':
            return 'The page changed. Say read page before trying to submit.'
        if self.form_page and current['url'] != self.form_page:
            return 'The form moved to a different page. Say read page before trying again.'
        controls = current.get('controls', [])
        if current.get('controls_truncated') or current.get('visible_frames'):
            return 'I cannot inspect the whole form on this page. Ask your helper to review it in the browser.'
        if any(c.get('value_truncated') for c in self._form_controls(current)):
            return 'A form answer is too long for safe spoken review. Ask your helper to check it in the browser.'
        missing = [c for c in self._form_controls(current) if c.get('required') and not c.get('disabled') and not c.get('readonly') and
                   ((not c.get('filled')) if c.get('type') == 'password' else
                    (not any(other.get('type')=='radio' and other.get('radio_group')==c.get('radio_group') and other.get('frame_index')==c.get('frame_index') and other.get('checked') for other in self._form_controls(current))) if c.get('type')=='radio' and c.get('radio_group') else
                    (not c.get('checked')) if kind(c) in ('checkbox','radio') else
                    (not c.get('selected_label')) if choice_field(c) and c.get('tag')!='select' else
                    (not c.get('value')))]
        if missing:
            return 'Required fields appear empty: ' + ', '.join(_label(c) for c in missing) + '. Complete them before submitting.'
        private = [c for c in controls if c['type']=='file' and c.get('required')]
        if private and not self.private_fields_confirmed:
            return ('This form has required private fields: ' + ', '.join(_label(c) for c in private) +
                    '. Complete them privately in the browser window. Then say private fields complete before reviewing submission.')
        buttons = [c for c in controls if c['role'] == 'button' and
                   (c.get('in_form') or re.search(r'\b(submit|apply|continue|next|send)\b', _label(c), re.I))]
        if len(buttons) != 1:
            return 'I cannot identify one final form button. Ask your helper to inspect this page.'
        button = buttons[0]
        if re.search(r'\b(pay|purchase|transfer|withdraw|deposit|wire|send money)\b',
                     _label(button) + ' ' + current.get('title',''), re.I):
            return 'This may move money. Ask your helper to review the accounts, amount, and final action in the browser.'
        self.review_parts = self._form_parts(current)
        if not self.review_parts: return 'I found no readable form fields. Ask your helper to inspect this page.'
        self.review_cursor = 0
        self.review_submission = {'button':button, 'url':current['url'],
                                  'signature':self._form_signature(current)}
        self.submit_pending = None
        return self._next_review()

    def confirm_submit(self):
        pending = self.submit_pending
        self.submit_pending = None
        if not pending: return 'There is no reviewed form ready to submit. Say review and submit form first.'
        try:
            current = self.backend.snapshot()
            if current['url'] != pending['url']:
                return 'The page changed. Submission canceled; review the form again.'
            button = pending['button']
            matches = [c for c in current.get('controls', []) if c['key'] == button['key'] and
                       _label(c) == _label(button) and c.get('in_form') == button.get('in_form')]
            if len(matches) != 1:
                return 'The submit button changed. Submission canceled; review the form again.'
            if self._form_signature(current) != pending['signature']:
                return 'A form answer changed. Submission canceled; review the form again.'
            self.progress('Loading page.')
            action=self.backend.activate(button)
            result = self._refresh()
            return (action.get('validation') or action.get('notice') or result) if isinstance(action,dict) else result
        except Exception:
            return 'The submission result is uncertain. Do not repeat it until you or your helper checks the site.'

    def fill(self, number, value):
        try:
            item = self._choice(number)
            if item['role'] != 'field' and not editable_combo(item): raise WebError('That number is not a field. Say list fields.')
            if item['type'] in ('file','checkbox','radio','submit'):
                raise WebError('This field needs direct interaction in the browser with your helper.')
            value=_voice_value(item,value)
            self.backend.fill(item, value)
            return 'Password entered.' if item['type']=='password' else 'Filled ' + _label(item) + ' with ' + value + '. Review the form before submitting it.'
        except (WebError,ValueError) as exc: return str(exc)
        except Exception: return 'That field changed. Say list fields and try again.'

    def list_item_name(self,item):
        return _label(item)+(' '+str(item.get('level',1)) if kind(item)=='heading' else '')

    def heading_items(self):
        return [e for e in (self.snapshot or {}).get('elements',[]) if kind(e)=='heading']

    def list_headings(self):
        items=self.heading_items()
        if not items:return 'No headings on this page.'
        self.list_focus='headings';self.heading_index=0;self.pending=None;self.list_prefix='';self.list_prefix_time=0
        return 'Headings list. '+(self.list_item_name(items[0])+'.')+' Enter moves to the heading. Escape returns to the page.'

    def move_heading(self,step):
        self.list_prefix='';self.list_prefix_time=0
        items=self.heading_items();index=self.heading_index+step
        if not 0<=index<len(items):return 'Start of list.' if index<0 else 'End of list.'
        self.heading_index=index;return (self.list_item_name(items[index])+'.')

    def activate_list_item(self):
        if self.list_focus=='links':return self.open_link((self.link_position or 0)+1)
        if self.list_focus=='headings':
            item=self.heading_items()[self.heading_index];self.list_focus=None
            self.browse.set_position(item['start']);self.reading.position=item['start'];self.reading.continuation=item['start'];self.cursor_changed()
            return describe(item)
        return 'Move to a link or heading first.'

    def command(self, spoken):
        try:self._sync_browser_tab()
        except Exception:return 'The browser connection is not ready. Keep Voice Companion open; it will reconnect automatically.'
        command = _plain(spoken).rstrip('.!?').lower()
        if self.list_focus in ('fields','favorites') and command in ('cancel','go back','close list','back to page','escape','no','no thanks'):
            self.list_focus=None;self.pending=None;return 'Virtual page view.'
        if self.list_focus=='search_engines':
            if command in ('cancel','escape','go back','back to page','close list'):self.list_focus=None;return 'Verification page. Escape returns to the web browser menu.'
            if command in ('next','previous'):
                self.engine_index=max(0,min(len(self.engine_choices)-1,self.engine_index+(-1 if command=='previous' else 1)));return self.engine_notice()
            if command in ('okay','ok','yes','confirm','that one'):return self.retry_search_engine()
            choice=re.fullmatch(r'(?:(?:try|use|choose|select|search with) )?(google|bing|brave(?: search)?|duck ?duck ?go)(?: for this search)?',command)
            if choice:return self.retry_search_engine('brave' if choice[1].startswith('brave') else 'duckduckgo' if choice[1].startswith('duck') else choice[1])
            return 'Choose an engine with arrows and Enter, or say its name.'
        from web_modes import mode_request
        mode=mode_request(command)
        if mode and self.snapshot:return self.set_app_mode(mode)
        boundary=command.replace('of the page','of page')
        boundary=re.sub(r'^(?:go|move|jump) (?:to )?(?:the )?','',boundary)
        if boundary in ('top of page','beginning of page','start of page'):return self.page_boundary()
        if boundary in ('bottom of page','end of page'):return self.page_boundary(True)
        if command in ('top of page','beginning of page','start of page','go to top of page','go to beginning of page','go to start of page'):return self.page_boundary()
        if command in ('bottom of page','end of page','go to bottom of page','go to end of page'):return self.page_boundary(True)
        if command in ('leave field','finish field editing'):
            if self.keyboard_field:self.commit_keyboard_field()
            return 'Page view.'
        letter=re.fullmatch(r'(?:select|choose|find) (?:option )?(?:starting|beginning) with ([a-z])',command)
        if letter and self.keyboard_field and choice_field(self.keyboard_field):return self.keyboard('ChoiceLetter:'+letter[1])
        if command in ('list headings','show headings','headings list','heading list'):return self.list_headings()
        if self.list_focus in ('links','headings') and not (self.pending or self.submit_pending):
            if command in ('cancel','go back','close list','back to page','escape'):
                self.list_focus=None;self.pending=None;self.link_position=None;return 'Virtual page view.'
            if command in ('yes','yes please','okay','ok','confirm','confirm that','that one','open current link','activate current link','go to heading','open current heading'):
                return self.activate_list_item()
            if command in ('no','no thanks'):
                self.list_focus=None;self.pending=None;return 'Virtual page view.'
            if self.list_focus=='headings' and command in ('next','previous','next heading','previous heading'):
                return self.move_heading(-1 if command.startswith('previous') else 1)
        if command=='refresh page' and self.snapshot:return self.keyboard('Refresh')
        if not self.keyboard_field and SKIP_AD.fullmatch(command):return self.skip_ad()
        browse_key=voice_browse_key(command)
        if browse_key and browse_key.startswith('Read:') and self.keyboard_field:browse_key=None  # Reading keys move through the page, not a text box.
        if browse_key and self.form_index is None and not self.list_focus and (not reading_request(command) or browse_key.split(':')[-1]=='p' and self.browse and any(kind(e)=='paragraph' for e in self.browse.elements)):return self.keyboard(browse_key)
        if not self.keyboard_field and command.startswith(('type ', 'enter text ', 'dictate ')) and self.browse and self.browse.current() and text_field(self.browse.current()):self.focus_current()
        if self.keyboard_field and command.startswith(('type ', 'enter text ', 'dictate ')):
            if self.keyboard_field.get('readonly'):return 'This field is read-only. Move to another field to enter text.'
            value=spoken.split(' ',2)[2] if command.startswith('enter text ') else spoken.split(' ',1)[1]
            try:value=_voice_value(self.keyboard_field,value)
            except ValueError as exc:return str(exc)
            self.keyboard_field['value']=value;self.web_edit_caret=len(value);self.selection=None
            if editable_combo(self.keyboard_field):return self.combo_typed() or self._combo_entered()
            return 'Text entered. Say save field, next control, or cancel field editing.'
        # In a site's own search box, "search for" searches that site.
        search=re.match(r'^\s*search(?: for)?\s+(?!the web\b)(.+)',spoken,re.I|re.S)
        combo=self.keyboard_field if self.keyboard_field else self.browse.current() if self.browse and self.form_index is None and not self.list_focus else None
        if search and editable_combo(combo) and not combo.get('readonly'):
            if not self.keyboard_field:self.focus_current()
            try:value=_voice_value(self.keyboard_field,search[1])
            except ValueError as exc:return str(exc)
            self.keyboard_field['value']=value;self.web_edit_caret=len(value);self.selection=None
            return self.combo_typed() or self.combo_done()
        if self.form_index is None and not self.list_focus and self.browse:
            item=self.browse.current()
            if command in ('read current element','current element','what element am i on'):
                return describe(item) if item else 'Move to a page element first.'
            if command in ('check checkbox','check current checkbox','uncheck checkbox','uncheck current checkbox'):
                if not item or kind(item)!='checkbox':return 'Move to a checkbox first.'
                try:
                    self.backend.choose_form_option(item,'no' if command.startswith('uncheck') else 'yes')
                    return self._refresh_at(item)
                except Exception:return 'The checkbox changed or could not be used. Refresh the page.'
            if command.startswith('select option '):
                if not item or kind(item) not in ('combobox','listbox'):return 'Move to a combo box or list box first.'
                try:
                    self.backend.choose_form_option(item,spoken.split(' ',2)[2])
                    self._refresh_at(item);self.focus_current();return self.keyboard_field.get('value','')+'.'
                except WebError as exc:return str(exc)
                except Exception:return 'The option changed or could not be used. Refresh the page.'
        if selection_command(spoken): return self.select_focused(spoken)
        if command in ('new line','insert new line','line break','carriage return','insert carriage return'): return self.insert_line_break()
        request = reading_request(command)
        if request and self.keyboard_field and text_field(self.keyboard_field):
            if self.keyboard_field.get('type')=='password':return 'Password field. Its value is masked.'
            unit,direction=request
            if unit=='line' and direction and self.keyboard_field.get('tag')!='textarea' and not self.keyboard_field.get('editable'):return self.leave_field('Up' if direction<0 else 'Down')
            reader=ReadingCursor();reader.set_text(self.keyboard_field.get('value',''));reader.position=getattr(self,'web_edit_caret',0)
            result=reader.read(direction=='top') if unit=='read' else reader.move(unit,direction)
            self.web_edit_caret=reader.position;self.selection=None;return result
        if request and self.snapshot:
            unit, direction = request
            self.reading.set_text(self.snapshot.get('text', ''))
            result=self.reading.read(direction == 'top') if unit == 'read' else self.reading.move(unit, direction)
            if self.browse:self.browse.set_position(self.reading.position)
            self.auto_focus_position();self.cursor_changed()
            return result
        match = re.fullmatch(BROWSER_REQUEST, command)
        if match: return self.choose_browser(match[1])
        if command in ('which browser','what browser am i using'):
            running = self.running_browser()
            # The preference is kept after a fallback; say which browser is really showing the page.
            return ('Guided websites use ' + browser_name(self.browser) + '.' +
                    (' This page is open in ' + browser_name(running) + ' because ' + browser_name(self.browser) + ' could not start.' if running != self.browser else '') +
                    ' Say use Chrome or use embedded Edge to change it.')
        if command in ('where am i','read current field','what field am i on'):
            try: item = self.backend.focused_control()
            except Exception: item = None
            return self._describe_focus(item) if item else 'No readable field has keyboard focus. Press Tab or say list fields.'
        if self.review_parts and command.startswith(('change ','edit ','correct ')):
            request=re.sub(r'^(?:change|edit|correct) ','',spoken,flags=re.I)
            return self.edit_review_field(request)
        if command in ('continue review','keep reviewing','read next fields'):
            return self._next_review()
        if self.submit_pending and command in ('no','no thanks','cancel','cancel submit'):
            self.submit_pending = None
            return 'No. Form submission canceled. Nothing was submitted. Say change followed by a field name to correct it, or review and submit form to review again.'
        if self.submit_pending and command in ('yes','yes please'):
            return self.confirm_submit()
        if self.submit_pending and command not in ('submit this form','yes submit this form','confirm submit form'):
            self.submit_pending = None
        if command in ('submit this form','yes submit this form','confirm submit form'):
            return self.confirm_submit()
        if command in ('review and submit form','submit form','finish this form','send this application'):
            return self.prepare_submit()
        if command in ('private fields complete','i completed the private fields'):
            self.private_fields_confirmed = True
            return 'I will not read or store private fields. Say review and submit form to check the rest.'
        if command in ('ask for help','get human help','get help from my helper','i need a person',
                       'help me with this site','what should my helper do'):
            return self.handoff()
        if command in ('review form','read form back','review my answers'):
            return self.review_form()
        if command in ('help','help me','what can i do','what can i do here','i need help'):
            return self._ask_field() if self.form_index is not None else self.help_here()
        if self.form_index is not None and command not in ('read page','read this page','refresh page'):
            if self.input_mode == 'commands' and command not in ('skip','skip field','repeat','repeat question',
                                                                 'what field','cancel form','stop form'):
                return 'Commands only is on. Say normal mode to answer this field.'
            return self._answer_field(spoken)
        if command in ('next','previous','next favorite','previous favorite','next field','previous field'):
            step=-1 if command.startswith('previous') else 1
            if self.list_focus=='favorites' and self.favorite_items:
                index=self.favorite_index+step
                if not 0<=index<len(self.favorite_items): return ('Start of list. ' if step<0 else 'End of list. ')+'That is the '+('first' if step<0 else 'last')+' favorite.'
                self.favorite_index=index
                return name_first('Favorite '+str(index+1)+' of '+str(len(self.favorite_items))+': '+self.favorite_items[index][1]['name']+'. Say that one to open it.')
            if self.list_focus=='fields' and self.choices:
                index=self.field_list_index+step
                if not 0<=index<len(self.choices): return ('Start of list. ' if step<0 else 'End of list. ')+'That is the '+('first' if step<0 else 'last')+' field.'
                self.field_list_index=index
                return announce_item(_label(self.choices[index]),index,len(self.choices),hotkey=False)+' Say that one to answer it.'
            return self.move_link(step)
        if command in ('that one','okay','ok','confirm','confirm that') and self.list_focus=='favorites' and self.favorite_items:
            return self.open(self.favorite_items[self.favorite_index][1]['url'])
        if command in ('that one','okay','ok','confirm','confirm that') and self.list_focus=='fields' and self.choices:
            self.list_focus=None
            self.form_fields=list(self.choices)
            self.form_index=self.field_list_index
            self.form_answers=[]
            self.form_page=self.snapshot['url']
            return self._ask_field()
        if self.pending and command in ('yes','yes please','that one','that is it',"that's it",'okay','ok','confirm','confirm that'):
            return self.confirm()
        if self.pending and command in ('no','no thanks','not that one','another one','next one'):
            if self.link_position is not None and self.list_focus=='links':
                self.pending = None
                return self.move_link(1)
            self.suggestion_index = (self.suggestion_index or 0) + 1
            return self._suggest()
        if self.choices and command in ('first','first one','second','second one','third','third one'):
            index = {'first':1,'first one':1,'second':2,'second one':2,'third':3,'third one':3}[command]
            return self.choose(str(index))
        if self.choices and command.startswith(('the ', 'open the ')):
            matches = match_choices(self.choices, spoken)
            if len(matches) == 1:
                return self.choose(str(self.choices.index(matches[0]) + 1))
        if command in ('read page','read this page','what is on this page','refresh page'):
            try: return self._refresh(read=True)
            except Exception: return 'The page could not be read. Ask your helper to look at the browser window.'
        if command in ('list links','show links','what links are here','read links'): return self.list_links()
        if command in ('next links','more links','next link','following link','move down','next'): return self.move_link(1)
        if command in ('previous links','prior links','previous link','prior link','move up','previous'): return self.move_link(-1)
        match = re.fullmatch(r'(?:open|choose|click) (?:link )?(?:number )?(\d+|one|two|three|four|five|six|seven|eight|nine|ten)(?: link)?', command)
        if match:
            number = {'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'ten':10}.get(match[1],match[1])
            return self.open_link(number)
        if command in ('continue reading','read more'): return self.read()
        if command in ('back','go back','previous page','go to previous page','go back a page'):return self.history(-1)
        if command in ('forward','go forward','next page','go to next page','go forward a page'):return self.history(1)
        if command in ('save favorite','add favorite','favorite this site'):
            if not self.snapshot: return 'Open a website first.'
            name = _plain(self.snapshot['title'])[:60]
            try: self.favorites.add(name, self.snapshot['url'])
            except WebError as exc: return str(exc)
            return 'Saved favorite ' + name + '. Say list favorites to hear it.'
        match = re.fullmatch(r'(?:save|add) favorite (?:as |called )?(.+)', spoken, re.I)
        if match:
            if not self.snapshot: return 'Open a website first.'
            try: self.favorites.add(match[1], self.snapshot['url'])
            except WebError as exc: return str(exc)
            return 'Saved favorite ' + match[1] + '.'
        if command in ('list favorites','my favorites'):
            values = self.favorites.all()
            self.favorite_items=list(values.items())
            self.favorite_index=0
            self.list_focus='favorites'
            self.pending=None
            return ('Found '+str(len(self.favorite_items))+' favorites. '+announce_item(self.favorite_items[0][1]['name'],0,len(self.favorite_items))+
                    ' Say next or previous, then that one to open it.' if self.favorite_items else 'No favorites saved yet.')
        if command.startswith('go to '):
            item=self.favorites.get(spoken.strip()[6:])
            if item:return self.open(item['url'])
        match = re.fullmatch(r'(?:open|go to) favorite (.+)', spoken, re.I)
        if match:
            item = self.favorites.get(match[1])
            return self.open(item['url']) if item else 'I could not find that favorite. Say list favorites.'
        if command in ('fill out this form','fill out this form for me','help me fill this form',
                       'help me with this form','complete this form'):
            return self.fields(guided=True)
        if command in ('list fields','what fields'): return self.fields()
        match = re.fullmatch(r'fill field (\d+) (?:with|as) (.+)', spoken, re.I)
        if match: return self.fill(match[1], match[2])
        match = re.fullmatch(r'open choice (\d+)', command)
        if match: return self.choose(match[1])
        if command in ('confirm web action','yes open it'): return self.confirm()
        if command in ('cancel','cancel web action'):
            self.pending = None
            self.suggestion_index = None
            return 'Canceled. The web action was not opened.'
        match = re.match(r'^(?:what does (?:this |the )?page say about|tell me about)\s+(.+)', spoken, re.I)
        if match: return self.answer_from_page(match[1])
        match = re.match(r'^(google|bing|brave(?: search)?|duck\s*duck\s*go)(?: for)?\s+(.+)', spoken, re.I)
        if match:return self.search(match[2],provider='duckduckgo' if match[1].lower().startswith('duck') else 'brave' if match[1].lower().startswith('brave') else match[1].lower())
        match = re.match(r'^(?:search(?: the web)?(?: for)?|look up)\s+(.+)', spoken, re.I)
        if match: return self.search(match[1])
        match = re.match(r'^what about\s+(.+)', spoken, re.I)
        if match: return self.search((self.last_search + ' ' + match[1]).strip())
        if command.startswith(('i am looking for ', "i'm looking for ", 'where is ', 'how do i ', 'find ', 'look for ')):
            return self.find(spoken)
        if self.keyboard_field and command in ('cancel','go back','browse mode'):
            self.keyboard_field=None;return 'Browse mode.'
        if self.input_mode == 'mixed' and self.keyboard_field and editable_combo(self.keyboard_field) and not command.startswith(('open ', 'go to ', 'look up ',
                 'what ', 'help ', 'list ', 'read ', 'fill ', 'submit ', 'click ', 'choose ', 'save ')):
            # Keep the field's own copy and the site's box the same.
            return self.dictate_to_focus(spoken)
        if self.input_mode == 'mixed' and not command.startswith(('open ', 'go to ', 'search ', 'look up ',
                 'what ', 'help ', 'list ', 'read ', 'fill ', 'submit ', 'click ', 'choose ', 'save ')):
            try:
                result = self.backend.dictate_focused(spoken)
                if not result.startswith('No editable text field'): return result
            except Exception: pass
        return self.find(spoken) if _tokens(spoken) else 'Tell me what you are looking for on this page, or say read page, list fields, or list favorites.'

def _frame_shown(frame,readable=False):
    """Whether a frame's element is shown; with readable, its key unless hidden from readers.

    Each frame_element() call holds the element in the page until released.
    Checked every 0.3 seconds on pages with ad frames, unreleased handles grew
    without limit, in the page and in the driver.
    """
    element=frame.frame_element()
    try:
        if not element.is_visible():return None
        if not readable:return ''
        if element.evaluate("e=>!!e.closest('[aria-hidden=\"true\"],[inert]')"):return None
        return element.get_attribute('data-voice-companion-key')
    finally:
        try:element.dispose()
        except Exception:pass


# Embedded Edge has no list of history entries for Companion, but the page's
# own navigation list (main page entries only, same site) can go straight to
# the previous page without stopping at entries that only changed a frame.
NAVIGATION_JUMP='''(delta)=>{const n=window.navigation;if(!n||!n.currentEntry||!(delta<0?n.canGoBack:n.canGoForward))return null;
const entries=n.entries(),index=n.currentEntry.index+delta;if(index<0||index>=entries.length)return null;
const result=n.traverseTo(entries[index].key);result.committed.catch(()=>{});result.finished.catch(()=>{});return 'started';}'''


SNAPSHOT_AS_TEXT="()=>JSON.stringify((/*SNAPSHOT*/)(),(key,value)=>value===undefined?null:value)"


def snapshot_value(value):
    """A page snapshot arrives as JSON text; test doubles may return the object."""
    return json.loads(value) if isinstance(value,str) else value


# The page's change counter. Players and animations rewrite position, size and
# opacity styles many times a second (a video's progress bar); nothing a
# reader hears changes, so that is not a page change. Display and visibility
# changes still are. Slider and progress values count as a change, but do not
# keep the page from looking settled.
DOCUMENT_STAMP=r'''()=>{const old=window.__voiceCompanionDocument;if(!old||old.root!==document.documentElement||old.version!==2){old?.observer?.disconnect();const same=!!old&&old.root===document.documentElement;const state={version:2,revision:(old?.revision||0)+1,changed:Date.now(),root:document.documentElement,epoch:(old?.epoch||0)+(same?0:1)};
const motion=/^(?:transform|translate|scale|rotate|width|height|min-width|min-height|max-width|max-height|left|top|right|bottom|inset|opacity|background-position|background-size|clip-path|stroke-dasharray|stroke-dashoffset|will-change|transition(?:-.*)?|animation(?:-.*)?|--.*)$/;
const styles=text=>{const found=new Map();for(const part of String(text||'').split(';')){const at=part.indexOf(':');if(at>0)found.set(part.slice(0,at).trim().toLowerCase(),part.slice(at+1).trim());}return found;};
const ignored=r=>{if(r.type!=='attributes')return false;const name=r.attributeName||'';if(name.startsWith('data-voice-companion-'))return true;if(name!=='style')return false;const before=styles(r.oldValue),after=styles(r.target.getAttribute('style'));for(const key of new Set([...before.keys(),...after.keys()]))if(before.get(key)!==after.get(key)&&!motion.test(key))return false;return true;};
const steady=r=>r.type==='attributes'&&(r.attributeName==='aria-valuenow'||r.attributeName==='aria-valuetext');
const observer=new MutationObserver(records=>{const real=records.filter(r=>!ignored(r));if(real.length){state.revision++;if(!real.every(steady))state.changed=Date.now();}});const options={childList:true,subtree:true,characterData:true,attributes:true,attributeOldValue:true};observer.observe(document.documentElement,options);const watched=new WeakSet();state.watch=root=>{if(!watched.has(root)){watched.add(root);observer.observe(root,options);}};state.observer=observer;window.__voiceCompanionDocument=state;}const state=window.__voiceCompanionDocument;return {url:location.href,epoch:String(performance.timeOrigin)+':'+state.epoch,revision:state.revision,ready:document.readyState,busy:!!document.querySelector('[aria-busy="true"]'),age:Date.now()-state.changed};}'''

# The same accessible name is used for snapshot and stale-target validation.
from web_accessible_names import ACCESSIBLE_LABEL

LIVE_IDENTITY='e=>{const label=('+ACCESSIBLE_LABEL+')(e);return {label,tag:e.tagName.toLowerCase(),type:(e.type||\'\').toLowerCase(),href:e.tagName===\'A\'?e.href:\'\',disabled:!!e.disabled||e.getAttribute(\'aria-disabled\')===\'true\'};}'

# Editable combo boxes: read the site's suggestions for one text box. The site
# owns its list; this only reports what it shows and which suggestion is active.
COMBO_STATE=r'''(e,arg)=>{
  const accessibleLabel='''+ACCESSIBLE_LABEL+r''';
  const deep=()=>{let a=document.activeElement;while(a?.shadowRoot?.activeElement)a=a.shadowRoot.activeElement;return a;};
  const shown=n=>!!n&&n.getClientRects().length>0&&getComputedStyle(n).visibility!=='hidden'&&!n.closest('[aria-hidden="true"],[inert]');
  const root=e.getRootNode(),byId=id=>id?((root.getElementById?root.getElementById(id):null)||document.getElementById(id)):null;
  const combo=e.closest('[role="combobox"]')||e,focused=deep(),typed=arg&&typeof arg.typed==='string'?arg.typed:null;
  const value=e.isContentEditable?e.innerText:String(e.value||'');
  const focusInBox=!!focused&&(focused===e||e.contains(focused));
  if(e.tagName==='INPUT'&&e.list&&!e.getAttribute('role')){
    const wanted=value.trim().toLowerCase();
    const options=[...new Set([...e.list.options].filter(o=>!o.disabled).map(o=>(o.value||o.label).trim()).filter(t=>t&&(!wanted||t.toLowerCase().includes(wanted))))];
    return {options,active:null,value,expanded:false,datalist:true,focus_in_box:focusInBox,focus_in_list:false};
  }
  const ids=[e,combo].flatMap(n=>((n.getAttribute('aria-controls')||'')+' '+(n.getAttribute('aria-owns')||'')).split(/\s+/)).filter(Boolean);
  const popups=[...new Set(ids.map(byId))].filter(n=>n&&n!==e&&!n.contains(e));
  const descendant=byId(e.getAttribute('aria-activedescendant')||combo.getAttribute('aria-activedescendant'));
  const owner=descendant?.closest('[role="listbox"],[role="grid"],[role="tree"]');if(owner)popups.push(owner);
  if(combo!==e)popups.push(...combo.querySelectorAll('[role="listbox"]'));
  // A site may not link its list to the box: use the one visible list of options.
  if(!popups.some(shown)){const lists=[...document.querySelectorAll('[role="listbox"]')].filter(l=>shown(l)&&l.querySelector('[role="option"]'));if(lists.length===1)popups.push(lists[0]);}
  const options=[...new Set(popups.filter(shown).flatMap(p=>p.matches('[role="option"]')?[p]:[...p.querySelectorAll('[role="option"]')]))].filter(shown);
  const labels=options.map(o=>accessibleLabel(o));
  const inside=n=>!!n&&!!focused&&(n===focused||n.contains(focused));
  let active=descendant?options.findIndex(o=>o===descendant||o.contains(descendant)):-1;
  if(active<0)active=options.findIndex(inside);
  if(active<0)active=options.findIndex(o=>o.getAttribute('aria-selected')==='true');
  // Sites such as YouTube copy the highlighted suggestion into the box.
  if(active<0&&typed!==null&&value!==typed)active=labels.findIndex(l=>l.trim().toLowerCase()===value.trim().toLowerCase());
  const expanded=combo.getAttribute('aria-expanded')==='true'||e.getAttribute('aria-expanded')==='true'||options.length>0;
  return {options:labels,active:active<0?null:active,value,expanded,datalist:false,focus_in_box:focusInBox,focus_in_list:!focusInBox&&popups.some(inside)};
}'''

# App mode: the element with keyboard focus, and text that the page announces
# through live regions (role status, alert or log, or aria-live). The live
# region watcher is a JavaScript property, so it never changes the page's DOM.
PAGE_KEY_STATE=r'''(arg)=>{
  const accessibleLabel='''+ACCESSIBLE_LABEL+r''';
  if(!window.__voiceCompanionLive){
    const live={texts:[]};
    const region=n=>{for(let x=n&&(n.nodeType===1?n:n.parentElement);x;x=x.parentElement||x.getRootNode()?.host){
      if(!x.getAttribute)continue;const level=x.getAttribute('aria-live'),role=x.getAttribute('role');
      if(level==='off')return null;if(level==='polite'||level==='assertive'||['status','alert','log'].includes(role))return x;}return null;};
    live.observer=new MutationObserver(records=>{const seen=new Set();for(const record of records){const target=region(record.target);if(!target||seen.has(target))continue;seen.add(target);
      if(target.closest('[aria-hidden="true"]'))continue;const text=String(target.innerText||target.textContent||'').replace(/\s+/g,' ').trim();
      if(text&&text!==target.__voiceCompanionSaid){target.__voiceCompanionSaid=text;live.texts.push(text.slice(0,300));}}});
    live.observer.observe(document.documentElement,{childList:true,subtree:true,characterData:true});
    window.__voiceCompanionLive=live;
  }
  const texts=arg&&arg.drain?window.__voiceCompanionLive.texts.splice(0):window.__voiceCompanionLive.texts.slice();
  let e=document.activeElement;while(e?.shadowRoot?.activeElement)e=e.shadowRoot.activeElement;
  if(!e||e===document.body||e===document.documentElement)return {focus:null,live:texts,has_focus:document.hasFocus()};
  window.__voiceCompanionFocusSerial=window.__voiceCompanionFocusSerial||0;
  if(!e.__voiceCompanionFocusId)e.__voiceCompanionFocusId=++window.__voiceCompanionFocusSerial;
  const tag=e.tagName.toLowerCase(),type=(e.type||'').toLowerCase();
  const editable=e.isContentEditable||tag==='textarea'||tag==='input'&&!['checkbox','radio','button','submit','reset','range','color','file','image','hidden'].includes(type);
  const text=editable?(e.isContentEditable?e.innerText:String(e.value||'')):'';
  return {has_focus:document.hasFocus(),live:texts,focus:{id:e.__voiceCompanionFocusId,label:accessibleLabel(e),role:accessibleLabel.role(e)||'',tag,type,
    value_text:e.getAttribute('aria-valuetext')||'',value_now:e.getAttribute('aria-valuenow')??(['range','number'].includes(type)?e.value:null),
    checked:accessibleLabel.checked(e),expanded:e.getAttribute('aria-expanded'),selected:tag==='select'?(e.selectedOptions[0]?.text||''):'',
    editable,password:type==='password',value:type==='password'?'':text.slice(0,2000),caret:type!=='password'&&typeof e.selectionStart==='number'?e.selectionStart:null}};
}'''
