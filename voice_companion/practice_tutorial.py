"""Isolated, spoken practice with fictional data; no account or file writes."""

import re


class PracticeTutorial:
    CHOICES=(('Getting started','getting started'),('Web browser','practice web browser'),('Word processor','practice documents'),('Email','practice email'),('Radio','practice radio'))
    BASICS=(
        'Getting started, lesson one. Companion speaks for itself. Say wake up to begin, and go to sleep when you want it to ignore ordinary speech. Sleep keeps your place. Say next lesson or press Enter to continue.',
        'Moving around. In menus, say next or previous, then okay to open an item. With the keyboard, use Up or Down and Enter. Escape goes back. Control stops speech. Say next lesson or press Enter.',
        'Writing. Open Word processor and speak a sentence. Companion adds it without reading it back. Say read document when you want to review. Say undo to undo your last change. Say next lesson or press Enter.',
        'Naming and saving. In Word processor, press F2, type a name, and press Enter. By voice, say name document followed by the name. Say save document to save. Say next lesson or press Enter.',
        'Options and help. Alt O opens Options. Choose Voice options to change rate, pitch, or volume and hear a sample before saving. Escape cancels the category. Say help with web browsing for instructions. Getting started is finished. Say practice web browser, practice documents, practice email, practice radio, or main menu.'
    )
    WEB=(
        'Web browser lesson one. A page is read with a virtual cursor, not like an app menu. Press Control L, type a website such as controlaltsuccess dot com, and press Enter. A typed address needs dots, not the word dot. By voice, say go to control alt success dot com. Say next lesson or press Enter.',
        'Finding your place. Press H for the next heading, or Shift H for the previous heading. K moves to the next link. By voice, say next heading or next link. Arrow keys then read from that new position. Say next lesson or press Enter.',
        'Opening things. Press Enter on a link or button. If Companion asks for confirmation, answer yes or no. By voice, say find contact us and confirm the result. Say next lesson or press Enter.',
        'Forms. Tab moves to the next field; Shift Tab goes back. Type or dictate your answer. Up and Down change a drop-down selection. Keyboard Send does not start voice review. By voice, say fill out this form for one question at a time. Say next lesson or press Enter.',
        'Voice review. Say review and submit form. Say continue review to hear the next answer. To correct an answer, say change this field or change followed by its name. Answer the question, review again, and say yes only when ready. Web browser lesson finished. Say getting started, practice documents, practice email, practice radio, or main menu.'
    )
    def __init__(self):
        self.lesson = None
        self.step = 0
        self.previous = ''
        self.preset_saved = False
        self.marked = False

    def say(self, message):
        self.previous = message
        return message

    def start(self):
        return self.say('Practice tutorial. Nothing here changes your real email, documents, or radio presets. '
                        'Choose Getting started, Web browser, Word processor, Email, or Radio with arrows and Enter. '
                        'By voice, say getting started, practice web browser, practice documents, practice email, or practice radio. '
                        'Lessons wait for you. Say repeat to hear the current instruction again, or main menu to leave.')

    def process(self, spoken):
        command = re.sub(r'[.!?]+$', '', spoken.casefold().strip())
        if command in ('main menu', 'back to main menu', 'exit tutorial', 'close tutorial'):
            return self.say('Practice ended. Back at the main menu.'), True
        if command in ('repeat', 'say that again', 'repeat instructions'):
            return self.previous, False
        if command in ('getting started','practice basics','basics lesson','practice web browser','web browser lesson','practice browser'):
            self.lesson='basics' if command in ('getting started','practice basics','basics lesson') else 'web'
            self.step=0
            return self.say((self.BASICS if self.lesson=='basics' else self.WEB)[0]),False
        if self.lesson in ('basics','web') and command in ('next','next lesson','continue','continue lesson','ok','okay','confirm','previous','previous lesson'):
            lessons=self.BASICS if self.lesson=='basics' else self.WEB
            step=max(0,min(len(lessons)-1,self.step+(-1 if command.startswith('previous') else 1)))
            if step==self.step:return self.say('Start of lesson.' if step==0 else 'End of lesson. Say main menu to leave.'),False
            self.step=step;return self.say(lessons[step]),False
        if command in ('practice radio', 'radio lesson', 'radio'):
            self.lesson, self.step, self.preset_saved = 'radio', 0, False
            return self.say('Radio practice. Imagine a station called W G N. Say search W G N.'), False
        if command in ('practice email', 'email lesson', 'email'):
            self.lesson, self.step, self.marked = 'email', 0, False
            return self.say('Email practice. These are fictional messages. Say go to inbox.'), False
        if command in ('practice documents', 'document lesson', 'documents'):
            self.lesson, self.step = 'documents', 0
            return self.say('Document practice. Say write a document.'), False
        if self.lesson == 'radio':
            if self.step == 0 and command in ('search w g n', 'search wgn', 'search radio for wgn'):
                self.step = 1
                return self.say('Practice result 1: W G N. Say save favorite or save preset.'), False
            if self.step == 1 and re.fullmatch(r'(?:add|save) (?:preset|favorite)(?: as .+)?', command):
                self.preset_saved, self.step = True, 2
                return self.say('Practice preset saved. Say list presets.'), False
            if self.step == 2 and command in ('list presets', 'preset list', 'list favorites', 'favorites list'):
                self.step = 3
                return self.say('W G N, 1 of 1. Say that one to play it.'), False
            if self.step == 3 and command in ('that one', 'okay', 'confirm', 'confirm that', 'play preset 1'):
                self.step = 4
                return self.say('Playing practice station W G N. No real audio stream opened. '
                                'Say delete preset to practice removing it.'), False
            if self.step == 4 and command == 'delete preset':
                self.step = 5
                return self.say('Delete practice preset W G N? Say yes or no.'), False
            if self.step == 5 and command in ('yes', 'no'):
                self.preset_saved = command != 'yes'
                self.step = 6
                return self.say(('Practice preset deleted.' if command == 'yes' else 'Deletion canceled.') +
                                ' Radio lesson finished. Say practice email, practice documents, or main menu.'), False
        if self.lesson == 'email':
            if self.step == 0 and command in ('go to inbox', 'open inbox', 'email'):
                self.step = 1
                return self.say('Inbox. From Sam. Subject: Hello. 1 of 2. Say next message.'), False
            if self.step == 1 and command == 'next message':
                self.step = 2
                return self.say('From Pat. Subject: Lunch. 2 of 2. Say select or mark.'), False
            if self.step == 2 and command in ('select', 'mark', 'select message', 'mark message'):
                self.marked, self.step = True, 3
                return self.say('One practice message selected. Say delete.'), False
            if self.step == 3 and command in ('delete', 'delete message'):
                self.step = 4
                return self.say('Move the selected practice message to Trash? Say yes or no.'), False
            if self.step == 4 and command in ('yes', 'no'):
                self.marked = False
                self.step = 5
                return self.say(('Practice message deleted. Inbox refreshed. Message 1 of 1: From Sam, subject Hello.'
                                 if command == 'yes' else 'Deletion canceled. The practice inbox is unchanged.') +
                                ' Email lesson finished. Say practice radio, practice documents, or main menu.'), False
        if self.lesson == 'documents':
            if self.step == 0 and command in ('write a document', 'new document'):
                self.step = 1
                return self.say('A practice document is open. Say hello world.'), False
            if self.step == 1 and command == 'hello world':
                self.step = 2
                return self.say('Practice text added: hello world. Say read from top.'), False
            if self.step == 2 and command in ('read from top', 'start reading', 'read document'):
                self.step = 3
                return self.say('Hello world. Document lesson finished. Say practice radio, practice email, or main menu.'), False
        return self.say('This is practice; no real action was taken. Say repeat for the last instruction, '
                        'practice radio, practice email, practice documents, or main menu.'), False
