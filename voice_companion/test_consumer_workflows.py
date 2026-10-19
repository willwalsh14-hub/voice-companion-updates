"""Long consumer workflows through the actual app, with isolated local data."""
import io,queue,random,tempfile,unittest
from contextlib import ExitStack,redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock,patch
import companion as app
from document_editor import VoiceDocument
from field_selection import FieldSelection
from keyboard_text import document_text
from media_hub import MediaHub
from media_navigation import MediaNavigation
from onboard_help import HelpSession
from practice_tutorial import PracticeTutorial
from reading_navigation import ReadingCursor

class ConsumerWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        defaults={'APP':self.root,'TEXT_MODE':True,'APP_WINDOW':None,'INPUT_MODE':'mixed','document':None,'email_draft':None,'mail_session':None,'web_session':None,'pending_website':None,'pending_send':None,'NAMED_LIST':None,'account_setup':False,'MAIN_MENU_INDEX':None,'SYNTH_PICK_INDEX':None,'VOICE_PICK_INDEX':None,'VOICE_PICK_ORIGINAL':None,'help_session':None,'tutorial_session':None,'MEDIA_HUB':None,'note_buffer':[],'note_selection':FieldSelection(),'note_reading':ReadingCursor(),'KEYBOARD_DIRTY':{},'EDIT_ACK':{},'PREFERENCES':dict(app.PREFERENCES),'DOCUMENT_BASELINE':None,'SLEEP_RETURN_MODE':'awake','SEARCH_RETURN_MODE':'awake'}
        for name,value in defaults.items():self.stack.enter_context(patch.object(app,name,value))
        self.speech=self.stack.enter_context(patch.object(app,'speak'))

    def say(self,text,mode,typed=False):
        return app.handle_keyboard(text,mode) if typed else app.handle(text,mode)

    def test_long_mixed_keyboard_voice_document_persists_exactly(self):
        mode=self.say('create a document','awake')
        self.say('First line.',mode);self.say('new line',mode);self.say('Second line.',mode)
        self.say('select all',mode);self.say('scratch',mode)
        self.say('Replacement text.',mode);self.say('save document as Consumer report',mode)
        self.say('set font Arial',mode);self.say('set font size 14 point',mode);self.say('double space',mode)
        self.say('go to beginning of document',mode);self.say('read current word',mode)
        self.assertIn('Replacement',self.speech.call_args.args[0])
        source=app.app_context(mode)['source'];before=document_text(app.document)
        after=before+'\n'+' '.join('word'+str(i) for i in range(400))
        self.assertTrue(app.apply_keyboard_edit({'source':source,'before':before,'after':after,'caret':len(after),'seq':1},mode))
        self.assertEqual(self.say('leave document',mode),'document_save')
        self.assertEqual(self.say('yes','document_save'),'word_processor')
        reopened=VoiceDocument.open_existing(self.root/'Documents','Consumer report')
        self.assertEqual(document_text(reopened),after)
        self.assertEqual(reopened.paragraphs[0].font,'Arial');self.assertEqual(reopened.paragraphs[0].size,14)

    def test_note_selection_and_switching_tasks_preserve_text(self):
        mode=self.say('write a note','awake');self.say('one two three',mode)
        self.say('select all',mode);self.say('scratch',mode);self.say('Keep this reminder.',mode)
        self.assertEqual(self.say('create a document',mode),'document')
        self.assertIn('Keep this reminder.',(self.root/'notes.txt').read_text())
        self.assertNotIn('one two three',(self.root/'notes.txt').read_text())
        self.assertEqual(document_text(app.document),'')

    def test_sleep_keeps_document_help_and_note_contexts(self):
        mode=self.say('create a document','awake');self.say('Persistent paragraph.',mode)
        self.assertEqual(self.say('go to sleep',mode),'sleep');self.assertEqual(app.SLEEP_RETURN_MODE,'document')
        self.assertEqual(app.resume_from_sleep(),'document')
        self.assertIn('Persistent paragraph.',document_text(app.document))
        self.assertEqual(self.say('help','document'),'help')
        original=app.help_session
        self.assertEqual(self.say('go to sleep','help'),'sleep');self.assertEqual(app.resume_from_sleep(),'help')
        self.assertIs(app.help_session,original)
        self.assertEqual(self.say('close help','help'),'document')

    def test_all_main_menu_boundaries_survive_rapid_and_voice_navigation(self):
        rng=random.Random(498)
        for _ in range(600):
            key=rng.choice(('Up','Down','Home','End'))
            before=app.MAIN_MENU_INDEX;last=len(app.MAIN_MENU_CHOICES)-1
            expected=0 if key=='Home' else last if key=='End' else (0 if key=='Down' else last) if before is None else max(0,min(last,before+(1 if key=='Down' else -1)))
            request=app.navigation_key(key,'awake')
            if request:self.say(request,'awake',typed=True)
            self.assertEqual(app.MAIN_MENU_INDEX,expected)
            notice=self.speech.call_args.args[0]
            if before is not None and (key in ('Up','Home') and before==0 or key in ('Down','End') and before==last):self.assertEqual(notice,'Start of list.' if key in ('Up','Home') else 'End of list.')
            else:self.assertTrue(notice.startswith(app.MAIN_MENU_CHOICES[expected][0]));self.assertNotIn('of list',notice)

    def test_keyboard_and_voice_email_draft_switching_does_not_send(self):
        self.stack.enter_context(patch.object(app,'ProtectedStore',return_value=Mock(selected=Mock(return_value=('gmail','user@example.test')))))
        self.stack.enter_context(patch.object(app,'MailSession',return_value=Mock(folder_name='Inbox',pending=None,process=Mock(return_value='Inbox.'))))
        self.stack.enter_context(patch.object(app,'submit',side_effect=AssertionError('Unexpected send')))
        mode=self.say('new email','awake',typed=True)
        self.assertEqual(self.speech.call_args.args[0],'New email. To field.')
        self.say('sam@example.test',mode,typed=True);self.say('Status update',mode);self.say('This is a draft.',mode)
        self.say('go to subject',mode);self.say('read subject',mode)
        self.assertIn('Status update',self.speech.call_args.args[0])
        self.assertEqual(self.say('go to sleep',mode),'sleep');self.assertEqual(app.resume_from_sleep(),'email_draft')
        self.assertEqual(self.say('cancel','email_draft'),'mailbox')
        self.assertIsNone(app.email_draft);self.assertFalse(list((self.root/'Email Drafts').glob('*.eml')))

    def test_full_help_topics_and_subtopics_have_finite_keyboard_and_voice_lists(self):
        guide=Path(__file__).with_name('START HERE - Veteran.txt')
        help=HelpSession(guide);help.start()
        for _ in range(len(help.topics)+4):help.process('next')
        self.assertEqual(help.topic_index,len(help.topics)-1)
        for _ in range(len(help.topics)+4):help.process('previous')
        self.assertEqual(help.topic_index,0)
        self.stack.enter_context(patch.object(app,'help_session',help))
        app.navigation_key('End','help');self.assertEqual(help.topic_index,len(help.topics)-1)
        app.navigation_key('Home','help');self.assertEqual(help.topic_index,0)

    def test_all_tutorial_lessons_remain_isolated(self):
        tutorial=PracticeTutorial();before=list(self.root.rglob('*'))
        lessons={'radio':['practice radio','search radio','jazz','save preset','main menu'],
                 'email':['practice email','email','next message','mark message','delete message','no','main menu'],
                 'documents':['practice documents','write a document','hello world','read from top','main menu']}
        for commands in lessons.values():
            for command in commands:
                result,finished=tutorial.process(command);self.assertTrue(result)
            self.assertTrue(finished)
        self.assertEqual(list(self.root.rglob('*')),before)

class CatalogPlayer:
    def __init__(self):self.kind=None;self.calls=[]
    def open(self,url,title,kind):self.calls.append(('open',url,title,kind));self.kind=kind
    def control(self,action,seconds=15,position=None):self.calls.append((action,seconds,position));return position if position is not None else 0
    def close(self):self.kind=None

class ConsumerMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.player=CatalogPlayer()
        self.catalog=[{'name':'WGN One','url':'https://audio.test/one','country':'US'},{'name':'WGN Two','url':'https://audio.test/two','country':'US'}]
        self.hub=MediaHub(self.root,player=self.player,clock=lambda:datetime(2026,10,12,15,0))
        self.hub.fetch_json=lambda url:{'results':[{'collectionName':'Planet One','feedUrl':'https://feeds.test/one'},{'collectionName':'Planet Two','feedUrl':'https://feeds.test/two'}]} if 'itunes' in url else self.catalog
        self.hub.choose_source('radio browser')

    def test_radio_search_keyboard_play_preset_and_cancel_delete(self):
        nav=MediaNavigation(self.hub,'radio');nav.enter('radio');nav.keyboard('Ctrl+F');nav.field_value('WGN')
        self.assertEqual(len(self.hub.stations),2)
        self.assertNotIn('of list',nav.keyboard('End'));nav.keyboard('Enter')
        self.assertEqual(self.player.calls[-1][1],'https://audio.test/two')
        nav.command('save preset as Favorite');nav.command('list presets')
        self.assertNotIn('of list',nav.announce())
        nav.keyboard('Delete');self.assertIsNotNone(nav.pending);nav.keyboard('Alt+N')
        self.assertEqual(len(self.hub.presets()),1)
        reopened=MediaHub(self.root,player=self.player);self.assertEqual(reopened.presets()[0]['url'],'https://audio.test/two')

    def test_radio_and_podcast_initial_results_announce_boundaries(self):
        for section,term in (('radio','WGN'),('podcast','Planet')):
            nav=MediaNavigation(self.hub,section);nav.enter(section);nav.command('search')
            self.assertNotIn('of list',nav.command(term))
            self.assertNotIn('of list',nav.keyboard('End'))
            self.assertIn('End of list',nav.keyboard('Down'))

    def test_podcast_search_feed_episode_navigation_and_subscription_cancel(self):
        nav=MediaNavigation(self.hub,'podcast');nav.command('search');nav.command('Planet')
        episodes=[{'title':'First episode','url':'https://audio.test/a','show':'Planet Two'},{'title':'Last episode','url':'https://audio.test/b','show':'Planet Two'}]
        with patch.object(self.hub,'_feed_episodes',return_value=episodes),patch.object(self.hub,'start_podcast_refresh'):
            nav.keyboard('End');nav.command('subscribe');nav.keyboard('Enter')
            self.assertEqual(len(self.hub.episodes),2)
            nav.keyboard('End');nav.keyboard('Enter');self.assertEqual(self.player.kind,'podcast')
            nav.command('pause');nav.command('resume');nav.command('rewind 15 seconds')
            self.assertIn(('seek',-15,None),self.player.calls)
            nav.command('back to podcasts');nav.command('list subscriptions');nav.command('remove selected subscription')
            nav.keyboard('Alt+N');self.assertEqual(len(self.hub._load('podcast-subscriptions.json',[])),1)

    def test_media_action_navigation_never_wraps(self):
        for section in ('radio','podcast'):
            nav=MediaNavigation(self.hub,section);nav.focus='actions'
            for _ in range(100):nav.keyboard('Down')
            self.assertEqual(nav.index,len(nav.actions)-1)
            for _ in range(100):nav.command('previous action')
            self.assertEqual(nav.index,0);self.assertNotIn('of list',nav.announce());self.assertEqual(nav.keyboard('Up'),'Start of list.')

if __name__=='__main__':unittest.main()
