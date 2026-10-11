"""Provider send requests. Authentication and voice confirmation live elsewhere.

Never retry a send automatically: a timeout can happen after acceptance.
The local draft must remain available until a provider response is recorded.
"""
import base64
import html
import json
import smtplib
import ssl
from email.message import EmailMessage
from urllib import error, parse, request

from email_draft import normalize_recipient
from email_formatting import formatted_body


class DeliveryError(Exception):
    def __init__(self,message,uncertain=True):
        super().__init__(message);self.uncertain=uncertain


def message_parts(draft):
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
    if context.get('action') == 'forward':
        return (comment + '\n\nForwarded message:\nFrom: ' + context.get('original_from', '') +
                '\nSubject: ' + context.get('original_subject', '') + '\n\n' + context.get('original_body', ''))
    return comment



def gmail_request(draft, access_token):
    recipient, subject, body = message_parts(draft)
    if not access_token:
        raise DeliveryError('Gmail is not connected.')
    msg = EmailMessage()
    msg['To'] = recipient
    if draft.cc: msg['Cc'] = ', '.join(draft.cc)
    if getattr(draft, 'bcc', []): msg['Bcc'] = ', '.join(draft.bcc)
    msg['Subject'] = subject
    context = draft.response_context
    if context.get('action') in ('reply', 'reply_all'):
        if context.get('message_id'):
            msg['In-Reply-To'] = context['message_id']
            msg['References'] = (context.get('references', '') + ' ' + context['message_id']).strip()
    msg.set_content(outgoing_body(draft, body))
    msg.add_alternative(formatted_body(draft), subtype='html')
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode('ascii')
    payload = {'raw': raw}
    if context.get('thread_id') and context.get('action') in ('reply', 'reply_all'):
        payload['threadId'] = context['thread_id']
    return request.Request(
        'https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + access_token,
                 'Content-Type': 'application/json'}, method='POST')


def microsoft_request(draft, access_token):
    recipient, subject, body = message_parts(draft)
    if not access_token:
        raise DeliveryError('Microsoft email is not connected.')
    context = draft.response_context
    if context.get('action') in ('reply', 'reply_all', 'forward'):
        action = {'reply':'reply', 'reply_all':'replyAll', 'forward':'forward'}[context['action']]
        source = parse.quote(context['source_id'], safe='')
        payload = {'message': {'body': {'contentType':'HTML', 'content':formatted_body(draft)}}}
        if context.get('recipient_override') or getattr(draft, 'bcc', []):
            payload['message'].update({'toRecipients': [{'emailAddress': {'address': recipient}}],
                                  'ccRecipients': [{'emailAddress': {'address': a}} for a in draft.cc],
                                  'bccRecipients': [{'emailAddress': {'address': a}} for a in draft.bcc]})
        elif action == 'forward':
            payload['toRecipients'] = [{'emailAddress': {'address': recipient}}]
        return request.Request(
            'https://graph.microsoft.com/v1.0/me/messages/' + source + '/' + action,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Authorization': 'Bearer ' + access_token,
                     'Content-Type': 'application/json'}, method='POST')
    payload = {'message': {'subject': subject,
                           'body': {'contentType': 'HTML', 'content': formatted_body(draft)},
                           'toRecipients': [{'emailAddress': {'address': recipient}}]},
               'saveToSentItems': True}
    if draft.cc:
        payload['message']['ccRecipients'] = [{'emailAddress': {'address': a}} for a in draft.cc]
    if getattr(draft, 'bcc', []):
        payload['message']['bccRecipients'] = [{'emailAddress': {'address': a}} for a in draft.bcc]
    return request.Request(
        'https://graph.microsoft.com/v1.0/me/sendMail',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + access_token,
                 'Content-Type': 'application/json'}, method='POST')


def yahoo_submit(draft, address, app_password, smtp_factory=smtplib.SMTP_SSL):
    recipient, subject, body = message_parts(draft)
    if not app_password or not address:
        raise DeliveryError('Yahoo is not connected.')
    msg = EmailMessage()
    msg['From'] = normalize_recipient(address)
    msg['To'] = recipient
    if draft.cc: msg['Cc'] = ', '.join(draft.cc)
    if getattr(draft, 'bcc', []): msg['Bcc'] = ', '.join(draft.bcc)
    msg['Subject'] = subject
    context = draft.response_context
    if context.get('action') in ('reply', 'reply_all') and context.get('message_id'):
        msg['In-Reply-To'] = context['message_id']
        msg['References'] = (context.get('references', '') + ' ' + context['message_id']).strip()
    msg.set_content(outgoing_body(draft, body))
    msg.add_alternative(formatted_body(draft), subtype='html')
    try:
        with smtp_factory('smtp.mail.yahoo.com', 465,
                          context=ssl.create_default_context(), timeout=30) as smtp:
            smtp.login(address, app_password)
            refused = smtp.send_message(msg)
    except (smtplib.SMTPException, OSError, TimeoutError) as exc:
        raise DeliveryError('The result is uncertain. Keep the draft and check with the recipient before trying again.') from exc
    if refused:
        raise DeliveryError('Yahoo did not accept the recipient. Keep the draft.')
    return 'accepted by Yahoo SMTP'


def submit(provider, draft, access_token, opener=request.urlopen, sender=''):
    """One attempt only. Return provider acceptance; never assert final delivery."""
    builders = {'gmail': gmail_request, 'outlook': microsoft_request,
                'microsoft 365': microsoft_request, 'exchange online': microsoft_request}
    if provider == 'yahoo':
        return yahoo_submit(draft, sender, access_token)
    if provider not in builders:
        raise DeliveryError('This email provider is not connected.')
    outgoing = builders[provider](draft, access_token)
    try:
        with opener(outgoing, timeout=30) as response:
            status = response.status
            data = response.read(65536)
    except error.HTTPError as exc:
        if exc.code >= 500:
            raise DeliveryError('The result is uncertain. Keep the draft and check Sent Mail before trying again.') from exc
        reason={400:'The provider rejected the message format or recipient.',401:'Email sign-in expired. Reconnect the sending account.',403:'The account does not have permission to send email. Reconnect it and allow sending permission.',429:'The email provider is limiting requests. Wait before reviewing the draft again.'}.get(exc.code,'The provider rejected the message. Check the account connection.')
        raise DeliveryError(reason,uncertain=False) from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise DeliveryError('The result is uncertain. Keep the draft and check Sent Items before trying again.') from exc
    if provider == 'gmail':
        try:
            message_id = json.loads(data)['id']
        except (ValueError, KeyError, TypeError) as exc:
            raise DeliveryError('The provider response is unclear. Check Sent Mail before trying again.') from exc
        if status != 200 or not isinstance(message_id, str) or not message_id:
            raise DeliveryError('The provider response is unclear. Check Sent Mail before trying again.')
        return message_id
    if status != 202:
        raise DeliveryError('The provider response is unclear. Check Sent Items before trying again.')
    return 'accepted by Microsoft Graph'
