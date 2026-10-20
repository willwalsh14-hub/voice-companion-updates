import unittest,tempfile
from pathlib import Path
from unittest.mock import patch
import companion as app
from input_parity import catalog
from file_manager import MENU,FileManager
from test_file_manager import FakeFiles,settle


class InputParityTests(unittest.TestCase):
    def test_declared_menu_actions_expose_voice_and_keyboard_routes(self):
        rows=catalog();self.assertGreater(len(rows),150)
        for row in rows:
            with self.subTest(area=row['area'],action=row['action']):
                self.assertTrue(row['action']);self.assertTrue(row['voice']);self.assertTrue(row['keyboard'])
        for name,key,voice in MENU:
            with self.subTest(file_action=name):
                request=app.navigation_key(key,'files')
                self.assertEqual({'enter':'open','escape':'go back'}.get(request,request),voice)

    def test_file_search_and_sort_voice_and_keyboard_have_the_same_effect(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ('Alpine.txt','Alpha.txt','Beta.txt'):(root/name).write_text(name)
            for key,voice in (('Ctrl+F','search files'),('Ctrl+Shift+F','search subfolders'),('Alt+S','sort files'),('Ctrl+Shift+1','sort by name'),('Ctrl+Shift+2','sort by date created'),('Ctrl+Shift+3','sort by date modified'),('Ctrl+Shift+4','sort by date added'),('Ctrl+Shift+Up','sort ascending'),('Ctrl+Shift+Down','sort descending')):
                states=[]
                for use_key in (False,True):
                    manager=FileManager(FakeFiles(root));manager.refresh(str(root));settle(manager)
                    with patch.object(app,'FILE_MANAGER',manager),patch.object(app,'speak'),patch.object(app,'sync_app_context'):
                        app.handle(app.navigation_key(key,'files') if use_key else voice,'files')
                        states.append((manager.pending,manager.menu,manager.view.sort,manager.view.descending,[i.name for i in manager.items]))
                self.assertEqual(states[0],states[1],key)


if __name__=='__main__':unittest.main()
