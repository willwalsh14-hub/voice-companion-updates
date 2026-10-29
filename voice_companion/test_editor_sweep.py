"""Word processor, Notepad, reader and File manager: bugs a new user met (sweep, October 29, 2026)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import companion as app
from document_editor import Paragraph, VoiceDocument
from test_document_save_as import DocumentFlowCase


class FailedSaveLeavesNoStrayFile(DocumentFlowCase):
    def test_a_locked_file_leaves_no_extra_document_in_the_list(self):
        made = VoiceDocument(app.documents_folder(), title='Letter')
        made.paragraphs = [Paragraph('Dear Bob.')]; made.save()
        with patch.object(Path, 'replace', side_effect=PermissionError('open in Word')):
            with self.assertRaises(OSError):
                made.save()
        names = sorted(p.name for p in app.documents_folder().glob('*.docx'))
        self.assertEqual(names, ['Letter.docx'])
        app.handle('list documents', 'word_processor')
        self.assertIn('1 of 1', self.last())


if __name__ == '__main__':
    unittest.main()
