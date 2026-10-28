"""Spoken, hierarchical access to the same guide shipped in accessible file formats."""
from list_announcements import name_first

import re
from pathlib import Path

from reading_navigation import ReadingCursor, reading_request


class HelpReadingCursor(ReadingCursor):
    """Read complete blocks without advancing past text that has not been requested."""
    def read(self, from_top=False):
        if from_top:self.position=self.continuation=0
        spans=self._spans('paragraph')
        span=next((s for s in spans if s.end()>self.continuation),None)
        if span is None:return 'End of topic.'
        self.position=span.start();self.continuation=span.end()
        return span.group().strip()

    def move(self,unit,direction):
        spans=self._spans(unit)
        if not spans:return 'There is no text to read.'
        boundary='Start of topic. ' if direction<0 and self.position<=spans[0].start() else 'End of topic. ' if direction>0 and self.position>=spans[-1].start() else ''
        value=super().move(unit,direction)
        if unit=='paragraph':
            self.continuation=next(s.end() for s in spans if s.start()==self.position)
        return boundary+value




class HelpSession:
    def __init__(self, path, release_notes_path=None):
        from guide_text import read_guide
        _, front, chapters = read_guide(path)
        self.topics = [{'key':'ABOUT THIS GUIDE','name':'About this guide','paragraphs':front,'parts':[]}]
        self.topics.extend({'key':c['key'],'name':c['name'],'paragraphs':c['paragraphs'],
                            'parts':c['parts']} for c in chapters)
        if not self.topics:
            raise ValueError('The user guide has no topics.')
        if release_notes_path is not None and Path(release_notes_path).is_file():
            notes=Path(release_notes_path).read_text(encoding='utf-8-sig')
            self.topics.append({'key':'WHATS NEW','name':"What's new",'paragraphs':[p.strip() for p in notes.split('\n\n') if p.strip()]})
        self.topic_index = 0
        self.subtopic_index = 0
        self.level = 'topics'
        self.reading = HelpReadingCursor()
        self.closed = False

    def _subtopics(self):
        topic = self.topics[self.topic_index]
        return [(p['name'],p['paragraphs']) for p in topic.get('parts',[])]

    def _item(self):
        if self.level == 'topics':
            return (name_first('Topic ' + str(self.topic_index + 1) + ' of ' + str(len(self.topics)) +
                    ': ' + self.topics[self.topic_index]['name'] +
                    '. Say next or previous, then that one. Say help with followed by a topic name to jump there.'))
        subs = self._subtopics()
        from menu_navigation import announce_item
        return (announce_item(subs[self.subtopic_index][0],self.subtopic_index,len(subs))+
                ' Say next or previous, then that one to read it. Say back to topics for all chapters.')

    def start(self):
        return 'User guide. ' + self._item()

    def topic(self, name):
        wanted = name.casefold().strip()
        aliases = {
            'files':'file manager','file manager':'file manager','file commands':'file manager','reader':'document reader','document reader':'document reader',
            'what is new':"what's new", 'whats new':"what's new", 'what s new':"what's new", 'release notes':"what's new",
            'email':'read and organize email', 'mail':'read and organize email', 'email commands':'read and organize email',
            'documents':'write a document', 'document':'write a document', 'document commands':'write a document',
            'web':'browse the web', 'browser':'browse the web', 'web browser':'browse the web', 'web commands':'browse the web', 'forms':'browse the web',
            'bookmarks':'favorites and web history', 'favorites':'favorites and web history', 'history':'favorites and web history',
            'radio':'listen to radio', 'podcasts':'listen to podcasts', 'podcast':'listen to podcasts',
            'writing email':'write an email', 'email draft':'write an email',
            'voice':'speech and voices', 'speech':'speech and voices', 'ai voice':'speech and voices', 'ai':'speech and voices',
            'ai voice setup':'speech and voices', 'voice setup':'speech and voices',
            'reading':'reading and editing text', 'commands':'using voice commands', 'input modes':'using voice commands',
            'contacts':'contacts', 'address book':'contacts', 'contact list':'contacts', 'accounts':'set up email', 'folders':'read and organize email',
            'calendar':'calendar', 'events':'calendar', 'appointments':'calendar', 'schedule':'calendar',
            'presets':'saved radio stations', 'radio presets':'saved radio stations',
            'settings':'change your options', 'options':'change your options', 'verbosity':'change your options',
            'keyboard':'using voice commands', 'email lists':'read and organize email', 'station database':'listen to radio',
            'notepad':'write a note', 'notes':'write a note', 'updates':'check for updates', 'tutorial':'using help and lessons',
            'downloads':'download files', 'download manager':'download files', 'download':'download files', 'download folder':'download files',
            'attachments':'read and organize email', 'attachment':'read and organize email', 'email attachments':'read and organize email',
            'display':'large text and colors', 'display options':'large text and colors', 'large text':'large text and colors',
            'text size':'large text and colors', 'colors':'large text and colors', 'colours':'large text and colors',
            'color scheme':'large text and colors', 'contrast':'large text and colors', 'high contrast':'large text and colors',
            'magnifier':'large text and colors', 'magnification':'large text and colors', 'zoom':'large text and colors',
            'low vision':'large text and colors', 'screen':'large text and colors', 'full screen':'large text and colors',
        }
        # Help on these screens opens their own part of a chapter, not its first part.
        part = {'attachments': 'open and save attachments', 'attachment': 'open and save attachments',
                'email attachments': 'open and save attachments'}.get(wanted)
        wanted = aliases.get(wanted, wanted)
        matches = [i for i, topic in enumerate(self.topics)
                   if topic['name'].casefold() == wanted or topic['name'].casefold().startswith(wanted)]
        if len(matches) != 1:
            return 'I could not identify one help topic. Say back to topics to browse the list.'
        self.topic_index = matches[0]
        subs = self._subtopics()
        found = [i for i, (name, _) in enumerate(subs) if part and name.casefold() == part]
        if found:
            self.subtopic_index = found[0]
            return self._open_article(subs[found[0]][1], subs[found[0]][0])
        return self._open_topic()

    def _open_topic(self):
        subs = self._subtopics()
        if subs:
            self.level = 'subtopics'
            self.subtopic_index = 0
            return self.topics[self.topic_index]['name'] + '. ' + self._item()
        return self._open_article(self.topics[self.topic_index]['paragraphs'],
                                  self.topics[self.topic_index]['name'])

    def _open_article(self, paragraphs, title):
        self.level = 'article'
        self.reading.set_text('\n\n'.join(paragraphs), reset=True)
        return title + '. ' + self.reading.read() + ' Say back to subsections for the chapter parts, or back to topics for all chapters.'

    def process(self, text):
        command = text.casefold().strip().rstrip('.!?')
        if command in ('main menu', 'back to main menu'):
            self.closed = True
            return 'Back at the main menu.'
        if command in ('close help', 'exit help', 'leave help', 'back to app'):
            self.closed = True
            return 'Help closed. Back where you were.'
        if command in ('back to topics', 'help topics', 'list help topics', 'list topics'):
            self.level = 'topics'
            return self._item()
        if command in ('back to email topics', 'email topics'):
            return self.topic('email')
        if command in ('back to subsections', 'list subsections', 'subsections'):
            if self._subtopics():
                self.level = 'subtopics'
                return self._item()
            self.level = 'topics'
            return self._item()
        if command in ('go back', 'back'):
            if self.level=='topics':
                self.closed=True;return 'Help closed. Back where you were.'
            if self.level == 'article' and self._subtopics():
                self.level = 'subtopics'
            else:
                self.level = 'topics'
            return self._item()
        if command.startswith(('help with ', 'help for ', 'go to help topic ', 'open help topic ')):
            name = re.sub(r'^(?:help (?:with|for)|(?:go to|open) help topic)\s+', '', command)
            return self.topic(name)
        if self.level == 'article':
            command={'next':'next paragraph','down':'next paragraph','move down':'next paragraph',
                     'previous':'previous paragraph','up':'previous paragraph','move up':'previous paragraph'}.get(command,command)
            if command in ('first block','last block'):
                spans=self.reading._spans('paragraph')
                self.reading.position=spans[-1 if command=='last block' else 0].start() if spans else 0
                return self.reading.move('paragraph',0)
            if command in ('read topic', 'read this topic', 'repeat topic'):
                return self.reading.read(from_top=True)
            request = reading_request(command)
            if request:
                unit, direction = request
                return (self.reading.read(direction == 'top') if unit == 'read'
                        else self.reading.move(unit, direction))
            return None
        if command in ('next', 'next topic', 'next subsection', 'move down', 'down',
                       'previous', 'previous topic', 'previous subsection', 'move up', 'up'):
            step = -1 if command.startswith(('previous', 'move up')) or command == 'up' else 1
            index_name = 'topic_index' if self.level == 'topics' else 'subtopic_index'
            length = len(self.topics) if self.level == 'topics' else len(self._subtopics())
            index = getattr(self, index_name) + step
            if not 0 <= index < length:
                return 'That is the ' + ('first' if step < 0 else 'last') + ' item. ' + self._item()
            setattr(self, index_name, index)
            return self._item()
        if command in ('that one', 'okay', 'ok', 'confirm', 'confirm that', 'open topic', 'open it'):
            if self.level == 'topics': return self._open_topic()
            name, paragraphs = self._subtopics()[self.subtopic_index]
            return self._open_article(paragraphs, name)
        return None

    def not_understood(self):
        """The short answer to words Help does not know: what to say, and how Escape leaves."""
        if self.level == 'topics':
            return 'I did not understand that. Say next to hear a topic, or press Escape to leave Help.'
        if self.level == 'article':
            return 'I did not understand that. Say next to keep reading, or press Escape to go back.'
        return 'I did not understand that. Say next to hear a part, or press Escape to go back.'

    @staticmethod
    def accepts(text):
        command=text.casefold().strip().rstrip('.!?')
        return bool(reading_request(command) or command.startswith(('help with ','help for ','go to help topic ','open help topic ')) or command in (
            'main menu','back to main menu','close help','exit help','leave help','back to app',
            'back to topics','help topics','list help topics','list topics','back to email topics','email topics',
            'back to subsections','list subsections','subsections','go back','back',
            'read topic','read this topic','repeat topic','first block','last block',
            'next','next topic','next subsection','move down','down','previous','previous topic',
            'previous subsection','move up','up','that one','okay','ok','confirm','confirm that','open topic','open it',
            'help','user guide','open user guide','manual','open manual'))

