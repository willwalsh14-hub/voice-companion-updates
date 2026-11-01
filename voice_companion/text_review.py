"""Choosing text by voice on screens you read but do not type in: an open email, a document in
the Document reader, a web page and a Help topic. Will, October 31, 2026: "I need full text
selection commands by both keyboard and voice. select by character, word, line, sentence,
paragraph". The Word processor and email you write use document_editor's own commands; these
are the same words, so a command works the same everywhere.

Each screen keeps its reading place (a reading_navigation.ReadingCursor) and a selection as a
(start, end) pair of character offsets. The window shows both, so the keys and the voice agree.
"""
import re

UNITS = ('character', 'word', 'line', 'sentence', 'paragraph')
UNIT = r'(character|letter|word|line|sentence|paragraph)'
EDGES = {'end': 1, 'bottom': 1, 'beginning': -1, 'start': -1, 'top': -1}
SELECT_ALL = ('select all', 'select all text', 'select everything', 'select the whole thing',
              'select entire document', 'select the entire document', 'select whole document',
              'select the whole document', 'select the whole message', 'select whole message',
              'select the whole page', 'select whole page')
CLEAR = ('clear selection', 'unselect', 'deselect', 'cancel selection', 'unselect all', 'deselect all',
         'unselect text', 'clear the selection', 'remove selection')
SAY = ('what is selected', 'say selection', 'read selection', 'selected text', 'read selected text',
       'read the selection', 'say selected text')
COPY = ('copy', 'copy selection', 'copy that', 'copy selected text', 'copy the selection', 'copy text')


def clean(command):
    command = str(command or '').lower().strip()
    command = re.sub(r'[.!?]+$', '', command)
    command = re.sub(r'\s*[,;:]+\s*', ' ', command)
    return re.sub(r'\s+', ' ', command).strip().replace('letter', 'character')


def request(command):
    """What a spoken selection command asks for, or None:
    ('all',), ('clear',), ('say',), ('copy',), ('unit', unit, where) where is 0 (the one here),
    1 or -1 (the next or previous one), ('extend', unit, 1 or -1), ('to', edge, scope)."""
    command = clean(command)
    if command in SELECT_ALL: return ('all',)
    if command in CLEAR: return ('clear',)
    if command in SAY: return ('say',)
    if command in COPY: return ('copy',)
    match = re.fullmatch(r'select (?:the )?(?:(current|this|next|previous) )?' + UNIT.replace('letter|', ''), command)
    if match:
        return ('unit', match[2], {'next': 1, 'previous': -1}.get(match[1], 0))
    match = re.fullmatch(r'(?:extend|add to|grow) (?:the )?selection (?:by |to )?(?:(?:the|one|a) )?(?:(next|previous) )?'
                         + UNIT.replace('letter|', ''), command)
    if match:
        return ('extend', match[2], -1 if match[1] == 'previous' else 1)
    match = re.fullmatch(r'select (?:from here )?(?:to|until) (?:the )?(end|beginning|start|top|bottom)'
                         r'(?: of (?:the )?(line|paragraph|sentence|document|text|message|page|topic))?', command)
    if match:
        scope = match[2] if match[2] in ('line', 'paragraph', 'sentence') else 'all'
        return ('to', EDGES[match[1]], scope)
    return None


def unit_span(reader, unit, caret, where=0):
    """(start, end) of the word, line and so on at caret, or of the next or previous one."""
    spans = [(m.start(), m.end()) for m in reader._spans(unit)]
    if not spans:
        return None
    index = next((i for i, (a, b) in enumerate(spans) if a <= caret < b), None)
    if index is None:
        # Between units (a space, a blank line): the next one is "here".
        index = next((i for i, (a, b) in enumerate(spans) if a >= caret), len(spans) - 1)
        if where < 0:
            index -= 1
        where = 0 if where > 0 else where
    index = max(0, min(len(spans) - 1, index + where))
    a, b = spans[index]
    text = reader.text
    if unit in ('line', 'paragraph') and b > a and text[b - 1:b] == '\n':
        b -= 1   # the line itself, not its line break
    return a, b


def scope_span(text, caret, scope):
    if scope == 'all':
        return 0, len(text)
    if scope == 'line':
        start = text.rfind('\n', 0, caret) + 1
        end = text.find('\n', caret)
        return start, len(text) if end < 0 else end
    from reading_navigation import ReadingCursor
    reader = ReadingCursor(); reader.set_text(text)
    return unit_span(reader, scope, caret) or (0, len(text))


def spoken(text, a, b):
    from keyboard_text import spoken_piece
    return spoken_piece(text[a:b])


def apply(reader, selection, want, clipboard=None):
    """Do a selection command on a screen's reading place. Returns (selection, words to say).
    selection is (start, end) or None. reader.position moves to the end you are working at."""
    text = reader.text
    kind = want[0]
    if kind == 'say':
        return selection, ('Selected: ' + spoken(text, *selection) + '.' if selection else 'No text is selected.')
    if kind == 'clear':
        return None, ('Selection cleared.' if selection else 'No text is selected.')
    if kind == 'copy':
        if not selection:
            return selection, 'No text is selected. Select text first, then say copy.'
        try:
            (clipboard or __import__('document_editor').copy_to_clipboard)(text[selection[0]:selection[1]])
        except Exception:
            return selection, 'The text could not be copied. Try again.'
        return selection, 'Copied.'
    if not text:
        return None, 'There is no text to select.'
    caret = max(0, min(len(text), reader.position))
    if kind == 'all':
        reader.position = reader.continuation = len(text)
        return (0, len(text)), 'All text selected.'
    if kind == 'unit':
        span = unit_span(reader, want[1], caret, want[2])
        if not span:
            return selection, 'There is no ' + want[1] + ' to select.'
        reader.position = reader.continuation = span[0]
        return span, 'Selected ' + spoken(text, *span) + '.'
    if kind == 'extend':
        unit, direction = want[1], want[2]
        a, b = selection or (caret, caret)
        if direction > 0:
            # To the end of the next word, line and so on after the selection.
            ends = []
            for m in reader._spans(unit):
                end = m.end() - 1 if unit in ('line', 'paragraph') and text[m.end() - 1:m.end()] == '\n' else m.end()
                ends.append(end)
            end = next((e for e in ends if e > b), None)
            if end is None:
                return selection, 'Nothing more to select.'
            reader.position = reader.continuation = end
            return (a, end), 'Selected ' + spoken(text, b, end) + '.'
        prev = [(m.start(), m.end()) for m in reader._spans(unit) if m.start() < a]
        if not prev:
            return selection, 'Nothing more to select.'
        new = (prev[-1][0], b)
        reader.position = reader.continuation = new[0]
        return new, 'Selected ' + spoken(text, new[0], a) + '.'
    if kind == 'to':
        edge, scope = want[1], want[2]
        start, end = scope_span(text, caret, scope)
        anchor = selection[0] if selection and edge > 0 else selection[1] if selection else caret
        new = (anchor, end) if edge > 0 else (start, anchor)
        if new[0] >= new[1]:
            return selection, 'Nothing to select there.'
        reader.position = reader.continuation = new[1] if edge > 0 else new[0]
        return new, 'Selected ' + spoken(text, *new) + '.'
    return selection, ''
