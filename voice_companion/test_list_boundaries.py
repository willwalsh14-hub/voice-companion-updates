"""Navigation boundaries, SAPI labels and confirmation accelerators."""
import tempfile,unittest
from unittest.mock import patch,Mock
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
    def test_email_end_and_home_follow_pages_without_wrapping(self):
        class Client:
            def list_messages(self,folder,cursor=None,limit=10):
                pages={None:([{'id':'a','subject':'First','from':'A'}],'p2'),'p2':([{'id':'b','subject':'Middle','from':'B'}],'p3'),'p3':([{'id':'c','subject':'Last','from':'C'}],None)}
                return pages[cursor]
        with tempfile.TemporaryDirectory() as folder:
            session=MailSession('gmail',folder);session.client=lambda:Client();session.folder_id='inbox';session.folder_name='Inbox';session.list_size=10
            session.list_messages();self.assertIn('End of list',session.list_boundary(True))
            self.assertEqual(session.rows[session.current-1]['id'],'c')
            self.assertIn('last message',session.process('next message'));self.assertEqual(session.rows[0]['id'],'c')
            self.assertIn('Start of list',session.list_boundary(False));self.assertEqual(session.rows[0]['id'],'a')
            self.assertIn('first message',session.process('previous message'));self.assertEqual(session.rows[0]['id'],'a')
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
if __name__=='__main__':unittest.main()
