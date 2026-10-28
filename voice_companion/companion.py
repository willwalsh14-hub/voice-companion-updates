"""Voice Companion: local, self-voicing Windows prototype."""
import user_stop
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
from email_delivery import DeliveryError, delivery_note, message_parts, submit
from mail_accounts import AccountError, ProtectedStore, account_token, connect, yahoo_connect, PROVIDERS as MAIL_PROVIDERS, REMOVED_PROVIDERS as REMOVED_MAIL_PROVIDERS
from mail_voice import MailSession
from mailbox_access import MailboxError
from practice_tutorial import PracticeTutorial
from cloud_recognition import CloudRecognition
from privacy_controls import narration_allowed,cloud_recognition_allowed
from parakeet_recognition import ParakeetRecognition
from browser_navigation import prepare_address
from web_assistant import RefreshPacer, WebSession
from audio_resample import PCM16Resampler, stereo_to_mono
from app_window import CompanionWindow
from reading_navigation import ReadingCursor, reading_request
import notepad  # Saved notes; imported here so the Windows build always packages it.
import time_date  # F12 and spoken requests for the time and date.
from voice_pitch import sapi_speak
from dictation_text import clean_dictation
from ai_dictation import dictation_scope, prepare_dictation
from onboard_help import HelpSession
from speech_controls import request as speech_setting_request
from file_manager import FileManager
from settings_model import DEFAULTS, SettingsSession, prompt_text, keyboard_request
from keyboard_text import document_text,replace_keyboard_text,absolute
from menu_navigation import boundary_notice, bounded_index, next_match, menu_label, announce_item

APP = Path(os.getenv('VOICE_COMPANION_DATA_DIR') or
           (Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'VoiceCompanion'))
DEFAULT_APP = APP
APP_VERSION = '0.2.171-test'
POWER_COMMANDS={'restart computer':'restart','restart the computer':'restart','restart windows':'restart','reboot computer':'restart','shut down computer':'shutdown','shut down the computer':'shutdown','shutdown computer':'shutdown','shut down windows':'shutdown','turn off computer':'shutdown','turn off the computer':'shutdown'}
POWER_COMMANDS.update(dict.fromkeys(('shut off the computer','shut off computer','turn the computer off','turn computer off','shut the computer down','shut computer down'), 'shutdown'))
POWER_COMMANDS.update(dict.fromkeys(('reboot the damn thing','reboot the computer','reboot','restart','restart the damn thing'), 'restart'))

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
# Saying the first three lists the saved drafts; the rest open a list to choose one from.
EMAIL_DRAFT_COMMANDS = ('list email drafts', 'list drafts', 'my email drafts',
                        'open email draft', 'open an email draft', 'choose email draft', 'choose an email draft')
SLEEP = ('go to sleep', 'stop listening', 'sleep companion')
EXIT = ('shut down companion', 'exit companion', 'quit companion', 'close companion', 'return to windows', 'exit voice companion', 'close voice companion')
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
SPEECH_PITCH = 0
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
REGULAR_BROWSER = None
account_setup = False
# Yahoo setup asks for the address, then the app password, in the app's own
# text box: {'provider': 'yahoo', 'step': 'address' | 'password', 'address': ...}.
ACCOUNT_ENTRY = None
# Thunderbird setup waiting for a yes or no: {'flow': thunderbird_setup.Setup, 'question': ...}.
THUNDERBIRD_SETUP = None
# Waiting for Thunderbird to finish adding an account: {'watch': thunderbird_accounts.Watch, 'address': ...}.
THUNDERBIRD_WATCH = None
# The list of connected email accounts while it is open (account_picker.py):
# {'purpose': 'folders' | 'inbox' | 'remove', 'accounts': [(provider, address)], 'index': 0,
#  'current': selected account, 'mode': screen, 'confirm': account waiting for yes or no,
#  'origin': {'account', 'session'} to return to when the folder list is canceled}.
ACCOUNT_PICKER = None
# The 'origin' above after an account was chosen for its folders: Escape in that
# folder list goes back to the account list.
FOLDER_ACCOUNT_RETURN = None
YAHOO_SETUP_PROMPT = ('Yahoo uses an app password, not your normal password. To make one, sign in to Yahoo, '
                      'open Account security, and choose Generate app password. '
                      'Now type or say your Yahoo email address, then press Enter. Say cancel to stop.')
YAHOO_PASSWORD_PROMPT = ('Now type or paste your Yahoo app password, then press Enter. '
                         'For privacy, do not say it aloud. Each character is spoken as hidden. '
                         'Say change address to fix the address, or cancel to stop.')
ACCOUNT_MENU_INDEX = None
# Spoken name and account type, in menu order. Thunderbird uses its own sign-in and
# is how Gmail, Outlook and Microsoft 365 work (Will, October 2026: no direct sign-in).
ACCOUNT_TYPES = (('Thunderbird, for Gmail, Outlook, Microsoft 365 and most email', 'thunderbird'), ('Yahoo', 'yahoo'))
ACCOUNT_TYPE_PROMPT = ('Which email account type? Thunderbird (T) works with Gmail, Outlook, Microsoft 365 and most other email. '
                       'Yahoo (Y) uses a Yahoo app password. Press a letter then Enter, or say the account type. Say cancel to stop.')
# Gmail and Microsoft mail are set up in Thunderbird; these words start that.
THROUGH_THUNDERBIRD = {'gmail': 'Gmail', 'google': 'Gmail', 'google mail': 'Gmail', 'outlook': 'Outlook',
                       'microsoft': 'Microsoft', 'microsoft 365': 'Microsoft 365', 'office 365': 'Office 365',
                       'exchange': 'Exchange', 'hotmail': 'Hotmail', 'work email': 'Work email'}
help_session = None
help_return_mode = 'awake'
TEXT_MODE = False
note_buffer = []
note_selection = FieldSelection()
note_reading = ReadingCursor()
OPEN_NOTE = None  # The saved note being changed, so saving updates it instead of adding a copy.
last_podcasts = []
MEDIA_HUB = None
MEDIA_NAV = None
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
        value = email_draft.recipient if field == 'recipient' else email_draft.subject if field == 'subject' else ', '.join(getattr(email_draft,field,[]))
        APP_WINDOW.focus_email_field(field, value)
resampler = None
def media_navigation():
    global MEDIA_NAV
    from media_navigation import MediaNavigation
    hub=media()
    if MEDIA_NAV is None or MEDIA_NAV.hub is not hub or MEDIA_NAV.section!=MEDIA_SECTION:
        MEDIA_NAV=MediaNavigation(hub,MEDIA_SECTION)
    return MEDIA_NAV

input_channels = 1
APP_WINDOW = None
KEYBOARD_SPEECH = None
EDIT_ACK = {}
KEYBOARD_DIRTY = {}
UI_CONTEXT_CACHE = None
PENDING_DOCUMENT_NAME = None
DOCUMENT_NAME_RETURN = "awake"
DOCUMENT_NAMING_ACTION = "name document"
DOCUMENT_CLOSE_TARGET = "awake"
DOCUMENT_BASELINE = None
CONFIRM_CHOICE = "yes"
# Chrome and extension setup (chrome_setup.py); None when not running.
CHROME_SETUP = None
CHROME_SETUP_RETURN = 'awake'
POWER_ACTION = None
POWER_RETURN_MODE = "awake"
EXIT_RETURN_MODE = "awake"
SEARCH_RETURN_MODE = 'awake'

def checkpoint_document():
    global DOCUMENT_BASELINE
    path=document.path
    task_navigation().remember(path)
    DOCUMENT_BASELINE=(id(document),path,path.read_bytes() if path.exists() else None)

def discard_document_changes():
    if DOCUMENT_BASELINE and DOCUMENT_BASELINE[0]==id(document):
        _,path,data=DOCUMENT_BASELINE
        if data is None:path.unlink(missing_ok=True)
        else:
            temporary=path.with_suffix('.restore.docx')
            temporary.write_bytes(data);temporary.replace(path)
        if document.path!=path:document.path.unlink(missing_ok=True)
    elif re.fullmatch(r'Untitled(?: \d+)?',document.title,re.I):document.path.unlink(missing_ok=True)
    KEYBOARD_DIRTY.pop(id(document),None)

def app_context(mode):
    logical=SLEEP_RETURN_MODE if mode=='sleep' else mode
    context={'mode':logical,'source':None,'text':'','caret':0,'selection':None,'echo':PREFERENCES.get('typing_echo','characters'),'phonetic':PREFERENCES.get('phonetic_enabled',True),'delay':float(PREFERENCES.get('phonetic_delay','0.5')),'ack':0}
    if logical=='task_input':context['task_input']=True
    if THUNDERBIRD_SETUP is not None:context['confirmation']=CONFIRM_CHOICE
    if ACCOUNT_ENTRY is not None:
        context['account_entry']=ACCOUNT_ENTRY['step']
        context['account_private']=ACCOUNT_ENTRY['step']=='password'
        if ACCOUNT_ENTRY.get('provider')=='thunderbird':
            import thunderbird_accounts
            context['account_label']={'address':thunderbird_accounts.ADDRESS_LABEL,'name':thunderbird_accounts.NAME_LABEL,
                                      'password':thunderbird_accounts.PASSWORD_LABEL}[ACCOUNT_ENTRY['step']]
        context['menu_letters']=False
        return context
    if ACCOUNT_PICKER is not None:
        # The account list owns the keys like any other list; its yes or no question like any question.
        if ACCOUNT_PICKER.get('confirm'):context['confirmation']=CONFIRM_CHOICE
        else:
            context.update(named_list=True,mode='list',menu_letters=True)
            return context
    context['menu_letters']=logical!='document_name' and menu_state(logical) is not None
    if SCREEN_MENU is not None and SCREEN_MENU.is_open(logical):context['screen_menu']=True
    if logical=='files' and FILE_MANAGER:
        context['file_name_input']=bool(FILE_MANAGER.pending and FILE_MANAGER.pending[0] in ('rename','search','search all'))
        if context['file_name_input']:
            action=FILE_MANAGER.pending[0]
            context['file_rename_value']=Path(FILE_MANAGER.pending[1][0]).name if action=='rename' else ''
            context['file_input_label']='New file name, including extension' if action=='rename' else 'Search folder and subfolders' if action=='search all' else 'Search names in this list'
        context['menu_letters']=not context['file_name_input'] and not FILE_MANAGER.busy
        if FILE_MANAGER.pending and FILE_MANAGER.pending[0]=='delete':context['confirmation']=CONFIRM_CHOICE
        context['file_revision']=FILE_MANAGER.revision
        context['file_items']=FILE_MANAGER.labels()
        if not FILE_MANAGER.menu and not FILE_MANAGER.pending and not FILE_MANAGER.busy:context['file_selected_indices']=[i for i,item in enumerate(FILE_MANAGER.items) if item.path in FILE_MANAGER.selected]
        context['file_index']=(0 if CONFIRM_CHOICE=='yes' else 1) if context.get('confirmation') is not None else FILE_MANAGER.menu_index if FILE_MANAGER.menu else FILE_MANAGER.index
    if logical=='downloads':__import__('download_screen').context(sys.modules[__name__],context)
    if logical=='contacts':__import__('contacts_screen').context(sys.modules[__name__],context)
    if logical=='calendar':__import__('calendar_screen').context(sys.modules[__name__],context)
    if logical=='attachments':__import__('attachment_screen').context(sys.modules[__name__],context)
    if logical=='browser_menu' and web_session:
        context['browser_name_input']=web_session.menu.level=='name'
        context['menu_letters']=not context['browser_name_input']
    if logical=='web' and web_session and web_session.list_focus in ('links','headings','fields'):context['menu_letters']=False
    if logical=='web' and web_session and web_session.list_focus in ('links','headings'):context['web_name_list']=web_session.list_focus
    if NAMED_LIST:context['named_list']=True;context['mode']='list';return context
    if logical in ('media','podcast') and MEDIA_HUB:
        nav=media_navigation();context['media_navigation']=True
        if nav.pending:context['confirmation']=nav.confirm_choice
        if nav.form:
            context['media_revision']=nav.revision
            if nav.field_index<len(nav.form['fields']):
                field=nav.form['fields'][nav.field_index];context['media_form']=dict(field)
                context['media_input']=not field['choices']
            else:context['media_button']='OK' if nav.field_index==len(nav.form['fields']) else 'Cancel'
        else:context['media_form']=None
    if pending_website is not None or logical in ('document_save','power_confirm','exit_confirm','update_offer') or logical=='email_draft' and pending_send is not None or chrome_confirming(logical):context['confirmation']=CONFIRM_CHOICE
    if logical=='mailbox' and mail_session and mail_session.pending:context['confirmation']=CONFIRM_CHOICE
    if logical=='web' and web_session and (web_session.pending or web_session.submit_pending):context['confirmation']=CONFIRM_CHOICE
    if logical=='document_name':context['naming_request']=PENDING_DOCUMENT_NAME
    editor=document if logical=='document' else email_draft if logical=='email_draft' and email_draft and (email_draft.compose_step or 'body')=='body' else None
    if editor is not None and (getattr(editor,'selection_candidates',[]) or getattr(editor,'replacement_candidates',[]) or getattr(editor,'pending_spacing',False)):
        return context
    if editor is not None:
        if logical=='document':context['document_table']=bool(editor.table_cell())
        source=str(id(editor));context.update(source=source,text=document_text(editor),readonly=False,ack=EDIT_ACK.get(source,0))
        position=editor.navigation_position or editor.insertion_position or (editor.cursor,len(editor.paragraphs[editor.cursor].text) if editor.paragraphs else 0)
        context['caret']=absolute(editor,position)
        if editor.selection:
            s=editor.selection;context['selection']=(absolute(editor,(s.first_paragraph,s.first_offset)),absolute(editor,(s.last_paragraph,s.last_offset)))
    elif logical=='web' and web_session and web_session.snapshot and not web_session.list_focus and web_session.form_index is None:
        context['web_revision']=getattr(web_session,'page_revision',0)
        context['web_cursor_revision']=getattr(web_session,'cursor_revision',0)
        context['web_elements']=web_session.snapshot.get('elements',[])
        field=getattr(web_session,'keyboard_field',None)
        context['web_browse']=field is None
        context['web_choice']=__import__('web_browse').choice_field(field)
        context['web_combo_edit']=__import__('web_browse').editable_combo(field)
        context['web_combo']=bool(web_session.browse and web_session.browse.current() and __import__('web_browse').kind(web_session.browse.current())=='combobox')
        context['web_app_mode']=getattr(web_session,'app_mode',False) is True
        context['web_slider']=bool(isinstance(getattr(web_session,'slider_field',None),dict))
        document_identity=(web_session.document_state or {}).get('epoch',getattr(web_session,'page_revision',0))
        if field:context['web_field_source']='web:'+str(id(web_session))+':document:'+str(document_identity)+':'+str(field.get('frame_index','main'))+':'+field['key']
        if field and field.get('type')=='password':context['web_private']=True;context['web_field']=dict(field)
        else:
            source='web:'+str(id(web_session))+(':field:'+str(field.get('frame_index','main'))+':'+field['key']+':document:'+str(document_identity) if field else ':page')
            context.update(source=source,text=field.get('value','') if field else web_session.reading.text,readonly=field is None or context['web_choice'] or bool(field.get('readonly')),selection=getattr(web_session,'selection',None),caret=getattr(web_session,'web_edit_caret',0) if field else web_session.reading.position,ack=EDIT_ACK.get(source,0))
            context['web_field']=field
        if web_session.pending or web_session.submit_pending:context['confirmation']=CONFIRM_CHOICE
    elif logical=='file_reader' and FILE_READER:
        context.update(source='file:'+str(id(FILE_READER)),text=FILE_READER.text,rich_spans=FILE_READER.spans,selection=FILE_READER.selection,readonly=True,caret=FILE_READER.reading.position,ack=EDIT_ACK.get('file:'+str(id(FILE_READER)),0))
    elif logical=='help' and help_session and help_session.level=='article':
        source='help:'+str(id(help_session))+':'+str(help_session.topic_index)+':'+str(help_session.subtopic_index)
        context.update(source=source,text=help_session.reading.text,readonly=True,help_reader=True,caret=help_session.reading.position,ack=EDIT_ACK.get(source,0))
    elif logical=='mailbox' and mail_session and mail_session.view=='message':
        mail_session.set_format(PREFERENCES.get('email_format','html'))
        source='mail:'+str(id(mail_session))+':'+str(mail_session.current)
        context.update(source=source,text=mail_session.body_text,rich_spans=getattr(mail_session.body_text,'spans',[]) if PREFERENCES.get('email_format','html')=='html' else [],readonly=True,caret=mail_session.reading.position,ack=EDIT_ACK.get(source,0))
        context['email_html']=PREFERENCES.get('email_format','html')=='html'
    return context

def sync_app_context(mode,focus=False):
    global UI_CONTEXT_CACHE
    TRANSCRIPT.mode=SLEEP_RETURN_MODE+', asleep' if mode=='sleep' else mode
    if APP_WINDOW is None:return
    context=app_context(mode)
    # What the window shows for low-vision users: screen name, list, selected item (display_screen.py).
    try:context.update(__import__('display_screen').context(sys.modules[__name__],mode,context))
    except Exception as exc:
        # The visual display must never stop speech or keys; keep the colors and text size at least.
        print('Voice Companion display error:',repr(exc),file=sys.stderr,flush=True)
        context['display']=__import__('display_options').settings(PREFERENCES)
    if UI_CONTEXT_CACHE and context.get('source')!=UI_CONTEXT_CACHE.get('source'):DICTATION_ECHO.clear()
    if KEYBOARD_SPEECH and not TEXT_MODE:
        KEYBOARD_SPEECH.configure(voice.Voice.Id,SPEECH_RATE,SPEECH_VOLUME,ESPEAK_SPEECH,SPEECH_PITCH,ai=AI_SPEECH,capital_letters=PREFERENCES.get('capital_letters','do nothing'))
    if focus or context!=UI_CONTEXT_CACHE:
        UI_CONTEXT_CACHE=dict(context);context['focus']=focus;APP_WINDOW.set_context(context)
        return True
    return False

def apply_keyboard_edit(payload,mode):
    context=app_context(mode)
    if payload['source']!=context['source']:return False
    if not payload.get('readonly') and payload['before']!=context['text']:
        # A voice insertion won the race. Never replace it with an old UI snapshot.
        # Acknowledge the stale sequence so the UI can accept the current text.
        EDIT_ACK[payload['source']]=max(EDIT_ACK.get(payload['source'],0),payload['seq'])
        sync_app_context(mode,True)
        if payload['before']!=payload['after']:
            speak('The text changed. The latest text is ready. Please try that edit again.')
        return False
    if payload.get('readonly'):
        if context['mode']=='web' and payload.get('web_cursor_revision',context.get('web_cursor_revision'))!=context.get('web_cursor_revision'):return False
        if payload['after']!=context['text']:return False
        if context['mode']=='web' and (context.get('web_choice') or web_session.keyboard_field and web_session.keyboard_field.get('readonly')):
            if not context.get('web_choice'):web_session.web_edit_caret=payload['caret'];web_session.selection=payload.get('selection')
            EDIT_ACK[payload['source']]=payload['seq'];return True
        reader=FILE_READER.reading if context['mode']=='file_reader' else help_session.reading if context['mode']=='help' else web_session.reading if context['mode']=='web' else mail_session.reading
        reader.position=payload['caret'];reader.continuation=payload['caret']
        if context['mode']=='file_reader':FILE_READER.selection=payload.get('selection')
        if context['mode']=='web' and web_session.browse and web_session.keyboard_field is None:
            web_session.selection=payload.get('selection')
            web_session.browse.set_position(payload['caret'])
            if not payload.get('selection'):web_session.auto_focus_position()
    elif context['mode']=='web':
        web_session.keyboard_field['value']=payload['after'];web_session.web_edit_caret=payload['caret']
        web_session.selection=payload.get('selection')
        # A site's search box shows suggestions as you type: send it each change.
        if payload['before']!=payload['after'] and context.get('web_combo_edit'):
            notice=web_session.combo_typed()
            if notice:speak(notice)
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

KEYBOARD_NAVIGATION=False
KEYBOARD_BATCH=False
KEYBOARD_BATCH_TEXT=None
KEYBOARD_BATCH_STATUS=False

def handle_keyboard(text,mode):
    global KEYBOARD_NAVIGATION
    previous=KEYBOARD_NAVIGATION;KEYBOARD_NAVIGATION=True
    try:return _handle_keyboard(text,mode)
    finally:KEYBOARD_NAVIGATION=previous

def _handle_keyboard(text,mode):
    global SLEEP_RETURN_MODE
    if mode=='sleep' and (text=='@microphone' or normalized_command(text) in MICROPHONE_OFF_PHRASES+MICROPHONE_ON_PHRASES):
        # F9 while asleep: still asleep, so say how to wake up, not "Voice commands are on".
        return set_microphone(MICROPHONE_OFF if text=='@microphone' else normalized_command(text) in MICROPHONE_ON_PHRASES,'sleep')
    if mode=='sleep' and normalized_command(text) not in ('wake up','wake companion'):
        result=handle_keyboard(text,SLEEP_RETURN_MODE)
        # "go to sleep" typed while asleep returns 'sleep'; keep the place to wake up to.
        if result!='sleep':SLEEP_RETURN_MODE=result
        if SLEEP_RETURN_MODE=='exit':return 'exit'
        sync_app_context('sleep',True)
        return 'sleep'
    if mode=='mailbox' and mail_session and mail_session.folder_choice and mail_session.folder_choice[0]=='rename' and not text.startswith('rename to ') and text not in ('cancel','go back'):
        text='rename to '+text
    return handle(text,mode,typed=True)
def process_keyboard_batch(mode):
    """Apply every queued action in order and announce only the final navigation result."""
    global KEYBOARD_BATCH,KEYBOARD_BATCH_TEXT,KEYBOARD_BATCH_STATUS,SLEEP_RETURN_MODE
    previous=KEYBOARD_BATCH;KEYBOARD_BATCH=True
    if not previous:KEYBOARD_BATCH_STATUS=False
    # App mode page keys go to the page through the browser's own channel; they
    # do not move the window's keyboard focus. Unless another page opened, the
    # window is not refocused or given the page again after them.
    page_keys_only=True;page_before=getattr(getattr(web_session,'backend',None),'page',None)
    if web_session is not None:
        # Held or fast keys in app mode: the page gets each one at once, and
        # Companion reads the page only after the last of them.
        def more_page_keys():
            try:waiting=APP_WINDOW.commands.queue[0]
            except (IndexError,AttributeError):return False
            return isinstance(waiting,tuple) and waiting[0]=='keyboard' and str(waiting[1]).startswith('Web:Pass:')
        web_session.more_keys=more_page_keys
    try:
        # Bound each batch so microphone controls continue to be serviced.
        for _ in range(100):
            try:command=APP_WINDOW.commands.get_nowait()
            except queue.Empty:break
            if TRANSCRIPT.enabled:
                if isinstance(command,tuple) and command and command[0] in ('account_secret','web_private_value'):
                    TRANSCRIPT.remember_private(command[1].get('value') if isinstance(command[1],dict) else command[1])
                label=__import__('speech_transcript').key_label(command)
                if label:TRANSCRIPT.mode=SLEEP_RETURN_MODE+', asleep' if mode=='sleep' else mode;TRANSCRIPT.key(label)
            # The Escape that stopped a wait (user_stop) is not also used to go back.
            if user_stop.begin_command() and command in (('keyboard','Escape'),'Escape'):continue
            if not (isinstance(command,tuple) and command[0]=='keyboard' and str(command[1]).startswith('Web:Pass:')):page_keys_only=False
            if isinstance(command,tuple) and command[0]=='text_edit':
                # UI-thread caret speech is already current; acknowledge without canceling it.
                KEYBOARD_BATCH_TEXT=None
                try:apply_keyboard_edit(command[1],mode)
                except (ValueError,OSError) as exc:speak(str(exc))
                continue
            interrupt_speech()
            if isinstance(command,tuple):
                if command[0]=='web_search_query':
                    if mode=='sleep':SLEEP_RETURN_MODE=run_web_search(command[1])
                    else:mode=run_web_search(command[1])
                    sync_app_context(mode,True)
                    continue
                elif command[0]=='web_private_value':
                    if command[1].get('field_source') and command[1]['field_source']!=app_context(SLEEP_RETURN_MODE if mode=='sleep' else mode).get('web_field_source'):
                        speak('The page changed. The old private field was not saved.');continue
                    try:
                        next_key=command[1].get('next','Done')
                        if next_key=='Done' and web_session.keyboard_field:
                            web_session.keyboard_field['value']=command[1]['value']
                        else:web_session.commit_keyboard_field(command[1]['value'])
                        command='web key '+next_key
                    except Exception:command=None;speak('The private field could not be saved. Try again without saying its contents.')
                elif command[0]=='account_secret':
                    mode=finish_account_secret(command[1],mode);continue
                elif command[0]=='open_link':
                    # Enter on a link in an email or document: the guided browser opens it.
                    mode=open_email_link(command[1]);sync_app_context(mode,True);continue
                elif command[0]=='media_field':
                    speak(media_navigation().field_value(command[1]['value'],command[1]['next']));sync_app_context(mode,True);continue
                elif command[0]=='email_field_tab':
                    command=commit_email_field_tab(command[1],SLEEP_RETURN_MODE if mode=='sleep' else mode)
                else:
                    key=command[1]
                    if key=='Control':KEYBOARD_BATCH_TEXT=None
                    # Control+F12 wakes the microphone; other keys work in the screen behind sleep.
                    asleep=mode=='sleep' and key in __import__('input_parity').SCREEN_KEYS['sleep']
                    command='open options' if key=='settings' else navigation_key(key,'sleep' if asleep else SLEEP_RETURN_MODE if mode=='sleep' else mode)
                if command is None:continue
            mode=resume_from_sleep() if mode=='sleep' and command.lower() in ('wake up','wake companion') else handle_keyboard(command,mode)
            if mode=='exit':break
    finally:KEYBOARD_BATCH=previous
    if not previous and APP_WINDOW.commands.empty() and KEYBOARD_BATCH_TEXT:
        if KEYBOARD_BATCH_STATUS:finish_mail_announcement()
        text=KEYBOARD_BATCH_TEXT;KEYBOARD_BATCH_TEXT=None
        APP_WINDOW.feedback(punctuation_for_speech(text))
    if mode=='web' and APP_WINDOW is not None:
        refocus=not page_keys_only or getattr(getattr(web_session,'backend',None),'page',None) is not page_before
        if sync_app_context(mode,refocus) and hasattr(APP_WINDOW,'wait_for_context'):APP_WINDOW.wait_for_context()
    return mode

SLEEP_RETURN_MODE = 'awake'
MAIN_MENU_PROMPT = 'Main menu. What would you like to do? Say next or previous to move through the items, and okay, confirm, confirm that, or that one to open.'

# Said together with the next main menu announcement, in one message: a second
# message would stop the first one (the keyboard voice interrupts itself).
MAIN_MENU_LEAD = ''

def announce_main_menu():
    global MAIN_MENU_INDEX, MAIN_MENU_LEAD
    MAIN_MENU_INDEX = None
    lead, MAIN_MENU_LEAD = MAIN_MENU_LEAD, ''
    if lead.strip():speak(lead.strip() + ' ' + prompt_text(MAIN_MENU_PROMPT, VERBOSITY))
    else:speak_prompt(MAIN_MENU_PROMPT)

def startup_prompt():
    """The startup announcement for the microphone as it is now (startup_speech.py)."""
    import startup_speech
    return startup_speech.announcement(APP_VERSION, microphone_state())

MAIN_MENU_INDEX = None
MAIN_MENU_CHOICES = (('File manager', 'file manager'), ('Word processor', 'word processor'), ('Document reader', 'document reader'), ('Notepad', 'notepad'), ('Email', 'email'), ('Contacts', 'contacts'), ('Calendar', 'calendar'), ('Web browser', 'web browser'), ('Media', 'media menu'), ('Options', 'options'), ('Check for updates', 'check for updates'), ('Help', 'help'), ('Shut down the computer', 'shut down computer'), ('Restart the computer', 'restart computer'), ('Exit Voice Companion', 'exit companion'))


def resume_from_sleep():
    global MAIN_MENU_INDEX
    interrupt_speech()
    mode = SLEEP_RETURN_MODE
    if mode == 'awake':
        MAIN_MENU_INDEX = None
        announce_main_menu()
        sync_app_context(mode,True)
        return mode
    # The same screen names as everywhere else ("Back to Word processor menu."), never the internal mode.
    speak('I am listening. ' + options_return_notice(mode))
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
    if key=='exit_app':return handle('exit companion',SETTINGS_RETURN_MODE,typed=True)
    if key=='open_notepad' and mode=='note':
        speak('Notepad. Continue your note.');return 'note'
    if key in ('radio_presets','radio_recordings','podcast_subscriptions'):
        MEDIA_SECTION='podcast' if key=='podcast_subscriptions' else 'radio'
        speak(media_navigation().run(SETTINGS_ACTIONS[key]))
        return 'media'
    if key=='web_favorites':
        web_session=web_session or WebSession(APP,progress=speak_status)
        web_session.auto_read=PREFERENCES.get('web_auto_read',True)
        web_session.search_engine=PREFERENCES.get('web_search_engine','google')
        speak(web_session.command('list favorites'))
        return 'web'
    return handle(SETTINGS_ACTIONS[key],mode,typed=True)

NAMED_LIST=None

def open_named_list(title,items):
    global NAMED_LIST,pending_website
    pending_website=None
    NAMED_LIST={'title':title,'items':items,'index':0} if items else None
    return (title+' list. '+announce_item(items[0][0],0,len(items))+' Arrows move, Home and End reach boundaries, Enter selects, and Escape closes the list.') if items else 'Empty list.'

def menu_state(mode):
    """Expose only active pickers; editable fields and mail messages are excluded."""
    def state(names, owner, attribute, current=None):
        return (names, getattr(owner,attribute) if current is None else current,
                lambda index:setattr(owner,attribute,index)) if names else None
    module=sys.modules[__name__]
    if account_setup:return state([name for name,_ in ACCOUNT_TYPES],module,'ACCOUNT_MENU_INDEX')
    if mode in ('document_save','power_confirm','exit_confirm','update_offer','chrome_setup') or mode=='email_draft' and pending_send is not None:
        return None
    if NAMED_LIST:
        return ([item[0] for item in NAMED_LIST['items']],NAMED_LIST['index'],lambda index:NAMED_LIST.update(index=index))
    if ACCOUNT_PICKER is not None and not ACCOUNT_PICKER.get('confirm'):
        from account_picker import label
        picker=ACCOUNT_PICKER
        return ([label(a,picker['current']) for a in picker['accounts']],picker['index'],lambda index:picker.update(index=index))
    if SYNTH_PICK_INDEX is not None:
        return state([x[1] for x in synthesizers()],module,'SYNTH_PICK_INDEX')
    if VOICE_PICK_INDEX is not None:
        return state([x[1] for x in voice_choices(VOICE_PICK_ENGINE)],module,'VOICE_PICK_INDEX')
    # While a folder loads, the arrows say Please wait (FileManager.command), not End of list.
    if mode=='files' and FILE_MANAGER and not FILE_MANAGER.pending and not FILE_MANAGER.busy:
        names=FILE_MANAGER.labels()
        attribute='menu_index' if FILE_MANAGER.menu else 'index'
        return state(names,FILE_MANAGER,attribute)
    if mode=='downloads':return __import__('download_screen').menu_state(sys.modules[__name__])
    if mode=='contacts':return __import__('contacts_screen').menu_state(sys.modules[__name__])
    if mode=='attachments':return __import__('attachment_screen').menu_state(sys.modules[__name__])
    task_state=task_navigation().state(mode)
    if task_state is not None:return task_state
    if mode=='awake':return state([x[0] for x in MAIN_MENU_CHOICES],module,'MAIN_MENU_INDEX')
    if mode=='tutorial' and tutorial_session:return tutorial_session.menu()
    if mode=='mailbox' and mail_session and mail_session.folder_picker and not mail_session.pending:
        picker=mail_session.folder_picker
        return ([mail_session._spoken_folder(x[0]) for x in picker['folders']],picker['index'],lambda index:picker.update(index=index))
    if mode=='browser_menu' and web_session:return web_session.menu.menu(web_session,PREFERENCES.get('web_keep_open',False))
    if mode=='help' and help_session:
        if help_session.level=='topics':return state([x['name'] for x in help_session.topics],help_session,'topic_index')
        if help_session.level=='subtopics':return state([x[0] for x in help_session._subtopics()],help_session,'subtopic_index')
    if mode in ('media','podcast') and MEDIA_HUB:return media_navigation().state()
    if mode=='web' and web_session and web_session.form_index is None and not web_session.submit_pending:
        web=web_session
        if web.list_focus=='search_engines':return state(web.engine_names(),web,'engine_index')
        if web.list_focus=='favorites':return state([x[1]['name'] for x in web.favorite_items],web,'favorite_index')
        if web.list_focus=='fields':return state([x.get('name') or x.get('label') or x.get('text') or x['role'] for x in web.choices],web,'field_list_index')
        if web.list_focus=='headings':return state([__import__('web_browse').describe(x) for x in web.heading_items()],web,'heading_index')
        if web.list_focus=='links' and web.snapshot:
            from urllib.parse import urlsplit
            links=web.link_items()
            def select(index):web.link_position=index
            return ([x.get('label') or x.get('name') or x.get('text') or x.get('href') for x in links],web.link_position,select) if links else None
    return None

def choose_menu_letter(letter,mode):
    state=menu_state(mode)
    if state is None:return False
    names,current,select=state
    if mode=='browser_menu':
        speak(web_session.menu.letter(letter,names));return True
    index=next_match(names,current,letter)
    if index is None:speak('No menu item starts with '+letter.upper()+'.');return True
    select(index)
    if current==index:speak('End of matching items.');return True
    if VOICE_PICK_INDEX is not None:preview_voice(index)
    else:speak(('Link ' if mode=='web' and web_session and web_session.list_focus=='links' else '')+announce_item(names[index],index,len(names),hotkey=not (mode=='web' and web_session and web_session.list_focus in ('links','headings','fields'))))
    return True

def commit_email_field_tab(payload,mode):
    """Commit an editable header before moving, without losing unsent text."""
    if mode!='email_draft' or email_draft is None:return None
    field=payload['field'];value=payload['value'].strip()
    if field!=(email_draft.compose_step or 'body'):return None
    try:
        if field=='recipient':
            recipient=normalize_recipient(value) if value else ''
            if email_draft.response_context and recipient!=email_draft.recipient:email_draft.response_context['recipient_override']=True
            email_draft.recipient=recipient
        elif field in ('cc','bcc'):
            addresses=[normalize_recipient(x) for x in re.split(r'[,;]',value) if x.strip()]
            if email_draft.response_context and addresses!=getattr(email_draft,field):email_draft.response_context['recipient_override']=True
            setattr(email_draft,field,addresses)
        elif field=='subject':
            if email_draft.response_context and value!=email_draft.subject:
                raise ValueError('This response keeps the original subject.')
            email_draft.subject=value
        email_draft.save()
    except (ValueError,OSError) as exc:speak(str(exc));return None
    if payload.get('key'):return navigation_key(payload['key'],mode)
    return 'send email' if payload.get('send') else navigation_key('ShiftTab' if payload['backward'] else 'Tab',mode)

def change_display(request,mode):
    """Change the text size, colors or full screen, save it, say it, and show it at once."""
    import display_options
    values,words=display_options.apply_request(PREFERENCES,request)
    if request[0]!='status':
        PREFERENCES.update(values)
        try:__import__('app_settings').save_preferences(sys.modules[__name__])
        except OSError:words+=' This change could not be saved. It lasts until Companion closes.'
    speak(words)
    sync_app_context(mode)


# The PC's clock. Tests replace it with a fixed moment.
CLOCK = datetime.now


def speak_clock(kind):
    """Say the time ('time'), the date ('date') or both ('both')."""
    speak(time_date.announcement(kind, CLOCK()))


def clock_text_entry(mode, typed=False):
    """True where a single word such as "Time." or "Date." is text, not a request."""
    if writing_dictation(mode, typed):return True
    if mode=='note' and typed and INPUT_MODE!='commands':return True  # Words typed into the note box.
    if mode in ('search','document_name','task_input','podcast'):return True
    manager=globals().get('FILE_MANAGER')
    if mode=='files' and manager and manager.pending and manager.pending[0] in ('rename','search','search all'):return True
    if mode=='media' and MEDIA_NAV is not None and getattr(MEDIA_NAV,'form',None):return True
    return mode=='web' and web_session is not None and bool(getattr(web_session,'keyboard_field',None))


def navigation_key(key,mode):
    global KEYBOARD_NAVIGATION
    if key in time_date.KEY_REQUESTS:
        # F12 or Insert+F12 works everywhere, even in setup, lists and questions; it changes nothing.
        previous=KEYBOARD_NAVIGATION;KEYBOARD_NAVIGATION=True
        try:interrupt_speech();speak_clock(time_date.KEY_REQUESTS[key])
        finally:KEYBOARD_NAVIGATION=previous
        return None
    if key=='F9':
        # F9 turns the microphone fully off or on, on every screen.
        return '@microphone'
    if key=='Ctrl+F1':
        # Control+F1 goes back to the main menu from every screen.
        return '@main_menu'
    if key.startswith('Display:'):
        # Control+Plus, Control+Minus, Control+0, Control+9 and F11 change the screen on every screen.
        previous=KEYBOARD_NAVIGATION;KEYBOARD_NAVIGATION=True
        try:
            interrupt_speech()
            request=__import__('display_options').KEY_REQUESTS.get(key)
            if request:change_display(request,mode)
        finally:KEYBOARD_NAVIGATION=previous
        return None
    if ACCOUNT_ENTRY is not None and key!='Control':
        # Setup owns the keyboard: keys must not reach the menu behind the text box.
        if key=='Escape':return 'cancel account setup'
        if key=='F1':return 'help'
        if key in ('Enter','Space','Up','Down','Left','Right','Home','End','Delete','Tab','ShiftTab','F5'):return 'account entry empty'
        return 'account entry busy'
    previous=KEYBOARD_NAVIGATION;KEYBOARD_NAVIGATION=True
    try:return _navigation_key(key,mode)
    finally:KEYBOARD_NAVIGATION=previous

def _navigation_key(key,mode):
    global CONFIRM_CHOICE, ACCOUNT_MENU_INDEX
    interrupt_speech()
    if key=='Ctrl+P':return 'toggle audio'
    if key=='Control':return None
    menu_key=screen_menu().keyboard(key,mode)
    if menu_key is not None:return menu_key
    if ACCOUNT_PICKER is not None and ACCOUNT_PICKER.get('mode')==mode and not ACCOUNT_PICKER.get('confirm'):
        request=account_picker_key(key)
        if request!='':return request
    task_key=task_navigation().keyboard(key,mode)
    if task_key is not None:return task_key
    if mode=='calendar' and __import__('calendar_screen').PENDING is not None:
        # A question on the Calendar owns the arrows and Enter (this one, all, or no).
        return __import__('calendar_screen').navigation_key(sys.modules[__name__],key)
    from input_parity import screen_command
    direct=screen_command(key,mode)
    if direct=='help with radio' and MEDIA_SECTION=='podcast':direct='help with podcasts'
    if direct:return direct
    if mode=='downloads':return __import__('download_screen').navigation_key(sys.modules[__name__],key)
    if mode=='attachments' and not NAMED_LIST:return __import__('attachment_screen').navigation_key(sys.modules[__name__],key)
    if mode=='update_download':
        return {'F5':'update status','Alt+S':'update status','Escape':'cancel update','Alt+C':'cancel update'}.get(key)
    if mode=='files':
        if key=='Ctrl+E':
            if FILE_MANAGER:FILE_MANAGER.menu=False;FILE_MANAGER.pending=None
            return 'search files'
        if FILE_MANAGER and FILE_MANAGER.pending and FILE_MANAGER.pending[0]=='delete':
            if key in ('Up','Left','Home','ShiftTab'):CONFIRM_CHOICE='yes';sync_app_context(mode);speak('Yes.');return None
            if key in ('Down','Right','End','Tab'):CONFIRM_CHOICE='no';sync_app_context(mode);speak('No.');return None
            return {'Enter':CONFIRM_CHOICE,'Space':CONFIRM_CHOICE,'Escape':'no','y':'yes','Y':'yes','n':'no','N':'no'}.get(key)
        return {'Up':'previous','Down':'next','Left':'previous','Right':'next','Home':'home','End':'end','Enter':'enter','Space':'toggle selection','Escape':'escape','Alt':'file manager menu','F1':'file manager help','F2':'rename','Delete':'delete','Ctrl+C':'copy','Ctrl+X':'cut','Ctrl+V':'paste','Ctrl+A':'select all','Ctrl+Shift+A':'clear selection','F5':'refresh','Ctrl+D':'drive list','Ctrl+E':'search files','Ctrl+F':'search files','Alt+N':'sort by name','Alt+C':'sort by date created','Alt+M':'sort by date modified','Alt+D':'sort by date added','Alt+A':'sort ascending','Alt+Z':'sort descending','Ctrl+Shift+F':'search subfolders','Ctrl+Shift+X':'clear search','Alt+S':'sort files','Alt+Home':'main menu','Ctrl+Shift+1':'sort by name','Ctrl+Shift+2':'sort by date created','Ctrl+Shift+3':'sort by date modified','Ctrl+Shift+4':'sort by date added','Ctrl+Shift+Up':'sort ascending','Ctrl+Shift+Down':'sort descending','BackSpace':'file backspace'}.get(key) if not key.startswith('Letter:') else ('file letter '+key[7:])
    if mode=='file_reader' and key=='Escape':return 'back to files'
    if mode=='file_reader':
        return {'Insert+F':'what formatting','CapsLock+F':'what formatting','Insert+Shift+Down':'what is selected','CapsLock+Down':'what is selected','H':'next heading','Shift+H':'previous heading','T':'next table','Shift+T':'previous table','K':'next link','Shift+K':'previous link','L':'next list','Shift+L':'previous list','B':'next bookmark','Shift+B':'previous bookmark','N':'next footnote','Shift+N':'previous footnote','Enter':'follow link'}.get(key)
    if mode=='regular_browser':
        if key=='Escape':return 'main menu'
        if key in ('Up','Down','Home','End','Enter','Space'):return 'regular browser key '+key
        return None
    if mode=='web' and web_session and web_session.list_focus in ('links','headings') and key in ('Up','Down','Left','Right','Home','End','Enter','Escape'):
        web_session.list_prefix='';web_session.list_prefix_time=0
    if NAMED_LIST and key in ('Up','Down','Left','Right','Enter','Escape'):return {'Up':'previous','Left':'previous','Down':'next','Right':'next','Enter':'okay','Escape':'close list'}[key]
    # The Contacts screen (contacts_screen.py); a list opened over it keeps its own keys.
    if mode=='contacts' and not NAMED_LIST:return __import__('contacts_screen').navigation_key(sys.modules[__name__],key)
    if mode=='calendar' and not NAMED_LIST:return __import__('calendar_screen').navigation_key(sys.modules[__name__],key)
    if key=='Alt+F4':return 'exit companion'
    if mode=='chrome_setup' and not chrome_confirming(mode):
        return {'Escape':'cancel','F5':'status','F1':'status'}.get(key)
    confirming=THUNDERBIRD_SETUP is not None or ACCOUNT_PICKER is not None and ACCOUNT_PICKER.get('confirm') or pending_website is not None or chrome_confirming(mode) or mode in ('document_save','power_confirm','exit_confirm','update_offer') or mode=='email_draft' and pending_send is not None or (mode=='mailbox' and mail_session and mail_session.pending) or (mode=='web' and web_session and (web_session.pending or web_session.submit_pending))
    if confirming:
        if key.lower() in ('y','n','alt+y','alt+n'):return 'yes' if key.lower() in ('y','alt+y') else 'no'
        if key in ('Up','Down','Left','Right','Tab','ShiftTab'):
            choice='yes' if key in ('Up','Left','ShiftTab') else 'no'
            if choice==CONFIRM_CHOICE:speak('Start of list.' if choice=='yes' else 'End of list.');return None
            CONFIRM_CHOICE=choice;speak(choice.capitalize()+'.');return None
        if key in ('Home','End'):
            choice='yes' if key=='Home' else 'no'
            if choice==CONFIRM_CHOICE:speak('Start of list.' if key=='Home' else 'End of list.');return None
            CONFIRM_CHOICE=choice;speak(CONFIRM_CHOICE.capitalize()+'.');return None
        if key=='Enter':return CONFIRM_CHOICE
        if key=='Escape':return 'cancel' if mode=='document_save' else 'no'
    if mode in ('media','podcast') and MEDIA_HUB:
        result=media_navigation().keyboard(key)
        if result=='__main__':return 'media menu'
        if result:speak(result)
        sync_app_context(mode,True)
        return None
    if mode=='browser_menu':
        if key in ('Up','Down','Left','Right','Home','End','Enter','Escape'):
            return {'Up':'previous','Left':'previous','Down':'next','Right':'next','Home':'home','End':'end','Enter':'enter','Escape':'escape'}[key]
        if key.startswith('Letter:'):return key
    if mode=='web' and key in ('Escape','Web:Escape') and web_session and isinstance(web_session.snapshot,dict) and web_session.snapshot.get('modal_key') and not web_session.list_focus:return 'web key Escape'
    if mode=='web' and key=='Escape' and web_session and not (web_session.list_focus or web_session.keyboard_field or isinstance(getattr(web_session,'slider_field',None),dict)):return 'web browser menu'
    if mode=='web' and key=='Web:Escape' and web_session and web_session.keyboard_field is None and not web_session.list_focus and not isinstance(getattr(web_session,'slider_field',None),dict):return 'web browser menu'
    if mode=='web' and key in ('F6','F7'):return 'list headings' if key=='F6' else 'list links'
    if mode=='web' and web_session and web_session.list_focus in ('links','headings') and key=='Enter':return 'open current heading' if web_session.list_focus=='headings' else 'open current link'
    if mode=='web' and key.startswith('Web:'):return 'web key '+key[4:]
    if mode=='search' and key=='Escape':return 'go back'
    if mode=='mailbox' and key.startswith('Email:'):return key[6:]
    if key in ('Home','End'):
        state=menu_state(mode)
        if state:
            names,current,select=state;index=0 if key=='Home' else len(names)-1
            if current==index:speak('Start of list.' if key=='Home' else 'End of list.');return None
            select(index)
            if VOICE_PICK_INDEX is not None:preview_voice(index);return None
            speak(('Link ' if mode=='web' and web_session and web_session.list_focus=='links' else '')+announce_item(names[index],index,len(names),hotkey=not (mode=='web' and web_session and web_session.list_focus in ('links','headings','fields'))));return None
        if mode=='mailbox' and mail_session and mail_session.view=='folder' and not mail_session.pending:
            speak(mail_session.page_edge(key=='End'));return None
    if key in ('Ctrl+Home','Ctrl+End') and mode=='mailbox' and mail_session and mail_session.view=='folder' and not mail_session.pending:
        # The newest or oldest message in the whole folder.
        return 'oldest message' if key=='Ctrl+End' else 'newest message'
    if mode=='help' and key=='Escape':return 'go back'
    if mode=='help' and help_session and help_session.level=='article' and (key.startswith('Help:') or key in ('Up','Down','Left','Right','Home','End')):
        return {'Up':'previous paragraph','Down':'next paragraph','Home':'first block','End':'last block',
                'Left':'previous character','Right':'next character','Ctrl+Left':'previous word','Ctrl+Right':'next word'}[key[5:] if key.startswith('Help:') else key]
    if key.startswith('Letter:'):
        if mode=='web' and web_session and not web_session.list_focus and key[-1] in __import__('web_browse').QUICK_KEYS:return 'web key Quick:'+key[-1]
        choose_menu_letter(key[-1],mode);return None
    if key=='Enter' and account_setup and ACCOUNT_MENU_INDEX is not None:
        return ACCOUNT_TYPES[ACCOUNT_MENU_INDEX][1]
    if account_setup and key in ('Up','Down','Left','Right'):
        step=1 if key in ('Down','Right') else -1
        if ACCOUNT_MENU_INDEX is not None and not 0<=ACCOUNT_MENU_INDEX+step<len(ACCOUNT_TYPES):speak('Start of list.' if step<0 else 'End of list.');return None
        ACCOUNT_MENU_INDEX=bounded_index(ACCOUNT_MENU_INDEX,step,len(ACCOUNT_TYPES))
        speak(announce_item(ACCOUNT_TYPES[ACCOUNT_MENU_INDEX][0],ACCOUNT_MENU_INDEX,len(ACCOUNT_TYPES)));return None
    if mode=='tutorial' and tutorial_session:
        if key=='Escape':return 'go back'
        if key=='Enter':return 'that one' if tutorial_session.menu() else 'next lesson'
        if key in ('Down','Right'):return 'next' if tutorial_session.menu() else 'next lesson'
        if key in ('Up','Left'):return 'previous' if tutorial_session.menu() else 'previous lesson'
    if mode=='email_draft' and key in ('Tab','ShiftTab') and email_draft:
        order=('recipient','cc','bcc','subject','body')
        current=email_draft.compose_step or 'body'
        target=order[(order.index(current)+(1 if key=='Tab' else -1))%len(order)]
        return 'go to '+('to' if target=='recipient' else target)
    if mode=='document' and key=='F2':return 'name document'
    if mode=='note' and key=='Ctrl+O':return 'list notes'
    if mode=='mailbox':
        # Escape leaves a folder list (with several accounts, back to the account list).
        if key=='Escape' and mail_session and (getattr(mail_session,'folder_picker',None) or getattr(mail_session,'folder_choice',None)):return 'cancel'
        if key=='Delete' and mail_session and mail_session.folder_picker:
            picker=mail_session.folder_picker
            return 'delete folder '+picker['folders'][picker['index']][0]
        request={'Ctrl+Shift+V':'move','Ctrl+Shift+E':'create folder','Ctrl+Y':'go to folder','Ctrl+R':'reply','Ctrl+Shift+R':'reply all','Ctrl+F':'forward','Ctrl+N':'new email','Ctrl+Shift+C':'copy sender address','Ctrl+Shift+N':'new email to sender','Delete':'delete','Space':'toggle message selection','ShiftUp':'extend selection previous','ShiftDown':'extend selection next','Ctrl+A':'select all messages','F2':'rename folder','Ctrl+Shift+D':'delete folder','F5':'list messages'}.get(key)
        if request:return request
    if mode=='email_draft' and key in ('Ctrl+Enter','Alt+S'):return 'send email'
    if key=='Alt+F4':return 'exit companion'
    if mode=='document_name' and key=='Escape':return 'cancel naming'
    if mode=='mailbox' and key=='Escape' and mail_session is not None and mail_session.view=='message':return 'go back'
    if mode=='settings':
        return 'cancel settings' if key=='Escape' else None
    if key=='Enter' and mode=='mailbox' and mail_session is not None and not (getattr(mail_session,'folder_picker',None) or getattr(mail_session,'folder_choice',None) or getattr(mail_session,'pending',None)):
        return 'open current message'
    editor=document if mode=='document' else email_draft if mode=='email_draft' else None
    if editor is not None and (getattr(editor,'selection_candidates',[]) or getattr(editor,'replacement_candidates',[]) or getattr(editor,'pending_spacing',False)):
        if key=='Enter':return 'ok'
        if key in ('Home','End'):return 'first item' if key=='Home' else 'last item'
        if key=='Escape':return 'cancel replacement' if getattr(editor,'replacement_candidates',[]) else 'cancel selection' if getattr(editor,'selection_candidates',[]) else 'cancel'
        if key in ('Up','Left'):return 'previous'
        if key in ('Down','Right'):return 'next'
    if mode=='awake' and key=='Escape':return 'exit companion'
    return keyboard_request(key,mode)

def apply_settings(values):
    from app_settings import apply_settings as apply
    return apply(sys.modules[__name__],values)

FILE_MANAGER=None
FILE_READER=None
TASK_NAV=None
SCREEN_MENU=None

def screen_menu():
    """The Alt menu of the current screen (screen_menus.py)."""
    global SCREEN_MENU
    from screen_menus import ScreenMenu
    if SCREEN_MENU is None or SCREEN_MENU.app is not sys.modules[__name__]:SCREEN_MENU=ScreenMenu(sys.modules[__name__])
    return SCREEN_MENU

def chrome_confirming(mode):
    return mode=='chrome_setup' and CHROME_SETUP is not None and CHROME_SETUP.question is not None

def task_navigation():
    global TASK_NAV
    from task_navigation import TaskNavigation
    if TASK_NAV is None or TASK_NAV.app is not sys.modules[__name__]:TASK_NAV=TaskNavigation(sys.modules[__name__])
    return TASK_NAV

def options_return_notice(mode):
    logical=SLEEP_RETURN_MODE if mode=='sleep' else mode
    return {'awake':'Main menu.','document':'Back to document.','email_draft':'Back to email draft.','mailbox':'Back to email.','web':'Back to web page.','browser_menu':'Back to web browser menu.','note':'Back to your note.','notepad_menu':'Back to Notepad menu.','reader_menu':'Back to Document reader menu.','files':'Back to file manager.','word_processor':'Back to Word processor menu.','document_menu':'Back to document menu.','media_menu':'Back to Media menu.','local_media':'Back to Media player.','recent_documents':'Back to recent documents list.','reader_recent':'Back to recent documents list.','file_reader':'Back to document reader.','help':'Back to help.','media':'Back to media.','attachments':'Back to attachments.'}.get(logical,'Back to '+logical.replace('_',' ')+'.')

def poll_file_manager(mode):
    global FILE_READER,SLEEP_RETURN_MODE
    if FILE_MANAGER is None:return mode
    notice=FILE_MANAGER.poll()
    logical=SLEEP_RETURN_MODE if mode=='sleep' else mode
    if logical=='files':
        if FILE_MANAGER.opened is not None and isinstance(FILE_MANAGER.opened,Path):
            selected=FILE_MANAGER.opened;FILE_MANAGER.opened=None
            try:
                result=task_navigation().selected_file(selected)
            except (OSError,ValueError) as exc:
                speak(str(exc));return mode
            if mode=='sleep':SLEEP_RETURN_MODE=result
            else:mode=result
            sync_app_context(mode,True)
            return mode
        if FILE_MANAGER.opened is not None:
            FILE_READER=FILE_MANAGER.opened;FILE_MANAGER.opened=None
            if task_navigation().picker=='document':
                task_navigation().remember(FILE_READER.path);task_navigation().picker=None
            notice=notice or 'Opened '+FILE_READER.title+'. Read-only document. '+FILE_READER.reading.read(True)
            if mode=='sleep':SLEEP_RETURN_MODE='file_reader'
            else:mode='file_reader'
        if notice:speak(notice)
        if notice:sync_app_context(mode,True)
    return mode

def options_closed(mode):
    """Options closed without saving (Escape, Cancel, Close): say where you are now."""
    __import__('app_settings').stop_preview(sys.modules[__name__])
    mode=SETTINGS_RETURN_MODE
    sync_app_context(mode,True)
    speak('Options closed. '+options_return_notice(mode))
    return mode

def save_options(values,mode):
    """Return to the previous task only after settings were saved successfully."""
    global SLEEP_RETURN_MODE
    __import__('app_settings').stop_preview(sys.modules[__name__])
    apply_settings(values)
    SETTINGS_PANEL.accepted()
    if APP_WINDOW is not None:
        # Old field notices must not interrupt the successful save message.
        while not APP_WINDOW.settings_notices.empty():APP_WINDOW.settings_notices.get_nowait()
    if mode=='sleep' and SLEEP_RETURN_MODE=='settings':SLEEP_RETURN_MODE=SETTINGS_RETURN_MODE
    elif mode=='settings':mode=SETTINGS_RETURN_MODE
    sync_app_context(mode,True)
    speak('Options saved. '+options_return_notice(mode))
    return mode

def apply_document_defaults(document):
    from app_settings import apply_document_defaults as apply
    apply(sys.modules[__name__],document)

def microphone_settings(sd,device_index=None):
    device = sd.query_devices(kind='input') if device_index is None else sd.query_devices(device_index,kind='input')
    rate = int(round(device['default_samplerate']))
    for channels in (1, 2):
        if channels > device.get('max_input_channels', 1):
            continue
        try:
            sd.check_input_settings(samplerate=rate, channels=channels, dtype='int16',**({'device':device_index} if device_index is not None else {}))
            PCM16Resampler(rate)
            return device, rate, channels
        except (ValueError, OSError, getattr(sd, 'PortAudioError', OSError)):
            continue
    raise OSError('No supported mono or stereo microphone format was found.')

def microphone_active(stream):
    try:return bool(stream.active)
    except Exception:return False

def close_microphone(stream):
    """A disconnected device must not prevent shutdown."""
    try:stream.close()
    except Exception:pass

def open_microphone(sd,attempts=5):
    """Drivers can need a moment after the installer listener releases input."""
    global resampler,input_channels
    for attempt in range(attempts):
        stream=None
        try:
            _,rate,channels=microphone_settings(sd)
            resampler=PCM16Resampler(rate,16000);input_channels=channels
            stream=sd.RawInputStream(samplerate=rate,blocksize=max(1,rate//20),dtype='int16',channels=channels,callback=callback)
            stream.start()
            return stream
        except Exception:
            if stream:
                try:stream.close()
                except Exception:pass
            if attempt+1<attempts:time.sleep(.3)
    return None


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


# "go to options" or "go to website ..." in the inbox are app commands, not folder names.
MAILBOX_GLOBAL_GO_TO = r'go to (?:the )?(?:main menu|options|settings|website )'


SEND_REVIEW_ANSWERS = ('confirm send email', 'yes send email', 'yes send it', 'yes send that', 'yes', 'yes please',
                       'confirm send', 'cancel send', 'do not send', 'no', 'no thanks')


def writing_dictation(mode, typed=False):
    """True when spoken words in this mode may be ordinary text for a document, email, or note.

    Several commands are recognized by their first words ("rate ...", "capital letters ...",
    "help with ...", "use ... at ..."). In a writing field, a sentence that only starts that
    way and does not complete the command is dictation, not an error."""
    editor_open = (mode == 'document' and document is not None or
                   mode == 'email_draft' and email_draft is not None or mode == 'note')
    return not typed and INPUT_MODE != 'commands' and editor_open


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
    if mode in ('document','email_draft','mailbox','help','web','note','file_reader') and reading_request(command) and not (mode in ('document','email_draft') and INPUT_MODE=='dictation'):return True
    if command in ('word processor','open word processor','notepad','open notepad'):return True
    if re.fullmatch(r'(?:(?:set|choose|use) )?(?:default )?search engine(?: to)? (google|bing|brave(?: search)?|duck duck go|duckduckgo)',command):return True
    if command in POWER_COMMANDS:return True
    if mode in ('settings','power_confirm','exit_confirm','media','help','browser_menu','chrome_setup'): return True
    if command in ('check for updates',"what's new",'what is new','whats new','release notes','restart computer','shut down computer'):return True
    if command in ('options','open options','show options','settings','open settings','show settings','preferences','open preferences'):return True
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
                   'louder', 'quieter', 'use sapi', 'use sapi voice', 'use windows voice', 'use ai voice', 'use espeak'):
        return True
    if mode in ('document', 'email_draft') and INPUT_MODE != 'dictation':
        from document_editor import START_COMMANDS, END_COMMANDS, FORMAT_READ_COMMANDS, document_navigation_request
        return bool(document_navigation_request(command)) or command in START_COMMANDS or command in END_COMMANDS or command in FORMAT_READ_COMMANDS or structural_selection_request(command)
    return False

def navigation_feedback_control(value,mode):
    command=normalized_command(value)
    if mode in ('document','email_draft','note') and INPUT_MODE=='dictation':return False
    if re.fullmatch(r'(?:next|previous|prior)(?: (?:item|option|voice|synthesizer|message|folder|preset|station|episode|podcast|lesson|topic|paragraph|line|word|sentence|character|heading|link|button|field|list|list item|region|frame))?',command):return True
    return mode=='web' and web_feedback_control(command,mode)

def interrupt_partial_navigation(recognizer,mode):
    try:partial=json.loads(recognizer.PartialResult()).get('partial','')
    except (ValueError,AttributeError):return False
    if not navigation_feedback_control(partial,mode):return False
    if speech_busy():interrupt_speech()
    return True


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


def web_feedback_control(text,mode):
    if mode in ('update_offer','update_download'):
        return fast_command_request(normalized_command(text),mode)
    if mode!='web' or not web_session:return False
    command=normalized_command(text)
    if web_session.pending or web_session.submit_pending:
        return command in ('yes','yes please','no','no thanks','cancel','okay','ok','confirm','confirm that')
    if web_session.list_focus=='search_engines':return True
    if web_session.list_focus in ('links','headings'):
        return command in ('next','previous','open current link','open current heading','go to heading','that one','okay','ok','cancel','close list','back to page')
    from web_browse import voice_browse_key
    return bool(voice_browse_key(command) or reading_request(command)) or command in ('list links','list headings','read page','leave website','go back','top of page','beginning of page','start of page','bottom of page','end of page','go to top of page','go to beginning of page','go to bottom of page','go to end of page')

def interrupt_for_dictation():
    # New speech cancels current audio, not the words of an unfinished sentence.
    sentence,choice=DICTATION_ECHO.sentence,DICTATION_ECHO.choice
    interrupt_speech()
    DICTATION_ECHO.sentence,DICTATION_ECHO.choice=sentence,choice

def interrupt_speech():
    global SPEECH_PAUSED
    DICTATION_ECHO.clear()
    if APP_WINDOW is not None:APP_WINDOW.ui_actions.put(('cancel_phonetic',None))
    if KEYBOARD_SPEECH is not None:KEYBOARD_SPEECH.interrupt(notify=False)
    preview=globals().get('SETTINGS_PREVIEW_SPEECH')
    if preview:preview.interrupt()
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
    from capital_feedback import preserve_character
    return preserve_character(text,_punctuation_for_speech(text))


def _punctuation_for_speech(text):
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
    preview=globals().get('SETTINGS_PREVIEW_SPEECH')
    if preview and preview.busy():return True
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

AUDIO_DUCKER = None


def audio_ducker():
    """The worker that lowers browser and media sound while Companion speaks (Windows only)."""
    global AUDIO_DUCKER
    if AUDIO_DUCKER is None and not TEXT_MODE and os.name == 'nt':
        import atexit
        from audio_ducking import AudioDucker, WindowsAudioSessions
        AUDIO_DUCKER = AudioDucker(WindowsAudioSessions, APP / 'audio-ducking.json')
        AUDIO_DUCKER.recover()
        AUDIO_DUCKER.start()
        # Never leave other programs quiet when Companion closes or stops on an error.
        atexit.register(AUDIO_DUCKER.close)
    return AUDIO_DUCKER


def audio_ducking_request(command):
    """Spoken audio ducking commands, already normalized ("Turn audio ducking off.", "Ducking level, strong.").

    Returns (setting, value), with value None for a level that is not one of the choices."""
    switch=(re.fullmatch(r'(?:(?:set|turn|switch) )?(?:the )?(?:audio )?ducking(?: to)? (on|off)',command) or
            re.fullmatch(r'(?:turn|switch) (on|off) (?:the )?(?:audio )?ducking',command) or
            re.fullmatch(r'(?:set )?duck audio(?: to)? (on|off)',command))
    if switch:return 'duck_audio',switch[1]=='on'
    enable=re.fullmatch(r'(enable|disable) (?:the )?(?:audio )?ducking',command)
    if enable:return 'duck_audio',enable[1]=='enable'
    level=re.fullmatch(r'(?:(?:set|change|use|choose|make) )?(?:the )?(?:audio )?ducking level(?: to)?(?: (.+))?',command)
    if level:
        from audio_ducking import spoken_level
        return 'duck_level',spoken_level(level[1] or '')
    others=(re.fullmatch(r'(?:(?:set|turn|switch) )?(?:also )?(?:lower|duck) other apps(?: too)?(?: to)? (on|off)',command) or
            re.fullmatch(r'(?:turn|switch) (on|off) (?:also )?(?:lower|duck)(?:ing)? (?:of )?other apps(?: too)?',command))
    if others:return 'duck_other_apps',others[1]=='on'
    return None


def update_audio_ducking(active=None):
    from audio_ducking import level_factor
    enabled = bool(PREFERENCES.get('duck_audio', True))
    lowered = enabled and bool((not SPEECH_PAUSED and speech_busy()) if active is None else active)
    factor = level_factor(PREFERENCES.get('duck_level'))
    ducker = None
    try:
        ducker = audio_ducker()
        if ducker is not None:
            ducker.request(lowered, factor, enabled and bool(PREFERENCES.get('duck_other_apps', False)))
    except Exception as exc:
        print('Audio ducking unavailable:', exc, file=sys.stderr)
        ducker = None
    if MEDIA_HUB is None:
        return
    # The radio player runs in a Firefox that Companion started, so Windows session
    # ducking already lowers it. Lower it inside the page only when that is not
    # working, so it is never lowered twice.
    session_ducking = ducker is not None and ducker.available is not False
    try:
        MEDIA_HUB.player.set_ducked(lowered and not session_ducking, factor)
    except Exception as exc:
        print('Audio ducking unavailable:', exc, file=sys.stderr)


def save_speech_settings():
    from storage import write_text_atomic
    write_text_atomic(APP / 'speech-settings.json',
        json.dumps({'pitch':SPEECH_PITCH,'rate': SPEECH_RATE, 'volume': SPEECH_VOLUME, 'punctuation': PUNCTUATION_LEVEL, 'verbosity':VERBOSITY, 'input_mode':INPUT_MODE,
                    'ai_enabled': bool(AI_SPEECH and AI_SPEECH.enabled), 'ai_voice': AI_VOICE_NAME,
                    'espeak_enabled': bool(ESPEAK_SPEECH and ESPEAK_SPEECH.enabled), 'espeak_voice': ESPEAK_VOICE_NAME,
                    'voice': voice.Voice.GetDescription() if not TEXT_MODE else ''}))



def change_speech_setting(command, writing=False):
    global SPEECH_PITCH, SPEECH_RATE, SPEECH_VOLUME
    request = speech_setting_request(command)
    if request is None: return False
    kind, operation, amount = request
    lower, upper = (-10, 10) if kind in ('rate','pitch') else (0, 100)
    # "Rate this product five stars." or "Speed limits matter." while writing is text.
    # A short attempt such as "volume banana" or "rate 1.5" is still a setting error.
    if writing and amount is None and len(re.findall(r"[\w'.-]+", command)) > 2: return False
    if amount is None or (operation == 'set' and not lower <= amount <= upper):
        speak(('Voice '+kind+' must be a whole number from minus ten to ten.') if kind in ('rate','pitch') else
              'Speech volume must be a whole number from zero to one hundred percent.')
        return True
    current = SPEECH_RATE if kind == 'rate' else SPEECH_PITCH if kind=='pitch' else SPEECH_VOLUME
    value = max(lower, min(upper, current + amount if operation == 'delta' else amount))
    if kind=='pitch':SPEECH_PITCH=value
    elif kind == 'rate':
        SPEECH_RATE = value
        if not TEXT_MODE: voice.Rate = value
    else:
        SPEECH_VOLUME = value
        if not TEXT_MODE: voice.Volume = value
    save_speech_settings()
    if KEYBOARD_SPEECH and not TEXT_MODE:KEYBOARD_SPEECH.configure(voice.Voice.Id,SPEECH_RATE,SPEECH_VOLUME,ESPEAK_SPEECH,SPEECH_PITCH,ai=AI_SPEECH,capital_letters=PREFERENCES.get('capital_letters','do nothing'))
    speak('Voice '+kind+' '+str(value)+'.' if kind in ('rate','pitch') else 'Volume '+str(value)+' percent.')
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
    choices = [('windows', 'SAPI')]
    if ESPEAK_SPEECH is not None: choices.append(('espeak', 'eSpeak NG'))
    if AI_SPEECH is not None: choices.append(('ai', 'AI voice'))
    return choices


def preview_voice(index):
    choices = voice_choices(VOICE_PICK_ENGINE)
    name = choose_voice(choices[index])
    speak(announce_item(name,index,len(choices)))


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


def use_synthesizer(text):
    global SYNTH_PICK_INDEX, VOICE_PICK_INDEX, VOICE_PICK_ORIGINAL, VOICE_PICK_ENGINE
    if normalized_command(text) not in ('use sapi','use sapi voice','use ai voice','use windows voice','use espeak','use espeak voice','use e speak'):return False
    requested = normalized_command(text)
    kind = 'windows' if requested in ('use windows voice','use sapi','use sapi voice') else 'ai' if requested == 'use ai voice' else 'espeak'
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
    return True


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
            SYNTH_PICK_INDEX = bounded_index(SYNTH_PICK_INDEX,step,len(synthesizers()))
        else:
            VOICE_PICK_INDEX = bounded_index(VOICE_PICK_INDEX,step,len(voice_choices(VOICE_PICK_ENGINE)))
            preview_voice(VOICE_PICK_INDEX)
            return True
    choices = synthesizers()
    prompt = announce_item(choices[SYNTH_PICK_INDEX][1],SYNTH_PICK_INDEX,len(choices))
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
        voice=ESPEAK_VOICE_NAME, program=program,pitch=SPEECH_PITCH), engine_name='eSpeak', error_callback=record_espeak_error)
    return True


def configure_ai_speech():
    global AI_SPEECH, AI_VOICE_NAME
    from ai_voice_settings import load
    from ai_speech import AISpeech, synthesize
    settings = load(APP)
    if not settings.get('key') or not narration_allowed(PREFERENCES,settings):
        if AI_SPEECH:AI_SPEECH.close();AI_SPEECH=None
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
        key=settings['key'], voice=AI_VOICE_NAME,pitch=SPEECH_PITCH)
    return True


def speak_prompt(text):
    speak(prompt_text(text,VERBOSITY))


from dictation_echo import DictationEcho
DICTATION_ECHO=DictationEcho()

def dictation_feedback(result):
    if str(result).startswith('Added: '):
        if TEXT_MODE:
            if PREFERENCES.get('dictation_echo')!='do not echo dictated text':speak(str(result)[7:])
            return
        DICTATION_ECHO.add(str(result)[7:], PREFERENCES.get('dictation_echo','phrase by phrase'))
        if APP_WINDOW is not None:APP_WINDOW.show('Text added.')
        return
    DICTATION_ECHO.clear()
    speak(result)

def poll_dictation_echo():
    if DICTATION_ECHO.choice is not None and DICTATION_ECHO.choice!=PREFERENCES.get('dictation_echo','phrase by phrase'):DICTATION_ECHO.clear()
    text=DICTATION_ECHO.take(busy=speech_busy())
    if text:speak(text)


def discard_feedback_events(cloud):
    """Drop narration transcripts, keeping connection errors for fallback."""
    remaining=[]
    while True:
        try:event=cloud.events.get_nowait()
        except queue.Empty:break
        if event[0]!='text':remaining.append(event)
    for event in remaining:cloud.events.put(event)


def speak(text):
    global KEYBOARD_BATCH_TEXT
    if KEYBOARD_BATCH:
        KEYBOARD_BATCH_TEXT=text;return
    TRANSCRIPT.said(text)   # off unless chosen; never raises
    if TEXT_MODE:print('Companion:', text, flush=True)
    if APP_WINDOW is not None:
        APP_WINDOW.show(text)
    if TEXT_MODE:
        return
    update_audio_ducking(True)
    if KEYBOARD_NAVIGATION and KEYBOARD_SPEECH and not KEYBOARD_SPEECH.problem:
        KEYBOARD_SPEECH.speak(punctuation_for_speech(text));return
    from capital_feedback import is_capital,prepare,play_sound
    if is_capital(text):
        if KEYBOARD_SPEECH and not KEYBOARD_SPEECH.problem:
            KEYBOARD_SPEECH.speak(punctuation_for_speech(text));return
        text=prepare(text,PREFERENCES.get('capital_letters','do nothing'));play_sound(text)
    if ESPEAK_SPEECH is not None and ESPEAK_SPEECH.enabled:
        ESPEAK_SPEECH.speak(punctuation_for_speech(text), SPEECH_RATE, SPEECH_VOLUME)
        return
    if AI_SPEECH is not None and AI_SPEECH.enabled:
        AI_SPEECH.speak(punctuation_for_speech(text), SPEECH_RATE, SPEECH_VOLUME)
        return
    if KEYBOARD_SPEECH and not KEYBOARD_SPEECH.problem:
        KEYBOARD_SPEECH.speak(punctuation_for_speech(text));return
    sapi_speak(voice,punctuation_for_speech(text),SPEECH_PITCH,flags=1)  # Async: microphone stays responsive.


def speak_keyboard_feedback(text):
    # Character echo must be immediate and must not wait for an API request.
    TRANSCRIPT.said(text)
    if APP_WINDOW is not None: APP_WINDOW.show(text)
    if not TEXT_MODE:
        update_audio_ducking(True)
        if KEYBOARD_SPEECH and not KEYBOARD_SPEECH.problem:
            KEYBOARD_SPEECH.speak(text);return
        from capital_feedback import prepare,play_sound
        text=prepare(text,PREFERENCES.get('capital_letters','do nothing'));play_sound(text)
        if ESPEAK_SPEECH is not None and ESPEAK_SPEECH.enabled:
            ESPEAK_SPEECH.speak(text, SPEECH_RATE, SPEECH_VOLUME)
        else: sapi_speak(voice,text,SPEECH_PITCH)


def speak_status(text):
    # Operation results must not be coalesced with routine list navigation.
    global KEYBOARD_BATCH,KEYBOARD_BATCH_STATUS
    previous=KEYBOARD_BATCH;KEYBOARD_BATCH=False
    if previous:KEYBOARD_BATCH_STATUS=True
    try:speak(text)
    finally:KEYBOARD_BATCH=previous

def mail_progress(text):
    speak_status(text)

def speak_web_result(text,previous_revision):
    """A loaded page is an operation result, not discardable caret feedback."""
    global KEYBOARD_NAVIGATION,KEYBOARD_BATCH_TEXT
    if web_session and getattr(web_session,'page_revision',0)!=previous_revision:
        sync_app_context('web',True)
        KEYBOARD_BATCH_TEXT=None
        previous=KEYBOARD_NAVIGATION;KEYBOARD_NAVIGATION=True
        try:speak_status(text)
        finally:KEYBOARD_NAVIGATION=previous
    else:web_dictation_feedback(text)

def web_dictation_feedback(result):
    text=None
    if web_session:
        for owner in (web_session,getattr(web_session,'backend',None)):
            value=getattr(owner,'last_dictated_text',None)
            if isinstance(value,str):
                text=value;owner.last_dictated_text=None
    if text is None:speak(result)
    elif text:dictation_feedback('Added: '+text)

CLOSE_BROWSER_COMMANDS=('close browser','close the browser','close browser window','close the browser window','close web browser',
                        'close web browser window','close the web browser','exit browser','exit web browser','quit browser')

def close_browser_window():
    """Close the browser even when Keep browser window open is on, then go to the main menu."""
    regular=getattr(getattr(web_session,'backend',None),'regular',False) is True
    try:web_session.leave(False)
    except Exception:
        speak('The browser window could not close. Try closing it again.');return 'web' if web_session.snapshot else 'browser_menu'
    speak('Companion let go of your browser. Its tabs stay open.' if regular else 'Browser window closed.')
    announce_main_menu()
    return 'awake'

def open_browser_menu(regular=False):
    global web_session
    if os.name=='nt':
        try:
            from browser_cdp_relay import prepare
            prepare(APP)
        except OSError:
            speak('The browser extension connection could not start. Close other copies of Companion and try again.')
    web_session=web_session or WebSession(APP,progress=speak_status)
    try:web_session.adopt_connected_browser()
    except Exception:
        speak('The regular browser tab could not be read. Reconnect it using the extension, then open this menu again.')
    notice=web_session.take_browser_notice()
    if not isinstance(notice,str):notice=''
    linked=getattr(web_session.backend,'regular',False) is True
    if not linked and getattr(web_session,'chrome_offer_declined',False) is not True:
        # Chrome chosen but not ready (or your own Chrome not linked): offer setup right here.
        import chrome_setup
        missing=chrome_setup.missing_for_web(sys.modules[__name__],web_session.browser,regular)
        if missing:
            if notice:speak(notice)
            web_session.menu.start()
            return chrome_setup.offer(sys.modules[__name__],'browser_menu',missing)
    menu=web_session.menu.start(resume=(PREFERENCES.get('web_keep_open',False) or getattr(web_session.backend,'regular',False)) and bool(web_session.snapshot))
    speak((notice+' ' if notice else '')+menu)
    return 'browser_menu'

def handle_browser_menu(text):
    global SEARCH_RETURN_MODE
    notice,action=web_session.menu.process(text,web_session,PREFERENCES.get('web_keep_open',False))
    if notice:speak(notice)
    if action=='main':return 'awake'
    if action=='downloads':return __import__('download_screen').open_manager(sys.modules[__name__],'browser_menu')
    if action=='chrome':
        import chrome_setup
        return chrome_setup.begin(sys.modules[__name__],'browser_menu')
    if action=='search':
        SEARCH_RETURN_MODE='browser_menu'
        speak('Search or enter address. Say or type your search or address, then press Enter. Escape returns to the web browser menu.');return 'search'
    if action in ('back','forward','resume') or isinstance(action,tuple):
        if action in ('back','forward') and not web_session.snapshot:
            speak('No browser page is open. Use Web history to open an earlier page.');return 'browser_menu'
        revision=web_session.page_revision
        try:result=web_session.open(action[1]) if isinstance(action,tuple) else web_session.resume() if action=='resume' else web_session.command('go '+action)
        except Exception:
            speak('The page could not open. Your favorites are still saved. Try the favorite again.');return 'browser_menu'
        if isinstance(action,tuple) and not getattr(web_session,'navigation_succeeded',False):
            speak(result)
            return 'browser_menu'
        if not result:speak('The page is no longer available.');return 'browser_menu'
        speak_web_result(result,revision)
        return 'web' if web_session.snapshot else 'browser_menu'
    return 'browser_menu'

def run_web_search(query):
    """Search-entry text is literal, even when it resembles an app command."""
    global web_session
    web_session=web_session or WebSession(APP,progress=speak_status)
    web_session.auto_read=PREFERENCES.get('web_auto_read',True)
    web_session.search_engine=PREFERENCES.get('web_search_engine','google')
    revision=getattr(web_session,'page_revision',0)
    from browser_navigation import looks_like_address
    speak_web_result(web_session.open(query) if looks_like_address(query) else web_session.search(query),revision)
    return 'web' if web_session.snapshot else 'search'

def refresh_browser_view(mode):
    logical=SLEEP_RETURN_MODE if mode=='sleep' else mode
    if logical!='web' or not web_session:return
    web_session.flush_scroll()
    revision=getattr(web_session,'page_revision',0)
    notice=web_session.refresh_changed()
    sync_app_context(mode)
    if notice:speak_web_result(notice,revision)

def browser_focus_notice(mode):
    """Native focus polling must not overwrite Companion browse-cursor speech."""
    if mode!='web' or not web_session or speech_busy():return ''
    if APP_WINDOW is not None and web_session.snapshot:return ''
    return web_session.focus_notice()


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


def save_note(text, say=None):
    """Save the note being written. Returns False, keeping the text, if it could not be saved."""
    global OPEN_NOTE
    say = say or speak
    opened = OPEN_NOTE
    try:
        if opened is not None and not text.strip():
            OPEN_NOTE = None
            say('The note is empty, so your saved note was not changed.')
            return True
        if opened is not None and notepad.replace(APP, opened, text):
            OPEN_NOTE = None
            say('Saved your note.')
            return True
        notepad.add(APP, text)
    except OSError:
        say('Your note could not be saved. Your words are still here. Close any program using your notes file, then say save note again.')
        return False
    OPEN_NOTE = None
    say('Your notes file changed, so this was saved as a new note.' if opened is not None else 'Saved your note.')
    return True


def flush_note(mode, say=None):
    """Save a note in progress. Returns False if it could not be saved."""
    if mode == 'note' and note_buffer:
        if (save_note(' '.join(note_buffer), say) if say else save_note(' '.join(note_buffer))) is False:
            return False
        note_buffer.clear()
        note_selection.text = None
    return True


NOTE_NUMBER = re.compile(r'open (?:the |my )?(?:saved )?note (?:number )?(\w+(?: \w+)?)')


def saved_notes():
    try:
        return notepad.newest_first(notepad.read_notes(APP)), None
    except OSError:
        return [], 'Your notes could not be read. Close any program using your notes file, then try again.'


def list_saved_notes():
    notes, problem = saved_notes()
    if problem:
        speak(problem)
    elif not notes:
        speak('You have no saved notes yet. Say Write a note to start one.')
    else:
        speak(open_named_list('Notes', [(notepad.label(n), 'open note ' + str(i + 1)) for i, n in enumerate(notes)]))


def open_saved_note(spoken, mode):
    """Open a note by its place in the notes list, newest first."""
    global OPEN_NOTE
    from document_editor import spoken_number
    notes, problem = saved_notes()
    if problem:
        speak(problem);return mode
    if not notes:
        speak('You have no saved notes yet. Say Write a note to start one.');return mode
    # Recognizers often hear "open note two" as "to" or "too", and "one" as "won".
    value = spoken_number({'to': '2', 'too': '2', 'won': '1', 'for': '4'}.get(spoken, spoken))
    number = int(value) if value and value.isdigit() else None
    if number is None or not 1 <= number <= len(notes):
        speak(('There is 1 note.' if len(notes) == 1 else 'There are ' + str(len(notes)) + ' notes.')
              + ' Say open note and a number from 1 to ' + str(len(notes)) + ', or say list notes.')
        return mode
    note = notes[number - 1]
    OPEN_NOTE = note
    note_buffer[:] = [note.text] if note.text else []
    note_selection.text = None
    speak('Opened the note from ' + notepad.spoken_date(note.stamp) + '. ' + (note.text.strip() or 'It is empty.'))
    speak_prompt('Speak to add to it. Say save note when finished.')
    return 'note'


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


UNSAVED_DOCUMENT = ('Your change is in the document, but the file could not be saved. It may be open in another program, '
                    'such as Word. Close it there, then say save document.')
UNSAVED_DRAFT = ('Your change is in the draft, but the draft could not be saved. Check that the disk has space, '
                 'then say save draft.')


def finish_exit(mode):
    global help_session, account_setup, pending_send, mail_session, KEYBOARD_BATCH_TEXT
    KEYBOARD_BATCH_TEXT=None
    # Never lose work: if the draft or the note cannot be saved, stay open and say so.
    if email_draft is not None:
        try:email_draft.save()
        except OSError:
            speak('Your email draft could not be saved, so Voice Companion is still open. Close any program using your drafts folder, then try again.')
            return mode
    if flush_note(mode) is False:return mode   # save_note said why; the words are kept
    help_session=None;account_setup=False;pending_send=None;mail_session=None
    if MEDIA_HUB is not None:
        try:MEDIA_HUB.player.close()
        except Exception:pass
    if web_session:
        try:web_session.leave(False)
        except Exception:pass
    try:interrupt_speech()
    except Exception:pass
    speak_status('Shutting down. Goodbye.')
    wait_for_speech(30000)
    return 'exit'


def close_from_window(mode):
    """The window was closed some other way than Exit. Save what can be saved, say so, and
    never stop with an error: the app is closing either way."""
    saved=True
    try:flush_keyboard_edits(True)
    except OSError:saved=False
    if email_draft is not None:
        try:email_draft.save()
        except OSError:saved=False
    if flush_note(SLEEP_RETURN_MODE if mode=='sleep' else mode) is False:saved=False
    speak('Voice Companion closed. Goodbye.' if saved else 'Voice Companion closed. Some of your work could not be saved.')


def thunderbird_connect_ready(setup):
    """Thunderbird is connected: connect its new addresses, or offer to add another account to it."""
    import account_picker
    import thunderbird_accounts as words
    from thunderbird_setup import Step
    from thunderbird_mailbox import accounts
    store = ProtectedStore(APP)
    try:
        found = accounts()
        pairs = words.identities(found)
        if not pairs:
            return Step('Thunderbird is connected, but it has no email accounts yet. Add one now? Say yes or no.', 'add_another')
        added = words.save_identities(store, found, select=True)
    except (MailboxError, AccountError) as exc:
        return Step(str(exc), None)
    except OSError:
        return Step('Voice Companion could not save the Thunderbird accounts. Close other programs, then say add account and choose Thunderbird.', None)
    if not added:
        names = '; '.join(account_picker.label(('thunderbird', identity['email'])) for _, identity in pairs)
        return Step(('Your Thunderbird account is already connected: ' if len(pairs) == 1 else
                     'Your Thunderbird accounts are already connected: ') + names +
                    '. Add another email account to Thunderbird? Say yes or no.', 'add_another')
    selected = store.selected()
    if len(added) == 1:
        message = account_picker.label(selected) + ' is connected and selected.'
    else:
        message = (str(len(added)) + ' Thunderbird accounts are connected: ' + ', '.join(added) + '. ' +
                   account_picker.label(selected) + ' is selected.')
    return Step(message + ' Say check email to open it. To add another email account to Thunderbird, say add Thunderbird account.', None)


def thunderbird_add_ready(setup):
    """Thunderbird is connected for "add Thunderbird account": ask for the address."""
    global THUNDERBIRD_VERSION
    import thunderbird_accounts as words
    from thunderbird_setup import Step
    from thunderbird_mailbox import accounts
    if setup.addon_outdated():
        return Step(words.NEEDS_UPDATE, None)
    THUNDERBIRD_VERSION = setup.version
    note = ''
    try:
        # Addresses added in Thunderbird earlier are connected now, and said, so the list is complete.
        added = words.save_identities(ProtectedStore(APP), accounts(), select=False)
        if added:
            note = ('Thunderbird already had ' + ' and '.join(added) + '. ' +
                    ('It is' if len(added) == 1 else 'They are') + ' now connected too. ')
    except (MailboxError, AccountError) as exc:
        return Step(str(exc), None)
    except OSError:
        pass
    return Step(note + words.ADDRESS_PROMPT, 'address')


THUNDERBIRD_VERSION = None
# The sign-in page Thunderbird handed over, so it can be opened again or in the other browser.
THUNDERBIRD_SIGN_IN = None


def addon_package():
    """The add-on package this copy of the app ships (built from source when running from source)."""
    package = Path(sys.executable).parent / 'Voice Companion Mail Connection.xpi'
    if not getattr(sys, 'frozen', False) or not package.is_file():
        from build_thunderbird_addon import build
        package = build(APP / 'thunderbird-addon' / 'Voice Companion Mail Connection.xpi')
    return package


def mail_relay():
    from thunderbird_bridge import current, prepare
    return current() or (prepare(APP) if os.name == 'nt' else None)


def thunderbird_starter(system=None):
    """thunderbird_mailbox calls this when a Thunderbird account is used and the add-on is not connected.

    Starts Thunderbird in the background and waits, saying a short line only
    when that takes more than two seconds; the user's command then goes on by
    itself. If a yes is needed (install Thunderbird or its connection), the
    setup question is asked and MailboxError carries it to the screen."""
    global THUNDERBIRD_SETUP, CONFIRM_CHOICE
    import thunderbird_setup
    from thunderbird_mailbox import NOT_CONNECTED
    relay = mail_relay()
    try:
        launched = thunderbird_setup.start_in_background(system or thunderbird_setup.WindowsSystem(), addon_package(),
                                                         relay, speak_status)
    except user_stop.Stopped as exc:
        from thunderbird_mailbox import STOPPED
        raise MailboxError(STOPPED) from exc
    except thunderbird_setup.NotReady:
        flow = new_thunderbird_setup()
        step = flow.start()
        if step.question:
            THUNDERBIRD_SETUP = {'flow': flow, 'question': step.question, 'message': step.message}
            CONFIRM_CHOICE = 'yes'
        raise MailboxError(step.message or NOT_CONNECTED)
    except OSError as exc:
        raise MailboxError(NOT_CONNECTED) from exc
    if relay is None or not relay.connected.is_set():
        raise MailboxError(thunderbird_setup.AUTO_NO_CONNECTION)
    if launched:
        quiet_thunderbird(relay)


THUNDERBIRD_STARTED = False   # Voice Companion started Thunderbird, so it closes it on exit

# F9 (or "turn off the microphone") turns the microphone fully off: the stream is closed and
# nothing listens, not even for "wake up". F9 turns it back on. (Control+F12 only puts
# listening to sleep.) Kept between starts (preferences "microphone_off"): Companion says at
# start that the microphone is off and how to turn it on.
MICROPHONE_OFF = False
MICROPHONE_ANNOUNCED = False   # set_microphone already said it is on
MICROPHONE_STILL_OFF = 'The microphone is off, as you left it. Press F9 to turn it on.'
# The microphone could not open (none plugged in, or blocked in Windows privacy settings).
# The main loop keeps trying; voice works by itself once it opens.
MICROPHONE_FAILED = False


from speech_transcript import TRANSCRIPT   # speech-transcript.txt, opt-in (Options or "start transcript")
TRANSCRIPT_START_PHRASES = ('start transcript', 'start the transcript', 'start speech transcript', 'turn on transcript',
                            'turn on the transcript', 'transcript on')
TRANSCRIPT_STOP_PHRASES = ('stop transcript', 'stop the transcript', 'stop speech transcript', 'turn off transcript',
                           'turn off the transcript', 'transcript off')


def apply_transcript():
    """Start or stop the transcript as the preference says."""
    if PREFERENCES.get('speech_transcript', False):TRANSCRIPT.start(APP)
    else:TRANSCRIPT.stop()


def set_transcript(on, mode):
    PREFERENCES['speech_transcript'] = bool(on)
    try:save_preferences()
    except OSError:pass   # it still works now; it is only not remembered
    apply_transcript()
    speak('Transcript on. I will write everything I say, and every key you press, to a file named speech-transcript in the Voice Companion data folder. Say stop transcript to turn it off.'
          if on else 'Transcript off.')
    return mode


def microphone_state():
    """'off' (turned off with F9), 'failed' (could not open) or 'on'."""
    return 'off' if MICROPHONE_OFF else 'failed' if MICROPHONE_FAILED else 'on'


# One-time startup notices wait here and are said in the startup announcement
# (startup_speech.py). None once startup has been announced: then they are said at once.
STARTUP_NOTICES = None
STARTUP_QUESTION = None


def startup_notice(text):
    if STARTUP_NOTICES is not None:STARTUP_NOTICES.append(text)
    else:speak(text)
MICROPHONE_OFF_PHRASES = ('turn off the microphone', 'turn off microphone', 'turn the microphone off', 'turn microphone off',
                          'microphone off', 'mic off', 'turn off the mic', 'turn the mic off', 'mute microphone', 'mute the microphone')
MICROPHONE_ON_PHRASES = ('turn on the microphone', 'turn on microphone', 'turn the microphone on', 'turn microphone on',
                         'microphone on', 'mic on', 'turn on the mic', 'turn the mic on', 'unmute microphone', 'unmute the microphone')


def microphone_retried(opened, mode):
    """The main loop tried to open the microphone again. Say when voice stops or comes back,
    once each: after F9, when the microphone was unplugged, or when Windows lets it go."""
    global MICROPHONE_FAILED, MICROPHONE_ANNOUNCED
    if opened:
        # F9 already said Microphone on; say it here only when it came back by itself.
        if mode!='sleep' and not MICROPHONE_ANNOUNCED:speak('The microphone is on.')
        MICROPHONE_FAILED=MICROPHONE_ANNOUNCED=False
    elif not MICROPHONE_FAILED:
        MICROPHONE_FAILED=True;MICROPHONE_ANNOUNCED=False
        speak('The microphone could not start, so voice commands are off for now. Use the keys. Voice commands start again by themselves when the microphone works.')


def set_microphone(on, mode):
    """Turn the microphone fully on or off, and say what happened and how to undo it."""
    global MICROPHONE_OFF, MICROPHONE_ANNOUNCED
    MICROPHONE_OFF = not on
    MICROPHONE_ANNOUNCED = on
    PREFERENCES['microphone_off'] = not on
    try:save_preferences()
    except OSError:pass   # The switch still works now; it is only not remembered.
    if on:
        speak('Microphone on. ' + ('Say wake up, or press Control+F12, to give voice commands.' if mode == 'sleep'
                                   else 'Voice commands are on.'))
    else:
        speak('Microphone off. Voice Companion is not listening at all. Press F9 to turn it back on.')
    return mode


FREEZE_WATCH = {'beat': None, 'reported': False}


def start_freeze_watch(limit=15.0, interval=5.0):
    """If the main loop stops for `limit` seconds, write where every thread is to
    freeze-report.txt in the app folder, once per freeze. faulthandler reads the
    stacks without making Python objects of other threads' frames, so it cannot
    free the window's Tk objects on the wrong thread."""
    def watch():
        while True:
            time.sleep(interval)
            beat = FREEZE_WATCH['beat']
            if beat is None:
                continue
            stalled = time.monotonic() - beat > limit
            if stalled and not FREEZE_WATCH['reported']:
                FREEZE_WATCH['reported'] = True
                try:
                    import faulthandler
                    APP.mkdir(parents=True, exist_ok=True)
                    with open(APP / 'freeze-report.txt', 'w', encoding='utf-8') as report:
                        report.write('Voice Companion ' + APP_VERSION + ' stopped responding for ' + str(int(time.monotonic() - beat))
                                     + ' seconds at ' + time.strftime('%Y-%m-%d %H:%M:%S') + '.\n')
                        report.flush()
                        faulthandler.dump_traceback(file=report, all_threads=True)
                except Exception:
                    pass
            elif not stalled:
                FREEZE_WATCH['reported'] = False
    thread = threading.Thread(target=watch, name='Freeze watch', daemon=True)
    thread.start()
    return thread


def remember_thunderbird_identity(account, identity_id, account_id):
    """Thunderbird sent from another identity for this address: keep that one for next time."""
    saved = {key: value for key, value in account.items() if key in ('address', 'account_id', 'identity_id', 'name')}
    saved.update(identity_id=identity_id, account_id=account_id)
    ProtectedStore(APP).save('thunderbird', saved, select=False)


def close_started_thunderbird(relay_source=None):
    """On exit: close Thunderbird if Voice Companion started it (never one the user opened).
    Thunderbird stays open while a message is being written."""
    if not THUNDERBIRD_STARTED:
        return None
    relay = (relay_source or mail_relay)()
    if relay is None or not relay.connected.is_set():
        return None
    user_stop.begin_command()   # Alt+F4 asked to stop waits; this short request must still run
    try:
        return relay.request('quit', {}, timeout=5)
    except Exception:
        return None


def quiet_thunderbird(relay):
    """Thunderbird was started for mail: keep its window out of the way and the keyboard in Voice Companion."""
    global THUNDERBIRD_STARTED
    if not THUNDERBIRD_STARTED:
        THUNDERBIRD_STARTED = True
        import atexit
        atexit.register(close_started_thunderbird)
    try:
        relay.request('minimize', {}, timeout=10)
    except Exception:
        pass
    if APP_WINDOW is not None and hasattr(APP_WINDOW, 'reclaim_focus'):
        APP_WINDOW.reclaim_focus()


def start_thunderbird_quietly():
    """At startup, when the selected account is a Thunderbird account: start Thunderbird so mail is ready. Silent."""
    try:
        selected = ProtectedStore(APP).selected()
    except Exception:
        return None
    if not selected or selected[0] != 'thunderbird':
        return None

    def run():
        import thunderbird_setup
        relay = mail_relay()
        try:
            if thunderbird_setup.start_in_background(thunderbird_setup.WindowsSystem(), addon_package(), relay,
                                                     lambda text: None):
                quiet_thunderbird(relay)
        except Exception:
            pass    # said only if mail is used and still cannot connect
    thread = threading.Thread(target=run, name='Start Thunderbird', daemon=True)
    thread.start()
    return thread


def start_thunderbird_add(mode):
    """ "Add Thunderbird account": get Thunderbird ready, then ask for the address."""
    global THUNDERBIRD_SETUP
    try:
        flow = new_thunderbird_setup(adding=True)
        THUNDERBIRD_SETUP = {'flow': flow, 'question': None, 'message': ''}
        return thunderbird_setup_step(flow.start(), mode)
    except Exception:
        THUNDERBIRD_SETUP = None
        speak('Thunderbird setup stopped because of a problem. Nothing was changed. Say add Thunderbird account to try again.')
        return mode


def ask_thunderbird_entry(step, message, mode, **details):
    """Open the speaking text box for the address, name or password to add to Thunderbird."""
    global ACCOUNT_ENTRY
    ACCOUNT_ENTRY = {'provider': 'thunderbird', 'step': step, 'version': THUNDERBIRD_VERSION, **details}
    speak(message)
    sync_app_context(mode, True)
    return mode


def ask_thunderbird_address(message, mode):
    return ask_thunderbird_entry('address', message, mode)


def thunderbird_address_step(text, command, mode):
    """The address or name typed or said for "add Thunderbird account". Passwords come by finish_account_secret."""
    global ACCOUNT_ENTRY
    import thunderbird_accounts as words
    entry = ACCOUNT_ENTRY
    if command in ('cancel', 'cancel account setup', 'cancel setup', 'stop', 'go back', 'escape', 'never mind', 'main menu'):
        ACCOUNT_ENTRY = None
        speak('Okay. No password was sent. Say what does Thunderbird say to hear the page.' if entry['step'] == 'password'
              else words.CANCELED)
        sync_app_context(mode, True)
        return mode
    prompt = {'address': words.ADDRESS_PROMPT, 'name': words.NAME_PROMPT, 'password': words.PASSWORD_PROMPT}[entry['step']]
    if command == 'account entry empty':
        speak('The box is empty. ' + prompt)
        return mode
    if command == 'account entry busy':
        speak(words.BUSY if entry['step'] == 'address' else prompt)
        return mode
    if command in ('help', 'what do i do', 'repeat', 'what now', 'say that again'):
        speak(prompt)
        return mode
    if entry['step'] == 'password':
        # Never repeat what was said: it may have been the password.
        speak('For privacy, type or paste the password instead of saying it. Then press Enter. Say cancel to skip.')
        return mode
    if entry['step'] == 'name':
        name = re.sub(r'\s+', ' ', text).strip().rstrip('.')
        if not name or len(name) > 100:
            speak(words.NAME_PROMPT)
            return mode
        ACCOUNT_ENTRY = None
        sync_app_context(mode, True)
        return open_thunderbird_setup(entry['address'], name, entry.get('version'), mode)
    try:
        address = normalize_recipient(text.strip().rstrip('.'))
    except ValueError:
        speak(words.ADDRESS_AGAIN)
        return mode
    ACCOUNT_ENTRY = None
    sync_app_context(mode, True)
    return add_thunderbird_address(address, entry.get('version'), mode)


def add_thunderbird_address(address, version, mode):
    """Connect the address if Thunderbird has it; otherwise ask the name if needed and open Thunderbird's page."""
    global mail_session, pending_send
    import account_picker
    import thunderbird_accounts as words
    from thunderbird_mailbox import accounts
    store = ProtectedStore(APP)
    try:
        found = accounts()
    except MailboxError as exc:
        speak(str(exc))
        return mode
    pairs = words.identities(found)
    has = [identity['email'] for _, identity in pairs if identity['email'].casefold() == address.casefold()]
    if has:
        account = ('thunderbird', has[0])
        if any(p == 'thunderbird' and a.casefold() == address.casefold() for p, a in connected_accounts(store)):
            speak(account_picker.label(account) + ' is already connected. Say switch account to use it.')
            return mode
        try:
            words.save_identities(store, found, select=False)
            store.select(*account)
        except (AccountError, OSError):
            speak('Voice Companion could not save this account. Close other programs, then say add Thunderbird account again.')
            return mode
        if mode not in ('mailbox', 'email_draft'):
            mail_session = None; pending_send = None
        speak('Thunderbird already has ' + has[0] + '. ' + account_picker.label(account) +
              ' is now connected and selected. Say check email to open it.')
        return mode
    # The name people see: the one already used in Thunderbird, or asked once.
    name = next((identity.get('name') for _, identity in pairs if identity.get('name')), '')
    if not name:
        return ask_thunderbird_entry('name', 'Adding ' + address + '. ' + words.NAME_PROMPT, mode, address=address)
    return open_thunderbird_setup(address, name, version, mode, found)


def open_thunderbird_setup(address, name, version, mode, found=None):
    """Open Thunderbird's own add account page in the background, filled in, and read it."""
    import thunderbird_accounts as words
    from thunderbird_bridge import ThunderbirdError, ThunderbirdUnavailable
    from thunderbird_mailbox import accounts, relay
    try:
        bridge = relay()
        found = found if found is not None else accounts()
        # Google and Microsoft sign-in pages come to Voice Companion's browser while this account is added.
        bridge.request('captureSignIn', {'seconds': words.WAIT_SECONDS}, timeout=30)
        result = bridge.request('openAccountSetup', {'email': address, 'name': name, 'show': False}, timeout=30) or {}
    except (MailboxError, ThunderbirdUnavailable):
        speak('Thunderbird stopped answering. Nothing was added. Say add Thunderbird account to try again.')
        return mode
    except ThunderbirdError:
        result = {}    # an older connection: the spoken steps still work
    prefilled = bool(result.get('prefilled'))
    copied = False
    if not prefilled:
        try:
            import document_editor
            document_editor.copy_to_clipboard(address)
            copied = bool(getattr(document_editor, 'CLIPBOARD_WRITE_OK', False))
        except Exception:
            copied = False
    start_thunderbird_watch(address, found)
    message = words.opened_message(address, bool(result.get('opened')), prefilled, copied, version)
    if prefilled:
        page = thunderbird_page()
        message += ' ' + words.page_words(page)
        speak(message)
        if page.get('needsPassword'):
            return ask_thunderbird_entry('password', words.PASSWORD_PROMPT, mode)
        return mode
    speak(message)
    return mode


def thunderbird_page():
    from thunderbird_bridge import current
    try:
        return current().request('setupPage', {}, timeout=15) or {}
    except Exception:
        return {}


def thunderbird_setup_command(command, mode):
    """Continue, read, show, password and sign-in commands while a Thunderbird account is being added. None: not one."""
    global ACCOUNT_ENTRY
    import thunderbird_accounts as words
    waiting = THUNDERBIRD_WATCH
    sign_in = re.fullmatch(r'open (?:the )?(?:thunderbird )?sign[ -]?in(?: page)?(?: again)?'
                           r'(?: in (chrome|embedded edge|edge webview2|webview2|webview 2))?', command)
    if sign_in and not (THUNDERBIRD_SIGN_IN or waiting):
        return None    # nothing from Thunderbird: the words mean something else here
    if not (command in words.CONTINUE_COMMANDS + words.READ_COMMANDS + words.SHOW_COMMANDS + words.PASSWORD_COMMANDS or sign_in):
        return None
    if sign_in:
        if not THUNDERBIRD_SIGN_IN:
            speak(words.NO_SIGN_IN)
            return mode
        return open_thunderbird_sign_in(mode, sign_in[1])
    from thunderbird_bridge import current
    if command in words.SHOW_COMMANDS:
        try:
            from thunderbird_mailbox import relay
            shown = (relay().request('showSetup', {}, timeout=15) or {}).get('shown')
        except Exception as exc:
            speak(str(exc) if isinstance(exc, MailboxError) else 'Thunderbird did not answer. Try again in a moment.')
            return mode
        speak('Thunderbird is in front now. Press Alt plus Tab to come back to Voice Companion.' if shown else
              'Thunderbird could not come to the front. Press Alt plus Tab to find it.')
        return mode
    if waiting is None:
        speak(words.NOT_ADDING)
        return mode
    try:
        if command in words.PASSWORD_COMMANDS:
            return ask_thunderbird_entry('password', words.PASSWORD_PROMPT, mode)
        before = current().request('setupPage', {}, timeout=15) or {}
        if command in words.READ_COMMANDS:
            speak(words.page_words(before))
            if before.get('needsPassword'):
                return ask_thunderbird_entry('password', words.PASSWORD_PROMPT, mode)
            return mode
        if not before.get('canContinue'):
            speak(words.page_words(before))
            return mode
        if (current().request('setupContinue', {}, timeout=15) or {}).get('pressed'):
            waiting['watch'].after_press(before)
            speak('Choosing ' + (before.get('button') or 'Continue') + ' in Thunderbird. Voice Companion reads the page when it changes.')
        else:
            speak(words.page_words(before))
    except Exception:
        speak('Thunderbird did not answer. Say what does Thunderbird say to try again.')
    return mode


def send_thunderbird_password(value, mode):
    """The password typed privately for Thunderbird's page: put in once, never spoken, kept or logged."""
    global ACCOUNT_ENTRY
    import thunderbird_accounts as words
    from thunderbird_bridge import current
    ACCOUNT_ENTRY = None
    sync_app_context(mode, True)
    if not value:
        speak('The box was empty. No password was sent. Say enter Thunderbird password to try again.')
        return mode
    try:
        bridge = current()
        filled = (bridge.request('fillPassword', {'password': value}, timeout=15) or {}).get('filled')
        if not filled:
            speak(words.PASSWORD_NOT_SENT)
            return mode
        before = bridge.request('setupPage', {}, timeout=15) or {}
        if before.get('canContinue') and (bridge.request('setupContinue', {}, timeout=15) or {}).get('pressed'):
            if THUNDERBIRD_WATCH is not None:
                THUNDERBIRD_WATCH['watch'].after_press(before)
            speak(words.PASSWORD_SENT)
        else:
            speak('The password is in Thunderbird. ' + words.page_words(before))
    except Exception:
        speak('Thunderbird did not answer, so no password was sent. Say enter Thunderbird password to try again.')
    return mode


def open_thunderbird_sign_in(mode, browser=None, announce=True):
    """Open the sign-in page Thunderbird handed over in Voice Companion's browser (embedded Edge or Chrome)."""
    global web_session
    import thunderbird_accounts as words
    address = THUNDERBIRD_WATCH['address'] if THUNDERBIRD_WATCH else ''
    try:
        web_session = web_session or WebSession(APP, progress=speak_status)
        web_session.auto_read = PREFERENCES.get('web_auto_read', True)
        web_session.search_engine = PREFERENCES.get('web_search_engine', 'google')
        if browser:
            web_session.choose_browser('webview2' if 'edge' in browser or 'webview' in browser else 'chrome')
        if announce:speak_status(words.sign_in_opening(address))
        revision = getattr(web_session, 'page_revision', 0)
        result = web_session.open(THUNDERBIRD_SIGN_IN)
        speak_web_result(result, revision)
        return 'web' if web_session.snapshot else mode
    except Exception:
        speak_status('The sign-in page could not open in Voice Companion\'s browser. '
                     'Say open sign-in page in Chrome, or open sign-in page in embedded Edge, to try the other browser.')
        return mode


def start_thunderbird_watch(address, found):
    global THUNDERBIRD_WATCH, THUNDERBIRD_SIGN_IN
    import thunderbird_accounts as words
    from thunderbird_bridge import current
    stop_thunderbird_watch()
    THUNDERBIRD_SIGN_IN = None
    THUNDERBIRD_WATCH = {'watch': words.Watch(current().request, address, found).start(), 'address': address}


def stop_thunderbird_watch():
    """Stop waiting, and give Thunderbird's web sign-in back to the Windows browser."""
    global THUNDERBIRD_WATCH
    waiting, THUNDERBIRD_WATCH = THUNDERBIRD_WATCH, None
    if waiting is None:
        return
    waiting['watch'].stop()
    try:
        waiting['watch'].request('captureSignIn', {'seconds': 0})
    except Exception:
        pass


def poll_thunderbird_watch(mode):
    """On the main loop: read Thunderbird's page, open its sign-in page, and say when the account is ready."""
    global THUNDERBIRD_WATCH, THUNDERBIRD_SIGN_IN, mail_session, pending_send
    waiting = THUNDERBIRD_WATCH
    if waiting is None:
        return mode
    try:
        event, found = waiting['watch'].events.get_nowait()
    except queue.Empty:
        return mode
    import thunderbird_accounts as words
    address = waiting['address']
    if event == 'reminder':
        speak_status(words.reminder_message(address))
        return mode
    if event == 'page':
        speak_status(words.page_words(found))
        if found.get('needsPassword') and ACCOUNT_ENTRY is None:
            return ask_thunderbird_entry('password', words.PASSWORD_PROMPT, mode)
        return mode
    if event == 'sign_in':
        if found.get('where') == 'thunderbird':
            speak_status(words.sign_in_in_thunderbird(address))
            return mode
        THUNDERBIRD_SIGN_IN = found['url']
        if ACCOUNT_ENTRY is not None or writing_dictation(mode):
            # Never pull someone out of writing or a private box: say it, and let them open it.
            speak_status(words.SIGN_IN_READY)
            return mode
        return open_thunderbird_sign_in(mode)
    stop_thunderbird_watch()
    THUNDERBIRD_SIGN_IN = None
    if event != 'ready':
        speak_status(words.gave_up_message(address))
        return mode
    known = waiting['watch'].known
    new = [identity['email'] for _, identity in words.identities(found) if identity.get('id') not in known]
    store = ProtectedStore(APP)
    # An open mailbox or draft keeps its account; elsewhere the new account is selected.
    choose = mode not in ('mailbox', 'email_draft')
    chosen = next((a for a in new if a.casefold() == address.casefold()), new[0]) if choose else None
    try:
        words.save_identities(store, found, select=False)
        if chosen:
            store.select('thunderbird', chosen)
            mail_session = None; pending_send = None
    except (AccountError, OSError):
        speak_status('Thunderbird added ' + ' and '.join(new) + ', but Voice Companion could not save it. '
                     'Say add account and choose Thunderbird to try again.')
        return mode
    speak_status(words.ready_message(new, chosen) + ' To add another, say add Thunderbird account.')
    return mode


def handle_mailbox_screen(text, command, mode, typed):
    """The email inbox screen: folders, reading, reply and forward, leaving."""
    global email_draft, mail_session, FOLDER_ACCOUNT_RETURN
    if mail_session is None:
        # The mailbox closed underneath this command (for example, the sending
        # account changed). Return to the main menu rather than failing.
        return 'awake'
    if getattr(mail_session, 'folder_picker', None) or getattr(mail_session, 'folder_choice', None):
        if FOLDER_ACCOUNT_RETURN is not None and getattr(mail_session, 'folder_picker', None) and command in ('cancel', 'go back', 'back', 'escape'):
            # This folder list came from the account list: go back one level.
            back, FOLDER_ACCOUNT_RETURN = FOLDER_ACCOUNT_RETURN, None
            mail_session.folder_picker = None
            mail_session.folder_choice = None
            return open_account_picker('folders', mode, origin=back)
        speak(mail_session.process(text))
        if not getattr(mail_session, 'folder_picker', None):
            FOLDER_ACCOUNT_RETURN = None
        return 'mailbox'
    FOLDER_ACCOUNT_RETURN = None
    if command in FOLDER_LIST_COMMANDS and len(connected_accounts(ProtectedStore(APP))) > 1:
        # Several accounts: choose the account first, then its folder.
        return open_account_picker('folders', mode)
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
            email_draft.compose_step = 'recipient' if context['action'] == 'forward' or not context['recipient'] else 'body'
            email_draft.save()
            recipient_note = ('Say email to followed by the forwarding address. '
                              if context['action'] == 'forward' else
                              # No address to reply to (none in the message): ask for one, never "Recipient ."
                              'This message has no address to reply to. Say email to followed by an address. '
                              if not email_draft.recipient else
                              'Recipient ' + email_draft.recipient +
                              ('. Also copying ' + ', '.join(email_draft.cc) if email_draft.cc else '') + '. ')
            attachment_note = forward_attachment_note(context)
            speak('New ' + command + ' draft. ' + recipient_note +
                  'Speak your message naturally. Say send it to hear the review, then confirm before it sends.' + attachment_note)
            focus_email_entry()
            return 'email_draft'
        except (MailboxError, AccountError) as exc:
            # Say what went wrong (for example an expired app password), not a guess.
            speak(str(exc))
            return mode
        except Exception:
            speak('Open and read a message first. If the account cannot read it, ask your helper to reconnect mail.')
            return mode
    if command in ('go back', 'back') and mail_session.view == 'message':
        speak(mail_session.process('back to folder'))
        return mode
    if command.startswith('go to website '):
        # Leave the inbox for the website, as from the main menu.
        mail_session = None
        return _handle(text, 'awake', typed)
    if command in ('go back', 'back', 'exit', 'leave email', 'main menu', 'close inbox'):
        mail_session = None
        pass
        return 'awake'
    speak(mail_session.process(text))
    return 'mailbox'


def forward_attachment_note(context):
    """What a new forward says about the original's files."""
    if context.get('action') != 'forward':
        return ''
    found = context.get('original_attachments')
    if not isinstance(found, list):
        return ' Voice Companion could not check the original attachments, so they may not be included.'
    if not found:
        return ''
    from mail_attachments import count_words, names
    return (' The original ' + count_words(len(found)) + ' go with it: ' + names(found) + '.' if len(found) > 1 else
            ' The original attachment goes with it: ' + names(found) + '.')


EMAIL_LINK_RECIPIENT = None


def open_email_link(url):
    """A link or button chosen in an email opens in the guided browser from Web browser
    options (Chrome or the built-in browser), never another program. An email address
    link starts a new email to it."""
    global web_session, EMAIL_LINK_RECIPIENT
    from urllib.parse import unquote, urlsplit
    if urlsplit(url).scheme.lower() == 'mailto':
        EMAIL_LINK_RECIPIENT = unquote(urlsplit(url).path).split('?')[0].strip()
        return handle('new email', 'mailbox', typed=True)
    web_session = web_session or WebSession(APP, progress=speak_status)
    web_session.auto_read = PREFERENCES.get('web_auto_read', True)
    web_session.search_engine = PREFERENCES.get('web_search_engine', 'google')
    revision = getattr(web_session, 'page_revision', 0)
    speak_web_result(web_session.open(url), revision)
    return 'web' if web_session.snapshot else 'mailbox'


def account_entry_step(text, command, mode):
    """One spoken or typed answer during Yahoo setup. The password is never taken by voice."""
    global ACCOUNT_ENTRY
    if ACCOUNT_ENTRY.get('provider') == 'thunderbird':
        return thunderbird_address_step(text, command, mode)
    if command in ('cancel', 'cancel account setup', 'cancel setup', 'stop', 'go back', 'escape', 'never mind', 'main menu'):
        ACCOUNT_ENTRY = None
        speak('Yahoo setup canceled. No account was saved.')
        sync_app_context(mode, True)
        return mode
    if command == 'account entry empty':
        speak(('The box is empty. Type or paste the app password, then press Enter.' if ACCOUNT_ENTRY['step'] == 'password'
               else 'The box is empty. Type your Yahoo email address, then press Enter.') + ' Press Escape to cancel.')
        return mode
    if command == 'account entry busy':
        speak('Yahoo setup is open. Finish it, or press Escape to cancel.')
        return mode
    if command in ('help', 'what do i do', 'repeat', 'what now', 'say that again'):
        speak(YAHOO_SETUP_PROMPT if ACCOUNT_ENTRY['step'] == 'address' else YAHOO_PASSWORD_PROMPT)
        return mode
    if ACCOUNT_ENTRY['step'] == 'password':
        if command in ('change address', 'change the address', 'wrong address', 'fix address', 'fix the address'):
            ACCOUNT_ENTRY = {'provider': 'yahoo', 'step': 'address'}
            speak('Type or say your Yahoo email address, then press Enter.')
            sync_app_context(mode, True)
            return mode
        # Never repeat what was said: it may have been the password.
        speak('For privacy, type or paste the app password instead of saying it. Then press Enter. Say cancel to stop.')
        return mode
    from email_draft import normalize_recipient
    try:
        address = normalize_recipient(text.strip().rstrip('.'))
    except ValueError:
        speak('That is not a complete email address. Type or say it again, such as sam at yahoo dot com. Say cancel to stop.')
        return mode
    ACCOUNT_ENTRY = {'provider': 'yahoo', 'step': 'password', 'address': address}
    speak('Address ' + address + '. ' + YAHOO_PASSWORD_PROMPT)
    sync_app_context(mode, True)
    return mode


def finish_account_secret(value, mode):
    """Typed or pasted app password from the private text box."""
    global ACCOUNT_ENTRY
    entry = ACCOUNT_ENTRY
    if entry and entry.get('provider') == 'thunderbird' and entry.get('step') == 'password':
        return send_thunderbird_password(value, mode)
    if not entry or entry.get('step') != 'password':
        speak('Nothing is waiting for a password. Say add account to connect email.')
        return mode
    checking = 'Checking the app password with Yahoo. This can take up to 30 seconds.'
    if KEYBOARD_BATCH and APP_WINDOW is not None:
        APP_WINDOW.feedback(checking)  # keyboard actions otherwise speak only their final result
    else:
        speak(checking)
    try:
        address = yahoo_connect(ProtectedStore(APP), entry['address'], value)
    except AccountError as exc:
        speak(str(exc) + ' Type or paste the app password again, say change address, or say cancel.')
        sync_app_context(mode, True)
        return mode
    except Exception:
        speak('Yahoo setup stopped because of a problem. No account was saved. Say add account to try again.')
        ACCOUNT_ENTRY = None
        sync_app_context(mode, True)
        return mode
    ACCOUNT_ENTRY = None
    speak('Yahoo account ' + address + ' is connected and selected. Say check email to open it.')
    sync_app_context(mode, True)
    return mode


FOLDER_LIST_COMMANDS = ('go to folder', 'open folder')
ACCOUNT_SWITCH_COMMANDS = ('switch account', 'switch accounts', 'switch email account', 'switch email accounts',
                           'change account', 'change email account', 'choose account', 'choose an account',
                           'choose email account', 'choose an email account')
ACCOUNT_REMOVE_COMMANDS = ('remove account', 'remove an account', 'remove email account', 'remove an email account',
                           'remove mail account', 'delete account', 'delete an account', 'delete email account',
                           'delete an email account', 'delete mail account', 'disconnect account',
                           'disconnect an account', 'disconnect email account', 'disconnect an email account')


def account_picker_words():
    import account_picker
    return account_picker


def connected_selection():
    """The chosen (provider, address), or None; never raises for a damaged or locked file."""
    try:
        return ProtectedStore(APP).selected()
    except (TypeError, AccountError, OSError):
        return None


def connected_accounts(store):
    """The connected (provider, address) pairs; none when the saved accounts cannot be read."""
    try:
        return list(store.accounts())
    except (TypeError, AccountError, OSError):
        return []


def open_account_picker(purpose, mode, origin=None):
    """List the connected accounts: before their folders, to switch, or to remove one."""
    global ACCOUNT_PICKER, NAMED_LIST, FOLDER_ACCOUNT_RETURN
    import account_picker as words
    store = ProtectedStore(APP)
    accounts = connected_accounts(store)
    if not accounts:
        ACCOUNT_PICKER = None
        speak_prompt('No email account is connected. Say add account.')
        return mode
    try:
        current = store.selected()
    except (AccountError, OSError):
        current = None
    current = current if current in accounts else None
    if purpose == 'inbox' and len(accounts) == 1:
        ACCOUNT_PICKER = None
        speak('Only one email account is connected: ' + words.label(accounts[0]) + '. Say add account to connect another.')
        return mode
    if purpose == 'folders' and origin is None:
        origin = {'account': current, 'session': mail_session}
    NAMED_LIST = None
    FOLDER_ACCOUNT_RETURN = None
    ACCOUNT_PICKER = {'purpose': purpose, 'accounts': words.ordered(accounts, current), 'index': 0,
                      'current': current, 'mode': mode, 'confirm': None, 'origin': origin}
    if purpose == 'remove' and len(accounts) == 1:
        return ask_account_removal(accounts[0], mode)
    speak(words.opening(ACCOUNT_PICKER))
    return mode


def ask_account_removal(account, mode):
    """Nothing is removed without a yes. Enter alone answers No until Yes is chosen."""
    global CONFIRM_CHOICE
    import account_picker as words
    ACCOUNT_PICKER['confirm'] = account
    CONFIRM_CHOICE = 'no'
    speak(words.removal_question(account))
    return mode


def account_picker_key(key):
    """The command for a key while the account list is open; '' leaves the key to the screen."""
    import account_picker as words
    picker = ACCOUNT_PICKER
    request = {'Up': 'previous', 'Left': 'previous', 'ShiftTab': 'previous', 'Down': 'next', 'Right': 'next',
               'Tab': 'next', 'Home': 'first item', 'End': 'last item', 'Enter': 'that one', 'Space': 'that one',
               'Escape': 'cancel'}.get(key)
    if request:
        return request
    if key.startswith('Letter:'):
        letter = key[7:]
        index = next_match([words.name(p) for p, _ in picker['accounts']], picker['index'], letter)
        if index is None:
            speak('No account starts with ' + letter.upper() + '.')
        else:
            picker['index'] = index
            speak(words.item(picker))
        return None
    return ''


def account_picker_step(text, command, mode):
    """One answer while the account list or its yes or no question is open. None: not for the list."""
    global ACCOUNT_PICKER
    import account_picker as words
    picker = ACCOUNT_PICKER
    if picker.get('mode') != mode or command in ('main menu', 'back to main menu', 'exit companion') or spoken_control(text):
        ACCOUNT_PICKER = None
        return None
    control = speech_control(text)
    if control:
        control_speech(control)
        return mode
    if picker['confirm']:
        if command in words.YES:
            return remove_mail_account(picker['confirm'], mode)
        if command in words.NO:
            ACCOUNT_PICKER = None
            speak('Nothing was removed.')
            return mode
        speak(words.removal_question(picker['confirm']))
        return mode
    accounts = picker['accounts']
    if command in words.CANCEL:
        return close_account_picker(mode)
    if command in words.NEXT + words.PREVIOUS + words.FIRST + words.LAST:
        old = picker['index']
        new = (old + 1 if command in words.NEXT else old - 1 if command in words.PREVIOUS else
               0 if command in words.FIRST else len(accounts) - 1)
        new = max(0, min(len(accounts) - 1, new))
        picker['index'] = new
        edge = ('' if new != old else 'Start of list. ' if command in words.PREVIOUS + words.FIRST else 'End of list. ')
        speak(edge + words.item(picker))
        return mode
    if command in words.REPEAT:
        speak(words.item(picker))
        return mode
    if command in words.CHOOSE:
        return choose_mail_account(picker['index'], mode)
    found = words.choice(text, accounts)
    if isinstance(found, int):
        return choose_mail_account(found, mode)
    speak(words.several(found[1], accounts) if found else words.LIST_HINT)
    return mode


def close_account_picker(mode):
    """Escape or cancel in the account list."""
    global ACCOUNT_PICKER, mail_session
    picker, ACCOUNT_PICKER = ACCOUNT_PICKER, None
    if picker['purpose'] == 'remove':
        speak('Nothing was removed.')
        return mode
    if picker['purpose'] == 'inbox':
        speak('Account list closed. Nothing was changed.')
        return mode
    origin = picker.get('origin') or {}
    account, session = origin.get('account'), origin.get('session')
    if account and session is not None and session is not mail_session:
        # An account was chosen, then both lists were canceled: go back to where you were.
        try:
            ProtectedStore(APP).select(*account)
        except (AccountError, OSError):
            speak('Folder list canceled.')
            return mode
        mail_session = session
        from account_picker import label
        speak('Folder list canceled. Back to ' + label(account) + ', ' + session.folder_name + '.')
        return mode
    speak('Folder list canceled.')
    return mode


def choose_mail_account(index, mode):
    """Act on the account chosen in the list."""
    global ACCOUNT_PICKER, FOLDER_ACCOUNT_RETURN, mail_session, pending_send
    import account_picker as words
    picker = ACCOUNT_PICKER
    account = picker['accounts'][index]
    picker['index'] = index
    store = ProtectedStore(APP)
    if account not in connected_accounts(store):
        ACCOUNT_PICKER = None
        speak('That account is no longer connected. Nothing was changed. Say list email accounts to hear them.')
        return mode
    if picker['purpose'] == 'remove':
        return ask_account_removal(account, mode)
    ACCOUNT_PICKER = None
    if picker['purpose'] == 'inbox':
        if mode in ('mailbox', 'email_draft'):
            return switch_mail_account(*account, mode)
        try:
            provider, address = store.select(*account)
        except (AccountError, OSError) as exc:
            speak(str(exc) if isinstance(exc, AccountError) else 'The account could not be chosen. Try again.')
            return mode
        pending_send = None
        mail_session = MailSession(provider, APP, progress=mail_progress)
        speak('Switched to ' + words.label((provider, address)) + '. ' + mail_session.process('open inbox'))
        return 'mailbox'
    # Folders: this account's folder list. Escape there comes back to the accounts.
    FOLDER_ACCOUNT_RETURN = picker['origin']
    if account == picker['current'] and mail_session is not None and mail_session.provider == account[0]:
        speak(mail_session.process('go to folder'))
    else:
        try:
            provider, address = store.select(*account)
        except (AccountError, OSError) as exc:
            FOLDER_ACCOUNT_RETURN = None
            speak(str(exc) if isinstance(exc, AccountError) else 'The account could not be chosen. Try again.')
            return mode
        pending_send = None
        mail_session = MailSession(provider, APP, progress=mail_progress)
        speak('Switched to ' + words.label((provider, address)) + '. ' + mail_session.process('go to folder'))
    if not getattr(mail_session, 'folder_picker', None):
        FOLDER_ACCOUNT_RETURN = None
    return mode


def switch_mail_account(provider, address, mode):
    """Use another connected account: the inbox, the email draft, or the next email."""
    global pending_send, mail_session
    if (mode == 'email_draft' and email_draft and email_draft.response_context
            and email_draft.provider not in REMOVED_MAIL_PROVIDERS):
        speak('This reply or forward belongs to its original mailbox. Save it and start a new email to use another account.')
        return mode
    try:
        provider, address = ProtectedStore(APP).select(provider, address)
    except (AccountError, OSError) as exc:
        # A removed account, or its file locked by another program: say so, nothing changes.
        speak(str(exc) if isinstance(exc, AccountError) and str(exc) else
              'The account could not be chosen. Nothing was changed. Try again.')
        return mode
    pending_send = None
    if mode == 'email_draft' and email_draft:
        if email_draft.provider in REMOVED_MAIL_PROVIDERS:
            email_draft.move_to_account(provider)
        else:
            email_draft.provider = provider
            email_draft.save()
    from account_picker import label
    if mode == 'mailbox':
        mail_session = MailSession(provider, APP, progress=mail_progress)
        speak('Switched to ' + label((provider, address)) + '. ' + mail_session.process('open inbox'))
    else:
        speak('Switched to ' + label((provider, address)) + '.')
    return mode


def remove_mail_account(account, mode):
    """After a yes: forget one account's saved sign-in and say what happens next."""
    global ACCOUNT_PICKER, mail_session, pending_send
    import account_picker as words
    ACCOUNT_PICKER = None
    provider, address = account
    store = ProtectedStore(APP)
    try:
        before = store.selected()
    except (AccountError, OSError):
        before = None
    try:
        now = store.remove(provider, address)
    except AccountError:
        speak('That account is no longer connected. Nothing was removed.')
        return mode
    except OSError:
        speak('The account could not be removed. Another program may be using its file. '
              'Close other programs, then say remove email account again.')
        return mode
    remaining = connected_accounts(store)
    message = ('The ' + words.name(provider) + ' account ' + address + ' is removed from Voice Companion. ' +
               words.removal_note(provider))
    if not remaining:
        message += ' No email account is connected now. Say add account to connect one.'
    elif now is None:
        message += ' Say switch account to choose the account to use.'
    elif before == account:
        message += ' Now using the ' + words.name(now[0]) + ' account ' + now[1] + '.'
    else:
        message += ' You are still using the ' + words.name(now[0]) + ' account ' + now[1] + '.'
    if pending_send is not None and tuple(pending_send[1:3]) == account:
        pending_send = None
    affected = mail_session is not None and mail_session.provider == provider and (before == account or before is None)
    if not affected:
        speak(message)
        return mode
    if mode == 'mailbox' and now is not None:
        mail_session = MailSession(now[0], APP, progress=mail_progress)
        speak(message + ' ' + mail_session.process('open inbox'))
        return mode
    mail_session = None
    if now is not None:
        message += ' Say email to open it.'
    speak(message)
    return 'awake' if mode == 'mailbox' else mode


def retire_old_mail_accounts():
    """At startup: remove Gmail and Microsoft accounts older versions connected directly.

    Email works only through Thunderbird and Yahoo now. Said once, when files
    were removed. A problem here must never stop the app."""
    global mail_session, pending_send
    import mail_accounts
    import account_picker
    try:
        result = mail_accounts.retire_removed_accounts(APP)
        if not result.removed:
            return
        if mail_session is not None and mail_session.provider in mail_accounts.REMOVED_PROVIDERS:
            mail_session = None
        if pending_send is not None and pending_send[1] in mail_accounts.REMOVED_PROVIDERS:
            pending_send = None
        startup_notice(account_picker.retired_message(result))
    except Exception:
        try:
            APP.mkdir(parents=True, exist_ok=True)
            (APP / 'Email-Accounts-Error.txt').write_text(traceback.format_exc(), encoding='utf-8')
        except OSError:
            pass


def new_thunderbird_setup(adding=False):
    """Thunderbird setup on this computer, with the add-on package this copy of the app ships.

    adding: for "add Thunderbird account"; once Thunderbird is connected, ask for the new address."""
    import thunderbird_setup
    from mail_accounts import thunderbird_connect
    return thunderbird_setup.Setup(thunderbird_setup.WindowsSystem(), addon_package(),
                                   lambda: thunderbird_connect(ProtectedStore(APP)), mail_relay, speak_status,
                                   ready=thunderbird_add_ready if adding else thunderbird_connect_ready, adding=adding)


THUNDERBIRD_YES = ('yes', 'yes please', 'y', 'yeah', 'yep', 'sure', 'okay', 'ok', 'do it', 'go ahead')
THUNDERBIRD_REPEAT = ('repeat', 'say that again', 'what', 'what now', 'what do i do', 'help', 'repeat that')


def thunderbird_setup_step(step, mode):
    """Speak one setup result; keep its yes or no question waiting."""
    global THUNDERBIRD_SETUP, CONFIRM_CHOICE
    flow = THUNDERBIRD_SETUP['flow'] if THUNDERBIRD_SETUP else None
    THUNDERBIRD_SETUP = None
    if step.question == 'address':
        return ask_thunderbird_address(step.message, mode)
    if step.question:
        THUNDERBIRD_SETUP = {'flow': flow, 'question': step.question, 'message': step.message}
        CONFIRM_CHOICE = 'yes'
    speak(step.message)
    sync_app_context(mode, True)
    return mode


def thunderbird_setup_answer(command, mode):
    """Yes starts the offered step. Anything else, except repeat, is no."""
    global THUNDERBIRD_SETUP
    if command in THUNDERBIRD_REPEAT:
        speak(THUNDERBIRD_SETUP['message'])
        return mode
    flow, question = THUNDERBIRD_SETUP['flow'], THUNDERBIRD_SETUP['question']
    if question in ('add_another', 'add_first'):
        THUNDERBIRD_SETUP = None
        if command in THUNDERBIRD_YES:
            return start_thunderbird_add(mode)
        speak('Okay. Nothing else was changed.')
        sync_app_context(mode, True)
        return mode
    try:
        step = flow.answer(question, command in THUNDERBIRD_YES)
    except Exception:
        THUNDERBIRD_SETUP = None
        speak('Thunderbird setup stopped because of a problem. Nothing else was changed. Say add account to try again.')
        return mode
    return thunderbird_setup_step(step, mode)


def _handle(text, mode, typed=False):
    global DOCUMENT_NAMING_ACTION, THUNDERBIRD_SIGN_IN
    global SYNTH_PICK_INDEX, VOICE_PICK_ENGINE, MAIN_MENU_INDEX
    global SLEEP_RETURN_MODE, document, email_draft, pending_website, pending_send, mail_session, web_session, account_setup, INPUT_MODE, VOICE_PICK_INDEX, VOICE_PICK_ORIGINAL, VOICE_PICK_CONFIRM, MEDIA_SECTION, help_session, help_return_mode, tutorial_session, AI_VOICE_NAME
    text = text.strip()
    global UPDATE_MANUAL, UPDATE_LAST_PERCENT, SETTINGS_PANEL, SETTINGS_RETURN_MODE, VERBOSITY, PENDING_DOCUMENT_NAME, DOCUMENT_NAME_RETURN, DOCUMENT_CLOSE_TARGET, CONFIRM_CHOICE
    global POWER_ACTION, POWER_RETURN_MODE, EXIT_RETURN_MODE
    global OPEN_NOTE, ACCOUNT_ENTRY, THUNDERBIRD_SETUP
    global SEARCH_RETURN_MODE,NAMED_LIST,EMAIL_LINK_RECIPIENT,MAIN_MENU_LEAD
    # Phrases that list saved notes. On the Notepad screen only the clear commands
    # count; a sentence such as "My notes are on the desk" is text.
    NOTE_LIST_COMMANDS = ('list notes', 'list my notes', 'list saved notes', 'list the notes', 'open notes', 'open my notes',
                          'open a note', 'open note', 'open saved notes', 'open a saved note', 'open saved note',
                          'show notes', 'show my notes', 'show saved notes')
    NOTE_LIST_ELSEWHERE = NOTE_LIST_COMMANDS + ('my notes', 'saved notes', 'notes list', 'what notes do i have',
                                              'read note', 'read notes', 'read my note', 'read my notes', 'read saved notes')
    command=normalized_command(text)
    if text=='@microphone':
        return set_microphone(MICROPHONE_OFF, mode)
    if text=='@main_menu':
        # Control+F1: the same as saying Main menu, so each screen saves or asks first.
        if mode=='awake' and ACCOUNT_ENTRY is None and THUNDERBIRD_SETUP is None:
            announce_main_menu();return 'awake'
        return handle('main menu', mode, typed=True)
    if command in MICROPHONE_OFF_PHRASES+MICROPHONE_ON_PHRASES and not writing_dictation(mode, typed):
        return set_microphone(command in MICROPHONE_ON_PHRASES, mode)
    if command in TRANSCRIPT_START_PHRASES+TRANSCRIPT_STOP_PHRASES and not writing_dictation(mode, typed):
        return set_transcript(command in TRANSCRIPT_START_PHRASES, mode)
    if ACCOUNT_ENTRY is not None:
        return account_entry_step(text, command, mode)
    if THUNDERBIRD_SETUP is not None:
        return thunderbird_setup_answer(command, mode)
    if command=='toggle audio':
        speak(MEDIA_HUB.command(command) if MEDIA_HUB else 'No audio is playing. Open a media file, station or episode first.')
        return mode
    # "What time is it?" works on every screen. Where people write, "Time." alone is text.
    clock=time_date.clock_request(text,clock_text_entry(mode,typed))
    if clock:
        speak_clock(clock);return mode
    # "Bigger text", "Change colors", "Full screen": the screen for low vision, from every screen
    # where words are commands. In a document, email, note or text box they are text.
    display=__import__('display_options').voice_request(text) if mode!='settings' and not clock_text_entry(mode,typed) else None
    if display:
        change_display(display,mode);return mode
    if ACCOUNT_PICKER is not None and not text.startswith('@sm:'):
        picked = account_picker_step(text, command, mode)
        if picked is not None:
            return picked
    # Adding a Thunderbird account: these work on every screen, the web sign-in page included.
    if THUNDERBIRD_WATCH is not None or THUNDERBIRD_SIGN_IN or not writing_dictation(mode, typed):
        handled = thunderbird_setup_command(command, mode)
        if handled is not None:
            return handled
    manager=globals().get('FILE_MANAGER')
    if mode=='files' and manager and manager.pending and manager.pending[0] in ('rename','search','search all'):
        try:speak(manager.command(text))
        except (OSError,ValueError) as error:speak(str(error))
        sync_app_context(mode,True);return mode
    if mode=='chrome_setup':
        import chrome_setup
        return chrome_setup.handle(sys.modules[__name__],text,mode)
    try:
        menu_result=screen_menu().handle(text,mode)
        if menu_result is not None:return menu_result
        task_result=task_navigation().handle(text,mode)
        if task_result is not None:return task_result
        # The Downloads screen, and "download manager" from the web (download_screen.py).
        download_result=__import__('download_screen').handle(sys.modules[__name__],text,mode,typed)
        if download_result is not None:return download_result
        # The Contacts screen, and "email John" or "open contacts" from the main menu (contacts_screen.py).
        contacts_result=__import__('contacts_screen').handle(sys.modules[__name__],text,mode,typed)
        if contacts_result is not None:return contacts_result
        # The Calendar screen, and "what's on my calendar today?" from other screens (calendar_screen.py).
        calendar_result=__import__('calendar_screen').handle(sys.modules[__name__],text,mode,typed)
        if calendar_result is not None:return calendar_result
        # Email attachments: the Attachments list, attaching, saving (attachment_screen.py).
        attachment_result=__import__('attachment_screen').handle(sys.modules[__name__],text,mode,typed)
        if attachment_result is not None:return attachment_result
    except (ValueError,OSError) as exc:
        speak(str(exc));return mode
    # A whole command only; in a document, email or note these words are text.
    if command in ('set up chrome','setup chrome','chrome setup','connect chrome','install chrome','install google chrome','set up the browser extension','install the browser extension','install browser extension','add the chrome extension','install the chrome extension') and mode!='search' and not writing_dictation(mode,typed):
        import chrome_setup
        return chrome_setup.begin(sys.modules[__name__],mode)
    if command in ('regular browser','open regular browser','connect regular browser'):
        return open_browser_menu(regular=True)
    if mode=='browser_menu' and web_session.menu.level=='name':return handle_browser_menu(text)
    background_request=re.fullmatch(r'(?:set )?keep audio playing in (?:the )?background (on|off)',command)
    if background_request and mode!='settings':
        PREFERENCES['keep_audio_playing']=background_request[1]=='on';save_preferences()
        speak('Keep audio playing in the background '+background_request[1]+'.');return mode

    if mode=='web' and command in ('web browser menu','browser menu','leave website'):
        web_session.leave(PREFERENCES.get('web_keep_open',False))
        return open_browser_menu()
    if command in CLOSE_BROWSER_COMMANDS and web_session and (mode in ('web','browser_menu') or mode=='awake' and web_session.snapshot):
        return close_browser_window()
    if mode == 'tutorial':
        if change_speech_setting(text) or use_synthesizer(text) or voice_menu(command):return mode
        control=speech_control(text)
        if control:
            control_speech(control);return mode
        if spoken_control(command)=='exit':
            tutorial_session=None
            return handle(text,'awake',typed)
        if spoken_control(command)=='sleep':
            SLEEP_RETURN_MODE=mode
            speak('Going to sleep. Say wake up to continue here.')
            return 'sleep'
        if tutorial_session is None:
            tutorial_session=PracticeTutorial(GUIDE)
            speak(tutorial_session.start());return mode
        if command in ('start of list','beginning of list','first item','end of list','last item') and tutorial_session.menu():
            navigation_key('End' if command in ('end of list','last item') else 'Home',mode);return mode
        interrupt_speech()
        result,closed=tutorial_session.process(text)
        if closed:
            # Said with Main menu in one message (handle() says it); two would cut each other off.
            tutorial_session=None;MAIN_MENU_LEAD='Tutorial closed.'
            return 'awake'
        speak(result)
        return mode
    if mode=='browser_menu' and command not in ('help','options','open options','main menu','back to main menu','exit companion','go to sleep') and not command.startswith(('help with ', 'help for ')) and (command=='resume' or not speech_control(command)) and speech_setting_request(text) is None and command not in VOICE_LIST_COMMANDS and command not in ('use sapi','use sapi voice','use windows voice','use ai voice','use espeak','document help','email help','web help','radio help','podcast help','commands help') and not spoken_control(command):
        return handle_browser_menu(text)
    if command in ('next','previous'):
        state=menu_state(mode)
        if state and state[1] is not None and (state[1]==0 and command=='previous' or state[1]==len(state[0])-1 and command=='next'):
            speak('Start of list.' if command=='previous' else 'End of list.');return mode
    # Word processor requests are handled by the launch menu.
    if command in ('notepad','open notepad'):text='write a note';command=text
    if command in ('start of list','beginning of list','first item','end of list','last item') and menu_state(mode):
        navigation_key('End' if command in ('end of list','last item') else 'Home',mode)
        return mode
    if NAMED_LIST:
        if command in ('cancel','close list','escape','go back'):
            NAMED_LIST=None;speak('List closed.')
            if mode=='email_draft':focus_email_entry()
            return mode
        if command in ('next','previous'):
            NAMED_LIST['index']=bounded_index(NAMED_LIST['index'],1 if command=='next' else -1,len(NAMED_LIST['items']))
            speak(announce_item(NAMED_LIST['items'][NAMED_LIST['index']][0],NAMED_LIST['index'],len(NAMED_LIST['items'])));return mode
        if command in PICK_CONFIRM:
            request=NAMED_LIST['items'][NAMED_LIST['index']][1];NAMED_LIST=None
            result=handle(request,mode,typed=True)
            if result=='email_draft':focus_email_entry()
            return result
        NAMED_LIST=None
    if mode=='exit_confirm':
        if command in ('no','no thanks','cancel','main menu','back'):
            speak('Canceled.');return EXIT_RETURN_MODE
        if command in ('yes','yes please','okay','ok','confirm','confirm that','that one'):
            return finish_exit(EXIT_RETURN_MODE)
        speak('Exit Voice Companion? Are you sure? Yes or no.');return mode
    if mode=='power_confirm':
        if command in ('no','no thanks','cancel','main menu','back'):
            POWER_ACTION=None;speak('Canceled.');return POWER_RETURN_MODE
        if command in ('yes','yes please','okay','ok','confirm','confirm that','that one'):
            try:
                if email_draft is not None:email_draft.save()
                flush_note(POWER_RETURN_MODE)
                from windows_actions import request_power
                request_power(POWER_ACTION)
            except (OSError,ValueError,__import__('subprocess').SubprocessError) as exc:
                speak('The computer could not '+str(POWER_ACTION)+'. Your app is still open.');POWER_ACTION=None;return POWER_RETURN_MODE
            speak('Restarting the computer.' if POWER_ACTION=='restart' else 'Shutting down the computer.')
            POWER_ACTION=None;return 'exit'
        speak('Restart the computer? Yes or no.' if POWER_ACTION=='restart' else 'Shut down the computer? Yes or no.');return mode
    if mode=='document_save' and not spoken_control(command) and command not in ('options','open options','settings','open settings'):
        if command in ('cancel','go back','back'):
            speak('Your document is still open.');return 'document'
        if command in ('yes','yes please','ok','okay','confirm','confirm that','that one'):
            if document is not None and not re.fullmatch(r'Untitled(?: \d+)?',document.title,re.I):
                try:
                    document.save()
                    checkpoint_document()
                except OSError:
                    speak('I could not save the document. Your document is still open.');return 'document'
                filename=document.path.name
                document=None
                speak('Saved '+filename+'.')
                return handle({'exit':'exit companion','restart':'restart computer','shutdown':'shut down computer'}[DOCUMENT_CLOSE_TARGET],'awake',typed) if DOCUMENT_CLOSE_TARGET in ('exit','restart','shutdown') else task_navigation().enter('word_processor','Word processor menu') if DOCUMENT_CLOSE_TARGET=='word_processor' else 'awake'
            DOCUMENT_NAME_RETURN=DOCUMENT_CLOSE_TARGET
            DOCUMENT_NAMING_ACTION='name document'
            PENDING_DOCUMENT_NAME=None
            speak('Name this document. Say or type its name, then press Enter. Say cancel to keep editing.')
            return 'document_name'
        if command in ('no','no thanks'):
            try:discard_document_changes()
            except OSError:
                speak('Could not discard changes safely. Your document is still open.');return 'document'
            document=None
            speak('Changes discarded.')
            return handle({'exit':'exit companion','restart':'restart computer','shutdown':'shut down computer'}[DOCUMENT_CLOSE_TARGET],'awake',typed) if DOCUMENT_CLOSE_TARGET in ('exit','restart','shutdown') else task_navigation().enter('word_processor','Word processor menu') if DOCUMENT_CLOSE_TARGET=='word_processor' else 'awake'
        speak('Save this document? Yes or no.');return mode
    if mode=='document_name' and not spoken_control(normalized_command(text)) and normalized_command(text) not in ('options','open options','settings','open settings'):
        if normalized_command(text) in ('cancel','cancel naming','go back'):
            PENDING_DOCUMENT_NAME=None
            speak('Naming canceled. Your document is still open.');return 'document'
        confirm=not typed and normalized_command(text) in ('ok','okay','confirm','confirm that','that one')
        if confirm and PENDING_DOCUMENT_NAME is None:
            speak('Say or type a document name first.');return 'document_name'
        title=re.sub(r'^(?:name document|save document as|save as)\s+','',text.strip(),flags=re.I)
        if confirm:title=PENDING_DOCUMENT_NAME[1]
        elif not typed:
            PENDING_DOCUMENT_NAME=(time.monotonic_ns(),title)
            speak(title+'. Press Enter or say okay to save the name.');return 'document_name'
        result=document.process(DOCUMENT_NAMING_ACTION+' '+title)
        speak(result)
        if result.startswith(('Document named ','Saved document as ')):
            PENDING_DOCUMENT_NAME=None
            checkpoint_document()
            return handle({'exit':'exit companion','restart':'restart computer','shutdown':'shut down computer'}[DOCUMENT_NAME_RETURN],'awake',typed) if DOCUMENT_NAME_RETURN in ('exit','restart','shutdown') else task_navigation().enter('word_processor','Word processor menu') if DOCUMENT_NAME_RETURN=='word_processor' else DOCUMENT_NAME_RETURN
        return 'document_name'
    if mode == 'settings' and SETTINGS_PANEL is not None and not SETTINGS_PANEL.closed.is_set():
        control=speech_control(text)
        if control:
            control_speech(control);return mode
        if command in ('main menu','back to main menu','go to main menu'):
            # Control+F1 or "Main menu" in Options: close it (changes not saved) and go there.
            SETTINGS_PANEL.cancel();__import__('app_settings').stop_preview(sys.modules[__name__])
            MAIN_MENU_LEAD='Options closed.'   # handle() says it with Main menu, in one message
            return 'awake'
        if spoken_control(text) in ('exit','sleep'):
            SETTINGS_PANEL.cancel()
            return handle(text,SETTINGS_RETURN_MODE,typed)
        SETTINGS_PANEL.command(text)
        return mode
    search_engine_request=re.fullmatch(r'(?:(?:set|choose|use) )?(?:default )?search engine(?: to)? (google|bing|brave(?: search)?|duck duck go|duckduckgo)',command)
    if search_engine_request:
        engine='brave' if search_engine_request[1].startswith('brave') else 'duckduckgo' if search_engine_request[1].startswith('duck') else search_engine_request[1]
        PREFERENCES['web_search_engine']=engine;save_preferences()
        if web_session:web_session.search_engine=engine
        speak('Default search engine '+{'google':'Google','bing':'Bing','brave':'Brave Search','duckduckgo':'DuckDuckGo'}[engine]+'.');return mode
    ducking_request=audio_ducking_request(command)
    if ducking_request:
        key,value=ducking_request
        if value is None:
            if not writing_dictation(mode,typed):
                speak('Choose a ducking level: light, medium, or strong. Light is 25 percent quieter, medium is 50 percent, and strong is 75 percent.')
                return mode
        else:
            PREFERENCES[key]=value
            try:save_preferences()
            except OSError:speak('The audio ducking setting could not be saved. It works until Voice Companion closes.')
            speak({'duck_audio':'Audio ducking '+('on. Browser and media sound gets quieter while Companion speaks.' if value else 'off. Browser and media sound stays at full volume.'),
                   'duck_level':'Ducking level '+str(value)+'.',
                   'duck_other_apps':'Lower other apps '+('on. Other programs also get quieter while Companion speaks. Screen readers are never lowered.' if value else 'off. Only browsers and Companion media get quieter.')}[key])
            return mode
    keep_open_request=re.fullmatch(r'(?:(?:set|turn) )?keep browser window open(?: to)? (on|off)',command)
    if keep_open_request:
        PREFERENCES['web_keep_open']=keep_open_request[1]=='on';save_preferences()
        speak('Keep browser window open '+keep_open_request[1]+'.');return mode
    auto_read_request=re.fullmatch(r'(?:(?:set|turn) )?(?:automatic page reading|automatically read (?:web )?pages|read pages automatically)(?: to)? (on|off)',command)
    toggle_read=re.fullmatch(r'(?:turn|switch) (on|off) automatic page reading',command)
    enable_read=re.fullmatch(r'(enable|disable) automatic page reading',command)
    if auto_read_request or toggle_read or enable_read:
        enabled=enable_read[1]=='enable' if enable_read else (auto_read_request or toggle_read)[1]=='on'
        PREFERENCES['web_auto_read']=enabled;save_preferences()
        if web_session:web_session.auto_read=enabled
        speak('Automatic page reading '+('on.' if enabled else 'off.'));return mode
    startup_command=re.fullmatch(r'(?:set )?(?:start with windows|start voice companion with windows|start automatically with windows|windows startup)(?: to)? (on|off)',command)
    if startup_command:
        try:
            from windows_actions import set_startup
            enabled=startup_command[1]=='on';set_startup(enabled)
            PREFERENCES['start_with_windows']=enabled;save_preferences()
            speak('Start with Windows '+startup_command[1]+'.')
        except (OSError,ValueError) as exc:speak(str(exc))
        return mode
    if mode=='mailbox' and re.fullmatch(r'go to (?:the )?(main menu|options|settings)',command):
        # In the inbox these are app commands, not folder names.
        text=command=re.fullmatch(r'go to (?:the )?(main menu|options|settings)',command)[1]
    if normalized_command(text) in ('options','open options','show options','settings','open settings','show settings','preferences','open preferences'):
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
    if re.match(r'^(?:set )?(?:echo dictated text|typing echo|phonetics|phonetic pronunciation|phonetic enabled|delayed phonetic pronunciation|phonetic delay|when encountering capital letters|capital letters)\b.+',update_command):
        session=SettingsSession(PREFERENCES,{})
        request=session.voice_setting(text)
        if request and request[0] in ('dictation_echo','typing_echo','phonetic_enabled','phonetic_delay','capital_letters'):
            try:
                message=session.set(*request)
                PREFERENCES[request[0]]=session.values[request[0]]
                save_preferences()
                if request[0]=='capital_letters' and KEYBOARD_SPEECH:KEYBOARD_SPEECH.capital_letters=session.values['capital_letters']
                speak(message+'.')
            except (ValueError,OSError) as exc:
                if not (isinstance(exc,ValueError) and writing_dictation(mode,typed)):speak(str(exc));return mode
            else:return mode
    if mode=='awake' and update_command in ('main menu','back to main menu'):
        announce_main_menu()
        return mode
    if re.match(r'^(?:set )?(?:echo dictated text|typing echo|phonetics|phonetic pronunciation|phonetic enabled|delayed phonetic pronunciation|phonetic delay|audio ducking|duck audio|automatic updates|check updates|document font|document size|document spacing|document alignment|default font|default line spacing|default alignment|browser|input mode|dictation type|email format|email list size|sending account|station database|podcast limit|ask before exiting voice companion|ask before shutting down the computer|ask before restarting the computer|announce email header names|email header names|email header order|spoken headers in order)\b.+',update_command):
        from app_settings import snapshot
        values,context=snapshot(sys.modules[__name__])
        session=SettingsSession(values,context)
        request=session.voice_setting(text)
        if request:
            try:
                message=session.set(*request)
            except ValueError as exc:
                # "Browser tabs are confusing." while writing is text, not a bad setting.
                if not writing_dictation(mode,typed):speak(str(exc));return mode
            else:
                try:
                    apply_settings(session.values)
                    speak(message+'.')
                except (ValueError,OSError) as exc:speak(str(exc))
                if mode=='mailbox' and mail_session is None:
                    # Changing the sending account closes the old mailbox session.
                    return 'awake'
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
        interrupt_speech()
        speak(str(UPDATES.percent)+' percent.')
        return mode
    if mode == 'update_offer':
        if update_command in ('no', 'no thanks', 'not now', 'later', 'cancel'):
            return return_from_update('' if STARTUP_UPDATE_PENDING else 'Okay. You can keep using this version.')
        if update_command in ('yes', 'yes please', 'install it', 'install update', 'okay', 'ok'):
            if not UPDATES.can_install():
                return return_from_update('Automatic installation works in the installed app. Quick Test can check for updates but cannot replace an installation.')
            try:
                if document is not None: document.save()
                if email_draft is not None: email_draft.save()
                flush_note(UPDATE_RETURN_MODE)
            except (OSError, ValueError):
                return return_from_update('I could not save your work. The update will wait.')
            UPDATE_LAST_PERCENT = 0
            if UPDATES.download():
                speak('Downloading the update. Say cancel update to stop.')
                return 'update_download'
            return return_from_update('The updater is busy. Please try again shortly.')
        if spoken_control(update_command) != 'exit':
            speak('Say yes to install the update, or no to continue.' if microphone_state() == 'on' else 'Press Y to install the update, or N to continue.')
            return mode
    if mode == 'update_download' and spoken_control(update_command) != 'exit':
        if UPDATES.phase in ('preparing','installing'):
            speak('The update is being installed and cannot be canceled now. Voice Companion will close and reopen when it finishes.')
            return mode
        if update_command in ('cancel', 'cancel update', 'stop update', 'no'):
            UPDATES.canceled.set()
            return return_from_update('Update canceled. You can keep using the app.')
        else: pass  # The cancel reminder is announced once when downloading begins.
        return mode
    if normalized_command(text) in ('set up ai voice', 'setup ai voice', 'ai voice setup'):
        if APP_WINDOW is not None:
            APP_WINDOW.setup_ai_voice()
            speak('AI voice setup dialog. It is for your trainer. The key is hidden. Use Tab to move between fields.')
        else:
            speak('AI voice setup requires the app window.')
        return mode
    if use_synthesizer(text):return mode
    bare_audio_control = normalized_command(text) in ('stop','resume') and MEDIA_HUB is not None and bool(MEDIA_HUB.player.kind)
    control = None if bare_audio_control else speech_control(text)
    if control:
        control_speech(control)
        return mode
    if text and (mode!='help' or HelpSession.accepts(text) or spoken_control(command)):
        interrupt_for_dictation()
        field=getattr(web_session,'keyboard_field',None) if mode=='web' else None
        fields=getattr(web_session,'form_fields',None);index=getattr(web_session,'form_index',None)
        if not isinstance(field,dict) and isinstance(fields,list) and isinstance(index,int) and 0<=index<len(fields):field=fields[index]
        password_field=isinstance(field,dict) and field.get('type')=='password'
        if mode=='web' and isinstance(web_session,WebSession) and not password_field:
            target=re.match(r'fill field (\d+) with ',text,re.I)
            if target and 0<int(target[1])<=len(web_session.choices):password_field=web_session.choices[int(target[1])-1].get('type')=='password'
            if not password_field and web_session.snapshot:
                try:password_field=(web_session.backend.focused_control() or {}).get('type')=='password'
                except Exception:pass
        feedback='Password input' if password_field else text
        if TEXT_MODE and not password_field:print('Heard:', feedback, flush=True)
        if APP_WINDOW is not None: APP_WINDOW.show('Heard: ' + feedback)
    command = re.sub(r'[.!?]+$', '', text.lower()).strip().replace('auto-download','auto download')
    # Recognizers add inner punctuation ("Yes, please."); compare plain phrases without it.
    command = re.sub(r'\s+', ' ', re.sub(r'\s*[,;!?]+\s*', ' ', command)).strip()
    if change_speech_setting(command, writing_dictation(mode, typed)): return mode
    if command in ('open user guides', 'open user guides folder', 'open guides', 'open guides folder',
                   'show user guides', 'where are the user guides', 'where are my user guides',
                   'where is the user guide folder'):
        try:
            from guide_files import publish_guides
            folder = publish_guides(documents_folder(), expected_version=APP_VERSION)
            if command.startswith(('open ', 'show ')) and os.name == 'nt':
                os.startfile(str(folder))
            speak('All six manual and release-note formats for Voice Companion ' + APP_VERSION + ' are in Documents, Voice Companion, User Guides. The full location is ' + str(folder) + '.')
        except (OSError, ValueError):
            speak('The user guide folder could not open or update. Say help for onboard instructions and ask your helper to check the installation.')
        return mode
    if voice_menu(command): return mode
    if command in ('start tutorial', 'audio tutorial', 'practice tutorial', 'open tutorial'):
        if mode not in ('awake', 'help'):
            speak('Say main menu first, then start tutorial. This protects what you are working on.')
            return mode
        help_session = None
        MAIN_MENU_INDEX=None
        tutorial_session = PracticeTutorial(GUIDE)
        speak(tutorial_session.start())
        return 'tutorial'
    power_commands=POWER_COMMANDS
    if mode!='sleep' and command in power_commands:
        action=power_commands[command]
        if mode in ('document','document_name','document_save') and document is not None:
            DOCUMENT_CLOSE_TARGET=action;CONFIRM_CHOICE='yes'
            speak('Save this document? Yes or no. Use arrows to choose, then Enter. Escape keeps editing.')
            return 'document_save'
        POWER_ACTION=action;POWER_RETURN_MODE=mode;CONFIRM_CHOICE='no'
        if not PREFERENCES.get('ask_before_'+action,True):return _handle('yes','power_confirm',typed)
        speak(('Restart the computer?' if action=='restart' else 'Shut down the computer?')+' Yes or no. Use arrows to choose, then Enter. Escape cancels.')
        return 'power_confirm'
    if command in ("what's new",'what s new','what is new','whats new','release notes','open release notes'):
        try:
            help_return_mode=mode if mode!='help' else help_return_mode
            help_session=HelpSession(GUIDE, release_notes_path=GUIDE.parent/'Voice Companion Release Notes.txt')
            speak(help_session.topic('what is new'));return 'help'
        except (OSError,ValueError):speak('Release notes could not open. Ask your helper to check the installation.');return mode
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
        if help_session.closed:
            # Name the screen you return to. The main menu is said by handle(), so say it with that.
            help_session = None
            if help_return_mode == 'awake':MAIN_MENU_LEAD = 'Help closed.'
            else:speak('Help closed. ' + options_return_notice(help_return_mode))
            return help_return_mode
        if result is not None:speak(result)
        return 'help'
    global SPEECH_PITCH, SPEECH_RATE, SPEECH_VOLUME, PUNCTUATION_LEVEL
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
    voice_request = command.startswith(('use voice ', 'change voice to ', 'set voice to '))
    if voice_request:
        name = re.sub(r'^(?:use voice|change voice to|set voice to)\s+', '', command)
        choices = [] if TEXT_MODE else voice_choices()
        matches = [item for item in choices if name == item[1].casefold()]
        if not matches:
            matches = [item for item in choices if name in item[1].casefold()]
        # "Use voice commands to send mail." while writing is text unless it names one voice.
        if len(matches) != 1 and writing_dictation(mode, typed): voice_request = False
    if voice_request:
        if TEXT_MODE: speak('Voice selection requires Windows.')
        else:
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
        # The two things a person most needs if a key press stopped the startup announcement.
        speak('Voice Companion version ' + APP_VERSION + '. ' + __import__('startup_speech').MICROPHONE[microphone_state()])
        return mode
    if spoken_control(command) == 'exit':
        if mode in ('document','document_name','document_save') and document is not None:
            DOCUMENT_CLOSE_TARGET='exit';CONFIRM_CHOICE='yes'
            speak('Save this document? Yes or no. Use arrows to choose, then Enter. Escape keeps editing.')
            return 'document_save'
        if PREFERENCES.get('ask_before_exit',True):
            EXIT_RETURN_MODE=mode;CONFIRM_CHOICE='no'
            speak('Exit Voice Companion? Are you sure? Yes or no. Use arrows or Tab to choose, then Enter. Escape cancels.')
            return 'exit_confirm'
        return finish_exit(mode)
    if spoken_control(command) == 'sleep':
        if mode != 'sleep': SLEEP_RETURN_MODE = mode
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
    global FILE_MANAGER,FILE_READER
    if command in ('file manager','open file manager','manage files','browse files'):
        if mode=='document' and document is not None:document.save()
        elif mode=='email_draft' and email_draft is not None:email_draft.save()
        else:flush_note(mode)
        pending_send=None
        if FILE_MANAGER is None:FILE_MANAGER=FileManager()
        task_navigation().picker=None
        task_navigation().reader_return='files'
        task_navigation().picker=None
        FILE_MANAGER.pick_extensions=None
        FILE_MANAGER.menu=False;FILE_MANAGER.pending=None
        from file_manager import start_location
        # Opens where File manager options say (drive list, C drive, Documents, ...).
        speak('File manager. '+FILE_MANAGER.refresh(start_location(PREFERENCES.get('file_manager_start'))))
        return 'files'
    if mode=='files':
        if command in ('main menu','back to main menu','close file manager','leave file manager'):
            # handle() says Main menu once when the screen changes.
            return 'awake'
        if command.startswith('file letter '):
            state=menu_state(mode)
            if state:speak(FILE_MANAGER.command('file letters '+text[len('file letter '):]));sync_app_context(mode,True)
            return mode
        try:result=FILE_MANAGER.command(text)
        except (ValueError,OSError) as exc:result=str(exc)
        if result.startswith('__attach__:'):
            # Enter while picking a file to attach, or the menu's Email this file and Attach to draft.
            screen=__import__('attachment_screen')
            try:
                if result=='__attach__:picked':return screen.picked(sys.modules[__name__])
                return screen.files_command(sys.modules[__name__],result[len('__attach__:'):],mode) or mode
            except (screen.AttachmentError,ValueError,OSError) as exc:
                speak(str(exc));return mode
        if result=='__main__':
            picker=task_navigation().picker
            task_navigation().picker=None
            if picker=='attach':return __import__('attachment_screen').picker_closed(sys.modules[__name__])
            # Escape from the drive list: Open a media file returns to the Media menu.
            if picker=='reader':return task_navigation().enter('reader_menu','Document reader menu')
            if picker:return task_navigation().enter('media_menu' if picker=='media' else 'word_processor','Media menu' if picker=='media' else 'Word processor menu')
            return 'awake'
        if FILE_MANAGER.pending and FILE_MANAGER.pending[0]=='delete':CONFIRM_CHOICE='no'
        speak(result)
        sync_app_context(mode,True)
        return mode
    # Help in Document reader opens the guide, as on every other screen.
    if mode=='file_reader' and not (command in ('help','user guide','open user guide','manual','open manual') or command.startswith(('help with ','help for '))):
        if command in ('back to files','go back','back','close document','leave document','escape'):
            if task_navigation().reader_return=='recent_documents':return task_navigation().enter('recent_documents','Recent documents')
            if task_navigation().reader_return=='reader_menu':return task_navigation().enter('reader_menu','Document reader menu')
            if task_navigation().reader_return=='downloads':
                back=__import__('download_screen').reader_back(sys.modules[__name__])
                if back:return back
            if task_navigation().reader_return=='attachments':
                back=__import__('attachment_screen').reader_back(sys.modules[__name__])
                if back:return back
            speak('Back to file manager. '+FILE_MANAGER.say_current());return 'files'
        if command in ('main menu','back to main menu'):
            # handle() announces the main menu once when the screen changes.
            return 'awake'
        request=reading_request(command)
        structure_request=re.fullmatch(r'(next|previous) (heading|table|row|cell|list|list item|link|footnote|bookmark)',command)
        if structure_request:
            speak(FILE_READER.navigate(structure_request[2],1 if structure_request[1]=='next' else -1));return mode
        if command in ('what formatting','say formatting','describe formatting'):
            speak(FILE_READER.describe_formatting());return mode
        if command in ('what is selected','say selection','read selection'):
            selected=FILE_READER.selection
            speak(FILE_READER.text[slice(*selected)] if selected else 'No text selected.');return mode
        if command in ('follow link','open link'):
            speak(FILE_READER.follow_link());return mode
        if request:
            unit,direction=request
            speak(FILE_READER.reading.read(direction=='top') if unit=='read' else FILE_READER.reading.move(unit,direction))
        elif command in ('beginning of document','top of document','go to beginning of document'):
            FILE_READER.reading.position=0;speak(FILE_READER.reading.move('line',0))
        elif command in ('end of document','go to end of document'):
            FILE_READER.reading.position=max(0,len(FILE_READER.text)-1);speak(FILE_READER.reading.move('line',0))
        elif command.startswith('find '):
            term=text[5:].strip();position=FILE_READER.text.casefold().find(term.casefold(),FILE_READER.reading.position+1)
            if position<0:position=FILE_READER.text.casefold().find(term.casefold())
            if position<0:speak('Text not found.')
            else:FILE_READER.reading.position=position;speak(FILE_READER.reading.move('line',0))
        else:speak('Document reader. Use arrows to read, or say read document, next word, next sentence, next paragraph, find followed by text, or back to files. This document is read-only.')
        return mode
    help_topic = re.fullmatch(r'(?:help with|help for|open help topic|go to help topic) (.+)', command)
    help_alias = {'email help': 'writing email' if mode == 'email_draft' else 'email',
                  'document help': 'documents', 'web help': 'web',
                  'radio help': 'radio', 'podcast help': 'podcasts',
                  'commands help': 'commands'}
    if help_topic and writing_dictation(mode, typed):
        # "Help with the dishes tonight" while writing is text unless it names a guide topic.
        try:
            probe = HelpSession(GUIDE, release_notes_path=GUIDE.parent/'Voice Companion Release Notes.txt')
            if probe.topic(help_topic[1]).startswith('I could not identify one help topic'): help_topic = None
        except (OSError, ValueError): pass
    if command in ('help', 'user guide', 'open user guide', 'manual', 'open manual') or help_topic:
        try:
            if mode != 'help' or help_session is None:
                help_return_mode = mode
                help_session = HelpSession(GUIDE, release_notes_path=GUIDE.parent/'Voice Companion Release Notes.txt')
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
            help_session = HelpSession(GUIDE, release_notes_path=GUIDE.parent/'Voice Companion Release Notes.txt')
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
        speak(media_navigation().run(command))
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
        if mail_session is None and provider in REMOVED_MAIL_PROVIDERS:
            # A draft from a removed Gmail or Microsoft connection: back to the chosen account.
            chosen = connected_selection()
            provider = chosen[0] if chosen else provider
        if mail_session is None and provider in MAIL_PROVIDERS:
            mail_session = MailSession(provider, APP, progress=mail_progress)
        if mail_session is not None:
            speak(mail_session.process('open inbox'))
            return 'mailbox'
        speak('No email account is connected.')
        return 'awake'
    if mode == 'email_draft' and email_draft is not None and email_field_destination(command):
        pending_send = None
        dictation_feedback(email_draft.process(text))
        focus_email_entry()
        return mode
    if (mode == 'mailbox' and mail_session is not None and command in FOLDER_LIST_COMMANDS and
            not (getattr(mail_session, 'folder_picker', None) or getattr(mail_session, 'folder_choice', None) or getattr(mail_session, 'pending', None))):
        # With several accounts the folder list starts with the account list.
        return handle_mailbox_screen(text, command, mode, typed)
    if mode == 'mailbox' and mail_session is not None and not re.match(MAILBOX_GLOBAL_GO_TO, command) and (
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
        speak(media_navigation().run(text))
        return 'media'
    if command == 'list favorites' and mode == 'media' and MEDIA_SECTION == 'radio':
        speak(media_navigation().run(text))
        return 'media'
    if mode=='media' and MEDIA_HUB and (media_navigation().form or media_navigation().pending) and spoken_control(command) is None and command not in ('main menu','back to main menu','leave radio','leave podcasts'):
        speak(media_navigation().command(text));return 'media'
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
            speak(media_navigation().enter(MEDIA_SECTION,keyboard=KEYBOARD_NAVIGATION))
            return 'media'
        try:
            speak(media_navigation().command(text));sync_app_context('media',True)
        except (OSError,ValueError,IndexError,ET.ParseError) as exc:
            speak('I could not complete that media request. ' + str(exc))
        return 'media'
    literal = re.fullmatch(r'(?:type|dictate) literally\s+(.+)', text, re.I | re.S)
    if command == 'type new line': literal_text = 'new line'
    else: literal_text = literal[1] if literal else None
    if literal_text is not None and mode == 'note':
        note_buffer.append(literal_text)
        note_selection.text = None
        dictation_feedback('Added: '+literal_text)
        return mode
    if literal_text is not None and mode == 'web' and web_session is not None:
        web_dictation_feedback(web_session.dictate_to_focus(literal_text,literal=True))
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
            if (email_draft.compose_step or 'body') == 'body': dictation_feedback(email_draft.process(text))
            else: speak_prompt('This email field is single-line. Say go to body to insert a new line in the message.')
            return mode
    if mode=='document' and command in ('leave document', 'close document', 'back to main menu',
               'go back', 'back', 'exit', 'exit document', 'exit document mode', 'close', 'main menu'):
        document.pending_spacing = False
        document.pending_spacing_candidate = None
        DOCUMENT_CLOSE_TARGET='awake' if command in ('main menu','back to main menu') else 'word_processor';CONFIRM_CHOICE='yes'
        speak('Save this document? Yes or no. Use arrows to choose, then Enter. Escape keeps editing.')
        return 'document_save'
    if mode=='document' and command in ('name document','rename document','save as','save document as'):
        DOCUMENT_NAME_RETURN='document';PENDING_DOCUMENT_NAME=None
        DOCUMENT_NAMING_ACTION='save as' if command.startswith('save') else 'name document'
        speak('Name this document. Say or type its name, then press Enter.');return 'document_name'
    if mode == 'document' and document is not None and document_control_request(text):
        result=document.process(text)
        if result.startswith(('Document named ','Saved document as ','Saved the Word document as ')):checkpoint_document()
        speak(result)
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
                dictation_feedback(email_draft.process(text))
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
    # While a send review is waiting, "yes" or "no" answers it; it is not message text.
    answering_send = mode == 'email_draft' and pending_send is not None and command in SEND_REVIEW_ANSWERS
    if INPUT_MODE == 'dictation' and not typed and not answering_send:
        if mode in ('email_draft', 'document'):
            editor = email_draft if mode == 'email_draft' else document
            if (mode == 'document' or mode == 'email_draft' and (email_draft.compose_step or 'body') == 'body') and (document_format_command(command) or editor.pending_spacing):
                speak(editor.process(text))
            elif mode == 'email_draft' and (email_draft.compose_step in ('subject','cc','bcc') or
                                          email_draft.compose_step == 'recipient' and
                                          (typed or '@' in text or ' at ' in text.lower())):
                dictation_feedback(email_draft.process(text))
                focus_email_entry()
            else:
                dictation_feedback(editor.append_text(text))
        elif mode == 'note':
            cleaned = prepare_dictation(text)
            if cleaned: note_buffer.append(cleaned)
            if cleaned:dictation_feedback('Added: '+cleaned)
        elif mode == 'web' and web_session:
            web_dictation_feedback(web_session.dictate_to_focus(text))
        else:
            speak_prompt('No text field is active. Say normal mode to choose a task.')
        return mode
    store = ProtectedStore(APP)
    import thunderbird_accounts
    if command in thunderbird_accounts.STOP_WAITING_COMMANDS:
        waiting = THUNDERBIRD_WATCH is not None
        stop_thunderbird_watch()
        speak(thunderbird_accounts.STOPPED_WAITING if waiting else thunderbird_accounts.NOT_WAITING)
        return mode
    direct_connection = re.fullmatch(
        r'(?:connect|add|set up|setup|sign in to|log in to)\s+(?:my\s+|an?\s+)?'
        r'(gmail|google mail|outlook|microsoft 365|office 365|exchange|hotmail|yahoo|thunderbird)(?:\s+(?:email|mail|account))?', command)
    if command in thunderbird_accounts.ADD_COMMANDS and (direct_connection or not writing_dictation(mode, typed)):
        account_setup = False
        return start_thunderbird_add(mode)
    if direct_connection and direct_connection[1] in THROUGH_THUNDERBIRD:
        # "Add Gmail account": Gmail and Microsoft mail are added in Thunderbird.
        account_setup = False
        speak(THROUGH_THUNDERBIRD[direct_connection[1]] + ' works through Thunderbird.')
        return start_thunderbird_add(mode)
    if direct_connection:
        account_setup = True
        command = direct_connection[1]
    if command in ('add account', 'add another account', 'add email account', 'connect email account', 'set up email'):
        account_setup = True
        speak(ACCOUNT_TYPE_PROMPT)
        return mode
    if account_setup:
        if command in ('cancel', 'cancel account setup'):
            account_setup = False
            speak('Account setup canceled.')
            return mode
        if command in THROUGH_THUNDERBIRD:
            account_setup = False
            speak(THROUGH_THUNDERBIRD[command] + ' works through Thunderbird.')
            return start_thunderbird_add(mode)
        provider = ('yahoo' if command in ('yahoo','yahoo mail') else
                    'thunderbird' if command in ('thunderbird','thunderbird mail','mozilla thunderbird') else None)
        if not provider:
            speak('Say Thunderbird, Yahoo, or cancel. Gmail, Outlook and Microsoft 365 work through Thunderbird.')
            return mode
        account_setup = False
        if provider == 'yahoo':
            ACCOUNT_ENTRY = {'provider': 'yahoo', 'step': 'address'}
            speak(YAHOO_SETUP_PROMPT)
            sync_app_context(mode, True)
            return mode
        if provider == 'thunderbird':
            # No sign-in here: find what Thunderbird still needs and say the next step.
            try:
                flow = new_thunderbird_setup()
                THUNDERBIRD_SETUP = {'flow': flow, 'question': None, 'message': ''}
                return thunderbird_setup_step(flow.start(), mode)
            except Exception:
                THUNDERBIRD_SETUP = None
                speak('Thunderbird setup stopped because of a problem. Ask your helper to check Email Setup Helper, section 3.')
                return mode
    if command in ('list email accounts', 'which email account', 'what email account', 'my email accounts'):
        accounts = store.accounts()
        selected = store.selected()
        if not accounts: speak_prompt('No email account is connected. Say add account.')
        else:
            # The kind comes first, so the same address through Thunderbird and directly are two items.
            import account_picker
            speak(open_named_list('Email accounts',[(account_picker.label((p,a),selected),'switch to '+account_picker.name(p).casefold()+' '+a) for p,a in accounts]))
        return mode
    # Whole commands only; while writing, these words are text.
    if command in ACCOUNT_SWITCH_COMMANDS + ACCOUNT_REMOVE_COMMANDS and not writing_dictation(mode, typed):
        return open_account_picker('remove' if command in ACCOUNT_REMOVE_COMMANDS else 'inbox', mode)
    contact_account = store.selected()
    # Thunderbird's address books and the contacts saved here, merged (contacts_book.py).
    from contacts_book import ContactBook, find as find_contacts
    contacts = ContactBook(APP, contact_account[1] if contact_account else '')
    contact = re.fullmatch(r'(?:add|save|create|update) contact (.+?) (?:as|at|email is) (.+)', text, re.I)
    if contact and writing_dictation(mode, typed):
        try: normalize_recipient(contact[2])
        except ValueError: contact = None
    if contact:
        try:
            where = contacts.add(contact[1], contact[2])
            address = normalize_recipient(contact[2])
            speak('Saved contact ' + contact[1] + ' at ' + address + (' in Thunderbird.' if where.startswith('in Thunderbird') else '.'))
        except (ValueError, OSError) as exc:
            speak(str(exc))
        return mode
    if command in ('list contacts','list address book','read address book'):
        entries = contacts.load()
        speak(open_named_list('Contacts',[(e.label(),'find contact '+e.name) for e in entries]) if entries else
              'Your address book is empty. Type add contact Bob as bob@example.com, then press Enter.')
        return mode
    contact = re.fullmatch(r'(?:find|read|look up) contact (.+)', text, re.I)
    if contact:
        found = find_contacts(contacts.cached(), contact[1])
        speak(found[0].label() if found else 'No contact with that name. Say list contacts.')
        return mode
    if command in ('connect google contacts','sync google contacts','refresh google contacts') and not writing_dictation(mode, typed):
        # Google contacts come through Thunderbird now, which syncs them itself.
        speak('Google contacts now come from Thunderbird. Add your Google account to Thunderbird, '
              'then say open contacts. Your contacts saved here are kept.')
        return mode
    match_account = re.fullmatch(r'(?:switch to|use|select) (.+)', command)
    if match_account and not ('@' in match_account[1] or ' at ' in match_account[1] or match_account[1] in
                              ('gmail','outlook','exchange','microsoft 365','yahoo','thunderbird')):
        match_account = None
    choice = match_account[1] if match_account else None
    account_kind = None
    if match_account and ('@' in choice or ' at ' in choice):
        # "Switch to Thunderbird, will at gmail dot com" or "... will@gmail.com through Thunderbird".
        from account_picker import spoken_account
        account_kind, spoken_address = spoken_account(choice)
        try: choice = normalize_recipient(spoken_address)
        except ValueError:
            # "Use the side door at noon." while writing is text, not an account name.
            if not writing_dictation(mode, typed):
                speak_prompt('I did not understand that email address. Say list email accounts and try again.')
                return mode
            match_account = None
    if match_account:
        accounts = store.accounts()
        if '@' in choice:
            matches = [(p,a) for p,a in accounts if a.casefold() == choice.casefold() and account_kind in (None, p)]
        elif account_picker_words().company_word(choice):
            # "Switch to Gmail": the Thunderbird account at Gmail.
            matches = account_picker_words().company_accounts(choice, accounts)
            if not matches:
                speak('No ' + account_picker_words().company_name(choice) + ' address is connected. '
                      + account_picker_words().company_name(choice) + ' works through Thunderbird. Say add account to set it up.')
                return mode
        else:
            matches = [(p,a) for p,a in accounts if p == choice]
        if len(matches) != 1:
            from account_picker import label as account_label
            speak(('That address is connected ' + str(len(matches)) + ' ways. Say ' +
                   ', or '.join('switch to ' + account_label(m) for m in matches) + '.' if '@' in choice else
                   'I cannot identify one connected account. Say list email accounts, then switch to its full email address.') if matches else
                  'That account is not connected. Say add account or list email accounts.')
            return mode
        return switch_mail_account(*matches[0], mode)
    # Everyday words for the inbox, as a new user says them ("Open the inbox.", "Check my mail.").
    # In a document, email or note they are text you are writing. In the inbox and on a web
    # page they keep going to the mailbox or the page (a webmail page has its own Inbox).
    if command in ('email', 'my email', 'open email') or mode not in ('mailbox', 'web') and not writing_dictation(mode, typed) and command in (
            'open my email', 'go to email', 'go to my email', 'inbox', 'my inbox', 'open inbox', 'open the inbox',
            'open my inbox', 'go to inbox', 'go to the inbox', 'go to my inbox', 'check my email', 'check mail',
            'check my mail', 'read email', 'read mail', 'read my mail', 'open mail', 'open my mail'):
        selected = store.selected()
        if not selected:
            speak_prompt('No email account is selected. Say add account, or list email accounts.')
            return mode
        mail_session = MailSession(selected[0], APP, progress=mail_progress)
        speak(mail_session.process('open inbox'))
        return 'mailbox'
    sender_recipient=None
    if command=='new email' and EMAIL_LINK_RECIPIENT:
        sender_recipient,EMAIL_LINK_RECIPIENT=EMAIL_LINK_RECIPIENT,None
    if mode=='mailbox' and command in ('copy sender address','copy sender email address','copy the sender address','email sender','email the sender','new email to sender','write an email to sender','compose email to sender'):
        from mail_voice import sender_address
        address=sender_address(mail_session.rows[mail_session.current-1]) if mail_session and mail_session.rows and mail_session.current else ''
        if not address:speak('No sender address is available for the current message.');return mode
        if command.startswith('copy'):
            from document_editor import copy_to_clipboard
            copy_to_clipboard(address)
            from document_editor import CLIPBOARD_WRITE_OK
            speak('Sender address copied.' if CLIPBOARD_WRITE_OK else 'The sender address could not be copied to the Windows clipboard. Try again.');return mode
        sender_recipient=address;command='new email'
    email_request = (command in ('write an email', 'write email', 'compose email', 'new email', 'create email',
                                 'write a message', 'compose a message', 'new message', 'send a message',
                                 'write a new message', 'compose a new message', 'write mail')
                     or bool(re.fullmatch(r'(?:write|compose|create|start|draft)(?:\s+(?:a|an|new|another|write))*\s+(?:e[ -]?mail|email|message)(?:\s+draft)?', command)))
    if mode == 'awake' and command in ('where am i', 'where am i now'):
        # The place, the item you are on, and whether anything is listening.
        speak('Main menu. '+(announce_item(MAIN_MENU_CHOICES[MAIN_MENU_INDEX][0],MAIN_MENU_INDEX,len(MAIN_MENU_CHOICES))+' ' if MAIN_MENU_INDEX is not None else '')
              +__import__('startup_speech').MICROPHONE[microphone_state()])
        return mode
    if mode == 'awake' and command in ('next', 'previous'):
        MAIN_MENU_INDEX = bounded_index(MAIN_MENU_INDEX,1 if command=='next' else -1,len(MAIN_MENU_CHOICES))
        speak(announce_item(MAIN_MENU_CHOICES[MAIN_MENU_INDEX][0],MAIN_MENU_INDEX,len(MAIN_MENU_CHOICES)))
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
            email_draft.compose_step = 'subject' if sender_recipient else 'recipient'
            if sender_recipient:email_draft.recipient=sender_recipient
            email_draft.dictating = INPUT_MODE != 'commands'
            email_draft.automatic_dictation = INPUT_MODE != 'commands'
            if selected: email_draft.provider = selected[0]
            email_draft.save()
            if typed or KEYBOARD_NAVIGATION:
                speak('New email. '+('Subject field.' if sender_recipient else 'To field.'))
            else:
                speak('New email. '+('New message to the selected sender. Subject field.' if sender_recipient else 'To field. Who do you want to send the email to? Type an address, or say a saved contact name.'))
            focus_email_entry()
            return 'email_draft'
        if document_request:
            document = VoiceDocument(documents_folder())
            checkpoint_document()
            apply_document_defaults(document)
            document.dictating = INPUT_MODE != 'commands'
            speak_prompt('New Word document. Speak to write. Say name document followed by a title, or ask for document help.')
            return 'document'
        note_buffer.clear()
        note_selection.text = None
        OPEN_NOTE = None
        speak_prompt('Dictation started. Speak naturally. Say save note when finished. Say list notes to open a saved note.')
        return 'note'
    if pending_website is not None:
        address, host = pending_website
        pending_website = None
        if command in ('yes', 'yes please', 'yes open website', 'yes open it', 'confirm website'):
            web_session = web_session or WebSession(APP,progress=speak_status)
            web_session.auto_read=PREFERENCES.get('web_auto_read',True)
            web_session.search_engine=PREFERENCES.get('web_search_engine','google')
            revision=getattr(web_session,'page_revision',0)
            result = web_session.open(address)
            speak_web_result(result,revision)
            return 'web' if web_session.snapshot else mode
        else:
            speak_prompt('Website request canceled. Say open website followed by the address to try again.')
        return mode
    if mode == 'web':
        if command in ('new favorite','add favorite','go to favorite','delete favorite','rename favorite','new bookmark','add bookmark','go to bookmark','delete bookmark','rename bookmark','web history','history','list bookmarks'):
            web_session.menu.start()
            return handle_browser_menu(text)
        web_session.auto_read=PREFERENCES.get('web_auto_read',True)
        web_session.search_engine=PREFERENCES.get('web_search_engine','google')
        if command in ('search','search web','search the web','web search','new search','web browser','open web browser') or text=='web key Search':
            SEARCH_RETURN_MODE='web'
            speak('Web address or search. Type an address or search and press Enter. Addresses do not need www or https. You can say a search. Escape cancels.')
            return 'search'
        revision=getattr(web_session,'page_revision',0)
        if text.startswith('web key '):
            result=web_session.keyboard(text[8:],direct_submit=True) if KEYBOARD_NAVIGATION and text[8:] in ('Activate','Space') else web_session.keyboard(text[8:])
            speak_web_result(result,revision);return mode
        web_session.input_mode = INPUT_MODE
        if command in ('leave website', 'close website','leave web browser', 'main menu', 'back to main menu'):
            try:web_session.leave(PREFERENCES.get('web_keep_open',False))
            except Exception:speak('The browser window could not close. Try closing it again.')
            announce_main_menu()
            return 'awake'
        if command.startswith(('open website ', 'go to website ')):
            try:
                pending_website = prepare_address(text.split(' ', 2)[2])
                speak('I heard website ' + pending_website[1] + '. Is that right? Say yes or no.')
            except ValueError as exc:
                speak(str(exc))
            return 'web'
        speak_web_result(web_session.command(text),revision)
        return 'web'
    # Saved drafts open from the inbox too (Control+O), not only from the main menu.
    if mode == 'mailbox' and not (command in EMAIL_DRAFT_COMMANDS or command.startswith('open email draft ')):
        return handle_mailbox_screen(text, command, mode, typed)
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
                if provider in REMOVED_MAIL_PROVIDERS:
                    # A draft started with a removed Gmail or Microsoft connection goes
                    # from the chosen account; the review below says which one. The
                    # "already attempted" check above used its old fingerprint first.
                    chosen = ProtectedStore(APP).selected()
                    if not chosen:
                        raise AccountError('No email account is connected. Say add account to connect one in Thunderbird.')
                    email_draft.move_to_account(chosen[0])
                    provider = email_draft.provider
                    recipient, subject, body = message_parts(email_draft)
                if provider not in MAIL_PROVIDERS:
                    raise AccountError('Choose a connected email account first. The draft has not been sent.')
                address = ProtectedStore(APP).load(provider)['address']
                if email_draft.response_context and email_draft.response_context.get('source_address') not in (None, address):
                    raise AccountError('This response belongs to a different mailbox.')
                pending_send = (email_draft.send_hash(), provider, address)
                copied = (('. CC ' + ', '.join(email_draft.cc) if email_draft.cc else '') +
                          ('. BCC ' + ', '.join(email_draft.bcc) if email_draft.bcc else ''))
                context = email_draft.response_context
                # One period after each part, never two: a subject or message that ends in
                # its own period, question mark or exclamation keeps it ("Sounds good." not "Sounds good..").
                sentence = lambda text: text if text.rstrip().endswith(('.', '?', '!')) else text.rstrip() + '.'
                source_note = (' This ' + context['action'].replace('_', ' ') +
                               ' uses the original message in your mailbox.' +
                               (' Original attachments may not be included.'
                                if context['action'] == 'forward' and not isinstance(context.get('original_attachments'), list) else '')
                               if context else '')
                from mail_attachments import count_words, limit_warning, missing, missing_message, names, total_size
                files = email_draft.all_attachments()
                gone = missing(files)
                if gone:
                    pending_send = None
                    speak(missing_message(gone) + ' The draft is saved.')
                    return mode
                attached = (' With ' + count_words(len(files)) + ': ' + names(files) + '.' if files else '')
                import account_picker
                speak('Review before sending. From ' + account_picker.label((provider, address)) + '. To ' + recipient +
                      copied + '. ' + sentence('Subject ' + (subject or 'no subject')) + attached + ' ' +
                      sentence('Message: ' + (body or 'no message body')) + source_note +
                      limit_warning(total_size(files)) +
                      ' Is that right? Press Y or N, or use arrows or Tab and Enter. Escape keeps the draft. You can also say yes to send or no to keep it.')
            except (AccountError, DeliveryError, ValueError, KeyError) as exc:
                reason=str(exc) if not isinstance(exc,KeyError) else 'The saved account information is incomplete. Reconnect the sending account.'
                speak(reason+' The draft is saved locally. It has not been sent.')
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
                try:
                    email_draft.save()
                except OSError:
                    # Nothing reached the mail service, so a retry is safe.
                    email_draft.last_attempt_hash = ''
                    speak('The draft could not be saved, so the email was not sent. Check that the disk has space and try again.')
                    return mode
                accepted = submit(provider, email_draft, token, sender=address)
                email_draft.last_accepted_hash = fingerprint
                email_draft.save()
            except (AccountError, DeliveryError, ValueError, OSError, KeyError) as exc:
                uncertain=email_draft.last_attempt_hash==fingerprint and (not isinstance(exc,DeliveryError) or exc.uncertain)
                if not uncertain:
                    email_draft.last_attempt_hash='';email_draft.save()
                reason=str(exc) if isinstance(exc,(AccountError,DeliveryError,ValueError)) else 'The email connection could not complete the request.'
                if getattr(exc,'sign_in_url',None):
                    # Thunderbird needs a web sign-in to send: open it where the user can use it.
                    THUNDERBIRD_SIGN_IN=exc.sign_in_url
                    speak_status(reason+' The draft is saved locally.')
                    return open_thunderbird_sign_in(mode,announce=False)
                speak(reason+' The draft is saved locally.'+(' Check Sent Mail before trying again; do not send a duplicate.' if uncertain else ' It has not been sent. Correct the problem and review the draft again.'))
                return mode
            speak_status('Email sent successfully.' + delivery_note(accepted))
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
                    speak('Back to the original message. ' + mail_session._next_body())
                else:
                    # The list already starts with the folder name; say it once.
                    speak('Back to ' + (result if result.startswith(mail_session.folder_name + '. ') else
                                        mail_session.folder_name + '. ' + result))
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
            try:
                email_draft.save()
            except OSError:
                speak(UNSAVED_DRAFT)
                return mode
            if mail_session is not None and command not in ('back to main menu', 'main menu'):
                speak('Email draft saved. ' + mail_session.process('back to folder'))
                return 'mailbox'
            speak('Email draft saved locally. Check whether it was sent before sending it again.')
            return 'awake'
        try:
            dictation_feedback(email_draft.process(text))
        except OSError:
            # The change is made; only the save failed (a full disk, or the file in use).
            speak(UNSAVED_DRAFT)
        focus_email_entry()
        return 'email_draft'
    if mode == 'document':
        if INPUT_MODE == 'commands' and command in ('start dictation', 'dictate', 'continue writing'):
            speak_prompt('Commands only is on. Say normal mode or dictation mode to write.')
            return mode
        try:
            result = document.process(text)
            if INPUT_MODE == 'mixed' and result.startswith('I did not recognize that document request.'):
                document.dictating = True
                result = document.append_text(text)
        except OSError:
            # The words are in the document; only the save failed. Word may have the file open.
            speak(UNSAVED_DOCUMENT)
            return 'document'
        if result.startswith(('Document named ','Saved document as ','Saved the Word document as ')):checkpoint_document()
        dictation_feedback(result)
        return 'document'
    if mode == 'note':
        request = reading_request(command)
        if request:
            note_reading.set_text(' '.join(note_buffer))
            unit, direction = request
            speak(note_reading.read(direction == 'top') if unit == 'read' else note_reading.move(unit, direction))
            return mode
        if command in NOTE_LIST_COMMANDS:
            # Save the note in progress first, so switching notes never loses words.
            if flush_note(mode):
                OPEN_NOTE = None
                list_saved_notes()
            return mode
        number = NOTE_NUMBER.fullmatch(command)
        if number:
            if not flush_note(mode):return mode
            OPEN_NOTE = None
            return open_saved_note(number[1], mode)
        if command in ('go back', 'back', 'exit', 'exit note', 'main menu', 'back to main menu'):
            # One message: what happened to the note, then where you are. A second
            # message would cut off the first; an empty note was never "saved".
            said = []
            had_words = bool(note_buffer)
            if not flush_note(mode, said.append):
                speak(' '.join(said));return mode
            lead = (' '.join(said) or 'Note saved.') if had_words else ('The note was empty, so nothing was saved.' if OPEN_NOTE is None else
                                                    'The note is empty, so your saved note was not changed.')
            OPEN_NOTE = None
            if command in ('main menu', 'back to main menu'):
                MAIN_MENU_LEAD = lead;return 'awake'
            # Escape and Go back return to the Notepad menu, as a document returns to Word processor.
            return task_navigation().enter('notepad_menu', lead + ' Notepad menu')
        if command in ('save note', 'finish note', 'done dictating'):
            if note_buffer:
                if save_note(' '.join(note_buffer)) is False:return mode
                note_buffer.clear()
                note_selection.text = None
            else:
                OPEN_NOTE = None
                speak('The note was empty.')
            return 'awake'
        if INPUT_MODE == 'commands':
            speak_prompt('Commands only is on. Say normal mode or dictation mode to add to the note.')
            return mode
        cleaned = prepare_dictation(text)
        if cleaned:
            note_buffer.append(cleaned)
            dictation_feedback('Added: '+cleaned)
        return 'note'
    if command in NOTE_LIST_ELSEWHERE:
        list_saved_notes()
        return mode
    if NOTE_NUMBER.fullmatch(command):
        return open_saved_note(NOTE_NUMBER.fullmatch(command)[1], mode)
    if command in ('list documents', 'what documents do i have', 'my documents'):
        folder = documents_folder()
        names = sorted((p.stem for p in folder.glob('*.docx')), key=str.casefold) if folder.exists() else []
        speak(open_named_list('Documents',[(name,'open document '+name) for name in names]) if names else 'You have no saved documents yet.')
        return mode
    if command.startswith('open document '):
        title = text[len('open document '):].strip()
        try:
            document = VoiceDocument.open_existing(documents_folder(), title)
            checkpoint_document()
        except FileNotFoundError:
            speak_prompt('I could not find that document. Say list documents to hear the names.')
            return mode
        except PermissionError as error:
            from document_reader import unreadable
            speak(unreadable(title, error))
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
            words = account_picker_words()
            kind = 'gmail' if 'gmail' in command else ('yahoo' if 'yahoo' in command else 'outlook')
            accounts = connected_accounts(store)
            # Gmail and Outlook addresses are Thunderbird accounts now.
            matches = ([(p,a) for p,a in accounts if p == 'yahoo'] if kind == 'yahoo' else
                       words.company_accounts(kind, accounts))
            if len(matches) != 1:
                speak('Say list email accounts and switch to the full email address.' if matches else
                      'That account is not connected. Say add account.' if kind == 'yahoo' else
                      'No ' + words.company_name(kind) + ' address is connected. ' + words.company_name(kind) +
                      ' works through Thunderbird. Say add account to set it up.')
                return mode
            provider = store.select(*matches[0])[0]
        mail_session = MailSession(provider, APP, progress=mail_progress)
        speak(mail_session.process('open inbox'))
        return 'mailbox'
    if command in EMAIL_DRAFT_COMMANDS[:3]:
        folder = APP / 'Email Drafts'
        names = sorted((p.stem for p in folder.glob('*.json')), key=str.casefold) if folder.exists() else []
        speak('Your email drafts are: ' + ', '.join(names[:20]) + ('. There are more drafts.' if len(names) > 20 else '.')
              if names else 'You have no saved email drafts yet.')
        return mode
    if command in EMAIL_DRAFT_COMMANDS[3:]:
        # A list to choose from with arrows and Enter (Control+O in email), or by voice.
        folder = APP / 'Email Drafts'
        names = sorted((p.stem for p in folder.glob('*.json')), key=str.casefold) if folder.exists() else []
        speak(open_named_list('Email drafts', [(name, 'open email draft ' + name) for name in names]) if names
              else 'You have no saved email drafts yet.')
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
        if mode=='search' and command in ('go back','back','exit'):
            if SEARCH_RETURN_MODE=='browser_menu':return open_browser_menu()
            return SEARCH_RETURN_MODE
        return 'awake'
    if mode == 'podcast':
        speak(media().search_podcasts(text))
        return 'media'
    if mode == 'search':
        return run_web_search(text)
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
        checkpoint_document()
        apply_document_defaults(document)
        document.dictating = INPUT_MODE != 'commands'
        speak_prompt('New Word document. Speak to write. Say name document followed by a title, or ask for document help.')
        return 'document'
    if re.search(r'\b(note|dictate)\b', command):
        note_buffer.clear()
        note_selection.text = None
        OPEN_NOTE = None
        speak_prompt('Dictation started. Speak naturally. Say save note when finished. Say list notes to open a saved note.')
        return 'note'
    provider_websites = {'gmail': 'https://mail.google.com/',
                         'outlook': 'https://outlook.live.com/mail/',
                         'yahoo mail': 'https://mail.yahoo.com/'}
    if command in ('open gmail', 'open outlook', 'open outlook mail', 'open yahoo', 'open yahoo mail'):
        provider = command.removeprefix('open ').removesuffix(' mail')
        webbrowser.open(provider_websites['yahoo mail' if provider == 'yahoo' else provider])
        speak('Opened ' + provider + ' in your usual browser. Voice Companion is not connected to this account.')
        return 'awake'
    if re.fullmatch(__import__('web_assistant').BROWSER_REQUEST, command) or command in ('which browser','what browser am i using'):
        web_session = web_session or WebSession(APP,progress=speak_status)
        web_session.auto_read=PREFERENCES.get('web_auto_read',True)
        web_session.search_engine=PREFERENCES.get('web_search_engine','google')
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
        web_session = web_session or WebSession(APP,progress=speak_status)
        web_session.auto_read=PREFERENCES.get('web_auto_read',True)
        web_session.search_engine=PREFERENCES.get('web_search_engine','google')
        speak(web_session.command(text))
        return 'web' if web_session.snapshot else 'awake'
    if 'email' in command or 'inbox' in command or re.search(r'\bmail\b', command):
        speak_prompt('Say add account to connect mail, list email accounts to hear connections, or email to open the selected inbox. Say write an email to create a draft.')
        return 'awake'
    if command in ('radio','internet radio'):
        MEDIA_SECTION = 'radio'
        speak_prompt(media_navigation().enter(MEDIA_SECTION,keyboard=KEYBOARD_NAVIGATION))
        return 'media'
    if command in ('podcasts','listen to podcasts'):
        MEDIA_SECTION = 'podcast'
        media().awaiting_radio_search = False
        speak_prompt(media_navigation().enter(MEDIA_SECTION,keyboard=KEYBOARD_NAVIGATION))
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
        try: speak(media_navigation().command(text))
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
    if command in ('web browser','open web browser','web browsing','open web browsing'):
        return open_browser_menu()
    direct_engine=re.match(r'^(google|bing|brave(?: search)?|duck\s*duck\s*go)(?: for)?\s+(.+)',text,re.I)
    if direct_engine:
        web_session=web_session or WebSession(APP,progress=speak_status)
        web_session.auto_read=PREFERENCES.get('web_auto_read',True)
        web_session.search_engine=PREFERENCES.get('web_search_engine','google')
        revision=getattr(web_session,'page_revision',0)
        speak_web_result(web_session.command(text),revision)
        return 'web' if web_session.snapshot else 'awake'
    if re.search(r'\b(search|look up|google|bing|find|web)\b', command):
        topic = re.sub(r'^.*?\b(?:search(?: the web)?(?: for)?|look up|google|bing|find(?: me)?|web(?: for)?)\b\s*', '', text)
        if topic:
            web_session = web_session or WebSession(APP,progress=speak_status)
            web_session.auto_read=PREFERENCES.get('web_auto_read',True)
            web_session.search_engine=PREFERENCES.get('web_search_engine','google')
            revision=getattr(web_session,'page_revision',0)
            speak_web_result(web_session.search(topic, provider='google' if command.startswith('google ') else 'bing' if command.startswith('bing ') else None),revision)
            return 'web' if web_session.snapshot else 'awake'
        SEARCH_RETURN_MODE='awake'
        speak('Web address or search. Type an address or search and press Enter. Addresses do not need www or https. You can say a search. Escape cancels.')
        return 'search'
    if mode in ('word_processor','notepad_menu','reader_menu','media_menu','local_media','recent_documents','reader_recent'):
        # Nothing above understood the words. A misheard word must not throw you out of
        # this menu (or stop the file playing): stay, and say where you are.
        if command in ('main menu','back to main menu','go to main menu'):return 'awake'   # handle() says Main menu
        speak('I did not understand that. You are on the '+options_return_notice(mode)[len('Back to '):].rstrip('.')+'. Press F1 for help.')
        return mode
    speak_prompt('I can write a note or document, open email, search the web, or find radio and podcasts. Say add account to connect email. Say commands only, dictation only, or normal mode to choose how speech works.')
    return 'awake'


UPDATES = None
UPDATE_RETURN_MODE = 'awake'
UPDATE_OFFER = False
UPDATE_MANUAL = False
UPDATE_LAST_PERCENT = 0
STARTUP_UPDATE_PENDING = False


def return_from_update(notice=''):
    """Leave the update question or download. notice: what happened, said in the same message
    as what comes next, so neither cuts the other off."""
    global STARTUP_UPDATE_PENDING, SLEEP_RETURN_MODE, MAIN_MENU_LEAD
    import startup_speech
    notice = str(notice or '').strip()
    if STARTUP_UPDATE_PENDING:
        STARTUP_UPDATE_PENDING = False
        state = microphone_state()
        if state == 'on':
            speak((notice + ' ' if notice else '') + startup_speech.followup('on'))
            return 'sleep'
        # Nobody can say "wake up" with the microphone off: go to the main menu.
        SLEEP_RETURN_MODE = 'awake'
        MAIN_MENU_LEAD = (notice + ' ' if notice else '') + startup_speech.microphone_text(state)
        # handle() says Main menu when the screen changes; from the main loop, say it here.
        if _HANDLE_DEPTH == 0:announce_main_menu()
        return 'awake'
    if notice:
        if UPDATE_RETURN_MODE == 'awake':
            MAIN_MENU_LEAD = notice
            if _HANDLE_DEPTH == 0:announce_main_menu()
        else:speak(notice)
    return UPDATE_RETURN_MODE


def update_offer_question():
    """The update question. "Say yes or no" only when the microphone can hear it."""
    return ('A new update is available. Would you like to install it now? Press Y or N, or use arrows or Tab and Enter. Escape cancels.'
            + (' You can also say yes or no.' if microphone_state() == 'on' else ''))


def startup_update_mode():
    """Check for an update before the startup announcement. Says nothing: an offer is kept in
    STARTUP_QUESTION and asked at the end of the startup announcement."""
    global STARTUP_UPDATE_PENDING, UPDATE_RETURN_MODE, CONFIRM_CHOICE, STARTUP_QUESTION
    STARTUP_UPDATE_PENDING = False
    STARTUP_QUESTION = None
    if '--resume-after-update' in sys.argv or (APP/'update-result.json').is_file():
        UPDATE_RETURN_MODE='sleep'
        return 'sleep'
    if PREFERENCES.get('check_updates',True) and UPDATES.check():
        try:
            event, payload = UPDATES.events.get(timeout=10)
        except queue.Empty:
            event, payload = 'check_failed', False
        if event == 'available':
            UPDATES.release = payload
            STARTUP_UPDATE_PENDING = True
            UPDATE_RETURN_MODE = 'sleep'
            CONFIRM_CHOICE='yes'
            STARTUP_QUESTION = update_offer_question()
            return 'update_offer'
    return 'sleep'


def collect_speech(said):
    """While a startup step runs, keep what it says (on this thread) for the startup announcement."""
    import contextlib
    @contextlib.contextmanager
    def collecting():
        module = sys.modules[__name__]
        original = module.speak
        owner = threading.get_ident()
        def keep(text, *args, **kwargs):
            if threading.get_ident() == owner:said.append(str(text))
            else:original(text, *args, **kwargs)
        module.speak = keep
        try:yield said
        finally:
            if module.speak is keep:module.speak = original
    return collecting()


def announce_startup():
    """Choose the first screen and say the ONE startup announcement (startup_speech.py).

    Every startup path comes here: normal, microphone off or broken, after an update,
    an update offered, Chrome setup asked for by the installer. Returns the first mode."""
    global SLEEP_RETURN_MODE, STARTUP_NOTICES, STARTUP_QUESTION
    import startup_speech
    if STARTUP_NOTICES is None:STARTUP_NOTICES = []
    mode = startup_update_mode()
    question = STARTUP_QUESTION
    if mode == 'sleep':
        said = []
        with collect_speech(said):
            mode = __import__('chrome_setup').startup_request(sys.modules[__name__], mode)
        if said:question = ' '.join(said)
    if mode == 'sleep' and microphone_state() != 'on':
        # Nobody can say "wake up" with the microphone off: start on the main menu.
        mode = 'awake';SLEEP_RETURN_MODE = 'awake'
    update_result = None
    result_file = APP / 'update-result.json'
    if result_file.exists():
        try:update_result = startup_speech.update_result(json.loads(result_file.read_text(encoding='utf-8-sig')))
        except (OSError, ValueError):update_result = None
        try:result_file.unlink(missing_ok=True)
        except OSError:pass
    # Gmail and Microsoft accounts connected directly by older versions are removed, said once.
    retire_old_mail_accounts()
    apply_transcript()
    if PREFERENCES.get('speech_transcript', False):
        STARTUP_NOTICES.append('The speech transcript is on. Say stop transcript to turn it off.')
    notices, STARTUP_NOTICES = STARTUP_NOTICES, None
    STARTUP_QUESTION = None
    speak(startup_speech.announcement(APP_VERSION, microphone_state(), update_result, notices, question))
    return mode


_HANDLE_DEPTH = 0

def handle(text, mode, typed=False):
    global _HANDLE_DEPTH, CONFIRM_CHOICE, KEYBOARD_NAVIGATION
    if _HANDLE_DEPTH==0:TRANSCRIPT.mode=SLEEP_RETURN_MODE+', asleep' if mode=='sleep' else mode
    if _HANDLE_DEPTH==0 and not typed:user_stop.begin_command()
    if not typed and _HANDLE_DEPTH==0 and APP_WINDOW is not None and not APP_WINDOW.commands.empty():
        mode=process_keyboard_batch(mode)
        if mode=='exit':return mode
    previous_navigation=KEYBOARD_NAVIGATION
    quick_read=mode in ('document','email_draft','mailbox','help','web','note','file_reader') and reading_request(text) is not None and not (mode in ('document','email_draft') and INPUT_MODE=='dictation')
    help_navigation=(mode=='help' and HelpSession.accepts(text)) or normalized_command(text) in ('help','user guide','open user guide','manual','open manual','document help','email help','web help','radio help','podcast help','commands help','release notes') or normalized_command(text).startswith(('help with ','help for '))
    if help_navigation and not speech_control(text):
        interrupt_speech()
        KEYBOARD_NAVIGATION=True
    if quick_read or navigation_feedback_control(text,mode):
        if not help_navigation:interrupt_speech()
        KEYBOARD_NAVIGATION=True
    old_mail_pending=getattr(mail_session,"pending",None) if mail_session else None
    old_send=pending_send
    _HANDLE_DEPTH += 1
    try:
        if _HANDLE_DEPTH==1:
            try:flush_keyboard_edits(True)
            except OSError:speak('Your typed changes could not be saved yet. The file may be open in another program. Close it and they will be saved.')
        with dictation_scope(PREFERENCES.get('dictation_type','old school'), typed=typed):
            result = _handle(text, mode, typed)
        if (mail_session and not old_mail_pending and getattr(mail_session,"pending",None)) or old_send is None and pending_send is not None:CONFIRM_CHOICE="yes"
    finally:
        _HANDLE_DEPTH -= 1
        KEYBOARD_NAVIGATION=previous_navigation
    url=getattr(mail_session,'link_request',None) if _HANDLE_DEPTH==0 else None
    if isinstance(url,str) and url:
        # A link or button chosen in an email (any key or voice path) opens here.
        mail_session.link_request=None
        result=open_email_link(url)
    if result == 'awake' and mode not in ('awake', 'sleep') and _HANDLE_DEPTH == 0:
        if MEDIA_HUB and not PREFERENCES.get('keep_audio_playing',False):MEDIA_HUB.audio_command('stop audio')
        announce_main_menu()
    if result!=mode:DICTATION_ECHO.clear()
    if _HANDLE_DEPTH==0 and result!='exit':sync_app_context(result,result!=mode)
    return result


def poll_app_updates(mode):
    global UPDATE_OFFER, UPDATE_RETURN_MODE, UPDATE_LAST_PERCENT, CONFIRM_CHOICE, UPDATE_MANUAL
    if UPDATES is None: return mode
    while not UPDATES.events.empty():
        event, payload = UPDATES.events.get_nowait()
        if event == 'available':
            UPDATES.release = payload
            UPDATE_OFFER = True
        elif event == 'current' and payload:
            UPDATE_MANUAL = False
            speak('Voice Companion is up to date.')
        elif event == 'unconfigured':
            UPDATE_MANUAL = False
            speak('The update server is not configured yet. Ask your trainer to set it up.')
        elif event == 'check_failed' and payload:
            UPDATE_MANUAL = False
            speak('I could not check for updates. You can keep using the app and try later.')
        elif event in ('download_failed', 'canceled'):
            if mode == 'update_download':
                mode = return_from_update('Update canceled.' if event == 'canceled' else 'The update could not be downloaded or verified. Your current app remains installed.')
        elif event == 'progress' and mode == 'update_download':
            percentage = min(100, payload // 5 * 5)
            if percentage > UPDATE_LAST_PERCENT:
                for step in range(UPDATE_LAST_PERCENT+5,percentage+1,5):speak(str(step)+' percent.')
                UPDATE_LAST_PERCENT = percentage
        elif event == 'downloaded' and mode == 'update_download' and not UPDATES.canceled.is_set():
            path, release = payload
            try:
                speak('Installing the update. Voice Companion will close and reopen when installation finishes.')
                finish_mail_announcement()
                if not UPDATES.begin_install(path, release):raise RuntimeError('Updater busy')
            except Exception:
                mode = return_from_update('The update could not start. Your current app remains available.')
        elif event == 'install_ready' and mode=='update_download':
            if web_session:
                try:web_session.leave(False)
                except Exception:pass
            if MEDIA_HUB is not None:
                try:MEDIA_HUB.player.close()
                except Exception:pass
            return 'exit'
        elif event == 'install_failed' and mode=='update_download':
            mode=return_from_update('The update could not start. Your current app remains available.')
    if UPDATE_OFFER and (mode in ('sleep', 'awake') or UPDATE_MANUAL) and not speech_busy():
        UPDATE_RETURN_MODE = 'awake' if mode == 'sleep' else mode
        UPDATE_OFFER = False
        # The manual check has its answer; later automatic offers wait for the main screen.
        UPDATE_MANUAL = False
        CONFIRM_CHOICE='yes'
        speak(update_offer_question())
        return 'update_offer'
    return mode


def main():
    global AI_SPEECH, UPDATES, SETTINGS_PANEL, SLEEP_RETURN_MODE, MICROPHONE_ANNOUNCED, MICROPHONE_OFF, STARTUP_NOTICES, MICROPHONE_FAILED
    if '--check-browser-bookmarks' in sys.argv:
        from packaged_browser_check import check
        return check(sys.modules[__name__])
    if '--check-update-environment' in sys.argv:
        from app_updates import check_update_environment
        return check_update_environment()
    if '--refresh-guides' in sys.argv:
        try:
            from guide_files import publish_guides
            target=publish_guides(documents_folder(), expected_version=APP_VERSION)
            print('Documentation copied and verified for Voice Companion '+APP_VERSION+': '+str(target),flush=True)
            return 0
        except (OSError, ValueError) as exc:
            APP.mkdir(parents=True,exist_ok=True)
            (APP/'user-guide-update-error.txt').write_text(str(exc),encoding='utf-8')
            print('Documentation refresh failed: '+str(exc),file=sys.stderr,flush=True)
            return 3
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
            import sounddevice
            from playwright.sync_api import sync_playwright
            import web_assistant
            import app_window
            import pypdfium2
            import markdown_it
            from offline_ocr import engine
            engine()
            runtime = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'browser-runtime'
            if getattr(sys, 'frozen', False) and not any(runtime.glob('firefox-*/firefox/firefox.exe')):
                raise RuntimeError('The bundled Firefox browser is missing.')
            if not MODEL.joinpath('am', 'final.mdl').is_file():
                raise RuntimeError('The bundled offline wake model is missing.')
            if not GUIDE.is_file():
                raise RuntimeError('The onboard user guide is missing.')
            from guide_files import guide_sources
            guide_sources(expected_version=APP_VERSION)
            update_root = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
            if getattr(sys, 'frozen', False):
                for name in ('VoiceCompanion-WebView2.exe', 'Microsoft.Web.WebView2.Core.dll',
                             'Microsoft.Web.WebView2.WinForms.dll', 'WebView2Loader.dll', 'LICENSE.txt'):
                    if not (update_root / 'webview2' / name).is_file():
                        raise RuntimeError('An embedded Edge component is missing: ' + name)
            if not all((update_root / name).is_file() for name in ('update-settings.json','apply-update.ps1')):
                raise RuntimeError('The updater components are missing.')
            print('Packaged Python, email, audio, browser, and private-entry components are available.', flush=True)
            return 0
        except Exception as exc:
            print('A packaged component is missing or broken: ' + str(exc), file=sys.stderr, flush=True)
            return 3
    if '--connect-yahoo' in sys.argv:
        provider = 'yahoo'
        try:
            address = connect(provider, APP)
            speak(provider.capitalize() + ' account ' + address + ' connected. You may close this window.')
            return 0
        except (AccountError, OSError, ValueError, KeyError) as exc:
            speak('Account connection did not finish. Ask your helper to check the registration and try again.')
            return 3
    if '--disconnect-yahoo' in sys.argv:
        provider = 'yahoo'
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
    # Notices from here on wait for the one startup announcement (announce_startup).
    STARTUP_NOTICES = []
    try:
        from guide_files import publish_guides
        publish_guides(documents_folder(), expected_version=APP_VERSION)
    except (OSError, ValueError) as exc:
        APP.mkdir(parents=True, exist_ok=True)
        (APP/'user-guide-update-error.txt').write_text(str(exc), encoding='utf-8')
        startup_notice('The user guide files could not be copied to Documents. Onboard help is still available. Ask your helper to check the installation.')

    import sounddevice as sd
    sample_rate = 16000
    global resampler, input_channels
    import contextlib
    input_stream=None
    # F9 or "turn off the microphone" is remembered after Companion closes.
    MICROPHONE_OFF=bool(PREFERENCES.get('microphone_off',False))
    if not MICROPHONE_OFF:input_stream=open_microphone(sd)
    MICROPHONE_FAILED=not MICROPHONE_OFF and input_stream is None
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
            startup_notice('Parakeet could not start. Using basic offline speech.')
    utterance = bytearray()
    if PREFERENCES.get('dictation_type')=='ai-based' and parakeet is None:
        startup_notice('AI dictation is not available. Using basic offline speech. Reinstall the full app to restore the AI speech model.')
    last_focus_check = 0.0
    with contextlib.ExitStack() as input_lifetime:
        if input_stream:input_lifetime.callback(close_microphone,input_stream)
        from app_updates import AppUpdates
        UPDATES = AppUpdates(APP, APP_VERSION, Path(getattr(sys, '_MEIPASS', Path(__file__).parent)))
        # One announcement, in one order, on every startup path (startup_speech.py):
        # version, update result, notices, microphone and how to use it, where you are.
        mode = announce_startup()
        sync_app_context(mode,True)
        # Keys must reach this window at once (Thunderbird or the installer may have the foreground).
        if APP_WINDOW is not None and hasattr(APP_WINDOW,'reclaim_focus'):APP_WINDOW.reclaim_focus()
        start_freeze_watch()
        # Listen and process keys while the asynchronous startup speech plays.
        settings_quiet_until=0
        settings_audio_blocked=False
        web_refresh=RefreshPacer();startup_speaking=True;startup_quiet_until=time.monotonic()+.3
        microphone_retry_at=0
        while True:
            FREEZE_WATCH['beat']=time.monotonic()
            poll_dictation_echo()
            mode=poll_thunderbird_watch(mode)
            if input_stream is not None and (MICROPHONE_OFF or not microphone_active(input_stream)):
                close_microphone(input_stream)
                input_stream=None;recognizer.Reset();utterance.clear()
            if input_stream is None and not MICROPHONE_OFF and time.monotonic()>=microphone_retry_at:
                microphone_retry_at=time.monotonic()+2
                input_stream=open_microphone(sd,attempts=1)
                microphone_retried(input_stream is not None,mode)
                if input_stream:
                    input_lifetime.callback(close_microphone,input_stream)
                    recognizer.Reset();utterance.clear()
            if APP_WINDOW is not None and not APP_WINDOW.commands.empty():
                mode=process_keyboard_batch(mode)
                if mode=='exit':return 0
                continue
            logical_mode=SLEEP_RETURN_MODE if mode=='sleep' else mode
            # refresh_now: back in browse mode after app mode, read the page view again at once.
            if logical_mode=='web' and web_session and (getattr(web_session,'refresh_now',False) is True or web_refresh.due(time.monotonic(),0 if APP_WINDOW is None else getattr(APP_WINDOW,'last_navigation',0))):
                started=time.monotonic()
                try:
                    refresh_browser_view(mode)
                except Exception:pass
                web_refresh.done(started,time.monotonic())
            if KEYBOARD_SPEECH and KEYBOARD_SPEECH.narration_interrupt.is_set():
                KEYBOARD_SPEECH.narration_interrupt.clear()
                voice.Speak('',3)
                if AI_SPEECH is not None:AI_SPEECH.interrupt()
            try:flush_keyboard_edits()
            except OSError:speak('Keyboard text could not be saved. Keep the document open and try save again.')
            mode = poll_file_manager(mode)
            mode = poll_app_updates(mode)
            if mode == 'exit': return 0
            mode = __import__('download_screen').poll(sys.modules[__name__], mode)
            if CHROME_SETUP is not None:
                mode = __import__('chrome_setup').poll(sys.modules[__name__],mode)
            update_audio_ducking()
            if APP_WINDOW is not None and APP_WINDOW.closed.is_set():
                close_from_window(mode)
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
                            mode=save_options(values,mode)
                        except Exception as exc:
                            SETTINGS_PANEL.failed('Settings were not saved: '+str(exc))
                    elif kind=='action':
                        mode=settings_action(values,SETTINGS_RETURN_MODE)
                    elif kind=='preview':
                        try:
                            if SETTINGS_PANEL is not None and not SETTINGS_PANEL.closed.is_set() and SETTINGS_PANEL.session.values==values[0]:__import__('app_settings').preview_settings(sys.modules[__name__],*values)
                        except Exception:speak('Voice preview could not play. Your saved settings are unchanged.')
                    settings_result=None
                if SETTINGS_PANEL is not None and SETTINGS_PANEL.closed.is_set() and mode=='sleep' and SLEEP_RETURN_MODE=='settings':
                    __import__('app_settings').stop_preview(sys.modules[__name__])
                    SLEEP_RETURN_MODE=SETTINGS_RETURN_MODE
                    sync_app_context(mode,True)
                if SETTINGS_PANEL is not None and SETTINGS_PANEL.closed.is_set() and mode=='settings':
                    mode=options_closed(mode)
                settings_notices=[]
                while not APP_WINDOW.settings_notices.empty():settings_notices.append(APP_WINDOW.settings_notices.get_nowait())
                if settings_notices:
                    interrupt_speech()
                    speak_keyboard_feedback(settings_notices[-1])
                if settings_result:
                    interrupt_speech()
                    if AI_SPEECH is not None: AI_SPEECH.enabled = False
                    if settings_result == 'removed':
                        if AI_SPEECH is not None: AI_SPEECH.close()
                        AI_SPEECH = None
                    try:
                        if settings_result == 'saved':
                            PREFERENCES['ai_consent']=True
                            __import__('app_settings').save_preferences(sys.modules[__name__])
                            configure_ai_speech()
                        save_speech_settings()
                        speak('AI voice settings saved. Say Use AI voice to test it.' if settings_result == 'saved'
                              else 'Saved AI voice settings removed. Using SAPI.')
                    except Exception:
                        speak('AI settings could not be loaded. Using SAPI.')
                key_notices = []
                while True:
                    try: key_notices.append(APP_WINDOW.key_feedback.get_nowait())
                    except queue.Empty: break
                if key_notices:
                    interrupt_speech()
                    speak_keyboard_feedback(key_notices[-1])
                if not APP_WINDOW.commands.empty():
                    mode=process_keyboard_batch(mode)
                    if mode=='exit':return 0
                    continue
            try:
                audio = AUDIO.get(timeout=0.02)
            except queue.Empty:
                if time.monotonic()-last_focus_check>1.2:
                    last_focus_check=time.monotonic()
                    notice = browser_focus_notice(mode)
                    if notice: speak(notice)
                continue
            if AUDIO_OVERFLOW:
                if cloud:
                    cloud.stop()
                    cloud = None
                    cloud_failed = True
                recover_audio_overflow(recognizer, utterance)
                continue
            if startup_speaking:
                if speech_busy():startup_quiet_until=time.monotonic()+.3
                if time.monotonic()<startup_quiet_until:recognizer.Reset();utterance.clear();continue
                startup_speaking=False
            narration_busy=not SPEECH_PAUSED and speech_busy()
            if mode=='settings' or narration_busy or settings_audio_blocked:
                # No synthesizer's readback may become another command or
                # dictated paragraph, including while the app is in background.
                # Keep local silence/sleep/exit controls available during feedback.
                if cloud:
                    # Pause audio feeding rather than permanently dropping the
                    # cloud recognizer after each spoken app response.
                    discard_feedback_events(cloud)
                if narration_busy:settings_quiet_until=time.monotonic()+0.3
                if time.monotonic()<settings_quiet_until:
                    settings_audio_blocked=True
                    utterance.clear()
                    if not recognizer.AcceptWaveform(audio):
                        interrupt_partial_navigation(recognizer,mode)
                    else:
                        control=json.loads(recognizer.Result()).get('text','').lower()
                        if spoken_control(control) or speech_control(control) or navigation_feedback_control(control,mode) or mode=='help' and HelpSession.accepts(control):
                            interrupt_speech()
                            mode=handle(control,mode)
                            if mode=='exit':return 0
                    continue
                if settings_audio_blocked:
                    recognizer.Reset();utterance.clear();settings_audio_blocked=False
                    continue
            if cloud and not cloud_recognition_allowed(mode,PREFERENCES,account_setup):
                cloud.stop();cloud=None
                recognizer.Reset();utterance.clear()
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
                if mode!='sleep':interrupt_partial_navigation(recognizer,mode)
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
            if mode != 'sleep' and speech_busy() and local_control and (mode!='help' or HelpSession.accepts(local_control)):
                # Stop the current readback as soon as the offline recognizer
                # recognizes a new utterance. Its words can still be refined
                # by Parakeet before executing the requested action.
                interrupt_for_dictation()
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
            if mode == 'sleep':
                for wake in WAKE:
                    if wake in heard:
                        mode = resume_from_sleep()
                        if not cloud_failed and not parakeet and cloud_recognition_allowed(mode,PREFERENCES,account_setup):
                            try:
                                while True:
                                    try:AUDIO.get_nowait()
                                    except queue.Empty:break
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
                SPEECH_PITCH = max(-10,min(10,int(settings.get('pitch',0))))
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
            KEYBOARD_SPEECH=KeyboardSpeech();KEYBOARD_SPEECH.configure(voice.Voice.Id,SPEECH_RATE,SPEECH_VOLUME,ESPEAK_SPEECH,SPEECH_PITCH,ai=AI_SPEECH,capital_letters=PREFERENCES.get('capital_letters','do nothing'));APP_WINDOW.keyboard_output=KEYBOARD_SPEECH
            APP_WINDOW.start()
            if os.name=='nt':
                try:
                    from browser_cdp_relay import prepare
                    prepare(APP)
                except OSError:pass
                try:
                    # Thunderbird's mail add-on connects here when Thunderbird is open.
                    from thunderbird_bridge import prepare as prepare_mail
                    prepare_mail(APP)
                    # A Thunderbird account starts Thunderbird when it is used, never "open it yourself".
                    import thunderbird_mailbox
                    thunderbird_mailbox.STARTER=thunderbird_starter
                    thunderbird_mailbox.PROGRESS=speak_status
                    thunderbird_mailbox.REMEMBER_IDENTITY=remember_thunderbird_identity
                    start_thunderbird_quietly()
                except OSError:pass
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





