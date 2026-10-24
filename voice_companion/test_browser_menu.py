import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import companion as app
from web_assistant import WebSession
from test_web_assistant import FakeBackend


class BrowserMenuTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.session = WebSession(self.folder.name, FakeBackend())
        self.session.backend.close=Mock()
        self.menu = self.session.menu

    def run_command(self, command, keep=True):
        return self.menu.process(command, self.session, keep)

    def test_resume_is_first_only_when_a_page_is_kept(self):
        self.session.open('https://example.test/')
        self.assertEqual(self.menu.choices(self.session,True)[0],('Resume where you left off','resume'))
        self.assertEqual(self.menu.choices(self.session,False)[0][1],'search')
        self.session.snapshot=None
        self.assertEqual(self.menu.choices(self.session,True)[0][1],'search')

    def test_spoken_favorite_commands_accept_added_final_punctuation(self):
        self.session.favorites.add('News','https://example.test/news')
        self.run_command('Go to favorite.')
        self.assertEqual(self.run_command('News.')[1],('open','https://example.test/news'))
        self.session.command('Go to News.')
        self.assertTrue(self.session.navigation_succeeded)
        self.session.favorites.add('Other.','https://example.test/other')
        self.assertEqual(self.run_command('Go to Other')[1],('open','https://example.test/other'))
        self.assertEqual(self.session.favorites.get('Other.')['name'],'Other.')

    def test_favorite_labels_aliases_and_go_to_saved_name(self):
        self.assertIn('Go to favorite',self.session.menu.start())
        self.assertNotIn('bookmark',self.session.menu.start())
        self.session.favorites.add('My News','https://example.test/News')
        self.assertEqual(self.run_command('go to My News')[1],('open','https://example.test/News'))
        self.run_command('go to favorite')
        self.assertEqual(self.run_command('enter')[1],('open','https://example.test/News'))
        result=self.session.command('go to My News')
        self.assertTrue(self.session.navigation_succeeded,result)

    def test_new_open_rename_delete_and_saved_bookmarks(self):
        self.session.open('example.com')
        self.run_command('new bookmark')
        self.assertEqual(self.menu.level, 'name')
        self.assertIn('Saved favorite My news', self.run_command('My news')[0])
        self.run_command('go to bookmark')
        self.assertEqual(self.run_command('enter')[1], ('open','https://example.com'))
        self.run_command('rename bookmark');self.run_command('enter')
        self.assertIn('Renamed', self.run_command('Daily news')[0])
        self.assertIsNone(self.session.favorites.get('My news'))
        reloaded = WebSession(self.folder.name, FakeBackend())
        self.assertEqual(reloaded.favorites.get('Daily news')['url'], 'https://example.com')
        self.run_command('delete bookmark');self.run_command('enter')
        self.assertEqual(self.session.favorites.all(), {})

    def test_typeahead_repeated_letter_prefix_and_boundaries(self):
        for name in ('News','NPR','Weather'):
            self.session.favorites.add(name,'https://example.com')
        self.run_command('go to bookmark')
        self.assertIn('NPR',self.run_command('Letter:n')[0])
        self.assertIn('News',self.run_command('Letter:n')[0])
        self.assertIn('NPR',self.run_command('Letter:p')[0])
        self.assertIn('End of list',self.run_command('end')[0] if self.menu.index==2 else (self.run_command('end'),self.run_command('next'))[1][0])
        self.run_command('home');self.assertEqual(self.run_command('previous')[0],'Start of list.')

    def test_history_survives_restart_and_opens_selected_page(self):
        self.session.open('example.com/one');self.session.open('example.com/two')
        self.session.open('example.com/two')
        self.assertEqual(len(self.menu.history()),2)
        other=WebSession(self.folder.name,FakeBackend())
        other.menu.process('web history',other,False)
        self.assertEqual(other.menu.process('enter',other,False)[1],('open','https://example.com/two'))

    def test_closed_retained_page_removes_resume_without_invalid_selection(self):
        self.session.open('example.com');self.run_command('end')
        self.assertEqual(self.menu.index,10)
        self.session.snapshot=None
        names,index,_=self.menu.menu(self.session,True)
        self.assertEqual(index,9);self.assertEqual(names[index],'Set up Chrome')
        self.run_command('previous');self.assertEqual(names[self.menu.index],'Downloads')
        self.run_command('previous')
        self.assertIn('Web history',self.run_command('enter')[0])

    def test_empty_lists_and_name_cancel_do_not_change_page(self):
        self.assertIn('Open a page',self.run_command('new bookmark')[0])
        self.assertIn('empty',self.run_command('delete bookmark')[0])
        self.assertIn('empty',self.run_command('enter')[0])
        self.run_command('escape');self.session.open('example.com')
        self.run_command('new bookmark');self.run_command('escape')
        self.assertEqual(self.session.favorites.all(),{})

    def test_duplicate_and_invalid_names_do_not_overwrite(self):
        self.session.open('example.com');self.session.favorites.add('News','https://example.com/old')
        self.run_command('new bookmark');self.assertIn('already exists',self.run_command('News')[0])
        self.assertIn('short name',self.run_command('a'*61)[0])
        self.assertEqual(self.session.favorites.get('News')['url'],'https://example.com/old')

    def test_fresh_browser_menu_and_escape_obey_keep_open(self):
        for keep in (False,True):
            self.session.open('example.com');self.session.reading.position=4
            with patch.object(app,'web_session',self.session),patch.object(app,'PREFERENCES',{'web_keep_open':keep}),patch.object(app,'speak'),patch.object(app,'APP_WINDOW',None),patch.object(app,'pending_website',None):
                self.assertEqual(app.handle('web browser','awake'),'browser_menu')
                self.assertEqual(app.navigation_key('Web:Escape','web'),'web browser menu')
                self.assertEqual(app.handle('web browser menu','web'),'browser_menu')
                choices=[x[1] for x in self.menu.choices(self.session,keep)]
                self.assertEqual('resume' in choices,keep)
                if keep:
                    self.session.backend.refocus=Mock(return_value=True)
                    self.assertEqual(app.handle('resume','browser_menu'),'web')
                    self.assertEqual(self.session.reading.position,4)

    def test_search_cancel_name_literal_and_keyboard_routes(self):
        self.session.open('example.com')
        with patch.object(app,'web_session',self.session),patch.object(app,'speak'),patch.object(app,'APP_WINDOW',None):
            self.assertEqual(app.handle('search or enter address','browser_menu'),'search')
            self.assertEqual(self.menu.process('search or type address',self.session,False)[1],'search')
            self.assertEqual(app.handle_keyboard(app.navigation_key('Escape','search'),'search'),'browser_menu')
            app.handle('new bookmark','browser_menu')
            self.assertTrue(app.app_context('browser_menu')['browser_name_input'])
            app.handle('Help','browser_menu',typed=True)
            self.assertIsNotNone(self.session.favorites.get('Help'))
            self.assertEqual(app.navigation_key('Enter','browser_menu'),'enter')
            self.assertEqual(app.navigation_key('Letter:n','browser_menu'),'Letter:n')

    def test_help_requests_interrupt_before_speaking_from_every_activity(self):
        for source in ('awake','document','email_draft','web','browser_menu','note','media','mailbox'):
            events=[]
            with patch.object(app,'web_session',self.session),patch.object(app,'help_session',None),patch.object(app,'APP_WINDOW',None),patch.object(app,'interrupt_speech',side_effect=lambda:events.append('stop')),patch.object(app,'speak',side_effect=lambda text:events.append('speak')),patch.object(app,'help_return_mode','awake'):
                self.assertEqual(app.handle('help with documents',source),'help')
                self.assertEqual(events[0],'stop')
                events.clear();app.handle('next','help')
                self.assertEqual(events[0],'stop')

    def test_quick_keys_queue_scroll_after_feedback_not_before(self):
        self.session.open('example.com')
        self.session.backend.scroll_to=Mock()
        self.session.keyboard('Quick:k')
        self.session.backend.scroll_to.assert_not_called()
        self.session.flush_scroll();self.session.backend.scroll_to.assert_called_once()

if __name__=='__main__':unittest.main()
