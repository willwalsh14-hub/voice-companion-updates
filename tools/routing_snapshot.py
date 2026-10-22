"""Record what Voice Companion does with each phrase on each screen, and check it later.

This is the safety net for restructuring `companion._handle`: every command
phrase found in the handler, plus everyday sentences and known tricky cases,
is sent to the app on several screens. For each one we record the screen it
ends on, what it says, what it asked the inbox or browser to do, and the text
of the open document or email. Restructuring must leave every record the same.

  python3 tools/routing_snapshot.py --check    # compare with tools/routing-snapshot.json
  python3 tools/routing_snapshot.py --update   # re-record after an INTENDED behaviour change

When a change is intended, update the snapshot in the same pull request and
say in the pull request which records changed and why.

Everything that could leave the machine or change it (network, browser,
email accounts, shutting down, opening folders) is replaced by stand-ins.
"""
import argparse
import ast
import contextlib
import copy
import io
import json
import os
import re
import signal
import sys
import tempfile
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parent
SOURCE = TOOLS.parent / 'voice_companion'
SNAPSHOT = TOOLS / 'routing-snapshot.json'
SCREENS = ('awake', 'document', 'email_draft', 'mailbox', 'web', 'note', 'help')

# Everyday writing and sentences that look like commands (see CLAUDE.md).
SENTENCES = (
    'Hello there.', 'Please meet me today.', 'Rate this product five stars.',
    'Pitch the tent by the lake.', 'Help with the dishes tonight', 'Capital letters are important.',
    'Browser tabs are confusing.', 'Use voice commands to send mail', 'Use the back door at night.',
    'Switch to the left lane at the light.', 'Volume two of the series is out.', 'Speed limits matter.',
    'Find a way to call Bob.', 'Update contact details as soon as you can.', 'Send to the team at the office.',
    'CC is fine with me.', 'Subject is closed for now.', 'The trial period is over',
    'Yes, please.', 'No, thanks.', 'volume banana', 'rate 1.5', 'rate 11', 'go to sleep',
    'go to main menu', 'go to options', 'go to website example dot com', 'open website VA dot gov.',
    'change font to Arial', 'replace paragraph with New text', 'change world to friend',
    'delete line', 'new paragraph', 'read paragraph', 'next word', 'select first paragraph',
)


def command_phrases():
    source = (SOURCE / 'companion.py').read_text(encoding='utf-8')
    # Command handling lives in _handle and the screen handlers (handle_*_screen) before it.
    body = source[source.index('def handle_mailbox_screen(') if 'def handle_mailbox_screen(' in source
                  else source.index('def _handle(text, mode, typed=False):'):source.index('def handle(text, mode, typed=False):')]
    phrases = set()
    for node in ast.walk(ast.parse(body)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.strip()
            if re.fullmatch(r"[a-z][a-z0-9 ']{1,40}", value) and 1 <= len(value.split()) <= 6:
                phrases.add(value)
    return sorted(phrases) + list(SENTENCES)


class Timeout(Exception):
    pass


def _alarm(signum, frame):
    raise Timeout()


def normalize(value, folder):
    text = str(value).replace(str(folder), '<DATA>')
    text = re.sub(r"<(?:Magic)?Mock[^>]*>", '<mock>', text)
    text = re.sub(r"id='\d+'", "id='N'", text)
    # The version changes every release; it is not a routing change.
    text = re.sub(r"\b0\.\d+\.\d+-test\b", '<VERSION>', text)
    return text


def record(phrases):
    sys.path.insert(0, str(SOURCE))
    folder = Path(tempfile.mkdtemp(prefix='vc-routing-'))
    os.environ['VOICE_COMPANION_DATA_DIR'] = str(folder / 'data')
    import urllib.request
    import companion
    from document_editor import Paragraph, VoiceDocument
    from email_draft import VoiceEmail

    class Store:
        def __init__(self, *args, **kwargs): pass
        def accounts(self): return []
        def selected(self): return None
        def select(self, provider, address): return (provider, address)
        def load(self, provider): raise companion.AccountError('That account is not connected.')

    class Session(mock.MagicMock):
        """Stand-in for the inbox or web session. Constructor arguments are ignored."""
        def __init__(self, *args, **kwargs):
            super().__init__(**{key: value for key, value in kwargs.items() if key not in ('progress', 'folder')})
            self.process.side_effect = lambda text, *a, **k: 'session handled: ' + str(text)
            self.command.side_effect = lambda text, *a, **k: 'session handled: ' + str(text)
            self.menu.process.return_value = ('menu handled', None)
            self.menu.start.return_value = 'Web browser menu.'

        def _get_child_mock(self, **kwargs):
            return mock.MagicMock(**kwargs)

    class MailSession(Session): pass
    class WebSession(Session): pass

    class SignIn(mock.MagicMock):
        def __getattr__(self, name):
            if name.startswith('_'): return super().__getattr__(name)
            raise OSError('sign-in disabled for routing snapshot')

    baseline = {name: copy.deepcopy(value) for name, value in vars(companion).items()
                if not name.startswith('__') and isinstance(value, (str, int, float, bool, type(None), tuple, list, dict, set))}
    spoken = []
    patches = [
        mock.patch.object(companion, 'speak', lambda text, *a, **k: spoken.append(str(text))),
        mock.patch.object(companion, 'interrupt_speech', lambda *a, **k: None),
        mock.patch.object(companion, 'ProtectedStore', Store),
        # Real classes, because the app checks isinstance(web_session, WebSession).
        mock.patch.object(companion, 'MailSession', MailSession),
        mock.patch.object(companion, 'WebSession', WebSession),
        # Sign-in libraries: the same stand-in whether or not they are installed here.
        mock.patch.dict(sys.modules, {name: SignIn(name=name) for name in
                                      ('msal', 'google_auth_oauthlib', 'google_auth_oauthlib.flow')}),
        mock.patch.object(urllib.request, 'urlopen', side_effect=OSError('network disabled for routing snapshot')),
        mock.patch('subprocess.Popen', mock.MagicMock(name='Popen')),
        mock.patch('subprocess.run', mock.MagicMock(name='run')),
        mock.patch('webbrowser.open', mock.MagicMock(name='browser')),
        mock.patch.object(os, 'startfile', mock.MagicMock(name='startfile'), create=True),
    ]
    import media_hub

    class ImmediateThread:
        """Runs media background work at once, so results never depend on timing."""
        def __init__(self, target=None, args=(), kwargs=None, daemon=None, **extra):
            self.target, self.args, self.kwargs = target, args, kwargs or {}
            self.finished = False
        def start(self):
            try: self.target(*self.args, **self.kwargs)
            except Exception: pass
            self.finished = True
        def is_alive(self): return False
        def join(self, timeout=None): pass

    patches += [mock.patch.object(media_hub.threading, 'Thread', ImmediateThread),
                mock.patch.object(media_hub.MediaHub, 'start_scheduler', lambda self: None)]
    results = {}
    signal.signal(signal.SIGALRM, _alarm)
    with contextlib.ExitStack() as stack:
        for item in patches: stack.enter_context(item)
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        for screen in SCREENS:
            for phrase in phrases:
                for name, value in baseline.items(): setattr(companion, name, copy.deepcopy(value))
                companion.TEXT_MODE = True; companion.APP = folder / 'data'; companion.APP_WINDOW = None
                companion.UPDATES = None; companion.MEDIA_HUB = None
                for name in ('document', 'email_draft', 'mail_session', 'web_session', 'help_session',
                             'pending_send', 'pending_website', 'tutorial_session'):
                    setattr(companion, name, None)
                companion.KEYBOARD_DIRTY.clear(); companion.note_buffer.clear()
                editor = None
                if screen == 'document':
                    editor = companion.document = VoiceDocument(folder / 'Docs', title='Routing check')
                    editor.paragraphs = [Paragraph('Hello world. Second sentence.')]; editor.dictating = True
                    companion.checkpoint_document()
                elif screen == 'email_draft':
                    editor = companion.email_draft = VoiceEmail(folder / 'Drafts', title='Routing check', recipient='friend@example.com',
                                                                subject='Hi', provider='gmail', compose_step='body')
                    editor.paragraphs = [Paragraph('Hello world.')]; editor.dictating = True
                elif screen == 'mailbox':
                    companion.mail_session = MailSession(name='mail', view='folder', pending=None, folder_picker=None,
                                                            folder_choice=None, all_messages=False, list_size=10)
                elif screen == 'help':
                    # The app is only on the help screen with a guide open.
                    companion.help_return_mode = 'awake'
                    companion.help_session = companion.HelpSession(
                        companion.GUIDE, release_notes_path=companion.GUIDE.parent / 'Voice Companion Release Notes.txt')
                elif screen == 'web':
                    companion.web_session = WebSession(name='web', pending=None, submit_pending=None, list_focus=None,
                                                           keyboard_field=None, form_index=None, choices=[], snapshot=None)
                spoken.clear()
                signal.alarm(10)
                try:
                    outcome = companion.handle(phrase, screen)
                except Timeout:
                    outcome = 'TIMEOUT'
                except BaseException as error:
                    outcome = 'EXCEPTION ' + type(error).__name__
                    if os.environ.get('ROUTING_SNAPSHOT_TRACE'):
                        import traceback
                        frame = traceback.extract_tb(error.__traceback__)[-1]
                        sys.__stderr__.write(f'{screen} | {phrase} -> {type(error).__name__}: {error} at {Path(frame.filename).name}:{frame.lineno}\n')
                finally:
                    signal.alarm(0)
                calls = []
                for session in (companion.mail_session, companion.web_session):
                    if isinstance(session, mock.MagicMock):
                        calls += [normalize(call, folder) for call in session.mock_calls
                                  if not str(call).startswith('call.__')][:3]
                entry = {'screen_after': str(outcome), 'said': [normalize(text, folder) for text in spoken[:3]]}
                if calls: entry['calls'] = calls
                if editor is not None: entry['text'] = [p.text for p in editor.paragraphs]
                results[screen + ' | ' + phrase] = entry
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true')
    group.add_argument('--update', action='store_true')
    args = parser.parse_args()
    phrases = command_phrases()
    if SNAPSHOT.exists():
        # Keep every phrase already recorded, even if its code moved out of _handle.
        saved_phrases = {key.split(' | ', 1)[1] for key in json.loads(SNAPSHOT.read_text(encoding='utf-8'))}
        phrases = sorted(set(phrases) | saved_phrases)
    current = record(phrases)
    if args.update:
        SNAPSHOT.write_text(json.dumps(current, indent=0, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8')
        print(f'Recorded {len(current)} phrase and screen pairs in {SNAPSHOT.name}.')
        return
    saved = json.loads(SNAPSHOT.read_text(encoding='utf-8'))
    changed = sorted(key for key in set(saved) | set(current) if saved.get(key) != current.get(key))
    for key in changed[:40]:
        print('CHANGED:', key)
        print('   before:', json.dumps(saved.get(key), ensure_ascii=False)[:300])
        print('   now:   ', json.dumps(current.get(key), ensure_ascii=False)[:300])
    if len(changed) > 40: print(f'... and {len(changed) - 40} more.')
    print(f'{len(current)} recorded; {len(changed)} changed.')
    sys.exit(1 if changed else 0)


if __name__ == '__main__':
    main()
