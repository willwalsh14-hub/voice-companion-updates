import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch
import companion as app
from document_editor import VoiceDocument
from file_manager import FileManager, Item, ShellFiles
from task_navigation import TaskNavigation, DOCUMENT_ACTIONS, SHORTCUTS
from test_file_manager import FakeFiles, settle


class TaskMenuTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name,value in {'APP':self.root,'APP_WINDOW':None,'TASK_NAV':None,'document':None,'FILE_MANAGER':None,
                           'FILE_READER':None,'MEDIA_HUB':None,'SETTINGS_PANEL':None,'NAMED_LIST':None,
                           'DOCUMENT_CLOSE_TARGET':'awake','DOCUMENT_NAME_RETURN':'awake','DOCUMENT_BASELINE':None,
                           'DOCUMENT_NAMING_ACTION':'name document',
                           'SETTINGS_RETURN_MODE':'awake',
                           'KEYBOARD_DIRTY':{},'EDIT_ACK':{},'INPUT_MODE':'mixed','PREFERENCES':dict(app.PREFERENCES)}.items():
            self.stack.enter_context(patch.object(app,name,value))
        self.say=self.stack.enter_context(patch.object(app,'speak'))
        self.stack.enter_context(patch.object(app,'sync_app_context'))

    def test_general_file_manager_mp3_uses_player_without_document_picker(self):
        path=self.root/'music.mp3';path.write_bytes(b'test')
        hub=Mock();app.MEDIA_HUB=hub
        self.assertEqual(app.task_navigation().selected_file(path),'local_media')
        hub.player.open.assert_called_once_with(path.resolve().as_uri(),path.name,'local')

    def test_search_chord_leaves_menu_or_pending_prompt_and_update_keys_are_available(self):
        app.FILE_MANAGER=FileManager(FakeFiles(self.root))
        for pending in (None,('rename',['a.txt']),('search',[])):
            app.FILE_MANAGER.pending=pending;app.FILE_MANAGER.menu='sort'
            self.assertEqual(app.navigation_key('Ctrl+E','files'),'search files')
            self.assertFalse(app.FILE_MANAGER.menu);self.assertIsNone(app.FILE_MANAGER.pending)
        for key,command in (('F5','update status'),('Alt+S','update status'),('Alt+C','cancel update'),('Escape','cancel update')):
            self.assertEqual(app.navigation_key(key,'update_download'),command)

    def test_word_processor_opens_four_item_menu_without_creating_file(self):
        self.assertEqual(app.handle('word processor','awake'),'word_processor')
        self.assertIsNone(app.document)
        self.assertEqual(app.menu_state('word_processor')[0],['Create a document','Open a document','Recent documents','Options'])
        self.assertEqual(app.handle('enter','word_processor'),'document')
        self.assertIsNotNone(app.document)

    def test_keyboard_open_and_number_letter_menu_accelerators_dispatch_end_to_end(self):
        manager=FileManager(FakeFiles(self.root))
        with patch('file_manager.FileManager',return_value=manager):
            self.assertEqual(app.handle(app.navigation_key('Ctrl+O','word_processor'),'word_processor'),'files')
        settle(manager);self.assertIsNone(manager.location)
        app.handle('create a document','awake')
        app.document.append_text('Keep this text.')
        mode=app.handle(app.navigation_key('Alt','document'),'document')
        self.assertEqual(mode,'document_menu')
        mode=app.handle(app.navigation_key('MenuPick:3',mode),mode)
        self.assertEqual(app.task_navigation().group,'Format')
        self.assertEqual(app.handle(app.navigation_key('MenuPick:A',mode),mode),'document')
        self.assertTrue(app.document.paragraphs[app.document.cursor].bold)

    def test_open_starts_at_drives_and_escape_returns_to_word_processor(self):
        manager=FileManager(FakeFiles(self.root))
        with patch('file_manager.FileManager',return_value=manager):
            self.assertEqual(app.handle('open a document','word_processor'),'files')
        settle(manager)
        self.assertIsNone(manager.location)
        self.assertEqual(app.navigation_key('Ctrl+D','files'),'drive list')
        self.assertIsNone(app.navigation_key('Ctrl+Home','files'))
        self.assertIsNone(app.navigation_key('Ctrl+M','files'))
        self.assertEqual(app.handle('escape','files'),'word_processor')

    def test_picker_docx_opens_editable_and_records_recent(self):
        doc=VoiceDocument(self.root,title='Existing');doc.append_text('Keep this text.');doc.save()
        manager=FileManager(FakeFiles(self.root));manager.opened=doc.path
        app.FILE_MANAGER=manager;app.task_navigation().picker='document'
        self.assertEqual(app.poll_file_manager('files'),'document')
        self.assertEqual(app.document.paragraphs[0].text,'Keep this text.')
        self.assertEqual(app.handle('recent documents','word_processor'),'recent_documents')
        self.assertEqual(app.menu_state('recent_documents')[0],['Existing.docx'])
        self.assertEqual(app.handle('enter','recent_documents'),'document')

    def test_missing_recent_file_reports_error_without_creating_it(self):
        nav=app.task_navigation();nav.recent=[str(self.root/'Gone.docx')]
        self.assertEqual(app.handle('enter','recent_documents'),'recent_documents')
        self.assertFalse((self.root/'Gone.docx').exists())
        self.assertIn('no longer available',self.say.call_args.args[0])

    def test_close_named_document_returns_to_launch_menu_after_save(self):
        app.document=VoiceDocument(self.root,title='Report');app.document.append_text('Saved body')
        self.assertEqual(app.handle('leave document','document'),'document_save')
        self.assertEqual(app.handle('yes','document_save'),'word_processor')
        self.assertIsNone(app.document)
        self.assertTrue((self.root/'Report.docx').is_file())

    def test_options_select_word_processor_and_return_target(self):
        panel=Mock();app.SETTINGS_PANEL=panel
        with patch.object(app,'open_settings',return_value='settings'):
            self.assertEqual(app.handle('options','word_processor'),'settings')
        panel.command.assert_called_once_with('Word processor')
        self.assertEqual(app.SETTINGS_RETURN_MODE,'word_processor')

    def test_parameter_shortcut_does_not_dictate_prompt_into_document(self):
        app.document=VoiceDocument(self.root,title='Test');app.document.append_text('Unchanged')
        key=app.navigation_key('Ctrl+Shift+P','document')
        self.assertEqual(app.handle(key,'document'),'task_input')
        self.assertEqual(app.handle('18','task_input',typed=True),'document')
        self.assertEqual(app.document.paragraphs[0].text,'Unchanged')
        self.assertEqual(app.document.paragraphs[0].size,18)
        self.assertEqual(app.handle(app.navigation_key('Ctrl+F','document'),'document'),'task_input')
        self.assertEqual(app.handle('cancel','task_input'),'document')

    def test_document_shortcuts_toggle_and_menu_reaches_table_commands(self):
        app.document=VoiceDocument(self.root,title='Test');app.document.append_text('Text')
        for expected in (True,False):
            self.assertEqual(app.handle(app.navigation_key('Ctrl+B','document'),'document'),'document')
            self.assertEqual(app.document.paragraphs[0].bold,expected)
        self.assertEqual(app.handle(app.navigation_key('Alt','document'),'document'),'document_menu')
        self.assertEqual(app.handle('Table','document_menu'),'document_menu')
        self.assertIn('Delete table',app.menu_state('document_menu')[0])
        self.assertEqual(app.handle('escape','document_menu'),'document_menu')
        self.assertEqual(app.handle('escape','document_menu'),'document')

    def test_media_menu_routes_radio_and_podcasts_without_recursing(self):
        self.assertEqual(app.handle('media menu','awake'),'media_menu')
        self.assertEqual(app.menu_state('media_menu')[0],['Radio','Podcasts','Media player'])
        self.assertIn(('Media','media menu'),app.MAIN_MENU_CHOICES)
        self.assertNotIn(('Radio','radio'),app.MAIN_MENU_CHOICES)
        self.assertEqual(app.handle('media player','media_menu'),'local_media')
        self.assertEqual(app.handle('escape','local_media'),'media_menu')

    def test_local_player_reuses_playback_engine_and_reports_paused_state(self):
        player=Mock(kind='local');player.status.return_value={'paused':True,'position':12,'duration':50}
        hub=Mock();hub.player=player;app.MEDIA_HUB=hub
        app.task_navigation().media_title='Sample.wav'
        self.assertEqual(app.handle('media status','local_media'),'local_media')
        self.assertIn('Paused.',self.say.call_args.args[0])
        app.handle('back 15 seconds','local_media');player.control.assert_called_with('seek',seconds=-15)
        player.adjust.return_value=1.5
        app.handle('speed one point five','local_media');player.adjust.assert_called_with('speed',1.5)

    def test_folder_notice_has_name_and_current_item_without_parent_path(self):
        folder=self.root/'Documents';folder.mkdir();(folder/'first.txt').write_text('Text')
        manager=FileManager(FakeFiles(self.root));manager.refresh(str(folder))
        result=settle(manager)
        self.assertTrue(result.startswith('Documents. first.txt, file.'))
        self.assertNotIn(str(self.root),result)

    def test_document_menu_fixed_actions_do_not_fall_through_to_dictation(self):
        app_commands={'create a document','leave document','name document','save as','help with documents'}
        for group,items in DOCUMENT_ACTIONS.items():
            for label,_,command in items:
                if command in app_commands or command.startswith(('@','toggle ')) or '{value}' in command:continue
                with self.subTest(group=group,label=label):
                    from document_editor import Paragraph
                    doc=VoiceDocument(self.root,title=group+' '+label,_opening=True)
                    doc.paragraphs=[Paragraph('Text in this paragraph.')]
                    doc.dictating=False
                    with patch.object(doc,'save'):result=doc.process(command)
                    self.assertNotIn('I did not recognize',result)

    def test_invalid_media_speed_keeps_parameter_prompt_for_retry(self):
        player=Mock(kind='local');player.adjust.return_value=1.5
        app.MEDIA_HUB=Mock(player=player)
        self.assertEqual(app.task_navigation().action('@speed','local_media'),'task_input')
        self.assertEqual(app.handle('not a speed','task_input',typed=True),'task_input')
        self.assertIsNotNone(app.task_navigation().prompt)
        self.assertEqual(app.handle('1.5','task_input',typed=True),'local_media')

    def test_menu_exit_preserves_document_save_confirmation(self):
        app.document=VoiceDocument(self.root,title='Draft');app.document.append_text('Keep me')
        self.assertEqual(app.handle('main menu','document_menu'),'document_save')

    def test_document_command_value_is_literal_even_if_it_names_an_app(self):
        app.document=VoiceDocument(self.root,title='Text');app.document.append_text('Text')
        self.assertEqual(app.handle(app.navigation_key('Ctrl+Shift+F','document'),'document'),'task_input')
        self.assertEqual(app.handle('Media player','task_input',typed=True),'document')
        self.assertEqual(app.document.paragraphs[0].font,'Media player')

    def test_save_as_shortcut_keeps_original_file(self):
        app.document=VoiceDocument(self.root,title='Original');app.document.append_text('Keep both files.')
        original=app.document.path;before=original.read_bytes()
        self.assertEqual(app.handle(app.navigation_key('Ctrl+Shift+S','document'),'document'),'document_name')
        self.assertEqual(app.handle('New copy','document_name',typed=True),'document')
        self.assertEqual(original.read_bytes(),before)
        self.assertEqual(app.document.path.name,'New copy.docx')
        self.assertTrue(app.document.path.exists())


if __name__=='__main__':unittest.main()
