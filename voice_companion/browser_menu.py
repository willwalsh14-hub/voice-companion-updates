"""Browser activity menus shared by spoken and keyboard commands."""
import json
import time
from pathlib import Path
from menu_navigation import announce_item
from privacy_controls import saved_address


class BrowserMenu:
    def __init__(self, folder):
        self.path = Path(folder) / 'web-history.json'
        self.level = 'menu'
        self.index = None
        self.items = []
        self.action = None
        self.prefix = ''
        self.last_letter = 0
        self.target = None
        self.history()

    def history(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            clean=[x for x in data if isinstance(x,dict) and x.get('name') and saved_address(x.get('url'))==x.get('url') and x.get('url')] if isinstance(data,list) else []
            if isinstance(data,list) and clean!=data:
                temporary=self.path.with_suffix('.tmp');temporary.write_text(json.dumps(clean,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(self.path)
            return clean
        except (OSError, ValueError):
            return []

    def record(self, snapshot):
        url = snapshot.get('url', '')
        if not url or saved_address(url)!=url:
            return
        data = self.history()
        if data and data[0]['url'] == url:
            return
        data.insert(0, {'url': url, 'name': snapshot.get('title') or url})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data[:200], ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def choices(self, session, keep_open):
        items = [('Search or type address', 'search'), ('Go to favorite', 'open'),
                 ('New favorite', 'new'), ('Delete favorite', 'delete'),
                 ('Rename favorite', 'rename'), ('Go back', 'back'),
                 ('Go forward', 'forward'), ('Web history', 'history')]
        if keep_open and session.snapshot:
            items.insert(0,('Resume where you left off', 'resume'))
        return items

    def start(self,resume=False):
        self.level = 'menu'; self.index = None; self.prefix = ''; self.target = None
        return 'Web browser menu. '+('Resume where you left off, ' if resume else '')+'Search or type address, Go to favorite, New favorite, Delete favorite, Rename favorite, Go back, Go forward, or Web history. Use arrows and Enter, or say an option. Escape returns to the main menu.'

    def menu(self, session, keep_open):
        if self.level == 'name':
            return None
        names = [x[0] for x in self.choices(session, keep_open)] if self.level == 'menu' else [x['name'] for x in self.items]
        if names and self.index is not None:self.index=max(0,min(self.index,len(names)-1))
        return (names, self.index, lambda index: setattr(self, 'index', index)) if names else None

    def notice(self, names):
        return announce_item(names[self.index], self.index, len(names), hotkey=False)

    def letter(self, value, names):
        now = time.monotonic()
        value = value.casefold()
        self.prefix = self.prefix + value if now - self.last_letter < 1.2 and self.prefix != value else value
        self.last_letter = now
        matches = [i for i, name in enumerate(names) if name.casefold().startswith(self.prefix)]
        if not matches:
            return 'No item starts with ' + self.prefix + '.'
        self.index = next((i for i in matches if self.index is None or i > self.index), matches[0]) if len(self.prefix) == 1 else matches[0]
        return self.notice(names)

    def process(self, text, session, keep_open):
        from web_assistant import WebError
        command = text.strip().casefold().rstrip('.!?')
        if command=='browser menu':command='web browser menu'
        if command in ('escape', 'cancel', 'close list', 'web browser menu'):
            if self.level == 'menu' and command != 'web browser menu':
                return 'Main menu.', 'main'
            return self.start(resume=keep_open and bool(session.snapshot)), None
        if self.level == 'name':
            name = text.strip()
            if not name:
                return 'Say or type the favorite name, then press Enter.', None
            try:
                if self.action == 'rename':
                    session.favorites.rename(self.target, name)
                else:
                    if session.favorites.get(name):return 'That favorite name already exists. Choose another name.', None
                    session.favorites.add(name, session.snapshot['url'])
            except (ValueError, OSError, KeyError, WebError) as exc:
                return str(exc), None
            result = ('Renamed favorite to ' if self.action == 'rename' else 'Saved favorite ') + name + '.'
            self.start(resume=keep_open and bool(session.snapshot))
            return result + ' Web browser menu.', None
        if command.startswith('go to ') and command not in ('go to favorite','go to bookmark'):
            name=text.strip()[6:]
            item=session.favorites.get(name)
            if item:
                self.start(resume=keep_open and bool(session.snapshot));return '',('open',item['url'])
        state = self.menu(session, keep_open)
        names = state[0] if state else []
        if command.startswith('letter:'):
            return self.letter(text[7:], names), None
        if command in ('next', 'previous', 'home', 'end'):
            self.prefix = ''
            if not names:
                return 'This list is empty.', None
            target = 0 if command == 'home' else len(names)-1 if command == 'end' else (0 if command == 'next' else len(names)-1) if self.index is None else self.index + (1 if command == 'next' else -1)
            if not 0 <= target < len(names) or target == self.index:
                return ('Start of list.' if command in ('previous', 'home') else 'End of list.'), None
            self.index = target
            return self.notice(names), None
        if self.level == 'menu':
            aliases = {'go to favorite':'open','list favorites':'open','new favorite':'new','add favorite':'new','delete favorite':'delete','rename favorite':'rename','search':'search', 'search or type address':'search', 'type address':'search', 'go to bookmark':'open', 'list bookmarks':'open', 'list favorites':'open', 'new bookmark':'new', 'add bookmark':'new', 'delete bookmark':'delete', 'rename bookmark':'rename', 'go back':'back', 'back':'back', 'go forward':'forward', 'forward':'forward', 'web history':'history', 'history':'history', 'resume':'resume', 'resume where you left off':'resume'}
            action = aliases.get(command)
            if command in ('okay', 'ok', 'confirm', 'confirm that', 'that one', 'enter') and self.index is not None:
                action = self.choices(session, keep_open)[self.index][1]
            if action in ('search', 'back', 'forward'):
                return '', action
            if action == 'resume':
                return ('', 'resume') if keep_open and session.snapshot else ('No page is available to resume.', None)
            if action == 'new':
                if not session.snapshot:
                    return 'Open a page before creating a favorite.', None
                self.level = 'name'; self.action = 'new'
                return 'New favorite. Say or type the favorite name, then press Enter. Escape cancels.', None
            if action in ('open', 'delete', 'rename', 'history'):
                self.action = action; self.level = 'list'; self.prefix = ''
                self.items = self.history() if action == 'history' else list(session.favorites.all().values())
                self.index = 0 if self.items else None
                return ('Web history. ' if action == 'history' else 'Favorites. ') + (self.notice([x['name'] for x in self.items]) + ' Type letters to narrow the list, then Enter, or say the name.' if self.items else 'The list is empty. Escape returns to the web browser menu.'), None
            return 'Choose a web browser menu option.', None
        if names:
            if command not in ('okay', 'ok', 'confirm', 'confirm that', 'that one', 'enter'):
                matches = [i for i, name in enumerate(names) if name.casefold().startswith(command)]
                if len(matches) != 1:
                    return 'Say more of the name, or use arrows and Enter.', None
                self.index = matches[0]
            item = self.items[self.index]
            if self.action in ('open', 'history'):
                self.start(resume=keep_open and bool(session.snapshot))
                return '', ('open', item['url'])
            if self.action == 'delete':
                try:session.favorites.delete(item['name'])
                except (ValueError,OSError) as exc:return str(exc),None
                self.items.pop(self.index)
                self.index = min(self.index, len(self.items)-1) if self.items else None
                return 'Deleted favorite ' + item['name'] + '.' + (' ' + self.notice([x['name'] for x in self.items]) if self.items else ' The list is empty.'), None
            self.target = item['name']; self.level = 'name'
            return 'Rename favorite ' + self.target + '. Say or type the new name, then press Enter. Escape cancels.', None
        return 'The list is empty. Escape returns to the web browser menu.', None
