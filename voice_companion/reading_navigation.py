"""Shared spoken reading position for documents, mail, and web pages."""
import re


class ReadingCursor:
    def __init__(self):
        self.text = ''
        self.position = 0
        self.continuation = 0

    def set_text(self, value, reset=False):
        value = str(value or '')
        if value != self.text:
            self.text = value
            self.position = 0 if reset else min(self.position, max(0, len(value) - 1))
            self.continuation = 0
        elif reset:
            self.position = self.continuation = 0

    def _spans(self, unit):
        pattern = {'character': r'[^\n]|\n', 'word': r'\S+',
                   'line': r'[^\n]*\n|[^\n]+$',
                   'sentence': r'\S.*?(?:[.!?](?=\s|$)|(?=\n)|$)',
                   'paragraph': r'[^\n]+|(?<=\n)(?=\n)' }[unit]
        return [m for m in re.finditer(pattern, self.text, re.DOTALL) if m.group().strip()]

    def move(self, unit, direction):
        spans = self._spans(unit)
        if not spans: return 'There is no text to read.'
        index = next((i for i, span in enumerate(spans) if span.start() <= self.position < span.end()), None)
        if index is None:
            index = next((i for i, span in enumerate(spans) if span.start() > self.position), len(spans)-1)
            if direction < 0: index += 1
        index = max(0, min(len(spans)-1, index + direction))
        self.position = spans[index].start()
        self.continuation = self.position
        return unit.capitalize() + ' ' + str(index+1) + ' of ' + str(len(spans)) + '. ' + spans[index].group().strip()

    def read(self, from_top=False):
        if not self.text.strip(): return 'There is no text to read.'
        if from_top: self.position = self.continuation = 0
        if self.continuation >= len(self.text): return 'End of text.'
        end = min(self.continuation + 1200, len(self.text))
        if end < len(self.text):
            boundary = self.text.rfind(' ', self.continuation + 900, end)
            if boundary > self.continuation: end = boundary
        piece = self.text[self.continuation:end].strip()
        self.position = self.continuation
        self.continuation = end
        return piece + (' Say continue reading for more.' if end < len(self.text) else ' End of text.')


def reading_request(command):
    command = command.lower().strip()
    if command in ('start reading','read from top','read from beginning','read all from top',
                   'read document','read email draft'):
        return ('read', 'top')
    if command in ('continue reading','read more'):
        return ('read', 'continue')
    match = re.fullmatch(r'(previous|next|read|current) (character|letter|word|line|sentence|paragraph)', command)
    if match:
        return (match[2].replace('letter','character'), {'previous':-1,'next':1,'read':0,'current':0}[match[1]])
    return None
