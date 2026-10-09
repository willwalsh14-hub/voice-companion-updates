import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from document_editor import VoiceDocument,Paragraph,START_COMMANDS,END_COMMANDS
from email_draft import VoiceEmail
from field_selection import FieldSelection
from dictation_text import clean_dictation

class DocumentControlTests(unittest.TestCase):
    def test_navigation_aliases_and_writing_position(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('First.'),Paragraph('Last words.')]
            for phrase in START_COMMANDS:
                self.assertIn('Beginning',doc.process(phrase));self.assertEqual(doc.cursor,0)
            doc.append_text('Opening.');self.assertEqual(doc.paragraphs[0].text,'Opening. First.')
            for phrase in END_COMMANDS:
                self.assertIn('End',doc.process(phrase));self.assertEqual(doc.cursor,1)
                self.assertEqual(doc.word_cursor,1)
            doc.append_text('Closing.');self.assertEqual(doc.paragraphs[1].text,'Last words. Closing.')

    def test_controls_in_every_input_mode(self):
        import companion
        with tempfile.TemporaryDirectory() as folder:
            for input_mode in ('mixed','dictation','commands'):
                doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('Body text')]
                with patch.object(companion,'document',doc),patch.object(companion,'INPUT_MODE',input_mode),patch.object(companion,'speak'):
                    companion.handle('Name document File '+input_mode,'document')
                    self.assertEqual(doc.path.name,'File '+input_mode+'.docx');self.assertTrue(doc.path.exists())
                    companion.handle('go to beginning of document','document');companion.handle('new line','document')
                    self.assertEqual(doc.paragraphs[0].text,'\nBody text')
                    companion.handle('select all','document');companion.handle('scratch that','document')
                    self.assertEqual(doc.paragraphs[0].text,'')
                    companion.handle('undo','document');self.assertEqual(doc.paragraphs[0].text,'\nBody text')

    def test_naming_save_as_and_reopening_actual_file(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.append_text('A separate title in the body.')
            old=doc.path;self.assertIn('Letter.docx',doc.process('name document Letter'))
            self.assertFalse(old.exists());self.assertEqual(doc.paragraphs[0].text,'A separate title in the body.')
            renamed=doc.path;doc.process('undo');self.assertTrue(old.exists());self.assertFalse(renamed.exists())
            doc.process("name the document Will's Letter")
            original=doc.path
            for phrase in ('save as Copy one','save document as Copy two.docx'):
                self.assertIn('Saved document as',doc.process(phrase));self.assertTrue(original.exists());self.assertTrue(doc.path.exists())
                reopened=VoiceDocument.open_existing(Path(folder),doc.title)
                self.assertEqual(reopened.paragraphs[0].text,'A separate title in the body.')
            before=doc.path;self.assertIn('already exists',doc.process("save as Will's Letter"));self.assertEqual(doc.path,before)
            self.assertIn('reserves',doc.process('name document CON'))
            self.assertIn('cannot use',doc.process('name document Bad/name'))
            doc.process('name document A\x0bReal\tName');self.assertEqual(doc.path.name,'A Real Name.docx')
            self.assertEqual(doc.paragraphs[0].text,'A separate title in the body.')

    def test_newline_literal_inline_and_table_cell(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.append_text('First new line Second new line')
            self.assertEqual(doc.paragraphs[0].text,'First\nSecond\n')
            doc.process('type new line');self.assertTrue(doc.paragraphs[0].text.endswith('new line'))
            doc.process('type literally name document Body Heading');self.assertIn('name document Body Heading',doc.paragraphs[0].text)
            title=doc.title;doc.process('create table with two rows and two columns');doc.process('new line')
            self.assertEqual(doc.table_cell().text,'\n');self.assertEqual(doc.title,title)
            doc.process('type new line');self.assertEqual(doc.table_cell().text,'\nnew line')

    def test_scratch_selection_and_unselected_recent_dictation(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('One two.'),Paragraph('Last sentence.')]
            for command in ('select first character','select first word','select first sentence','select first line','select first paragraph','select all'):
                before=[p.text for p in doc.paragraphs];doc.process(command);selected=doc.selected_text();self.assertTrue(selected)
                self.assertIn('Deleted:',doc.process('scratch'));doc.process('undo');self.assertEqual([p.text for p in doc.paragraphs],before)
            doc.process('create table with two rows and two columns');doc.append_text('Cell one');doc.process('next cell');doc.append_text('Cell two')
            before=[p.text for p in doc.paragraphs];doc.process('select all');self.assertIn('Deleted:',doc.process('scratch that'))
            self.assertTrue(all(not p.text for p in doc.paragraphs));doc.process('undo');self.assertEqual([p.text for p in doc.paragraphs],before)
            doc.selection=None;doc.append_text('Recent addition');self.assertIn('Undid',doc.process('scratch that'))

    def test_notes_email_fields_and_email_body_do_not_dictate_commands(self):
        import companion
        with tempfile.TemporaryDirectory() as folder:
            draft=VoiceEmail(Path(folder),compose_step='body');draft.paragraphs=[Paragraph('Body')]
            with patch.object(companion,'INPUT_MODE','dictation'),patch.object(companion,'email_draft',draft),patch.object(companion,'APP_WINDOW',None),patch.object(companion,'speak'):
                companion.handle('new line','email_draft');self.assertEqual(draft.paragraphs[0].text,'Body\n')
                companion.handle('select all','email_draft');companion.handle('scratch that','email_draft');self.assertEqual(draft.paragraphs[0].text,'')
                draft.compose_step='recipient';draft.recipient='test@example.com'
                companion.handle('new line','email_draft');self.assertEqual(draft.recipient,'test@example.com')
                companion.handle('select all','email_draft');companion.handle('scratch','email_draft');self.assertEqual(draft.recipient,'')
            with patch.object(companion,'INPUT_MODE','dictation'),patch.object(companion,'note_buffer',['Text']),patch.object(companion,'note_selection',FieldSelection()),patch.object(companion,'speak'):
                companion.handle('new line','note');self.assertIn('\n',' '.join(companion.note_buffer))
                companion.handle('select all','note');companion.handle('scratch that','note');self.assertEqual(companion.note_buffer,[''])

    def test_newline_replaces_selection_with_one_undo(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('One two')];doc.process('select first word');doc.process('new line')
            self.assertEqual(doc.paragraphs[0].text,'\n two');doc.process('undo');self.assertEqual(doc.paragraphs[0].text,'One two')

    def test_name_save_failure_preserves_title_and_body(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.append_text('Body');old=doc.path
            with patch.object(doc,'save',side_effect=OSError('locked')):self.assertIn('could not save',doc.process('save as New'))
            self.assertEqual(doc.path,old);self.assertTrue(old.exists());self.assertEqual(doc.paragraphs[0].text,'Body')

    def test_newline_across_table_selection_preserves_cells_and_single_undo(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.process('create table with two rows and two columns')
            doc.append_text('First');doc.process('next cell');doc.append_text('Second');before=[p.text for p in doc.paragraphs]
            doc.process('select all');doc.process('new line');self.assertEqual(len(doc.paragraphs),4)
            doc.process('undo');self.assertEqual([p.text for p in doc.paragraphs],before)

    def test_web_and_notes_literal_line_break_commands(self):
        import companion
        from unittest.mock import Mock
        with patch.object(companion,'note_buffer',[]),patch.object(companion,'speak'),patch.object(companion,'INPUT_MODE','dictation'):
            companion.handle('type new line','note');self.assertEqual(companion.note_buffer,['new line'])
        web=Mock()
        with patch.object(companion,'web_session',web),patch.object(companion,'speak'),patch.object(companion,'INPUT_MODE','dictation'):
            companion.handle('new line','web');web.insert_line_break.assert_called_once()
            companion.handle('type new line','web');web.dictate_to_focus.assert_called_once_with('new line',literal=True)

    def test_window_scratch_updates_saved_recipient_instead_of_retaining_old_address(self):
        import companion
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            draft=VoiceEmail(Path(folder),recipient='old@example.com',compose_step='recipient')
            window=Mock();window.select_email_text.return_value='Deleted selected text.';window.email_selection_edit=('recipient','')
            with patch.object(companion,'email_draft',draft),patch.object(companion,'APP_WINDOW',window),patch.object(companion,'speak'):
                companion.handle('scratch that','email_draft')
                self.assertEqual(draft.recipient,'')
                self.assertEqual(VoiceEmail.open_existing(Path(folder),draft.title).recipient,'')

class WholeTextReplacementTests(unittest.TestCase):
    def test_document_and_email_browse_all_matches_confirm_cancel_undo(self):
        for kind in (VoiceDocument,VoiceEmail):
            with tempfile.TemporaryDirectory() as folder:
                doc=kind(Path(folder));doc.paragraphs=[Paragraph('First cat sleeps. Another cat plays.'),Paragraph('Last cat waits.')]
                if kind is VoiceEmail: doc.compose_step='body';doc.automatic_dictation=True
                doc.cursor=1
                result=doc.process('replace cat with dog')
                self.assertIn('1 of 3',result);self.assertIn('First cat sleeps',result)
                self.assertNotIn('Another cat plays.',result)
                self.assertIn('3 of 3',doc.process('previous'))
                self.assertIn('Last cat waits.',doc.process('read sentence'))
                doc.process('next');doc.process('next');doc.process('that one')
                self.assertEqual([p.text for p in doc.paragraphs],['First cat sleeps. Another dog plays.','Last cat waits.'])
                doc.process('undo');self.assertIn('Another cat plays.',doc.paragraphs[0].text)
                doc.process('replace cat with dog');doc.process('cancel replacement')
                self.assertFalse(doc.replacement_candidates);self.assertNotIn('dog',doc.paragraphs[0].text)
                doc.process('replace Last with Final');self.assertEqual(doc.paragraphs[1].text,'Final cat waits.')
                self.assertIn('could not find',doc.process('replace absent with present'))

    def test_more_than_twenty_matches_and_whole_word_case(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('Cat concatenate cat.') for _ in range(15)]
            self.assertIn('1 of 30',doc.process('replace cat with dog'))
            doc.process('match 30');self.assertEqual(doc.paragraphs[-1].text,'Cat concatenate dog.')

    def test_app_routing_all_modes_document_and_email(self):
        import companion
        for input_mode in ('mixed','commands','dictation'):
            for kind,mode in ((VoiceDocument,'document'),(VoiceEmail,'email_draft')):
                with tempfile.TemporaryDirectory() as folder:
                    doc=kind(Path(folder));doc.paragraphs=[Paragraph('First cat.'),Paragraph('Last cat.')]
                    if kind is VoiceEmail: doc.compose_step='body'
                    with patch.object(companion,'document',doc),patch.object(companion,'email_draft',doc),patch.object(companion,'INPUT_MODE',input_mode),patch.object(companion,'speak'):
                        companion.handle('replace cat with dog',mode)
                        companion.handle('next',mode);companion.handle('confirm that',mode)
                        self.assertEqual([p.text for p in doc.paragraphs],['First cat.','Last dog.'])
