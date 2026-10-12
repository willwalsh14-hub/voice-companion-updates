"""Consumer regressions for keyboard browsing and private voice input."""
import unittest
from unittest.mock import Mock,patch
import companion as app
from browser_navigation import prepare_address,looks_like_address,contains_address
from external_microphone import external_input,find_external_input,ExternalMicrophoneGuard
from menu_navigation import announce_item
from settings_model import CATEGORIES,fields,prompt_text

class ConsumerRegressionTests(unittest.TestCase):
    def test_typed_addresses_preserve_path_query_and_accept_optional_prefix(self):
        for address,expected in [('example.com/SpeedTest','https://example.com/SpeedTest'),('www.example.com','https://www.example.com'),('http://example.com/Test?x=One#Result','https://example.com/Test?x=One#Result'),('https://example.com/Test','https://example.com/Test')]:
            self.assertTrue(looks_like_address(address));self.assertEqual(prepare_address(address)[0],expected)
        self.assertFalse(looks_like_address('speed test near me'))
        for address in ('https://user:password@example.com','javascript:alert(1)','example.com:1234'):
            with self.assertRaises(ValueError):prepare_address(address)

    def test_address_box_opens_domains_but_searches_words(self):
        session=Mock(snapshot={'text':'page'},page_revision=1)
        with patch.object(app,'web_session',session),patch.object(app,'speak_web_result'):
            app.run_web_search('example.com/SpeedTest');session.open.assert_called_once_with('example.com/SpeedTest');session.search.assert_not_called()
            app.run_web_search('speed test');session.search.assert_called_once_with('speed test')

    def test_spoken_addresses_do_not_enter_search_or_email(self):
        session=Mock()
        with patch.object(app,'web_session',session),patch.object(app,'speak') as speak,patch.object(app,'flush_keyboard_edits'):
            for mode in ('search','email_draft','web','note'):
                for text in ('example.com','person@example.com','person at example dot com','open website example dot com'):
                    self.assertEqual(app.handle(text,mode),mode);self.assertIn('must be typed',speak.call_args.args[0])
            session.open.assert_not_called();session.search.assert_not_called();session.dictate_to_focus.assert_not_called()

    def test_arriving_at_item_does_not_announce_boundary_at_any_verbosity(self):
        for level in ('high','medium','low'):
            for index in (0,1):
                text=prompt_text(announce_item('An item',index,2),level)
                self.assertTrue(text.startswith('An item'));self.assertNotIn('of list',text)
        with patch.object(app,'MAIN_MENU_INDEX',len(app.MAIN_MENU_CHOICES)-2),patch.object(app,'SYNTH_PICK_INDEX',None),patch.object(app,'VOICE_PICK_INDEX',None),patch.object(app,'speak') as speak:
            app.handle('next','awake');self.assertIn('Exit Voice Companion',speak.call_args.args[0]);self.assertNotIn('of list',speak.call_args.args[0])
            app.handle('next','awake');speak.assert_called_with('End of list.')
            app.navigation_key('Home','awake');self.assertIn('Word processor',speak.call_args.args[0])
            app.handle('previous','awake');speak.assert_called_with('Start of list.')

    def test_all_options_categories_are_named_options(self):
        for name in CATEGORIES:self.assertTrue(name.endswith(' options'));self.assertTrue(fields(name,{}))

    def test_refresh_is_available_while_keyboard_only_and_asleep(self):
        session=Mock(page_revision=1,refresh_changed=Mock(return_value=''))
        with patch.object(app,'web_session',session),patch.object(app,'SLEEP_RETURN_MODE','web'),patch.object(app,'sync_app_context') as sync:
            app.refresh_browser_view('web');app.refresh_browser_view('sleep')
            self.assertEqual(session.refresh_changed.call_count,2);sync.assert_called_with('sleep')

    def test_internal_and_unknown_inputs_cannot_enable_voice(self):
        for name in ('Microphone Array (Realtek)','Internal microphone','USB Microphone','Virtual USB microphone'):
            self.assertFalse(external_input({'name':name,'max_input_channels':2},[]))
        external={'name':'Microphone (USB Audio)','max_input_channels':2,'hostapi':1}
        endpoints=[{'name':external['name'],'connection':'USB\\VID_1234'}]
        self.assertTrue(external_input(external,endpoints))
        devices=[{'name':'Microphone Array (Realtek)','max_input_channels':2,'hostapi':0},external]
        sd=Mock(query_devices=Mock(return_value=devices),query_hostapis=Mock(return_value={'name':'Windows WASAPI'}))
        self.assertEqual(find_external_input(sd,endpoints)[0],1)
        self.assertIsNone(find_external_input(sd,[]))

    def test_disconnected_external_endpoint_disables_voice_even_if_audio_list_cached(self):
        device={'name':'Microphone (USB Audio)','max_input_channels':2}
        guard=ExternalMicrophoneGuard(Mock(query_devices=Mock(return_value=device)),0,device)
        with patch('external_microphone.connected_external_endpoints',return_value=[{'name':device['name'],'connection':'USB\\VID_1234'}]):self.assertTrue(guard.check_connection())
        with patch('external_microphone.connected_external_endpoints',return_value=[]):self.assertFalse(guard.check_connection())

if __name__=='__main__':unittest.main()
