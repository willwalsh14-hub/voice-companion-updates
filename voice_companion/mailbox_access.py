"""Pieces shared by the Yahoo and Thunderbird mailboxes.

The direct Gmail and Microsoft mailboxes were removed (Will, October 2026):
those addresses work through Thunderbird now.
"""
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


class MailboxError(Exception):
    """A mail request failed. unchanged=True means nothing in the mailbox changed."""
    def __init__(self, message='', unchanged=False):
        super().__init__(message)
        self.unchanged = unchanged


def message_date(value):
    """Speak a provider delivery date in the computer's local time zone."""
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            date = datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
        elif isinstance(value, str) and len(value) > 10 and value[2] == '-' and value[6] == '-':
            date = datetime.strptime(value, '%d-%b-%Y %H:%M:%S %z')
        elif isinstance(value, str) and re.match(r'\d{4}-\d\d-\d\dT', value):
            # ISO 8601 only: RFC 2822 dates such as "Tue, ..." and "Thu, ..." also contain a T.
            date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        else:
            date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return date.astimezone().strftime('%B %d, %Y').replace(' 0', ' ')
    except (TypeError, ValueError, OverflowError, OSError):
        return 'unavailable'


from email_reader import render_html


def html_text(source):
    return render_html(source)


def part_text(part):
    """Text of one message part. An unknown character set is read as UTF-8
    instead of hiding the whole message."""
    try:
        return part.get_content()
    except LookupError:
        return (part.get_payload(decode=True) or b'').decode('utf-8', 'replace')
