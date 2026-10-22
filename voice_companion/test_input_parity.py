import io,re,tempfile,unittest
from contextlib import ExitStack,redirect_stdout
from pathlib import Path
from unittest.mock import Mock,patch
import companion as app
from input_parity import catalog,SCREEN_KEYS,BODY_ONLY,chord,screen_key,screen_command
from file_manager import MENU,FileManager
from test_file_manager import FakeFiles,settle
from field_selection import FieldSelection
from reading_navigation import ReadingCursor

GUIDE=Path(__file__).with_name('START HERE - Veteran.txt')


def key_event(key):
    """The Tk keysym and modifier state that produce a SCREEN_KEYS key name."""
    parts=key.split('+');name=parts[-1];state=(4 if 'Ctrl' in parts else 0)|(1 if 'Shift' in parts else 0)
    return (name.lower() if len(name)==1 else name),state


class InputParityTests(unittest.TestCase):
    def test_declared_menu_actions_expose_voice_and_keyboard_routes(self):
        rows=catalog();self.assertGreater(len(rows),150)
        for row in rows:
            with self.subTest(area=row['area'],action=row['action']):
                self.assertTrue(row['action']);self.assertTrue(row['voice']);self.assertTrue(row['keyboard'])
        for name,key,voice in MENU:
            with self.subTest(file_action=name):
                request=app.navigation_key(key,'files')
                self.assertEqual({'enter':'open','escape':'go back'}.get(request,request),voice)

    def test_file_search_and_sort_voice_and_keyboard_have_the_same_effect(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ('Alpine.txt','Alpha.txt','Beta.txt'):(root/name).write_text(name)
            for key,voice in (('Ctrl+F','search files'),('Ctrl+Shift+F','search subfolders'),('Alt+S','sort files'),('Ctrl+Shift+1','sort by name'),('Ctrl+Shift+2','sort by date created'),('Ctrl+Shift+3','sort by date modified'),('Ctrl+Shift+4','sort by date added'),('Ctrl+Shift+Up','sort ascending'),('Ctrl+Shift+Down','sort descending')):
                states=[]
                for use_key in (False,True):
                    manager=FileManager(FakeFiles(root));manager.refresh(str(root));settle(manager)
                    with patch.object(app,'FILE_MANAGER',manager),patch.object(app,'speak'),patch.object(app,'sync_app_context'):
                        app.handle(app.navigation_key(key,'files') if use_key else voice,'files')
                        states.append((manager.pending,manager.menu,manager.view.sort,manager.view.descending,[i.name for i in manager.items]))
                self.assertEqual(states[0],states[1],key)


class ScreenKeyTableTests(unittest.TestCase):
    """Every direct key in SCREEN_KEYS is read from the real table the app uses."""

    def test_every_screen_key_runs_its_spoken_command(self):
        self.assertGreaterEqual(sum(len(keys) for keys in SCREEN_KEYS.values()),20)
        for mode,keys in SCREEN_KEYS.items():
            for key,command in keys.items():
                with self.subTest(mode=mode,key=key):
                    with patch.object(app,'speak'),patch.object(app,'NAMED_LIST',None):
                        request=app.navigation_key(key,mode)
                    self.assertEqual(request,('@prompt:'+command) if '{value}' in command else command)
                    self.assertEqual(screen_command(key,mode),request)
        with patch.object(app,'MEDIA_SECTION','podcast'):
            self.assertEqual(app.navigation_key('F1','media'),'help with podcasts')

    def test_window_sends_each_key_only_on_its_screen_and_not_during_a_question(self):
        for mode,keys in SCREEN_KEYS.items():
            for key in keys:
                keysym,state=key_event(key)
                with self.subTest(mode=mode,key=key):
                    self.assertEqual(chord(keysym,state),key)
                    self.assertEqual(screen_key(mode,keysym,state,{}),key)
                    self.assertIsNone(screen_key(mode,keysym,state,{'confirmation':'yes'}))
                    for private in ('web_private','media_input','browser_name_input','file_name_input','task_input'):
                        self.assertIsNone(screen_key(mode,keysym,state,{private:True}))
                    if key in ('Ctrl+S','Ctrl+F'):self.assertIsNone(screen_key('files',keysym,state,{}))
        # Keys keep their meaning elsewhere: Alt combinations and plain letters are never taken.
        self.assertIsNone(screen_key('note','s',8|4,{}))
        self.assertIsNone(screen_key('note','s',0,{}))
        self.assertIsNone(screen_key('awake','F1',1,{}))
        self.assertEqual(chord('Page_Down',0),'Next')

    def test_page_keys_change_mail_pages_only_in_the_message_list(self):
        self.assertEqual(screen_key('mailbox','Next',0,{}),'Next')
        self.assertEqual(screen_key('mailbox','Prior',0,{}),'Prior')
        self.assertIsNone(screen_key('mailbox','Next',0,{'source':'mail:1:2'}))
        self.assertIsNone(screen_key('mailbox','Prior',0,{},typed_text='Recei'))
        self.assertEqual(screen_key('mailbox','i',4,{'source':'mail:1:2'}),'Ctrl+I')

    def test_formatting_keys_apply_to_the_message_body_only(self):
        for key in BODY_ONLY:
            keysym,state=key_event(key)
            self.assertEqual(screen_key('email_draft',keysym,state,{}),key)
            self.assertIsNone(screen_key('email_draft',keysym,state,{},email_field='subject'))
        self.assertEqual(screen_key('email_draft','s',4,{},email_field='subject'),'Ctrl+S')

    def test_guide_gives_the_spoken_command_and_the_key_for_each_new_key(self):
        guide=GUIDE.read_text(encoding='utf-8')
        names={'Next':'Page Down','Prior':'Page Up'}
        for mode,keys in SCREEN_KEYS.items():
            for key,command in keys.items():
                with self.subTest(mode=mode,key=key):
                    written=names.get(key,key)
                    self.assertTrue(written in guide or written.replace('Ctrl+','Control+') in guide,written)
        for phrase in ('Save note','Save draft','Next messages','Previous messages','Message details','Audio tutorial',
                       'Go to start of line','Go to end of line','Page down','Page up'):
            self.assertIn(phrase.lower(),guide.lower(),phrase)


class ScreenKeyEffectTests(unittest.TestCase):
    """Pressing the key and saying the command leave the app in the same state."""

    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        defaults={'APP':self.root,'TEXT_MODE':True,'APP_WINDOW':None,'INPUT_MODE':'mixed','document':None,
                  'email_draft':None,'mail_session':None,'web_session':None,'pending_website':None,
                  'pending_send':None,'NAMED_LIST':None,'account_setup':False,'MAIN_MENU_INDEX':None,
                  'SYNTH_PICK_INDEX':None,'VOICE_PICK_INDEX':None,'VOICE_PICK_ORIGINAL':None,
                  'help_session':None,'tutorial_session':None,'MEDIA_HUB':None,'note_buffer':[],
                  'note_selection':FieldSelection(),'note_reading':ReadingCursor(),'OPEN_NOTE':None,
                  'KEYBOARD_DIRTY':{},'EDIT_ACK':{},'PREFERENCES':dict(app.PREFERENCES),
                  'SLEEP_RETURN_MODE':'awake','SEARCH_RETURN_MODE':'awake','FILE_READER':None}
        for name,value in defaults.items():
            self.stack.enter_context(patch.object(app,name,value,create=(name=='OPEN_NOTE')))
        self.speech=self.stack.enter_context(patch.object(app,'speak'))
        self.stack.enter_context(patch.object(app,'speak_prompt',side_effect=lambda text,*a,**k:app.speak(text)))
        self.stack.enter_context(patch.object(app,'dictation_feedback',side_effect=lambda text,*a,**k:app.speak(text)))

    def last(self):return str(self.speech.call_args.args[0])

    def press(self,key,mode):
        request=app.navigation_key(key,mode)
        return app.handle_keyboard(request,mode) if request else mode

    def test_f1_opens_the_matching_help_topic_on_each_screen(self):
        expected={'media':'Listen To Radio','awake':None,'note':'Write A Note','email_draft':'Write An Email','mailbox':'Read And Organize Email',
                  'file_reader':'Document Reader','web':'Browse The Web','browser_menu':'Browse The Web',
                  'word_processor':'Write A Document','recent_documents':'Write A Document',
                  'media_menu':'Media Player','local_media':'Media Player'}
        from document_reader import open_document
        reader=self.root/'reader.txt';reader.write_text('Reader text.',encoding='utf-8')
        for mode,topic in expected.items():
            with self.subTest(mode=mode):
                app.help_session=None
                if mode=='file_reader':app.FILE_READER=open_document(reader)
                if mode=='browser_menu':app.web_session=Mock(menu=Mock(level='menu'))
                if mode=='email_draft':
                    from email_draft import VoiceEmail
                    app.email_draft=VoiceEmail(self.root/'Email Drafts')
                if mode=='media':app.MEDIA_HUB=Mock()
                if mode=='mailbox':app.mail_session=Mock(folder_picker=None,folder_choice=None,pending=None,view='folder')
                self.assertEqual(self.press('F1',mode),'help')
                level_name=app.help_session.topics[app.help_session.topic_index]['name']
                if topic:self.assertEqual(level_name.casefold(),topic.casefold())
                else:self.assertEqual(app.help_session.level,'topics')
                # Saying the same command opens the same place.
                app.help_session=None
                self.assertEqual(app.handle(SCREEN_KEYS[mode]['F1'],mode),'help')
                self.assertEqual(app.help_session.topics[app.help_session.topic_index]['name'],level_name)
                app.web_session=None;app.email_draft=None;app.mail_session=None;app.MEDIA_HUB=None

    def test_notepad_save_and_read_keys_match_voice(self):
        for use_key in (False,True):
            with self.subTest(use_key=use_key):
                mode=app.handle('write a note','awake')
                app.handle_keyboard('Buy milk.',mode)
                if use_key:self.press('Ctrl+Shift+R',mode)
                else:app.handle('Read from top.',mode)
                self.assertIn('Buy milk.',self.last())
                self.speech.reset_mock()
                self.assertEqual(self.press('Ctrl+S',mode) if use_key else app.handle('Save note.',mode),'awake')
                self.assertEqual(self.speech.call_args_list[0].args[0],'Saved your note.')
        self.assertEqual((self.root/'notes.txt').read_text(encoding='utf-8').count('Buy milk.'),2)

    def test_email_draft_keys_save_read_and_format_like_voice(self):
        from email_draft import VoiceEmail
        from email_reader import MessageBody
        results=[]
        for use_key in (False,True):
            draft=VoiceEmail(self.root/('Drafts'+str(use_key)));draft.compose_step='body';draft.recipient='sam@example.com'
            app.email_draft=draft
            app.handle('Thank you for the help.','email_draft')
            if use_key:self.press('Ctrl+Shift+R','email_draft')
            else:app.handle('read email draft','email_draft')
            self.assertIn('Thank you for the help.',self.last())
            app.handle('select all','email_draft')
            self.press('Ctrl+B','email_draft') if use_key else app.handle('toggle bold','email_draft')
            bold=draft.formatting_report('bold')
            self.press('Ctrl+S','email_draft') if use_key else app.handle('save draft','email_draft')
            results.append((bold,self.last().startswith('Email draft saved locally')))
        self.assertEqual(results[0],results[1]);self.assertEqual(results[0],('Bold.',True))
        # In a header box, formatting is not applied to the address.
        draft.compose_step='subject'
        self.assertIsNone(app.task_navigation().handle('toggle bold','email_draft'))

    def test_email_header_box_is_kept_before_a_direct_key(self):
        from email_draft import VoiceEmail
        draft=VoiceEmail(self.root/'Drafts');draft.compose_step='subject';app.email_draft=draft
        request=app.commit_email_field_tab({'field':'subject','value':'Lunch plans','backward':False,'key':'Ctrl+S'},'email_draft')
        self.assertEqual(draft.subject,'Lunch plans');self.assertEqual(request,'save draft')

    def test_mail_page_keys_and_details_match_voice(self):
        from mail_voice import MailSession
        class FakeClient:
            def __init__(self,*args):pass
            def folders(self):return [('Inbox','INBOX')]
            def list_messages(self,folder,cursor=None,limit=10):
                page=int(cursor or 0)
                return ([{'id':str(page*10+i),'from':'p'+str(page)+'@example.com','subject':'Page '+str(page)+' item '+str(i)} for i in range(2)],None if page else '1')
        states=[]
        for use_key in (False,True):
            with patch('mail_voice.account_token',return_value=('me@example.com','token')):
                session=MailSession('gmail',str(self.root),FakeClient);session.process('open inbox')
                app.mail_session=session
                said=[]
                for key,voice in (('Next','next messages'),('Ctrl+I','message details'),('Prior','previous messages'),('Ctrl+I','message details')):
                    self.press(key,'mailbox') if use_key else app.handle(voice,'mailbox')
                    said.append(self.last())
                states.append(said)
        self.assertEqual(states[0],states[1])
        self.assertIn('Page 1 item 0',states[0][1]);self.assertIn('Page 0 item 0',states[0][3])

    def test_document_reader_find_key_opens_a_prompt_and_finds_like_voice(self):
        from document_reader import open_document
        path=self.root/'letter.txt';path.write_text('Dear Sam,\nThe clinic opens at nine.\nThanks.',encoding='utf-8')
        positions=[]
        for use_key in (False,True):
            app.FILE_READER=open_document(path)
            if use_key:
                self.assertEqual(self.press('Ctrl+F','file_reader'),'task_input')
                self.assertIn('Escape cancels',self.last())
                self.assertEqual(app.handle_keyboard('clinic','task_input'),'file_reader')
            else:self.assertEqual(app.handle('find clinic','file_reader'),'file_reader')
            positions.append((app.FILE_READER.reading.position,self.last()))
        self.assertEqual(positions[0],positions[1]);self.assertIn('clinic',positions[0][1])
        app.FILE_READER=open_document(path)
        self.press('Ctrl+F','file_reader');self.assertEqual(app.handle_keyboard('escape','task_input'),'file_reader')
        self.assertEqual(app.handle('Help.','file_reader'),'help')

    def test_audio_tutorial_is_on_the_main_menu(self):
        names=[name for name,_ in app.MAIN_MENU_CHOICES]
        self.assertEqual(names[names.index('Help')+1],'Audio tutorial')
        app.MAIN_MENU_INDEX=names.index('Audio tutorial')
        with patch.object(app,'PracticeTutorial') as tutorial:
            tutorial.return_value.start.return_value='Tutorial courses.'
            self.assertEqual(self.press('Enter','awake'),'tutorial')
            self.assertEqual(app.handle('Start tutorial.','awake'),'tutorial')

    def test_line_start_and_end_by_voice_match_home_and_end(self):
        from document_editor import VoiceDocument,Paragraph
        doc=VoiceDocument(self.root);doc.paragraphs=[Paragraph('First line\nSecond line'),Paragraph('Next')];doc.cursor=0
        app.document=doc
        doc.insertion_position=(0,3)
        self.assertEqual(app.handle('Go to end of line.','document'),'document')
        self.assertEqual(doc.insertion_position,(0,10));self.assertEqual(self.last(),'End of line. First line')
        self.assertEqual(app.handle('move to the start of line','document'),'document')
        self.assertEqual(doc.insertion_position,(0,0))
        doc.navigation_position=None;doc.insertion_position=(0,14)
        app.handle('jump to beginning of line','document');self.assertEqual(doc.insertion_position,(0,11))
        # Writing comes first: a sentence without the command verb is text.
        app.handle('End of line.','document')
        self.assertIn('End of line.',doc.paragraphs[0].text)
        empty=VoiceDocument(self.root/'empty');app.document=empty
        app.handle('go to end of line','document');self.assertEqual(self.last(),'The document is empty.')

    def test_web_page_and_line_voice_commands_match_the_keys(self):
        from web_assistant import WebSession
        from web_browse import BrowseCursor,voice_browse_key
        for phrase,key in (('page down','Read:Next'),('Page up.','Read:Prior'),('go to start of line','Read:Home'),
                           ('Go to the end of the line.','Read:End'),('beginning of line','Read:Home')):
            self.assertEqual(voice_browse_key(phrase),key,phrase)
        text='\n'.join('Line '+str(i)+' of the page' for i in range(60))
        positions=[]
        for use_key in (False,True):
            with tempfile.TemporaryDirectory() as folder:
                session=WebSession(folder,Mock());session.snapshot={'text':text,'elements':[],'controls':[]}
                session.reading.set_text(text);session.browse=BrowseCursor(session.snapshot)
                steps=[]
                for key,phrase in (('Read:Next','page down'),('Read:End','end of line'),('Read:Home','start of line'),('Read:Prior','page up')):
                    session.keyboard(key) if use_key else session.command(phrase)
                    steps.append(session.reading.position)
                positions.append(steps)
        self.assertEqual(positions[0],positions[1]);self.assertEqual(positions[0][0],text.index('Line 20 '))
        with tempfile.TemporaryDirectory() as folder:
            session=WebSession(folder,Mock());session.snapshot={'text':text,'elements':[],'controls':[]}
            session.reading.set_text(text);session.browse=BrowseCursor(session.snapshot)
            session.keyboard_field={'key':'q','tag':'input','type':'text','label':'Search','value':''}
            session.find=Mock(return_value='Not found.')  # The usual answer to words it does not know.
            session.command('page down');self.assertEqual(session.reading.position,0)


@unittest.skipUnless(__import__('sys').platform=='win32','Windows Tk keyboard integration')
class WindowScreenKeyTests(unittest.TestCase):
    def setUp(self):
        from app_window import CompanionWindow
        self.window=CompanionWindow('parity test');self.window.start()
    def tearDown(self):
        if self.window.thread.is_alive():self.call(lambda:self.window.root.destroy())
        self.window.thread.join(5)
    def call(self,function):
        import threading
        done=threading.Event();errors=[]
        def run():
            try:self.window.root.update_idletasks();function()
            except Exception as exc:errors.append(exc)
            finally:done.set()
        self.window.ui_actions.put(('ui_call',run));self.assertTrue(done.wait(5))
        if errors:raise errors[0]
    def wait(self,condition):
        import time
        end=time.monotonic()+5
        while not condition() and time.monotonic()<end:time.sleep(.015)
        self.assertTrue(condition())
    def test_note_keys_add_typed_words_first(self):
        self.window.set_context(dict(mode='note',source=None,text='',caret=0,ack=0,focus=True))
        self.wait(lambda:self.window.context.get('mode')=='note')
        self.call(lambda:self.window.typed.insert(0,'Buy bread'))
        self.call(lambda:self.window.typed.event_generate('<KeyPress>',keysym='s',state=4))
        self.wait(lambda:('keyboard','Ctrl+S') in self.window.commands.queue)
        self.assertEqual(list(self.window.commands.queue)[:2],['Buy bread',('keyboard','Ctrl+S')])
        self.call(lambda:self.assertEqual(self.window.typed.get(),''))
    def test_f1_and_mail_page_keys_reach_the_app(self):
        self.window.set_context(dict(mode='mailbox',source=None,text='',caret=0,ack=0,focus=True))
        self.wait(lambda:self.window.context.get('mode')=='mailbox')
        for keysym,state,expected in (('F1',0,'F1'),('Next',0,'Next'),('Prior',0,'Prior'),('i',4,'Ctrl+I')):
            self.call(lambda k=keysym,s=state:self.window.typed.event_generate('<KeyPress>',keysym=k,state=s))
            self.wait(lambda e=expected:('keyboard',e) in self.window.commands.queue)


if __name__=='__main__':unittest.main()
