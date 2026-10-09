"""Voice Companion: local, self-voicing Windows prototype."""
import json
import os
import queue
import shutil
import re
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from vosk import KaldiRecognizer, Model, SetLogLevel
from document_editor import VoiceDocument, structural_selection_request, document_control_request, LINE_BREAK_COMMANDS
from field_selection import FieldSelection, selection_command
from email_draft import VoiceEmail, normalize_recipient, email_field_destination
from address_book import AddressBook
from media_hub import MediaHub
from email_delivery import DeliveryError, message_parts, submit
from mail_accounts import AccountError, ProtectedStore, account_token, connect
from mail_voice import MailSession
from practice_tutorial import PracticeTutorial
from cloud_recognition import CloudRecognition
from parakeet_recognition import ParakeetRecognition
from browser_navigation import prepare_address
from web_assistant import WebSession
from audio_resample import PCM16Resampler, stereo_to_mono
from app_window import CompanionWindow
from reading_navigation import ReadingCursor, reading_request
from dictation_text import clean_dictation
from onboard_help import HelpSession
from speech_controls import request as speech_setting_request
from settings_model import DEFAULTS, SettingsSession, prompt_text, keyboard_request
from keyboard_text import document_text,replace_keyboard_text,absolute

APP = Path(os.getenv('VOICE_COMPANION_DATA_DIR') or
           (Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'VoiceCompanion'))
DEFAULT_APP = APP
APP_VERSION = '0.2.90-test'
def documents_folder():
    if os.getenv('VOICE_COMPANION_DATA_DIR') or APP != DEFAULT_APP:
        return APP / 'Documents'
    folder = Path.home() / 'Documents'
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
                folder = Path(os.path.expandvars(winreg.QueryValueEx(key, 'Personal')[0]))
        except (OSError, ValueError):
            pass
    target = folder / 'Voice Companion'
    legacy = APP / 'Documents'
    if legacy.exists() and legacy.resolve() != target.resolve():
        target.mkdir(parents=True, exist_ok=True)
        for old in legacy.glob('*.docx'):
            destination = target / old.name
            if not destination.exists():
                shutil.copy2(old, destination)
    return target

def document_format_command(command):
    if structural_selection_request(command) or document_control_request(command):
        return True
    if command in ('normal text', 'make this normal text', 'make this a normal paragraph', 'make this a bulleted list', 'make this a bullet', 'make this a numbered list', 'remove table', 'delete cell contents', 'where in table', 'what cell', 'move right a cell', 'move left a cell', 'row down', 'row up', 'column right', 'column left', 'list headings', 'what are the headings', 'next heading', 'go to next heading', 'new line', 'insert new line', 'line break', 'new paragraph', 'undo', 'undo that', 'remove list', 'end list', 'leave table', 'after table', 'read table', 'review table', 'delete table', 'clear cell', 'read cell', 'next cell', 'previous cell'):
        return True
    return bool(re.match(r'^(?:apply heading(?: level)? |heading (?:level )?|make this (?:a heading|bold|italic|underlined)|'
                         r'(?:set|change|use) (?:word )?(?:font|size) |(?:bold|italic|italicize|underline) (?:word|this|paragraph)|'
                         r'(?:remove|not) (?:bold|italic|underline)|(?:single|double|custom) spac|'
                         r'line spacing |(?:set|change|read|reset|default) (?:document |all )?line spacing|'
                         r'(?:bulleted|bullet|numbered|number) list|(?:align|justify) |(?:bold|italicize|underline|highlight) |(?:insert|add|create|delete) (?:a )?(?:table|row|column) |'
                         r'create table |(?:next|previous) (?:cell|row|column)|read formatting|save document)', command))
BUNDLED_MODEL = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'model'
PARAKEET_MODEL = Path(os.getenv('VOICE_COMPANION_PARAKEET_MODEL',
    str(Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'model-parakeet')))
MODEL = Path(os.getenv('VOICE_COMPANION_MODEL', str(BUNDLED_MODEL if BUNDLED_MODEL.exists() else APP / 'model')))
GUIDE = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'START HERE - Veteran.txt'
WAKE = ('wake up',)
SLEEP = ('go to sleep', 'stop listening', 'sleep companion')
EXIT = ('shut down companion', 'exit companion', 'quit companion')
AUDIO = queue.Queue(maxsize=400)  # About 20 seconds of 50 ms microphone blocks.
AUDIO_OVERFLOW = False
SPEAKING = False
MUTE_UNTIL = 0.0
SPEECH_PAUSED = False
AI_SPEECH = None
AI_VOICE_NAME = 'coral'
ESPEAK_SPEECH = None
ESPEAK_VOICE_NAME = 'en-us'
SYNTH_PICK_INDEX = None
VOICE_PICK_ENGINE = None
SPEECH_RATE = -1
SPEECH_VOLUME = 100
PUNCTUATION_LEVEL = 'some'
VERBOSITY = 'high'
SETTINGS_PANEL = None
SETTINGS_RETURN_MODE = 'awake'
try:
    PREFERENCES = DEFAULTS | json.loads((APP/'preferences.json').read_text(encoding='utf-8'))
except (OSError,ValueError,TypeError): PREFERENCES = dict(DEFAULTS)
VERBOSITY = PREFERENCES.get('verbosity','high')
INPUT_MODE = 'mixed'
VOICE_PICK_INDEX = None
VOICE_PICK_ORIGINAL = None
VOICE_PICK_CONFIRM = False
SILENCE = ('be quiet', 'quiet', 'stop talking', 'stop speaking', 'stop reading',
           'shut up', 'shut the fuck up', 'shut the f up', 'shut the hell up',
           'hush', 'hush up', 'silence', 'stop')
CONTINUE_SPEECH = ('resume', 'resume speaking', 'resume reading', 'keep going',
                   'keep talking', 'continue talking', 'continue speaking')
document = None
email_draft = None
pending_send = None
mail_session = None
pending_website = None
web_session = None
account_setup = False
help_session = None
help_return_mode = 'awake'
TEXT_MODE = False
note_buffer = []
note_selection = FieldSelection()
note_reading = ReadingCursor()
last_podcasts = []
MEDIA_HUB = None
MEDIA_SECTION = 'radio'
MEDIA_NOTICES = queue.Queue()
tutorial_session = None


def media():
    global MEDIA_HUB
    if MEDIA_HUB is None:
        MEDIA_HUB = MediaHub(APP, notify=MEDIA_NOTICES.put)
        MEDIA_HUB.start_scheduler()
    return MEDIA_HUB


def focus_email_entry():
    if APP_WINDOW is not None and email_draft is not None:
        field = email_draft.compose_step or 'body'
        if field=='body':return
        value = email_draft.recipient if field == 'recipient' else email_draft.subject if field == 'subject' else ''
        APP_WINDOW.focus_email_field(field, value)
resampler = None
input_channels = 1
APP_WINDOW = None
KEYBOARD_SPEECH = None
EDIT_ACK = {}
KEYBOARD_DIRTY = {}
UI_CONTEXT_CACHE = None

def app_context(mode):
    logical=SLEEP_RETURN_MODE if mode=='sleep' else mode
    context={'mode':logical,'source':None,'text':'','caret':0,'selection':None,'echo':PREFERENCES.get('typing_echo','characters'),'phonetic':PREFERENCES.get('phonetic_enabled',True),'delay':float(PREFERENCES.get('phonetic_delay','0.5')),'ack':0}
    editor=document if logical=='document' else email_draft if logical=='email_draft' and email_draft and (email_draft.compose_step or 'body')=='body' else None
    if editor is not None and (getattr(editor,'selection_candidates',[]) or getattr(editor,'replacement_candidates',[]) or getattr(editor,'pending_spacing',False)):
        return context
    if editor is not None:
        source=str(id(editor));context.update(source=source,text=document_text(editor),readonly=False,ack=EDIT_ACK.get(source,0))
        position=editor.navigation_position or editor.insertion_position or (editor.cursor,len(editor.paragraphs[editor.cursor].text) if editor.paragraphs else 0)
        context['caret']=absolute(editor,position)
        if editor.selection:
            s=editor.selection;context['selection']=(absolute(editor,(s.first_paragraph,s.first_offset)),absolute(editor,(s.last_paragraph,s.last_offset)))
    elif logical=='mailbox' and mail_session and mail_session.view=='message':
        source='mail:'+str(id(mail_session))+':'+str(mail_session.current)
        context.update(source=source,text=mail_session.body_text,readonly=True,caret=mail_session.reading.position,ack=EDIT_ACK.get(source,0))
    return context

def sync_app_context(mode,focus=False):
    global UI_CONTEXT_CACHE
    if APP_WINDOW is None:return
    context=app_context(mode)
    if KEYBOARD_SPEECH and not TEXT_MODE:
        KEYBOARD_SPEECH.configure(voice.Voice.Id,SPEECH_RATE,SPEECH_VOLUME,ESPEAK_SPEECH)
    if focus or context!=UI_CONTEXT_CACHE:
        UI_CONTEXT_CACHE=dict(context);context['focus']=focus;APP_WINDOW.set_context(context)

def apply_keyboard_edit(payload,mode):
    context=app_context(mode)
    if payload['source']!=context['source']:return False
    if payload.get('readonly'):
        if payload['after']!=context['text']:return False
        mail_session.reading.position=payload['caret'];mail_session.reading.continuation=payload['caret']
    else:
        logical=SLEEP_RETURN_MODE if mode=='sleep' else mode
        editor=document if logical=='document' else email_draft
        replace_keyboard_text(editor,payload['before'],payload['after'],payload['caret'],payload.get('selection'))
        if payload['before']!=payload['after']:KEYBOARD_DIRTY[id(editor)]=(editor,time.monotonic())
    EDIT_ACK[payload['source']]=payload['seq']
    sync_app_context(mode)
    return True

def flush_keyboard_edits(force=False):
    for key,(editor,when) in list(KEYBOARD_DIRTY.items()):
        if force or time.monotonic()-when>.4:
            try:editor.save()
            except OSError:
                KEYBOARD_DIRTY[key]=(editor,time.monotonic()+30)
                raise
            KEYBOARD_DIRTY.pop(key,None)

def handle_keyboard(text,mode):
    global SLEEP_RETURN_MODE
    if mode=='sleep' and normalized_command(text) not in ('wake up','wake companion'):
        SLEEP_RETURN_MODE=handle(text,SLEEP_RETURN_MODE,typed=True)
        if SLEEP_RETURN_MODE=='exit':return 'exit'
        sync_app_context('sleep',True)
        return 'sleep'
    return handle(text,mode,typed=True)
SLEEP_RETURN_MODE = 'awake'
MAIN_MENU_PROMPT = 'Main menu. What would you like to do? Say next or previous to move through the items, and okay, confirm, confirm that, or that one to open.'

def announce_main_menu():
    global MAIN_MENU_INDEX
    MAIN_MENU_INDEX = None
    speak_prompt(MAIN_MENU_PROMPT)

def startup_prompt():
    return 'Voice Companion ' + APP_VERSION + ' is ready. Say wake up to get started and launch the main menu. Say help for the user guide.'

MAIN_MENU_INDEX = None
MAIN_MENU_CHOICES = (('Documents', 'create a document'), ('Email', 'email'), ('Web browsing', 'search the web'), ('Radio', 'radio'), ('Podcasts', 'podcasts'), ('Notes', 'write a note'), ('Help', 'help'))


def resume_from_sleep():
    global MAIN_MENU_INDEX
    mode = SLEEP_RETURN_MODE
    if mode == 'awake':
        MAIN_MENU_INDEX = None
        announce_main_menu()
        sync_app_context(mode,True)
        return mode
    speak('I am listening. Returning to ' + {'awake':'the main menu', 'mailbox':'your email folder', 'email_draft':'your email draft', 'document':'your document'}.get(mode, mode.replace('_', ' ')) + '.')
    if mode == 'email_draft': focus_email_entry()
    sync_app_context(mode,True)
    return mode


def finish_mail_announcement():
    deadline = time.monotonic() + 30
    while speech_busy() and time.monotonic() < deadline:
        if APP_WINDOW is not None and APP_WINDOW.closed.is_set(): break
        time.sleep(0.05)


SETTINGS_ACTIONS = __import__('app_settings').ACTIONS

def save_preferences():
    from app_settings import save_preferences as save
    save(sys.modules[__name__])

def open_settings(mode):
    from app_settings import open_settings as show
    return show(sys.modules[__name__],mode)

def settings_action(key,mode):
    global MEDIA_SECTION,web_session
    if key in ('radio_presets','radio_recordings','podcast_subscriptions'):
        MEDIA_SECTION='podcast' if key=='podcast_subscriptions' else 'radio'
        speak(media().command(SETTINGS_ACTIONS[key],section=MEDIA_SECTION))
        return 'media'
    if key=='web_favorites':
        web_session=web_session or WebSession(APP)
        speak(web_session.command('list favorites'))
        return 'web'
    return handle(SETTINGS_ACTIONS[key],mode,typed=True)

def navigation_key(key,mode):
    if mode=='document_name' and key=='Escape':return 'cancel naming'
    if mode=='mailbox' and key=='Escape' and mail_session is not None and mail_session.view=='message':return 'go back'
    if mode=='settings':
        return 'cancel settings' if key=='Escape' else None
    if key=='Enter' and mode=='mailbox' and mail_session is not None and not (getattr(mail_session,'folder_picker',None) or getattr(mail_session,'folder_choice',None) or getattr(mail_session,'pending',None)):
        return 'open current message'
    editor=document if mode=='document' else email_draft if mode=='email_draft' else None
    if editor is not None and (getattr(editor,'selection_candidates',[]) or getattr(editor,'replacement_candidates',[]) or getattr(editor,'pending_spacing',False)):
        if key=='Enter':return 'ok'
        if key=='Escape':return 'cancel replacement' if getattr(editor,'replacement_candidates',[]) else 'cancel selection' if getattr(editor,'selection_candidates',[]) else 'cancel'
        if key in ('Up','Left'):return 'previous'
        if key in ('Down','Right'):return 'next'
    if mode=='awake' and key=='Escape':return 'main menu'
    return keyboard_request(key,mode)

def apply_settings(values):
    from app_settings import apply_settings as apply
    return apply(sys.modules[__name__],values)

def apply_document_defaults(document):
    from app_settings import apply_document_defaults as apply
    apply(sys.modules[__name__],document)

def microphone_settings(sd):
    device = sd.query_devices(kind='input')
    rate = int(round(device['default_samplerate']))
    for channels in (1, 2):
        if channels > device.get('max_input_channels', 1):
            continue
        try:
            sd.check_input_settings(samplerate=rate, channels=channels, dtype='int16')
            PCM16Resampler(rate)
            return device, rate, channels
        except (ValueError, OSError, getattr(sd, 'PortAudioError', OSError)):
            continue
    raise OSError('No supported mono or stereo microphone format was found.')


def spoken_control(value):
    """Only a complete utterance can put the app to sleep or shut it down."""
    command = re.sub(r'[.!?]+$', '', value.strip().lower()).strip()
    if command in EXIT:
        return 'exit'
    if command in SLEEP:
        return 'sleep'
    return None


def normalized_command(value):
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', value.casefold())).strip()


def speech_control(value):
    command = normalized_command(value)
    if command in SILENCE:
        return 'pause'
    if command in CONTINUE_SPEECH:
        return 'resume'
    return None


FAST_SILENCE = frozenset(('be quiet', 'shut up', 'shut the fuck up', 'shut the f up',
                          'shut the hell up', 'stop talking', 'stop speaking',
                          'stop reading', 'hush up'))
FAST_OFFLINE_COMMANDS = frozenset((
    'next', 'previous', 'next message', 'previous message', 'next folder', 'previous folder',
    'back to inbox', 'go to inbox', 'go to sent', 'get more emails', 'go back a list',
    'next preset', 'previous preset', 'list presets', 'read more', 'go back', 'back',
    'pause', 'play', 'resume'))



def fast_command_request(command, mode):
    if mode == 'settings': return True
    if command in ('settings','open settings','show settings','preferences','open preferences'):return True
    if re.fullmatch(r'(?:set )?verbosity (high|medium|low)',command): return True
    if mode in ('update_offer','update_download') and command in ('yes','yes please','no','no thanks','okay','ok','install it','install update','cancel','cancel update','stop update','not now','later','status','update status'): return True
    setting = speech_setting_request(command)
    # Let the dictation model refine an unparsed number before rejecting it.
    if setting is not None and setting[2] is not None: return True
    if command in FAST_OFFLINE_COMMANDS: return True
    if command in PICK_CONFIRM and mode in ('awake','media','web','mailbox','help'): return True
    if command in PICK_CONFIRM and mode in ('document','email_draft') and INPUT_MODE != 'dictation':
        editor = document if mode == 'document' else email_draft
        if editor is not None and (getattr(editor,'replacement_candidates',None) or getattr(editor,'selection_candidates',None)): return True
    if command in VOICE_LIST_COMMANDS or command in ('select voice', 'choose voice'): return True
    if SYNTH_PICK_INDEX is not None or VOICE_PICK_INDEX is not None:
        return command in PICK_CONFIRM or command in ('next voice', 'previous voice', 'next synthesizer',
            'previous synthesizer', 'cancel voice', 'cancel synthesizer')
    if command in ('speak faster', 'speak slower', 'faster', 'slower', 'speak louder', 'speak quieter',
                   'louder', 'quieter', 'use windows voice', 'use ai voice', 'use espeak'):
        return True
    if mode in ('document', 'email_draft') and INPUT_MODE != 'dictation':
        from document_editor import START_COMMANDS, END_COMMANDS, FORMAT_READ_COMMANDS, document_navigation_request
        return bool(document_navigation_request(command)) or command in START_COMMANDS or command in END_COMMANDS or command in FORMAT_READ_COMMANDS or structural_selection_request(command)
    return False

def fast_offline_silence(recognizer, utterance):
    """Act on an unambiguous partial phrase before endpoint silence expires."""
    try:
        partial = normalized_command(json.loads(recognizer.PartialResult()).get('partial', ''))
    except (ValueError, AttributeError):
        return False
    if partial not in FAST_SILENCE:
        return False
    control_speech('pause')
    recognizer.Reset()
    utterance.clear()
    return True


def interrupt_speech():
    global SPEECH_PAUSED
    if APP_WINDOW is not None:APP_WINDOW.ui_actions.put(('cancel_phonetic',None))
    if KEYBOARD_SPEECH is not None:KEYBOARD_SPEECH.interrupt()
    if ESPEAK_SPEECH is not None:
        ESPEAK_SPEECH.interrupt()
    if AI_SPEECH is not None:
        AI_SPEECH.interrupt()
    if TEXT_MODE or 'voice' not in globals():
        SPEECH_PAUSED = False
        return
    if SPEECH_PAUSED:
        voice.Resume()
        SPEECH_PAUSED = False
    voice.Speak('', 3)  # SAPI async + purge: discard the old spoken request.


def control_speech(action):
    global SPEECH_PAUSED
    if action=='pause' and APP_WINDOW is not None:APP_WINDOW.ui_actions.put(('cancel_phonetic',None))
    if KEYBOARD_SPEECH is not None:KEYBOARD_SPEECH.control(action)
    if ESPEAK_SPEECH is not None:
        (ESPEAK_SPEECH.pause if action == 'pause' else ESPEAK_SPEECH.resume)()
    if AI_SPEECH is not None:
        (AI_SPEECH.pause if action == 'pause' else AI_SPEECH.resume)()
    if TEXT_MODE or 'voice' not in globals():
        SPEECH_PAUSED = action == 'pause'
        return
    if action == 'pause' and not SPEECH_PAUSED:
        voice.Pause()  # SAPI preserves the precise playback position.
        SPEECH_PAUSED = True
    elif action == 'resume' and SPEECH_PAUSED:
        voice.Resume()
        SPEECH_PAUSED = False


def punctuation_for_speech(text):
    if PUNCTUATION_LEVEL == 'some':
        return text
    names = {',': ' comma ', '.': ' period ', ':': ' colon ', ';': ' semicolon ',
             '?': ' question mark ', '!': ' exclamation mark ',
             '(': ' open parenthesis ', ')': ' close parenthesis ',
             '"': ' quotation mark ', '@': ' at sign ', '#': ' hash ',
             '/': ' slash ', '-': ' dash '}
    if PUNCTUATION_LEVEL == 'most':
        return re.sub(r'[:,;?!()\"@#/-]',lambda match:names[match[0]],text)
    if PUNCTUATION_LEVEL == 'none':
        return re.sub(r'[,.:;?!()"#]', ' ', text)
    return re.sub(r'[,.:;?!()"@#/-]', lambda match: names[match[0]], text)


def speech_busy():
    if TEXT_MODE or SPEECH_PAUSED:
        return SPEECH_PAUSED
    if KEYBOARD_SPEECH is not None and KEYBOARD_SPEECH.busy():
        return True
    if ESPEAK_SPEECH is not None and ESPEAK_SPEECH.busy():
        return True
    if AI_SPEECH is not None and AI_SPEECH.busy():
        return True
    try:
        return not bool(voice.WaitUntilDone(0))
    except Exception:
        return False



def wait_for_speech(timeout_ms):
    # Startup/diagnostics must wait for the selected local or online player too.
    deadline = time.monotonic() + timeout_ms / 1000
    while speech_busy():
        if time.monotonic() >= deadline: return False
        time.sleep(0.01)
    return True

def update_audio_ducking(active=None):
    if MEDIA_HUB is None:
        return
    player = MEDIA_HUB.player
    try:
        player.set_ducked(PREFERENCES.get('duck_audio',True) and ((not SPEECH_PAUSED and speech_busy()) if active is None else active))
    except Exception as exc:
        print('Audio ducking unavailable:', exc, file=sys.stderr)


def save_speech_settings():
    APP.mkdir(parents=True, exist_ok=True)
    (APP / 'speech-settings.json').write_text(
        json.dumps({'rate': SPEECH_RATE, 'volume': SPEECH_VOLUME, 'punctuation': PUNCTUATION_LEVEL, 'verbosity':VERBOSITY, 'input_mode':INPUT_MODE,
                    'ai_enabled': bool(AI_SPEECH and AI_SPEECH.enabled), 'ai_voice': AI_VOICE_NAME,
                    'espeak_enabled': bool(ESPEAK_SPEECH and ESPEAK_SPEECH.enabled), 'espeak_voice': ESPEAK_VOICE_NAME,
                    'voice': voice.Voice.GetDescription() if not TEXT_MODE else ''}), encoding='utf-8')



def change_speech_setting(command):
    global SPEECH_RATE, SPEECH_VOLUME
    request = speech_setting_request(command)
    if request is None: return False
    kind, operation, amount = request
    lower, upper = (-10, 10) if kind == 'rate' else (0, 100)
    if amount is None or (operation == 'set' and not lower <= amount <= upper):
        speak('Speech rate must be a whole number from minus ten to ten.' if kind == 'rate' else
              'Speech volume must be a whole number from zero to one hundred percent.')
        return True
    current = SPEECH_RATE if kind == 'rate' else SPEECH_VOLUME
    value = max(lower, min(upper, current + amount if operation == 'delta' else amount))
    if kind == 'rate':
        SPEECH_RATE = value
        if not TEXT_MODE: voice.Rate = value
    else:
        SPEECH_VOLUME = value
        if not TEXT_MODE: voice.Volume = value
    save_speech_settings()
    speak('Speech rate ' + str(value) + '.' if kind == 'rate' else 'Volume ' + str(value) + ' percent.')
    return True

def voice_choices(engine=None):
    windows = voice.GetVoices()
    choices = [('windows', windows.Item(i).GetDescription(), windows.Item(i))
               for i in range(windows.Count)]
    if ESPEAK_SPEECH is not None:
        from espeak_speech import VOICES as ESPEAK_VOICES
        choices += [('espeak', 'eSpeak ' + name, code) for name, code in ESPEAK_VOICES]
    if AI_SPEECH is not None:
        from ai_voice_settings import VOICES
        choices += [('ai', 'AI voice ' + name.title(), name) for name in VOICES]
    return [choice for choice in choices if engine is None or choice[0] == engine]


def choose_voice(choice):
    global AI_VOICE_NAME, ESPEAK_VOICE_NAME
    interrupt_speech()
    kind, name, value = choice
    if ESPEAK_SPEECH is not None: ESPEAK_SPEECH.enabled = kind == 'espeak'
    if AI_SPEECH is not None: AI_SPEECH.enabled = kind == 'ai'
    if kind == 'ai': AI_VOICE_NAME = value
    elif kind == 'espeak': ESPEAK_VOICE_NAME = value
    else: voice.Voice = value
    return name


def synthesizers():
    choices = [('windows', 'Windows speech')]
    if ESPEAK_SPEECH is not None: choices.append(('espeak', 'eSpeak NG'))
    if AI_SPEECH is not None: choices.append(('ai', 'AI voice'))
    return choices


def preview_voice(index):
    choices = voice_choices(VOICE_PICK_ENGINE)
    name = choose_voice(choices[index])
    speak(name + ', ' + str(index + 1) + ' of ' + str(len(choices)) + '.')


def keep_voice():
    global VOICE_PICK_INDEX, VOICE_PICK_ORIGINAL, VOICE_PICK_CONFIRM, VOICE_PICK_ENGINE
    name = choose_voice(voice_choices(VOICE_PICK_ENGINE)[VOICE_PICK_INDEX])
    VOICE_PICK_INDEX = VOICE_PICK_ORIGINAL = VOICE_PICK_ENGINE = None
    VOICE_PICK_CONFIRM = False
    save_speech_settings()
    speak('Using ' + name + '.')


VOICE_LIST_COMMANDS = frozenset(('list voices', 'voice list', 'voices list', 'list voice',
    'show voices', 'available voices', 'what voices are available', 'what voices do you have',
    'which voices do you have', 'select synthesizer', 'select synthesiser', 'choose synthesizer',
    'list synthesizers', 'synthesizer list'))
PICK_CONFIRM = frozenset(('that one', 'confirm that', 'okay', 'ok', 'use that voice',
                         'use this voice', 'use that synthesizer', 'confirm', 'yes', 'yes please'))


def voice_menu(command):
    global SYNTH_PICK_INDEX, VOICE_PICK_ENGINE, VOICE_PICK_INDEX, VOICE_PICK_ORIGINAL
    global VOICE_PICK_CONFIRM, AI_VOICE_NAME, ESPEAK_VOICE_NAME
    opening = command in VOICE_LIST_COMMANDS or command in ('select voice', 'choose voice')
    active = SYNTH_PICK_INDEX is not None or VOICE_PICK_INDEX is not None
    if not opening and not active: return False
    if not opening and command not in PICK_CONFIRM and command not in (
            'next', 'previous', 'next voice', 'previous voice', 'next synthesizer',
            'previous synthesizer', 'cancel', 'cancel voice', 'cancel synthesizer', 'back',
            'no', 'no thanks'): return False
    if TEXT_MODE:
        speak('Voice selection requires Windows.')
        return True
    if opening:
        if VOICE_PICK_ORIGINAL is None:
            VOICE_PICK_ORIGINAL = (voice.Voice, bool(AI_SPEECH and AI_SPEECH.enabled), AI_VOICE_NAME,
                                  bool(ESPEAK_SPEECH and ESPEAK_SPEECH.enabled), ESPEAK_VOICE_NAME)
        VOICE_PICK_CONFIRM = False
        VOICE_PICK_INDEX = VOICE_PICK_ENGINE = None
        SYNTH_PICK_INDEX = 0
        if command in ('select voice', 'choose voice'):
            kind = 'espeak' if ESPEAK_SPEECH and ESPEAK_SPEECH.enabled else 'ai' if AI_SPEECH and AI_SPEECH.enabled else 'windows'
            VOICE_PICK_ENGINE = kind
            SYNTH_PICK_INDEX = None
            VOICE_PICK_INDEX = 0
            preview_voice(0)
            return True
    elif command in ('cancel', 'cancel voice', 'cancel synthesizer'):
        interrupt_speech()
        if VOICE_PICK_ORIGINAL:
            original, ai_enabled, ai_name, espeak_enabled, espeak_name = VOICE_PICK_ORIGINAL
            voice.Voice = original
            AI_VOICE_NAME, ESPEAK_VOICE_NAME = ai_name, espeak_name
            if AI_SPEECH is not None: AI_SPEECH.enabled = ai_enabled
            if ESPEAK_SPEECH is not None: ESPEAK_SPEECH.enabled = espeak_enabled
        SYNTH_PICK_INDEX = VOICE_PICK_INDEX = VOICE_PICK_ORIGINAL = VOICE_PICK_ENGINE = None
        VOICE_PICK_CONFIRM = False
        speak('Voice choice canceled.')
        return True
    elif command == 'back' and SYNTH_PICK_INDEX is None:
        SYNTH_PICK_INDEX = 0
        VOICE_PICK_INDEX = VOICE_PICK_ENGINE = None
    elif command in ('no', 'no thanks'):
        speak('Say next, previous, okay, or cancel voice.')
        return True
    elif command in PICK_CONFIRM:
        if SYNTH_PICK_INDEX is not None:
            VOICE_PICK_ENGINE = synthesizers()[SYNTH_PICK_INDEX][0]
            SYNTH_PICK_INDEX = None
            VOICE_PICK_INDEX = 0
            preview_voice(0)
        else:
            keep_voice()
        return True
    elif command.startswith(('next', 'previous')):
        step = 1 if command.startswith('next') else -1
        if SYNTH_PICK_INDEX is not None:
            SYNTH_PICK_INDEX = (SYNTH_PICK_INDEX + step) % len(synthesizers())
        else:
            VOICE_PICK_INDEX = (VOICE_PICK_INDEX + step) % len(voice_choices(VOICE_PICK_ENGINE))
            preview_voice(VOICE_PICK_INDEX)
            return True
    choices = synthesizers()
    prompt = choices[SYNTH_PICK_INDEX][1] + ', ' + str(SYNTH_PICK_INDEX + 1) + ' of ' + str(len(choices)) + '.'
    if command in VOICE_LIST_COMMANDS or command == 'back':
        prompt = 'Select synthesizer. ' + prompt + ' Say next, previous, okay, or cancel voice.'
    speak(prompt)
    return True


def record_espeak_error(exc):
    # Do not write dictated text, command input, subprocess output or credentials.
    import subprocess
    kind = 'speech generation' if isinstance(exc, (subprocess.SubprocessError, OSError, ValueError)) else 'audio playback'
    code = getattr(exc, 'returncode', None)
    APP.mkdir(parents=True, exist_ok=True)
    (APP / 'espeak-error.txt').write_text(
        'eSpeak failure: ' + kind + '\nError type: ' + type(exc).__name__ +
        '\nProcess exit code: ' + str(code) +
        '\nCheck eSpeak setup output and Microsoft Visual C++ x64 runtime.\n', encoding='utf-8')


def configure_espeak_speech():
    global ESPEAK_SPEECH
    from espeak_speech import executable, synthesize
    program = executable()
    if program is None: return False
    from ai_speech import AISpeech
    def fallback():
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        return win32com.client.Dispatch('SAPI.SpVoice')
    ESPEAK_SPEECH = AISpeech(fallback, lambda text, rate: synthesize(text, rate,
        voice=ESPEAK_VOICE_NAME, program=program), engine_name='eSpeak', error_callback=record_espeak_error)
    return True


def configure_ai_speech():
    global AI_SPEECH, AI_VOICE_NAME
    from ai_voice_settings import load
    from ai_speech import AISpeech, synthesize
    settings = load(APP)
    if not settings.get('key'):
        return False
    if AI_SPEECH is None:
        def fallback():
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            return win32com.client.Dispatch('SAPI.SpVoice')
        AI_SPEECH = AISpeech(fallback)
    AI_VOICE_NAME = settings.get('voice', 'coral')
    AI_SPEECH.synthesizer = lambda text, rate: synthesize(text, rate,
        key=settings['key'], voice=AI_VOICE_NAME)
    return True


def speak_prompt(text):
    speak(prompt_text(text,VERBOSITY))


def speak(text):
    print('Companion:', text, flush=True)
    if APP_WINDOW is not None:
        APP_WINDOW.show(text)
    if TEXT_MODE:
        return
    update_audio_ducking(True)
    if ESPEAK_SPEECH is not None and ESPEAK_SPEECH.enabled:
        ESPEAK_SPEECH.speak(punctuation_for_speech(text), SPEECH_RATE, SPEECH_VOLUME)
        return
    if AI_SPEECH is not None and AI_SPEECH.enabled:
        AI_SPEECH.speak(punctuation_for_speech(text), SPEECH_RATE, SPEECH_VOLUME)
        return
    voice.Speak(punctuation_for_speech(text), 1)  # Async: microphone stays responsive.


def speak_keyboard_feedback(text):
    # Character echo must be immediate and must not wait for an API request.
    if APP_WINDOW is not None: APP_WINDOW.show(text)
    if not TEXT_MODE:
        update_audio_ducking(True)
        if ESPEAK_SPEECH is not None and ESPEAK_SPEECH.enabled:
            ESPEAK_SPEECH.speak(str(text), SPEECH_RATE, SPEECH_VOLUME)
        else: voice.Speak(str(text), 1)


def mail_progress(text):
    speak(text)


def callback(indata, frames, time_info, status):
    global AUDIO_OVERFLOW
    if status:
        print(status, file=sys.stderr)
    if time.monotonic() >= MUTE_UNTIL:
        incoming = stereo_to_mono(bytes(indata)) if input_channels == 2 else bytes(indata)
        converted = resampler.convert(incoming) if resampler else incoming
        if converted:
            try:
                AUDIO.put_nowait(converted)
            except queue.Full:
                AUDIO_OVERFLOW = True


def recover_audio_overflow(recognizer, utterance):
    """Discard incomplete speech instead of acting on delayed, partial commands."""
    global AUDIO_OVERFLOW
    if not AUDIO_OVERFLOW:
        return False
    AUDIO_OVERFLOW = False
    while True:
        try:
            AUDIO.get_nowait()
        except queue.Empty:
            break
    recognizer.Reset()
    utterance.clear()
    speak('I fell behind and missed some words. Please repeat your last request.')
    return True


def save_note(text):
    APP.mkdir(parents=True, exist_ok=True)
    path = APP / 'notes.txt'
    with path.open('a', encoding='utf-8') as f:
        f.write(f'\n[{datetime.now():%Y-%m-%d %H:%M}] {text}\n')
    speak('Saved your note.')


def flush_note(mode):
    if mode == 'note' and note_buffer:
        save_note(' '.join(note_buffer))
        note_buffer.clear()
        note_selection.text = None


def podcast_search(topic):
    if not topic:
        speak('What podcast topic would you like?')
        return 'podcast'
    url = 'https://itunes.apple.com/search?media=podcast&entity=podcast&limit=5&term=' + urllib.parse.quote(topic)
    try:
        with urllib.request.urlopen(url, timeout=12) as response:
            shows = json.load(response).get('results', [])
        if not shows:
            speak('I found no podcasts for ' + topic)
        else:
            names = ', '.join(p.get('collectionName', 'untitled') for p in shows[:3])
            speak('I found: ' + names + '. Say open first podcast to open the first result.')
            global last_podcasts
            last_podcasts = shows
    except (OSError, ValueError):
        speak('Podcast search could not connect. Please try later.')
    return 'awake'


def _handle(text, mode, typed=False):
    global SYNTH_PICK_INDEX, VOICE_PICK_ENGINE, MAIN_MENU_INDEX
    global SLEEP_RETURN_MODE, document, email_draft, pending_website, pending_send, mail_session, web_session, account_setup, INPUT_MODE, VOICE_PICK_INDEX, VOICE_PICK_ORIGINAL, VOICE_PICK_CONFIRM, MEDIA_SECTION, help_session, help_return_mode, tutorial_session, AI_VOICE_NAME
    text = text.strip()
    global UPDATE_MANUAL, UPDATE_LAST_PERCENT, SETTINGS_PANEL, SETTINGS_RETURN_MODE, VERBOSITY
    if mode=='document_name' and not spoken_control(normalized_command(text)) and normalized_command(text) not in ('settings','open settings'):
        if normalized_command(text) in ('cancel','cancel naming','go back'):
            speak('Naming canceled. Your document is still open.');return 'document'
        title=re.sub(r'^(?:name document|save document as|save as)\s+','',text.strip(),flags=re.I)
        result=document.process('name document '+title)
        speak(result)
        if result.startswith('Document named '):
            return 'awake'
        return 'document_name'
    if mode == 'settings' and SETTINGS_PANEL is not None and not SETTINGS_PANEL.closed.is_set():
        control=speech_control(text)
        if control:
            control_speech(control);return mode
        if spoken_control(text) in ('exit','sleep'):
            SETTINGS_PANEL.cancel()
            return handle(text,SETTINGS_RETURN_MODE,typed)
        SETTINGS_PANEL.command(text)
        return mode
    if normalized_command(text) in ('settings','open settings','show settings','preferences','open preferences'):
        SETTINGS_RETURN_MODE = mode
        return open_settings(mode)
    verbosity_request = re.fullmatch(r'(?:set )?verbosity(?: to)? (high|medium|low)',normalized_command(text))
    if verbosity_request:
        VERBOSITY = verbosity_request[1]
        PREFERENCES['verbosity'] = VERBOSITY
        save_preferences(); save_speech_settings()
        speak('Verbosity '+VERBOSITY+'.')
        return mode
    update_command = normalized_command(text)
    if re.match(r'^(?:set )?(?:typing echo|phonetics|phonetic pronunciation|phonetic enabled|delayed phonetic pronunciation|phonetic delay)\b.+',update_command):
        session=SettingsSession(PREFERENCES,{})
        request=session.voice_setting(text)
        if request and request[0] in ('typing_echo','phonetic_enabled','phonetic_delay'):
            try:
                message=session.set(*request)
                PREFERENCES[request[0]]=session.values[request[0]]
                save_preferences();speak(message+'.')
            except (ValueError,OSError) as exc:speak(str(exc))
            return mode
    if mode=='awake' and update_command in ('main menu','back to main menu'):
        announce_main_menu()
        return mode
    if re.match(r'^(?:set )?(?:typing echo|phonetics|phonetic pronunciation|phonetic enabled|delayed phonetic pronunciation|phonetic delay|audio ducking|duck audio|automatic updates|check updates|document font|document size|document spacing|document alignment|default font|default line spacing|default alignment|browser|input mode|email list size|sending account|station database|podcast limit)\b.+',update_command):
        from app_settings import snapshot
        values,context=snapshot(sys.modules[__name__])
        session=SettingsSession(values,context)
        request=session.voice_setting(text)
        if request:
            try:
                message=session.set(*request)
                apply_settings(session.values)
                speak(message+'.')
            except (ValueError,OSError) as exc:speak(str(exc))
            return mode
    if update_command in ('check for updates', 'check updates', 'update voice companion'):
        if UPDATES is None:
            speak('Update checking is available in the installed app.')
        elif UPDATES.busy:
            speak('An update check or download is already in progress.')
        else:
            UPDATE_MANUAL = True
            if UPDATES.check(manual=True): speak('Checking for updates.')
        return mode
    if update_command in ('status', 'update status') and mode == 'update_download':
        speak(str(UPDATES.percent) + '%')
        return mode
    if mode == 'update_offer':
        if update_command in ('no', 'no thanks', 'not now', 'later', 'cancel'):
            if not STARTUP_UPDATE_PENDING: speak('Okay. You can keep using this version.')
            return return_from_update()
        if update_command in ('yes', 'yes please', 'install it', 'install update', 'okay', 'ok'):
            if not UPDATES.can_install():
                speak('Automatic installation works in the installed app. Quick Test can check for updates but cannot replace an installation.')
                return return_from_update()
            try:
                if document is not None: document.save()
                if email_draft is not None: email_draft.save()
                flush_note(UPDATE_RETURN_MODE)
            except (OSError, ValueError):
                speak('I could not save your work. The update will wait.')
                return return_from_update()
            UPDATE_LAST_PERCENT = 0
            if UPDATES.download():
                speak('Downloading the update. Say cancel update to stop.')
                return 'update_download'
            speak('The updater is busy. Please try again shortly.')
            return return_from_update()
        if spoken_control(update_command) != 'exit':
            speak('Say yes to install the update, or no to continue.')
            return mode
    if mode == 'update_download' and spoken_control(update_command) != 'exit':
        if update_command in ('cancel', 'cancel update', 'stop update', 'no'):
            UPDATES.canceled.set()
            speak('Update canceled. You can keep using the app.')
            return return_from_update()
        else: pass  # The cancel reminder is announced once when downloading begins.
        return mode
    if normalized_command(text) in ('set up ai voice', 'setup ai voice', 'ai voice setup'):
        if APP_WINDOW is not None:
            APP_WINDOW.setup_ai_voice()
            speak('AI voice setup is open for your trainer. The key is hidden. Use Tab to move between fields.')
        else:
            speak('AI voice setup requires the app window.')
        return mode
    if normalized_command(text) in ('use ai voice', 'use windows voice', 'use espeak', 'use espeak voice', 'use e speak'):
        requested = normalized_command(text)
        kind = 'windows' if requested == 'use windows voice' else 'ai' if requested == 'use ai voice' else 'espeak'
        if TEXT_MODE:
            choices = []
        elif kind == 'windows':
            choices = [('windows', voice.Voice.GetDescription(), voice.Voice)]
        elif kind == 'ai' and AI_SPEECH is not None:
            choices = [('ai', 'AI voice ' + AI_VOICE_NAME.title(), AI_VOICE_NAME)]
        elif kind == 'espeak' and ESPEAK_SPEECH is not None:
            from espeak_speech import ALL_VOICES
            choices = [('espeak', 'eSpeak ' + name, code) for name, code in ALL_VOICES if code == ESPEAK_VOICE_NAME]
        else: choices = []
        if choices:
            choose_voice(choices[0])
            SYNTH_PICK_INDEX = VOICE_PICK_INDEX = VOICE_PICK_ORIGINAL = VOICE_PICK_ENGINE = None
            save_speech_settings()
            speak('Using ' + choices[0][1] + '.')
        else:
            speak('That synthesizer is not configured. Ask your trainer to check setup.')
        return mode
    bare_audio_control = normalized_command(text) in ('stop','resume') and MEDIA_HUB is not None and bool(MEDIA_HUB.player.kind)
    control = None if bare_audio_control else speech_control(text)
    if control:
        control_speech(control)
        return mode
    if text:
        interrupt_speech()
        print('Heard:', text, flush=True)
        if APP_WINDOW is not None: APP_WINDOW.show('Heard: ' + text)
    command = re.sub(r'[.!?]+$', '', text.lower()).strip().replace('auto-download','auto download')
    if change_speech_setting(command): return mode
    if command in ('open user guides', 'open user guides folder', 'open guides', 'open guides folder',
                   'show user guides', 'where are the user guides', 'where are my user guides',
                   'where is the user guide folder'):
        try:
            from guide_files import publish_guides
            folder = publish_guides(documents_folder())
            if command.startswith(('open ', 'show ')) and os.name == 'nt':
                os.startfile(str(folder))
            speak('All five user guide formats are in Documents, Voice Companion, User Guides. The full location is ' + str(folder) + '.')
        except OSError:
            speak('The user guide folder could not open or update. Say help for onboard instructions and ask your helper to check the installation.')
        return mode
    if voice_menu(command): return mode
    if mode == 'tutorial':
        if spoken_control(command) == 'exit':
            tutorial_session = None
            return handle(text, 'awake', typed)
        if spoken_control(command) == 'sleep':
            SLEEP_RETURN_MODE = mode
            speak('Going to sleep. Say wake up to continue here.')
            return 'sleep'
        if tutorial_session is None:
            tutorial_session = PracticeTutorial()
            speak(tutorial_session.start())
            return 'tutorial'
        result, closed = tutorial_session.process(text)
        speak(result)
        if closed:
            tutorial_session = None
            return 'awake'
        return 'tutorial'
    if command in ('start tutorial', 'audio tutorial', 'practice tutorial', 'open tutorial'):
        if mode not in ('awake', 'help'):
            speak('Say main menu first, then start tutorial. This protects what you are working on.')
            return mode
        help_session = None
        tutorial_session = PracticeTutorial()
        speak(tutorial_session.start())
        return 'tutorial'
    if mode == 'help' and not spoken_control(command) and command not in (
            'faster', 'slower', 'speak faster', 'speak slower', 'louder', 'quieter',
            'list voices', 'next voice', 'previous voice'):
        if command in ('main menu', 'back to main menu'):
            returning = help_return_mode
            help_session = None
            if returning == 'awake':
                pass
                return 'awake'
            return handle('main menu', returning)
        if help_session is None:
            speak_prompt('The user guide could not open. Say help to try again.')
            return help_return_mode
        result = help_session.process('back to topics' if command in (
            'help', 'user guide', 'open user guide', 'manual', 'open manual') else text)
        speak(result)
        if help_session.closed:
            help_session = None
            return help_return_mode
        return 'help'
    global SPEECH_RATE, SPEECH_VOLUME, PUNCTUATION_LEVEL
    if command in ('check voices', 'voice check', 'voice diagnostics'):
        if TEXT_MODE:
            speak('Voice checking requires Windows speech output.')
        else:
            from voice_inventory import report
            try:
                names, path = report(voice, APP / 'voice-check.txt')
                speak(str(len(names)) + ' usable SAPI 5 voices found. A voice check was saved in your Voice Companion data folder. ' +
                      'Ask your helper to open voice-check.txt there for details.')
            except (OSError, AttributeError) as exc:
                speak('Voice check could not be saved: ' + str(exc))
        return mode
    if command.startswith(('use voice ', 'change voice to ', 'set voice to ')):
        name = re.sub(r'^(?:use voice|change voice to|set voice to)\s+', '', command)
        if TEXT_MODE: speak('Voice selection requires Windows.')
        else:
            choices = voice_choices()
            matches = [item for item in choices if name == item[1].casefold()]
            if not matches:
                matches = [item for item in choices if name in item[1].casefold()]
            if len(matches) == 1:
                chosen = choose_voice(matches[0])
                SYNTH_PICK_INDEX = VOICE_PICK_ENGINE = VOICE_PICK_INDEX = VOICE_PICK_ORIGINAL = None
                VOICE_PICK_CONFIRM = False
                save_speech_settings()
                speak('Using ' + chosen + '.')
            else: speak_prompt('Voice name was not unique. Say list voices and try the full name.')
        return mode
    punctuation = re.fullmatch(r'(?:punctuation|set punctuation to) (none|some|most|all)', command)
    if punctuation:
        PUNCTUATION_LEVEL = punctuation[1]
        save_speech_settings()
        speak('Punctuation ' + PUNCTUATION_LEVEL + '.')
        return mode
    if command in ('version', 'what version', 'about voice companion'):
        speak('Voice Companion version ' + APP_VERSION + '. This is the self-voicing email and web test build.')
        return mode
    if spoken_control(command) == 'exit':
        help_session = None
        account_setup = False
        pending_send = None
        mail_session = None
        flush_note(mode)
        if MEDIA_HUB is not None: MEDIA_HUB.player.close()
        speak('Shutting down. Goodbye.')
        return 'exit'
    if spoken_control(command) == 'sleep':
        SLEEP_RETURN_MODE = mode
        pending_send = None
        if mode == 'email_draft' and email_draft is not None: email_draft.save()
        if mode == 'document' and document is not None: document.save()
        if mode == 'note': flush_note(mode)
        pending_website = None
        if MEDIA_HUB is not None and MEDIA_HUB.player.kind:
            try: MEDIA_HUB.player.control('pause')
            except Exception: pass
        speak('Going to sleep. Say wake up when you need me.')
        return 'sleep'
    help_topic = re.fullmatch(r'(?:help with|help for|open help topic|go to help topic) (.+)', command)
    help_alias = {'email help': 'writing email' if mode == 'email_draft' else 'email',
                  'document help': 'documents', 'web help': 'web',
                  'radio help': 'radio', 'podcast help': 'podcasts',
                  'commands help': 'commands'}
    if command in ('help', 'user guide', 'open user guide', 'manual', 'open manual') or help_topic:
        try:
            if mode != 'help' or help_session is None:
                help_return_mode = mode
                help_session = HelpSession(GUIDE)
            topic_name = ('writing email' if help_topic and help_topic[1] == 'email' and mode == 'email_draft'
                          else help_topic[1] if help_topic else None)
            result = help_session.topic(topic_name) if topic_name else help_session.process('back to topics')
            speak(('User guide. ' if mode != 'help' else '') + result)
            return 'help'
        except (OSError, ValueError):
            speak('The user guide could not open. Ask your helper to reinstall Voice Companion.')
            return help_return_mode if mode == 'help' else mode
    if command in help_alias:
        try:
            help_return_mode = mode
            help_session = HelpSession(GUIDE)
            speak(help_session.topic(help_alias[command]))
            return 'help'
        except (OSError, ValueError):
            speak('The user guide could not open. Ask your helper to reinstall Voice Companion.')
            return mode
    requested_mode = {
        'dictation mode':'dictation', 'dictation only':'dictation', 'switch to dictation mode':'dictation',
        'commands mode':'commands', 'command mode':'commands', 'commands only':'commands',
        'switch to command mode':'commands', 'switch to commands mode':'commands',
        'mixed mode':'mixed', 'normal mode':'mixed', 'commands and dictation':'mixed',
        'dictation and commands':'mixed', 'switch to mixed mode':'mixed',
    }.get(command)
    if requested_mode:
        INPUT_MODE = requested_mode
        save_speech_settings()
        if document is not None: document.dictating = requested_mode != 'commands'
        if email_draft is not None:
            email_draft.dictating = requested_mode != 'commands'
            email_draft.automatic_dictation = requested_mode != 'commands'
        if web_session is not None: web_session.input_mode = requested_mode
        speak({'mixed':'Normal mode. Commands and ordinary dictation are available.',
               'commands':'Commands only. Ordinary speech will not be added as text.',
               'dictation':'Dictation only. Speech goes into the current document, email, or focused web text field.'}[requested_mode])
        return mode
    if command in ('what mode', 'which mode', 'what input mode'):
        speak('Input mode: ' + INPUT_MODE + '.')
        return mode
    if command in ('subscription list', 'subscriptions list', 'podcast list', 'podcasts list',
                   'list podcast', 'list podcasts', 'list subscriptions', 'my podcasts'):
        MEDIA_SECTION = 'podcast'
        speak(media().command(command, section='podcast'))
        return 'media'
    if command in ('where are my documents', 'where do documents save', 'open documents',
                   'open documents folder', 'show documents folder'):
        folder = documents_folder()
        folder.mkdir(parents=True, exist_ok=True)
        if command.startswith(('open ', 'show ')) and os.name == 'nt':
            os.startfile(str(folder))
        speak('Your Word documents are in Documents, Voice Companion. The full location is ' + str(folder) + '.')
        return mode
    if mode == 'email_draft' and email_draft is not None and (
            command in ('cancel', 'cancel draft', 'cancel email', 'cancel message',
                        'cancel this draft', 'cancel this email', 'cancel this message',
                        'cancel reply', 'cancel forward', 'discard draft', 'discard email',
                        'discard this draft', 'discard this email', 'discard message',
                        'back', 'go back', 'inbox', 'discard', 'never mind', 'nevermind',
                        'cancel new email', 'cancel this reply', 'cancel this forward',
                        'cancel reply draft', 'cancel forward draft', 'cancel forwarding') or
            re.fullmatch(r'(?:go|go back|back|return|take me)(?: to)? (?:the |my )?inbox', command)):
        pending_send = None
        try:
            discarded = email_draft.discard()
        except OSError:
            speak('I could not remove the local draft files. The draft is still open; ask your helper before trying again.')
            return mode
        provider = email_draft.provider
        email_draft = None
        if APP_WINDOW is not None: APP_WINDOW.clear_email_field()
        speak('Draft canceled without saving.' if discarded else
              'Draft closed without another save. A previous send attempt record remains; check your mailbox before sending again.')
        finish_mail_announcement()
        if mail_session is None and provider in ('gmail', 'outlook', 'yahoo'):
            mail_session = MailSession(provider, APP, progress=mail_progress)
        if mail_session is not None:
            speak(mail_session.process('open inbox'))
            return 'mailbox'
        speak('Back at the main menu. No email account is connected.')
        return 'awake'
    if mode == 'email_draft' and email_draft is not None and email_field_destination(command):
        pending_send = None
        speak(email_draft.process(text))
        focus_email_entry()
        return mode
    if mode == 'mailbox' and mail_session is not None and (
            command in ('that one','okay','ok','confirm','confirm that','next message','previous message','next','previous',
                        'back to inbox','return to inbox','go to inbox','open inbox', 'email list size',
                        'mail list size','how many emails','how many messages per page',
                        'next list','next page','next messages','get more emails','more emails','show more emails','show more messages','get more messages',
                        'previous list','previous page','previous messages','go back a list','back a list','back one list','earlier emails') or
            command.startswith(('go to ', 'open folder ', 'set list to ', 'set to ', 'change list to ',
                                'set email list size ', 'change email list size ',
                                'set mail list size ', 'set message list size ', 'set page size '))):
        speak(mail_session.process(text))
        return mode
    if mode != 'web' and (command in ('list presets', 'radio presets', 'my presets', 'preset list',
                    'favorites list', 'favorite list', 'list radio favorites',
                    'next preset', 'previous preset', 'save preset', 'add preset',
                    'save favorite', 'add favorite',
                    'play preset', 'open preset', 'delete preset', 'remove preset') or
            command.startswith(('save preset as ', 'add preset as ',
                                'save favorite as ', 'add favorite as ', 'play preset ',
                                'open preset ', 'delete preset ', 'remove preset '))):
        MEDIA_SECTION = 'radio'
        speak(media().command(text, section='radio'))
        return 'media'
    if command == 'list favorites' and mode == 'media' and MEDIA_SECTION == 'radio':
        speak(media().command(text, section='radio'))
        return 'media'
    if MEDIA_HUB is not None and ((command in ('pause','stop','play','resume') and (MEDIA_HUB.player.kind or mode == 'media')) or re.fullmatch(
            r'(?:play|resume|pause|stop) (?:radio|podcast|audio)',command) or
            re.fullmatch(r'(?:rewind|back|go back|fast forward|forward|skip ahead) (?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|forty|fifty|sixty)\b.*(?:seconds?|minutes?|hours?)',command) or
            (mode != 'media' and command in ('next chapter','previous chapter','next episode','previous episode',
                        'next podcast','previous podcast','next show','previous show'))):
        speak(MEDIA_HUB.command(text))
        return mode
    if mode == 'media':
        if command in ('exit','main menu','back to main menu','leave radio','leave podcasts'):
            speak('Scheduled recordings continue while the app runs.')
            return 'awake'
        if command in ('go back','back') and not (media().stations if MEDIA_SECTION == 'radio' else media().shows):
            speak('Scheduled recordings continue while the app runs.')
            return 'awake'
        if command in ('radio','internet radio','podcasts','listen to podcasts'):
            MEDIA_SECTION = 'podcast' if 'podcast' in command else 'radio'
            if MEDIA_SECTION == 'podcast': media().awaiting_radio_search = False
            else: media().awaiting_podcast_search = False
            speak('Podcasts are ready. Say search podcasts for a name or list subscriptions.' if MEDIA_SECTION == 'podcast'
                  else 'Radio is ready. Say search followed by a station name, or browse genre jazz.')
            return 'media'
        try: speak(media().command(text, section=MEDIA_SECTION))
        except (OSError,ValueError,IndexError,ET.ParseError) as exc:
            speak('I could not complete that media request. ' + str(exc))
        return 'media'
    literal = re.fullmatch(r'(?:type|dictate) literally\s+(.+)', text, re.I | re.S)
    if command == 'type new line': literal_text = 'new line'
    else: literal_text = literal[1] if literal else None
    if literal_text is not None and mode == 'note':
        note_buffer.append(literal_text)
        note_selection.text = None
        speak('Added literal text.')
        return mode
    if literal_text is not None and mode == 'web' and web_session is not None:
        speak(web_session.dictate_to_focus(literal_text,literal=True))
        return mode
    if command in LINE_BREAK_COMMANDS:
        if mode == 'note':
            note_buffer[:] = [' '.join(note_buffer)+'\n']
            note_selection.text = None
            speak('New line.')
            return mode
        if mode == 'web' and web_session is not None:
            speak(web_session.insert_line_break())
            return mode
        if mode == 'email_draft' and email_draft is not None:
            if (email_draft.compose_step or 'body') == 'body': speak(email_draft.process(text))
            else: speak_prompt('This email field is single-line. Say go to body to insert a new line in the message.')
            return mode
    if mode == 'document' and document is not None and document_control_request(text):
        speak(document.process(text))
        return mode
    if selection_command(text):
        if mode == 'email_draft' and email_draft is not None:
            field = email_draft.compose_step or 'body'
            if field != 'body' and APP_WINDOW is not None:
                result = APP_WINDOW.select_email_text(text)
                edit = getattr(APP_WINDOW,'email_selection_edit',None)
                if isinstance(edit,tuple) and len(edit)==2 and edit[0]==field:
                    email_draft.apply_field_selection_edit(*edit)
                speak(result)
            else:
                speak(email_draft.process(text))
            return mode
        if mode == 'document' and document is not None:
            speak(document.process(text))
            return mode
        if mode == 'note':
            speak(note_selection.select(' '.join(note_buffer), text)[0])
            if note_selection.edited_text is not None: note_buffer[:] = [note_selection.edited_text]
            return mode
        if mode == 'web' and web_session is not None:
            speak(web_session.select_focused(text))
            return mode
    if mode in ('document', 'email_draft'):
        editor = document if mode == 'document' else email_draft
        body_active = mode == 'document' or (email_draft.compose_step or 'body') == 'body'
        if body_active and ((((isinstance(editor.replacement_candidates, list) and editor.replacement_candidates) or (isinstance(editor.selection_candidates, list) and editor.selection_candidates)) and command not in ('main menu', 'back to main menu', 'leave document', 'close document', 'go back', 'back', 'exit', 'exit document', 'leave email', 'close email', 'exit email')) or re.fullmatch(r'(?:replace|change) .+ (?:with|to) .+', command) or re.fullmatch(r'insert (?:before|after) .+', command) or re.fullmatch(r'(?:select|find|highlight|bold|underline|italicize|copy|cut|delete) .+', command)):
            speak(editor.process(text))
            return mode
    if INPUT_MODE == 'dictation':
        if mode in ('email_draft', 'document'):
            editor = email_draft if mode == 'email_draft' else document
            if (mode == 'document' or mode == 'email_draft' and (email_draft.compose_step or 'body') == 'body') and (document_format_command(command) or editor.pending_spacing):
                speak(editor.process(text))
            elif mode == 'email_draft' and (email_draft.compose_step in ('subject','cc','bcc') or
                                          email_draft.compose_step == 'recipient' and
                                          (typed or '@' in text or ' at ' in text.lower())):
                speak(email_draft.process(text))
                focus_email_entry()
            else:
                speak(editor.append_text(text))
        elif mode == 'note':
            cleaned = clean_dictation(text)
            if cleaned: note_buffer.append(cleaned)
            speak('Added to note.')
        elif mode == 'web' and web_session:
            speak(web_session.dictate_to_focus(text))
        else:
            speak_prompt('No text field is active. Say normal mode to choose a task.')
        return mode
    store = ProtectedStore(APP)
    direct_connection = re.fullmatch(
        r'(?:connect|add|set up|setup|sign in to|log in to)\s+(?:my\s+|an?\s+)?'
        r'(gmail|google mail|outlook|microsoft 365|exchange|yahoo)(?:\s+(?:email|mail|account))?', command)
    if direct_connection:
        account_setup = True
        selected_type = direct_connection[1]
        command = ('gmail' if selected_type in ('gmail','google mail') else
                   'outlook' if selected_type in ('outlook','microsoft 365','exchange') else 'yahoo')
    if command in ('add account', 'add another account', 'add email account', 'connect email account', 'set up email'):
        account_setup = True
        speak('Which email account type? Say Gmail, Outlook or Exchange, or Yahoo. A trusted person can use the normal sign-in page or type private details.')
        return mode
    if account_setup:
        if command in ('cancel', 'cancel account setup'):
            account_setup = False
            speak('Account setup canceled.')
            return mode
        provider = ('gmail' if command in ('gmail','google','google mail') else
                    'outlook' if command in ('outlook','microsoft','microsoft 365','exchange','work email') else
                    'yahoo' if command in ('yahoo','yahoo mail') else None)
        if not provider:
            speak('Say Gmail, Outlook or Exchange, Yahoo, or cancel.')
            return mode
        account_setup = False
        speak('Opening ' + provider.capitalize() + ' account setup. A trusted person may complete the private sign-in. Please wait for the result.')
        try:
            address = connect(provider, APP, graphical=True)
            speak(provider.capitalize() + ' account ' + address + ' is connected and selected. Say check email to open it.')
        except (AccountError, ValueError, OSError) as exc:
            speak('Account setup did not finish. ' + str(exc))
        return mode
    if command in ('list email accounts', 'which email account', 'what email account', 'my email accounts'):
        accounts = store.accounts()
        selected = store.selected()
        if not accounts: speak_prompt('No email account is connected. Say add account.')
        else:
            speak('Connected accounts: ' + '; '.join(p.capitalize() + ' ' + a +
                  (' selected' if selected == (p,a) else '') for p,a in accounts) +
                  '. Say switch to followed by the email address.')
        return mode
    contact_account = store.selected()
    contacts = AddressBook(APP, contact_account[1] if contact_account else '')
    contact = re.fullmatch(r'(?:add|save|create|update) contact (.+?) (?:as|at|email is) (.+)', text, re.I)
    if contact:
        try:
            address = contacts.save(contact[1], contact[2])
            result = 'Saved contact ' + contact[1] + ' at ' + address + '.'
            if contact_account and contact_account[0] == 'gmail':
                try:
                    from google_contacts import sync
                    result += ' ' + sync(APP, authorize=False)
                except Exception:
                    result += ' Saved locally. Say sync Google Contacts after granting permission.'
            speak(result)
        except (ValueError, OSError) as exc:
            speak(str(exc))
        return mode
    if command in ('list contacts','list address book','read address book'):
        entries = list(contacts.all().values())
        speak('Contacts: ' + '; '.join(e['name'] + ', ' + e['address'] for e in entries[:30]) +
              ('. Say find contact followed by a name for another entry.' if len(entries)>30 else '.') if entries else
              'Your address book is empty. Type add contact Bob as bob@example.com, then press Enter.')
        return mode
    contact = re.fullmatch(r'(?:find|read|look up) contact (.+)', text, re.I)
    if contact:
        entry = contacts.get(contact[1])
        speak(entry['name'] + ', ' + entry['address'] if entry else 'No contact with that name. Say list contacts.')
        return mode
    if command in ('connect google contacts','sync google contacts','refresh google contacts'):
        try:
            from google_contacts import sync
            speak('Connecting to Google Contacts. Please complete the Google permission screen if it opens.')
            speak(sync(APP, authorize=True))
        except (AccountError, ValueError, OSError, KeyError, TimeoutError) as exc:
            speak('Google Contacts did not sync. ' + str(exc))
        except Exception:
            speak('Google Contacts could not sync. Gmail remains connected. Ask your helper to check that the People API is enabled and Contacts permission is granted.')
        return mode
    match_account = re.fullmatch(r'(?:switch to|use|select) (.+)', command)
    if match_account and ('@' in match_account[1] or ' at ' in match_account[1] or match_account[1] in
                          ('gmail','outlook','exchange','microsoft 365','yahoo')):
        choice = match_account[1]
        accounts = store.accounts()
        if '@' in choice or ' at ' in choice:
            try: choice = normalize_recipient(choice)
            except ValueError:
                speak_prompt('I did not understand that email address. Say list email accounts and try again.')
                return mode
            matches = [(p,a) for p,a in accounts if a.casefold() == choice.casefold()]
        else:
            provider = 'outlook' if choice in ('exchange','microsoft 365') else choice
            matches = [(p,a) for p,a in accounts if p == provider]
        if len(matches) != 1:
            speak('I cannot identify one connected account. Say list email accounts, then switch to its full email address.' if matches else
                  'That account is not connected. Say add account or list email accounts.')
            return mode
        if mode == 'email_draft' and email_draft and email_draft.response_context:
            speak('This reply or forward belongs to its original mailbox. Save it and start a new email to use another account.')
            return mode
        provider, address = store.select(*matches[0])
        pending_send = None
        if mode == 'email_draft' and email_draft:
            email_draft.provider = provider
            email_draft.save()
        if mode == 'mailbox':
            mail_session = MailSession(provider, APP, progress=mail_progress)
            speak('Switched to ' + address + '. ' + mail_session.process('open inbox'))
        else:
            speak('Switched to ' + provider.capitalize() + ' account ' + address + '.')
        return mode
    if command in ('email', 'my email', 'open email'):
        selected = store.selected()
        if not selected:
            speak_prompt('No email account is selected. Say add account, or list email accounts.')
            return mode
        mail_session = MailSession(selected[0], APP, progress=mail_progress)
        speak(mail_session.process('open inbox'))
        return 'mailbox'
    email_request = (command in ('write an email', 'write email', 'compose email', 'new email', 'create email',
                                 'write a message', 'compose a message', 'new message', 'send a message',
                                 'write a new message', 'compose a new message', 'write mail')
                     or bool(re.fullmatch(r'(?:write|compose|create|start|draft)(?:\s+(?:a|an|new|another|write))*\s+(?:e[ -]?mail|email|message)(?:\s+draft)?', command)))
    if mode == 'awake' and command in ('next', 'previous'):
        MAIN_MENU_INDEX = (0 if command == 'next' else len(MAIN_MENU_CHOICES)-1) if MAIN_MENU_INDEX is None else (MAIN_MENU_INDEX + (1 if command == 'next' else -1)) % len(MAIN_MENU_CHOICES)
        speak(MAIN_MENU_CHOICES[MAIN_MENU_INDEX][0] + ', ' + str(MAIN_MENU_INDEX + 1) + ' of ' + str(len(MAIN_MENU_CHOICES)) + '.')
        return mode
    if mode == 'awake' and command in PICK_CONFIRM:
        if MAIN_MENU_INDEX is None:
            speak('Say next or previous to choose an option.')
            return mode
        request = MAIN_MENU_CHOICES[MAIN_MENU_INDEX][1]
        MAIN_MENU_INDEX = None
        return handle(request, mode)
    document_request = command in ('create a document', 'new document', 'write a document', 'create document')
    note_request = command in ('write a note', 'new note', 'take a note')
    if email_request or document_request or note_request:
        pending_send = None
        if not email_request: mail_session = None
        if mode == 'document' and document is not None:
            document.save()
        elif mode == 'email_draft' and email_draft is not None:
            email_draft.save()
        else:
            flush_note(mode)
        pending_website = None
        if email_request:
            selected = store.selected()
            email_draft = VoiceEmail(APP / 'Email Drafts')
            email_draft.compose_step = 'recipient'
            email_draft.dictating = INPUT_MODE != 'commands'
            email_draft.automatic_dictation = INPUT_MODE != 'commands'
            if selected: email_draft.provider = selected[0]
            email_draft.save()
            speak('New email draft named ' + email_draft.title + '. ' +
                  ('Sending account ' + selected[1] + '. ' if selected else 'No sending account selected. Say add account. ') +
                  'Who do you want to send the email to? Say or type an address, or a saved contact name.')
            focus_email_entry()
            return 'email_draft'
        if document_request:
            document = VoiceDocument(documents_folder())
            apply_document_defaults(document)
            document.dictating = INPUT_MODE != 'commands'
            speak_prompt('New Word document. Speak to write. Say name document followed by a title, or ask for document help.')
            return 'document'
        note_buffer.clear()
        note_selection.text = None
        speak_prompt('Dictation started. Speak naturally. Say save note when finished.')
        return 'note'
    if pending_website is not None:
        address, host = pending_website
        pending_website = None
        if command in ('yes', 'yes please', 'yes open website', 'yes open it', 'confirm website'):
            web_session = web_session or WebSession(APP)
            result = web_session.open(address)
            speak(result)
            return 'web' if web_session.snapshot else mode
        else:
            speak_prompt('Website request canceled. Say open website followed by the address to try again.')
        return mode
    if mode == 'web':
        web_session.input_mode = INPUT_MODE
        if command in ('leave website', 'close website', 'main menu', 'back to main menu'):
            speak('The browser window remains available.')
            return 'awake'
        if command.startswith(('open website ', 'go to website ')):
            try:
                pending_website = prepare_address(text.split(' ', 2)[2])
                speak('I heard website ' + pending_website[1] + '. Is that right? Say yes or no.')
            except ValueError as exc:
                speak(str(exc))
            return 'web'
        speak(web_session.command(text))
        return 'web'
    if mode == 'mailbox':
        if getattr(mail_session, 'folder_picker', None) or getattr(mail_session, 'folder_choice', None):
            speak(mail_session.process(text))
            return 'mailbox'
        response_actions = {'reply': 'reply', 'reply to message': 'reply',
                            'reply all': 'reply_all', 'reply to all': 'reply_all',
                            'forward': 'forward', 'forward message': 'forward'}
        if command in response_actions:
            try:
                context = mail_session.response_context(response_actions[command])
                email_draft = VoiceEmail(APP / 'Email Drafts')
                email_draft.dictating = INPUT_MODE != 'commands'
                email_draft.automatic_dictation = INPUT_MODE != 'commands'
                email_draft.provider = mail_session.provider
                email_draft.recipient = context['recipient']
                email_draft.cc = context['cc']
                email_draft.subject = context['subject']
                email_draft.response_context = context
                email_draft.compose_step = 'recipient' if context['action'] == 'forward' else 'body'
                email_draft.save()
                recipient_note = ('Say email to followed by the forwarding address. '
                                  if context['action'] == 'forward' else
                                  'Recipient ' + email_draft.recipient +
                                  ('. Also copying ' + ', '.join(email_draft.cc) if email_draft.cc else '') + '. ')
                attachment_note = (' Original attachments are not included in this forward.'
                                   if context['action'] == 'forward' and mail_session.provider != 'outlook' else '')
                speak('New ' + command + ' draft. ' + recipient_note +
                      'Speak your message naturally. Say send it to hear the review, then confirm before it sends.' + attachment_note)
                focus_email_entry()
                return 'email_draft'
            except Exception:
                speak('Open and read a message first. If the account cannot read it, ask your helper to reconnect mail.')
                return mode
        if command in ('go back', 'back') and mail_session.view == 'message':
            speak(mail_session.process('back to folder'))
            return mode
        if command in ('go back', 'back', 'exit', 'leave email', 'main menu', 'close inbox'):
            mail_session = None
            pass
            return 'awake'
        speak(mail_session.process(text))
        return 'mailbox'
    # Keep active writing in its editor. A dictated sentence that happens to
    # start with "open document" must not replace the current document.
    if mode == 'email_draft':
        if command in ('cancel send', 'do not send') or (pending_send is not None and command in ('no', 'no thanks')):
            pending_send = None
            speak('Sending canceled. The email remains a local draft.')
            return mode
        if command in ('send email', 'send message', 'send the email', 'send this email',
                       'send it', 'send that', 'send this', 'please send it', 'please send that', 'send'):
            pending_send = None
            email_draft.dictating = False
            email_draft.save()
            try:
                recipient, subject, body = message_parts(email_draft)
                if email_draft.send_hash() in (email_draft.last_accepted_hash, email_draft.last_attempt_hash):
                    speak('This same draft was already attempted. Check Sent Mail before trying again. Ask your helper if you need to send a corrected message.')
                    return mode
                provider = email_draft.provider
                if provider not in ('gmail', 'outlook', 'yahoo'):
                    raise AccountError('Choose a connected email account first. The draft has not been sent.')
                address = ProtectedStore(APP).load(provider)['address']
                if email_draft.response_context and email_draft.response_context.get('source_address') not in (None, address):
                    raise AccountError('This response belongs to a different mailbox.')
                pending_send = (email_draft.send_hash(), provider, address)
                copied = (('. CC ' + ', '.join(email_draft.cc) if email_draft.cc else '') +
                          ('. BCC ' + ', '.join(email_draft.bcc) if email_draft.bcc else ''))
                context = email_draft.response_context
                source_note = ('. This ' + context['action'].replace('_', ' ') +
                               ' uses the original message in your mailbox' +
                               ('. Original attachments are not included' if context['action'] == 'forward' and provider != 'outlook' else '')
                               if context else '')
                speak('Review before sending. From ' + address + '. To ' + recipient +
                      copied + '. Subject ' + subject + '. Message: ' + body + source_note +
                      '. Is that right? Say yes to send, or no to keep the draft.')
            except (AccountError, DeliveryError, ValueError, KeyError):
                speak('This email is saved locally but is not ready to send. Check the recipient, subject, body, and account connection. It has not been sent.')
            return mode
        if command in ('confirm send email', 'yes send email', 'yes send it', 'yes send that',
                       'yes', 'yes please', 'confirm send'):
            if pending_send is None or pending_send[0] != email_draft.send_hash():
                pending_send = None
                speak('There is no current send review. Say send email to review this draft first.')
                return mode
            fingerprint, provider, address = pending_send
            pending_send = None
            try:
                current_address, token = account_token(provider, APP)
                if current_address != address:
                    raise AccountError('The sending account changed. Review the email again.')
                email_draft.last_attempt_hash = fingerprint
                email_draft.save()
                submit(provider, email_draft, token, sender=address)
                email_draft.last_accepted_hash = fingerprint
                email_draft.save()
            except (AccountError, DeliveryError, ValueError, OSError, KeyError):
                speak('I could not confirm delivery. The draft is saved. Check with the recipient and your mail account before trying again; do not send a duplicate.')
                return mode
            speak('Email sent successfully.')
            finish_mail_announcement()
            original_body = (mail_session.body_text if mail_session is not None and
                             email_draft.response_context and mail_session.view == 'message' else None)
            original_id = email_draft.response_context.get('source_id')
            mail_session = mail_session or MailSession(provider, APP, progress=mail_progress)
            try:
                # Reload the folder we were using, preserving its name and page.
                result = mail_session.list_messages(mail_session.page_cursor)
                original_index = next((i for i, row in enumerate(mail_session.rows, 1)
                                       if original_id and row['id'] == original_id), None)
                if original_body is not None and original_index is not None:
                    mail_session.current = original_index
                    mail_session.view = 'message'
                    mail_session.body_text = original_body
                    mail_session.reading.set_text(original_body, reset=True)
                    speak('Back at the original message. ' + mail_session._next_body())
                else:
                    speak('Back to ' + mail_session.folder_name + '. ' + result)
            except Exception:
                speak_prompt('Your message was accepted, but the folder could not refresh. Say list messages to try again.')
            return 'mailbox'
        if pending_send is not None:
            pending_send = None
        if INPUT_MODE == 'commands' and command in ('start dictation', 'dictate', 'continue writing'):
            speak_prompt('Commands only is on. Say normal mode or dictation mode to write.')
            return mode
        if command in ('leave email', 'close email', 'back to main menu',
                       'go back', 'back', 'exit', 'exit email', 'main menu'):
            email_draft.save()
            if mail_session is not None and command not in ('back to main menu', 'main menu'):
                speak('Email draft saved. Back to ' + mail_session.folder_name + '. ' + mail_session.process('back to folder'))
                return 'mailbox'
            speak('Email draft saved locally. Check whether it was sent before sending it again.')
            return 'awake'
        speak(email_draft.process(text))
        focus_email_entry()
        return 'email_draft'
    if mode == 'document':
        if INPUT_MODE == 'commands' and command in ('start dictation', 'dictate', 'continue writing'):
            speak_prompt('Commands only is on. Say normal mode or dictation mode to write.')
            return mode
        if command in ('leave document', 'close document', 'back to main menu',
                       'go back', 'back', 'exit', 'exit document', 'exit document mode', 'close', 'main menu'):
            document.pending_spacing = False
            document.pending_spacing_candidate = None
            document.save()
            if re.fullmatch(r'Untitled(?: \d+)?',document.title,re.I):
                speak('Name this document. Say or type its name, then press Enter. Say cancel to keep editing.')
                return 'document_name'
            speak('Document saved.')
            return 'awake'
        result = document.process(text)
        if INPUT_MODE == 'mixed' and result.startswith('I did not recognize that document request.'):
            document.dictating = True
            result = document.append_text(text)
        speak(result)
        return 'document'
    if mode == 'note':
        request = reading_request(command)
        if request:
            note_reading.set_text(' '.join(note_buffer))
            unit, direction = request
            speak(note_reading.read(direction == 'top') if unit == 'read' else note_reading.move(unit, direction))
            return mode
        if command in ('go back', 'back', 'exit', 'exit note', 'main menu', 'back to main menu'):
            flush_note(mode)
            speak('Note saved.')
            return 'awake'
        if command in ('save note', 'finish note', 'done dictating'):
            if note_buffer:
                save_note(' '.join(note_buffer))
                note_buffer.clear()
                note_selection.text = None
            else:
                speak('The note was empty.')
            return 'awake'
        if INPUT_MODE == 'commands':
            speak_prompt('Commands only is on. Say normal mode or dictation mode to add to the note.')
            return mode
        cleaned = clean_dictation(text)
        if cleaned: note_buffer.append(cleaned)
        return 'note'
    if command in ('list documents', 'what documents do i have', 'my documents'):
        folder = documents_folder()
        names = sorted((p.stem for p in folder.glob('*.docx')), key=str.casefold) if folder.exists() else []
        speak('Your documents are: ' + ', '.join(names[:20]) + ('. There are more documents.' if len(names) > 20 else '.')
              if names else 'You have no saved documents yet.')
        return mode
    if command.startswith('open document '):
        title = text[len('open document '):].strip()
        try:
            document = VoiceDocument.open_existing(documents_folder(), title)
        except FileNotFoundError:
            speak_prompt('I could not find that document. Say list documents to hear the names.')
            return mode
        except (ValueError, OSError, KeyError, zipfile.BadZipFile, ET.ParseError):
            speak('I could not safely open that document in this test editor.')
            return mode
        document.dictating = INPUT_MODE != 'commands'
        speak('Opened ' + document.title + '. Speak to add text, or say read document.')
        return 'document'
    if command in ('check gmail', 'read gmail', 'open gmail inbox',
                   'check outlook', 'read outlook', 'open outlook inbox',
                   'check microsoft email', 'check yahoo', 'read yahoo',
                   'open yahoo inbox', 'check email', 'read my email'):
        if command in ('check email', 'read my email'):
            selected = store.selected()
            if not selected:
                speak_prompt('No email account is selected. Say add account, or list email accounts.')
                return mode
            provider = selected[0]
        else:
            provider = 'gmail' if 'gmail' in command else ('yahoo' if 'yahoo' in command else 'outlook')
            matches = [(p,a) for p,a in store.accounts() if p == provider]
            if len(matches) != 1:
                speak('Say list email accounts and switch to the full email address.' if matches else 'That account is not connected. Say add account.')
                return mode
            store.select(*matches[0])
        mail_session = MailSession(provider, APP, progress=mail_progress)
        speak(mail_session.process('open inbox'))
        return 'mailbox'
    if command in ('list email drafts', 'list drafts', 'my email drafts'):
        folder = APP / 'Email Drafts'
        names = sorted((p.stem for p in folder.glob('*.json')), key=str.casefold) if folder.exists() else []
        speak('Your email drafts are: ' + ', '.join(names[:20]) + ('. There are more drafts.' if len(names) > 20 else '.')
              if names else 'You have no saved email drafts yet.')
        return mode
    if command.startswith('open email draft '):
        try:
            email_draft = VoiceEmail.open_existing(APP / 'Email Drafts', text[len('open email draft '):])
        except (FileNotFoundError, ValueError, OSError, KeyError, TypeError):
            speak_prompt('I could not open that draft. Say list email drafts to hear the names.')
            return mode
        email_draft.dictating = INPUT_MODE != 'commands'
        email_draft.automatic_dictation = INPUT_MODE != 'commands'
        speak('Opened email draft ' + email_draft.title + '. ' +
              ('Who do you want to send it to?' if email_draft.compose_step == 'recipient' else
               'What is the subject?' if email_draft.compose_step == 'subject' else
               'Speak to add to the message, or say send it for a full review.'))
        focus_email_entry()
        return 'email_draft'
    if mode in ('podcast', 'search') and command in ('go back', 'back', 'exit', 'main menu', 'back to main menu'):
        pass
        return 'awake'
    if mode == 'podcast':
        speak(media().search_podcasts(text))
        return 'media'
    if mode == 'search':
        web_session = web_session or WebSession(APP)
        speak(web_session.search(text))
        return 'web' if web_session.snapshot else 'awake'
    if command in ('resume document', 'continue document', 'open current document'):
        if document is None:
            speak_prompt('There is no document open. Say create a document.')
            return 'awake'
        speak('Continuing ' + document.title + '. Say read paragraph to hear your place.')
        return 'document'
    if command in ('resume email', 'continue email', 'resume draft'):
        if email_draft is None:
            speak_prompt('There is no email open. Say write an email.')
            return 'awake'
        speak('Continuing email draft ' + email_draft.title + '. Say read paragraph to hear the body.')
        return 'email_draft'
    if re.search(r'\b(document|word file|word document)\b', command):
        document = VoiceDocument(documents_folder())
        apply_document_defaults(document)
        document.dictating = INPUT_MODE != 'commands'
        speak_prompt('New Word document. Speak to write. Say name document followed by a title, or ask for document help.')
        return 'document'
    if re.search(r'\b(note|dictate)\b', command):
        note_buffer.clear()
        note_selection.text = None
        speak_prompt('Dictation started. Speak naturally. Say save note when finished.')
        return 'note'
    provider_websites = {'gmail': 'https://mail.google.com/',
                         'outlook': 'https://outlook.live.com/mail/',
                         'yahoo mail': 'https://mail.yahoo.com/'}
    if command in ('open gmail', 'open outlook', 'open outlook mail', 'open yahoo', 'open yahoo mail'):
        provider = command.removeprefix('open ').removesuffix(' mail')
        webbrowser.open(provider_websites['yahoo mail' if provider == 'yahoo' else provider])
        speak('Opened ' + provider + ' in your usual browser. Voice Companion is not connected to this account.')
        return 'awake'
    if re.fullmatch(r'(?:use|switch to|choose) (?:browser )?(?:edge|chrome|brave|firefox)(?: browser)?', command) or command in ('which browser','what browser am i using'):
        web_session = web_session or WebSession(APP)
        speak(web_session.command(text))
        return mode
    if command.startswith(('open website ', 'go to website ')):
        value = text.split(' ', 2)[2]
        try:
            pending_website = prepare_address(value)
        except ValueError as exc:
            speak(str(exc))
            return mode
        speak('I heard website ' + pending_website[1] + '. Is that right? Say yes or no.')
        return mode
    if command in ('list favorites','my favorites') or command.startswith(('open favorite ', 'go to favorite ')):
        web_session = web_session or WebSession(APP)
        speak(web_session.command(text))
        return 'web' if web_session.snapshot else 'awake'
    if 'email' in command or 'inbox' in command or re.search(r'\bmail\b', command):
        speak_prompt('Say add account to connect mail, list email accounts to hear connections, or email to open the selected inbox. Say write an email to create a draft.')
        return 'awake'
    if command in ('radio','internet radio'):
        MEDIA_SECTION = 'radio'
        speak_prompt('Radio is ready. Say search followed by a station name, browse genre jazz, or choose station database.')
        return 'media'
    if command in ('podcasts','listen to podcasts'):
        MEDIA_SECTION = 'podcast'
        media().awaiting_radio_search = False
        speak_prompt('Podcasts are ready. Say search followed by a name, browse podcasts category science, or list subscriptions.')
        return 'media'
    if command.startswith(('choose station database','set station database','use station database','switch to station database',
                           'choose radio source','set radio source','use radio source','switch to radio source',
                           'search radio','find station','find radio','browse genre','browse country','browse state','browse city',
                           'search podcast','find podcast','browse podcasts','list podcasts','list subscriptions','refresh subscriptions',
                           'check podcasts','update subscribed podcasts','auto download','automatically download',
                           'manual downloads','turn off auto downloads','set podcast ','set show ',
                           'open podcast ','subscribe to podcast ','subscribe podcast ',
                           'list recordings',
                           'list scheduled recordings','import radiosure','play station ','play episode ',
                           'play recording ','play download ','record station ','schedule station ')) or command in ('station database','which station database','which radio source','list station databases'):
        MEDIA_SECTION = 'podcast' if any(word in command for word in ('podcast','subscription','episode','download')) else 'radio'
        try: speak(media().command(text, section=MEDIA_SECTION))
        except (OSError,ValueError,IndexError,ET.ParseError) as exc:
            speak('I could not complete that media request. ' + str(exc))
        return 'media'
    if command=='open first podcast':
        try: speak(media().open_podcast(1))
        except (OSError,ValueError,IndexError,ET.ParseError): speak('Search for a podcast first.')
        return 'media'
    if 'podcast' in command:
        topic = re.sub(r'^.*?podcasts?\s*(?:about|on|for|called)?\s*', '', text)
        try: speak(media().search_podcasts(topic))
        except (OSError,ValueError): speak('Podcast search could not connect. Try later.')
        return 'media'
    if re.search(r'\b(search|look up|google|find|web)\b', command):
        topic = re.sub(r'^.*?\b(?:search(?: the web)?(?: for)?|look up|google|find(?: me)?|web(?: for)?)\b\s*', '', text)
        if topic:
            web_session = web_session or WebSession(APP)
            speak(web_session.search(topic, provider='google' if command.startswith('google ') else 'brave'))
            return 'web' if web_session.snapshot else 'awake'
        speak('What would you like to search for?')
        return 'search'
    speak_prompt('I can write a note or document, open email, search the web, or find radio and podcasts. Say add account to connect email. Say commands only, dictation only, or normal mode to choose how speech works.')
    return 'awake'


UPDATES = None
UPDATE_RETURN_MODE = 'awake'
UPDATE_OFFER = False
UPDATE_MANUAL = False
UPDATE_LAST_PERCENT = 0
STARTUP_UPDATE_PENDING = False


def return_from_update():
    global STARTUP_UPDATE_PENDING
    if STARTUP_UPDATE_PENDING:
        STARTUP_UPDATE_PENDING = False
        speak(startup_prompt())
        return 'sleep'
    return UPDATE_RETURN_MODE


def startup_update_mode():
    """Check before readiness; the input stream is already open for yes/no."""
    global STARTUP_UPDATE_PENDING, UPDATE_RETURN_MODE
    STARTUP_UPDATE_PENDING = False
    if PREFERENCES.get('check_updates',True) and UPDATES.check():
        try:
            event, payload = UPDATES.events.get(timeout=10)
        except queue.Empty:
            event, payload = 'check_failed', False
        if event == 'available':
            UPDATES.release = payload
            STARTUP_UPDATE_PENDING = True
            UPDATE_RETURN_MODE = 'sleep'
            speak('A new update is available. Would you like to install it now? Say yes or no.')
            return 'update_offer'
    speak(startup_prompt())
    return 'sleep'


_HANDLE_DEPTH = 0

def handle(text, mode, typed=False):
    global _HANDLE_DEPTH
    _HANDLE_DEPTH += 1
    try:
        if _HANDLE_DEPTH==1:flush_keyboard_edits(True)
        result = _handle(text, mode, typed)
    finally:
        _HANDLE_DEPTH -= 1
    if result == 'awake' and mode not in ('awake', 'sleep') and _HANDLE_DEPTH == 0:
        announce_main_menu()
    if _HANDLE_DEPTH==0:sync_app_context(result,result!=mode)
    return result


def poll_app_updates(mode):
    global UPDATE_OFFER, UPDATE_RETURN_MODE, UPDATE_LAST_PERCENT
    if UPDATES is None: return mode
    while not UPDATES.events.empty():
        event, payload = UPDATES.events.get_nowait()
        if event == 'available':
            UPDATES.release = payload
            UPDATE_OFFER = True
        elif event == 'current' and payload:
            speak('Voice Companion is up to date.')
        elif event == 'unconfigured':
            speak('The update server is not configured yet. Ask your trainer to set it up.')
        elif event == 'check_failed' and payload:
            speak('I could not check for updates. You can keep using the app and try later.')
        elif event in ('download_failed', 'canceled'):
            if mode == 'update_download':
                speak('Update canceled.' if event == 'canceled' else 'The update could not be downloaded or verified. Your current app remains installed.')
                mode = return_from_update()
        elif event == 'progress' and mode == 'update_download':
            percentage = min(100, payload // 5 * 5)
            if percentage > UPDATE_LAST_PERCENT:
                UPDATE_LAST_PERCENT = percentage
                speak(str(percentage) + '%')
        elif event == 'downloaded' and mode == 'update_download' and not UPDATES.canceled.is_set():
            path, release = payload
            try:
                speak('Installing the update. Voice Companion will close and reopen when installation finishes.')
                finish_mail_announcement()
                UPDATES.launch(path, release)
                return 'exit'
            except Exception:
                speak('The update could not start. Your current app remains available.')
                mode = return_from_update()
    if UPDATE_OFFER and (mode in ('sleep', 'awake') or UPDATE_MANUAL) and not speech_busy():
        UPDATE_RETURN_MODE = 'awake' if mode == 'sleep' else mode
        UPDATE_OFFER = False
        speak('A new update is available. Would you like to install it now? Say yes or no.')
        return 'update_offer'
    return mode


def main():
    global AI_SPEECH, UPDATES, SETTINGS_PANEL, SLEEP_RETURN_MODE
    if '--check-update-environment' in sys.argv:
        from app_updates import check_update_environment
        return check_update_environment()
    if '--version' in sys.argv:
        print('Voice Companion ' + APP_VERSION, flush=True)
        return 0
    if (getattr(sys, 'frozen', False) and Path(sys.executable).name.casefold() == 'voicecompanion.exe'
            and not any(flag in sys.argv for flag in
                        ('--check-update-environment', '--check-speech-file', '--check-speech', '--check-runtime', '--check-model', '--check-audio', '--record-test', '--text-mode'))):
        installed = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Voice Companion'
        if Path(sys.executable).parent.resolve() != installed.resolve():
            speak('This is a build file, not an installation. Run VoiceCompanion Setup to install the app.')
            return 3
    if '--check-runtime' in sys.argv:
        try:
            import tkinter
            import win32crypt
            import google_auth_oauthlib.flow
            import msal
            import sounddevice
            from playwright.sync_api import sync_playwright
            import web_assistant
            import app_window
            runtime = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'browser-runtime'
            if getattr(sys, 'frozen', False) and not any(runtime.glob('firefox-*/firefox/firefox.exe')):
                raise RuntimeError('The bundled Firefox browser is missing.')
            if not MODEL.joinpath('am', 'final.mdl').is_file():
                raise RuntimeError('The bundled offline wake model is missing.')
            if not GUIDE.is_file():
                raise RuntimeError('The onboard user guide is missing.')
            from guide_files import guide_sources
            guide_sources()
            update_root = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
            if not all((update_root / name).is_file() for name in ('update-settings.json','apply-update.ps1')):
                raise RuntimeError('The updater components are missing.')
            print('Packaged Python, email, audio, browser, and private-entry components are available.', flush=True)
            return 0
        except Exception as exc:
            print('A packaged component is missing or broken: ' + str(exc), file=sys.stderr, flush=True)
            return 3
    if any('--connect-' + p in sys.argv for p in ('gmail', 'outlook', 'yahoo')):
        provider = next(p for p in ('gmail', 'outlook', 'yahoo') if '--connect-' + p in sys.argv)
        try:
            address = connect(provider, APP)
            speak(provider.capitalize() + ' account ' + address + ' connected. You may close this window.')
            return 0
        except (AccountError, OSError, ValueError, KeyError) as exc:
            speak('Account connection did not finish. Ask your helper to check the registration and try again.')
            return 3
    if any('--disconnect-' + p in sys.argv for p in ('gmail', 'outlook', 'yahoo')):
        provider = next(p for p in ('gmail', 'outlook', 'yahoo') if '--disconnect-' + p in sys.argv)
        try:
            ProtectedStore(APP).disconnect(provider)
            speak('Selected ' + provider.capitalize() + ' account disconnected.')
            return 0
        except AccountError as exc:
            speak(str(exc))
            return 3
    if '--check-speech-file' in sys.argv:
        from sapi_build_check import check_file_speech
        return check_file_speech()
    if '--check-speech' in sys.argv:
        speak('Voice Companion Windows speech test. I am speaking without a screen reader.')
        if not wait_for_speech(15000): return 3
        return 0
    if '--check-speech-control' in sys.argv:
        speak('One two three four five six seven eight nine ten. This sentence tests interrupting speech.')
        control_speech('pause')
        control_speech('resume')
        interrupt_speech()
        if not wait_for_speech(5000): return 3
        print('Windows speech pause, resume, and interruption completed.', flush=True)
        return 0
    if '--record-test' in sys.argv:
        return microphone_diagnostic()
    if '--check-audio' in sys.argv:
        import sounddevice as sd
        device, input_rate, channels = microphone_settings(sd)
        print('Default microphone ready:', device['name'], 'at', input_rate, 'Hz',
              'with', channels, 'channel(s)', flush=True)
        speak('Voice Companion speaker test. If you heard this, sound is working.')
        if not wait_for_speech(15000): return 3
        return 0
    if not MODEL.exists() and not TEXT_MODE:
        print('Speech model missing. Download a Vosk English model and extract it to:', MODEL, file=sys.stderr)
        if '--check-model' not in sys.argv:
            speak('My offline speech files are missing. Please ask your trainer to run check setup.')
        return 2
    if TEXT_MODE:
        speak('Voice Companion text test is ready. Type wake up to wake it.')
        mode = 'sleep'
        for line in sys.stdin:
            heard = line.strip()
            if mode == 'sleep':
                if any(w in heard.lower() for w in WAKE):
                    mode = resume_from_sleep()
            else:
                mode = handle(heard, mode)
            if mode == 'exit':
                break
        return 0
    # Announce readiness once, after models and microphone are initialized.
    SetLogLevel(-1)
    model = Model(str(MODEL))
    if '--check-model' in sys.argv:
        print('Offline Vosk model loaded successfully.', flush=True)
        if PARAKEET_MODEL.is_dir():
            recognizer = ParakeetRecognition(PARAKEET_MODEL)
            # Check the packaged inference path, not only the model files.
            recognizer.recognize(b'\x00\x00' * 16000)
            print('Offline Parakeet model loaded and inference completed.', flush=True)
        return 0
    try:
        from guide_files import publish_guides
        publish_guides(documents_folder())
    except OSError:
        speak('The user guide files could not be copied to Documents. Onboard help is still available. Ask your helper to check the installation.')

    import sounddevice as sd
    sample_rate = 16000
    _, input_rate, channels = microphone_settings(sd)
    global resampler, input_channels
    resampler = PCM16Resampler(input_rate, sample_rate)
    input_channels = channels
    recognizer = KaldiRecognizer(model, sample_rate)
    mode = 'sleep'
    media()  # Restore scheduled radio recordings after an app restart.
    cloud = None
    cloud_failed = False
    parakeet = None
    if os.getenv('VOICE_COMPANION_SPEECH', '').lower() == 'parakeet' or PARAKEET_MODEL.is_dir():
        try:
            parakeet = ParakeetRecognition(PARAKEET_MODEL)
            print('Offline Parakeet dictation is ready.', flush=True)
        except Exception as exc:
            print('Parakeet initialization failed:', exc, file=sys.stderr)
            speak('Parakeet could not start. Using basic offline speech.')
    utterance = bytearray()
    last_focus_check = 0.0
    with sd.RawInputStream(samplerate=input_rate, blocksize=max(1, input_rate // 20),
                           dtype='int16', channels=channels, callback=callback):
        from app_updates import AppUpdates
        UPDATES = AppUpdates(APP, APP_VERSION, Path(getattr(sys, '_MEIPASS', Path(__file__).parent)))
        mode = startup_update_mode()
        # Do not allow the start announcement to activate the microphone itself.
        wait_for_speech(15000)
        while not AUDIO.empty():
            try: AUDIO.get_nowait()
            except queue.Empty: break
        recognizer.Reset()
        resampler.reset()
        result_file = APP / 'update-result.json'
        if result_file.exists():
            try:
                result = json.loads(result_file.read_text(encoding='utf-8-sig'))
                speak('The update needs a Windows restart to finish. Ask your trainer to restart when convenient.' if result.get('restart_required') else 'Voice Companion was updated successfully.' if result.get('succeeded') else 'The update did not finish. You can keep using the app or ask your trainer for help.')
            except (OSError, ValueError): pass
            try: result_file.unlink(missing_ok=True)
            except OSError: pass
        settings_quiet_until=0
        settings_audio_blocked=False
        while True:
            if KEYBOARD_SPEECH and KEYBOARD_SPEECH.narration_interrupt.is_set():
                KEYBOARD_SPEECH.narration_interrupt.clear()
                voice.Speak('',3)
                if AI_SPEECH is not None:AI_SPEECH.interrupt()
            try:flush_keyboard_edits()
            except OSError:speak('Keyboard text could not be saved. Keep the document open and try save again.')
            mode = poll_app_updates(mode)
            if mode == 'exit': return 0
            update_audio_ducking()
            if APP_WINDOW is not None and APP_WINDOW.closed.is_set():
                flush_keyboard_edits(True)
                speak('Voice Companion closed. Goodbye.')
                return 0
            while True:
                try: media_notice = MEDIA_NOTICES.get_nowait()
                except queue.Empty: break
                speak(media_notice)
            if APP_WINDOW is not None:
                try: settings_result = APP_WINDOW.settings_results.get_nowait()
                except queue.Empty: settings_result = None
                if isinstance(settings_result,tuple):
                    kind,values=settings_result
                    if kind=='general':
                        try:
                            apply_settings(values)
                            SETTINGS_PANEL.accepted()
                            speak('Settings saved.')
                        except Exception as exc:
                            SETTINGS_PANEL.failed('Settings were not saved: '+str(exc))
                    elif kind=='action':
                        mode=settings_action(values,SETTINGS_RETURN_MODE)
                    settings_result=None
                if SETTINGS_PANEL is not None and SETTINGS_PANEL.closed.is_set() and mode=='sleep' and SLEEP_RETURN_MODE=='settings':
                    SLEEP_RETURN_MODE=SETTINGS_RETURN_MODE
                    sync_app_context(mode,True)
                if SETTINGS_PANEL is not None and SETTINGS_PANEL.closed.is_set() and mode=='settings':
                    mode=SETTINGS_RETURN_MODE
                    sync_app_context(mode,True)
                settings_notices=[]
                while not APP_WINDOW.settings_notices.empty():settings_notices.append(APP_WINDOW.settings_notices.get_nowait())
                if settings_notices:
                    interrupt_speech()
                    for settings_notice in settings_notices:speak_keyboard_feedback(settings_notice)
                if settings_result:
                    interrupt_speech()
                    if AI_SPEECH is not None: AI_SPEECH.enabled = False
                    if settings_result == 'removed':
                        if AI_SPEECH is not None: AI_SPEECH.close()
                        AI_SPEECH = None
                    try:
                        if settings_result == 'saved': configure_ai_speech()
                        save_speech_settings()
                        speak('AI voice settings saved. Say Use AI voice to test it.' if settings_result == 'saved'
                              else 'Saved AI voice settings removed. Using Windows speech.')
                    except Exception:
                        speak('AI settings could not be loaded. Using Windows speech.')
                key_notices = []
                while True:
                    try: key_notices.append(APP_WINDOW.key_feedback.get_nowait())
                    except queue.Empty: break
                if key_notices:
                    interrupt_speech()
                    for key_notice in key_notices: speak_keyboard_feedback(key_notice)
                try: typed_command = APP_WINDOW.commands.get_nowait()
                except queue.Empty: typed_command = None
                if typed_command:
                    if isinstance(typed_command,tuple):
                        if typed_command[0]=='text_edit':
                            try:apply_keyboard_edit(typed_command[1],mode)
                            except (ValueError,OSError) as exc:speak(str(exc))
                            continue
                        key=typed_command[1]
                        typed_command='open settings' if key=='settings' else navigation_key(key,SLEEP_RETURN_MODE if mode=='sleep' else mode)
                        if typed_command is None:continue
                    if mode=='sleep' and typed_command.lower() in ('wake up','wake companion'):
                        mode=resume_from_sleep()
                    else:
                        mode = handle_keyboard(typed_command,mode)
                    if mode == 'exit': return 0
                    continue
            try:
                audio = AUDIO.get(timeout=0.02)
            except queue.Empty:
                if mode == 'web' and web_session and not speech_busy():
                    notice = web_session.focus_notice()
                    if notice: speak(notice)
                continue
            if mode == 'web' and web_session and not speech_busy() and time.monotonic() - last_focus_check > 1.2:
                last_focus_check = time.monotonic()
                notice = web_session.focus_notice()
                if notice: speak(notice)
            if AUDIO_OVERFLOW:
                if cloud:
                    cloud.stop()
                    cloud = None
                    cloud_failed = True
                recover_audio_overflow(recognizer, utterance)
                continue
            if mode=='settings' or (KEYBOARD_SPEECH and KEYBOARD_SPEECH.busy()) or settings_audio_blocked:
                # Settings readback must not become another settings command.
                # Keep local silence/sleep/exit controls available during feedback.
                if cloud:
                    cloud.stop();cloud=None
                if (mode=='settings' and speech_busy()) or (KEYBOARD_SPEECH and KEYBOARD_SPEECH.busy()):settings_quiet_until=time.monotonic()+0.2
                if time.monotonic()<settings_quiet_until:
                    settings_audio_blocked=True
                    utterance.clear()
                    if recognizer.AcceptWaveform(audio):
                        control=json.loads(recognizer.Result()).get('text','').lower()
                        if spoken_control(control):
                            mode=handle(control,mode)
                            if mode=='exit':return 0
                    continue
                if settings_audio_blocked:
                    recognizer.Reset();utterance.clear();settings_audio_blocked=False
                    continue
            if cloud and cloud.active:
                cloud.write(audio)
                if recognizer.AcceptWaveform(audio):
                    local_control = json.loads(recognizer.Result()).get('text', '').lower()
                    if spoken_control(local_control) or speech_control(local_control):
                        mode = handle(local_control, mode)
                        # Pausing narration does not require shutting down online recognition.
                        if mode in ('sleep', 'exit'):
                            cloud.stop()
                            cloud = None
                        if mode == 'exit':
                            return 0
                        continue
                elif mode != 'sleep' and fast_offline_silence(recognizer, utterance):
                    continue
                while not cloud.events.empty():
                    event, payload = cloud.events.get_nowait()
                    if event == 'error':
                        cloud.stop()
                        cloud = None
                        cloud_failed = True
                        speak('Cloud dictation lost its connection. I am switching to offline speech.')
                        break
                    print('Cloud heard:', payload, flush=True)
                    mode = handle(payload, mode)
                    if mode in ('sleep', 'exit'):
                        cloud.stop()
                        cloud = None
                        break
                if mode == 'exit':
                    return 0
                # Keep offline recognition fresh for fallback, but do not act on
                # its lower-accuracy guesses while cloud recognition is active.
                continue
            if mode != 'sleep' and parakeet:
                utterance.extend(audio)
                # Never send more than 25 seconds as one model request.
                if len(utterance) > 25 * sample_rate * 2:
                    utterance.clear()
                    recognizer.Reset()
                    speak('That was too long. Please speak in shorter passages.')
                    continue
            if not recognizer.AcceptWaveform(audio):
                if mode != 'sleep' and fast_offline_silence(recognizer, utterance):
                    continue
                continue
            heard = json.loads(recognizer.Result()).get('text', '').strip()
            local_control = heard.lower()
            if mode != 'sleep' and (spoken_control(local_control) or speech_control(local_control)):
                utterance.clear()
                mode = handle(local_control, mode)
                if mode == 'exit':
                    return 0
                continue
            if mode != 'sleep' and speech_busy() and local_control:
                # Stop the current readback as soon as the offline recognizer
                # recognizes a new utterance. Its words can still be refined
                # by Parakeet before executing the requested action.
                interrupt_speech()
            if mode != 'sleep' and fast_command_request(local_control, mode):
                utterance.clear()
                mode = handle(local_control, mode)
                if mode == 'exit': return 0
                continue
            if mode != 'sleep' and parakeet and utterance:
                try:
                    better = parakeet.recognize(bytes(utterance))
                    if better:
                        heard = better
                except Exception as exc:
                    print('Parakeet recognition failed:', exc, file=sys.stderr)
                    speak('Offline dictation had a problem. Using basic speech for this request.')
                finally:
                    utterance.clear()
            heard = heard.lower() if mode == 'sleep' else heard
            if not heard:
                continue
            print('Offline heard:', heard, flush=True)
            if mode == 'sleep':
                for wake in WAKE:
                    if wake in heard:
                        mode = resume_from_sleep()
                        if not cloud_failed and not parakeet:
                            try:
                                cloud = CloudRecognition()
                                cloud.start()
                                speak('Cloud recognition is active.')
                            except (ImportError, ValueError):
                                cloud = None
                                cloud_failed = True
                                speak('Cloud speech is not configured. Using offline speech.')
                            except Exception:
                                cloud = None
                                cloud_failed = True
                                speak('Cloud speech is unavailable. Using offline speech.')
                        remainder = heard.split(wake, 1)[1].strip()
                        if remainder:
                            mode = handle(remainder, mode)
                        break
            else:
                mode = handle(heard, mode)
            if mode == 'exit':
                return 0


def microphone_diagnostic():
    """Short trainer test using the same device and audio conversion as the app."""
    import sounddevice as sd
    if not MODEL.exists():
        raise FileNotFoundError('The bundled Vosk model is missing.')
    device, rate, channels = microphone_settings(sd)
    converter = PCM16Resampler(rate)
    recorded = bytearray()

    def collect(indata, frames, time_info, status):
        mono = stereo_to_mono(bytes(indata)) if channels == 2 else bytes(indata)
        recorded.extend(converter.convert(mono))

    speak('Microphone test. In a moment, say: Wake up. Create a document. Speak normally.')
    with sd.RawInputStream(samplerate=rate, blocksize=max(1, rate // 2), dtype='int16',
                           channels=channels, callback=collect):
        threading.Event().wait(7)
    if not recorded:
        speak('The microphone captured no audio. Ask your trainer to check Windows microphone settings.')
        return 2
    SetLogLevel(-1)
    recognizer = KaldiRecognizer(Model(str(MODEL)), 16000)
    recognizer.AcceptWaveform(bytes(recorded))
    basic = json.loads(recognizer.FinalResult()).get('text', '').strip()
    print('Default microphone:', device['name'], 'at', rate, 'Hz;', channels, 'channel(s)', flush=True)
    print('Vosk heard:', basic or '(no words)', flush=True)
    if PARAKEET_MODEL.is_dir():
        advanced = ParakeetRecognition(PARAKEET_MODEL).recognize(bytes(recorded))
        print('Parakeet heard:', advanced or '(no words)', flush=True)
        speak('Parakeet heard: ' + (advanced or 'no words.'))
    else:
        speak('Basic speech heard: ' + (basic or 'no words.'))
    if 'wake up' not in basic.lower():
        speak('The wake phrase was not recognized. Ask your trainer to check the microphone and try again.')
        return 2
    if PARAKEET_MODEL.is_dir() and 'document' not in advanced.lower():
        speak('The dictation test did not hear the word document. Ask your trainer to check the microphone and try again.')
        return 2
    return 0


if __name__ == '__main__':
    # PyInstaller's windowed application has no stdout/stderr handles.
    # Keep diagnostic output available in the separate console executable.
    windowed = sys.stdout is None or '--window' in sys.argv
    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w', encoding='utf-8')
    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w', encoding='utf-8')
    def startup_alert(message):
        if sys.platform == 'win32' and windowed:
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(None, message, 'Voice Companion', 0x10)
            except Exception:
                pass
    TEXT_MODE = '--text-mode' in sys.argv
    if not TEXT_MODE and '--check-model' not in sys.argv:
        try:
            if sys.platform != 'win32':
                raise OSError('Voice Companion speech output requires Windows.')
            import win32com.client
            voice = win32com.client.Dispatch('SAPI.SpVoice')
            try:
                settings = json.loads((APP / 'speech-settings.json').read_text(encoding='utf-8'))
                SPEECH_RATE = max(-10, min(10, int(settings.get('rate', -1))))
                SPEECH_VOLUME = max(0, min(100, int(settings.get('volume', 100))))
                PUNCTUATION_LEVEL = settings.get('punctuation', 'some')
                VERBOSITY = settings.get('verbosity',PREFERENCES.get('verbosity','high'))
                if VERBOSITY not in ('high','medium','low'): VERBOSITY='high'
                INPUT_MODE = settings.get('input_mode','mixed')
                if INPUT_MODE not in ('mixed','commands','dictation'): INPUT_MODE='mixed'
                if PUNCTUATION_LEVEL not in ('none', 'some', 'most', 'all'): PUNCTUATION_LEVEL = 'some'
                voices = voice.GetVoices()
                named = [voices.Item(i) for i in range(voices.Count)
                         if voices.Item(i).GetDescription() == settings.get('voice')]
                if named: voice.Voice = named[0]
            except (OSError, ValueError, TypeError, KeyError):
                pass
            voice.Rate = SPEECH_RATE
            voice.Volume = SPEECH_VOLUME
            try:
                if configure_espeak_speech():
                    stored = locals().get('settings', {})
                    ESPEAK_SPEECH.enabled = bool(stored.get('espeak_enabled', False))
                    from espeak_speech import ALL_VOICES as ESPEAK_VOICES
                    if stored.get('espeak_voice') in {code for _, code in ESPEAK_VOICES}: ESPEAK_VOICE_NAME = stored['espeak_voice']
            except Exception:
                ESPEAK_SPEECH = None
                print('eSpeak unavailable; Windows speech remains available.', file=sys.stderr)
            try:
                if configure_ai_speech():
                    AI_SPEECH.enabled = bool(locals().get('settings', {}).get('ai_enabled', False)) and not bool(ESPEAK_SPEECH and ESPEAK_SPEECH.enabled)
                    from ai_voice_settings import VOICES
                    stored_ai_voice = locals().get('settings', {}).get('ai_voice')
                    if stored_ai_voice in VOICES: AI_VOICE_NAME = stored_ai_voice
            except Exception:
                AI_SPEECH = None
                print('AI voice settings unavailable; using Windows speech.', file=sys.stderr)

        except Exception as exc:
            print('Windows speech output could not start:', exc, file=sys.stderr)
            try:
                APP.mkdir(parents=True, exist_ok=True)
                (APP / 'startup-error.txt').write_text(
                    'Voice Companion ' + APP_VERSION + '\nSpeech initialization:\n' +
                    traceback.format_exc(), encoding='utf-8')
            except OSError:
                pass
            startup_alert('Voice Companion could not start speech. Ask your trainer to run the setup check.')
            raise SystemExit(2)
    if windowed and not TEXT_MODE and not any(flag in sys.argv for flag in
            ('--check-update-environment', '--check-speech-file', '--check-speech', '--check-speech-control', '--check-runtime', '--check-model', '--check-audio', '--record-test', '--version')):
        try:
            APP_WINDOW = CompanionWindow(APP_VERSION, APP)
            from keyboard_speech import KeyboardSpeech
            KEYBOARD_SPEECH=KeyboardSpeech();APP_WINDOW.keyboard_output=KEYBOARD_SPEECH
            APP_WINDOW.start()
        except Exception:
            APP_WINDOW = None
            APP.mkdir(parents=True, exist_ok=True)
            (APP / 'window-error.txt').write_text(traceback.format_exc(), encoding='utf-8')
            speak('The typing window could not open. Ask your helper to check window-error.txt. Keyboard email entry is unavailable in this session.')
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('Voice Companion could not start or continue:', type(exc).__name__, exc, file=sys.stderr)
        try:
            APP.mkdir(parents=True, exist_ok=True)
            (APP / 'startup-error.txt').write_text(
                'Voice Companion ' + APP_VERSION + '\n' + traceback.format_exc(), encoding='utf-8')
        except OSError:
            pass
        if not TEXT_MODE and '--check-model' not in sys.argv:
            try:
                if isinstance(exc, OSError):
                    speak('Voice Companion could not use the microphone or an audio device. Please ask your trainer to run check setup.')
                else:
                    speak('Voice Companion stopped because of a problem. Please ask your trainer to run check setup.')
            except Exception:
                pass
            startup_alert('Voice Companion stopped because of a problem. Ask your trainer to run the setup check.')
        raise SystemExit(2)





