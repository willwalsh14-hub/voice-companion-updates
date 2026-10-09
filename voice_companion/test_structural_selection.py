import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from document_editor import VoiceDocument, Paragraph
from email_draft import VoiceEmail

class StructuralSelectionTests(unittest.TestCase):
    def test_paragraph_navigation_selects_whole_paragraphs_and_updates_position(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder))
            doc.paragraphs=[Paragraph('First words.'),Paragraph('Second words.'),Paragraph('Third words.')]
            doc.cursor=2
            for command,index in [('select first paragraph',0),('select next paragraph',1),
                                  ('select previous paragraph',0),('select last paragraph',2),
                                  ('select paragraph two',1),('select the third paragraph',2),
                                  ('select paragraph 1',0),('select current paragraph',0)]:
                self.assertIn('Selected:',doc.process(command))
                self.assertEqual(doc.cursor,index)
                self.assertEqual(doc.selected_text(),doc.paragraphs[index].text)
            before=doc.selection
            self.assertIn('no previous',doc.process('select previous paragraph'))
            self.assertEqual(doc.selection,before)
            self.assertIn('not available',doc.process('select paragraph 99'))

    def test_select_all_preempts_phrase_ambiguity_and_supports_editing(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder))
            doc.paragraphs=[Paragraph('all cat cat'),Paragraph('Second paragraph')]
            doc.process('select cat')
            self.assertTrue(doc.selection_candidates)
            self.assertIn('Selected all text',doc.process('Select all.'))
            self.assertFalse(doc.selection_candidates)
            self.assertEqual(doc.selected_text(),'all cat cat\nSecond paragraph')
            doc.process('bold that')
            self.assertTrue(all(run.bold for p in doc.paragraphs for run in p.runs))
            doc.process('delete selection')
            self.assertEqual('\n'.join(p.text for p in doc.paragraphs),'')
            doc.process('undo')
            self.assertEqual('\n'.join(p.text for p in doc.paragraphs),'all cat cat\nSecond paragraph')

    def test_literal_all_is_still_selectable_with_word_prefix(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('We all agree.')]
            self.assertIn('Selected: all',doc.process('select the word all'))
            self.assertEqual(doc.selected_text(),'all')

    def test_empty_and_aliases(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder))
            self.assertIn('empty',doc.process('select all'))
            doc.paragraphs=[Paragraph('Text')]
            for alias in ('select everything','select all text','select the entire document'):
                self.assertIn('Selected all text',doc.process(alias))
            self.assertIn('no next',doc.process('select next paragraph'))

    def test_structural_selection_in_dictation_only_mode_does_not_insert_command(self):
        import companion
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('Original text')]
            with patch.object(companion,'document',doc),patch.object(companion,'INPUT_MODE','dictation'),patch.object(companion,'speak'):
                self.assertEqual(companion.handle('select first paragraph','document'),'document')
                self.assertEqual(doc.selected_text(),'Original text')
                companion.handle('select all','document')
                self.assertEqual(doc.paragraphs[0].text,'Original text')

    def test_same_editor_selection_in_email_body(self):
        with tempfile.TemporaryDirectory() as folder:
            draft=VoiceEmail(Path(folder),compose_step='body')
            draft.paragraphs=[Paragraph('Body one'),Paragraph('Body two')]
            self.assertIn('Selected:',draft.process('select first paragraph'))
            self.assertEqual(draft.selected_text(),'Body one')

    def test_all_structural_units_and_relative_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            for unit, first, second, last in [('character','O','n','!'),('word','One.','Two!','Three!'),('sentence','One.','Two!','Three!'),('line','One. Two!\n','Three!','Three!')]:
                doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('One. Two!\nThree!')]
                for command, expected in [(f'select first {unit}',first),(f'select next {unit}',second),(f'select previous {unit}',first),(f'select {unit} two',second),(f'select last {unit}',last)]:
                    self.assertIn('Selected:',doc.process(command))
                    self.assertEqual(doc.selected_text(),expected)
                self.assertIn('There is no',doc.process(f'select next {unit}'))
                self.assertIn('There is no',doc.process(f'select {unit} 999'))
