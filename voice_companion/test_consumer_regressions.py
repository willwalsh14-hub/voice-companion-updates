"""Consumer regressions for keyboard browsing and private voice input."""
import unittest
from unittest.mock import Mock,patch
import companion as app
from browser_navigation import prepare_address,looks_like_address,contains_address
from menu_navigation import announce_item
from settings_model import CATEGORIES,fields,prompt_text

class ConsumerRegressionTests(unittest.TestCase):
    def test_typed_addresses_preserve_path_query_and_accept_optional_prefix(self):
        for address,expected in [('example.com/SpeedTest','https://example.com/SpeedTest'),('www.example.com','https://www.example.com'),('http://example.com/Test?x=One#Result','http://example.com/Test?x=One#Result'),('https://example.com/Test','https://example.com/Test')]:
            self.assertTrue(looks_like_address(address));self.assertEqual(prepare_address(address)[0],expected)
        self.assertFalse(looks_like_address('speed test near me'))
        self.assertEqual(prepare_address('youtube.com/@ExampleChannel')[0],'https://youtube.com/@ExampleChannel')
        self.assertEqual(prepare_address('example.com/?email=person@example.com')[0],'https://example.com/?email=person@example.com')
        for address in ('https://user:password@example.com','javascript:alert(1)','http://example.com:65536','http://example.com:0'):
            with self.assertRaises(ValueError):prepare_address(address)

    def test_address_box_opens_domains_but_searches_words(self):
        session=Mock(snapshot={'text':'page'},page_revision=1)
        with patch.object(app,'web_session',session),patch.object(app,'speak_web_result'):
            app.run_web_search('example.com/SpeedTest');session.open.assert_called_once_with('example.com/SpeedTest');session.search.assert_not_called()
            app.run_web_search('speed test');session.search.assert_called_once_with('speed test')

    def test_spoken_website_address_reaches_navigation_confirmation(self):
        session=Mock()
        with patch.object(app,'web_session',session),patch.object(app,'speak') as speak,patch.object(app,'pending_website',None):
            app.handle('open website example dot com','awake')
            self.assertTrue(any('example.com' in str(c.args) for c in speak.call_args_list))
            self.assertFalse(any('must be typed' in str(c.args) for c in speak.call_args_list))

    def test_repeated_home_end_announce_list_boundary(self):
        with patch.object(app,'MAIN_MENU_INDEX',1),patch.object(app,'SYNTH_PICK_INDEX',None),patch.object(app,'VOICE_PICK_INDEX',None),patch.object(app,'speak') as speak:
            app.navigation_key('Home','awake');self.assertNotIn('of list',speak.call_args.args[0])
            app.navigation_key('Home','awake');speak.assert_called_with('Start of list.')
            app.navigation_key('End','awake');self.assertNotIn('of list',speak.call_args.args[0])
            app.navigation_key('End','awake');speak.assert_called_with('End of list.')

    def test_arriving_at_item_does_not_announce_boundary_at_any_verbosity(self):
        for level in ('high','medium','low'):
            for index in (0,1):
                text=prompt_text(announce_item('An item',index,2),level)
                self.assertTrue(text.startswith('An item'));self.assertNotIn('of list',text)
        with patch.object(app,'MAIN_MENU_INDEX',len(app.MAIN_MENU_CHOICES)-2),patch.object(app,'SYNTH_PICK_INDEX',None),patch.object(app,'VOICE_PICK_INDEX',None),patch.object(app,'speak') as speak:
            app.handle('next','awake');self.assertIn('Exit Voice Companion',speak.call_args.args[0]);self.assertNotIn('of list',speak.call_args.args[0])
            app.handle('next','awake');speak.assert_called_with('End of list.')
            app.navigation_key('Home','awake');self.assertIn('File manager',speak.call_args.args[0])
            app.handle('previous','awake');speak.assert_called_with('Start of list.')

    def test_all_options_categories_are_named_options(self):
        for name in CATEGORIES:self.assertTrue(name.endswith(' options'));self.assertTrue(fields(name,{}))

    def test_refresh_is_available_while_keyboard_only_and_asleep(self):
        session=Mock(page_revision=1,refresh_changed=Mock(return_value=''))
        with patch.object(app,'web_session',session),patch.object(app,'SLEEP_RETURN_MODE','web'),patch.object(app,'sync_app_context') as sync:
            app.refresh_browser_view('web');app.refresh_browser_view('sleep')
            self.assertEqual(session.refresh_changed.call_count,2);sync.assert_called_with('sleep')

    def test_main_menu_order_is_identical_for_voice_and_keyboard(self):
        expected=['File manager','Word processor','Document reader','Notepad','Email','Web browser','Media','Options','Check for updates','Help','Shut down the computer','Restart the computer','Exit Voice Companion']
        self.assertEqual([label for label,_ in app.MAIN_MENU_CHOICES],expected)
        with patch.object(app,'MAIN_MENU_INDEX',None),patch.object(app,'SYNTH_PICK_INDEX',None),patch.object(app,'VOICE_PICK_INDEX',None),patch.object(app,'speak') as speak:
            for label in expected:app.handle('next','awake');self.assertTrue(speak.call_args.args[0].startswith(label))
            app.MAIN_MENU_INDEX=None
            for label in expected:
                request=app.navigation_key('Down','awake');app.handle_keyboard(request,'awake');self.assertTrue(speak.call_args.args[0].startswith(label))

    def test_notepad_options_return_to_existing_note_without_changing_it(self):
        with patch.object(app,'note_buffer',['Keep my unfinished note']),patch.object(app,'speak'):
            self.assertEqual(app.settings_action('open_notepad','note'),'note')
            self.assertEqual(app.note_buffer,['Keep my unfinished note'])

    def test_option_category_names_match_app_names(self):
        for label in ('Auto-update options','Word processor options','Notepad options'):self.assertIn(label,CATEGORIES);self.assertTrue(fields(label,{}))
        for label in ('Documents options','Updates options','Notes options'):self.assertNotIn(label,CATEGORIES)

    def test_default_microphone_accepts_jabra_usb_usbc_analog_and_internal_inputs(self):
        for name in ('Microphone (2- Jabra Evolve2 30', 'Jabra Engage 65', 'USB-C Headset', 'Microphone (Realtek Audio)', 'Microphone Array'):
            with self.subTest(name=name):
                device={'name':name,'default_samplerate':44100,'max_input_channels':1}
                sd=Mock(query_devices=Mock(return_value=device),check_input_settings=Mock())
                self.assertEqual(app.microphone_settings(sd),(device,44100,1))
                sd.query_devices.assert_called_once_with(kind='input')
                sd.check_input_settings.assert_called_once_with(samplerate=44100,channels=1,dtype='int16')

if __name__=='__main__':unittest.main()
