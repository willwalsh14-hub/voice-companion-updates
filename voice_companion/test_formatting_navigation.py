import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from document_editor import VoiceDocument,Paragraph,TextRun
from email_draft import VoiceEmail

class FormattingNavigationTests(unittest.TestCase):
    def test_movement_clears_selection_and_heading_formats_only_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            for phrase in ('move to end of document','move to the end of the document','jump to end of doc','go to end of document','end of document','move to beginning of document','move to start of the document'):
                doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('First'),Paragraph('Last')]
                doc.process('select all');self.assertIsNotNone(doc.selection)
                self.assertNotIn('not recognize',doc.process(phrase));self.assertIsNone(doc.selection)
                doc.process('heading level two');target=0 if 'beginning' in phrase or 'start' in phrase else 1
                self.assertEqual([p.kind for p in doc.paragraphs],['Heading 2' if i==target else 'Normal' for i in range(2)])
                reopened=VoiceDocument.open_existing(Path(folder),doc.title);self.assertEqual([p.kind for p in reopened.paragraphs],[p.kind for p in doc.paragraphs])

    def test_granular_movement_and_pending_matches_clear_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            for phrase in ('next word','move to next word','next sentence','next line','next paragraph','previous paragraph','next heading'):
                doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('First line.\nSecond line.'),Paragraph('Heading',kind='Heading 1')]
                doc.process('select all');doc.selection_candidates=[doc.selection];doc.selection_action='select'
                doc.process(phrase);self.assertIsNone(doc.selection);self.assertFalse(doc.selection_candidates)
                doc.process('align right');self.assertEqual(sum(p.alignment=='right' for p in doc.paragraphs),1)
            doc=VoiceDocument(Path(folder));doc.process('create table with two rows and two columns');doc.process('select all');doc.process('next cell');self.assertIsNone(doc.selection)
            doc.process('heading level two');self.assertEqual(sum(p.kind=='Heading 2' for p in doc.paragraphs),1)

    def test_current_units_follow_navigation_and_do_not_search_literals(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('First line.\nLast word.')]
            doc.process('move to end of document')
            for phrase,expected in [('select line','Last word.'),('select word','Last'),('select paragraph','First line.\nLast word.')]:
                # Reset movement before each current-unit selection.
                doc.process('move to end of document')
                doc.process(phrase)
                self.assertEqual(doc.selected_text(),'word.' if phrase=='select word' else expected)
            doc.process('move to beginning of document');doc.process('next line');self.assertEqual(doc.cursor,0)
            doc.process('select line');self.assertEqual(doc.selected_text(),'Last word.')
            doc.process('select character');self.assertEqual(doc.selected_text(),'L')

    def test_say_font_full_summary_and_individual_reports(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('Text',kind='Heading 2',bold=True,font='Arial',size=18,alignment='center',line_spacing_twips=360)]
            result=doc.process('say font')
            for part in ('Arial','18 points','Heading 2','Bold','center','1.50 line spacing'):self.assertIn(part,result)
            for phrase,expected in [('say font name','Font Arial.'),('say size','Font size 18 points.'),('say font size','Font size 18 points.'),('say alignment','Alignment center.'),('say line spacing','1.50 line spacing.'),('say heading','Style Heading 2.'),('say bold','Bold.')]:self.assertEqual(doc.process(phrase),expected)
            doc.paragraphs=[Paragraph('AB',runs=[TextRun('A',font='Arial',size=14),TextRun('B',font='Calibri',size=18)])]
            doc.process('select all');self.assertIn('Font mixed',doc.process('say font'));self.assertIn('Font size mixed',doc.process('say size'))
            doc.process('move to end of document');self.assertEqual(doc.process('say font name'),'Font Calibri.')

    def test_combined_font_size_and_short_size_commands_preserve_body(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('Body words')]
            for phrase,font,size in [('set font Arial 14','Arial',14),('set font Times New Roman size 16','Times New Roman',16),('set font name Calibri and font size eighteen','Calibri',18),('set font Arial twenty four','Arial',24)]:
                self.assertIn('Formatted',doc.process(phrase));self.assertEqual((doc.paragraphs[0].font,doc.paragraphs[0].size),(font,size));self.assertEqual(doc.paragraphs[0].text,'Body words')
            for phrase,size in [('set size 14',14),('set size eighteen',18),('set font size to 20 points',20)]:doc.process(phrase);self.assertEqual(doc.paragraphs[0].size,size)
            before=(doc.paragraphs[0].font,doc.paragraphs[0].size)
            self.assertIn('6 to 72',doc.process('set font Arial 100'));self.assertEqual((doc.paragraphs[0].font,doc.paragraphs[0].size),before)

    def test_selected_combined_font_change_and_single_undo(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('First second')];doc.process('select first word');doc.process('set font Arial 18')
            self.assertEqual([(r.text,r.font,r.size) for r in doc.paragraphs[0].runs],[('First','Arial',18),(' second','',0)])
            doc.process('undo');self.assertEqual(doc.paragraphs[0].text,'First second');self.assertEqual(doc.paragraphs[0].font,'')

    def test_document_and_email_commands_route_in_every_input_mode(self):
        import companion
        with tempfile.TemporaryDirectory() as folder:
            for input_mode in ('mixed','dictation','commands'):
                for mode,attribute,editor in [('document','document',VoiceDocument(Path(folder))),('email_draft','email_draft',VoiceEmail(Path(folder),compose_step='body'))]:
                    editor.paragraphs=[Paragraph('First'),Paragraph('Last')]
                    with patch.object(companion,attribute,editor),patch.object(companion,'INPUT_MODE',input_mode),patch.object(companion,'speak') as speech,patch.object(companion,'APP_WINDOW',None):
                        companion.handle('select all',mode);companion.handle('move to end of document',mode);companion.handle('heading level two',mode)
                        self.assertEqual([p.kind for p in editor.paragraphs],['Normal','Heading 2'])
                        companion.handle('set font Arial 18',mode);companion.handle('say font',mode)
                        self.assertIn('Arial',speech.call_args.args[0]);self.assertIn('18 points',speech.call_args.args[0]);self.assertEqual([p.text for p in editor.paragraphs],['First','Last'])
                        companion.handle('select paragraph',mode);self.assertEqual(editor.selected_text(),'Last')

    def test_singular_point_combined_and_separate_commands_in_all_modes(self):
        import companion
        with tempfile.TemporaryDirectory() as folder:
            for input_mode in ('mixed','dictation','commands'):
                for mode,attribute,editor in [('document','document',VoiceDocument(Path(folder))),('email_draft','email_draft',VoiceEmail(Path(folder),compose_step='body'))]:
                    editor.paragraphs=[Paragraph('Original body')]
                    with patch.object(companion,attribute,editor),patch.object(companion,'INPUT_MODE',input_mode),patch.object(companion,'speak'),patch.object(companion,'APP_WINDOW',None):
                        companion.handle('set font Times New Roman 16 point',mode)
                        self.assertEqual((editor.paragraphs[0].font,editor.paragraphs[0].size),('Times New Roman',16))
                        for phrase,size in [('set size 14 point',14),('set font size eighteen point',18),('make it 16 point',16),('make it fourteen points',14)]:
                            companion.handle(phrase,mode);self.assertEqual(editor.paragraphs[0].size,size);self.assertEqual(editor.paragraphs[0].font,'Times New Roman')
                        companion.handle('set font Arial',mode);self.assertEqual(editor.paragraphs[0].font,'Arial');self.assertEqual(editor.paragraphs[0].size,14)
                        self.assertEqual(editor.paragraphs[0].text,'Original body')
