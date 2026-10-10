"""Spoken, hierarchical access to the same guide shipped in accessible file formats."""
from list_announcements import name_first

import re
from pathlib import Path

from reading_navigation import ReadingCursor, reading_request


SUBTOPICS = {
    'AI VOICES': [('Choose an AI voice', 0, 1), ('Using AI narration', 1, 2),
                  ('Connection and fallback', 2, 3), ('Trainer setup', 3, 4)],
    'WRITE A DOCUMENT': [('Getting started', 0, 4), ('Reading and editing', 4, 12),
                         ('Selecting text', 12, 14), ('Formatting', 14, 18),
                         ('Saving and finding documents', 18, 23)],
    'WRITE AN EMAIL DRAFT': [('Accounts', 0, 3), ('Recipients and contacts', 3, 8),
                             ('Writing and editing', 8, 14), ('Review and send', 14, 15)],
    'READ AND ORGANIZE EMAIL - HELPER MUST CONNECT AND TEST THE ACCOUNT FIRST': [
        ('Read messages', 0, 1), ('Go to folders', 1, 2),
        ('Mark, move, and delete messages', 2, 3), ('Reply and forward', 3, 4),
        ('Create and manage folders', 4, 5)],
    'WEB BROWSING': [('Search and links', 0, 2), ('Choose a browser', 2, 3),
                     ('Favorites and filling forms', 3, 4), ('Review and submit forms', 4, 5)],
    'FIND AND PLAY PODCASTS': [('Search for a show', 0, 1), ('Browse episodes', 1, 2)],
    'SAVE AND PLAY RADIO PRESETS': [('Save a preset', 0, 1),
                                     ('Play saved stations', 1, 2), ('Delete a preset', 2, 3)],
}


class HelpSession:
    def __init__(self, path, release_notes_path=None):
        lines = Path(path).read_text(encoding='utf-8-sig').splitlines()
        self.topics = []
        current = None
        for line in lines[1:]:
            value = line.strip()
            if not value:
                continue
            if value == value.upper() and re.fullmatch(r'[A-Z0-9 ,.-]+', value):
                current = {'key': value, 'name': value.split(' - HELPER')[0].title(), 'paragraphs': []}
                self.topics.append(current)
            else:
                if current is None:
                    current = {'key': 'ABOUT THIS TEST', 'name': 'About this test', 'paragraphs': []}
                    self.topics.append(current)
                current['paragraphs'].append(value)
        if not self.topics:
            raise ValueError('The user guide has no topics.')
        if release_notes_path is not None and Path(release_notes_path).is_file():
            notes=Path(release_notes_path).read_text(encoding='utf-8-sig')
            self.topics.append({'key':'WHATS NEW','name':"What's new",'paragraphs':[p.strip() for p in notes.split('\n\n') if p.strip()]})
        self.topic_index = 0
        self.subtopic_index = 0
        self.level = 'topics'
        self.reading = ReadingCursor()
        self.closed = False

    def _subtopics(self):
        topic = self.topics[self.topic_index]
        result = []
        for name, start, end in SUBTOPICS.get(topic['key'], []):
            paragraphs = topic['paragraphs'][start:end]
            if paragraphs:
                result.append((name, paragraphs))
        return result

    def _item(self):
        if self.level == 'topics':
            return (name_first('Topic ' + str(self.topic_index + 1) + ' of ' + str(len(self.topics)) +
                    ': ' + self.topics[self.topic_index]['name'] +
                    '. Say next or previous, then that one. Say help with followed by a topic name to jump there.'))
        subs = self._subtopics()
        return (name_first('Subtopic ' + str(self.subtopic_index + 1) + ' of ' + str(len(subs)) +
                ': ' + subs[self.subtopic_index][0] +
                '. Say next or previous, then that one to read it. Say back to topics for the main list.'))

    def start(self):
        return 'User guide. ' + self._item()

    def topic(self, name):
        wanted = name.casefold().strip()
        aliases = {'what is new':"what's new",'whats new':"what's new",'release notes':"what's new",'email': 'read and organize email', 'mail': 'read and organize email',
                   'email commands': 'read and organize email',
                   'documents': 'write a document', 'document': 'write a document',
                   'document commands': 'write a document',
                   'web': 'web browsing', 'browser': 'web browsing',
                   'web commands': 'web browsing', 'forms': 'web browsing',
                   'radio': 'find and play radio', 'podcasts': 'find and play podcasts',
                   'podcast': 'find and play podcasts', 'writing email': 'write an email draft',
                   'email draft': 'write an email draft', 'voice': 'speech controls',
                   'speech': 'speech controls', 'ai voice': 'ai voices', 'ai': 'ai voices',
                   'ai voice setup': 'ai voices', 'voice setup': 'ai voices', 'reading': 'reading text',
                   'commands': 'commands and modes', 'input modes': 'voice input modes',
                   'contacts': 'write an email draft', 'folders': 'read and organize email',
                   'presets': 'save and play radio presets', 'radio presets': 'save and play radio presets',
                   'settings': 'self-voicing settings', 'verbosity': 'self-voicing settings', 'keyboard': 'keyboard navigation',
                   'email lists': 'email list size and pages', 'station database': 'choose a radio station source'}
        wanted = aliases.get(wanted, wanted)
        matches = [i for i, topic in enumerate(self.topics)
                   if topic['name'].casefold() == wanted or topic['name'].casefold().startswith(wanted)]
        if len(matches) != 1:
            return 'I could not identify one help topic. Say back to topics to browse the list.'
        self.topic_index = matches[0]
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
        return title + '. ' + self.reading.read() + ' Say back to subsections or back to topics when done.'

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
            if self.level == 'article' and self._subtopics():
                self.level = 'subtopics'
            else:
                self.level = 'topics'
            return self._item()
        if command.startswith(('help with ', 'help for ', 'go to help topic ', 'open help topic ')):
            name = re.sub(r'^(?:help (?:with|for)|(?:go to|open) help topic)\s+', '', command)
            return self.topic(name)
        if self.level == 'article':
            if command in ('read topic', 'read this topic', 'repeat topic'):
                return self.reading.read(from_top=True)
            request = reading_request(command)
            if request:
                unit, direction = request
                return (self.reading.read(direction == 'top') if unit == 'read'
                        else self.reading.move(unit, direction))
            return 'Say continue reading, next paragraph, back to subsections, back to topics, or close help.'
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
        return self._item()

