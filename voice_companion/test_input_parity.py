import io,re,tempfile,unittest
from contextlib import ExitStack,redirect_stdout
from pathlib import Path
from unittest.mock import Mock,patch
import companion as app
from key_state import ALT_STATE  # 0x20000 on Windows; 0x8 there is Num Lock.
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
                    with patch.object(app,'speak'),patch.object(app,'NAMED_LIST',None),patch.object(app,'MEDIA_SECTION','radio'):
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
                    self.assertEqual(screen_key(mode,keysym,state,{'help_reader':True}),key)
                    self.assertIsNone(screen_key(mode,keysym,state,{'confirmation':'yes'}))
                    for private in ('web_private','media_input','browser_name_input','file_name_input','task_input'):
                        self.assertIsNone(screen_key(mode,keysym,state,{private:True}))
                    if key in ('Ctrl+S','Ctrl+F'):self.assertIsNone(screen_key('files',keysym,state,{}))
        # Keys keep their meaning elsewhere: Alt combinations and plain letters are never taken.
        self.assertIsNone(screen_key('note','s',ALT_STATE|4,{}))
        self.assertIsNone(screen_key('note','s',0,{}))
        self.assertIsNone(screen_key('awake','F1',1,{}))
        # Sentence keys work only inside an open help topic, not in the topic list.
        self.assertIsNone(screen_key('help','Down',4,{}))
        self.assertEqual(screen_key('help','Down',4,{'help_reader':True}),'Ctrl+Down')
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
        """Each key appears in the guide on the same line as the spoken command it matches."""
        lines=GUIDE.read_text(encoding='utf-8').casefold().splitlines()
        names={'Next':'Page Down','Prior':'Page Up','Ctrl+Down':'Ctrl+Down Arrow','Ctrl+Up':'Ctrl+Up Arrow'}
        for mode,keys in SCREEN_KEYS.items():
            for key,command in keys.items():
                with self.subTest(mode=mode,key=key):
                    written=names.get(key,key).replace('Ctrl+','Control+').casefold()
                    phrase=command.replace('{value}','').strip()
                    self.assertTrue(any(written in line and phrase in line for line in lines),(written,phrase))
        for phrase in ('Save note','Save draft','Next messages','Previous messages','Message details','Start tutorial',
                       'Go to start of line','Go to end of line','Page down','Page up'):
            self.assertIn(phrase.casefold(),'\n'.join(lines),phrase)
        # One spelling for keys, as speech reads it.
        self.assertNotIn('ctrl+','\n'.join(lines))


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

    def test_audio_tutorial_has_a_main_menu_key(self):
        # The main menu keeps its 12 items; Control+Shift+T starts the tutorial like the spoken command.
        self.assertNotIn('Audio tutorial',[name for name,_ in app.MAIN_MENU_CHOICES])
        with patch.object(app,'PracticeTutorial') as tutorial:
            tutorial.return_value.start.return_value='Tutorial courses.'
            self.assertEqual(self.press('Ctrl+Shift+T','awake'),'tutorial')
            app.tutorial_session=None
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


class MailFake:
    """A mail client with the same shape as the real ones: folders, pages and message bodies."""
    def __init__(self,*args):pass
    def folders(self):return [('Inbox','INBOX'),('Receipts','Receipts')]
    def list_messages(self,folder,cursor=None,limit=10):
        page=int(cursor or 0)
        return ([{'id':str(page*10+i),'from':'p'+str(page)+'@example.com','subject':'Page '+str(page)+' item '+str(i)} for i in range(2)],None if page else '1')


class NewKeyEffectTests(ScreenKeyEffectTests):
    """Each key added for parity does what the spoken command does."""

    def both(self,mode,key,voice,setup=None,state=None):
        """Run the voice command and the key from the same start; return what was said and the end state."""
        results=[]
        for use_key in (False,True):
            if setup:setup()
            self.speech.reset_mock()
            screen=self.press(key,mode) if use_key else app.handle(voice,mode)
            said=[str(c.args[0]) for c in self.speech.call_args_list]
            results.append((screen,said,state() if state else None))
        self.assertEqual(results[0],results[1],key)
        return results[0]

    def mailbox(self):
        from mail_voice import MailSession
        self.stack.enter_context(patch('mail_voice.account_token',return_value=('me@example.com','token')))
        session=MailSession('gmail',str(self.root),MailFake);session.process('open inbox')
        app.mail_session=session
        return session

    def test_main_menu_keys_for_version_mode_and_guides(self):
        for key,voice in (('Ctrl+Shift+V','What version?'),('Ctrl+Shift+M','what mode')):
            screen,said,_=self.both('awake',key,voice)
            self.assertEqual(screen,'awake');self.assertTrue(said)
        self.assertIn(app.APP_VERSION,self.both('awake','Ctrl+Shift+V','what version')[1][0])
        with patch('guide_files.open_folder',create=True),patch('os.startfile',create=True):
            screen,said,_=self.both('awake','Ctrl+Shift+G','Open user guides.')
        self.assertEqual(screen,'awake')

    def test_f12_puts_the_microphone_to_sleep_and_wakes_it(self):
        for mode in ('awake','note','files','document','web','mailbox','help'):
            with self.subTest(mode=mode):
                self.assertEqual(app.navigation_key('F12',mode),'go to sleep')
        app.SLEEP_RETURN_MODE='awake'
        self.assertEqual(self.press('F12','awake'),'sleep')
        self.assertEqual(app.handle('go to sleep','awake'),'sleep')
        self.assertEqual(app.navigation_key('F12','sleep'),'wake up')
        self.assertEqual(app.handle_keyboard('wake up','sleep'),'awake')

    def test_mail_folder_selection_and_contact_keys_match_voice(self):
        session=self.mailbox()
        self.both('mailbox','Ctrl+Shift+I','Which folder?')
        self.assertIn('Inbox',self.last())
        def marked():session._clear_marks();session.process('toggle message selection')
        self.both('mailbox','Ctrl+Shift+S','what is selected',setup=marked)
        self.assertIn('1',self.last())
        self.both('mailbox','Ctrl+Shift+A','clear selection',setup=marked,state=lambda:len(session.marked_ids))
        self.assertEqual(len(session.marked_ids),0)
        def no_picker():session.folder_picker=None
        self.both('mailbox','Ctrl+Shift+M','move folder',setup=no_picker,state=lambda:session.folder_picker['action'])
        self.assertEqual(session.folder_picker['action'],'move_source');session.folder_picker=None
        from address_book import AddressBook
        with patch.object(app,'current_account',return_value=None,create=True):
            AddressBook(app.APP,'').save('Sam Jones','sam@example.com')
            def closed():app.NAMED_LIST=None
            screen,said,_=self.both('mailbox','Ctrl+K','List contacts.',setup=closed,state=lambda:app.NAMED_LIST['title'])
            self.assertIn('Sam Jones',said[0])
            for mode in ('email_draft',):self.assertEqual(app.navigation_key('Ctrl+K',mode),'list contacts')
            # Add contact opens a box for the name and address; the result is the same contact.
            app.NAMED_LIST=None
            self.assertEqual(self.press('Ctrl+Shift+K','mailbox'),'task_input')
            self.assertIn('Escape cancels',self.last())
            self.assertEqual(app.handle_keyboard('Pat Lee as pat@example.com','task_input'),'mailbox')
            self.assertEqual(AddressBook(app.APP,'').get('Pat Lee')['address'],'pat@example.com')

    def test_message_link_and_button_lists_have_keys(self):
        session=self.mailbox()
        for key,voice in (('F7','list links'),('F8','list buttons')):
            session.view='folder'
            self.both('mailbox',key,voice)
            self.assertEqual(self.last(),'Open a message first.')
        session.view='message';session.body_text='Visit the site.'
        (self.root/'preferences.json').write_text('{"email_format": "html"}',encoding='utf-8')
        with patch('email_reader.message_controls',return_value=[(6,14,'link','https://example.com')]):
            self.both('mailbox','F7','list links')
        self.assertIn('the site',self.last())

    def test_saved_email_drafts_can_be_chosen_with_control_o(self):
        from email_draft import VoiceEmail
        draft=VoiceEmail(self.root/'Email Drafts');draft.compose_step='body';draft.recipient='sam@example.com'
        draft.process('Lunch on Friday.');draft.save()
        self.mailbox()
        def closed():app.NAMED_LIST=None
        screen,said,_=self.both('mailbox','Ctrl+O','open email draft',setup=closed,state=lambda:[n for n,_ in app.NAMED_LIST['items']])
        self.assertIn('Email drafts list',said[0])
        self.assertEqual(app.navigation_key('Enter','mailbox'),'okay')
        self.assertEqual(app.handle_keyboard('okay','mailbox'),'email_draft')
        self.assertIn('Lunch on Friday.',app.email_draft.body_text() if hasattr(app.email_draft,'body_text') else str(app.email_draft.paragraphs[0].text))
        # The spoken list is unchanged.
        app.handle('list email drafts','mailbox');self.assertIn('Your email drafts are:',self.last())
        empty=self.root/'empty';empty.mkdir()
        with patch.object(app,'APP',empty):
            app.handle('open email draft','awake');self.assertEqual(self.last(),'You have no saved email drafts yet.')

    def test_web_find_and_field_list_keys_match_voice(self):
        from web_assistant import WebSession
        session=WebSession(str(self.root),Mock());app.web_session=session
        calls=[]
        session.find=lambda spoken:calls.append(spoken) or 'Found it.'
        session.fields=lambda guided=False:'Fields: Name, Email.'
        self.assertEqual(app.handle('find login','web'),'web')
        self.assertEqual(self.press('Ctrl+F','web'),'task_input')
        self.assertIn('Escape cancels',self.last())
        self.assertEqual(app.handle_keyboard('login','task_input'),'web')
        self.assertEqual(calls[0].lower(),calls[1].lower())
        self.both('web','F8','List fields.')
        self.assertEqual(self.last(),'Fields: Name, Email.')

    def test_help_sentence_keys_match_voice(self):
        app.handle('help','awake')
        topic=next(i for i,t in enumerate(app.help_session.topics) if t['name'].casefold()=='getting started')
        def open_topic():
            app.help_session.topic_index=topic;app.help_session.level='topics';app.handle('that one','help');app.handle('that one','help')
        positions=[]
        for key,voice in (('Ctrl+Down','next sentence'),('Ctrl+Up','previous sentence')):
            positions.append(self.both('help',key,voice,setup=open_topic,state=lambda:app.help_session.reading.position))
        self.assertEqual(app.help_session.level,'article')

    def test_tutorial_keys_match_voice(self):
        def fresh():
            app.tutorial_session=None;app.handle('start tutorial','awake');app.handle('that one','tutorial');app.handle('that one','tutorial')
        state=lambda:(app.tutorial_session.level,app.tutorial_session.index)
        for key,voice in (('Ctrl+Shift+R','Repeat.'),('F1','list commands'),('Ctrl+L','Lesson list.'),('Ctrl+Down','continue reading')):
            with self.subTest(key=key):self.both('tutorial',key,voice,setup=fresh,state=state)
        fresh();app.handle('find lesson email','tutorial');expected=(self.last(),state())
        fresh()
        self.assertEqual(self.press('Ctrl+F','tutorial'),'task_input')
        self.assertEqual(app.handle_keyboard('email','task_input'),'tutorial')
        self.assertEqual((self.last(),state()),expected)
        fresh();self.press('Ctrl+Shift+F','tutorial');self.assertEqual(app.handle_keyboard('next message','task_input'),'tutorial')
        said=self.last();fresh();app.handle('explain command next message','tutorial');self.assertEqual(self.last(),said)

    def test_radio_station_actions_can_be_spoken_by_name(self):
        from media_navigation import MediaNavigation,RADIO_STATION
        hub=Mock();hub.stations=[{'name':'Jazz FM','url':'http://x'}];hub.station_index=0;hub.current_station=None
        hub.preset_browsing=False;hub.browsing_episodes=False;hub.player.kind=None;hub.presets.return_value=[]
        for action in RADIO_STATION:
            if not action.form:continue
            with self.subTest(action=action.label):
                spoken=MediaNavigation(hub,'radio');spoken.command(action.label+'.')
                pressed=MediaNavigation(hub,'radio');pressed.keyboard('Down')
                pressed.focus='actions';pressed.index=[a.label for a in pressed.actions].index(action.label);pressed.activate()
                self.assertEqual(spoken.form['name'],pressed.form['name'])

    def test_google_contacts_is_in_email_options(self):
        from settings_model import fields
        from app_settings import ACTIONS
        field=next(f for f in fields('Email options',{}) if f.key=='google_contacts')
        self.assertEqual(field.kind,'action');self.assertEqual(ACTIONS['google_contacts'],'connect google contacts')
        with patch('google_contacts.sync',return_value='Google Contacts synced.') as sync:
            app.settings_action('google_contacts','mailbox')
        sync.assert_called_once()


class CatalogParityTests(unittest.TestCase):
    """Every menu item, option and media action has a spoken name that the app understands."""

    def test_settings_phrases_reach_the_same_field(self):
        from settings_model import CATEGORIES,fields,SettingsSession
        from input_parity import settings_phrase
        session=SettingsSession({}, {})
        for category in CATEGORIES:
            for field in fields(category,{}):
                if field.kind=='action':continue
                with self.subTest(field=field.key):
                    request=session.voice_setting((settings_phrase(field)+' on').capitalize()+'.')
                    self.assertIsNotNone(request,field.label);self.assertEqual(request[0],field.key)

    def test_settings_buttons_have_spoken_commands(self):
        from settings_model import CATEGORIES,fields
        from app_settings import ACTIONS
        for category in CATEGORIES:
            for field in fields(category,{}):
                if field.kind=='action':self.assertIn(field.key,ACTIONS)

    def test_menu_items_spoken_or_entered_open_the_same_place(self):
        with ExitStack() as stack:
            root=Path(stack.enter_context(tempfile.TemporaryDirectory()))
            stack.enter_context(redirect_stdout(io.StringIO()))
            speech=stack.enter_context(patch.object(app,'speak'))
            for name,value in {'APP':root,'TEXT_MODE':True,'APP_WINDOW':None,'NAMED_LIST':None,'document':None,'email_draft':None}.items():
                stack.enter_context(patch.object(app,name,value))
            stack.enter_context(patch.object(app,'open_settings',return_value='settings'))
            stack.enter_context(patch.object(app,'SETTINGS_PANEL',Mock()))
            stack.enter_context(patch.object(app,'SETTINGS_RETURN_MODE',app.SETTINGS_RETURN_MODE))
            stack.enter_context(patch.object(app,'TASK_NAV',None))
            stack.enter_context(patch.object(app,'MEDIA_SECTION','radio'))
            stack.enter_context(patch.object(app,'MEDIA_HUB',None))
            stack.enter_context(patch.object(app,'open_browser_menu',return_value='browser_menu'))
            stack.enter_context(patch.object(app,'open_email_reader',return_value='mailbox',create=True))
            nav=app.task_navigation()
            for mode in ('word_processor','media_menu'):
                for index,(name,key,voice) in enumerate(nav.items(mode)):
                    with self.subTest(mode=mode,item=name):
                        results=[]
                        for use_key in (False,True):
                            nav.enter(mode,'Menu');speech.reset_mock()
                            if use_key:
                                nav.indices[mode]=index
                                results.append(app.handle_keyboard(app.navigation_key('Enter',mode),mode))
                            else:results.append(app.handle(name+'.',mode))
                        self.assertEqual(results[0],results[1])



INVENTORY=Path(__file__).resolve().parents[1]/'tools'/'input_parity_inventory.py'


@unittest.skipUnless(INVENTORY.exists(),'The inventory script is kept in the repository, not the installer.')
class InventoryTests(unittest.TestCase):
    def inventory(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('input_parity_inventory',INVENTORY)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    def test_every_action_has_a_voice_and_a_keyboard_way(self):
        module=self.inventory()
        with redirect_stdout(io.StringIO()):rows=module.rows()
        self.assertGreater(len(rows),400)
        self.assertEqual(module.problems(rows),[])
        # Every key added for parity is listed in the inventory.
        text=' '.join(row[3] for row in rows)
        for keys in SCREEN_KEYS.values():
            for key in keys:
                written={'Next':'Page Down','Prior':'Page Up'}.get(key,key)
                self.assertIn(written.replace('Ctrl+Down','Ctrl+Down Arrow').split(' Arrow')[0],text,key)

    def test_committed_inventory_matches_the_app(self):
        module=self.inventory()
        with redirect_stdout(io.StringIO()):expected=module.render()
        self.assertEqual(module.TARGET.read_text(encoding='utf-8'),expected,
                         'Run python3 tools/input_parity_inventory.py and commit tools/input-parity-inventory.md.')


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
        self.window.ui_actions.put(('ui_call',run));(done.wait(5) or self.fail(self.window.debug_state()))
        if errors:raise errors[0]
    def wait(self,condition):
        import time
        end=time.monotonic()+5
        while not condition() and time.monotonic()<end:time.sleep(.015)
        if not condition():self.fail(self.window.debug_state())
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
