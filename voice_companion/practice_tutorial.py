"""Isolated, spoken practice with fictional data; no account or file writes."""

import re


class PracticeTutorial:
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
                        'Say practice radio, practice email, or practice documents. Say main menu to leave at any time.')

    def process(self, spoken):
        command = re.sub(r'[.!?]+$', '', spoken.casefold().strip())
        if command in ('main menu', 'back to main menu', 'exit tutorial', 'close tutorial'):
            return self.say('Practice ended. Back at the main menu.'), True
        if command in ('repeat', 'say that again', 'repeat instructions'):
            return self.previous, False
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
