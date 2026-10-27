"""Email attachments fully by voice, like Contacts and Calendar.

- In a message: "what's attached", "open the PDF", "save report to Documents", "save all to Desktop".
- In the Attachments list: "next attachment", "open report", "how big is it",
  "read attachment details", "remove photo", Control+I and Control+D.
- In a draft: "attach my resume from Documents", a short list when several match,
  "attach the last download", "remove photo". Writing comes first: sentences that only
  sound like these stay text.
- Names match loosely: "report dot PDF", "the photo", "resume final" for "Résumé (final).pdf".
- Bugs found in the audit, each with the test that shows it.
"""
import email.generator
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import attachment_screen
import companion as app
import downloads
import mail_attachments as files
from document_editor import Paragraph
from email_draft import VoiceEmail
from test_attachment_flows import PHOTO, REPORT, AttachmentMailbox, FlowTestCase


class PlacesTestCase(FlowTestCase):
    """The Flow setup, plus Documents, Desktop, Downloads and Pictures as real folders."""

    def setUp(self):
        super().setUp()
        self.places = {}
        for key, (_, name) in attachment_screen.PLACES.items():
            folder = self.root / 'home' / name
            folder.mkdir(parents=True)
            self.places[key] = folder
        self.stack.enter_context(patch('attachment_screen.place', side_effect=lambda key: self.places[key]))
        self.stack.enter_context(patch.object(downloads, '_MANAGER', None))

    def opened_message(self):
        mode = self.inbox()
        app.handle('open current message', mode)
        return mode


class NameMatchingTests(unittest.TestCase):
    def test_spoken_names_fit_loosely(self):
        names = ['report.pdf', 'photo.jpg', 'Résumé (final).docx', 'IMG_2041.HEIC', 'budget 2026.xlsx']
        self.assertEqual(files.name_matches(names, 'report dot PDF'), [0])
        self.assertEqual(files.name_matches(names, 'Report.pdf.'), [0])
        self.assertEqual(files.name_matches(names, 'the photo'), [1])
        self.assertEqual(files.name_matches(names, 'resume final'), [2])
        self.assertEqual(files.name_matches(names, 'my resume'), [2])
        self.assertEqual(files.name_matches(names, 'the spreadsheet'), [4])
        self.assertEqual(files.name_matches(names, 'IMG underscore 2041'), [3])
        # "The pictures" is a kind: both pictures fit, so the caller asks which.
        self.assertEqual(files.name_matches(names, 'the picture'), [1, 3])
        self.assertEqual(files.name_matches(names, 'budget'), [4])
        self.assertEqual(files.name_matches(names, 'banana'), [])
        self.assertEqual(files.name_matches(names, ''), [])
        # Without loose matching, a word in the middle of a name is not enough (used where people write).
        self.assertEqual(files.name_matches(names, 'final', loose=False), [])
        self.assertEqual(files.name_matches(names, 'final'), [2])

    def test_kinds_and_joined_names(self):
        self.assertEqual(files.kind_words('a.pdf'), 'PDF document')
        self.assertEqual(files.kind_words('a.JPG'), 'picture')
        self.assertEqual(files.kind_words('a.zip'), 'compressed folder')
        self.assertEqual(files.kind_words('setup.exe'), 'program')
        self.assertEqual(files.kind_words('data.xyz'), 'XYZ file')
        self.assertEqual(files.kind_words('README'), 'file')
        self.assertEqual(files.joined(['a']), 'a')
        self.assertEqual(files.joined(['a', 'b']), 'a and b')
        self.assertEqual(files.joined(['a', 'b', 'c']), 'a, b and c')
        self.assertEqual(files.joined([str(i) for i in range(8)]), '0, 1, 2, 3, 4 and 3 more')
        self.assertEqual(files.file_count(1), '1 file')


class MessageVoiceTests(PlacesTestCase):
    def test_whats_attached_and_attachments_open_the_list_with_its_count(self):
        mode = self.opened_message()
        self.assertEqual(app.handle("What's attached?", mode), 'attachments')
        self.assertEqual(self.last(), 'Attachments, 3 files. report.pdf, 10 kilobytes, 1 of 3.')
        self.press('Escape', 'attachments')
        self.assertEqual(app.handle('Attachments.', mode), 'attachments')
        self.assertEqual(self.last(), 'Attachments, 3 files. report.pdf, 10 kilobytes, 1 of 3.')

    def test_open_and_save_by_name_from_the_open_message(self):
        mode = self.opened_message()
        self.assertEqual(app.handle('Open the photo.', mode), 'mailbox')
        self.assertEqual(Path(self.started[-1]).read_bytes(), PHOTO)
        app.handle('Save report dot PDF to Documents.', mode)
        self.assertEqual(self.last(), 'Saved report.pdf in your Documents folder.')
        self.assertEqual((self.places['documents'] / 'report.pdf').read_bytes(), REPORT)
        app.handle('save all to Desktop', mode)
        self.assertEqual(self.last(), 'Saved 3 attachments in your Desktop folder: report.pdf, photo.jpg, setup.exe.')
        app.handle('How big are the attachments?', mode)
        self.assertIn('3 attachments: report.pdf, 10 kilobytes; photo.jpg, 14 bytes; setup.exe, 10 bytes. Together they are 10 kilobytes.',
                      self.last())
        # Commands of the message itself are left alone.
        with patch.object(app.mail_session, 'process', return_value='Message 1 of 2.') as process:
            app.handle('open current message', mode)
        process.assert_called()

    def test_a_folder_that_cannot_be_written_says_so_plainly(self):
        mode = self.press('L', self.opened_message())
        with patch('tempfile.mkstemp', side_effect=PermissionError(13, 'Access is denied')):
            app.handle('save it to Desktop', mode)
        self.assertEqual(self.last(), 'Voice Companion cannot save files in Desktop. Choose another folder.')
        self.assertEqual(list(self.places['desktop'].iterdir()), [])

    def test_an_unavailable_downloads_folder_is_not_a_raw_error(self):
        # Before: "[Errno 13] Permission denied: ..." was spoken.
        mode = self.press('L', self.opened_message())
        with patch('downloads.default_folder', return_value=self.root / 'gone' / 'Downloads'), \
                patch.object(Path, 'mkdir', side_effect=PermissionError(13, 'Permission denied')):
            app.handle('save attachment', mode)
        self.assertEqual(self.last(), 'Your Downloads folder is not available. Say save it to, and a folder such as Documents.')


class ListVoiceTests(PlacesTestCase):
    def list_mode(self):
        return self.press('L', self.opened_message())

    def test_move_open_and_size_by_voice(self):
        mode = self.list_mode()
        app.handle('Next attachment.', mode)
        self.assertEqual(self.last(), 'photo.jpg, 14 bytes, 2 of 3.')
        app.handle('previous attachment', mode)
        self.assertEqual(self.last(), 'report.pdf, 10 kilobytes, 1 of 3.')
        app.handle('How big is it?', mode)
        self.assertEqual(self.last(), 'report.pdf is 10 kilobytes.')
        app.handle('read attachment details', mode)
        self.assertEqual(self.last(), 'report.pdf. PDF document, 10 kilobytes. Attachment 1 of 3.')
        app.handle('go to setup', mode)
        self.assertEqual(self.last(), 'setup.exe, 10 bytes, 3 of 3.')
        self.press('Ctrl+I', mode)
        self.assertEqual(self.last(), 'setup.exe. Program, 10 bytes. Attachment 3 of 3. Only open programs from people you trust.')
        self.assertEqual(app.handle('open the photo', mode), 'attachments')
        self.assertEqual(Path(self.started[-1]).name, 'photo.jpg')
        self.assertEqual(attachment_screen.INDEX, 1)
        app.handle('open the second one', mode)
        self.assertEqual(Path(self.started[-1]).read_bytes(), PHOTO)
        app.handle('open attachment 7', mode)
        self.assertEqual(self.last(), 'There are 3 attachments. photo.jpg, 14 bytes, 2 of 3.')
        app.handle('open banana', mode)
        self.assertEqual(self.last(), 'No attachment is called banana. The files are report.pdf, photo.jpg and setup.exe.')

    def test_save_by_voice_and_control_d(self):
        mode = self.list_mode()
        app.handle('Save it to Documents.', mode)
        self.assertEqual(self.last(), 'Saved report.pdf in your Documents folder.')
        app.handle('save the photo to my desktop folder', mode)
        self.assertEqual(self.last(), 'Saved photo.jpg in your Desktop folder.')
        self.assertEqual(attachment_screen.INDEX, 1)
        app.handle('save all to Pictures', mode)
        self.assertIn('Saved 3 attachments in your Pictures folder', self.last())
        self.press('Ctrl+D', mode)
        self.assertIn('to a folder. Type or say a folder', self.last())
        app.handle_keyboard('Music', mode)
        self.assertEqual(self.last(), 'Saved photo.jpg in your Music folder.')
        # A name with "to" in it is a name, not a folder.
        AttachmentMailbox.files['m1'].append(('1.5', 'letter to Sam.txt', b'Dear Sam'))
        self.addCleanup(AttachmentMailbox.files['m1'].pop)
        app.mail_session.attachments = None
        mode = self.press('L', self.press('Escape', mode))
        app.handle('save letter to Sam', mode)
        self.assertEqual(self.last(), 'Saved letter to Sam.txt in your Downloads folder.')

    def test_two_names_that_fit_ask_for_more(self):
        AttachmentMailbox.files['m4'] = [('2', 'scan page 1.pdf', b'1'), ('3', 'scan page 2.pdf', b'2')]
        self.addCleanup(AttachmentMailbox.files.pop, 'm4')
        mode = self.inbox()
        app.mail_session.rows.append({'id': 'm4', 'from': 'Al <al@example.com>', 'subject': 'Scans'})
        app.handle('read message 3', mode)
        mode = self.press('L', mode)
        app.handle('open scan', mode)
        self.assertEqual(self.last(), '2 attachments match scan: scan page 1.pdf and scan page 2.pdf. Say more of the name. '
                                      'scan page 1.pdf, 1 byte, 1 of 2.')
        self.assertEqual(self.started, [])


class DraftVoiceTests(PlacesTestCase):
    def write(self, folder, name, data=b'data', age=0):
        path = self.places[folder] / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if age:
            os.utime(path, (time.time() - age, time.time() - age))
        return path

    def test_attach_by_name_from_a_named_folder(self):
        draft = self.draft()
        self.write('documents', 'Quarterly report.pdf', b'x' * 2048)
        self.write('documents', 'notes.txt')
        self.assertEqual(app.handle('Attach report from Documents.', 'email_draft'), 'email_draft')
        self.assertEqual(self.last(), 'Attached Quarterly report.pdf. 1 attachment.')
        self.assertEqual([a['name'] for a in draft.attachments], ['Quarterly report.pdf'])
        app.handle('Attach notes dot txt from my documents folder', 'email_draft')
        self.assertEqual(self.last(), 'Attached notes.txt. 2 attachments.')
        app.handle('attach banana from Documents', 'email_draft')
        self.assertEqual(self.last(), 'No file called banana is in your Documents folder. Say attach a file to choose one in File manager.')

    def test_several_matches_offer_a_short_list_newest_first(self):
        draft = self.draft()
        self.write('documents', 'resume old.docx', age=3600)
        self.write('documents', 'Jobs/resume 2026.docx')
        self.write('documents', '.hidden/resume secret.docx')
        self.write('documents', 'resume shortcut.lnk')
        mode = app.handle('attach my resume from Documents', 'email_draft')
        self.assertEqual(mode, 'email_draft')
        self.assertTrue(self.last().startswith('2 files match resume. Choose one and press Enter. Files to attach list. '
                                               'resume 2026.docx, in Documents, Jobs, 4 bytes'), self.last())
        self.assertTrue(app.app_context('email_draft').get('named_list'))
        self.press('Down', mode)
        self.assertIn('resume old.docx, in Documents, 4 bytes', self.last())
        self.assertEqual(self.press('Enter', mode), 'email_draft')
        self.assertEqual(self.last(), 'Attached resume old.docx. 1 attachment.')
        self.assertEqual([a['name'] for a in draft.attachments], ['resume old.docx'])

    def test_attach_the_last_download(self):
        draft = self.draft()
        gone = self.places['downloads'] / 'gone.pdf'
        newest = self.write('downloads', 'form.pdf', b'form')
        data = self.root / 'data'
        (data / 'download-history.json').write_text(json.dumps([
            {'id': 'd3', 'source': 'edge', 'ref': '3', 'name': 'big.zip', 'path': str(self.places['downloads'] / 'big.zip'),
             'state': 'downloading', 'created': time.time()},
            {'id': 'd2', 'source': 'edge', 'ref': '2', 'name': 'gone.pdf', 'path': str(gone), 'state': 'finished',
             'created': time.time() - 10},
            {'id': 'd1', 'source': 'edge', 'ref': '1', 'name': 'form.pdf', 'path': str(newest), 'state': 'finished',
             'created': time.time() - 20}]), encoding='utf-8')
        self.assertEqual(app.handle('Attach the last download.', 'email_draft'), 'email_draft')
        self.assertEqual(self.last(), 'Attached form.pdf. 1 attachment.')
        self.assertEqual([a['name'] for a in draft.attachments], ['form.pdf'])

    def test_no_download_yet(self):
        self.draft()
        app.handle('attach the last download', 'email_draft')
        self.assertIn('There is no finished download to attach.', self.last())

    def test_list_size_and_remove_by_voice_in_the_draft(self):
        draft = self.draft()
        draft.attach([self.files / 'letter.txt', self.files / 'photo.jpg'])
        self.assertEqual(app.handle('Attachments', 'email_draft'), 'attachments')
        self.assertEqual(self.last(), 'Attachments, 2 files. letter.txt, 9 bytes, 1 of 2.')
        app.handle('read attachment details', 'attachments')
        self.assertEqual(self.last(), 'letter.txt. Text file, 9 bytes. Attachment 1 of 2. It is in your My files folder.')
        app.handle('how big are they', 'attachments')
        self.assertEqual(self.last(), '2 attachments. Together they are 23 bytes.')
        app.handle('remove the photo', 'attachments')
        self.assertEqual(self.last(), 'Removed photo.jpg. 1 attachment left. letter.txt, 9 bytes, 1 of 1.')
        self.press('Escape', 'attachments')
        # The guide's own example: the name as said, with "dot".
        self.assertEqual(app.handle('Remove attachment letter dot txt.', 'email_draft'), 'email_draft')
        self.assertEqual(self.last(), 'Removed letter.txt. No attachments left.')
        self.assertEqual(draft.attachments, [])

    def test_remove_attachment_report_pdf_as_the_guide_says(self):
        # Before: "Remove attachment report.pdf" was typed into the email instead.
        draft = self.draft()
        report = self.write('documents', 'report.pdf')
        draft.attach([report])
        self.assertEqual(app.handle('Remove attachment report.pdf.', 'email_draft'), 'email_draft')
        self.assertEqual(self.last(), 'Removed report.pdf. No attachments left.')
        self.assertEqual(' '.join(p.text for p in draft.paragraphs), 'See the files.')

    def test_writing_that_only_sounds_like_attaching_is_text(self):
        draft = self.draft()
        draft.attach([self.files / 'photo.jpg'])
        self.write('documents', 'trip.jpg')
        for sentence in ('Attach the photos from the trip.', 'Attach report tomorrow.', 'Remove the photo from the album.',
                         'Take the dog out.', 'Open the report when you can.'):
            app.handle(sentence, 'email_draft')
        text = ' '.join(p.text for p in draft.paragraphs)
        for sentence in ('Attach the photos from the trip.', 'Attach report tomorrow.', 'Remove the photo from the album.',
                         'Take the dog out.', 'Open the report when you can.'):
            self.assertIn(sentence, text)
        self.assertEqual([a['name'] for a in draft.attachments], ['photo.jpg'])

    def test_attach_without_a_folder_in_the_list_searches_the_usual_places(self):
        draft = self.draft()
        draft.attach([self.files / 'photo.jpg'])
        self.write('desktop', 'Benefits letter.pdf')
        mode = app.handle('list attachments', 'email_draft')
        self.assertEqual(app.handle('attach benefits letter', mode), 'attachments')
        self.assertEqual(self.last(), 'Attached Benefits letter.pdf. 2 attachments. Benefits letter.pdf, 4 bytes, 2 of 2.')
        app.handle('attach the last download', mode)
        self.assertIn('There is no finished download to attach.', self.last())

    def test_save_a_draft_file_to_a_named_folder_copies_it(self):
        draft = self.draft()
        draft.attach([self.files / 'letter.txt'])
        mode = app.handle('list attachments', 'email_draft')
        app.handle('save it', mode)
        self.assertEqual(self.last(), 'letter.txt is already on this computer, in your My files folder.')
        app.handle('save it to Desktop', mode)
        self.assertEqual(self.last(), 'Saved letter.txt in your Desktop folder.')
        self.assertEqual((self.places['desktop'] / 'letter.txt').read_text(encoding='utf-8'), 'Dear Sam.')

    def test_send_review_names_forwarded_and_added_files(self):
        mode = self.opened_message()
        app.handle('forward', mode)
        draft = app.email_draft
        draft.recipient, draft.compose_step = 'al@example.com', 'body'
        draft.attach([self.files / 'letter.txt'])
        with patch('companion.ProtectedStore') as store:
            store.return_value.load.return_value = {'address': 'me@example.com'}
            store.return_value.selected.return_value = ('thunderbird', 'me@example.com')
            app.handle('send it', 'email_draft')
        self.assertIn('With 4 attachments: report.pdf, photo.jpg, setup.exe, letter.txt.', self.last())


class ForwardDuplicateTests(PlacesTestCase):
    """Two forwarded files with the same name: only the one taken off is left out."""

    def setUp(self):
        super().setUp()
        AttachmentMailbox.files['m5'] = [('1.2', 'scan.pdf', b'first scan'), ('1.3', 'scan.pdf', b'second scan, longer'),
                                         ('1.4', 'notes.txt', b'n')]
        self.addCleanup(AttachmentMailbox.files.pop, 'm5')

    def forward(self):
        mode = self.inbox()
        app.mail_session.rows.append({'id': 'm5', 'from': 'Al <al@example.com>', 'subject': 'Scans'})
        app.handle('read message 3', mode)
        app.handle('forward', mode)
        return app.email_draft

    def test_the_chosen_one_of_two_same_names_is_dropped_and_stays_dropped(self):
        draft = self.forward()
        mode = app.handle('list attachments', 'email_draft')
        self.press('Down', mode)
        self.assertEqual(self.last(), 'scan.pdf, 19 bytes, 2 of 3.')
        self.press('Delete', mode)
        # Before: the first scan.pdf was left out, and the one taken off was sent.
        self.assertEqual([a['id'] for a in draft.original_attachments()], ['1.2', '1.4'])
        self.assertEqual([a['id'] for a in draft.dropped_originals()], ['1.3'])
        again = VoiceEmail.open_existing(draft.folder, draft.title)
        self.assertEqual([a['id'] for a in again.original_attachments()], ['1.2', '1.4'])

    def test_older_drafts_that_name_only_the_file_still_work(self):
        context = {'action': 'forward', 'original_attachments': [{'id': '1', 'name': 'a.pdf'}, {'id': '2', 'name': 'b.pdf'}],
                   'dropped_attachments': ['b.pdf']}
        from email_draft import original_split
        kept, dropped = original_split(context)
        self.assertEqual([a['id'] for a in kept], ['1'])
        self.assertEqual([a['id'] for a in dropped], ['2'])

    def test_thunderbird_is_told_which_part_to_leave_out(self):
        import thunderbird_mailbox
        draft = self.forward()
        mode = app.handle('list attachments', 'email_draft')
        self.press('Down', mode)
        self.press('Delete', mode)
        draft.recipient = 'al@example.com'
        draft.response_context['source_id'] = '5'    # Thunderbird message ids are numbers
        sent = {}

        class Bridge:
            def request(self, method, params, timeout=None, progress=None, every=None):
                if method == 'send':
                    sent.update(params)
                    return {'headerMessageId': 'x'}
                return {}
        with patch.object(thunderbird_mailbox, 'relay', return_value=Bridge()):
            thunderbird_mailbox.submit(draft, {'identity_id': 'id1', 'address': 'me@example.com'})
        self.assertEqual(sent['forward']['dropParts'], ['1.3'])
        self.assertEqual(sent['forward']['drop'], ['scan.pdf'])


class LargeFileDraftTests(unittest.TestCase):
    def test_saving_a_draft_does_not_encode_a_large_file_again(self):
        # Before: every sentence written in an email with a 25 megabyte file took seconds,
        # because the whole file was encoded again on each save.
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            big = folder / 'scan.pdf'
            big.write_bytes(os.urandom(3 * 1024 * 1024))
            draft = VoiceEmail(folder / 'Email Drafts', title='Scan', recipient='sam@example.com', subject='Scan')
            draft.paragraphs = [Paragraph('Here is the scan.')]
            draft.attach([big])
            written = []
            real = email.generator.BytesGenerator.write
            def counting(generator, text):
                written.append(len(text))
                return real(generator, text)
            with patch.object(email.generator.BytesGenerator, 'write', counting):
                draft.paragraphs.append(Paragraph('One more line.'))
                draft.save()
            self.assertLess(sum(written), 200 * 1024)
            from email import policy
            from email.parser import BytesParser
            saved = BytesParser(policy=policy.default).parsebytes(draft.path.read_bytes())
            parts = {part.get_filename(): part.get_content() for part in saved.iter_attachments()}
            self.assertEqual(parts['scan.pdf'], big.read_bytes())
            self.assertIn('One more line.', saved.get_body(preferencelist=('plain',)).get_content())


class HelpTests(ListVoiceTests.__bases__[0]):
    def test_f1_in_the_list_opens_the_attachments_part_of_the_guide(self):
        # Before: it opened the email chapter on "Read messages", 1 of 7.
        mode = self.press('L', self.opened_message())
        self.assertEqual(self.press('F1', mode), 'help')
        self.assertTrue(self.last().startswith('User guide. Open and save attachments. '), self.last())


if __name__ == '__main__':
    unittest.main()
