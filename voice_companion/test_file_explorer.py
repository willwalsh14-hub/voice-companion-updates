import tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from file_manager import FileManager,Item,SORT_MENU
from file_explorer import FileView
from test_file_manager import FakeFiles,settle


class FileExplorerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        for name in ('Alpha.txt','Alpine.txt','Beta.txt','123.txt'):(self.root/name).write_text(name)
        (self.root/'Folder').mkdir();(self.root/'Folder'/'Alpine nested.txt').write_text('Nested')
        self.manager=FileManager(FakeFiles(self.root));self.manager.refresh(str(self.root));settle(self.manager)

    def test_first_letters_cycle_narrow_backspace_and_escape_restore(self):
        m=self.manager
        m.command('file letters a');self.assertEqual(m.current().name,'Alpha.txt')
        m.command('file letters a');self.assertEqual(m.current().name,'Alpine.txt')
        m.command('file letters l');m.command('file letters p');m.command('file letters i')
        self.assertEqual([item.name for item in m.items],['Alpine.txt'])
        m.command('file backspace');self.assertEqual(len(m.items),2)
        m.command('escape');self.assertEqual(len(m.items),5);self.assertEqual(m.location,str(self.root))
        m.view.last_key=0;m.command('file letters 1');self.assertEqual(m.current().name,'123.txt')

    def test_search_filters_names_and_recursive_search_can_be_cleared(self):
        m=self.manager;m.command('search files');m.command('alp');self.assertEqual(len(m.items),2)
        m.command('clear search');self.assertEqual(len(m.items),5)
        m.command('search subfolders for alpine');settle(m)
        self.assertEqual({item.name for item in m.items},{'Alpine.txt','Alpine nested.txt'})
        m.command('clear search');settle(m);self.assertEqual(len(m.items),5)

    def test_every_sort_has_menu_voice_and_key_and_unknown_dates_stay_last(self):
        import companion as app
        m=self.manager;m.view.all_items=[Item('One','one',created=2,modified=10,added=4),Item('Two','two',created=1,modified=20,added=3),Item('Unknown','unknown')];m.items=m.view.shown()
        for name,key,request in SORT_MENU:
            self.assertIsNotNone(app.navigation_key(key,'files'))
            m.command(request)
        m.command('sort by date modified descending');self.assertEqual([i.name for i in m.items],['Two','One','Unknown'])
        m.command('sort by date created ascending');self.assertEqual([i.name for i in m.items],['Two','One','Unknown'])
        m.command('sort files');self.assertEqual(m.menu,'sort');m.command('end');m.command('enter');self.assertTrue(m.view.descending)

    def test_mp3_open_after_typeahead_and_return_to_drives_routes_to_media(self):
        m=self.manager
        (self.root/'Music.mp3').write_bytes(b'test')
        m.refresh_here();settle(m)
        m.command('file letters m');m.command('file letters u')
        self.assertEqual(m.current().name,'Music.mp3')
        m.command('open');settle(m);self.assertEqual(m.opened,self.root/'Music.mp3')
        m.opened=None;m.command('drive list');settle(m);m.command('enter');settle(m)
        m.command('file letters m');m.command('open');settle(m)
        self.assertEqual(m.opened,self.root/'Music.mp3')

    def test_date_added_is_persisted_first_seen_and_not_creation_time(self):
        store=self.root/'state';item=Item('File','file',created=1)
        first=FileView(store).dated([item],'folder')[0]
        time.sleep(.01)
        again=FileView(store).dated([item,Item('New','new')],'folder')
        self.assertEqual(again[0].added,first.added);self.assertGreater(again[1].added,first.added)
        self.assertNotEqual(first.created,first.added)

    def test_pending_search_treats_app_names_as_literal_text(self):
        import companion as app
        m=self.manager
        with patch.object(app,'FILE_MANAGER',m),patch.object(app,'speak'),patch.object(app,'sync_app_context'):
            m.command('search files');self.assertEqual(app.handle('word processor','files'),'files')
            self.assertEqual(m.view.filter,'word processor')
            m.command('clear search');m.command('search files');self.assertEqual(app.handle('main menu','files'),'files')
            self.assertEqual(m.view.filter,'main menu')

    def test_empty_prefix_and_cancellation_do_not_leave_a_stale_search(self):
        m=self.manager;m.items=[];m.view.all_items=[]
        m.command('file backspace')
        m.command('search files');self.assertIn('canceled',m.command('escape').lower());self.assertIsNone(m.pending)


if __name__=='__main__':unittest.main()
