"""Spoken text selection and editing checks for documents and mail drafts."""
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from document_editor import VoiceDocument
from email_draft import VoiceEmail


class SelectionTests(unittest.TestCase):
    def test_named_formatting_and_ambiguity(self):
        with tempfile.TemporaryDirectory() as folder:
            doc = VoiceDocument(Path(folder))
            doc.append_text('The cat chased the dog. The cat slept.')
            self.assertIn('2 matches', doc.process('select cat'))
            self.assertIn('Selected: cat', doc.process('second match'))
            self.assertIn('cat', doc.process('what is selected'))
            self.assertIn('Bold applied', doc.process('bold that'))
            self.assertIn('2 possible ranges', doc.process('highlight cat through slept'))
            self.assertIn('Highlight applied', doc.process('second match'))
            self.assertIn('Underline applied', doc.process('underline dog'))
            self.assertIn('Italicize applied', doc.process('italicize chased'))
            with ZipFile(doc.path) as z:
                xml = z.read('word/document.xml')
            self.assertIn(b'<w:highlight w:val="yellow"/>', xml)
            self.assertIn(b'<w:u w:val="single"/>', xml)
            restored = VoiceDocument.open_existing(Path(folder), doc.title)
            self.assertEqual(restored.paragraphs[0].text, 'The cat chased the dog. The cat slept.')
            self.assertGreater(len(restored.paragraphs[0].runs), 1)

    def test_find_delete_phrase_range_copy_cut_paste_and_undo(self):
        with tempfile.TemporaryDirectory() as folder:
            doc = VoiceDocument(Path(folder))
            doc.append_text('Same here, Bob. The cat saw the dog.')
            self.assertIn('Found and selected: Same here, Bob', doc.process('find same here Bob'))
            self.assertIn('Copied', doc.process('copy selection'))
            self.assertIn('Deleted', doc.process('delete same here Bob'))
            self.assertEqual(doc.paragraphs[0].text, '. The cat saw the dog.')
            self.assertIn('Undid', doc.process('undo'))
            self.assertEqual(doc.paragraphs[0].text, 'Same here, Bob. The cat saw the dog.')
            self.assertIn('Selected: cat saw the dog', doc.process('select cat through dog'))
            self.assertIn('Cut', doc.process('cut that'))
            self.assertEqual(doc.paragraphs[0].text, 'Same here, Bob. The .')
            self.assertIn('Pasted', doc.process('paste'))
            self.assertTrue(doc.paragraphs[0].text.endswith('cat saw the dog'))

    def test_cross_paragraph_range_delete_current_and_scratch(self):
        with tempfile.TemporaryDirectory() as folder:
            doc = VoiceDocument(Path(folder))
            doc.append_text('A cat starts here')
            doc.process('new paragraph')
            doc.append_text('and a dog ends here')
            self.assertIn('cat starts here\nand a dog', doc.process('select cat through dog'))
            self.assertIn('Deleted', doc.process('delete that'))
            self.assertEqual([p.text for p in doc.paragraphs], ['A  ends here'])
            doc.process('undo')
            self.assertEqual([p.text for p in doc.paragraphs], ['A cat starts here', 'and a dog ends here'])
            doc.append_text('More dictation.')
            self.assertIn('Undid', doc.process('scratch that'))
            self.assertEqual(doc.paragraphs[1].text, 'and a dog ends here')
            self.assertIn('no recent', doc.process('scratch that'))
            doc.cursor = 1
            self.assertIn('Deleted', doc.process('delete line'))
            self.assertEqual(doc.paragraphs[1].text, '')

    def test_email_draft_preserves_selected_formatting(self):
        with tempfile.TemporaryDirectory() as folder:
            draft = VoiceEmail(Path(folder))
            draft.append_text('Please read this sentence.')
            draft.process('bold read this')
            draft.process('highlight sentence')
            restored = VoiceEmail.open_existing(Path(folder), draft.title)
            self.assertEqual(restored.paragraphs[0].text, 'Please read this sentence.')
            self.assertGreater(len(restored.paragraphs[0].runs), 1)
            self.assertIn(b'<mark>', restored.path.read_bytes())


if __name__ == '__main__':
    unittest.main()

class MatchBrowsingAndPunctuationTests(unittest.TestCase):
    def test_replacement_does_not_import_endpoint_punctuation(self):
        from document_editor import Paragraph
        for kind in (VoiceDocument,VoiceEmail):
            with tempfile.TemporaryDirectory() as folder:
                doc=kind(Path(folder));doc.paragraphs=[Paragraph('A cat runs, then cat sleeps.')]
                if kind is VoiceEmail: doc.compose_step='body'
                doc.process('Replace cat with dog.');doc.process('that one')
                self.assertEqual(doc.paragraphs[0].text,'A dog runs, then cat sleeps.')
                doc.process('replace cat with fox!');self.assertEqual(doc.paragraphs[0].text,'A dog runs, then fox sleeps.')
                doc.word_cursor=1;doc.process('Replace word with wolf.')
                self.assertEqual(doc.paragraphs[0].text,'A wolf runs, then fox sleeps.')
                doc.process('replace fox with bear period.');self.assertIn('bear. sleeps.',doc.paragraphs[0].text)
                doc.process('undo');self.assertIn('fox sleeps.',doc.paragraphs[0].text)
                doc.word_cursor=5;doc.process('replace word with rests,')
                self.assertEqual(doc.paragraphs[0].text,'A wolf runs, then fox rests.')

    def test_selection_and_insert_browse_context_and_preserve_text_until_confirmed(self):
        from document_editor import Paragraph
        import companion
        from unittest.mock import patch
        for kind,mode in ((VoiceDocument,'document'),(VoiceEmail,'email_draft')):
            for input_mode in ('mixed','commands','dictation'):
                with tempfile.TemporaryDirectory() as folder:
                    doc=kind(Path(folder));doc.paragraphs=[Paragraph('First red fox runs.'),Paragraph('Last red fox sleeps.')]
                    if kind is VoiceEmail: doc.compose_step='body'
                    with patch.object(companion,'document',doc),patch.object(companion,'email_draft',doc),patch.object(companion,'INPUT_MODE',input_mode),patch.object(companion,'speak') as speak:
                        companion.handle('select red fox',mode);self.assertIn('1 of 2',speak.call_args.args[0])
                        companion.handle('previous',mode);self.assertIn('Last red fox sleeps.',speak.call_args.args[0])
                        companion.handle('confirm that',mode);self.assertEqual(doc.selection.first_paragraph,1)
                        for direction in ('before','after'):
                            companion.handle('insert '+direction+' red fox',mode)
                            companion.handle('next',mode);companion.handle('okay',mode)
                            self.assertIsNone(doc.selection);self.assertEqual(doc.insertion_position,(1,5 if direction=='before' else 12))
                            doc.append_text('quickly')
                            self.assertEqual(doc.paragraphs[1].text,'Last quickly red fox sleeps.' if direction=='before' else 'Last red fox quickly sleeps.')
                            doc.process('undo')
                        companion.handle('insert before red fox',mode);companion.handle('cancel selection',mode)
                        self.assertFalse(doc.selection_candidates)
                        self.assertEqual(doc.paragraphs[1].text,'Last red fox sleeps.')

    def test_range_browsing_and_more_than_twenty_matches(self):
        from document_editor import Paragraph
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('cat sleeps.') for _ in range(25)]
            self.assertIn('1 of 25',doc.process('select cat'));doc.process('previous');doc.process('ok');self.assertEqual(doc.selection.first_paragraph,24)
            self.assertIn('possible ranges',doc.process('select cat through sleeps'))
            doc.process('next');self.assertIn('2 of 25',doc.process('read context'));doc.process('that one');self.assertEqual(doc.selected_text(),'cat sleeps')
