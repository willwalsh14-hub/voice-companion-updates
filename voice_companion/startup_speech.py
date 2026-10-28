"""The startup announcement: one message, always in the same order (Will, October 2026).

Order:
  1. the version, and that the app is ready
  2. the result of an update that just finished
  3. one-time notices (an old email account removed, a part that could not start)
  4. the microphone, and how to use voice or keys
  5. where you are: the main menu, or a question that waits for an answer

It is said as ONE message. The keyboard voice stops what it is saying when new
speech starts, so separate startup messages used to cut each other off: after an
update, people heard only "Voice Companion was updated successfully." and never
the version or how to start.
"""

READY = 'Voice Companion {version} is ready.'

UPDATE_RESULTS = {
    'succeeded': 'Voice Companion was updated successfully.',
    'restart': 'The update needs a Windows restart to finish. Ask your trainer to restart when convenient.',
    'failed': 'The update did not finish. You can keep using the app or ask your trainer for help.',
}

KEYS = 'Use your Up and Down arrow keys, Enter and Escape to move through the menus.'

# The microphone, said before a question that has its own instructions.
MICROPHONE = {
    'on': 'The microphone is on.',
    'off': 'The microphone is off, as you left it. Press F9 to turn it on.',
    'failed': 'The microphone could not start, so voice commands are off for now.',
}

# The microphone and how to use the app, said before "You are on the main menu."
# Never "say wake up" when nothing is listening.
HOW_TO = {
    'on': 'The microphone is on. Say wake up to control the app by voice, or use your Up and Down arrow keys, '
          'Enter and Escape to move through the menus. Say start tutorial for spoken lessons that wait for you. '
          'Help is available.',
    'off': MICROPHONE['off'] + ' ' + KEYS + ' Press F1 for help.',
    'failed': MICROPHONE['failed'] + ' ' + KEYS + ' Press F1 for help. Check that a microphone is plugged in '
              'and allowed in Windows privacy settings. Voice commands start by themselves when it works.',
}

MAIN_MENU = 'You are on the main menu.'


def update_result(result):
    """The kind of update result in update-result.json: 'restart', 'succeeded' or 'failed'."""
    if not isinstance(result, dict):
        return 'failed'
    if result.get('restart_required'):
        return 'restart'
    return 'succeeded' if result.get('succeeded') else 'failed'


def update_notice(result):
    """Why an update stopped, when one of the user's own programs held a Voice Companion
    file (apply-update.ps1 writes their names in blocked_by), and what to do next."""
    if not isinstance(result, dict) or result.get('succeeded'):
        return ''
    names = result.get('blocked_by')
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list):
        return ''
    names = sorted({name.strip() for name in names if isinstance(name, str) and name.strip()})
    if not names:
        return ''
    joined = names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]
    verb, them = ('was', 'it') if len(names) == 1 else ('were', 'them')
    return (joined + ' ' + verb + ' using a Voice Companion file, so the update could not install. '
            'Save your work in ' + joined + ' and close ' + them + '. Then choose Check for updates on the main menu.')


def microphone_text(state):
    """The microphone and how to use the app, without where you are."""
    return HOW_TO.get(state, HOW_TO['on'])


def followup(state):
    """What comes after a startup question is answered: the microphone, how to use it, where you are."""
    return microphone_text(state) + ' ' + MAIN_MENU


def announcement(version, microphone='on', result=None, notices=(), question=None):
    """The whole startup announcement.

    microphone: 'on', 'off' (turned off with F9) or 'failed' (could not open).
    result: None, or a key of UPDATE_RESULTS.
    notices: one-time messages, said once, in order.
    question: a question asked at startup (an update offer, Chrome setup). It is
    said last, as where you are; its own words say how to answer it."""
    parts = [READY.format(version=version)]
    if result:
        parts.append(UPDATE_RESULTS[result])
    seen = set()
    for notice in notices:
        notice = str(notice or '').strip()
        if notice and notice not in seen:
            seen.add(notice)
            parts.append(notice)
    if question:
        parts.append(MICROPHONE.get(microphone, MICROPHONE['on']))
        parts.append(str(question).strip())
    else:
        parts.append(followup(microphone))
    return ' '.join(parts)
