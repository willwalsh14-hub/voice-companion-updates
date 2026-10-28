"""The question asked before a written email draft is closed (Will, October 2026).

Escape, "Cancel", "Back" or "Go back" in an email draft used to throw it away at
once. Now, when the draft holds something not yet saved, Voice Companion asks:
save it as a draft, throw it away, or keep writing. Save is chosen first, and
"No" or Escape keeps writing, so a quick answer never loses anything. A draft
with nothing typed closes without the question.

The app is in the mode MODE while asking. companion.py calls context(),
navigation_key() and answer(); `m` is the companion module.
"""
import re

MODE = 'draft_leave'
CHOICES = ('save', 'discard', 'keep')
NAMES = {'save': 'Save as a draft', 'discard': 'Throw it away', 'keep': 'Keep writing'}
CHOICE = 'save'
QUESTION = ('Leave this email? Save it as a draft, throw it away, or keep writing. '
            'Use arrows to choose, then Enter. Escape keeps writing.')
SHORT_QUESTION = 'Say save draft, throw it away, or keep writing.'

SAVE_WORDS = ('save', 'save it', 'save draft', 'save the draft', 'save as draft', 'save as a draft',
              'save it as a draft', 'save email', 'save and close', 'yes', 'yes please')
DISCARD_WORDS = ('throw it away', 'throw away', 'throw away the draft', 'throw away the email',
                 'throw this away', 'discard', 'discard it', 'discard draft', 'discard the draft',
                 'discard email', 'delete it', 'delete draft', 'delete the draft', 'do not save', "don't save")
KEEP_WORDS = ('keep writing', 'keep editing', 'continue writing', 'go on writing', 'back to the email',
              'no', 'no thanks', 'cancel', 'go back', 'back', 'never mind', 'nevermind', 'escape')
PICK_WORDS = ('that one', 'okay', 'ok', 'confirm', 'confirm that')


def has_work(draft):
    """True when the draft holds something the person typed or chose."""
    context = draft.response_context or {}
    if any(p.text.strip() for p in draft.paragraphs) or draft.attachments:
        return True
    if context:
        # A reply or forward fills in its own address and subject; only changes count.
        return bool(context.get('recipient_override') or context.get('dropped_attachments')
                    or draft.bcc or (context.get('action') == 'forward' and draft.recipient))
    return bool(draft.recipient or draft.subject or draft.cc or draft.bcc)


def unsaved(draft, dirty=False):
    """True when closing could lose something: work that was changed since it was last saved."""
    return has_work(draft) and (dirty or bool(getattr(draft, '_discard_baseline', None)))


def ask(m):
    global CHOICE
    CHOICE = 'save'
    m.speak(QUESTION)
    return MODE


def context(m, value):
    value['confirmation'] = CHOICE
    value['menu_letters'] = False
    return value


def choice_words():
    index = CHOICES.index(CHOICE)
    return NAMES[CHOICE] + ', ' + str(index + 1) + ' of ' + str(len(CHOICES)) + '.'


def navigation_key(m, key):
    """The spoken answer for a key, or None when the key only moved the choice."""
    global CHOICE
    if key in ('Up', 'Left', 'ShiftTab', 'Home', 'Down', 'Right', 'Tab', 'End'):
        place = CHOICES.index(CHOICE)
        if key in ('Home', 'End'):
            wanted = 0 if key == 'Home' else len(CHOICES) - 1
        else:
            wanted = place + (-1 if key in ('Up', 'Left', 'ShiftTab') else 1)
        if not 0 <= wanted < len(CHOICES) or wanted == place:
            m.speak('Start of list.' if wanted <= place and key in ('Up', 'Left', 'ShiftTab', 'Home') else 'End of list.')
        else:
            CHOICE = CHOICES[wanted]
            m.speak(choice_words())
            m.sync_app_context(MODE)
        return None
    if key in ('Enter', 'Space'):
        return {'save': 'save draft', 'discard': 'throw it away', 'keep': 'keep writing'}[CHOICE]
    if key == 'Escape':
        return 'keep writing'
    if key.lower() in ('y', 'alt+y'):
        return 'save draft'
    if key.lower() in ('n', 'alt+n'):
        return 'keep writing'
    return None


def answer(text):
    """'save', 'discard', 'keep', or None when the words are not an answer."""
    command = ' '.join(re.sub(r'[^a-z\' ]', ' ', str(text).casefold()).split())
    if command in PICK_WORDS:
        return CHOICE
    if command in SAVE_WORDS:
        return 'save'
    if command in DISCARD_WORDS:
        return 'discard'
    if command in KEEP_WORDS:
        return 'keep'
    return None
