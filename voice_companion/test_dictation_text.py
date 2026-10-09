import tempfile
import unittest
from pathlib import Path

from dictation_text import clean_dictation
from document_editor import VoiceDocument
from email_draft import VoiceEmail


class DictationTextTests(unittest.TestCase):
    def test_spoken_period_and_final_filler(self):
        self.assertEqual(clean_dictation('Same here, Bob, period. Uh\r'), 'Same here, Bob.')
        self.assertEqual(clean_dictation('Um, hello comma Bob period'), 'hello, Bob.')
        self.assertEqual(clean_dictation('Are you there question mark'), 'Are you there?')

    def test_ordinary_words_and_literal_escape(self):
        self.assertEqual(clean_dictation('The period of history was long.'),
                         'The period of history was long.')
        self.assertEqual(clean_dictation('I heard the word comma in class.'),
                         'I heard the word comma in class.')
        with tempfile.TemporaryDirectory() as folder:
            email = VoiceEmail(Path(folder))
            email.process('Same here, Bob, period. Uh')
            self.assertEqual(email.paragraphs[0].text, 'Same here, Bob.')
            email.process('type literally uh period')
            self.assertEqual(email.paragraphs[0].text, 'Same here, Bob. uh period')
            document = VoiceDocument(Path(folder))
            document.dictating = True
            document.process('Hello period. Um')
            self.assertEqual(document.paragraphs[0].text, 'Hello.')


if __name__ == '__main__': unittest.main()
