import unittest
from unittest.mock import Mock,patch
import companion
from list_announcements import name_first

class MenuPromptTests(unittest.TestCase):
    def test_startup_version_help_and_wake_instructions(self):
        prompt=companion.startup_prompt()
        self.assertTrue(prompt.startswith('Voice Companion '+companion.APP_VERSION+' is ready.'))
        self.assertIn('Use your wake command or keyboard to begin.',prompt)
        self.assertNotIn('wake up',prompt.lower())
        self.assertIn('Help is available.',prompt)
    def test_all_picker_confirmation_aliases_skip_dictation_model(self):
        with patch.object(companion,'INPUT_MODE','mixed'):
            for mode in ('awake','media','web','mailbox','help'):
                for phrase in ('next','previous','that one','confirm','confirm that','okay','ok'):
                    self.assertTrue(companion.fast_command_request(phrase,mode),(phrase,mode))
            self.assertFalse(companion.fast_command_request('okay','note'))
            with patch.object(companion,'email_draft',Mock(replacement_candidates=[1],selection_candidates=[])):
                self.assertTrue(companion.fast_command_request('confirm that','email_draft'))
            with patch.object(companion,'INPUT_MODE','dictation'):
                self.assertFalse(companion.fast_command_request('okay','email_draft'))
    def test_every_named_picker_has_name_first_preserves_instructions(self):
        for kind in ('Voice','Preset','Choice','Station','Episode','Podcast','Favorite','Field','Topic','Subtopic','Folder','Link'):
            self.assertEqual(name_first(kind+' 2 of 7: Radio. Say that one to open it.'),'Radio (R), 2 of 7. Say that one to open it.')
        self.assertEqual(name_first('Found 7 links. Link 2 of 7: Radio. Open link Radio on example.org? Say yes.'),'Found 7 links. Radio (R), 2 of 7. Open link Radio on example.org? Say yes.')
        for text in ('Checked 3 of 7 feeds.','Paragraph 2 of 7. Text.','Dictated text remains unchanged.'):
            self.assertEqual(name_first(text),text)
    def test_main_menu_return_only_once_and_sleep_keeps_task(self):
        with patch.object(companion,'_handle',return_value='awake'),patch.object(companion,'speak') as speech:
            companion.handle('main menu','document')
            speech.assert_called_once_with(companion.MAIN_MENU_PROMPT)
        with patch.object(companion,'SLEEP_RETURN_MODE','document'),patch.object(companion,'speak') as speech:
            self.assertEqual(companion.resume_from_sleep(),'document')
            self.assertNotIn(companion.MAIN_MENU_PROMPT,[c.args[0] for c in speech.call_args_list])
    def test_main_menu_navigation_order_and_confirm(self):
        with patch.object(companion,'MAIN_MENU_INDEX',None),patch.object(companion,'speak') as speech,patch.object(companion,'SYNTH_PICK_INDEX',None),patch.object(companion,'VOICE_PICK_INDEX',None):
            companion.handle('next','awake');speech.assert_called_with('Documents (D), 1 of 13.')
            companion.handle('previous','awake');speech.assert_called_with('Exit Voice Companion (E), 13 of 13.')

