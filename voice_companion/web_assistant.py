"""Guided, self-voicing browsing in a separate browser profile.

The browser is opened only after a user command. Page text is untrusted and is
never treated as an instruction to the assistant. No credentials are stored by
this module; the browser retains its own session in its dedicated profile.
"""
from list_announcements import name_first
from field_selection import FieldSelection, selection_command
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, quote

from browser_navigation import prepare_address
from web_browse import BrowseCursor,QUICK_KEYS,SEMANTICS,describe,kind,voice_browse_key
from reading_navigation import ReadingCursor, reading_request
from dictation_text import clean_dictation


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
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def add(self, name, url):
        name = _plain(name)
        if not name or len(name) > 60 or any(c in name for c in '\r\n'):
            raise WebError('Use a short name for the favorite.')
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname:
            raise WebError('Only secure HTTPS pages can be saved.')
        data = self.all()
        data[name.casefold()] = {'name': name, 'url': url}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def get(self, name):
        return self.all().get(_plain(name).casefold())


class BrowserBackend:
    """Playwright uses a dedicated profile, never the user's normal one."""
    BROWSERS = ('edge', 'chrome', 'brave', 'firefox')

    def __init__(self, folder, browser='edge'):
        self.folder = Path(folder)
        self.browser = browser.lower()
        if self.browser not in self.BROWSERS:
            raise WebError('Choose Edge, Chrome, Brave, or Firefox.')
        self.playwright = self.context = self.page = None

    def start(self):
        if self.page and not self.page.is_closed(): return
        try:
            from playwright.sync_api import sync_playwright
            bundled = Path(getattr(__import__('sys'), '_MEIPASS', Path(__file__).parent)) / 'browser-runtime'
            if bundled.exists(): os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(bundled)
            self.folder.mkdir(parents=True, exist_ok=True)
            self.playwright = sync_playwright().start()
            if self.browser == 'firefox':
                engine, kwargs = self.playwright.firefox, {}
            else:
                engine = self.playwright.chromium
                kwargs = {'channel': {'edge':'msedge', 'chrome':'chrome'}.get(self.browser)}
                if self.browser == 'brave':
                    candidates = [Path(os.environ.get('PROGRAMFILES', '')) / 'BraveSoftware/Brave-Browser/Application/brave.exe',
                                  Path(os.environ.get('PROGRAMFILES(X86)', '')) / 'BraveSoftware/Brave-Browser/Application/brave.exe',
                                  Path(os.environ.get('LOCALAPPDATA', '')) / 'BraveSoftware/Brave-Browser/Application/brave.exe']
                    executable = next((p for p in candidates if p.is_file()), None)
                    if not executable: raise WebError('Brave is not installed in a standard Windows location.')
                    kwargs = {'executable_path': str(executable)}
            self.context = engine.launch_persistent_context(
                str(self.folder), headless=False, accept_downloads=False, **kwargs)
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.page.set_default_timeout(10000)
        except Exception as exc:
            self.close()
            raise WebError('The guided ' + self.browser.capitalize() +
                           ' browser could not start. Ask your helper to check that browser and the installation.') from exc

    def close(self):
        try:
            if self.context: self.context.close()
        finally:
            if self.playwright: self.playwright.stop()
            self.page = self.context = self.playwright = None

    def goto(self, url):
        self.start()
        self.page.goto(url, wait_until='domcontentloaded', timeout=30000)

    def back(self):
        self.page.go_back(wait_until='domcontentloaded', timeout=30000)

    def settle(self):
        # Some sites populate links after the first page paint.
        self.page.wait_for_timeout(500)

    def snapshot(self):
        self.start()
        script = '''() => {
          const visible = e => [...e.getClientRects()].some(r=>r.width>0&&r.height>0) && getComputedStyle(e).visibility !== 'hidden';
          const accessibleLabel=e=>{const root=e.getRootNode();const refs=(e.getAttribute('aria-labelledby')||'').split(/\\s+/).filter(Boolean).map(id=>root.querySelector('#'+CSS.escape(id))?.textContent||'').join(' ');return (e.getAttribute('aria-label')||refs||(e.labels&&[...e.labels].map(x=>x.innerText).join(' '))||e.getAttribute('placeholder')||(e.tagName==='IMG'?e.alt:'')||e.innerText||e.getAttribute('title')||(e.tagName==='INPUT'&&['submit','button','reset'].includes(e.type)?e.value:'')||(e.tagName==='A'&&e.href?new URL(e.href).hostname:'')||'').trim().replace(/\\s+/g,' ').slice(0,120);};
          const text = e => (e?.innerText || e?.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 180);
          // Include open shadow roots used by modern sites and search widgets.
          const selector='a,button,input,textarea,select,[role="button"],[role="link"],[role="checkbox"],[role="radio"],[role="textbox"],[role="combobox"],[contenteditable="true"]';
          const all = root => [...root.querySelectorAll('*')].flatMap(e =>
            [e, ...(e.shadowRoot ? all(e.shadowRoot) : [])]);
          const priority=document.querySelector('#search,#main,main,[role="main"],article');
          const nodes=[...new Set([...(priority ? all(priority).filter(e=>e.matches(selector)) : []),
                                   ...all(document).filter(e=>e.matches(selector))])];
          const controls = [];
          for (let i=0; i<nodes.length && controls.length<500; i++) {
            const e=nodes[i]; if (!visible(e) || e.closest('[aria-hidden="true"],[inert]')) continue;
            const tag=e.tagName.toLowerCase(), type=(e.type || '').toLowerCase();
            if (['hidden','submit-image'].includes(type)) continue;
            let label=accessibleLabel(e);
            label=(label || '').trim().replace(/\\s+/g,' ').slice(0,120);
            if (!label) continue;
            const role=(tag==='a' && e.href ? 'link' : e.getAttribute('role')) || (tag==='button'||tag==='input'&&['submit','button','reset','image'].includes(type)?'button':
                ['input','textarea','select'].includes(tag)?'field':'');
            if (!role) continue;
            e.setAttribute('data-voice-companion-key', String(i));
            controls.push({key:String(i), role, label, tag, type,
                in_form:!!e.closest('form'),
                required:!!e.required, disabled:!!e.disabled, radio_group:e.name ? [...document.forms].indexOf(e.form)+':'+e.name : '', checked:!!e.checked||e.getAttribute('aria-checked')==='true',
                options:tag==='select'?[...e.options].map(o=>({label:o.text.trim().slice(0,80), value:o.value})).slice(0,30):[],
                selected_label:tag==='select'?(e.selectedOptions[0]?.text.trim().slice(0,80) || ''):'',
                href:tag==='a'?e.href:'', value:type==='password'?'':
                (['input','textarea','select'].includes(tag)?String(e.value || '').slice(0,2000):''),
                value_truncated:type!=='password' && String(e.value || '').length>2000,
                filled:type==='password'?!!e.value:false});
          }
          // Read the full visible page. Sites may put most content outside main.
          /*SEMANTICS*/
          const bodyText=output.trimEnd().slice(0,200000);
          const frameUrls=[...document.querySelectorAll('iframe')].map(e=>e.src || '');
          const challenge=/(captcha|recaptcha|hcaptcha|unusual traffic|just a moment)/i
              .test((document.title || '')+' '+location.href) ||
              /(verify (?:that )?you(?:'re| are) human|unusual traffic|complete the security check|checking your browser)/i
              .test(bodyText.slice(0,1000)) ||
              frameUrls.some(url=>/(recaptcha|hcaptcha|turnstile|arkoselabs|captcha)/i.test(url));
          return {title:document.title || location.hostname, url:location.href,
                  text:bodyText, elements:elements.filter(e=>e.start<200000), elements_truncated:elements.length>=5000, text_truncated:(document.body?.innerText || '').length>200000, controls, challenge,
                  controls_truncated:nodes.length>500,
                  visible_frames:[...document.querySelectorAll('iframe')].filter(visible).length};
        }'''
        script=script.replace('/*SEMANTICS*/',SEMANTICS)
        result = self.page.evaluate(script)
        # Playwright can inspect cross-origin frames without asking page JS to
        # cross the browser's origin boundary.
        for index, frame in enumerate(self.page.frames):
            if frame == self.page.main_frame: continue
            try:
                frame_element=frame.frame_element()
                if not frame_element.is_visible() or frame_element.evaluate("e=>!!e.closest('[aria-hidden=\"true\"],[inert]')"):continue
                part = frame.evaluate(script)
            except Exception:
                continue
            for control in part['controls']:
                control['frame_index'] = index
                control['frame_url'] = part['url']
            result['controls'].extend(part['controls'])
            result['challenge'] = result.get('challenge', False) or part.get('challenge', False)
            if part['text']:
                base=len(result['text'])+1
                part['elements']=[e for e in part.get('elements',[]) if e.get('end',0)<=50000]
                for element in part.get('elements',[]):
                    element.update(frame_index=index,frame_url=part['url']);element['start']+=base;element['end']+=base
                    if element.get('table'):element['table']='frame'+str(index)+':'+element['table']
                    if element.get('role')=='table':element['key']='frame'+str(index)+':'+element['key']
                result.setdefault('elements',[]).extend(part.get('elements',[]))
                result['text'] += '\n' + part['text'][:50000]
                result['text_truncated'] = result.get('text_truncated', False) or part.get('text_truncated', False) or len(part['text']) > 50000
        return result

    def focused_control(self):
        """Read current keyboard focus; never expose secret field contents."""
        self.start()
        script = '''() => {
          let e=document.activeElement;while(e?.shadowRoot?.activeElement)e=e.shadowRoot.activeElement;
          if (!e || e===document.body) return null;
          const label=(e.getAttribute('aria-label') || (e.labels && [...e.labels].map(x=>x.innerText).join(' ')) ||
            e.getAttribute('placeholder') || e.innerText || e.getAttribute('title') || e.tagName).trim().replace(/\\s+/g,' ').slice(0,120);
          return {label, tag:e.tagName.toLowerCase(), type:(e.type || '').toLowerCase(),
            value:e.type==='password'?'':String(e.value || '').slice(0,300),
            checked:!!e.checked, selected:e.tagName==='SELECT'?(e.selectedOptions[0]?.text || ''):''};
        }'''
        for frame in reversed(self.page.frames):
            try:
                item=frame.evaluate(script)
                if item and item.get("tag") not in ("iframe","frame"):return item
            except Exception:continue
        return None

    def dictate_focused(self, spoken, literal=False):
        self.start()
        for frame in self.page.frames:
            try:
                target = frame.evaluate('''() => {
                  let e=document.activeElement;while(e?.shadowRoot?.activeElement)e=e.shadowRoot.activeElement;
                  if (!e || e===document.body) return null;
                  const tag=e.tagName.toLowerCase(), type=(e.type || '').toLowerCase();
                  return {tag, type, editable:!!e.isContentEditable,
                          label:(e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.labels?.[0]?.innerText || e.tagName).slice(0,120)};
                }''')
            except Exception:
                continue
            if not target: continue
            if target['type'] in ('password','hidden','file','number','email','url','tel'):
                return 'That field needs private or precise keyboard entry. I did not type into it.'
            if not (target['editable'] or target['tag'] == 'textarea' or
                    (target['tag'] == 'input' and target['type'] in ('text','search',''))):
                continue
            # Browser keyboard inserts at the caret, retaining native input events.
            self.page.keyboard.insert_text(spoken if literal else clean_dictation(spoken))
            return 'Added text to ' + _plain(target['label']) + '. Review the field before submitting.'
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
        element=frame.locator('[data-voice-companion-key="'+item['key']+'"]')
        if element.count()!=1:raise WebError('The page changed. Refresh it before continuing.')
        current=element.evaluate(LIVE_IDENTITY)
        if current['label'].casefold()!=_label(item).casefold() or current['tag']!=item['tag'] or current['type']!=item.get('type','') or current['href']!=item.get('href',''):
            raise WebError('The control changed. Refresh the page before continuing.')
        if current['disabled']:raise WebError('That control is unavailable.')
        return element

    def activate(self,item):
        element=self._element(item)
        if self.context:before=list(self.context.pages)
        else:before=[]
        element.click(timeout=10000)
        if self.context:
            new=[p for p in self.context.pages if p not in before]
            if new:self.page=new[-1]

    def focus(self,item):self._element(item).focus()

    def fill(self,item,value,private=False):
        element=self._element(item)
        if item.get('type')=='password' and not private:
            raise WebError('Password needs private keyboard entry.')
        element.fill(value,timeout=10000)

    def choose_form_option(self, item, answer):
        element=self._element(item)
        if item['tag'] == 'select':
            options = [o for o in item.get('options', []) if o['label'].casefold() == answer.casefold()]
            if len(options) != 1:
                raise WebError('I could not identify one option. Ask me to repeat the choices.')
            element.select_option(value=options[0]['value'], timeout=10000)
        elif item['type'] in ('checkbox', 'radio'):
            if answer not in ('yes', 'no'):
                raise WebError('Say yes or no for this choice.')
            if item['type'] == 'radio' and answer == 'no':
                return
            if answer == 'yes': element.check(timeout=10000)
            else: element.uncheck(timeout=10000)
        elif item.get('role') in ('checkbox','radio'):
            if answer not in ('yes','no'):raise WebError('Say yes or no for this choice.')
            if item.get('role')=='radio' and answer=='no':return
            checked=element.get_attribute('aria-checked')=='true'
            if checked!=(answer=='yes'):element.click(timeout=10000)
        else:
            raise WebError('That is not a choice field.')


EdgeBackend = BrowserBackend


class WebSession:
    def __init__(self, folder, backend=None):
        self.favorites = Favorites(folder)
        self.folder = Path(folder)
        self.browser = self._browser_preference()
        self.backend = backend or BrowserBackend(self.folder / ('Browser Profile ' + self.browser), self.browser)
        self.injected_backend = backend is not None
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
        self.browse=None;self.keyboard_field=None

    def keyboard(self,key):
        if not self.snapshot:return 'Open a website first.'
        if self.browse is None:self.browse=BrowseCursor(self.snapshot)
        try:
            if self.keyboard_field and key.startswith(('Quick:','Table:')):self.commit_keyboard_field()
            if key.startswith('Quick:'):
                parts=key.split(':');message=self.browse.move(parts[-1],len(parts)>2)
            elif key.startswith('Table:'):message=self.browse.table_move(key.split(':')[-1])
            elif key in ('Tab','ShiftTab'):
                if self.keyboard_field:self.commit_keyboard_field()
                message=self.browse.move('',key=='ShiftTab',tab=True)
            elif key=='Refresh':
                self.backend.page.reload(wait_until='domcontentloaded',timeout=30000)
                return self._refresh()
            elif key=='Back':return self.command('go back')
            elif key=='Done':
                if self.keyboard_field:self.commit_keyboard_field()
                return 'Browse mode.'
            elif key=='Escape':
                if self.keyboard_field:self.keyboard_field=None;return 'Browse mode. Field editing canceled.'
                self.pending=None;self.submit_pending=None;return 'Browse mode. Press Alt+T for a typed command, or say leave website to return to the main menu.'
            elif key in ('Activate','Space'):
                if self.keyboard_field:self.commit_keyboard_field();return 'Browse mode.'
                item=self.browse.current()
                if not item:return 'Move to a link or control first.'
                role=kind(item)
                if role=='edit':
                    self.backend.focus(item);self.keyboard_field=dict(item);self.web_edit_caret=len(item.get('value',''))
                    return item['label']+'. Forms mode. Tab saves and moves to the next control. Escape cancels.'
                if role in ('checkbox','radio'):
                    self.backend.choose_form_option(item,'no' if role=='checkbox' and item.get('checked') else 'yes')
                    return self._refresh_at(item)
                if role=='combobox':return describe(item)+' Use Up or Down to change the selection.'
                if role=='button' and item.get('type')=='submit':return self.prepare_submit()
                if role in ('link','button'):
                    if item.get('href') and urlsplit(item['href']).scheme!='https':return 'Only secure HTTPS links can be opened.'
                    self.choices=[item];return self.choose(1)
                return describe(item)
            elif key in ('Up','Down') and self.browse.current() and kind(self.browse.current())=='combobox':
                item=self.browse.current();options=item.get('options',[])
                if not options:return 'This custom combo box needs direct interaction in the browser.'
                index=next((i for i,o in enumerate(options) if o['label']==item.get('selected_label')),0)
                index=max(0,min(len(options)-1,index+(-1 if key=='Up' else 1)))
                self.backend.choose_form_option(item,options[index]['label']);return self._refresh_at(item)
            else:return self.command({'Up':'previous line','Down':'next line','Left':'previous character','Right':'next character','Home':'read from beginning','End':'read current line'}.get(key,'read current line'))
            self.reading.position=self.browse.position;self.reading.continuation=self.browse.position
            return message
        except Exception as exc:
            return str(exc) if isinstance(exc,WebError) else 'The page changed or the control could not be used. Press F5 to refresh it.'

    def _refresh_at(self,item):
        snapshot=self.backend.snapshot();self.snapshot=snapshot;self.reading.set_text(snapshot.get('text',''))
        self.browse=BrowseCursor(snapshot)
        target=next((e for e in self.browse.elements if e.get('key')==item.get('key') and e.get('frame_index')==item.get('frame_index') and e.get('label')==item.get('label')),None)
        if target:
            self.browse.index=self.browse.elements.index(target);self.browse.position=target.get('start',0);self.reading.position=self.browse.position
            return describe(target)
        return 'The page changed. Press F5 to refresh.'

    def commit_keyboard_field(self,value=None):
        item=self.keyboard_field
        if item is None:return
        text=item.get('value','') if value is None else value
        self.backend.fill(item,text,private=item.get('type')=='password')
        self.keyboard_field=None;self.submit_pending=None;self.review_submission=None;self._refresh_at(item)

    def insert_line_break(self):
        if not self.snapshot: return 'Open a website first.'
        try: return self.backend.insert_line_break()
        except Exception: return 'I could not insert the line break. Focus a multiline field and try again.'

    def select_focused(self, spoken):
        if not self.snapshot: return 'Open a website first.'
        try: return self.backend.select_focused(spoken)
        except Exception: return 'I could not select text. Focus the browser field and try again.'

    def dictate_to_focus(self, spoken, literal=False):
        if not self.snapshot: return 'Open a website first.'
        self.submit_pending = None
        self.review_submission = None
        if self.keyboard_field:
            if self.keyboard_field.get('type')=='password':return 'Use private keyboard entry for this field.'
            self.keyboard_field['value']=self.keyboard_field.get('value','')+(spoken if literal else clean_dictation(spoken))
            return 'Text entered. Say save field or next control.'
        try: return self.backend.dictate_focused(spoken,literal=True) if literal else self.backend.dictate_focused(spoken)
        except Exception: return 'I could not enter that text. Check the browser field before trying again.'

    def _browser_preference(self):
        try:
            choice = (self.folder / 'browser-choice.txt').read_text(encoding='utf-8').strip().lower()
            return choice if choice in BrowserBackend.BROWSERS else 'firefox'
        except OSError:
            return 'firefox'

    def choose_browser(self, name):
        name = name.lower()
        if name not in BrowserBackend.BROWSERS:
            return 'Choose Edge, Chrome, Brave, or Firefox.'
        if self.browser != name:
            if not self.injected_backend:
                try: self.backend.close()
                except Exception: pass
                self.backend = BrowserBackend(self.folder / ('Browser Profile ' + name), name)
            self.browser = name
            self.snapshot = None
            self.submit_pending = None
            self.review_submission = None
        if not self.injected_backend:
            self.folder.mkdir(parents=True, exist_ok=True)
            (self.folder / 'browser-choice.txt').write_text(name, encoding='utf-8')
        return 'Selected ' + name.capitalize() + '. Say open website followed by the address. This uses a separate browser profile.'

    def _refresh(self):
        self.snapshot = self.backend.snapshot()
        # DOMContentLoaded often precedes text inserted by the site's scripts.
        # Wait briefly before declaring a genuinely empty page.
        if not self.snapshot.get('challenge') and not str(self.snapshot.get('text', '')).strip():
            for _ in range(12):
                if not hasattr(self.backend, 'settle'): break
                self.backend.settle()
                self.snapshot = self.backend.snapshot()
                if self.snapshot.get('challenge') or str(self.snapshot.get('text', '')).strip(): break
        self.reading.set_text(self.snapshot.get('text', ''), reset=True)
        self.browse=BrowseCursor(self.snapshot);self.keyboard_field=None
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
        url = self.snapshot['url']
        if self.snapshot.get('challenge'):
            return 'This site is asking for human verification. I cannot complete that challenge by voice. A helper can inspect the browser, or you can try another search engine.'
        if urlsplit(url).scheme != 'https':
            return 'This page is not secure. Do not enter private information here.'
        if not str(self.snapshot.get('text', '')).strip():
            return ('I found controls but no readable page text. Say list links or try read page again.'
                    if self.snapshot.get('controls') else
                    'I could not read the page content after waiting for it to load. Try read page again or ask your helper to inspect the browser.')
        return ('Page: ' + _plain(self.snapshot['title']) + '. Site: ' +
                (urlsplit(url).hostname or 'unknown') + '. ' + self.read())

    def open(self, url):
        try:
            safe, _ = prepare_address(url)
            self.backend.goto(safe)
            return self._refresh()
        except (ValueError, WebError) as exc:
            return str(exc)
        except Exception:
            return 'The page did not open. Check the address and internet connection.'

    def search(self, query, provider='brave'):
        if not _plain(query): return 'What would you like to look up?'
        urls = {
            'google': ('Google', 'https://www.google.com/search?q='),
            'brave': ('Brave Search', 'https://search.brave.com/search?q='),
            'duckduckgo': ('DuckDuckGo', 'https://html.duckduckgo.com/html/?q='),
        }
        order = (['google','brave','duckduckgo'] if provider == 'google' else ['brave','duckduckgo'])
        failures = []
        for engine in order:
            name, base = urls[engine]
            try:
                self.backend.goto(base + quote(query))
                result = self._refresh()
                if self.snapshot.get('challenge') or not self._search_readable():
                    if hasattr(self.backend, 'settle'): self.backend.settle()
                    result = self._refresh()
                if self.snapshot.get('challenge'):
                    failures.append(name + ' asked for human verification')
                    continue
                if not self._search_readable():
                    failures.append(name + ' had no readable results')
                    continue
                self.last_search = query
                return (('I switched from ' + ', '.join(failures) + ' to ' + name + '. ') if failures else
                        'Searching with ' + name + '. ') + result
            except Exception:
                failures.append(name + ' could not open')
        return ('I could not read search results. ' + '; '.join(failures) +
                '. A helper can inspect the browser. I cannot solve human verification by voice.')

    def _search_readable(self):
        if not self.snapshot: return False
        links = [c for c in self.snapshot.get('controls', []) if c.get('role') == 'link'
                 and urlsplit(c.get('href', '')).scheme == 'https']
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
        controls = [c for c in self.snapshot['controls'] if c['role'] in ('link','button')]
        self.choices = match_choices(controls, request)
        if not self.choices:
            # A result can arrive after the initial page paint.
            for _ in range(3):
                try:
                    if hasattr(self.backend, 'settle'): self.backend.settle()
                    self.snapshot = self.backend.snapshot()
                except Exception:
                    break
                controls = [c for c in self.snapshot['controls'] if c['role'] in ('link','button')]
                self.choices = match_choices(controls, request)
                if self.choices: break
        if not self.choices:
            return ('I could not identify a matching link or button. The site may have changed or hidden its controls. '
                    'Say list links to hear what I can find, or ask your helper to inspect the page.')
        self.suggestion_index = 0
        return self._suggest()

    def list_links(self, direction='first'):
        if not self.snapshot: return 'Open a website first.'
        if direction == 'first':
            try: self.snapshot = self.backend.snapshot()
            except Exception: return 'The links could not be read. Try read page.'
            self.link_offset = 0
            self.link_position = None
            self.pending = None
            self.choices = []
        links = [c for c in self.snapshot['controls'] if c['role'] == 'link' and
                 urlsplit(c.get('href', '')).scheme == 'https']
        if not links and direction == 'first':
            for _ in range(3):
                try:
                    if hasattr(self.backend, 'settle'): self.backend.settle()
                    self.snapshot = self.backend.snapshot()
                except Exception: break
                links = [c for c in self.snapshot['controls'] if c['role'] == 'link' and
                         urlsplit(c.get('href', '')).scheme == 'https']
                if links: break
        if not links:
            return 'I could not identify links on this page. They may be loading or inaccessible to this browser helper. Ask your helper to inspect the page.'
        if direction != 'first': return self.move_link(1 if direction == 'next' else -1)
        self.link_position = 0
        self.list_focus = 'links'
        self.pending = links[0]
        return (name_first('Found '+str(len(links))+' links. Link 1 of '+str(len(links))+': '+_label(links[0])+'. '+
                self._suggest_link(links[0])+' Say next or previous to browse.'))

    def open_link(self, number):
        if not self.snapshot: return 'Open a website first.'
        links = [c for c in self.snapshot['controls'] if c['role'] == 'link' and
                 urlsplit(c.get('href', '')).scheme == 'https']
        index = int(number)-1
        if index < 0 or index >= len(links): return 'That link number is not on this page. Say list links.'
        self.link_position = index
        self.pending = links[index]
        return self._suggest_link(links[index])

    def move_link(self, direction):
        if not self.snapshot: return 'Open a website first.'
        links = [c for c in self.snapshot['controls'] if c['role'] == 'link' and
                 urlsplit(c.get('href', '')).scheme == 'https']
        if not links: return 'No links are listed. Say list links.'
        position = self.link_position
        index = (0 if direction > 0 else len(links)-1) if position is None else position + direction
        if index < 0 or index >= len(links): return 'That is the '+('first' if index<0 else 'last')+' link on this page.'
        self.link_position = index
        self.list_focus = 'links'
        self.pending = links[index]
        return _label(links[index]) + ', ' + str(index+1) + ' of ' + str(len(links)) + '. ' + self._suggest_link(links[index])

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
        if destination and urlsplit(destination).scheme != 'https':
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
        links = [c for c in self.snapshot['controls'] if c['role'] in ('link','button')]
        if not links:
            try:
                if hasattr(self.backend, 'settle'): self.backend.settle()
                self.snapshot = self.backend.snapshot()
                links = [c for c in self.snapshot['controls'] if c['role'] in ('link','button')]
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
        identity = (item.get('label'), item.get('tag'), item.get('type'))
        if identity == self.last_focus: return ''
        self.last_focus = identity
        return self._describe_focus(item)

    @staticmethod
    def _describe_focus(item):
        kind = item.get('type') or item.get('tag') or 'control'
        if kind == 'password': state = 'Private field. Type it without speaking it aloud.'
        elif kind in ('checkbox','radio'): state = 'Checked.' if item.get('checked') else 'Not checked.'
        elif item.get('tag') == 'select': state = 'Selected ' + _plain(item.get('selected')) + '.'
        elif item.get('tag') in ('input','textarea'): state = 'Current value ' + (_plain(item.get('value')) or 'blank') + '.'
        else: state = ''
        return 'Focused ' + _plain(item.get('label')) + ', ' + kind + '. ' + state

    def handoff(self):
        if not self.snapshot: return 'No website is open. Ask your helper to open the site with you.'
        url = urlsplit(self.snapshot['url'])
        return ('Ask your helper to look at the ' + self.browser.capitalize() + ' window titled ' + _plain(self.snapshot['title']) +
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
        if destination and urlsplit(destination).scheme != 'https':
            return 'That link does not use a secure web address. Ask your helper to inspect it.'
        self.pending = item
        self.suggestion_index = int(number) - 1
        host = urlsplit(destination).hostname if destination else None
        return ('I heard open ' + _label(item) + (', on ' + host if host else '') +
                '. Is that right? Say yes or no.')

    def confirm(self):
        if not self.pending: return 'There is no web action waiting for confirmation.'
        item = self.pending
        self.pending = None
        if item.get('role') == 'button' and (item.get('in_form') or
                re.search(r'\b(pay|purchase|transfer|send|submit|delete|apply|confirm|place order)\b', _label(item), re.I)):
            return 'This action may submit information or move money. Ask your helper to review and complete it in the browser.'
        try:
            self.backend.activate(item)
            return self._refresh()
        except WebError as exc: return str(exc)
        except Exception: return 'The page changed or the action did not complete. Say read page to check it.'

    def fields(self, guided=False):
        if not self.snapshot: return 'Open a website first.'
        self.choices = [c for c in self.snapshot['controls'] if c['tag'] in ('input','textarea','select')
                        and c['type'] not in ('hidden','submit','button','image')][:30]
        if not self.choices: return 'I did not find readable form fields on this page.'
        if guided:
            self.form_fields = list(self.choices)
            self.form_index = 0
            self.form_answers = []
            self.form_page = self.snapshot['url']
            self.private_fields_confirmed = False
            return self._ask_field()
        self.list_focus='fields'
        self.field_list_index=0
        self.pending=None
        return ('Found '+str(len(self.choices))+' fields. Field 1: '+_label(self.choices[0])+
                '. Say next or previous, then that one to answer it. Passwords need private entry in Edge.')

    def _ask_field(self):
        while self.form_index is not None and self.form_index < len(self.form_fields):
            item = self.form_fields[self.form_index]
            if item['type'] in ('password','file'):
                self.form_index += 1
                continue
            if item['tag'] == 'select':
                options = [o['label'] for o in item.get('options', []) if o['label']][:10]
                return ('Field ' + str(self.form_index + 1) + ': ' + _label(item) +
                        '. Choices: ' + ', '.join(options) + '. Which one? Say skip to leave it unchanged.')
            if item['type'] in ('checkbox','radio'):
                return ('Field ' + str(self.form_index + 1) + ': ' + _label(item) +
                        '. Say yes or no, or skip to leave it unchanged.')
            return ('Field ' + str(self.form_index + 1) + ': ' + _label(item) +
                    '. What should I enter? Say skip to leave it blank, or cancel form to stop.')
        self.form_index = None
        manual = [_label(item) for item in self.form_fields if item['type'] in ('password','file')]
        return ('That is the end of the text fields. ' +
                ('These need private or manual entry: ' + ', '.join(manual) + '. ' if manual else '') +
                'Say review form to hear the answers entered by voice. Review every answer and other controls in Edge with your helper before submitting.')

    def _answer_field(self, spoken):
        item = self.form_fields[self.form_index]
        if spoken.lower() in ('skip','skip field'):
            self.form_index += 1
            return 'Skipped ' + _label(item) + '. ' + self._ask_field()
        if spoken.lower() in ('repeat','repeat question','what field'):
            return self._ask_field()
        if spoken.lower() in ('cancel form','stop form'):
            self.form_index = None
            return 'Stopped filling the form. Entries already made remain on the page; nothing was submitted.'
        value = _plain(spoken)
        if item['type'] in ('checkbox','radio'):
            if value.casefold() not in ('yes','no'):
                return 'Say yes or no for ' + _label(item) + ', or say skip.'
            value = value.casefold()
        elif item['tag'] == 'select':
            options = [o['label'] for o in item.get('options', []) if o['label'].casefold() == value.casefold()]
            if len(options) != 1:
                return 'I could not identify one choice. ' + self._ask_field()
            value = options[0]
        else:
            value = clean_dictation(value)
        try:
            if item['tag'] == 'select' or item['type'] in ('checkbox','radio'):
                self.backend.choose_form_option(item, value)
            else:
                self.backend.fill(item, value)
        except Exception:
            self.form_index = None
            return 'The field changed. Stop and review the page before trying again.'
        self.form_index += 1
        self.form_answers.append({'label': _label(item), 'value': value, 'key': item['key'],
                                  'tag': item['tag'], 'type': item['type']})
        return 'Recorded ' + value + ' for ' + _label(item) + '. ' + self._ask_field()

    def review_form(self):
        if not self.snapshot: return 'Open a website first.'
        try: current = self.backend.snapshot()
        except Exception: return 'I could not read the current form. Ask your helper to inspect Edge.'
        if current['url'] != self.snapshot['url']:
            return 'The page changed. Say read page, then review form again.'
        self.review_submission = None
        self.review_parts = self._form_parts(current)
        self.review_cursor = 0
        return self._next_review()

    @staticmethod
    def _form_controls(current):
        return [c for c in current.get('controls', []) if c.get('tag') in ('input','textarea','select')
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
            elif c.get('tag') == 'select': value = c.get('selected_label') or 'nothing selected'
            elif c.get('type') in ('checkbox','radio'): value = 'checked' if c.get('checked') else 'not checked'
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
            return result + 'Say continue review for the remaining fields. Submission is not ready yet.'
        if self.review_submission:
            pending = self.review_submission
            self.review_submission = None
            self.submit_pending = pending
            return (result + 'The button is ' + _label(pending['button']) + ' on ' +
                    (urlsplit(pending['url']).hostname or 'this site') +
                    '. Is every field right? Say yes to submit, or no to cancel.')
        return result + 'End of form review. Nothing has been submitted.'

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
            return 'I cannot inspect the whole form on this page. Ask your helper to review it in Edge.'
        if any(c.get('value_truncated') for c in self._form_controls(current)):
            return 'A form answer is too long for safe spoken review. Ask your helper to check it in Edge.'
        missing = [c for c in self._form_controls(current) if c.get('required') and not c.get('disabled') and
                   ((not c.get('filled')) if c.get('type') == 'password' else
                    (not any(other.get('type')=='radio' and other.get('radio_group')==c.get('radio_group') and other.get('frame_index')==c.get('frame_index') and other.get('checked') for other in self._form_controls(current))) if c.get('type')=='radio' and c.get('radio_group') else
                    (not c.get('checked')) if c.get('type') in ('checkbox','radio') else
                    (not c.get('value')))]
        if missing:
            return 'Required fields appear empty: ' + ', '.join(_label(c) for c in missing) + '. Complete them before submitting.'
        private = [c for c in controls if c['type'] in ('password','file') and c.get('required')]
        if private and not self.private_fields_confirmed:
            return ('This form has required private fields: ' + ', '.join(_label(c) for c in private) +
                    '. Complete them privately in Edge. Then say private fields complete before reviewing submission.')
        buttons = [c for c in controls if c['role'] == 'button' and
                   (c.get('in_form') or re.search(r'\b(submit|apply|continue|next|send)\b', _label(c), re.I))]
        if len(buttons) != 1:
            return 'I cannot identify one final form button. Ask your helper to inspect this page.'
        button = buttons[0]
        if re.search(r'\b(pay|purchase|transfer|withdraw|deposit|wire|send money)\b',
                     _label(button) + ' ' + current.get('title',''), re.I):
            return 'This may move money. Ask your helper to review the accounts, amount, and final action in Edge.'
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
            self.backend.activate(button)
            result = self._refresh()
            return 'I pressed ' + _label(button) + '. Check the result before trying again. ' + result
        except Exception:
            return 'The submission result is uncertain. Do not repeat it until you or your helper checks the site.'

    def fill(self, number, value):
        try:
            item = self._choice(number)
            if item['role'] != 'field': raise WebError('That number is not a field. Say list fields.')
            if item['type'] == 'password':
                raise WebError('Password needs private entry in Edge. Ask your helper if needed; do not say it aloud.')
            if item['type'] in ('password','file','checkbox','radio','submit'):
                raise WebError('This field needs direct interaction in Edge with your helper.')
            self.backend.fill(item, value)
            return 'Filled ' + _label(item) + ' with ' + value + '. Review the form before submitting it.'
        except WebError as exc: return str(exc)
        except Exception: return 'That field changed. Say list fields and try again.'

    def command(self, spoken):
        command = _plain(spoken).rstrip('.!?').lower()
        if command=='refresh page' and self.snapshot:return self.keyboard('Refresh')
        browse_key=voice_browse_key(command)
        if browse_key and self.form_index is None and not self.list_focus:return self.keyboard(browse_key)
        if self.keyboard_field and command.startswith(('type ', 'enter text ', 'dictate ')):
            if self.keyboard_field.get('type')=='password':return 'Use private keyboard entry for this field.'
            value=spoken.split(' ',2)[2] if command.startswith('enter text ') else spoken.split(' ',1)[1]
            self.keyboard_field['value']=value
            return 'Text entered. Say save field, next control, or cancel field editing.'
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
                if not item or kind(item)!='combobox':return 'Move to a combo box first.'
                try:
                    self.backend.choose_form_option(item,spoken.split(' ',2)[2])
                    return self._refresh_at(item)
                except WebError as exc:return str(exc)
                except Exception:return 'The option changed or could not be used. Refresh the page.'
        if selection_command(spoken): return self.select_focused(spoken)
        if command in ('new line','insert new line','line break','carriage return','insert carriage return'): return self.insert_line_break()
        request = reading_request(command)
        if request and self.snapshot:
            unit, direction = request
            self.reading.set_text(self.snapshot.get('text', ''))
            result=self.reading.read(direction == 'top') if unit == 'read' else self.reading.move(unit, direction)
            if self.browse:self.browse.set_position(self.reading.position)
            return result
        match = re.fullmatch(r'(?:use|switch to|choose) (?:browser )?(edge|chrome|brave|firefox)(?: browser)?', command)
        if match: return self.choose_browser(match[1])
        if command in ('which browser','what browser am i using'):
            return 'Guided websites use ' + self.browser.capitalize() + '. Say use Chrome, use Brave, use Firefox, or use Edge to change it.'
        if command in ('where am i','read current field','what field am i on'):
            try: item = self.backend.focused_control()
            except Exception: item = None
            return self._describe_focus(item) if item else 'No readable field has keyboard focus. Press Tab or say list fields.'
        if command in ('continue review','keep reviewing','read next fields'):
            return self._next_review()
        if self.submit_pending and command in ('no','no thanks','cancel','cancel submit'):
            self.submit_pending = None
            return 'No. Form submission canceled. Nothing was submitted.'
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
                if not 0<=index<len(self.favorite_items): return 'That is the '+('first' if step<0 else 'last')+' favorite.'
                self.favorite_index=index
                return name_first('Favorite '+str(index+1)+' of '+str(len(self.favorite_items))+': '+self.favorite_items[index][1]['name']+'. Say that one to open it.')
            if self.list_focus=='fields' and self.choices:
                index=self.field_list_index+step
                if not 0<=index<len(self.choices): return 'That is the '+('first' if step<0 else 'last')+' field.'
                self.field_list_index=index
                return name_first('Field '+str(index+1)+' of '+str(len(self.choices))+': '+_label(self.choices[index])+'. Say that one to answer it.')
            return self.move_link(step)
        if command in ('that one','okay','ok','confirm','confirm that') and self.list_focus=='favorites' and self.favorite_items:
            return self.open(self.favorite_items[self.favorite_index][1]['url'])
        if command in ('that one','okay','ok','confirm','confirm that') and self.list_focus=='fields' and self.choices:
            self.form_fields=list(self.choices)
            self.form_index=self.field_list_index
            self.form_answers=[]
            self.form_page=self.snapshot['url']
            return self._ask_field()
        if self.pending and command in ('yes','yes please','that one','that is it',"that's it",'okay','ok','confirm','confirm that'):
            return self.confirm()
        if self.pending and command in ('no','no thanks','not that one','another one','next one'):
            if self.link_position is not None:
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
            try: return self._refresh()
            except Exception: return 'The page could not be read. Ask your helper to inspect Edge.'
        if command in ('list links','show links','what links are here','read links'): return self.list_links()
        if command in ('next links','more links','next link','following link','move down','next'): return self.move_link(1)
        if command in ('previous links','prior links','previous link','prior link','move up','previous'): return self.move_link(-1)
        match = re.fullmatch(r'(?:open|choose|click) (?:link )?(?:number )?(\d+|one|two|three|four|five|six|seven|eight|nine|ten)(?: link)?', command)
        if match:
            number = {'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'ten':10}.get(match[1],match[1])
            return self.open_link(number)
        if command in ('continue reading','read more'): return self.read()
        if command in ('back','go back','previous page'):
            try: self.backend.back(); return self._refresh()
            except Exception: return 'There is no previous page to read.'
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
            return ('Found '+str(len(self.favorite_items))+' favorites. Favorite 1: '+self.favorite_items[0][1]['name']+
                    '. Say next or previous, then that one to open it.' if self.favorite_items else 'No favorites saved yet.')
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
        match = re.match(r'^google(?: for)?\s+(.+)', spoken, re.I)
        if match: return self.search(match[1], provider='google')
        match = re.match(r'^(?:search(?: the web)?(?: for)?|look up)\s+(.+)', spoken, re.I)
        if match: return self.search(match[1])
        match = re.match(r'^what about\s+(.+)', spoken, re.I)
        if match: return self.search((self.last_search + ' ' + match[1]).strip())
        if command.startswith(('i am looking for ', "i'm looking for ", 'where is ', 'how do i ', 'find ', 'look for ')):
            return self.find(spoken)
        if self.keyboard_field and command in ('cancel','go back','browse mode'):
            self.keyboard_field=None;return 'Browse mode.'
        if self.input_mode == 'mixed' and not command.startswith(('open ', 'go to ', 'search ', 'look up ',
                 'what ', 'help ', 'list ', 'read ', 'fill ', 'submit ', 'click ', 'choose ', 'save ')):
            try:
                result = self.backend.dictate_focused(spoken)
                if not result.startswith('No editable text field'): return result
            except Exception: pass
        return self.find(spoken) if _tokens(spoken) else 'Tell me what you are looking for on this page, or say read page, list fields, or list favorites.'

# The same accessible name is used for snapshot and stale-target validation.
LIVE_IDENTITY=r'''e=>{const root=e.getRootNode();const refs=(e.getAttribute('aria-labelledby')||'').split(/\s+/).filter(Boolean).map(id=>root.querySelector('#'+CSS.escape(id))?.textContent||'').join(' ');const label=(e.getAttribute('aria-label')||refs||(e.labels&&[...e.labels].map(x=>x.innerText).join(' '))||e.getAttribute('placeholder')||(e.tagName==='IMG'?e.alt:'')||e.innerText||e.getAttribute('title')||(e.tagName==='INPUT'&&['submit','button','reset'].includes(e.type)?e.value:'')||(e.tagName==='A'&&e.href?new URL(e.href).hostname:'')||'').trim().replace(/\s+/g,' ').slice(0,120);return {label,tag:e.tagName.toLowerCase(),type:(e.type||'').toLowerCase(),href:e.tagName==='A'?e.href:'',disabled:!!e.disabled||e.getAttribute('aria-disabled')==='true'};}'''
