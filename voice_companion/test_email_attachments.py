"""Email attachments: spoken sizes, safe saving, Yahoo parts, drafts that carry files.

The Yahoo tests run the real yahoo_mailbox code through the real imaplib against
the local stand-in server from test_yahoo_mail, taught here to answer
BODYSTRUCTURE and section fetches the way an IMAP server does (RFC 3501). Every
message is built with Python's email package, so the parts are real MIME.
"""
import base64
import os
import re
import tempfile
import unittest
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import imap_structure
import mail_attachments as files
from mail_attachments import AttachmentError
from mailbox_access import MailboxError
from test_yahoo_mail import ADDRESS, APP_PASSWORD, YahooIMAPServer, YahooSMTPServer
from yahoo_mailbox import YahooMailbox

PDF = b'%PDF-1.4\n' + bytes(range(256)) * 9000          # about 2.3 megabytes, every byte value
PHOTO = b'\xff\xd8\xff\xe0JFIF' + os.urandom(300 * 1024)


def mime_message(attachments=(), inline_image=False, html=True, body='Here are the files.', subject='Files'):
    msg = EmailMessage()
    msg['From'] = 'Sam Lee <sam@example.com>'
    msg['To'] = ADDRESS
    msg['Subject'] = subject
    msg['Date'] = 'Mon, 05 Oct 2026 10:00:00 +0000'
    msg['Message-ID'] = '<files-' + subject.replace(' ', '') + '@example.com>'
    msg.set_content(body)
    if html:
        msg.add_alternative('<html><body><p>' + body + '</p><img src="cid:logo"></body></html>', subtype='html')
        if inline_image:
            # A picture that is part of the message's look: no file name.
            msg.get_payload()[1].add_related(b'\x89PNG logo', maintype='image', subtype='png', cid='<logo>')
    for name, data, maintype, subtype in attachments:
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return msg.as_bytes()


def _q(value):
    if value is None:
        return 'NIL'
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def _params(pairs):
    if not pairs:
        return 'NIL'
    return '(' + ' '.join(_q(k) + ' ' + _q(v) for k, v in pairs) + ')'


def _raw_params(part, header):
    """Parameters as a server sends them: RFC 2231 names stay encoded."""
    found = []
    for key, value in (part.get_params(header=header, unquote=True) or [])[1:]:
        if isinstance(value, tuple):
            charset, _, text = value
            found.append((key + '*', (charset or 'utf-8') + "''" + quote(text.encode('latin-1'), safe='')))
        else:
            found.append((key, value))
    return found


def _crlf(data):
    return re.sub(rb'\r?\n', b'\r\n', data)


def bodystructure(part):
    """The BODYSTRUCTURE an IMAP server sends for a parsed message (RFC 3501, 7.4.2)."""
    maintype, subtype = part.get_content_maintype(), part.get_content_subtype()
    if part.is_multipart() and maintype == 'multipart':
        children = ''.join(bodystructure(child) for child in part.get_payload())
        return '(' + children + ' ' + _q(subtype) + ' ' + _params(_raw_params(part, 'content-type')) + ' NIL NIL NIL)'
    raw = _crlf(section_bytes(part))
    fields = ' '.join([_q(maintype), _q(subtype), _params(_raw_params(part, 'content-type')),
                       _q(part.get('Content-ID')), 'NIL', _q(part.get('Content-Transfer-Encoding', '7bit')), str(len(raw))])
    if maintype == 'text':
        fields += ' ' + str(raw.count(b'\n'))
    elif (maintype, subtype) == ('message', 'rfc822'):
        inner = part.get_payload()[0]
        fields += ' (NIL NIL NIL NIL NIL NIL NIL NIL NIL NIL) ' + bodystructure(inner) + ' ' + str(raw.count(b'\n'))
    disposition = part.get_content_disposition()
    dsp = 'NIL' if not disposition else '(' + _q(disposition) + ' ' + _params(_raw_params(part, 'content-disposition')) + ')'
    return '(' + fields + ' NIL ' + dsp + ' NIL NIL)'


def section_bytes(part):
    if part.get_content_type() == 'message/rfc822':
        return part.get_payload()[0].as_bytes()
    payload = part.get_payload(decode=False)
    return payload.encode('latin-1', 'replace') if isinstance(payload, str) else bytes(payload)


def leaf(msg, number):
    part = msg
    for index in number.split('.'):
        if not part.is_multipart() or part.get_content_type() == 'message/rfc822':
            if index != '1':
                return None
            continue
        children = part.get_payload()
        if int(index) > len(children):
            return None
        part = children[int(index) - 1]
    return part


class StructureServer(YahooIMAPServer):
    """The Yahoo stand-in, plus BODYSTRUCTURE, BODY.PEEK[HEADER] and BODY.PEEK[1.2]<0.100>."""

    def __init__(self, *args, literal_structure=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.literal_structure = literal_structure

    def _fetch(self, box, seq, item, items, readonly):
        wanted = items.upper()
        if item.get('parsed_raw') is not item['raw']:
            item['parsed'], item['parsed_raw'] = BytesParser(policy=policy.compat32).parsebytes(item['raw']), item['raw']
        parsed = item['parsed']
        head = '* ' + str(seq) + ' FETCH (UID ' + str(item['uid']) + ' '
        if 'BODYSTRUCTURE' in wanted:
            text = bodystructure(parsed)
            if self.literal_structure:
                # Send the first file name as a literal, as servers do for 8-bit names.
                match = re.search(r'"(?:filename|name)\*?" "([^"]+)"', text)
                if match:
                    value = match[1].encode('utf-8')
                    before, after = text[:match.start(1) - 1], text[match.end(1) + 1:]
                    return [(head + 'BODYSTRUCTURE ' + before + '{' + str(len(value)) + '}\r\n').encode('latin-1')
                            + value + (after + ')\r\n').encode('latin-1')]
            return [head + 'BODYSTRUCTURE ' + text + ')\r\n']
        section = re.search(r'BODY\.PEEK\[([0-9.]+|HEADER)\](?:<(\d+)\.(\d+)>)?', wanted)
        if not section:
            return super()._fetch(box, seq, item, items, readonly)
        name = section[1]
        if name == 'HEADER':
            data = item['raw'].split(b'\n\n', 1)[0] + b'\n\n'
        else:
            part = leaf(parsed, name)
            if part is None:
                return [head.rstrip() + ')\r\n']
            data = _crlf(section_bytes(part))
        label = 'BODY[' + name + ']'
        if section[2] is not None:
            start, length = int(section[2]), int(section[3])
            data = data[start:start + length]
            label += '<' + section[2] + '>'
        self.section_fetches = getattr(self, 'section_fetches', 0) + 1
        return [(head + label + ' {' + str(len(data)) + '}\r\n').encode('latin-1') + data + b')\r\n']


class SpokenWordsTests(unittest.TestCase):
    def test_sizes_in_plain_words(self):
        self.assertEqual(files.size_words(0), '0 bytes')
        self.assertEqual(files.size_words(1), '1 byte')
        self.assertEqual(files.size_words(300 * 1024), '300 kilobytes')
        self.assertEqual(files.size_words(1024), '1 kilobyte')
        self.assertEqual(files.size_words(int(1.2 * 1024 * 1024)), '1.2 megabytes')
        self.assertEqual(files.size_words(25 * 1024 * 1024), '25 megabytes')
        self.assertEqual(files.size_words(None), 'size unknown')

    def test_summary_names_and_sizes(self):
        found = [{'name': 'report.pdf', 'size': int(1.2 * 1024 * 1024)}, {'name': 'photo.jpg', 'size': 300 * 1024}]
        self.assertEqual(files.summary(found), '2 attachments: report.pdf, 1.2 megabytes; photo.jpg, 300 kilobytes.')
        self.assertEqual(files.summary(found[:1]), '1 attachment: report.pdf, 1.2 megabytes.')
        many = [{'name': 'f%d.txt' % i, 'size': 10} for i in range(8)]
        self.assertTrue(files.summary(many).endswith('f4.txt, 10 bytes; and 3 more.'))
        self.assertEqual(files.label(found[1], 1, 2), 'photo.jpg, 300 kilobytes, 2 of 2.')

    def test_names_cannot_leave_the_folder_or_break_windows(self):
        self.assertEqual(files.safe_filename('..\\..\\Startup\\evil.exe'), 'evil.exe')
        self.assertEqual(files.safe_filename('a/b/c.txt'), 'c.txt')
        self.assertEqual(files.safe_filename('what?.pdf'), 'what_.pdf')
        self.assertEqual(files.safe_filename('CON.txt'), '_CON.txt')
        self.assertEqual(files.safe_filename('  . '), 'attachment')
        self.assertTrue(files.safe_filename('x' * 300 + '.pdf').endswith('.pdf'))
        self.assertLessEqual(len(files.safe_filename('x' * 300 + '.pdf')), 150)

    def test_kinds(self):
        self.assertEqual(files.kind('report.pdf'), 'document')
        self.assertEqual(files.kind('song.MP3'), 'media')
        self.assertEqual(files.kind('invoice.pdf.exe'), 'program')
        self.assertEqual(files.kind('script.js'), 'program')
        self.assertEqual(files.kind('photo.jpg'), 'other')

    def test_limit_warning_counts_email_encoding(self):
        self.assertEqual(files.limit_warning(10 * files.MB), '')
        self.assertIn('may be too large to send', files.limit_warning(20 * files.MB))


class SavingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def test_never_overwrites_and_counts_up(self):
        (self.folder / 'report.pdf').write_bytes(b'mine')
        first = files.save_stream(self.folder, 'report.pdf', [b'new'])
        second = files.save_stream(self.folder, 'report.pdf', [b'newer'])
        self.assertEqual((first.name, second.name), ('report (1).pdf', 'report (2).pdf'))
        self.assertEqual((self.folder / 'report.pdf').read_bytes(), b'mine')
        self.assertEqual(second.read_bytes(), b'newer')
        self.assertEqual(files.save_stream(self.folder, 'README', [b'x']).name, 'README')
        self.assertEqual(files.save_stream(self.folder, 'README', [b'x']).name, 'README (1)')

    def test_empty_and_large_files(self):
        self.assertEqual(files.save_stream(self.folder, 'empty.txt', []).read_bytes(), b'')
        pieces = [bytes([i]) * files.MB for i in range(30)]
        seen = []
        with patch('mail_attachments.time.monotonic', side_effect=range(0, 1000, 2)):
            path = files.save_stream(self.folder, 'big.bin', iter(pieces), progress=seen.append)
        self.assertEqual(path.stat().st_size, 30 * files.MB)
        self.assertTrue(seen and seen[-1] <= 30 * files.MB)

    def test_failure_or_stop_leaves_nothing_behind(self):
        def broken():
            yield b'part'
            raise MailboxError('This attachment is damaged. It could not be saved.')
        with self.assertRaises(MailboxError):
            files.save_stream(self.folder, 'broken.pdf', broken())
        calls = []
        def stop():
            calls.append(1)
            if len(calls) > 1:
                raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            files.save_stream(self.folder, 'stopped.pdf', [b'a', b'b'], stop=stop)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_missing_folder_is_said(self):
        with self.assertRaises(AttachmentError) as raised:
            files.save_stream(self.folder / 'gone', 'a.txt', [b'x'])
        self.assertIn('not available', str(raised.exception))

    def test_a_name_taken_at_the_last_moment_is_not_replaced(self):
        real_link = os.link
        def race(source, target):
            if Path(target).name == 'late.txt' and not Path(target).exists():
                Path(target).write_bytes(b'someone else')
            return real_link(source, target)
        with patch('mail_attachments.os.link', race):
            path = files.save_stream(self.folder, 'late.txt', [b'mine'])
        self.assertEqual(path.name, 'late (1).txt')
        self.assertEqual((self.folder / 'late.txt').read_bytes(), b'someone else')

    def test_local_attachments_refuse_folders_and_missing_files(self):
        with self.assertRaises(AttachmentError) as raised:
            files.local_attachment(self.folder)
        self.assertIn('is a folder. Choose files, not folders.', str(raised.exception))
        with self.assertRaises(AttachmentError) as raised:
            files.local_attachment(self.folder / 'nothing.txt')
        self.assertIn('no longer there', str(raised.exception))


class StructureTests(unittest.TestCase):
    def structure_of(self, raw):
        parsed = BytesParser(policy=policy.compat32).parsebytes(raw)
        reply = [('* 1 FETCH (UID 5 BODYSTRUCTURE ' + bodystructure(parsed) + ')').encode()]
        return imap_structure.find_structure(reply)

    def test_files_are_listed_but_inline_pictures_and_text_are_not(self):
        raw = mime_message([('report.pdf', PDF, 'application', 'pdf'), ('photo.jpg', PHOTO, 'image', 'jpeg')],
                           inline_image=True)
        found = imap_structure.attachments(self.structure_of(raw))
        self.assertEqual([(a['name'], a['type']) for a in found], [('report.pdf', 'application/pdf'), ('photo.jpg', 'image/jpeg')])
        self.assertLess(abs(found[0]['size'] - len(PDF)), 100)
        plain, html = imap_structure.text_parts(self.structure_of(raw))
        self.assertEqual((plain.number, html.number), ('1.1', '1.2.1'))

    def test_encoded_names_are_read(self):
        raw = mime_message([('résumé ünïcode.pdf', b'x' * 10, 'application', 'pdf')])
        self.assertEqual(imap_structure.attachments(self.structure_of(raw))[0]['name'], 'résumé ünïcode.pdf')
        reply = [b'* 1 FETCH (BODYSTRUCTURE ("application" "pdf" ("name" "=?UTF-8?B?w6l0w6kucGRm?=") NIL NIL "base64" 8 NIL NIL NIL NIL))']
        self.assertEqual(imap_structure.attachments(imap_structure.find_structure(reply))[0]['name'], 'été.pdf')
        reply = [b'* 1 FETCH (BODYSTRUCTURE ("application" "pdf" NIL NIL NIL "base64" 8 NIL ("attachment" ("filename*0" "long " "filename*1" "name.pdf")) NIL NIL))']
        self.assertEqual(imap_structure.attachments(imap_structure.find_structure(reply))[0]['name'], 'long name.pdf')

    def test_attached_message_and_text_file(self):
        inner = EmailMessage(); inner['Subject'] = 'Old'; inner.set_content('Old words.')
        msg = EmailMessage(); msg['Subject'] = 'With message'; msg.set_content('See below.')
        msg.add_attachment(inner)
        msg.add_attachment('Plain notes.', filename='notes.txt')
        found = imap_structure.attachments(self.structure_of(msg.as_bytes()))
        self.assertEqual([a['name'] for a in found], ['Attached message.eml', 'notes.txt'])

    def test_damaged_replies_and_base64(self):
        with self.assertRaises(imap_structure.StructureError):
            imap_structure.find_structure([b'* 1 FETCH (UID 5 BODYSTRUCTURE ("text" "plain"'])
        with self.assertRaises(imap_structure.StructureError):
            imap_structure.decode(b'not!base64', 'base64')
        stream = imap_structure.Base64Stream()
        data = base64.encodebytes(PHOTO)
        out = b''.join(stream.feed(data[i:i + 1000]) for i in range(0, len(data), 1000)) + stream.finish()
        self.assertEqual(out, PHOTO)


class YahooAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.server = StructureServer(); self.addCleanup(self.server.close)

    def mailbox(self):
        return YahooMailbox(ADDRESS, APP_PASSWORD, self.server.factory)

    def test_reading_says_the_files_and_saving_gives_the_same_bytes(self):
        uid = self.server.add('Inbox', mime_message([('report.pdf', PDF, 'application', 'pdf'),
                                                     ('photo.jpg', PHOTO, 'image', 'jpeg')], inline_image=True))
        body = self.mailbox().read_message(str(uid), 'Inbox')
        self.assertIn('Here are the files.', body)
        self.assertEqual([a['name'] for a in body.attachments], ['report.pdf', 'photo.jpg'])
        for item, original in zip(body.attachments, (PDF, PHOTO)):
            path = files.save_stream(self.folder, item['name'], self.mailbox().attachment_chunks(str(uid), item, 'Inbox'))
            self.assertEqual(path.read_bytes(), original, item['name'])
        self.assertGreater(self.server.section_fetches, 3, 'a large file comes in pieces')
        self.assertEqual(self.mailbox().attachments(str(uid), 'Inbox'), body.attachments)

    def test_a_message_without_files(self):
        uid = self.server.add('Inbox', mime_message())
        self.assertEqual(self.mailbox().read_message(str(uid), 'Inbox').attachments, [])

    def test_a_large_message_is_read_without_downloading_its_files(self):
        big = os.urandom(6 * 1024 * 1024)
        uid = self.server.add('Inbox', mime_message([('video.mp4', big, 'video', 'mp4')], body='Watch this.'))
        body = self.mailbox().read_message(str(uid), 'Inbox')
        self.assertIn('Watch this.', body)
        self.assertEqual(body.attachments[0]['name'], 'video.mp4')
        context = self.mailbox().response_context(str(uid), 'forward', ADDRESS, 'Inbox')
        self.assertEqual(context['original_attachments'][0]['name'], 'video.mp4')
        self.assertIn('Watch this.', context['original_body'])
        self.assertEqual(self.mailbox().attachment_bytes(str(uid), body.attachments[0], 'Inbox'), big)

    def test_literal_file_names(self):
        self.server.literal_structure = True
        uid = self.server.add('Inbox', mime_message([('report.pdf', b'%PDF small', 'application', 'pdf')]))
        self.assertEqual(self.mailbox().attachments(str(uid), 'Inbox')[0]['name'], 'report.pdf')

    def test_damaged_attachment_gives_a_clear_message_and_no_file(self):
        msg = EmailMessage(); msg['Subject'] = 'Broken'; msg.set_content('Hi')
        msg.add_attachment(b'data', maintype='application', subtype='pdf', filename='broken.pdf')
        raw = msg.as_bytes().replace(base64.b64encode(b'data'), b'ZGF0!!==')
        uid = self.server.add('Inbox', raw)
        item = self.mailbox().attachments(str(uid), 'Inbox')[0]
        with self.assertRaises(MailboxError) as raised:
            files.save_stream(self.folder, item['name'], self.mailbox().attachment_chunks(str(uid), item, 'Inbox'))
        self.assertEqual(str(raised.exception), 'This attachment is damaged. It could not be saved.')
        self.assertEqual(list(self.folder.iterdir()), [])


class Draft:
    def __init__(self, attachments=(), context=None):
        from document_editor import Paragraph
        self.recipient, self.subject, self.cc, self.bcc = 'friend@example.com', 'Files', [], []
        self.paragraphs = [Paragraph(text='Here you go.')]
        self.response_context = context or {}
        self.attachments = list(attachments)

    def original_attachments(self):
        from email_draft import VoiceEmail
        return VoiceEmail.original_attachments(self)


class YahooSendingAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.server = StructureServer(); self.addCleanup(self.server.close)
        self.smtp = YahooSMTPServer(); self.addCleanup(self.smtp.close)

    def send(self, draft):
        from email_delivery import yahoo_submit
        box = YahooMailbox(ADDRESS, APP_PASSWORD, self.server.factory)
        context = draft.response_context
        return yahoo_submit(draft, ADDRESS, APP_PASSWORD, self.smtp.factory, sent_folder=box.save_sent_copy,
                            originals=lambda item: box.attachment_bytes(context['source_id'], item, context.get('source_folder')))

    def attached(self, name, data):
        path = self.folder / name
        path.write_bytes(data)
        return files.local_attachment(path)

    def test_files_go_as_multipart_and_parse_back(self):
        self.send(Draft([self.attached('report.pdf', PDF), self.attached('empty.txt', b'')]))
        sent = BytesParser(policy=policy.default).parsebytes(self.smtp.received[0][1])
        self.assertEqual(sent.get_content_type(), 'multipart/mixed')
        self.assertIn('Here you go.', sent.get_body(preferencelist=('plain',)).get_content())
        found = {part.get_filename(): part.get_content() for part in sent.iter_attachments()}
        self.assertEqual(found['report.pdf'], PDF)
        self.assertEqual(found['empty.txt'], '' if isinstance(found['empty.txt'], str) else b'')
        self.assertEqual(len(self.server.folders['Sent'].messages), 1)

    def test_a_missing_file_stops_the_send_plainly(self):
        from email_delivery import DeliveryError
        item = self.attached('gone.pdf', b'x')
        Path(item['path']).unlink()
        with self.assertRaises(DeliveryError) as raised:
            self.send(Draft([item]))
        self.assertFalse(raised.exception.uncertain)
        self.assertIn('gone.pdf is no longer in its folder, so this email was not sent.', str(raised.exception))
        self.assertEqual(self.smtp.received, [])

    def test_forward_carries_the_original_files_less_the_dropped_one(self):
        uid = self.server.add('Inbox', mime_message([('report.pdf', PDF, 'application', 'pdf'),
                                                     ('photo.jpg', PHOTO, 'image', 'jpeg')]))
        context = YahooMailbox(ADDRESS, APP_PASSWORD, self.server.factory).response_context(str(uid), 'forward', ADDRESS, 'Inbox')
        context['dropped_attachments'] = ['photo.jpg']
        self.send(Draft([self.attached('mine.txt', b'mine')], context))
        sent = BytesParser(policy=policy.default).parsebytes(self.smtp.received[0][1])
        found = {part.get_filename(): part.get_content() for part in sent.iter_attachments()}
        self.assertEqual(sorted(found), ['mine.txt', 'report.pdf'])
        self.assertEqual(found['report.pdf'], PDF)
        self.assertIn('Forwarded message', sent.get_body(preferencelist=('plain',)).get_content())

    def test_too_large_for_yahoo_is_said(self):
        from email_delivery import DeliveryError
        self.smtp.data_reply = '552 Message size exceeds fixed maximum message size'
        with self.assertRaises(DeliveryError) as raised:
            self.send(Draft([self.attached('a.txt', b'x')]))
        self.assertIn('too large', str(raised.exception))
        self.assertFalse(raised.exception.uncertain)


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def draft(self):
        from email_draft import VoiceEmail
        from document_editor import Paragraph
        draft = VoiceEmail(self.folder / 'Email Drafts', title='Trip', recipient='sam@example.com', subject='Photos',
                           compose_step='body')
        draft.paragraphs = [Paragraph('Pictures from the trip.')]
        return draft

    def test_eml_keeps_the_files_and_the_draft_reopens_with_them(self):
        from email_draft import VoiceEmail
        photo = self.folder / 'photo.jpg'; photo.write_bytes(PHOTO)
        notes = self.folder / 'notes.txt'; notes.write_text('Notes', encoding='utf-8')
        draft = self.draft()
        said = draft.attach([photo, notes])
        self.assertEqual(said, 'Attached photo.jpg and notes.txt. 2 attachments.')
        saved = BytesParser(policy=policy.default).parsebytes(draft.path.read_bytes())
        found = {part.get_filename(): part.get_content() for part in saved.iter_attachments()}
        self.assertEqual(found['photo.jpg'], PHOTO)
        self.assertIn('Pictures from the trip.', saved.get_body(preferencelist=('plain',)).get_content())
        again = VoiceEmail.open_existing(self.folder / 'Email Drafts', 'Trip')
        self.assertEqual([a['name'] for a in again.attachments], ['photo.jpg', 'notes.txt'])
        self.assertEqual(again.attachments[0]['path'], str(photo))
        # A file gone later: the draft still opens and keeps its place, the .eml just leaves it out.
        notes.unlink()
        again.save()
        self.assertEqual(len(VoiceEmail.open_existing(self.folder / 'Email Drafts', 'Trip').attachments), 2)

    def test_attaching_twice_folders_and_too_much(self):
        photo = self.folder / 'photo.jpg'; photo.write_bytes(b'x')
        draft = self.draft()
        draft.attach([photo])
        with self.assertRaises(AttachmentError) as raised:
            draft.attach([photo])
        self.assertIn('already attached', str(raised.exception))
        with self.assertRaises(AttachmentError):
            draft.attach([self.folder])
        big = self.folder / 'big.bin'
        with open(big, 'wb') as handle:
            handle.truncate(51 * files.MB)
        with self.assertRaises(AttachmentError) as raised:
            draft.attach([big])
        self.assertIn('more than email can carry', str(raised.exception))
        self.assertEqual(len(draft.attachments), 1)

    def test_attachments_change_the_send_fingerprint_only_when_present(self):
        draft = self.draft()
        before = draft.send_hash()
        draft.attachments = []
        self.assertEqual(draft.send_hash(), before)
        photo = self.folder / 'photo.jpg'; photo.write_bytes(b'x')
        draft.attach([photo])
        self.assertNotEqual(draft.send_hash(), before)

    def test_damaged_attachment_data_is_refused(self):
        import json
        from email_draft import VoiceEmail
        draft = self.draft(); draft.save()
        state = json.loads(draft.state_path.read_text(encoding='utf-8'))
        state['attachments'] = [{'path': 5, 'name': 'x', 'size': 1}]
        draft.state_path.write_text(json.dumps(state), encoding='utf-8')
        with self.assertRaises(ValueError):
            VoiceEmail.open_existing(self.folder / 'Email Drafts', 'Trip')

    def test_forward_originals_can_be_taken_off(self):
        draft = self.draft()
        draft.response_context = {'action': 'forward', 'original_attachments': [{'id': '2', 'name': 'a.pdf', 'size': 3},
                                                                                {'id': '3', 'name': 'b.jpg', 'size': 4}]}
        draft.drop_original('a.pdf')
        self.assertEqual([a['name'] for a in draft.all_attachments()], ['b.jpg'])
        self.assertTrue(draft.all_attachments()[0]['original'])


if __name__ == '__main__':
    unittest.main()
