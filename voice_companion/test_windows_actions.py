import os
from pathlib import Path,PosixPath
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock,MagicMock,patch
import companion
import windows_actions as actions
from settings_model import DEFAULTS,SettingsSession

class WindowsActionTests(unittest.TestCase):
    def test_startup_defaults_off_and_voice_settings_match_checkbox(self):
        self.assertFalse(DEFAULTS['start_with_windows'])
        session=SettingsSession(DEFAULTS,{})
        for command in ('start with windows on','windows startup on','start automatically with windows on'):
            self.assertEqual(session.voice_setting(command),('start_with_windows','on'))
        session.set('start_with_windows','on');session.cancel()
        self.assertFalse(session.values['start_with_windows'])
    def test_registry_is_per_user_quoted_and_reversible(self):
        registry=MagicMock();registry.HKEY_CURRENT_USER=1;registry.KEY_SET_VALUE=2;registry.REG_SZ=3
        handle=registry.CreateKeyEx.return_value.__enter__.return_value
        executable=Path('Some Folder/VoiceCompanion.exe').resolve()
        with patch.dict(sys.modules,winreg=registry),patch.object(actions.os,'name','nt'),patch.object(actions,'Path',return_value=executable),patch.object(actions.sys,'frozen',True,create=True),patch.object(actions.sys,'executable','/Some Folder/VoiceCompanion.exe'):
            actions.set_startup(True)
            registry.CreateKeyEx.assert_called_with(1,actions.RUN_KEY,0,2)
            registry.SetValueEx.assert_called_with(handle,actions.RUN_NAME,0,3,'"'+str(executable)+'"')
            actions.set_startup(False);registry.DeleteValue.assert_called_with(handle,actions.RUN_NAME)
            with patch.object(actions.sys,'frozen',False):
                with self.assertRaises(ValueError):actions.set_startup(True)
    def test_power_never_forces_other_programs_to_close(self):
        system=Path('SystemRoot').resolve()
        with patch.object(actions.os,'name','nt'),patch.object(actions,'Path',return_value=system),patch.object(actions.subprocess,'run') as run:
            for action,flag in [('restart','/r'),('shutdown','/s')]:
                actions.request_power(action)
                self.assertEqual(run.call_args.args[0][1:],[flag,'/t','0'])
                self.assertNotIn('/f',run.call_args.args[0]);self.assertTrue(run.call_args.kwargs['check'])
    def test_power_requires_confirmation_and_keyboard_defaults_to_no(self):
        with patch.object(companion,'speak'),patch.object(companion,'document',None),patch.object(companion,'email_draft',None),patch.object(companion,'flush_note'),patch.object(companion,'CONFIRM_CHOICE','yes'),patch.object(companion,'POWER_ACTION',None),patch('windows_actions.request_power') as power:
            for phrase in ('restart computer','restart windows','shut down computer','turn off the computer'):
                self.assertEqual(companion.handle(phrase,'awake'),'power_confirm')
                power.assert_not_called()
                self.assertEqual(companion.navigation_key('Enter','power_confirm'),'no')
                self.assertEqual(companion.navigation_key('Escape','power_confirm'),'no')
                self.assertEqual(companion.handle('no','power_confirm'),'awake')
                power.assert_not_called()
            companion.handle('restart computer','awake')
            self.assertEqual(companion.navigation_key('Down','power_confirm'),None)
            self.assertEqual(companion.navigation_key('Enter','power_confirm'),'yes')
            self.assertEqual(companion.handle('yes','power_confirm'),'exit')
            power.assert_called_once_with('restart')
    def test_power_failure_returns_to_context_and_sleep_cannot_trigger_it(self):
        with patch.object(companion,'speak'),patch.object(companion,'flush_note'),patch.object(companion,'email_draft',None),patch('windows_actions.request_power',side_effect=OSError('blocked')) as power:
            self.assertEqual(companion.handle('restart computer','note'),'power_confirm')
            self.assertEqual(companion.handle('yes','power_confirm'),'note')
            with patch.object(companion,'voice_menu',return_value=False),patch.object(companion,'change_speech_setting',return_value=False):
                companion.handle('restart computer','sleep')
            self.assertEqual(power.call_count,1)
    def test_document_must_finish_save_question_before_power_confirmation(self):
        with patch.object(companion,'document',Mock()),patch.object(companion,'speak'),patch.object(companion,'discard_document_changes'),patch('windows_actions.request_power') as power:
            self.assertEqual(companion.handle('shut down computer','document'),'document_save')
            self.assertEqual(companion.handle('cancel','document_save'),'document')
            companion.handle('shut down computer','document')
            self.assertEqual(companion.handle('no','document_save'),'power_confirm')
            power.assert_not_called()
    def test_whats_new_reads_same_packaged_release_notes_and_preserves_return(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);guide=root/'guide.txt';guide.write_text('GUIDE\nSTART\nExisting help.\n')
            (root/'Voice Companion Release Notes.txt').write_text('Release Notes for Voice Companion '+companion.APP_VERSION+'\n\nNew Apps and Features\n\n1. Test feature\n\na. Actual detail\n')
            with patch.object(companion,'GUIDE',guide),patch.object(companion,'speak') as speech,patch.object(companion,'help_session',None),patch.object(companion,'help_return_mode','awake'):
                self.assertEqual(companion.handle('what is new','awake'),'help')
                self.assertIn('Actual detail',' '.join(str(x) for x in speech.call_args_list))
                self.assertEqual(companion.help_session.level,'article')
                self.assertEqual(companion.handle('close help','help'),'awake')
    def test_main_menu_check_updates_dispatches_same_command(self):
        index=next(i for i,(name,cmd) in enumerate(companion.MAIN_MENU_CHOICES) if cmd=='check for updates')
        updater=Mock(busy=False)
        with patch.object(companion,'MAIN_MENU_INDEX',index),patch.object(companion,'UPDATES',updater),patch.object(companion,'speak'):
            self.assertEqual(companion.handle('ok','awake'),'awake')
            updater.check.assert_called_once_with(manual=True)

if __name__=='__main__':unittest.main()
