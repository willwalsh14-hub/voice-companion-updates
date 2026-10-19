"""Protect plain language, current instructions, and accessible guide navigation."""
from pathlib import Path
import re
import tempfile
import unittest
from docx import Document
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from guide_text import read_guide
from documentation_quality import report
from onboard_help import HelpSession
import build_user_guides as guides
from unittest.mock import patch

ROOT=Path(__file__).parent


class DocumentationQualityTests(unittest.TestCase):
    def test_full_outline_plain_instructions_and_separate_step_lists(self):
        text=(ROOT/'START HERE - Veteran.txt').read_text()
        self.assertNotRegex(text,r'\b(?:Voice|Keyboard):')
        _,_,chapters=read_guide(ROOT/'START HERE - Veteran.txt')
        contents=text.split('Contents\n',1)[1].split('1. GETTING STARTED',1)[0]
        for chapter in chapters:
            for part in chapter['parts']:self.assertIn(part['heading'],contents)
        main=chapters[0]['parts'][2]['paragraphs']
        self.assertTrue(all('- '+name in main for name in ('Word processor','Email','Web browser','Media','Help')))
        for chapter in chapters:
            for part in chapter['parts']:
                expected=1
                for value in part['paragraphs']:
                    step=guides.STEP.fullmatch(value)
                    if step:
                        self.assertEqual(int(step[1]),expected,part['heading']);expected+=1
                    else:expected=1

    def test_reading_level_stays_below_eighth_grade_for_every_part(self):
        result=report(ROOT)
        self.assertLessEqual(result['manual_estimated_grade'],8)
        self.assertLessEqual(result['release_notes_estimated_grade'],8)
        self.assertEqual(result['over_target'],[])
        self.assertGreaterEqual(len(result['manual_parts']),90)
        self.assertGreaterEqual(len(result['release_note_parts']),1)
        self.assertGreaterEqual(len(result['setup_parts']),40)

    def test_current_tasks_and_meaningful_limits_are_explained(self):
        text=(ROOT/'START HERE - Veteran.txt').read_text()
        for command in ('Cancel send','Review and submit form','Cancel field editing','Save as My Letter Copy',
                        'Resume where you left off','Rename favorite','Delete favorite','Auto download 5 episodes',
                        'Cancel selected scheduled recording','Normal mode','What is selected','Undo','Go to folder'):
            self.assertIn(command,text)
        for explanation in ('Nothing is sent before the review and confirmation','Gmail and Yahoo forwarding do not include',
                            'Google Calendar and Google Drive are not connected','Keep Companion running and the computer awake',
                            'Some podcast hosts do not allow downloads','The same selection and editing rules'):
            self.assertIn(explanation,text)
        self.assertNotRegex(text,r'(?i)regression|checksum|runtime|dispatch|stale caret|virtual cursor|source build|provider registration')
        self.assertNotRegex(text,r'Version 0\.2\.(?:5[0-9]|6[0-9]|7[0-9])')

    def test_each_help_part_contains_its_complete_instructions(self):
        _,_,chapters=read_guide(ROOT/'START HERE - Veteran.txt')
        help=HelpSession(ROOT/'START HERE - Veteran.txt')
        for chapter in chapters:
            help.topic(chapter['name'])
            self.assertEqual([name for name,_ in help._subtopics()],[p['name'] for p in chapter['parts']])
            for index,part in enumerate(chapter['parts']):
                help.subtopic_index=index;help.process('that one')
                self.assertEqual(help.reading.text,'\n\n'.join(part['paragraphs']))
                help.process('back to subsections')

    def test_heading_links_steps_and_all_text_survive_the_format_builders(self):
        title,blocks=guides.sections()
        with tempfile.TemporaryDirectory() as directory,patch.object(guides,'ROOT',Path(directory)):
            guides.build_html(title,blocks);guides.build_word(title,blocks)
            guides.build_epub(title,blocks);guides.build_daisy(title,blocks);guides.validate(blocks)
            doc=Document(Path(directory)/(guides.STEM+'.docx'))
            self.assertEqual(doc.paragraphs[0].style.name,'Title')
            self.assertEqual(len(doc.tables),0)
            self.assertFalse(doc.styles.element.xpath('.//w:pBdr'))
            raw=' '.join(p.text for p in doc.paragraphs)
            for _,paragraphs in blocks:
                for value in paragraphs:
                    text=re.sub(r'^\d+\. ','',value) if guides.STEP.fullmatch(value) else value[2:] if value.startswith('- ') else value
                    self.assertIn(text,raw)
            with ZipFile(Path(directory)/(guides.STEM+'.epub')) as archive:
                content=ET.fromstring(archive.read('OEBPS/content.xhtml'))
                ids={e.get('id') for e in content.iter() if e.get('id')}
                nav=ET.fromstring(archive.read('OEBPS/nav.xhtml'))
                self.assertTrue(all(e.get('href').split('#')[1] in ids for e in nav.iter() if e.tag.endswith('a')))

if __name__=='__main__':unittest.main()
