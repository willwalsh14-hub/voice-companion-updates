"""Windows integration checks for the real category dialog and message loop."""
import ctypes as c
from ctypes import wintypes as w
import sys
import threading
import time
import unittest
from native_settings import NativeSettings
from settings_model import SettingsSession,DEFAULTS

@unittest.skipUnless(sys.platform=='win32','Native Windows controls require Windows')
class NativeSettingsTests(unittest.TestCase):
    def open_panel(self):
        self.saved=[];self.notices=[]
        context={'engines':('windows',),'windows_voices':('Test voice',),'espeak_voices':(),'ai_voices':('coral',),'accounts':(),'podcasts':(),'podcast_limits':{}}
        values=DEFAULTS|{'rate':'0','volume':'100','punctuation':'some','input_mode':'mixed','engine':'windows','windows_voice':'Test voice','ai_voice':'coral','email_list_size':'10','browser':'firefox','radio_source':'all','podcast_limit':'manual','ai_consent':False,'remove_ai':False,'api_key':''}
        self.session=SettingsSession(values,context)
        def save(values):self.saved.append(values);self.panel.accepted()
        self.panel=NativeSettings(self.session,self.notices.append,save,lambda key:None).start()
        self.assertTrue(self.panel.ready.wait(8));self.assertFalse(self.panel.closed.is_set(),self.notices)
        self.u=c.WinDLL('user32',use_last_error=True)
        self.u.GetDlgItem.restype=w.HWND;self.u.GetDlgItem.argtypes=[w.HWND,c.c_int]
        self.u.SendMessageW.restype=c.c_ssize_t;self.u.SendMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM]
        self.u.PostMessageW.restype=w.BOOL;self.u.PostMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM]
        return self.panel.hwnd
    def wait(self,condition):
        until=time.monotonic()+5
        while not condition() and time.monotonic()<until:time.sleep(.02)
        self.assertTrue(condition(),self.notices)
    def tearDown(self):
        if hasattr(self,'panel') and not self.panel.closed.is_set():self.panel.cancel();self.panel.closed.wait(3)
    def test_category_arrows_choose_enter_opens_then_checkbox_and_enter_save(self):
        hwnd=self.open_panel();categories=self.u.GetDlgItem(hwnd,100)
        self.u.PostMessageW(categories,0x100,0x28,0) # Choose only; Enter opens.
        self.wait(lambda:self.session.category==1)
        self.assertFalse(self.u.GetDlgItem(hwnd,200))
        self.u.PostMessageW(categories,0x100,0x0D,0)
        self.wait(lambda:bool(self.u.GetDlgItem(hwnd,200)))
        checkbox=self.u.GetDlgItem(hwnd,202)
        for _ in range(2):self.panel.command('next setting')
        self.wait(lambda:any('Lower media volume while Companion speaks' in message for message in self.notices))
        before=bool(self.u.SendMessageW(checkbox,0xF0,0,0))
        self.u.SendMessageW(checkbox,0x100,0x20,0);self.u.SendMessageW(checkbox,0x101,0x20,0)
        self.wait(lambda:bool(self.u.SendMessageW(checkbox,0xF0,0,0))!=before)
        self.u.PostMessageW(checkbox,0x100,0x0D,0)
        self.wait(lambda:len(self.saved)==1)
        self.assertFalse(self.panel.closed.is_set())
        self.assertEqual(len(self.saved),1);self.assertEqual(self.saved[0]['duck_audio'],not before)
    def test_escape_discards_changes_and_voice_settings_are_staged(self):
        hwnd=self.open_panel();self.panel.command('verbosity low')
        self.wait(lambda:self.session.values['verbosity']=='low' and 'Verbosity low' in self.notices)
        self.assertFalse(self.saved)
        self.assertTrue(self.u.PostMessageW(self.u.GetDlgItem(hwnd,200),0x100,0x1B,0))
        self.wait(lambda:self.session.values['verbosity']=='high' and not self.u.GetDlgItem(hwnd,200))
        self.assertFalse(self.panel.closed.is_set())
        self.panel.command('close settings');self.assertTrue(self.panel.closed.wait(5))
        self.assertFalse(self.saved)
    def test_combo_arrow_changes_value_and_enter_saves(self):
        hwnd=self.open_panel()
        self.u.PostMessageW(self.u.GetDlgItem(hwnd,100),0x100,0x0D,0)
        self.wait(lambda:any('Verbosity, high' in message for message in self.notices))
        combo=self.u.GetDlgItem(hwnd,200)
        self.u.PostMessageW(combo,0x100,0x28,0)
        self.wait(lambda:self.session.values['verbosity']=='medium')
        self.u.PostMessageW(combo,0x100,0x0D,0)
        self.wait(lambda:len(self.saved)==1)
        self.assertEqual(self.saved[0]['verbosity'],'medium')
    def test_enter_opens_category_and_focus_is_spoken(self):
        hwnd=self.open_panel();categories=self.u.GetDlgItem(hwnd,100)
        self.u.PostMessageW(categories,0x100,0x0D,0)
        self.wait(lambda:any('Verbosity, high' in message for message in self.notices))
        self.assertTrue(any('Verbosity' in message for message in self.notices))

    def test_text_entry_is_self_voicing_and_password_is_hidden(self):
        hwnd=self.open_panel();self.panel.command('documents')
        self.wait(lambda:self.session.category==5)
        self.wait(lambda:any('Font for new documents, Calibri' in message for message in self.notices))
        entry=self.u.GetDlgItem(hwnd,200)
        self.u.PostMessageW(entry,0x102,ord('Z'),0)
        self.wait(lambda:'Z' in self.notices)
        self.panel.command('synthesizer')
        self.wait(lambda:self.session.category==2)
        for _ in range(3):self.panel.command('next setting')
        self.wait(lambda:any('AI API key; blank keeps the saved key. Hidden.' in message for message in self.notices))
        password=self.u.GetDlgItem(hwnd,204)
        self.assertTrue(password)
        before=len(self.notices)
        self.u.PostMessageW(password,0x102,ord('Q'),0)
        self.wait(lambda:'Hidden character.' in self.notices[before:])
        self.assertNotIn('Q',self.notices[before:])

    def test_every_category_tabs_without_invalid_value_and_escape_closes(self):
        hwnd=self.open_panel()
        for name in ('Speech','Synthesizer','Input','Email','Documents','Web browsing','Radio','Podcasts','Updates','Verbosity'):
            self.panel.command(name)
            self.wait(lambda:self.session.category_name()==name)
            self.u.PostMessageW(self.u.GetDlgItem(hwnd,100),0x100,9,0)
            time.sleep(.15)
            self.assertFalse(any('Choose one of' in n or 'Settings:' in n for n in self.notices),self.notices)
        self.assertTrue(self.u.PostMessageW(self.u.GetDlgItem(hwnd,200),0x100,0x1B,0))
        self.wait(lambda:not self.u.GetDlgItem(hwnd,200))
        self.panel.command('close settings');self.assertTrue(self.panel.closed.wait(5),self.notices)
        self.assertFalse(self.saved)

    def test_enter_activates_action_without_saving(self):
        hwnd=self.open_panel();actions=[]
        self.panel.action_callback=actions.append
        self.panel.command('Email');self.wait(lambda:self.session.category_name()=='Email')
        self.panel.command('next setting')
        self.wait(lambda:any('Add an email account. Button.' in n for n in self.notices))
        self.u.PostMessageW(self.u.GetDlgItem(hwnd,202),0x100,0x0D,0)
        self.assertTrue(self.panel.closed.wait(5),self.notices)
        self.assertEqual(actions,['add_account']);self.assertFalse(self.saved)

if __name__=='__main__':unittest.main()


