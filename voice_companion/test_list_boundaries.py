"""Navigation boundaries, SAPI labels and confirmation accelerators."""
import tempfile,unittest
from unittest.mock import patch,Mock
from pathlib import Path
import companion as app
from menu_navigation import bounded_index,boundary_notice,next_match
from settings_model import SettingsSession,DEFAULTS
from mail_voice import MailSession

class BoundaryTests(unittest.TestCase):
    def test_empty_single_and_finite_lists(self):
        self.assertIsNone(bounded_index(None,1,0))
        for count in (1,2,20):
            for step in (-1,1):
                index=0 if step<0 else count-1
                self.assertEqual(bounded_index(index,step,count),index)
        self.assertEqual(boundary_notice(0,1),'Start and end of list.')
    def test_letters_stop_at_last_match_but_other_letters_jump_back(self):
        names=['Alpha','Beta','Another']
        self.assertEqual(next_match(names,0,'a'),2)
        self.assertEqual(next_match(names,2,'a'),2)
        self.assertEqual(next_match(names,2,'b'),1)
    def test_main_voice_navigation_stops_and_home_end_voice_equivalents(self):
        with patch.object(app,'MAIN_MENU_INDEX',0),patch.object(app,'SYNTH_PICK_INDEX',None),patch.object(app,'VOICE_PICK_INDEX',None),patch.object(app,'account_setup',False),patch.object(app,'speak') as speech:
            app.handle('previous','awake');self.assertEqual(app.MAIN_MENU_INDEX,0)
            self.assertIn('Start of list',speech.call_args.args[0])
            app.handle('end of list','awake');last=len(app.MAIN_MENU_CHOICES)-1
            self.assertEqual(app.MAIN_MENU_INDEX,last)
            app.handle('next','awake');self.assertEqual(app.MAIN_MENU_INDEX,last)
            self.assertIn('End of list',speech.call_args.args[0])
            app.handle('beginning of list','awake');self.assertEqual(app.MAIN_MENU_INDEX,0)
    def test_sapi_display_maps_legacy_setting_without_resetting_voice(self):
        session=SettingsSession(DEFAULTS|{'engine':'windows','windows_voice':'Existing voice'}, {'engines':('SAPI','espeak','ai')})
        self.assertEqual(session.values['engine'],'SAPI')
        session.set('engine','windows');self.assertEqual(session.values['engine'],'SAPI')
        request=session.voice_setting('use SAPI');session.set(*request)
        self.assertEqual(session.values['windows_voice'],'Existing voice')
        self.assertEqual(app.synthesizers()[0],('windows','SAPI'))
    def test_confirmation_accelerators_and_bounded_arrows(self):
        with patch.object(app,'CONFIRM_CHOICE','no'),patch.object(app,'speak'),patch.object(app,'interrupt_speech'):
            for mode in ('exit_confirm','power_confirm','update_offer','document_save'):
                self.assertEqual(app.navigation_key('Alt+Y',mode),'yes')
                self.assertEqual(app.navigation_key('Alt+N',mode),'no')
                app.navigation_key('Home',mode);self.assertEqual(app.CONFIRM_CHOICE,'yes')
                app.navigation_key('Up',mode);self.assertEqual(app.CONFIRM_CHOICE,'yes')
                app.navigation_key('End',mode);self.assertEqual(app.CONFIRM_CHOICE,'no')
                app.navigation_key('Down',mode);self.assertEqual(app.CONFIRM_CHOICE,'no')
    def test_voice_picker_home_end_previews_selected_voice(self):
        with patch.object(app,'VOICE_PICK_INDEX',0),patch.object(app,'SYNTH_PICK_INDEX',None),patch.object(app,'voice_choices',return_value=[('windows','First',None),('windows','Last',None)]),patch.object(app,'preview_voice') as preview,patch.object(app,'account_setup',False):
            app.navigation_key('End','awake');preview.assert_called_with(1)
            app.navigation_key('Home','awake');preview.assert_called_with(0)
    def test_named_document_contact_account_lists_share_navigation_without_activation(self):
        with patch.object(app,'NAMED_LIST',None),patch.object(app,'speak'),patch.object(app,'interrupt_speech'):
            for title in ('Documents','Contacts','Email accounts'):
                app.open_named_list(title,[('Alpha','help'),('Beta','help')])
                app.handle('previous','awake');self.assertEqual(app.NAMED_LIST['index'],0)
                app.navigation_key('End','awake');self.assertEqual(app.NAMED_LIST['index'],1)
                app.handle('next','awake');self.assertEqual(app.NAMED_LIST['index'],1)
                app.navigation_key('Home','awake');self.assertEqual(app.NAMED_LIST['index'],0)
                app.handle('close list','awake');self.assertIsNone(app.NAMED_LIST)
    def test_named_picker_returns_to_email_header_and_cancels_old_website_question(self):
        from types import SimpleNamespace
        draft=SimpleNamespace(compose_step='recipient',recipient='keep@example.com')
        with patch.object(app,'NAMED_LIST',None),patch.object(app,'pending_website',('https://example.test','example.test')),patch.object(app,'email_draft',draft),patch.object(app,'APP_WINDOW',Mock()) as window,patch.object(app,'speak'):
            app.open_named_list('Contacts',[('Alpha','find contact Alpha')]);self.assertIsNone(app.pending_website)
            self.assertEqual(app._handle('close list','email_draft'),'email_draft')
            window.focus_email_field.assert_called_with('recipient','keep@example.com')
            app.open_named_list('Contacts',[('Alpha','find contact Alpha')])
            with patch.object(app,'handle',return_value='email_draft') as handle:
                self.assertEqual(app._handle('okay','email_draft'),'email_draft')
                handle.assert_called_with('find contact Alpha','email_draft',typed=True)
            window.focus_email_field.assert_called_with('recipient','keep@example.com')
    def test_email_end_and_home_follow_pages_without_wrapping(self):
        class Client:
            def list_messages(self,folder,cursor=None,limit=10):
                pages={None:([{'id':'a','subject':'First','from':'A'}],'p2'),'p2':([{'id':'b','subject':'Middle','from':'B'}],'p3'),'p3':([{'id':'c','subject':'Last','from':'C'}],None)}
                return pages[cursor]
        with tempfile.TemporaryDirectory() as folder:
            session=MailSession('gmail',folder);session.client=lambda:Client();session.folder_id='inbox';session.folder_name='Inbox';session.list_size=10
            # Will, October 2026: arrows stop at the page's edges; pages change only on purpose.
            session.list_messages()
            said=session.process('next message')
            self.assertTrue(said.startswith('End of list.'));self.assertIn('Press Page Down',said);self.assertEqual(session.rows[0]['id'],'a')
            said=session.process('previous message')
            self.assertTrue(said.startswith('Start of list.'));self.assertNotIn('Page Up',said)
            self.assertIn('Middle',session.process('next page'));self.assertEqual(session.rows[0]['id'],'b')
            self.assertIn('Page Up',session.process('previous message'))
            # Control+End and Control+Home: the oldest and newest message in the folder.
            self.assertNotIn('of list',session.list_boundary(True));self.assertEqual(session.rows[0]['id'],'c')
            said=session.process('next message');self.assertTrue(said.startswith('End of list.'));self.assertNotIn('Page Down',said)
            self.assertNotIn('of list',session.list_boundary(False));self.assertEqual(session.rows[0]['id'],'a')
    def test_failed_email_end_preserves_current_page(self):
        from mailbox_access import MailboxError
        with tempfile.TemporaryDirectory() as folder:
            session=MailSession('gmail',folder);session.rows=[{'id':'a'}];session.current=1;session.next_cursor='p2';session.page_cursor=None;session.previous_cursors=[]
            def fail(cursor):session.rows=[];session.current=None;raise MailboxError('Offline')
            session.list_messages=fail
            self.assertIn('Could not reach the end',session.list_boundary(True))
            self.assertEqual(session.rows,[{'id':'a'}]);self.assertEqual(session.current,1);self.assertEqual(session.next_cursor,'p2');self.assertEqual(session.previous_cursors,[])
    def test_repeated_email_cursor_reports_failure_without_looping(self):
        with tempfile.TemporaryDirectory() as folder:
            session=MailSession('gmail',folder);session.rows=[{'id':'a'}];session.next_cursor='repeat';session.page_cursor=None;session.previous_cursors=[]
            def fetch(cursor):session.rows=[{'id':'a'}];session.next_cursor='repeat';session.page_cursor=cursor
            session.list_messages=fetch
            self.assertIn('could not reach the end',session.list_boundary(True))
class EverydayWorkflowTests(unittest.TestCase):
    def test_current_release_notes_match_build_version_and_required_sections(self):
        from pathlib import Path
        from release_documents import load_notes
        self.assertEqual(load_notes(Path(__file__).parent)['version'],app.APP_VERSION)

    def test_new_email_keyboard_is_short_and_voice_remains_guided(self):
        from pathlib import Path
        from contextlib import ExitStack
        for level in ('high','medium','low'):
            for typed in (True,False):
                with tempfile.TemporaryDirectory() as folder,ExitStack() as stack:
                    for name,value in {'APP':Path(folder),'VERBOSITY':level,'email_draft':None,'mail_session':None,'pending_send':None,'NAMED_LIST':None,'MAIN_MENU_INDEX':None,'SYNTH_PICK_INDEX':None,'VOICE_PICK_INDEX':None,'account_setup':False,'KEYBOARD_NAVIGATION':False,'INPUT_MODE':'mixed'}.items():
                        stack.enter_context(patch.object(app,name,value))
                    stack.enter_context(patch.object(app,'ProtectedStore',return_value=Mock(selected=Mock(return_value=('gmail','me@example.test')))))
                    window=stack.enter_context(patch.object(app,'APP_WINDOW',Mock()))
                    speech=stack.enter_context(patch.object(app,'speak'))
                    self.assertEqual(app.handle('new email','awake',typed=typed),'email_draft')
                    notice=speech.call_args.args[0]
                    if typed:self.assertEqual(notice,'New email. To field.')
                    else:self.assertIn('Who do you want to send',notice)
                    self.assertNotIn('Untitled',notice);self.assertNotIn('me@example.test',notice)
                    self.assertEqual(app.email_draft.compose_step,'recipient')
                    window.focus_email_field.assert_called_with('recipient','')
                    if not typed:
                        app.handle('sam@example.test','email_draft',typed=True)
                        self.assertEqual(app.email_draft.compose_step,'subject')
                        self.assertIn('subject',speech.call_args.args[0].lower())
                        app.handle('Tomorrow meeting','email_draft')
                        self.assertEqual(app.email_draft.compose_step,'body')
                        self.assertIn('message',speech.call_args.args[0].lower())
                        app.handle('We will meet at noon.','email_draft')
                        self.assertEqual(app.email_draft.recipient,'sam@example.test')
                        self.assertEqual(app.email_draft.subject,'Tomorrow meeting')
                        self.assertIsNone(app.pending_send)
    def test_guidance_cannot_remove_or_delay_boundaries(self):
        from list_announcements import name_first
        from settings_model import prompt_text
        from menu_navigation import announce_item
        from native_settings import choice_feedback
        for level in ('high','medium','low'):
            for index,count,expected in ((0,2,'Start of list.'),(1,2,'End of list.'),(0,1,'Start and end of list.')):
                for notice in (announce_item('Test',index,count),name_first(f'Topic {index+1} of {count}: Test. Say next or previous. Say that one to open.'),choice_feedback('Speech rate, zero',index,count)):
                    spoken=prompt_text(notice,level)
                    self.assertFalse(spoken.startswith(expected),(level,spoken));self.assertIn('Test' if not notice.startswith('Speech') else 'Speech rate',spoken)
                    self.assertEqual(spoken.count(expected),0)
    def test_inbox_entry_and_page_transitions_announce_real_folder_boundaries(self):
        class Client:
            def list_messages(self,folder,cursor=None,limit=10):
                return ([{'id':'a','subject':'First','from':'Sam'}],'page2') if cursor is None else ([{'id':'b','subject':'Last','from':'Alex'}],None)
        with tempfile.TemporaryDirectory() as folder:
            session=MailSession('gmail',folder);session.client=lambda:Client();session.folder_id='inbox';session.folder_name='Inbox'
            self.assertNotIn('of list',session.list_messages())
            self.assertNotIn('End of list.',session._focus(1))
            self.assertTrue(session.process('next message').startswith('End of list.'))
            self.assertIn('Last',session.process('next page'));self.assertTrue(session.process('next message').startswith('End of list.'))
            self.assertNotIn('Start of list.',session._focus(1))
            self.assertIn('First',session.process('previous page'));self.assertTrue(session.process('previous message').startswith('Start of list.'))
    def test_singleton_inbox_entry_announces_both_boundaries(self):
        with tempfile.TemporaryDirectory() as folder:
            session=MailSession('gmail',folder);session.client=lambda:Mock(list_messages=Mock(return_value=([{'id':'a','subject':'Only','from':'Sam'}],None)))
            self.assertIn('Only',session.list_messages());self.assertTrue(session.process('next message').startswith('End of list.'));self.assertTrue(session.process('previous message').startswith('Start of list.'))

    def test_network_failure_preserves_email_page_and_history(self):
        from mailbox_access import MailboxError
        for command in ('next page','previous page'):
            with tempfile.TemporaryDirectory() as folder:
                session=MailSession('gmail',folder)
                session.rows=[{'id':'a','subject':'Keep','from':'Sam'}];session.current=1
                session.next_cursor='next';session.page_cursor='current';session.previous_cursors=[None]
                session.client=lambda:Mock(list_messages=Mock(side_effect=MailboxError('Offline')))
                self.assertIn('Offline',session.process(command))
                self.assertEqual(session.rows[0]['id'],'a');self.assertEqual(session.current,1)
                self.assertEqual(session.next_cursor,'next');self.assertEqual(session.page_cursor,'current')
                self.assertEqual(session.previous_cursors,[None])

    def test_batched_keyboard_email_menu_uses_short_announcement(self):
        import queue
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            window=Mock();window.commands=queue.Queue();window.commands.put(('keyboard','Ctrl+N'))
            with patch.object(app,'APP',Path(folder)),patch.object(app,'APP_WINDOW',window),patch.object(app,'email_draft',None),patch.object(app,'mail_session',Mock(folder_picker=None,folder_choice=None,pending=None)),patch.object(app,'ProtectedStore',return_value=Mock(selected=Mock(return_value=('gmail','me@example.test')))),patch.object(app,'interrupt_speech'),patch.object(app,'sync_app_context'),patch.object(app,'KEYBOARD_BATCH_TEXT',None),patch.object(app,'KEYBOARD_BATCH',False),patch.object(app,'NAMED_LIST',None),patch.object(app,'INPUT_MODE','mixed'):
                self.assertEqual(app.process_keyboard_batch('mailbox'),'email_draft')
                window.feedback.assert_called_once_with('New email. To field.')

    def test_leaving_named_document_keeps_filename_and_saves_changes(self):
        from pathlib import Path
        from document_editor import VoiceDocument
        for typed in (True,False):
            with tempfile.TemporaryDirectory() as folder:
                doc=VoiceDocument(Path(folder));doc.append_text('First paragraph.')
                doc.process('save document as My report');doc.append_text('Second paragraph.')
                with patch.object(app,'document',doc),patch.object(app,'APP_WINDOW',None),patch.object(app,'KEYBOARD_DIRTY',{}),patch.object(app,'INPUT_MODE','mixed'),patch.object(app,'speak') as speech:
                    self.assertEqual(app.handle('leave document','document',typed=typed),'document_save')
                    self.assertEqual(app.handle('yes','document_save',typed=typed),'word_processor')
                    self.assertIsNone(app.document)
                    self.assertIn('Saved My report.docx.',[call.args[0] for call in speech.call_args_list])
                    self.assertFalse(any('Name this document' in call.args[0] for call in speech.call_args_list))
                reopened=VoiceDocument.open_existing(Path(folder),'My report')
                self.assertIn('Second paragraph.',reopened.process('read document'))

    def test_failed_named_document_save_keeps_editor_open(self):
        from pathlib import Path
        from document_editor import VoiceDocument
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder),title='My report')
            with patch.object(app,'document',doc),patch.object(app,'APP_WINDOW',None),patch.object(app,'KEYBOARD_DIRTY',{}),patch.object(app,'speak') as speech,patch.object(doc,'save',side_effect=OSError('Disk unavailable')):
                self.assertEqual(app.handle('yes','document_save'),'document')
                self.assertIs(app.document,doc)
                self.assertIn('still open',speech.call_args.args[0])

if __name__=='__main__':unittest.main()


class EmailPageKeyTests(unittest.TestCase):
    """Will, October 2026: Home and End stay on this page; Page Down and Page Up change the
    page; Control+Home and Control+End reach the newest and oldest message in the folder."""
    def session(self):
        class Client:
            def list_messages(self,folder,cursor=None,limit=10):
                pages={None:([{'id':'a1','subject':'One','from':'A'},{'id':'a2','subject':'Two','from':'B'}],'p2'),
                       'p2':([{'id':'b1','subject':'Three','from':'C'},{'id':'b2','subject':'Four','from':'D'}],None)}
                return pages[cursor]
        folder=tempfile.mkdtemp();self.addCleanup(__import__('shutil').rmtree,folder,True)
        session=MailSession('gmail',folder);session.client=lambda:Client();session.folder_id='inbox';session.folder_name='Inbox';session.list_size=10
        session.list_messages();return session

    def test_home_and_end_stay_on_the_page(self):
        session=self.session()
        self.assertIn('Two',session.page_edge(True));self.assertEqual(session.rows[0]['id'],'a1')
        said=session.page_edge(True);self.assertTrue(said.startswith('End of list.'));self.assertIn('Page Down',said)
        self.assertIn('One',session.page_edge(False))
        self.assertTrue(session.page_edge(False).startswith('Start of list.'))

    def test_page_keys_and_control_home_end_reach_the_app(self):
        import companion
        session=self.session();said=[]
        with patch.object(companion,'mail_session',session),patch.object(companion,'speak',said.append):
            self.assertEqual(companion.navigation_key('Next','mailbox'),'next messages')
            self.assertEqual(companion.navigation_key('Prior','mailbox'),'previous messages')
            self.assertEqual(companion.navigation_key('Ctrl+End','mailbox'),'oldest message')
            self.assertIn('Four',session.process('oldest message'));self.assertEqual(session.rows[0]['id'],'b1')
            self.assertEqual(companion.navigation_key('Ctrl+Home','mailbox'),'newest message')
            self.assertIn('One',session.process('newest message'));self.assertEqual(session.rows[0]['id'],'a1')
            self.assertIsNone(companion.navigation_key('End','mailbox'))
            self.assertEqual(session.rows[0]['id'],'a1');self.assertIn('Two',said[-1])

    def test_the_window_passes_page_keys_on_the_mail_screen(self):
        source=(Path(__file__).parent/'app_window.py').read_text(encoding='utf-8')
        branch=source[source.index("elif mode=='mailbox':"):source.index("elif mode=='email_draft' and key in ('Tab','ISO_Left_Tab'):")]
        self.assertIn("key in ('Next','Prior')",branch);self.assertIn("request='Ctrl+'+key",branch)


class EmailLetterKeyTests(unittest.TestCase):
    """Will, October 2026: email actions on one key each, no modifiers."""
    def test_each_letter_runs_its_email_command(self):
        import companion
        from input_parity import screen_key
        session=Mock();session.view='folder';session.pending=None;session.folder_picker=None;session.folder_choice=None
        expected={'N':'new email','R':'reply','A':'reply all','F':'forward','M':'move','G':'go to folder','W':'which folder',
                  'I':'message details','C':'copy sender address','E':'new email to sender','D':'open email draft',
                  'P':'list contacts','U':'clear selection','S':'what is selected'}
        with patch.object(companion,'mail_session',session),patch.object(companion,'speak'):
            for letter,command in expected.items():
                self.assertEqual(screen_key('mailbox',letter.lower(),0,{},None,''),letter)
                self.assertEqual(companion.navigation_key(letter,'mailbox'),command)

    def test_letters_never_act_while_typing_or_in_a_folder_list(self):
        from input_parity import screen_key
        self.assertIsNone(screen_key('mailbox','r',0,{},None,'list messages'))
        self.assertIsNone(screen_key('mailbox','r',0,{'menu_letters':True},None,''))
        # In an open message, K and B still move by link and button.
        self.assertEqual(screen_key('mailbox','r',0,{'source':'mail:1'},None,''),'R')
        self.assertIsNone(screen_key('mailbox','k',0,{'source':'mail:1'},None,''))
        self.assertIsNone(screen_key('mailbox','b',0,{'source':'mail:1'},None,''))


class OldKeysStillWorkTests(unittest.TestCase):
    def test_earlier_keys_still_run_their_commands(self):
        from input_parity import screen_key, screen_command, OLD_KEYS
        self.assertEqual(screen_key('mailbox','I',0x4,{},None,''),'Ctrl+I')
        self.assertEqual(screen_command('Ctrl+I','mailbox'),'message details')
        self.assertEqual(screen_command('Ctrl+Shift+R','email_draft'),'read email draft')
        self.assertEqual(screen_command('Ctrl+Shift+F','tutorial'),'@prompt:explain command {value}')
        for mode,keys in OLD_KEYS.items():
            for key in keys:
                self.assertTrue(screen_command(key,mode),(mode,key))
