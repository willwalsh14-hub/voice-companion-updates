"""Sending through Yahoo or Thunderbird. Authentication and voice confirmation live elsewhere.

Never retry a send automatically: a timeout can happen after acceptance.
The local draft must remain available until a provider response is recorded.
"""
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from email_draft import normalize_recipient
from email_formatting import formatted_body


class DeliveryError(Exception):
    def __init__(self,message,uncertain=True):
        super().__init__(message);self.uncertain=uncertain


def message_parts(draft):
    if not str(draft.recipient or '').strip():
        # Said instead of "I could not understand one complete email address".
        raise ValueError('This email has no recipient. Say email to, followed by an address, then send it again.')
    recipient = normalize_recipient(draft.recipient)
    subject = draft.subject.strip()
    body = '\n\n'.join(p.text for p in draft.paragraphs).strip()
    if any(c in subject for c in '\r\n'):
        raise DeliveryError('The subject must be on one line.',uncertain=False)
    for address in draft.cc:
        normalize_recipient(address)
    for address in getattr(draft, 'bcc', []):
        normalize_recipient(address)
    return recipient, subject, body


def outgoing_body(draft, comment):
    context = draft.response_context
    # Without an original sender or text, an empty "Forwarded message" block is just noise.
    if context.get('action') == 'forward' and (context.get('original_from') or context.get('original_body')):
        return (comment + '\n\nForwarded message:\nFrom: ' + context.get('original_from', '') +
                '\nSubject: ' + context.get('original_subject', '') + '\n\n' + context.get('original_body', ''))
    return comment



class Accepted(str):
    """A provider's acceptance. note is extra spoken detail, such as a Sent copy that failed."""
    note = ''


def delivery_note(result):
    note = getattr(result, 'note', '')
    return note if isinstance(note, str) else ''


YAHOO_SIGN_IN_FAILED = ('Yahoo did not accept the saved app password. Ask your helper to make a new '
                        'Yahoo app password, then say add account.')


TOO_LARGE = ('Yahoo refused this email because it is too large. Remove an attachment with Control+L and Delete, '
             'then review the draft again.')


def add_attachments(msg, draft, originals=None):
    """Add the draft's files to a message being built: first the original's files on a
    forward (originals(item) gives their bytes), then files from this computer.
    Raises DeliveryError (certainly not sent) when a file is gone or cannot be read."""
    from mail_attachments import AttachmentError, mime_type, missing, missing_message, read_local
    files = getattr(draft, 'attachments', None)
    files = list(files) if isinstance(files, list) else []
    gone = missing(files)
    if gone:
        raise DeliveryError(missing_message(gone), uncertain=False)
    kept = draft.original_attachments() if callable(getattr(type(draft), 'original_attachments', None)) else []
    for item in kept:
        if originals is None:
            raise DeliveryError('The original attachments could not be read, so the email was not sent. Try again.', uncertain=False)
        try:
            data = originals(item)
        except Exception as exc:
            raise DeliveryError('The original attachments could not be read from Yahoo, so the email was not sent. '
                                'Check the internet connection, then review the draft again.', uncertain=False) from exc
        maintype, subtype = mime_type(item['name'])
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=item['name'])
    for item in files:
        try:
            data = read_local(item)
        except AttachmentError as exc:
            raise DeliveryError(str(exc), uncertain=False) from exc
        maintype, subtype = mime_type(item['name'])
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=item['name'])


def yahoo_submit(draft, address, app_password, smtp_factory=smtplib.SMTP_SSL, sent_folder=None, originals=None):
    """Send through smtp.mail.yahoo.com, then keep a copy in Yahoo's Sent folder
    when sent_folder (a function taking the raw message and its Message-ID) is given.
    originals(item), on a forward, gives the bytes of one of the original's files.

    Only a failure after the message data started can leave the result
    uncertain; a refused sign-in, address or message was certainly not sent."""
    recipient, subject, body = message_parts(draft)
    app_password = ''.join(str(app_password or '').split())
    if not app_password or not address:
        raise DeliveryError('Yahoo is not connected.')
    msg = EmailMessage()
    msg['From'] = normalize_recipient(address)
    msg['To'] = recipient
    if draft.cc: msg['Cc'] = ', '.join(draft.cc)
    if getattr(draft, 'bcc', []): msg['Bcc'] = ', '.join(draft.bcc)
    msg['Subject'] = subject
    # Yahoo's Sent copy needs these to sort and match.
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid(domain=normalize_recipient(address).rpartition('@')[2] or 'yahoo.com')
    context = draft.response_context
    if context.get('action') in ('reply', 'reply_all') and context.get('message_id'):
        msg['In-Reply-To'] = context['message_id']
        msg['References'] = (context.get('references', '') + ' ' + context['message_id']).strip()
    msg.set_content(outgoing_body(draft, body))
    msg.add_alternative(formatted_body(draft), subtype='html')
    add_attachments(msg, draft, originals)
    stage, smtp = 'connect', None
    try:
        smtp = smtp_factory('smtp.mail.yahoo.com', 465, context=ssl.create_default_context(), timeout=30)
        smtp.login(address, app_password)
        stage = 'send'
        refused = smtp.send_message(msg)
        stage = 'accepted'
    except smtplib.SMTPAuthenticationError as exc:
        raise DeliveryError(YAHOO_SIGN_IN_FAILED, uncertain=False) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise DeliveryError('Yahoo would not send to ' + ', '.join(exc.recipients) +
                            '. Check the address, then review the draft again.', uncertain=False) from exc
    except smtplib.SMTPSenderRefused as exc:
        if exc.smtp_code == 552:
            raise DeliveryError(TOO_LARGE, uncertain=False) from exc
        raise DeliveryError('Yahoo would not send from this account right now. Wait a few minutes, '
                            'then review the draft again.', uncertain=False) from exc
    except smtplib.SMTPDataError as exc:
        if exc.smtp_code == 552:
            raise DeliveryError(TOO_LARGE, uncertain=False) from exc
        raise DeliveryError('Yahoo refused this message. Wait a few minutes, then review the draft again. '
                            'If it happens again, ask your helper.', uncertain=False) from exc
    except (smtplib.SMTPException, OSError, TimeoutError) as exc:
        if stage == 'connect':
            raise DeliveryError('Voice Companion could not reach Yahoo Mail. Check the internet connection, '
                                'then review the draft again.', uncertain=False) from exc
        raise DeliveryError('The result is uncertain. Keep the draft and check with the recipient before trying again.') from exc
    finally:
        if smtp is not None:
            try:
                if stage == 'accepted': smtp.quit()
                else: smtp.close()
            except Exception:
                # Yahoo already accepted the message; a failed goodbye changes nothing.
                try: smtp.close()
                except Exception: pass
    result = Accepted('accepted by Yahoo SMTP')
    if refused:
        result.note += (' Yahoo did not accept ' + ', '.join(refused) +
                        ', so they did not get it. Everyone else did.')
    if sent_folder is not None:
        try:
            sent_folder(msg.as_bytes(), str(msg['Message-ID']))
        except Exception:
            # The message was sent; only the copy is missing. Never report a failed send.
            result.note += ' A copy could not be saved in your Yahoo Sent folder.'
    return result


def yahoo_sent_folder(address, app_password):
    from yahoo_mailbox import YahooMailbox
    return YahooMailbox(address, app_password).save_sent_copy


def yahoo_originals(draft, address, app_password):
    """On a forward, a function giving the bytes of one of the original message's files."""
    from yahoo_mailbox import YahooMailbox
    context = draft.response_context or {}
    mailbox = YahooMailbox(address, app_password)
    return lambda item: mailbox.attachment_bytes(context['source_id'], item, context.get('source_folder') or 'INBOX')


def submit(provider, draft, access_token, sender=''):
    """One attempt only. Return provider acceptance; never assert final delivery."""
    if provider == 'yahoo':
        return yahoo_submit(draft, sender, access_token, sent_folder=yahoo_sent_folder(sender, access_token),
                            originals=yahoo_originals(draft, sender, access_token))
    if provider == 'thunderbird':
        from thunderbird_mailbox import submit as thunderbird_submit
        return thunderbird_submit(draft, access_token)
    raise DeliveryError('This email account is not connected. Email works through Thunderbird or Yahoo. '
                        'Say add account to connect one.', uncertain=False)
