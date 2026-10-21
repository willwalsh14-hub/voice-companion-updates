"""Word processor, document commands, and local media navigation."""
import json
from pathlib import Path
from menu_navigation import announce_item, bounded_index, next_match

DOCUMENT_TYPES = {'.docx', '.txt', '.md', '.csv', '.tsv', '.html', '.htm', '.pdf', '.odt', '.epub'}
MEDIA_TYPES = {'.mp3', '.wav', '.ogg', '.oga', '.opus', '.flac', '.m4a', '.aac', '.mp4', '.m4v', '.webm', '.ogv', '.mov'}

# Every menu action uses the existing voice command path, including its prompts,
# save confirmations, undo history and validation. A value opens a text prompt.
DOCUMENT_ACTIONS = {
    'File': [('Save', 'Ctrl+S', 'save document'), ('Save as', 'Ctrl+Shift+S', 'save as'),
             ('Rename', 'F2', 'name document'), ('Open', 'Ctrl+O', '@open'),
             ('New', 'Ctrl+N', 'create a document'), ('Close', 'Ctrl+W', 'leave document')],
    'Edit': [('Undo', 'Ctrl+Z', 'undo'), 
             ('Select all', 'Ctrl+A', 'select all'), ('Copy', 'Ctrl+C', 'copy selection'),
             ('Cut', 'Ctrl+X', 'cut selection'), ('Paste', 'Ctrl+V', 'paste'),
             ('Find text', 'Ctrl+F', 'find {value}'), ('Replace text', 'Ctrl+H', 'replace {value}'),
             ('Select text', '', 'select {value}'), ('Delete selected text', '', 'delete selection'),
             ('Scratch last dictation', 'Ctrl+Shift+Z', 'scratch that'), ('Describe selection', 'Insert+Shift+Down', 'what is selected')],
    'Format': [('Bold', 'Ctrl+B', 'toggle bold'), ('Italic', 'Ctrl+I', 'toggle italic'),
               ('Underline', 'Ctrl+U', 'toggle underline'), ('Remove bold', '', 'remove bold'),
               ('Remove italic', '', 'remove italic'), ('Remove underline', '', 'remove underline'),
               ('Font', 'Ctrl+Shift+F', 'set font {value}'), ('Font size', 'Ctrl+Shift+P', 'set font size {value}'),
               ('Line spacing', '', 'line spacing {value}'), ('Single spacing', 'Ctrl+1', 'single space'),
               ('Double spacing', 'Ctrl+2', 'double space'), ('One and a half spacing', 'Ctrl+5', 'line spacing 1.5'),
               ('Heading', 'Ctrl+Alt+1', 'heading one'), ('Normal text', 'Ctrl+Shift+N', 'normal text'),
               ('Formatting details', 'Insert+F', 'what formatting')],
    'Insert': [('New line', 'Enter', 'new line'), ('New paragraph', 'Ctrl+Enter', 'new paragraph'), ('Table', '', 'create table with {value}'),
               ('Bulleted list', '', 'bulleted list'), ('Numbered list', '', 'numbered list'),
               ('End list', '', 'end list')],
    'Read and navigate': [('Read document', 'Ctrl+Shift+R', 'read document'),
                          ('Read paragraph', '', 'read paragraph'), ('Read sentence', '', 'read sentence'),
                          ('Read word', '', 'read word'), ('Next heading', '', 'next heading'),
                          ('List headings', '', 'list headings'), ('Navigate document', 'Ctrl+G', '{value}'),
                          ('Beginning of document', 'Ctrl+Home', 'go to beginning of document'),
                          ('End of document', 'Ctrl+End', 'go to end of document'),
                          ('Document details', '', 'where am i')],
    'Dictation': [('Start dictation', 'F4', 'start dictation'), ('Pause dictation', 'Shift+F4', 'pause dictation')],
    'More commands': [('Run any document voice command', 'Alt+T', '{value}'),
                      ('Document help', 'F1', 'help with documents'), ('Word processor options', 'Alt+O', '@options')],
}
DOCUMENT_ACTIONS['Table'] = [(label, '', command) for label, command in (
    ('Read table','read table'),('Read cell','read cell'),('Next cell','next cell'),('Previous cell','previous cell'),
    ('Next row','next row'),('Previous row','previous row'),('Next column','next column'),('Previous column','previous column'),
    ('Add row','add row'),('Add column','add column'),('Delete row','delete row'),('Delete column','delete column'),
    ('Clear cell','clear cell'),('Delete table','delete table'),('Leave table','leave table'))]
DOCUMENT_ACTIONS['Format'].extend([(label,key,command) for label,key,command in (
    ('Align left','Ctrl+L','align left'),('Align center','Ctrl+E','align center'),('Align right','Ctrl+R','align right'),
    ('Justify','Ctrl+J','align justified'),('Heading level','','heading {value}'),('Highlight text','','highlight {value}'),
    ('Word font','','set word font {value}'),('Word font size','','set word size {value}'),
    ('Whole document spacing','','document line spacing {value}'),('Reset paragraph spacing','','reset line spacing'),
    ('Reset document spacing','','reset document line spacing'))])
DOCUMENT_ACTIONS['Read and navigate'].extend([(direction.title()+' '+unit, '', direction+' '+unit)
    for unit in ('character','word','line','sentence','paragraph') for direction in ('next','previous')])
DOCUMENT_ACTIONS['Edit'].extend([(label, '', command) for label,command in (
    ('Clear selection','clear selection'),('Set selection start','selection start at {value}'),('Set selection end','selection end at {value}'),
    ('Insert before text','insert before {value}'),('Insert after text','insert after {value}'),
    ('Replace current word','replace word with {value}'),('Replace current sentence','replace sentence with {value}'),
    ('Replace current paragraph','replace paragraph with {value}'),('Delete current word','delete current word'),
    ('Delete current line','delete current line'),('Delete current sentence','delete sentence'),('Delete current paragraph','delete current paragraph'))])
TABLE_KEYS={'next cell':'Tab','previous cell':'Shift+Tab','next row':'Alt+Down','previous row':'Alt+Up','next column':'Alt+Right','previous column':'Alt+Left'}
DOCUMENT_ACTIONS['Table']=[(name,TABLE_KEYS.get(command,''),command) for name,_,command in DOCUMENT_ACTIONS['Table']]
SHORTCUTS = {key: command for items in DOCUMENT_ACTIONS.values() for _, key, command in items if key and key not in ('Enter', 'Alt+T','Tab','Shift+Tab')}
SHORTCUTS.update({'Ctrl+Alt+'+str(level):'heading '+str(level) for level in range(1,7)})
SHORTCUTS.update({'CapsLock+F':'what formatting','CapsLock+Down':'what is selected',
                 'Ctrl+Shift+L':'bulleted list','Ctrl+Shift+7':'numbered list','Ctrl+Alt+T':'create table with {value}',
                 'Ctrl+Shift+H':'highlight {value}','Ctrl+Alt+H':'list headings','Ctrl+Alt+Down':'next heading',
                 'Ctrl+Alt+Up':'previous heading','Ctrl+Shift+Delete':'delete selection','Ctrl+Alt+S':'select {value}'})
DOCUMENT_ACTIONS['Format'].extend([('Heading '+str(level),'Ctrl+Alt+'+str(level),'heading '+str(level)) for level in range(2,7)])
# Every command also has a stable two-step menu accelerator: Alt, group digit,
# then the displayed item number. This covers commands without a conventional key.
for _group,_items in DOCUMENT_ACTIONS.items():
    DOCUMENT_ACTIONS[_group]=[(name,key or next((k for k,v in SHORTCUTS.items() if v==command),'Alt, '+str(list(DOCUMENT_ACTIONS).index(_group)+1)+', '+('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'[i])),command) for i,(name,key,command) in enumerate(_items)]


class TaskNavigation:
    def __init__(self, app):
        self.app = app
        self.indices = {}
        self.group = None
        self.prompt = None
        self.picker = None
        self.recent = []
        self.media_title = None
        self.reader_return = 'files'

    def remember(self, path):
        path = Path(path)
        if path.stem.lower().startswith('untitled'):
            return
        target = self.app.APP/'recent-documents.json'
        try:
            old = json.loads(target.read_text(encoding='utf-8')) if target.exists() else []
            names = [str(path.resolve())] + [str(p) for p in old if str(p).casefold() != str(path.resolve()).casefold()]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(names[:20]), encoding='utf-8')
        except (OSError, ValueError, TypeError):
            pass

    def items(self, mode):
        if mode == 'word_processor':
            return [('Create a document', 'Ctrl+N', 'create a document'), ('Open a document', 'Ctrl+O', '@open'),
                    ('Recent documents', 'Ctrl+R', '@recent'), ('Options', 'Alt+O', '@options')]
        if mode == 'media_menu':
            return [('Radio', '', 'radio'), ('Podcasts', '', 'podcasts'), ('Media player', '', 'media player')]
        if mode == 'recent_documents':
            return [(Path(p).name, '', '@recent:'+p) for p in self.recent]
        if mode == 'document_menu':
            return DOCUMENT_ACTIONS[self.group] if self.group else [(name, '', '@group:'+name) for name in DOCUMENT_ACTIONS]
        if mode == 'local_media':
            return [('Open a media file', 'Ctrl+O', '@media_open'), ('Play or pause', 'Space', 'play or pause'),
                    ('Stop', 'Ctrl+S', 'stop media'), ('Back 15 seconds', 'Left', 'back 15 seconds'),
                    ('Forward 15 seconds', 'Right', 'forward 15 seconds'), ('Volume up', 'Up', 'volume up'),
                    ('Volume down', 'Down', 'volume down'), ('Playback speed', '', '@speed'),
                    ('Status', 'Ctrl+I', 'media status')]
        return None

    def state(self, mode):
        items = self.items(mode)
        if items is None or not items:
            return None
        return ([name for name, _, _ in items], self.indices.get(mode, 0), lambda i: self.indices.update({mode:i}))

    def enter(self, mode, title):
        self.indices[mode] = 0
        items = self.items(mode)
        self.app.speak(title+'. '+(self.describe(items[0], 0, len(items)) if items else 'No recent documents.'))
        return mode

    def describe(self, item, index, count):
        return announce_item(item[0], index, count) + (' '+item[1]+'.' if item[1] else '')

    def pick(self, kind):
        m = self.app
        if m.document is not None:
            m.document.save()
        from file_manager import FileManager
        m.FILE_MANAGER = FileManager()
        m.FILE_MANAGER.pick_extensions = MEDIA_TYPES if kind == 'media' else DOCUMENT_TYPES
        m.FILE_MANAGER.pick_label = 'media' if kind == 'media' else 'document'
        self.picker = kind
        m.speak(('Open media file. ' if kind == 'media' else 'Open a document. ')+m.FILE_MANAGER.refresh())
        return 'files'

    def open_document(self, path):
        m = self.app
        path = Path(path)
        if not path.is_file():
            raise ValueError('This file is no longer available. Choose another document.')
        if path.suffix.lower() == '.docx':
            from document_editor import VoiceDocument
            import zipfile
            from xml.etree import ElementTree as ET
            try:
                with zipfile.ZipFile(path) as archive:
                    names=archive.namelist()
                    if sum(info.file_size for info in archive.infolist())>50*1024*1024:raise ValueError('This document expands beyond the reading limit.')
                    source=ET.fromstring(archive.read('word/document.xml'))
                    w='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
                    custom_style=any(n.get(w+'val') not in ('Normal',*('Heading'+str(i) for i in range(1,7))) for n in source.iter(w+'pStyle'))
                    if custom_style or any(name.startswith(('word/header','word/footer','word/footnotes','word/endnotes','word/comments')) for name in names):
                        raise ValueError('This document has features the editor cannot preserve.')
                opened = VoiceDocument.open_existing(path.parent, path.stem)
            except (zipfile.BadZipFile, KeyError, ET.ParseError, EOFError) as error:
                # An empty "New Microsoft Word Document" or a damaged file must not stop the app.
                raise ValueError('This document is empty or damaged, so it could not be opened.') from error
            except ValueError as error:
                if 'cannot preserve' not in str(error):raise
                from document_reader import open_document
                m.FILE_READER=open_document(path);self.remember(path)
                m.speak('Opened '+path.name+'. Document reader preserves this document structure. Editing is unavailable for this file. '+m.FILE_READER.reading.read(True))
                return 'file_reader'
            m.document = opened
            m.checkpoint_document()
            m.document.dictating = False
            self.remember(path)
            m.speak('Opened '+path.name+'. Document. '+m.document.process('read paragraph'))
            return 'document'
        from document_reader import open_document
        from file_manager import FileManager
        if m.FILE_MANAGER is None:m.FILE_MANAGER=FileManager()
        self.remember(path)
        m.speak(m.FILE_MANAGER.start_job(lambda:open_document(path),m.FILE_MANAGER.finish_open,'Opening '+path.name+'.'))
        return 'files'

    def selected_file(self, path):
        kind = self.picker
        if kind == 'media' or kind is None and Path(path).suffix.lower() in MEDIA_TYPES:
            path = Path(path)
            self.app.media().player.open(path.resolve().as_uri(), path.name, 'local')
            self.media_title = path.name
            self.picker=None
            return self.enter('local_media', 'Playing '+path.name+'. Media player')
        self.reader_return='files'
        result=self.open_document(path)
        self.picker=None
        return result

    def action(self, command, mode):
        m = self.app
        if command == '@open': return self.pick('document')
        if command == '@media_open': return self.pick('media')
        if command == '@recent':
            try: self.recent = json.loads((m.APP/'recent-documents.json').read_text(encoding='utf-8'))[:20]
            except (OSError, ValueError, TypeError): self.recent = []
            if not self.recent:
                self.recent = [str(p) for p in sorted(m.documents_folder().glob('*.docx'), key=lambda p:p.stat().st_mtime, reverse=True)[:20]]
            self.recent=[p for p in self.recent if isinstance(p,str)]
            return self.enter('recent_documents', 'Recent documents')
        if command.startswith('@recent:'):
            self.reader_return='recent_documents'
            return self.open_document(command[8:])
        if command.startswith('@group:'):
            self.group = command[7:]
            return self.enter('document_menu', self.group+' menu')
        if command == '@options':
            m.SETTINGS_RETURN_MODE = mode
            result = m.open_settings(mode)
            if result == 'settings': m.SETTINGS_PANEL.command('Word processor')
            return result
        if command == '@speed':
            self.prompt = ('speed {value}', 'local_media')
            m.speak('Playback speed. Type or say a number from 0.5 to 3, then Enter. Escape cancels.')
            return 'task_input'
        if '{value}' in command:
            self.prompt = (command, 'document')
            prompt={'{value}':'Document command. Type or say the full command, then Enter.',
                    'replace {value}':'Replace text. Type or say the old text, with, then the new text. Press Enter.',
                    'create table with {value}':'Table size. Type or say the rows and columns, such as two rows and two columns. Press Enter.'}.get(command)
            m.speak((prompt or command.replace('{value}', '').strip()+'. Type or say the value, then Enter.')+' Escape cancels.')
            return 'task_input'
        return m.handle(command, 'document' if mode == 'document_menu' else 'awake' if mode in ('media_menu','word_processor') else mode, typed=True)

    def keyboard(self, key, mode):
        if mode=='document_menu' and key.startswith('MenuPick:'):
            token=key.split(':',1)[1].upper()
            index='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'.find(token) if self.group else int(token)-1 if token.isdigit() else -1
            return '@menu:'+str(index)
        if mode == 'document':
            if key in ('Doc:NextCell','Doc:PreviousCell'):return 'next cell' if key=='Doc:NextCell' else 'previous cell'
            if key == 'Alt': self.group = None; return 'document menu'
            return SHORTCUTS.get(key)
        if mode == 'local_media':
            return {'Space':'play or pause','Ctrl+O':'@media_open','Ctrl+S':'stop media','Left':'back 15 seconds','Right':'forward 15 seconds',
                    'Up':'volume up','Down':'volume down','Ctrl+I':'media status','Escape':'escape'}.get(key, {'Enter':'enter','Tab':'next','ShiftTab':'previous'}.get(key))
        if self.items(mode) is not None:
            if mode == 'word_processor' and key in ('Ctrl+N','Ctrl+O','Ctrl+R','Alt+O'):
                return {'Ctrl+N':'create a document','Ctrl+O':'@open','Ctrl+R':'@recent','Alt+O':'@options'}[key]
            return {'Up':'previous','Left':'previous','Down':'next','Right':'next','Enter':'enter','Escape':'escape','Home':'home','End':'end'}.get(key)
        if mode == 'task_input' and key == 'Escape': return 'cancel'
        return None

    def handle(self, text, mode):
        m = self.app
        command = text if text.startswith('@') else m.normalized_command(text)
        if mode == 'task_input':
            template, target = self.prompt
            if command in ('cancel','escape','go back'):
                self.prompt=None
                m.speak('Canceled. Back to '+target.replace('_',' ')+'.'); return target
            if not text.strip():m.speak('Enter a value, or press Escape to cancel.');return mode
            result=self.handle(template.format(value=text.strip()), target) if target == 'local_media' else m.handle(template.format(value=text.strip()), target, typed=True)
            self.prompt=None
            return result
        if mode in ('document_name','document_save','settings','power_confirm','exit_confirm','tutorial','sleep','search'):return None
        if mode=='files' and m.FILE_MANAGER and m.FILE_MANAGER.pending:return None
        if m.NAMED_LIST:return None
        if mode=='browser_menu' and m.web_session and m.web_session.menu.level=='name':return None
        if mode=='document_menu' and (command=='toggle audio' or m.speech_control(text) or m.speech_setting_request(text)):
            return None
        if mode in ('document','document_menu') and command in ('options','open options','word processor options'):
            return self.action('@options',mode)
        if mode=='document_menu' and command in ('main menu','back to main menu','exit companion'):
            return m.handle(text,'document',typed=True)
        if mode in ('word_processor','media_menu','recent_documents','document_menu','local_media') and (m.spoken_control(command) or command in ('help','exit companion','go to sleep','wake up','main menu','back to main menu')):
            return None
        if mode=='document' and command in ('toggle bold','toggle italic','toggle underline'):
            attribute=command.split()[1]
            value=m.document.formatting_report(attribute).lower()==attribute+'.'
            m.speak(m.document.process(('remove '+attribute) if value else 'make this '+('underlined' if attribute=='underline' else attribute)))
            return mode
        if mode in ('awake','document','word_processor') and command in ('open a document','open document'):
            return self.pick('document')
        if command in ('word processor','open word processor','word processor menu'):
            if mode == 'document' and m.document:m.document.save()
            elif mode=='email_draft' and m.email_draft:m.email_draft.save()
            else:m.flush_note(mode)
            return self.enter('word_processor', 'Word processor menu')
        if command in ('media','media menu','open media') and mode not in ('document','email_draft'):
            return self.enter('media_menu', 'Media menu')
        if command in ('media player','open media player'):
            return self.enter('local_media', 'Media player')
        if command in ('document menu','document commands','menu') and mode == 'document':
            self.group = None
            return self.enter('document_menu', 'Document menu. Use arrows and Enter to choose a group')
        if mode == 'document' and (text in SHORTCUTS.values() or command.startswith('@')):
            return self.action(text, mode) if '{value}' in text or text.startswith('@') else None
        items = self.items(mode)
        if items is None: return None
        if mode=='document_menu' and command.startswith('@menu:'):
            index=int(command[6:])
            if 0<=index<len(items):return self.action(items[index][2],mode)
            m.speak('No command for this key.');return mode
        if mode=='document_menu' and command.isdigit():
            index=int(command)-1
            if 0<=index<len(items):return self.action(items[index][2],mode)
            m.speak('Choose an item number from 1 to '+str(len(items))+'.');return mode
        if mode == 'local_media':
            action = {'play or pause':'toggle','play':'play','resume':'play','pause':'pause','stop media':'stop','stop':'stop',
                      'back 15 seconds':'seek','rewind':'seek','forward 15 seconds':'seek'}.get(command)
            player = m.media().player
            if action:
                if player.kind!='local': m.speak('Open a media file first.'); return mode
                result = player.control(action, seconds=-15 if command in ('back 15 seconds','rewind') else 15)
                m.speak('This file cannot seek.' if action=='seek' and result is None else ('Paused.' if result else 'Playing.') if action == 'toggle' else {'play':'Playing.','pause':'Paused.','stop':'Stopped.','seek':'Position changed.'}[action]); return mode
            if command in ('volume up','volume down') or command.startswith('speed '):
                if player.kind!='local': m.speak('Open a media file first.'); return mode
                value = player.adjust('volume', .1 if command=='volume up' else -.1) if command.startswith('volume') else player.adjust('speed', float(__import__('document_editor').spoken_number(text.strip()[6:]) or text.strip()[6:]))
                m.speak(('Volume '+str(round(value*100))+' percent.') if command.startswith('volume') else 'Playback speed '+str(value)+'.'); return mode
            if command in ('status','media status'):
                if player.kind!='local':m.speak('No media file open. Choose Open a media file.');return mode
                status=player.status()
                m.speak((self.media_title or 'Media file')+'. '+('Paused.' if status['paused'] else 'Playing.')+' Position '+str(round(status['position']))+' seconds.'); return mode
        if command in ('escape','go back','back','main menu'):
            if mode == 'document_menu':
                if self.group: self.group = None; return self.enter(mode, 'Document menu')
                m.speak('Back to document.'); return 'document'
            if mode == 'recent_documents': return self.enter('word_processor', 'Word processor menu')
            if mode == 'local_media':
                if not m.PREFERENCES.get('keep_audio_playing',False) and m.MEDIA_HUB and m.MEDIA_HUB.player.kind=='local':m.MEDIA_HUB.player.control('stop')
                return self.enter('media_menu', 'Media menu')
            m.announce_main_menu(); return 'awake'
        if command in ('next','previous','home','end'):
            if not items: m.speak('No recent documents.'); return mode
            old = self.indices.get(mode, 0)
            index = 0 if command=='home' else len(items)-1 if command=='end' else max(0,min(len(items)-1,old+(1 if command=='next' else -1)))
            self.indices[mode] = index
            m.speak(('Start of list. ' if index==old==0 else 'End of list. ' if index==old else '')+self.describe(items[index], index, len(items))); return mode
        if command.startswith('letter:'):
            index = next_match([i[0] for i in items], self.indices.get(mode, 0), command[-1])
            if index is not None: self.indices[mode] = index; m.speak(self.describe(items[index], index, len(items)))
            return mode
        if command.startswith('@'): return self.action(text, mode)
        if command in ('open a document','open document'): return self.pick('document')
        if command in ('recent documents','recent'): return self.action('@recent', mode)
        if command in ('options','word processor options') and mode == 'word_processor': return self.action('@options', mode)
        if command in ('open media','open a media file','open media file') and mode == 'local_media': return self.pick('media')
        if command in ('enter','okay','ok','confirm','that one','open'):
            if not items: m.speak('No recent documents.'); return mode
            return self.action(items[self.indices.get(mode,0)][2], mode)
        for name, _, request in items:
            if command == name.lower(): return self.action(request, mode)
        if mode == 'word_processor' and command in ('create a document','new document'): return None
        if command in ('options','open options') and mode!='word_processor':return None
        if mode!='document_menu':return None
        m.speak('Choose '+', '.join(item[0] for item in items)+'.'); return mode
