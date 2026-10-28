"""On the main menu, only asking for a document makes a new one (0.2.172).

Any phrase with the word "document" used to start a new document there, so
"Close document.", "Beginning of document." or "Save document as" asked for a
new document's name."""
import companion
from test_core_email_fixes import CompanionTestCase


class MainMenuDocumentWordsTests(CompanionTestCase):
    def test_asking_for_a_document_makes_a_new_one(self):
        for phrase in ('Create a document.', 'new document', 'Write a document', 'Document.', 'Start a new document.',
                       'Make a Word document', 'I want to write a document.'):
            with self.subTest(phrase=phrase):
                self.said.clear()
                self.assertEqual(companion.handle(phrase, 'awake'), 'document_name')
                self.assertEqual(self.said[-1], 'Please say or type the name for your document, then press Enter or say confirm.')

    def test_other_document_words_say_no_document_is_open(self):
        for phrase in ('Close document.', 'leave document', 'exit document', 'Beginning of document.', 'go to end of document',
                       'top of document', 'save document as', 'rename document', 'name document'):
            with self.subTest(phrase=phrase):
                self.said.clear()
                self.assertEqual(companion.handle(phrase, 'awake'), 'awake')
                self.assertTrue(self.said[-1].startswith('No document is open.'), self.said)
