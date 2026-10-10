"""Shared spoken reading position for documents, mail, and web pages."""
import re
from bisect import bisect_right


class ReadingCursor:
    def __init__(self):
        self.text = ''
        self.position = 0
        self.continuation = 0
        self._cache={}

    def set_text(self, value, reset=False):
        value = str(value or '')
        if value != self.text:
            self.text = value
            self.position = 0 if reset else min(self.position, max(0, len(value) - 1))
            self.continuation = 0
            self._cache={}
        elif reset:
            self.position = self.continuation = 0

    def _spans(self, unit):
        if unit in self._cache:return self._cache[unit]
        pattern = {'character': r'[^\n]|\n', 'word': r'\S+',
                   'line': r'[^\n]*\n|[^\n]+$',
                   'sentence': r'\S.*?(?:[.!?](?=\s|$)|(?=\n)|$)',
                   'paragraph': r'[^\n]+|(?<=\n)(?=\n)' }[unit]
        spans=[m for m in re.finditer(pattern,self.text,re.DOTALL) if unit in ('character','line') or m.group().strip()]
        self._cache[unit]=spans
        return spans

    def move(self, unit, direction):
        spans = self._spans(unit)
        if not spans: return 'There is no text to read.'
        starts_key=unit+':starts'
        if starts_key not in self._cache:self._cache[starts_key]=[span.start() for span in spans]
        index=max(0,bisect_right(self._cache[starts_key],self.position)-1)
        if self.position>=spans[index].end():
            if direction>=0:index+=1
        else:index+=direction
        index=max(0,min(len(spans)-1,index))
        self.position = spans[index].start()
        self.continuation = self.position
        value=spans[index].group()
        if unit=='character':
            from keyboard_text import character
            return character(value)
        return value.strip() or 'Blank line.'

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
    command=re.sub(r'\s+',' ',command.lower().strip().rstrip('.!?'))
    command=re.sub(r'^(?:go|move|jump) (?:to )?(?:the )?','',command)
    command=re.sub(r'^(read) (?:the )?current ',r'\1 ',command)
    command=re.sub(r'^(next|previous|read|current) (?:the )?',r'\1 ',command)
    command={'go back a paragraph':'previous paragraph','back a paragraph':'previous paragraph','forward a paragraph':'next paragraph'}.get(command,command)
    if command in ('start reading','read from top','read from beginning','read all from top',
                   'read document','read email draft'):
        return ('read', 'top')
    if command in ('continue reading','read more'):
        return ('read', 'continue')
    match = re.fullmatch(r'(previous|next|read|current) (character|letter|word|line|sentence|paragraph)', command)
    if match:
        return (match[2].replace('letter','character'), {'previous':-1,'next':1,'read':0,'current':0}[match[1]])
    return None
