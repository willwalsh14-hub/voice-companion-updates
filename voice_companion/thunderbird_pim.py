"""Contacts and calendars through Thunderbird (the add-on's pim.js and calendar Experiment).

Thunderbird syncs Google, Microsoft and other address books (CardDAV) and
calendars (CalDAV) by itself once the account is set up there. Voice Companion
asks the add-on for them, the same way it asks for mail (thunderbird_mailbox.call):
Thunderbird is started in the background when it is not running.

Every failure becomes PimError with a plain sentence that says what to do.
"""
from mailbox_access import MailboxError

# An add-on copy from before contacts and calendars (1.2 and older) answers this.
UNSUPPORTED = 'Unsupported mail request.'
UPDATE_NEEDED = ('The Voice Companion connection in Thunderbird must be updated first. '
                 'Voice Companion updates it by itself in a moment. Thunderbird restarts once. If you are writing a message in Thunderbird, finish it first. Then try again.')
NO_THUNDERBIRD = ('Contacts and calendars come from Thunderbird, and Thunderbird is not set up yet. '
                  'Say add Thunderbird account to set it up.')


# What to do next, for the add-on's answers that say only what went wrong. Spoken messages say both.
NEXT_STEPS = {
    'Thunderbird could not list its calendars.': 'Close Thunderbird, then try again.',
    'Thunderbird could not read its calendars.': 'Try again in a minute.',
    'Thunderbird could not save the event.': 'Close Thunderbird, then try again.',
    'Thunderbird could not save the change.': 'Close Thunderbird, then try again.',
    'Thunderbird could not delete the event.': 'Close Thunderbird, then try again.',
    # The add-on's answer when Thunderbird itself failed in a way it does not name.
    'Thunderbird could not complete that request.': 'Close Thunderbird, then try again.',
}


# Requests that change something. When Thunderbird got one but did not answer in time, it may still
# make the change, so "not saved" would be wrong and trying again at once could add it twice.
CHANGING = ('addContact', 'updateContact', 'deleteContact', 'addEvent', 'deleteEvent', 'modifyEvent')
UNSURE = ('Thunderbird did not answer in time, so it may still make the change. '
          'Wait a minute. Then check if it worked before you try again.')


class PimError(Exception):
    """Thunderbird could not give or change contacts or events. The text says why and what to do."""


def gone(exc):
    """True when the contact or event was removed meanwhile (in Thunderbird, on a phone), so the
    list on screen must be read again: "That event is no longer in the calendar."."""
    return 'is no longer in' in str(exc)


def unsure(exc):
    """True when Thunderbird may have made the change after all (it did not answer in time),
    so the screen must not say it was not saved."""
    from thunderbird_mailbox import CHANGE_UNSURE
    return str(exc) in (UNSURE, CHANGE_UNSURE)


def connected():
    import thunderbird_mailbox
    return thunderbird_mailbox.connected()


def has_thunderbird_account(folder):
    """True when Voice Companion has a Thunderbird email account saved, so starting Thunderbird makes sense."""
    try:
        from mail_accounts import ProtectedStore
        return any(provider == 'thunderbird' for provider, _ in ProtectedStore(folder).accounts())
    except Exception:
        return False


def usable(folder):
    """Ask Thunderbird only when it is connected now, or when the user uses Thunderbird for email.
    Opening Contacts must never start installing Thunderbird by itself."""
    return connected() or has_thunderbird_account(folder)


def call(method, **params):
    import thunderbird_mailbox
    try:
        return thunderbird_mailbox.call(method, **params)
    except MailboxError as exc:
        text = str(exc)
        if text == thunderbird_mailbox.NO_ANSWER and method in CHANGING:
            raise PimError(UNSURE) from exc
        if text in NEXT_STEPS:
            text += ' ' + NEXT_STEPS[text]
        raise PimError(UPDATE_NEEDED if text == UNSUPPORTED else text) from exc


def lists(value):
    """A part of an answer that should be a list, or [] when it is missing or damaged."""
    return list(value) if isinstance(value, (list, tuple)) else []


def contacts():
    """{'books': [...], 'contacts': [...], 'failed': n} from every Thunderbird address book."""
    answer = call('contacts')
    if not isinstance(answer, dict) or not isinstance(answer.get('contacts'), list):
        raise PimError('Thunderbird sent contacts Voice Companion could not read. Try again.')
    return dict(answer, books=lists(answer.get('books')), failedBooks=lists(answer.get('failedBooks')))


def add_contact(name, email='', phone='', book_id=''):
    return call('addContact', bookId=book_id, name=name, email=email, phone=phone)


def update_contact(contact_id, **changes):
    """Change a contact's name, first email address or first phone number (name=, email=, phone=).
    An empty email or phone removes it; the rest of the contact is kept."""
    return call('updateContact', id=contact_id, **{key: value for key, value in changes.items() if value is not None})


def delete_contact(contact_id):
    return call('deleteContact', id=contact_id)


def calendars():
    answer = call('calendars')
    if not isinstance(answer, list):
        raise PimError('Thunderbird sent calendars Voice Companion could not read. Try again.')
    return answer


def events(start_ms, end_ms):
    answer = call('events', start=int(start_ms), end=int(end_ms))
    if not isinstance(answer, dict) or not isinstance(answer.get('events'), list):
        raise PimError('Thunderbird sent events Voice Companion could not read. Try again.')
    return dict(answer, failed=lists(answer.get('failed')))


def add_event(calendar_id, title, start, end, all_day=False, location='', notes=''):
    return call('addEvent', calendarId=calendar_id, title=title, start=start, end=end, allDay=bool(all_day),
                location=location, notes=notes)


def delete_event(calendar_id, event_id, occurrence='', all_of_them=False):
    return call('deleteEvent', calendarId=calendar_id, id=event_id, occurrence=occurrence, all=bool(all_of_them))


def modify_event(calendar_id, event_id, changes, occurrence='', all_of_them=False):
    """Change an event. changes holds only what changes (see modifyEvent in pim.js): title, location,
    notes; allDay with start and end; calendarId to move it; shift and length for a whole repeating event."""
    return call('modifyEvent', calendarId=calendar_id, id=event_id, occurrence=occurrence, all=bool(all_of_them),
                changes=dict(changes))
