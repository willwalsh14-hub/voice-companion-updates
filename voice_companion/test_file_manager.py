import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch,Mock
from file_manager import FileManager,Item,ShellFiles,valid_name

class FakeFiles:
    def __init__(self,root):self.root=Path(root);self.clip=([],False);self.actions=[]
    def roots(self):return [Item('Test drive',str(self.root),True,True)]
    def list(self,path):return [Item(p.name,str(p),p.is_dir()) for p in sorted(Path(path).iterdir(),key=lambda p:(not p.is_dir(),p.name))]
    def parent(self,path):return str(Path(path).parent)
    def clipboard(self,paths,cut):self.clip=(list(paths),cut)
    def get_clipboard(self):return self.clip
    def operate(self,action,paths,destination=None,name=None):self.actions.append((action,list(paths),destination,name))

def settle(manager):
    notices=[];until=time.monotonic()+5
    while manager.busy and time.monotonic()<until:
        result=manager.poll()
        if result:notices.append(result)
        time.sleep(.005)
    if manager.busy:raise AssertionError('File task did not finish')
    return ' '.join(notices)

class FileManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        (self.root/'Documents').mkdir();(self.root/'Documents'/'Voice Companion').mkdir();(self.root/'a.txt').write_text('Alpha');(self.root/'b.txt').write_text('Bravo')
        self.backend=FakeFiles(self.root);self.fm=FileManager(self.backend);self.fm.refresh();settle(self.fm)
    def test_drives_boundaries_enter_escape_and_parent_focus(self):
        self.assertIn('Start of list',self.fm.command('previous'))
        with self.assertRaises(ValueError):self.fm.command('select')
        self.fm.command('enter');settle(self.fm)
        self.fm.command('Documents');settle(self.fm)
        self.fm.command('Voice Companion');settle(self.fm)
        self.fm.command('escape');settle(self.fm)
        self.assertEqual(self.fm.location,str(self.root/'Documents'))
        self.assertEqual(self.fm.current().name,'Voice Companion')
        self.fm.command('escape');settle(self.fm);self.assertEqual(self.fm.location,str(self.root))
        self.fm.command('escape');settle(self.fm);self.assertIsNone(self.fm.location)
        self.assertEqual(self.fm.command('escape'),'__main__')
    def test_selection_verbs_batch_clipboard_delete_cancel_and_rename(self):
        self.fm.command('enter');settle(self.fm)
        for command in ('select a.txt','mark b.txt','uncheck a.txt','check a.txt','unmark b.txt','select b.txt'):self.fm.command(command)
        self.assertEqual(len(self.fm.selected),2)
        self.assertIn('Unselected',self.fm.command('unselect a.txt'));self.fm.command('select a.txt')
        self.fm.command('copy');settle(self.fm)
        self.assertEqual(set(self.backend.clip[0]),self.fm.selected)
        self.fm.command('delete');self.fm.command('no');self.assertEqual(self.backend.actions,[])
        self.fm.command('delete');self.fm.command('yes');settle(self.fm)
        self.assertEqual(self.backend.actions[0][0],'delete');self.assertEqual(len(self.backend.actions[0][1]),2)
        self.fm.command('clear selection');self.fm.index=1
        self.fm.command('rename');self.fm.command('renamed.txt');settle(self.fm)
        self.assertEqual(self.backend.actions[-1][0],'rename');self.assertEqual(self.backend.actions[-1][3],'renamed.txt')
    def test_menu_exposes_keys_and_voice_and_does_not_execute_on_arrows(self):
        self.fm.command('file manager menu');self.assertIn('Ctrl+C',str(self.fm.labels()))
        self.fm.command('next');self.fm.command('end');self.assertEqual(self.backend.actions,[])
        self.assertIn('End of list',self.fm.command('next'))
        self.fm.command('escape');self.assertFalse(self.fm.menu)
    def test_internal_reader_opens_file_without_external_launch(self):
        self.fm.command('enter');settle(self.fm)
        self.fm.command('a.txt');notice=settle(self.fm)
        self.assertEqual(self.fm.opened.text,'Alpha');self.assertIn('Read-only',notice)
    def test_cancelled_read_does_not_replace_new_location(self):
        import threading
        gate=threading.Event()
        self.fm.start_job(lambda:(gate.wait(2),[])[1],lambda items:self.fm.items.clear(),'Loading')
        self.fm.command('escape');self.assertFalse(self.fm.busy)
        gate.set();time.sleep(.03);self.fm.poll();self.assertTrue(self.fm.items)
    def test_invalid_names_and_drive_alias(self):
        for name in ('../bad','CON','NUL.txt','name.','a/b','a:b'):
            with self.assertRaises(ValueError):valid_name(name)
        self.fm.items=[Item('Local disk (C:)','C:\\',True,True)]
        self.fm.choose('c drive');self.assertEqual(self.fm.index,0)
    def test_real_dispatch_options_return_and_delete_keyboard_confirmation(self):
        import companion as app
        self.fm.command('enter');settle(self.fm)
        with patch.object(app,'FILE_MANAGER',self.fm),patch.object(app,'APP_WINDOW',None),patch.object(app,'speak') as speak,patch.object(app,'sync_app_context'),patch.object(app,'CONFIRM_CHOICE','yes'):
            app.handle('delete','files');self.assertEqual(app.CONFIRM_CHOICE,'no')
            self.assertEqual(app.navigation_key('Enter','files'),'no')
            app.handle('no','files');self.assertFalse(self.backend.actions)
        self.assertEqual(app.MAIN_MENU_CHOICES[0],('File manager','file manager'))
        self.assertEqual(app.options_return_notice('document'),'Back to document.')

@unittest.skipUnless(os.name=='nt','Real Windows Shell integration')
class WindowsShellTests(unittest.TestCase):
    def test_shell_namespace_clipboard_copy_move_rename_and_recycle(self):
        with tempfile.TemporaryDirectory(prefix='VoiceCompanion-Shell-Test-') as folder:
            root=Path(folder);source=root/'source';target=root/'target';source.mkdir();target.mkdir()
            (source/'one.txt').write_text('Shell copy');(source/'folder').mkdir();(source/'folder'/'nested.txt').write_text('Nested')
            backend=ShellFiles()
            self.assertTrue(backend.roots())
            self.assertTrue(any(i.path.lower().startswith('c:') for i in backend.roots()))
            names={i.name for i in backend.list(str(source))};self.assertEqual(names,{'one.txt','folder'})
            paths=[str(source/'one.txt'),str(source/'folder')];backend.clipboard(paths,False)
            actual,cut=backend.get_clipboard();self.assertEqual(set(actual),set(paths));self.assertFalse(cut)
            backend.operate('copy',actual,str(target));self.assertEqual((target/'one.txt').read_text(),'Shell copy');self.assertTrue((target/'folder'/'nested.txt').exists())
            with self.assertRaises(ValueError):backend.operate('copy',actual,str(target))
            backend.operate('rename',[str(target/'one.txt')],name='renamed.txt');self.assertTrue((target/'renamed.txt').exists())
            backend.clipboard([str(target/'renamed.txt')],True)
            cut_paths,is_cut=backend.get_clipboard();self.assertTrue(is_cut)
            backend.operate('cut',cut_paths,str(source));backend.finish_cut(cut_paths);self.assertFalse((target/'renamed.txt').exists());self.assertTrue((source/'renamed.txt').exists())
            backend.operate('delete',[str(source/'renamed.txt')]);self.assertFalse((source/'renamed.txt').exists())
            from win32com.client import Dispatch
            recycled=[i for i in Dispatch('Shell.Application').NameSpace(10).Items() if str(i.Name).startswith('renamed')]
            self.assertTrue(recycled,'The disposable file was not found in Recycle Bin.')

if __name__=='__main__':unittest.main()
