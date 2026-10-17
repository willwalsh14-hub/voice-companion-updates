"""Consumer course navigation, command coverage and isolation from real actions."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from practice_tutorial import PracticeTutorial
from tutorial_curriculum import COURSES
from onboard_help import HelpSession
from settings_model import CATEGORIES, fields

class PracticeTutorialTests(unittest.TestCase):
    def test_every_app_has_task_lessons_and_complete_documented_reference(self):
        session=PracticeTutorial()
        self.assertEqual([c['name'] for c in session.courses],['Getting started','Word processor','Notepad','Email','Web browser','Radio','Podcasts','Options','Help and updates','File manager'])
        guide={t['key']:t for t in HelpSession(Path(__file__).with_name('START HERE - Veteran.txt')).topics}
        for course,definition in zip(session.courses,COURSES):
            self.assertGreaterEqual(len(course['lessons']),12,course['name'])
            self.assertGreaterEqual(len(course['commands']),15,course['name'])
            reference=' '.join(l['text'] for l in course['lessons'] if l['reference'])
            for source in definition[-1]:
                for paragraph in guide[source]['paragraphs']:self.assertIn(paragraph,reference)
        option_titles={l['title'] for l in session.courses[7]['lessons']}
        for category in CATEGORIES:
            for field in fields(category,{}):self.assertIn(category+': '+field.label,option_titles)

    def test_every_course_waits_repeats_and_supports_full_progression(self):
        for index,course in enumerate(PracticeTutorial().courses):
            with self.subTest(course=course['name']):
                session=PracticeTutorial();first=session.process(course['aliases'][0])[0]
                self.assertEqual(session.process('repeat')[0],first)
                self.assertIn('Start of lesson',session.process('previous lesson')[0])
                self.assertEqual(session.process('next lesson')[0],session._read_lesson(1))
                self.assertEqual(session.process('previous lesson')[0],first)
                for step in range(1,len(course['lessons'])):
                    result,closed=session.process('next lesson');self.assertFalse(closed);self.assertEqual(session.step,step);self.assertIn(course['lessons'][step]['title'],result)
                self.assertIn('End of lesson',session.process('next lesson')[0])

    def test_lesson_index_search_reference_and_back(self):
        session=PracticeTutorial();session.process('email')
        session.process('lesson list');self.assertEqual(session.level,'lessons')
        session.process('next');session.process('that one');self.assertEqual(session.step,1)
        session.process('back');self.assertEqual(session.level,'lessons')
        session.process('find lesson review and send');self.assertEqual(session.level,'matches')
        result,_=session.process('that one');self.assertIn('Review and send',result)
        session.process('list commands');self.assertEqual(session.level,'commands')
        session.process('that one');self.assertEqual(session.level,'reference')
        session.process('back');self.assertEqual(session.level,'commands')
        result,_=session.process('explain command send it');self.assertIn('nothing is sent before',' '.join(session.article).casefold())
        self.assertIn('did not change your work',result)
        session.process('courses');self.assertIsNone(session.lesson)

    def test_long_explanation_preserves_conditions_and_reads_all_parts(self):
        session=PracticeTutorial();session.process('word processor')
        session.process('explain command select word');self.assertGreater(len(session.article),1)
        text=[session.article[0]]
        while session.article_index+1<len(session.article):
            session.process('continue reading');text.append(session.article[session.article_index])
        self.assertIn('Undo',' '.join(text));self.assertIn('End of explanation',session.process('continue reading')[0])

    def test_keyboard_and_voice_choose_same_course_and_lesson(self):
        import companion as app
        for typed in (False,True):
            session=PracticeTutorial()
            with patch.object(app,'tutorial_session',session),patch.object(app,'MAIN_MENU_INDEX',None),patch.object(app,'NAMED_LIST',None),patch.object(app,'SYNTH_PICK_INDEX',None),patch.object(app,'VOICE_PICK_INDEX',None),patch.object(app,'speak'),patch.object(app,'sync_app_context'):
                dispatch=app.handle_keyboard if typed else app.handle
                dispatch('next','tutorial');self.assertEqual(session.course_index,0)
                dispatch(app.navigation_key('Enter','tutorial'),'tutorial');self.assertEqual(session.lesson,'basics')
                dispatch(app.navigation_key('Enter','tutorial'),'tutorial');self.assertEqual(session.step,1)
                dispatch(app.navigation_key('Escape','tutorial'),'tutorial');self.assertEqual(session.level,'lessons')
                app.navigation_key('End','tutorial');self.assertEqual(session.index,len(session.course()['lessons'])-1)
                app.navigation_key('Home','tutorial');self.assertEqual(session.index,0)
                dispatch('notepad','tutorial');self.assertEqual(session.lesson,'notepad')
                dispatch('options','tutorial');self.assertEqual(session.lesson,'options')
                dispatch('podcasts','tutorial');self.assertEqual(session.lesson,'podcasts')

    def test_real_work_is_untouched_by_commands_in_tutorial(self):
        import companion as app
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'keep.txt').write_text('Keep this')
            originals={'document':Mock(),'email_draft':Mock(),'mail_session':Mock(),'MEDIA_HUB':Mock(),'web_session':Mock()}
            stack=__import__('contextlib').ExitStack()
            with stack:
                for name,value in originals.items():stack.enter_context(patch.object(app,name,value))
                for name,value in {'APP':root,'TEXT_MODE':True,'APP_WINDOW':None,'NAMED_LIST':None,'SYNTH_PICK_INDEX':None,'VOICE_PICK_INDEX':None,'tutorial_session':None,'account_setup':False}.items():stack.enter_context(patch.object(app,name,value))
                stack.enter_context(patch.object(app,'speak'))
                mode=app.handle('start tutorial','awake')
                for phrase in ('email','send email','delete message','yes','notepad','save note','word processor','name document Example','radio','record station 1 for 30 minutes','podcasts','download episode 1','options','default font Arial','check for updates'):
                    self.assertEqual(app.handle(phrase,mode),'tutorial',phrase)
                for obj in originals.values():self.assertEqual(obj.mock_calls,[])
                self.assertEqual([p.name for p in root.iterdir()],['keep.txt'])
                self.assertEqual(app.handle('main menu',mode),'awake');self.assertIsNone(app.tutorial_session)

if __name__=='__main__':unittest.main()
