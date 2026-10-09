import json
import unittest
from unittest.mock import Mock,patch
import companion

class CommandLatencyTests(unittest.TestCase):
    def test_pause_variants_execute_from_partial_without_endpoint_or_purge(self):
        with patch.object(companion,'control_speech') as control,patch.object(companion,'interrupt_speech') as purge:
            for phrase in ('shut up','shut the hell up','shut the fuck up','be quiet','stop speaking'):
                recognizer=Mock();recognizer.PartialResult.return_value=json.dumps({'partial':phrase})
                audio=bytearray(b'pending')
                self.assertTrue(companion.fast_offline_silence(recognizer,audio))
                self.assertEqual(audio,bytearray());recognizer.Reset.assert_called_once()
            self.assertEqual(control.call_count,5);purge.assert_not_called()

    def test_complete_navigation_skips_second_model_but_dictation_and_actions_do_not(self):
        with patch.object(companion,'INPUT_MODE','mixed'),patch.object(companion,'VOICE_PICK_INDEX',None),patch.object(companion,'SYNTH_PICK_INDEX',None):
            for phrase in ('go to end of document','select current paragraph','say font','voice list','speak faster'):
                self.assertTrue(companion.fast_command_request(phrase,'document'),phrase)
            for phrase in ('This is my paragraph','send email','delete document','save as new filename','set font Times New Roman 16 point'):
                self.assertFalse(companion.fast_command_request(phrase,'document'),phrase)
            with patch.object(companion,'INPUT_MODE','dictation'):
                self.assertFalse(companion.fast_command_request('select current paragraph','document'))

    def test_partial_non_silence_is_never_executed(self):
        for phrase in ('stop','next','send email','type shut up','shut the','go to end of document'):
            recognizer=Mock();recognizer.PartialResult.return_value=json.dumps({'partial':phrase})
            self.assertFalse(companion.fast_offline_silence(recognizer,bytearray(b'audio')))
            recognizer.Reset.assert_not_called()

class ConciseMenusTests(unittest.TestCase):
    def test_wake_and_main_menu_browsing_confirm_aliases(self):
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as folder,patch.object(companion,'APP',Path(folder)),patch.object(companion,'speak') as speak,patch.object(companion,'SLEEP_RETURN_MODE','awake'),patch.object(companion,'MAIN_MENU_INDEX',None),patch.object(companion,'SYNTH_PICK_INDEX',None),patch.object(companion,'VOICE_PICK_INDEX',None):
            self.assertEqual(companion.resume_from_sleep(),'awake')
            speak.assert_called_with(companion.MAIN_MENU_PROMPT)
            companion.handle('next','awake');speak.assert_called_with('Documents, 1 of 7.')
            companion.handle('previous','awake');speak.assert_called_with('Help, 7 of 7.')
            for confirm in ('that one','confirm that','ok','okay'):
                companion.MAIN_MENU_INDEX=0
                self.assertEqual(companion.handle(confirm,'awake'),'document')
            self.assertEqual(companion.handle('write a document','awake'),'document')

    def test_synthesizer_browsing_only_says_position_and_name(self):
        with patch.object(companion,'synthesizers',return_value=[('windows','Windows speech'),('espeak','eSpeak NG')]),patch.object(companion,'SYNTH_PICK_INDEX',0),patch.object(companion,'VOICE_PICK_INDEX',None),patch.object(companion,'speak') as speak:
            companion.voice_menu('next');speak.assert_called_with('eSpeak NG, 2 of 2.')
            companion.voice_menu('previous');speak.assert_called_with('Windows speech, 1 of 2.')

    def test_navigation_fast_path_all_units_and_aliases(self):
        with patch.object(companion,'INPUT_MODE','mixed'):
            for mode in ('document','email_draft'):
                for direction in ('next','previous'):
                    for unit in ('paragraph','word','line','sentence','character','heading'):
                        self.assertTrue(companion.fast_command_request(direction+' '+unit,mode))
                self.assertTrue(companion.fast_command_request('go forward a paragraph',mode))
