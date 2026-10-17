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
from privacy_controls import narration_allowed,cloud_recognition_allowed
from parakeet_recognition import ParakeetRecognition
from browser_navigation import prepare_address
from web_assistant import WebSession
from audio_resample import PCM16Resampler, stereo_to_mono
from app_window import CompanionWindow
from reading_navigation import ReadingCursor, reading_request
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
APP_VERSION = '0.2.145-test'
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
ACCOUNT_MENU_INDEX = None
help_session = None
help_return_mode = 'awake'
TEXT_MODE = False
note_buffer = []
note_selection = FieldSelection()
note_reading = ReadingCursor()
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
DOCUMENT_CLOSE_TARGET = "awake"
DOCUMENT_BASELINE = None
CONFIRM_CHOICE = "yes"
POWER_ACTION = None
POWER_RETURN_MODE = "awake"
EXIT_RETURN_MODE = "awake"
SEARCH_RETURN_MODE = 'awake'

def checkpoint_document():
    global DOCUMENT_BASELINE
    path=document.path
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
    context['menu_letters']=logical!='document_name' and menu_state(logical) is not None
    if logical=='files' and FILE_MANAGER:
        context['file_name_input']=bool(FILE_MANAGER.pending and FILE_MANAGER.pending[0]=='rename')
        if context['file_name_input']:context['file_rename_value']=Path(FILE_MANAGER.pending[1][0]).name
        context['menu_letters']=not context['file_name_input'] and not FILE_MANAGER.busy
        if FILE_MANAGER.pending and FILE_MANAGER.pending[0]=='delete':context['confirmation']=CONFIRM_CHOICE
        context['file_revision']=FILE_MANAGER.revision
        context['file_items']=FILE_MANAGER.labels()
        context['file_index']=(0 if CONFIRM_CHOICE=='yes' else 1) if context.get('confirmation') is not None else FILE_MANAGER.menu_index if FILE_MANAGER.menu else FILE_MANAGER.index
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
    if pending_website is not None or logical in ('document_save','power_confirm','exit_confirm','update_offer') or logical=='email_draft' and pending_send is not None:context['confirmation']=CONFIRM_CHOICE
    if logical=='mailbox' and mail_session and mail_session.pending:context['confirmation']=CONFIRM_CHOICE
    if logical=='web' and web_session and (web_session.pending or web_session.submit_pending):context['confirmation']=CONFIRM_CHOICE
    if logical=='document_name':context['naming_request']=PENDING_DOCUMENT_NAME
    editor=document if logical=='document' else email_draft if logical=='email_draft' and email_draft and (email_draft.compose_step or 'body')=='body' else None
    if editor is not None and (getattr(editor,'selection_candidates',[]) or getattr(editor,'replacement_candidates',[]) or getattr(editor,'pending_spacing',False)):
        return context
    if editor is not None:
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
        context['web_choice']=bool(field and __import__('web_browse').kind(field) in ('combobox','listbox'))
        context['web_combo']=bool(web_session.browse and web_session.browse.current() and __import__('web_browse').kind(web_session.browse.current())=='combobox')
        document_identity=(web_session.document_state or {}).get('epoch',getattr(web_session,'page_revision',0))
        if field:context['web_field_source']='web:'+str(id(web_session))+':document:'+str(document_identity)+':'+str(field.get('frame_index','main'))+':'+field['key']
        if field and field.get('type')=='password':context['web_private']=True;context['web_field']=dict(field)
        else:
            source='web:'+str(id(web_session))+(':field:'+str(field.get('frame_index','main'))+':'+field['key']+':document:'+str(document_identity) if field else ':page')
            context.update(source=source,text=field.get('value','') if field else web_session.reading.text,readonly=field is None or context['web_choice'] or bool(field.get('readonly')),selection=getattr(web_session,'selection',None),caret=getattr(web_session,'web_edit_caret',0) if field else web_session.reading.position,ack=EDIT_ACK.get(source,0))
            context['web_field']=field
        if web_session.pending or web_session.submit_pending:context['confirmation']=CONFIRM_CHOICE
    elif logical=='file_reader' and FILE_READER:
        context.update(source='file:'+str(id(FILE_READER)),text=FILE_READER.text,readonly=True,caret=FILE_READER.reading.position,ack=EDIT_ACK.get('file:'+str(id(FILE_READER)),0))
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
    if APP_WINDOW is None:return
    context=app_context(mode)
    if UI_CONTEXT_CACHE and context.get('source')!=UI_CONTEXT_CACHE.get('source'):DICTATION_ECHO.clear()
    if KEYBOARD_SPEECH and not TEXT_MODE:
        KEYBOARD_SPEECH.configure(voice.Voice.Id,SPEECH_RATE,SPEECH_VOLUME,ESPEAK_SPEECH,SPEECH_PITCH,ai=AI_SPEECH,capital_letters=PREFERENCES.get('capital_letters','do nothing'))
    if focus or context!=UI_CONTEXT_CACHE:
        UI_CONTEXT_CACHE=dict(context);context['focus']=focus;APP_WINDOW.set_context(context)

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
        if context['mode']=='web' and web_session.browse and web_session.keyboard_field is None:
            web_session.selection=payload.get('selection')
            web_session.browse.set_position(payload['caret'])
            if not payload.get('selection'):web_session.auto_focus_position()
    elif context['mode']=='web':
        web_session.keyboard_field['value']=payload['after'];web_session.web_edit_caret=payload['caret']
        web_session.selection=payload.get('selection')
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
    if mode=='sleep' and normalized_command(text) not in ('wake up','wake companion'):
        SLEEP_RETURN_MODE=handle_keyboard(text,SLEEP_RETURN_MODE)
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
    try:
        # Bound each batch so microphone controls continue to be serviced.
        for _ in range(100):
            try:command=APP_WINDOW.commands.get_nowait()
            except queue.Empty:break
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
                elif command[0]=='media_field':
                    speak(media_navigation().field_value(command[1]['value'],command[1]['next']));sync_app_context(mode,True);continue
                elif command[0]=='email_field_tab':
                    command=commit_email_field_tab(command[1],SLEEP_RETURN_MODE if mode=='sleep' else mode)
                else:
                    key=command[1]
                    if key=='Control':KEYBOARD_BATCH_TEXT=None
                    command='open options' if key=='settings' else navigation_key(key,SLEEP_RETURN_MODE if mode=='sleep' else mode)
                if command is None:continue
            mode=resume_from_sleep() if mode=='sleep' and command.lower() in ('wake up','wake companion') else handle_keyboard(command,mode)
            if mode=='exit':break
    finally:KEYBOARD_BATCH=previous
    if not previous and APP_WINDOW.commands.empty() and KEYBOARD_BATCH_TEXT:
        if KEYBOARD_BATCH_STATUS:finish_mail_announcement()
        text=KEYBOARD_BATCH_TEXT;KEYBOARD_BATCH_TEXT=None
        APP_WINDOW.feedback(punctuation_for_speech(text))
    return mode

SLEEP_RETURN_MODE = 'awake'
MAIN_MENU_PROMPT = 'Main menu. What would you like to do? Say next or previous to move through the items, and okay, confirm, confirm that, or that one to open.'

def announce_main_menu():
    global MAIN_MENU_INDEX
    MAIN_MENU_INDEX = None
    speak_prompt(MAIN_MENU_PROMPT)

def startup_prompt():
    return 'Voice Companion ' + APP_VERSION + ' is ready. Say wake up to control the app by voice, or use your Up and Down arrow keys, Enter and Escape to move through the menus. Say start tutorial for spoken lessons that wait for you. Help is available.'

MAIN_MENU_INDEX = None
MAIN_MENU_CHOICES = (('File manager', 'file manager'), ('Word processor', 'create a document'), ('Notepad', 'write a note'), ('Email', 'email'), ('Web browser', 'web browser'), ('Radio', 'radio'), ('Podcasts', 'podcasts'), ('Options', 'options'), ('Check for updates', 'check for updates'), ('Help', 'help'), ('Shut down the computer', 'shut down computer'), ('Restart the computer', 'restart computer'), ('Exit Voice Companion', 'exit companion'))


def resume_from_sleep():
    global MAIN_MENU_INDEX
    interrupt_speech()
    mode = SLEEP_RETURN_MODE
    if mode == 'awake':
        MAIN_MENU_INDEX = None
        announce_main_menu()
        sync_app_context(mode,True)
        return mode
    speak('I am listening. Returning to ' + {'awake':'the main menu', 'mailbox':'your email folder', 'email_draft':'your email draft', 'document':'your document','browser_menu':'the web browser menu'}.get(mode, mode.replace('_', ' ')) + '.')
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
    if account_setup:return state(['Gmail','Outlook or Exchange','Yahoo'],module,'ACCOUNT_MENU_INDEX')
    if mode in ('document_save','power_confirm','exit_confirm','update_offer') or mode=='email_draft' and pending_send is not None:
        return None
    if NAMED_LIST:
        return ([item[0] for item in NAMED_LIST['items']],NAMED_LIST['index'],lambda index:NAMED_LIST.update(index=index))
    if SYNTH_PICK_INDEX is not None:
        return state([x[1] for x in synthesizers()],module,'SYNTH_PICK_INDEX')
    if VOICE_PICK_INDEX is not None:
        return state([x[1] for x in voice_choices(VOICE_PICK_ENGINE)],module,'VOICE_PICK_INDEX')
    if mode=='files' and FILE_MANAGER and not FILE_MANAGER.pending:
        names=FILE_MANAGER.labels()
        attribute='menu_index' if FILE_MANAGER.menu else 'index'
        return state(names,FILE_MANAGER,attribute)
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
        if field=='recipient':email_draft.recipient=normalize_recipient(value) if value else ''
        elif field in ('cc','bcc'):
            setattr(email_draft,field,[normalize_recipient(x) for x in re.split(r'[,;]',value) if x.strip()])
        elif field=='subject':
            if email_draft.response_context and value!=email_draft.subject:
                raise ValueError('This response keeps the original subject.')
            email_draft.subject=value
        email_draft.save()
    except (ValueError,OSError) as exc:speak(str(exc));return None
    return 'send email' if payload.get('send') else navigation_key('ShiftTab' if payload['backward'] else 'Tab',mode)

def navigation_key(key,mode):
    global KEYBOARD_NAVIGATION
    previous=KEYBOARD_NAVIGATION;KEYBOARD_NAVIGATION=True
    try:return _navigation_key(key,mode)
    finally:KEYBOARD_NAVIGATION=previous

def _navigation_key(key,mode):
    global CONFIRM_CHOICE, ACCOUNT_MENU_INDEX
    interrupt_speech()
    if key=='Ctrl+Alt+P':return 'toggle audio'
    if key=='Control':return None
    if mode=='files':
        if FILE_MANAGER and FILE_MANAGER.pending and FILE_MANAGER.pending[0]=='delete':
            if key in ('Up','Left','Home','ShiftTab'):CONFIRM_CHOICE='yes';speak('Yes.');return None
            if key in ('Down','Right','End','Tab'):CONFIRM_CHOICE='no';speak('No.');return None
            return {'Enter':CONFIRM_CHOICE,'Space':CONFIRM_CHOICE,'Escape':'no','y':'yes','Y':'yes','n':'no','N':'no'}.get(key)
        return {'Up':'previous','Down':'next','Left':'previous','Right':'next','Home':'home','End':'end','Enter':'enter','Space':'toggle selection','Escape':'escape','Alt':'file manager menu','F1':'file manager help','F2':'rename','Delete':'delete','Ctrl+C':'copy','Ctrl+X':'cut','Ctrl+V':'paste','Ctrl+A':'select all','Ctrl+Shift+A':'clear selection','F5':'refresh','Ctrl+Home':'drive list','Ctrl+M':'main menu'}.get(key) if not key.startswith('Letter:') else ('file letter '+key[7:])
    if mode=='file_reader' and key=='Escape':return 'back to files'
    if mode=='regular_browser':
        if key=='Escape':return 'main menu'
        if key in ('Up','Down','Home','End','Enter','Space'):return 'regular browser key '+key
        return None
    if mode=='web' and web_session and web_session.list_focus in ('links','headings') and key in ('Up','Down','Left','Right','Home','End','Enter','Escape'):
        web_session.list_prefix='';web_session.list_prefix_time=0
    if NAMED_LIST and key in ('Up','Down','Left','Right','Enter','Escape'):return {'Up':'previous','Left':'previous','Down':'next','Right':'next','Enter':'okay','Escape':'close list'}[key]
    if key=='Alt+F4':return 'exit companion'
    confirming=pending_website is not None or mode in ('document_save','power_confirm','exit_confirm','update_offer') or mode=='email_draft' and pending_send is not None or (mode=='mailbox' and mail_session and mail_session.pending) or (mode=='web' and web_session and (web_session.pending or web_session.submit_pending))
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
        if result=='__main__':return 'main menu'
        if result:speak(result)
        sync_app_context(mode,True)
        return None
    if mode=='browser_menu':
        if key in ('Up','Down','Left','Right','Home','End','Enter','Escape'):
            return {'Up':'previous','Left':'previous','Down':'next','Right':'next','Home':'home','End':'end','Enter':'enter','Escape':'escape'}[key]
        if key.startswith('Letter:'):return key
    if mode=='web' and key in ('Escape','Web:Escape') and web_session and isinstance(web_session.snapshot,dict) and web_session.snapshot.get('modal_key') and not web_session.list_focus:return 'web key Escape'
    if mode=='web' and key=='Escape' and web_session and not (web_session.list_focus or web_session.keyboard_field):return 'web browser menu'
    if mode=='web' and key=='Web:Escape' and web_session and web_session.keyboard_field is None and not web_session.list_focus:return 'web browser menu'
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
            speak(mail_session.list_boundary(key=='End'));return None
    if mode=='help' and key=='Escape':return 'go back'
    if mode=='help' and help_session and help_session.level=='article' and (key.startswith('Help:') or key in ('Up','Down','Left','Right','Home','End')):
        return {'Up':'previous paragraph','Down':'next paragraph','Home':'first block','End':'last block',
                'Left':'previous character','Right':'next character','Ctrl+Left':'previous word','Ctrl+Right':'next word'}[key[5:] if key.startswith('Help:') else key]
    if key.startswith('Letter:'):
        if mode=='web' and web_session and not web_session.list_focus and key[-1] in __import__('web_browse').QUICK_KEYS:return 'web key Quick:'+key[-1]
        choose_menu_letter(key[-1],mode);return None
    if key=='Enter' and account_setup and ACCOUNT_MENU_INDEX is not None:
        return ('gmail','outlook','yahoo')[ACCOUNT_MENU_INDEX]
    if account_setup and key in ('Up','Down','Left','Right'):
        step=1 if key in ('Down','Right') else -1
        if ACCOUNT_MENU_INDEX is not None and not 0<=ACCOUNT_MENU_INDEX+step<3:speak('Start of list.' if step<0 else 'End of list.');return None
        ACCOUNT_MENU_INDEX=bounded_index(ACCOUNT_MENU_INDEX,step,3)
        speak(announce_item(('Gmail','Outlook or Exchange','Yahoo')[ACCOUNT_MENU_INDEX],ACCOUNT_MENU_INDEX,3));return None
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
    if mode=='mailbox':
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

def options_return_notice(mode):
    logical=SLEEP_RETURN_MODE if mode=='sleep' else mode
    return {'awake':'Main menu.','document':'Back to document.','email_draft':'Back to email draft.','mailbox':'Back to email.','web':'Back to web page.','browser_menu':'Back to web browser menu.','note':'Back to Notepad.','files':'Back to file manager.','file_reader':'Back to document reader.','help':'Back to help.','media':'Back to media.'}.get(logical,'Back to '+logical.replace('_',' ')+'.')

def poll_file_manager(mode):
    global FILE_READER,SLEEP_RETURN_MODE
    if FILE_MANAGER is None:return mode
    notice=FILE_MANAGER.poll()
    logical=SLEEP_RETURN_MODE if mode=='sleep' else mode
    if logical=='files':
        if FILE_MANAGER.opened is not None:
            FILE_READER=FILE_MANAGER.opened;FILE_MANAGER.opened=None
            notice=notice or 'Opened '+FILE_READER.title+'. Read-only document. '+FILE_READER.reading.read(True)
            if mode=='sleep':SLEEP_RETURN_MODE='file_reader'
            else:mode='file_reader'
        if notice:speak(notice)
        if notice:sync_app_context(mode,True)
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
    if mode in ('settings','power_confirm','exit_confirm','media','help','browser_menu'): return True
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
        json.dumps({'pitch':SPEECH_PITCH,'rate': SPEECH_RATE, 'volume': SPEECH_VOLUME, 'punctuation': PUNCTUATION_LEVEL, 'verbosity':VERBOSITY, 'input_mode':INPUT_MODE,
                    'ai_enabled': bool(AI_SPEECH and AI_SPEECH.enabled), 'ai_voice': AI_VOICE_NAME,
                    'espeak_enabled': bool(ESPEAK_SPEECH and ESPEAK_SPEECH.enabled), 'espeak_voice': ESPEAK_VOICE_NAME,
                    'voice': voice.Voice.GetDescription() if not TEXT_MODE else ''}), encoding='utf-8')



def change_speech_setting(command):
    global SPEECH_PITCH, SPEECH_RATE, SPEECH_VOLUME
    request = speech_setting_request(command)
    if request is None: return False
    kind, operation, amount = request
    lower, upper = (-10, 10) if kind in ('rate','pitch') else (0, 100)
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

def open_browser_menu():
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
    speak(web_session.menu.start(resume=(PREFERENCES.get('web_keep_open',False) or getattr(web_session.backend,'regular',False)) and bool(web_session.snapshot)))
    return 'browser_menu'

def handle_browser_menu(text):
    global SEARCH_RETURN_MODE
    notice,action=web_session.menu.process(text,web_session,PREFERENCES.get('web_keep_open',False))
    if notice:speak(notice)
    if action=='main':return 'awake'
    if action=='search':
        SEARCH_RETURN_MODE='browser_menu'
        speak('Search or type address. Say or type your search or address, then press Enter. Escape returns to the web browser menu.');return 'search'
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


def finish_exit(mode):
    global help_session, account_setup, pending_send, mail_session, KEYBOARD_BATCH_TEXT
    KEYBOARD_BATCH_TEXT=None
    if email_draft is not None:email_draft.save()
    help_session=None;account_setup=False;pending_send=None;mail_session=None
    flush_note(mode)
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


def _handle(text, mode, typed=False):
    global SYNTH_PICK_INDEX, VOICE_PICK_ENGINE, MAIN_MENU_INDEX
    global SLEEP_RETURN_MODE, document, email_draft, pending_website, pending_send, mail_session, web_session, account_setup, INPUT_MODE, VOICE_PICK_INDEX, VOICE_PICK_ORIGINAL, VOICE_PICK_CONFIRM, MEDIA_SECTION, help_session, help_return_mode, tutorial_session, AI_VOICE_NAME
    text = text.strip()
    global UPDATE_MANUAL, UPDATE_LAST_PERCENT, SETTINGS_PANEL, SETTINGS_RETURN_MODE, VERBOSITY, PENDING_DOCUMENT_NAME, DOCUMENT_NAME_RETURN, DOCUMENT_CLOSE_TARGET, CONFIRM_CHOICE
    global POWER_ACTION, POWER_RETURN_MODE, EXIT_RETURN_MODE
    global SEARCH_RETURN_MODE,NAMED_LIST
    command=normalized_command(text)
    if command in ('regular browser','open regular browser','connect regular browser'):
        return open_browser_menu()
    if command=='toggle audio':
        speak(MEDIA_HUB.command(command) if MEDIA_HUB else 'No audio is playing. Choose a station or episode first.')
        return mode
    if mode=='browser_menu' and web_session.menu.level=='name':return handle_browser_menu(text)
    background_request=re.fullmatch(r'(?:set )?keep audio playing in (?:the )?background (on|off)',command)
    if background_request and mode!='settings':
        PREFERENCES['keep_audio_playing']=background_request[1]=='on';save_preferences()
        speak('Keep audio playing in the background '+background_request[1]+'.');return mode

    if mode=='web' and command in ('web browser menu','browser menu','leave website'):
        web_session.leave(PREFERENCES.get('web_keep_open',False))
        return open_browser_menu()
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
        speak(result)
        if closed:
            tutorial_session=None
            return 'awake'
        return mode
    if mode=='browser_menu' and command not in ('help','options','open options','main menu','back to main menu','exit companion','go to sleep') and not command.startswith(('help with ', 'help for ')) and (command=='resume' or not speech_control(command)) and speech_setting_request(text) is None and command not in VOICE_LIST_COMMANDS and command not in ('use sapi','use sapi voice','use windows voice','use ai voice','use espeak','document help','email help','web help','radio help','podcast help','commands help') and not spoken_control(command):
        return handle_browser_menu(text)
    if command in ('next','previous'):
        state=menu_state(mode)
        if state and state[1] is not None and (state[1]==0 and command=='previous' or state[1]==len(state[0])-1 and command=='next'):
            speak('Start of list.' if command=='previous' else 'End of list.');return mode
    if command in ('word processor','open word processor'):text='create a document';command=text
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
                return handle({'exit':'exit companion','restart':'restart computer','shutdown':'shut down computer'}[DOCUMENT_CLOSE_TARGET],'awake',typed) if DOCUMENT_CLOSE_TARGET in ('exit','restart','shutdown') else 'awake'
            DOCUMENT_NAME_RETURN=DOCUMENT_CLOSE_TARGET
            PENDING_DOCUMENT_NAME=None
            speak('Name this document. Say or type its name, then press Enter. Say cancel to keep editing.')
            return 'document_name'
        if command in ('no','no thanks'):
            try:discard_document_changes()
            except OSError:
                speak('Could not discard changes safely. Your document is still open.');return 'document'
            document=None
            speak('Changes discarded.')
            return handle({'exit':'exit companion','restart':'restart computer','shutdown':'shut down computer'}[DOCUMENT_CLOSE_TARGET],'awake',typed) if DOCUMENT_CLOSE_TARGET in ('exit','restart','shutdown') else 'awake'
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
        result=document.process('name document '+title)
        speak(result)
        if result.startswith('Document named '):
            PENDING_DOCUMENT_NAME=None
            checkpoint_document()
            return handle({'exit':'exit companion','restart':'restart computer','shutdown':'shut down computer'}[DOCUMENT_NAME_RETURN],'awake',typed) if DOCUMENT_NAME_RETURN in ('exit','restart','shutdown') else DOCUMENT_NAME_RETURN
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
    search_engine_request=re.fullmatch(r'(?:(?:set|choose|use) )?(?:default )?search engine(?: to)? (google|bing|brave(?: search)?|duck duck go|duckduckgo)',command)
    if search_engine_request:
        engine='brave' if search_engine_request[1].startswith('brave') else 'duckduckgo' if search_engine_request[1].startswith('duck') else search_engine_request[1]
        PREFERENCES['web_search_engine']=engine;save_preferences()
        if web_session:web_session.search_engine=engine
        speak('Default search engine '+{'google':'Google','bing':'Bing','brave':'Brave Search','duckduckgo':'DuckDuckGo'}[engine]+'.');return mode
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
            except (ValueError,OSError) as exc:speak(str(exc))
            return mode
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
        interrupt_speech()
        speak(str(UPDATES.percent)+' percent.')
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
        if UPDATES.phase in ('preparing','installing'):
            speak('Preparing installation. Voice Companion will reopen in sleep mode.')
            return mode
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
    if change_speech_setting(command): return mode
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
        if result is not None:speak(result)
        if help_session.closed:
            help_session = None
            return help_return_mode
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
    global FILE_MANAGER,FILE_READER
    if command in ('file manager','open file manager','manage files','browse files'):
        if mode=='document' and document is not None:document.save()
        if FILE_MANAGER is None:FILE_MANAGER=FileManager()
        FILE_MANAGER.menu=False;FILE_MANAGER.pending=None
        speak('File manager. '+FILE_MANAGER.refresh())
        return 'files'
    if mode=='files':
        if command in ('main menu','back to main menu','close file manager','leave file manager'):
            announce_main_menu();return 'awake'
        if command.startswith('file letter '):
            state=menu_state(mode)
            if state:choose_menu_letter(command[-1],mode)
            return mode
        try:result=FILE_MANAGER.command(text)
        except (ValueError,OSError) as exc:result=str(exc)
        if result=='__main__':announce_main_menu();return 'awake'
        if FILE_MANAGER.pending and FILE_MANAGER.pending[0]=='delete':CONFIRM_CHOICE='no'
        speak(result)
        sync_app_context(mode,True)
        return mode
    if mode=='file_reader':
        if command in ('back to files','go back','back','close document','leave document','escape'):
            speak('Back to file manager. '+FILE_MANAGER.say_current());return 'files'
        if command in ('main menu','back to main menu'):
            announce_main_menu();return 'awake'
        request=reading_request(command)
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
        if mail_session is None and provider in ('gmail', 'outlook', 'yahoo'):
            mail_session = MailSession(provider, APP, progress=mail_progress)
        if mail_session is not None:
            speak(mail_session.process('open inbox'))
            return 'mailbox'
        speak('Back at the main menu. No email account is connected.')
        return 'awake'
    if mode == 'email_draft' and email_draft is not None and email_field_destination(command):
        pending_send = None
        dictation_feedback(email_draft.process(text))
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
        DOCUMENT_CLOSE_TARGET='awake';CONFIRM_CHOICE='yes'
        speak('Save this document? Yes or no. Use arrows to choose, then Enter. Escape keeps editing.')
        return 'document_save'
    if mode=='document' and command in ('name document','rename document','save as','save document as'):
        DOCUMENT_NAME_RETURN='document';PENDING_DOCUMENT_NAME=None
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
    if INPUT_MODE == 'dictation' and not typed:
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
        speak('Which email account type? Gmail (G), Outlook or Exchange (O), Yahoo (Y). Press a letter then Enter, or say the account type. A trusted person can use the normal sign-in page or type private details.')
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
            speak(open_named_list('Email accounts',[(p.capitalize()+' '+a+(' selected' if selected==(p,a) else ''),'switch to '+a) for p,a in accounts]))
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
        speak(open_named_list('Contacts',[(e['name'],'find contact '+e['name']) for e in entries]) if entries else
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
    sender_recipient=None
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
        speak_prompt('Dictation started. Speak naturally. Say save note when finished.')
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
        if command in ('leave website', 'close website','close browser','exit browser','leave web browser','close web browser','exit web browser', 'main menu', 'back to main menu'):
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
                      copied + '. Subject ' + (subject or 'no subject') + '. Message: ' + (body or 'no message body') + source_note +
                      '. Is that right? Press Y or N, or use arrows or Tab and Enter. Escape keeps the draft. You can also say yes to send or no to keep it.')
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
                email_draft.save()
                submit(provider, email_draft, token, sender=address)
                email_draft.last_accepted_hash = fingerprint
                email_draft.save()
            except (AccountError, DeliveryError, ValueError, OSError, KeyError) as exc:
                uncertain=email_draft.last_attempt_hash==fingerprint and (not isinstance(exc,DeliveryError) or exc.uncertain)
                if not uncertain:
                    email_draft.last_attempt_hash='';email_draft.save()
                reason=str(exc) if isinstance(exc,(AccountError,DeliveryError,ValueError)) else 'The email connection could not complete the request.'
                speak(reason+' The draft is saved locally.'+(' Check Sent Mail before trying again; do not send a duplicate.' if uncertain else ' It has not been sent. Correct the problem and review the draft again.'))
                return mode
            speak_status('Email sent successfully.')
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
        dictation_feedback(email_draft.process(text))
        focus_email_entry()
        return 'email_draft'
    if mode == 'document':
        if INPUT_MODE == 'commands' and command in ('start dictation', 'dictate', 'continue writing'):
            speak_prompt('Commands only is on. Say normal mode or dictation mode to write.')
            return mode
        result = document.process(text)
        if INPUT_MODE == 'mixed' and result.startswith('I did not recognize that document request.'):
            document.dictating = True
            result = document.append_text(text)
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
        cleaned = prepare_dictation(text)
        if cleaned:
            note_buffer.append(cleaned)
            dictation_feedback('Added: '+cleaned)
        return 'note'
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
    global STARTUP_UPDATE_PENDING, UPDATE_RETURN_MODE, CONFIRM_CHOICE
    STARTUP_UPDATE_PENDING = False
    if '--resume-after-update' in sys.argv or (APP/'update-result.json').is_file():
        UPDATE_RETURN_MODE='sleep'
        speak(startup_prompt())
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
            speak('A new update is available. Would you like to install it now? Press Y or N, or use arrows or Tab and Enter. Escape cancels. You can also say yes or no.')
            return 'update_offer'
    speak(startup_prompt())
    return 'sleep'


_HANDLE_DEPTH = 0

def handle(text, mode, typed=False):
    global _HANDLE_DEPTH, CONFIRM_CHOICE, KEYBOARD_NAVIGATION
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
        if _HANDLE_DEPTH==1:flush_keyboard_edits(True)
        with dictation_scope(PREFERENCES.get('dictation_type','old school'), typed=typed):
            result = _handle(text, mode, typed)
        if (mail_session and not old_mail_pending and getattr(mail_session,"pending",None)) or old_send is None and pending_send is not None:CONFIRM_CHOICE="yes"
    finally:
        _HANDLE_DEPTH -= 1
        KEYBOARD_NAVIGATION=previous_navigation
    if result == 'awake' and mode not in ('awake', 'sleep') and _HANDLE_DEPTH == 0:
        if MEDIA_HUB and not PREFERENCES.get('keep_audio_playing',False):MEDIA_HUB.audio_command('stop audio')
        announce_main_menu()
    if result!=mode:DICTATION_ECHO.clear()
    if _HANDLE_DEPTH==0 and result!='exit':sync_app_context(result,result!=mode)
    return result


def poll_app_updates(mode):
    global UPDATE_OFFER, UPDATE_RETURN_MODE, UPDATE_LAST_PERCENT, CONFIRM_CHOICE
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
                for step in range(UPDATE_LAST_PERCENT+5,percentage+1,5):speak(str(step)+' percent.')
                UPDATE_LAST_PERCENT = percentage
        elif event == 'downloaded' and mode == 'update_download' and not UPDATES.canceled.is_set():
            path, release = payload
            try:
                speak('Installing the update. Voice Companion will close and reopen when installation finishes.')
                finish_mail_announcement()
                if not UPDATES.begin_install(path, release):raise RuntimeError('Updater busy')
            except Exception:
                speak('The update could not start. Your current app remains available.')
                mode = return_from_update()
        elif event == 'install_ready' and mode=='update_download':
            if web_session:
                try:web_session.leave(False)
                except Exception:pass
            if MEDIA_HUB is not None:
                try:MEDIA_HUB.player.close()
                except Exception:pass
            return 'exit'
        elif event == 'install_failed' and mode=='update_download':
            speak('The update could not start. Your current app remains available.')
            mode=return_from_update()
    if UPDATE_OFFER and (mode in ('sleep', 'awake') or UPDATE_MANUAL) and not speech_busy():
        UPDATE_RETURN_MODE = 'awake' if mode == 'sleep' else mode
        UPDATE_OFFER = False
        CONFIRM_CHOICE='yes'
        speak('A new update is available. Would you like to install it now? Press Y or N, or use arrows or Tab and Enter. Escape cancels. You can also say yes or no.')
        return 'update_offer'
    return mode


def main():
    global AI_SPEECH, UPDATES, SETTINGS_PANEL, SLEEP_RETURN_MODE
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
            guide_sources(expected_version=APP_VERSION)
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
        publish_guides(documents_folder(), expected_version=APP_VERSION)
    except (OSError, ValueError) as exc:
        APP.mkdir(parents=True, exist_ok=True)
        (APP/'user-guide-update-error.txt').write_text(str(exc), encoding='utf-8')
        speak('The user guide files could not be copied to Documents. Onboard help is still available. Ask your helper to check the installation.')

    import sounddevice as sd
    sample_rate = 16000
    global resampler, input_channels
    import contextlib
    input_stream=None
    input_stream=open_microphone(sd)
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
    if PREFERENCES.get('dictation_type')=='ai-based' and parakeet is None:
        speak('AI dictation is not available. Using basic offline speech. Reinstall the full app to restore the AI speech model.')
    last_focus_check = 0.0
    with contextlib.ExitStack() as input_lifetime:
        if input_stream:input_lifetime.callback(close_microphone,input_stream)
        from app_updates import AppUpdates
        UPDATES = AppUpdates(APP, APP_VERSION, Path(getattr(sys, '_MEIPASS', Path(__file__).parent)))
        mode = startup_update_mode()
        sync_app_context(mode,True)
        if input_stream is None:speak('The microphone could not open. Voice commands are disabled. Keyboard navigation is available. Check the Windows input device and microphone permissions, then restart Companion.')
        # Listen and process keys while the asynchronous startup speech plays.
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
        last_web_refresh=0.0;startup_speaking=True;startup_quiet_until=time.monotonic()+.3
        microphone_retry_at=0
        while True:
            poll_dictation_echo()
            if input_stream is not None and not microphone_active(input_stream):
                close_microphone(input_stream)
                input_stream=None
            if input_stream is None and time.monotonic()>=microphone_retry_at:
                microphone_retry_at=time.monotonic()+2
                input_stream=open_microphone(sd,attempts=1)
                if input_stream:
                    input_lifetime.callback(close_microphone,input_stream)
                    recognizer.Reset();utterance.clear()
                    if mode!='sleep':speak('The microphone is on.')
            if APP_WINDOW is not None and not APP_WINDOW.commands.empty():
                mode=process_keyboard_batch(mode)
                if mode=='exit':return 0
                continue
            logical_mode=SLEEP_RETURN_MODE if mode=='sleep' else mode
            if logical_mode=='web' and web_session and time.monotonic()-last_web_refresh>=.3 and (APP_WINDOW is None or time.monotonic()-getattr(APP_WINDOW,'last_navigation',0)>.12):
                last_web_refresh=time.monotonic()
                try:
                    refresh_browser_view(mode)
                except Exception:pass
            if KEYBOARD_SPEECH and KEYBOARD_SPEECH.narration_interrupt.is_set():
                KEYBOARD_SPEECH.narration_interrupt.clear()
                voice.Speak('',3)
                if AI_SPEECH is not None:AI_SPEECH.interrupt()
            try:flush_keyboard_edits()
            except OSError:speak('Keyboard text could not be saved. Keep the document open and try save again.')
            mode = poll_file_manager(mode)
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
                    __import__('app_settings').stop_preview(sys.modules[__name__])
                    mode=SETTINGS_RETURN_MODE
                    sync_app_context(mode,True)
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





