"""Indexed spoken instruction and command lookup; never executes app actions."""
import re
from tutorial_curriculum import COURSES, build_courses, split_instruction


class PracticeTutorial:
    CHOICES=tuple((name,aliases[0]) for _,name,aliases,_,_ in COURSES)

    def __init__(self, guide=None):
        self.courses=build_courses(guide)
        self.lesson=None
        self.course_index=None
        self.index=None
        self.step=0
        self.level='courses'
        self.previous=''
        self.matches=[]
        self.article=[]
        self.article_index=0
        self.return_level='lessons'

    def say(self,message):
        self.previous=message
        return message

    def start(self):
        return self.say('Audio tutorial. Thorough spoken courses cover Getting started, Word processor, Notepad, Email, Web browser, Radio, Podcasts, Options, and Help and updates. '
                        'This is instruction, not a simulation. App commands are explained here, never executed. '
                        'Use Up and Down to select a course and Enter to open it, or say its name. '
                        'Lessons wait for you. Say next lesson, previous lesson, repeat, lesson list, list commands, find lesson followed by a topic, or explain command followed by its words. '
                        'Control stops speech. If needed, say stop talking before your next voice request. Main menu leaves the tutorial.')

    def course(self):return self.courses[self.course_index if self.course_index is not None else 0]

    def menu(self):
        if self.level=='courses':return [c['name'] for c in self.courses],self.course_index,self._select_course
        if self.level=='lessons':return [l['title'] for l in self.course()['lessons']],self.index,self._select_index
        if self.level=='commands':return [c['title'] for c in self.course()['commands']],self.index,self._select_index
        if self.level=='matches':return [c['name']+': '+l['title'] for c,l in self.matches],self.index,self._select_index
        return None

    def _select_course(self,index):self.course_index=index
    def _select_index(self,index):self.index=index

    def _item(self):
        names,index,_=self.menu()
        if not names:return 'No commands are documented in this course.'
        index=0 if index is None else index
        return names[index]+'. '+str(index+1)+' of '+str(len(names))+'. Press Enter or say that one to open. '

    def _open_course(self,index):
        self.course_index=index;self.lesson=self.course()['key'];self.step=0;self.level='article';self.index=0
        return self._read_lesson(0)

    def _read_lesson(self,index):
        lesson=self.course()['lessons'][index];self.step=index;self.level='article';self.return_level='lessons'
        self.article=split_instruction(lesson['text']);self.article_index=0
        return self.say(self.course()['name']+'. Lesson '+str(index+1)+' of '+str(len(self.course()['lessons']))+'. '+lesson['title']+'. '+self.article[0]+' Say next lesson, repeat, or lesson list.')

    def _reference(self,entry):
        self.return_level=self.level if self.level in ('commands','matches') else 'commands'
        self.level='reference';self.article=split_instruction(entry['text']);self.article_index=0
        return self.say('Command explanation: '+entry['title']+'. '+self.article[0]+(' Say continue reading for the rest of this explanation.' if len(self.article)>1 else '')+' This command was explained, not executed.')

    def _find(self,query,commands=False):
        wanted=query.casefold().strip().rstrip('.!?')
        if not wanted:return self.say('Give a topic after find lesson or a command after explain command.')
        ordered=[self.course()]+[c for c in self.courses if c is not self.course()]
        if commands:
            exact=[entry for c in ordered for entry in c['commands'] if entry['title'].casefold()==wanted]
            if exact:return self._reference(exact[0])
        self.matches=[]
        for course in ordered:
            entries=course['commands'] if commands else course['lessons']
            for entry in entries:
                if wanted in entry['title'].casefold() or wanted in entry['text'].casefold():self.matches.append((course,entry))
        # A literal app phrase can be explained even when the guide uses it
        # without quotes: search instructional lesson text, never dispatch it.
        if not self.matches and commands:
            for course in ordered:
                for entry in course['lessons']:
                    if wanted in entry['text'].casefold():self.matches.append((course,entry))
        if not self.matches:return self.say('No explanation found for '+query+'. Say lesson list, list commands, or find lesson followed by a subject. No app action was taken.')
        self.level='matches';self.index=0;self.search_commands=commands
        return self.say(str(len(self.matches))+' matching explanations. '+self._item())

    def process(self,spoken):
        command=re.sub(r'[.!?]+$','',spoken.casefold().strip())
        if command in ('main menu','back to main menu','exit tutorial','close tutorial'):
            return self.say('Tutorial closed. Back at the main menu.'),True
        if command in ('repeat','say that again','repeat instructions','repeat lesson'):return self.previous,False
        for index,course in enumerate(self.courses):
            if command in course['aliases'] or command==course['name'].casefold():return self._open_course(index),False
        if command in ('courses','course list','list courses','back to courses'):
            self.level='courses';self.lesson=None
            return self.say('Tutorial courses. '+self._item()),False
        if command in ('lesson list','list lessons','back to lessons','lessons'):
            if self.lesson is None:return self.say('Choose a course first. '+self._item()),False
            self.level='lessons';self.index=self.step
            return self.say(self.course()['name']+' lessons. '+self._item()),False
        if command in ('list commands','command list','commands','back to commands'):
            if self.lesson is None:return self.say('Choose a course first to hear its command list.'),False
            self.level='commands';self.index=0
            return self.say(self.course()['name']+' command reference. '+self._item()),False
        match=re.match(r'^(?:find lesson|find topic|search lessons|find command|explain command)\s+(.+)$',command)
        if match:return self._find(match[1],commands=command.startswith(('find command','explain command'))),False
        if command in ('go back','back','escape'):
            if self.level=='courses':return self.say('Tutorial closed. Back at the main menu.'),True
            if self.level in ('article','reference'):
                self.level=self.return_level;self.index=self.step if self.level=='lessons' else (self.index or 0)
            else:self.level='courses';self.lesson=None
            return self.say(self._item()),False
        match=re.fullmatch(r'(?:lesson|open lesson|go to lesson) (.+)',command)
        if match and self.lesson:
            from speech_controls import integer
            number=integer(match[1])
            index=(number or 0)-1
            if 0<=index<len(self.course()['lessons']):return self._read_lesson(index),False
            return self.say('That lesson number is not in this course. Say lesson list.'),False
        if self.level in ('article','reference') and command in ('continue reading','continue explanation'):
            if self.article_index+1>=len(self.article):return self.say('End of explanation. Say next lesson, lesson list, or courses.'),False
            self.article_index+=1
            return self.say(self.article[self.article_index]+(' Say continue reading for more.' if self.article_index+1<len(self.article) else ' End of explanation.')),False
        if self.level in ('article','reference') and command in ('next','next lesson','continue','continue lesson','ok','okay','confirm','confirm that','that one','previous','previous lesson'):
            index=self.step+(-1 if command.startswith('previous') else 1)
            if not 0<=index<len(self.course()['lessons']):return self.say('Start of lesson list.' if index<0 else 'End of lesson list. Say courses to choose another app, or main menu to leave.'),False
            return self._read_lesson(index),False
        if self.menu():
            names,index,select=self.menu()
            if command in ('next','previous','next lesson','previous lesson'):
                step=-1 if command.startswith('previous') else 1
                target=(0 if step>0 else len(names)-1) if index is None else index+step
                if not 0<=target<len(names):return self.say('Start of list.' if step<0 else 'End of list.'),False
                select(target);return self.say(self._item()),False
            if command in ('that one','okay','ok','confirm','confirm that','open lesson','open it'):
                index=index or 0
                if self.level=='courses':return self._open_course(index),False
                if self.level=='lessons':return self._read_lesson(index),False
                if self.level=='commands':return self._reference(self.course()['commands'][index]),False
                course,entry=self.matches[index];self.course_index=self.courses.index(course);self.lesson=course['key']
                if getattr(self,'search_commands',False):return self._reference(entry),False
                return self._read_lesson(course['lessons'].index(entry)),False
        # Exact phrases get their documented explanation instead of a fake result.
        return self._find(command,commands=True),False
