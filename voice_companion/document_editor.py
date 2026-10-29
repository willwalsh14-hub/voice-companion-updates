"""Voice Word editor. Writes basic DOCX files using Python's standard library."""
import html
import re
import zipfile
import copy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from difflib import SequenceMatcher
from xml.etree import ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from storage import keep_previous
from reading_navigation import ReadingCursor, reading_request
from dictation_text import clean_dictation
from ai_dictation import prepare_dictation

CONTENT_TYPES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
</Types>'''
ROOT_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'''
DOC_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/></Relationships>'''
STYLES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
          '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
          + ''.join('<w:style w:type="paragraph" w:styleId="Heading%d"><w:name w:val="heading %d"/>'
                    '<w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:outlineLvl w:val="%d"/></w:pPr>'
                    '<w:rPr><w:b/><w:sz w:val="%d"/></w:rPr></w:style>'
                    % (n, n, n-1, max(22, 36-2*n)) for n in range(1, 7)) + '</w:styles>')
NUMBERING = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="singleLevel"/><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum><w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="singleLevel"/><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num><w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num></w:numbering>'''
XMLNS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
VOICE_CLIPBOARD = ''
CLIPBOARD_WRITE_OK = False
CLIPBOARD_ERRORS = (ImportError, OSError)
try:
    from pywintypes import error as WindowsClipboardError
    CLIPBOARD_ERRORS += (WindowsClipboardError,)
except ImportError:
    pass


def copy_to_clipboard(value):
    global VOICE_CLIPBOARD, CLIPBOARD_WRITE_OK
    VOICE_CLIPBOARD = value
    CLIPBOARD_WRITE_OK = False
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(value, win32clipboard.CF_UNICODETEXT)
            CLIPBOARD_WRITE_OK = True
        finally:
            win32clipboard.CloseClipboard()
    except CLIPBOARD_ERRORS:
        pass


def paste_from_clipboard():
    if VOICE_CLIPBOARD and not CLIPBOARD_WRITE_OK:
        return VOICE_CLIPBOARD
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                value = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                if isinstance(value, str) and value:
                    return value
        finally:
            win32clipboard.CloseClipboard()
    except CLIPBOARD_ERRORS:
        pass
    return VOICE_CLIPBOARD


def spoken_number(value):
    """Accept digits or short spoken numbers for spacing values."""
    value = value.strip().lower().replace('-', ' ')
    fractions = {'a half': '.5', 'one half': '.5', 'a quarter': '.25',
                 'one quarter': '.25', 'three quarters': '.75'}
    for phrase, decimal in fractions.items():
        if value.endswith(' and ' + phrase):
            whole = spoken_number(value[:-len(' and '+phrase)])
            return whole + decimal if whole is not None and '.' not in whole else None
    if re.fullmatch(r'\d+(?:\.\d+)?', value):
        return value
    small = dict(zip('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(), range(20)))
    tens = {'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50, 'sixty': 60,
            'seventy': 70, 'eighty': 80, 'ninety': 90}
    def integer(words):
        if not words:
            return None
        if len(words) == 1:
            return small.get(words[0], tens.get(words[0]))
        if len(words) == 2 and words[0] in tens and words[1] in small and small[words[1]] < 10:
            return tens[words[0]] + small[words[1]]
        if words[0] in ('one', 'a') and words[1] == 'hundred':
            rest = words[2:]
            if rest and rest[0] == 'and':
                rest = rest[1:]
            return 100 + (integer(rest) or 0) if not rest or integer(rest) is not None else None
        return None
    if ' point ' in value:
        whole, decimal = value.split(' point ', 1)
        first = integer(whole.split())
        fractional = decimal.split()
        if first is None or not 1 <= len(fractional) <= 2 or any(word not in small or small[word] > 9 for word in fractional):
            return None
        return str(first) + '.' + ''.join(str(small[word]) for word in fractional)
    parsed = integer(value.split())
    return str(parsed) if parsed is not None else None


@dataclass
class TextRun:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    font: str = ''
    size: int = 0
    highlight: str = ''


LINE_BREAK_COMMANDS = ('new line', 'insert new line', 'line break', 'carriage return', 'insert carriage return')
START_COMMANDS = ('beginning of document', 'beginning of the document', 'go to beginning', 'go to beginning of document', 'go to the beginning of document', 'go to beginning of the document', 'go to the beginning of the document', 'start of document', 'go to start of document', 'first paragraph')
END_COMMANDS = ('end of document', 'end of the document', 'go to end', 'go to end of document', 'go to the end of document', 'go to end of the document', 'go to the end of the document', 'last paragraph')
NAME_PATTERN = r'(?:name (?:the |this |my )?document(?: as)?|rename (?:the |this |my )?document(?: to)?|document name is|title is|save (?:the |this |my )?document as|save as)\s*(.*)'


FORMAT_READ_COMMANDS = {'say font':'all', 'say formatting':'all', 'say font size':'size', 'say size':'size', 'say font name':'font', 'say line spacing':'spacing', 'say spacing':'spacing', 'say alignment':'alignment', 'say heading':'kind', 'say style':'kind', 'say bold':'bold', 'say italic':'italic', 'say underline':'underline', 'say highlight':'highlight', 'say paragraph alignment':'alignment', 'say paragraph spacing':'spacing'}


def document_navigation_request(raw):
    command = ' '.join(raw.lower().strip().rstrip('.!?').split())
    if command in START_COMMANDS: return 'beginning of document'
    if command in END_COMMANDS: return 'end of document'
    match = re.fullmatch(r'(?:(?:go|move|jump|return|take me) (?:to )?)?(?:the )?(beginning|start|end)(?: of (?:the |my )?(?:document|doc))?',command)
    if match: return 'end of document' if match[1]=='end' else 'beginning of document'
    # Home and End: "go to end of line". The verb is required, so "End of line." can still be written.
    line = re.fullmatch(r'(?:go|move|jump) to (?:the )?(start|beginning|end) of (?:the |this )?line', command)
    if line: return 'end of line' if line[1] == 'end' else 'start of line'
    original_command = command
    command = re.sub(r'^(?:go|move|jump) (?:to )?(?:the )?', '', command)
    if re.fullmatch(r'(?:next|previous) (?:character|word|line|sentence|paragraph|heading|cell|row|column)',command): return command
    aliases = {'go back a paragraph':'previous paragraph','go forward a paragraph':'next paragraph','move right a cell':'next cell','move left a cell':'previous cell','row down':'next row','row up':'previous row','column right':'next column','column left':'previous column','leave table':'leave table','after table':'leave table'}
    return aliases.get(original_command,aliases.get(command))


def document_control_request(raw):
    command = ' '.join(raw.lower().strip().rstrip('.!?').split())
    return bool(re.fullmatch(r'make it (?:\d+|[a-z -]+) points?',command)) or bool(document_navigation_request(raw)) or command in FORMAT_READ_COMMANDS or command in LINE_BREAK_COMMANDS + START_COMMANDS + END_COMMANDS + ('scratch','scratch that','type new line') or bool(re.fullmatch(NAME_PATTERN, command)) or command.startswith(('type literally ', 'dictate literally '))


# "change font to Arial" sets formatting; it must not replace the word "font" in the text.
FORMAT_CHANGE_TARGETS = frozenset(('font', 'font name', 'the font', 'font size', 'size', 'the font size',
                                   'line spacing', 'the line spacing', 'document line spacing',
                                   'all line spacing', 'word font', 'word size', 'spacing'))

TABLE_SELECTION_NOTICE = ('That selection crosses table cells. Change or paste one cell at a time, '
                          'or say delete selection to clear the text and keep the cells.')


def document_filename(title):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', title).strip(' .')[:80]
    if name.lower().endswith('.docx'): name = name[:-5].rstrip(' .')
    return name or 'Untitled'


def checked_document_name(text):
    """(file name without .docx, None), or (None, what to say) when Windows cannot use the name."""
    proposed = ' '.join(str(text).split()).strip(' .')
    # Said aloud, the file type comes as words: "My letter dot docx" (or "dot d o c x").
    proposed = re.sub(r'\s+dot\s+d\s?o\s?c\s?x$', '', proposed, flags=re.I).strip(' .')
    if not proposed: return None, 'Please say or type a document name.'
    if re.search(r'[<>:"/\\|?*]', proposed): return None, 'That file name contains a character Windows cannot use. Please choose another name.'
    proposed = document_filename(proposed)
    if re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', proposed, re.I): return None, 'Windows reserves that file name. Please choose another name.'
    return proposed, None


def existing_document(folder, name):
    """The .docx in folder with this name, in any letter case, or None."""
    wanted = (name + '.docx').casefold()
    try:
        return next((p for p in Path(folder).glob('*.docx') if p.name.casefold() == wanted), None)
    except OSError:
        return None


def find_document(folder, title):
    """The .docx in folder that "open document" plus this title means, or None."""
    name = document_filename(title)
    legacy = re.sub(r'[^\w .-]+', '', title).strip(' .')[:80]
    matches = [p for p in Path(folder).glob('*.docx') if p.stem.casefold() in (name.casefold(),legacy.casefold(),title.casefold())]
    # "Letter.docx.docx" and "Letter.docx" both sanitize to "Letter"; open the exact one.
    matches.sort(key=lambda p: (p.stem != title, p.stem.casefold() != title.casefold(), p.name))
    return matches[0] if matches else None


PARAGRAPH_ORDINALS = {'first':1, 'second':2, 'third':3, 'fourth':4, 'fifth':5,
                      'sixth':6, 'seventh':7, 'eighth':8, 'ninth':9, 'tenth':10}


def structural_selection_request(command):
    command = command.lower().strip().rstrip('.!?')
    if command in ('select all', 'select all text', 'select everything',
                   'select entire document', 'select the entire document',
                   'select whole document', 'select the whole document'):
        return ('all', None)
    match = re.fullmatch(r'select (?:the )?(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|last|next|previous|current|this) (paragraph|character|word|line|sentence)', command)
    if match: return (match[2], match[1])
    match = re.fullmatch(r'select (?:the )?(paragraph|character|word|line|sentence) (?:number )?(\d+|one|two|three|four|five|six|seven|eight|nine|ten)', command)
    if match: return (match[1], match[2])
    match = re.fullmatch(r'select (?:the )?(paragraph|character|word|line|sentence)', command)
    if match: return (match[1], 'current')
    return None


@dataclass
class TextSelection:
    first_paragraph: int
    first_offset: int
    last_paragraph: int
    last_offset: int


@dataclass
class Paragraph:
    text: str
    kind: str = 'Normal'
    bold: bool = False
    italic: bool = False
    underline: bool = False
    font: str = ''
    size: int = 0
    table_id: int = 0
    row: int = 0
    column: int = 0
    runs: list[TextRun] = field(default_factory=list)
    alignment: str = 'left'
    line_spacing_twips: int = 0
    line_spacing_rule: str = 'auto'


# Undo and Redo keep this many steps, and drop the oldest steps once the copies they
# keep hold more text than UNDO_TEXT_LIMIT characters (so a long book cannot fill memory).
UNDO_LIMIT = 100
UNDO_TEXT_LIMIT = 4_000_000
UNDO_REQUESTS = {'undo': 'undo', 'undo that': 'undo', 'undo last change': 'undo',
                 'redo': 'redo', 'redo that': 'redo', 'redo last change': 'redo'}
# How a command is named after "Undid" or "Redid", when its words are not plain enough.
UNDO_NAMES = {'toggle bold': 'bold', 'toggle italic': 'italic', 'toggle underline': 'underline',
              'new line': 'a new line', 'insert new line': 'a new line', 'line break': 'a new line',
              'new paragraph': 'a new paragraph', 'paste': 'the paste', 'paste that': 'the paste',
              'paste text': 'the paste'}


def undo_words(text, limit=10):
    """At most `limit` words of what changed, without the closing punctuation, for "Undid typing: ..."."""
    words = ' '.join(str(text).split()).rstrip('.,;:!?')
    parts = words.split(' ')
    return ' '.join(parts[:limit]) + (' and more' if len(parts) > limit else '')


def undo_name(command):
    """Plain words for a voice command that changed the document: "Undid bold.", "Undid delete paragraph."."""
    command = ' '.join(str(command or '').split())
    if not command:
        return 'the last change'
    return UNDO_NAMES.get(command) or undo_words(command, 8)


@dataclass
class UndoStep:
    """The document as it was before one change, and the words that name the change."""
    title: str
    paragraphs: list
    cursor: int
    label: str
    extra: object = None
    # Typing from the keyboard: (kind, start, end, text) while one run of typing can grow.
    run: tuple | None = None

    def size(self):
        return sum(len(p.text) for p in self.paragraphs)


@dataclass
class VoiceDocument:
    folder: Path
    title: str = 'Untitled'
    paragraphs: list[Paragraph] = field(default_factory=list)
    cursor: int = 0
    insertion_position: tuple[int,int] | None = None
    navigation_position: tuple[int,int] | None = None
    dictating: bool = False
    sentence_cursor: int = 0
    word_cursor: int = 0
    pending_spacing: bool = False
    pending_spacing_candidate: tuple[str, str] | None = None
    selection: TextSelection | None = None
    selection_candidates: list[TextSelection] = field(default_factory=list)
    selection_action: str = ''
    selection_index: int = 0
    replacement_candidates: list[TextSelection] = field(default_factory=list)
    replacement_index: int = 0
    replacement_text: str = ''
    last_change_type: str = ''
    # Undo and Redo history, newest last (UndoStep). Kept in memory while the document is open.
    undo_steps: list = field(default_factory=list, repr=False, compare=False)
    redo_steps: list = field(default_factory=list, repr=False, compare=False)
    _opening: bool = False
    reading: ReadingCursor = field(default_factory=ReadingCursor, repr=False)
    source_path: Path | None = field(default=None, repr=False)
    # Font and size from Word processor options. Every paragraph without its own
    # font or size uses them, including ones added later (a new paragraph has none).
    default_font: str = field(default='', repr=False)
    default_size: float = field(default=0, repr=False)
    # Alignment and spacing for the first paragraph; later paragraphs copy it.
    pending_layout: tuple | None = field(default=None, repr=False)

    # Said when there is nothing to read; an email draft says "message body".
    EMPTY_NOTICE = 'The document is empty.'

    def __setattr__(self, name, value):
        # A selection is a pair of paragraph/offset positions. Remember the text it
        # was made against so a later edit (delete paragraph, dictation, undo...)
        # cannot leave it pointing at different words or past the end.
        if name == 'selection':
            object.__setattr__(self, '_selection_text',
                               None if value is None else tuple(p.text for p in getattr(self, 'paragraphs', [])))
        object.__setattr__(self, name, value)

    def drop_stale_selection(self):
        if self.selection is not None and getattr(self, '_selection_text', None) != tuple(p.text for p in self.paragraphs):
            self.selection = None

    def __post_init__(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        if self.title == 'Untitled' and not self._opening:
            number = 2
            while self.path.exists():
                self.title = f'Untitled {number}'
                number += 1

    @classmethod
    def open_existing(cls, folder: Path, title: str):
        folder.mkdir(parents=True, exist_ok=True)
        path = find_document(folder, title)
        if path is None:
            raise FileNotFoundError(title)
        if path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError('This document is too large for the editor.')
        try:
            with zipfile.ZipFile(path) as archive:
                if archive.getinfo('word/document.xml').file_size > 10 * 1024 * 1024:
                    raise ValueError('This document is too large for the editor.')
                root = ET.fromstring(archive.read('word/document.xml'))
        except (zipfile.BadZipFile, KeyError, ET.ParseError, EOFError) as error:
            # An empty "New Microsoft Word Document" or a damaged file is not a crash.
            raise ValueError('This document is empty or damaged, so it could not be opened.') from error
        ns = '{' + XMLNS + '}'
        allowed = {ns + tag for tag in ('document', 'body', 'p', 'pPr', 'pStyle', 'r', 'rPr', 'b', 'i', 'u', 'highlight',
                   'rFonts', 'sz', 't', 'sectPr', 'numPr', 'ilvl', 'numId', 'tbl', 'tblPr', 'tblGrid',
                   'gridCol', 'tr', 'tc', 'tcPr', 'tcW', 'tblW', 'tblBorders',
                   'top', 'bottom', 'left', 'right', 'insideH', 'insideV', 'jc', 'spacing', 'br')}
        if any(node.tag not in allowed for node in root.iter()):
            raise ValueError('This document uses formatting the editor cannot preserve.')
        # Attributes this editor would silently drop on save: space before/after
        # paragraphs and paragraph styles other than headings.
        if any(node.get(ns + side) is not None for node in root.iter(ns + 'spacing')
               for side in ('before', 'after', 'beforeLines', 'afterLines', 'beforeAutospacing', 'afterAutospacing')):
            raise ValueError('This document uses formatting the editor cannot preserve.')
        if any(not re.fullmatch(r'(?:Heading[1-6]|Normal)', node.get(ns + 'val', ''))
               for node in root.iter(ns + 'pStyle')):
            raise ValueError('This document uses formatting the editor cannot preserve.')
        document = cls(folder, title=path.stem, _opening=True, source_path=path)
        def switched_on(props, tag):
            # <w:b w:val="0"/> and <w:u w:val="none"/> mean the formatting is off.
            node = props.find(ns + tag) if props is not None else None
            return node is not None and node.get(ns + 'val', 'true').lower() not in ('0', 'false', 'off', 'none')
        def points(node):
            half_points = int(node.get(ns + 'val'))
            return half_points // 2 if half_points % 2 == 0 else half_points / 2
        def parse_paragraph(node, table_id=0, row=0, column=0):
            style = node.find('./' + ns + 'pPr/' + ns + 'pStyle')
            kind = 'Normal'
            if style is not None and re.fullmatch(r'Heading[1-6]', style.get(ns+'val', '')):
                kind = 'Heading ' + style.get(ns+'val')[-1]
            num = node.find('./' + ns + 'pPr/' + ns + 'numPr/' + ns + 'numId')
            align = node.find('./' + ns + 'pPr/' + ns + 'jc')
            spacing = node.find('./' + ns + 'pPr/' + ns + 'spacing')
            if num is not None:
                kind = 'Bulleted list' if num.get(ns+'val') == '1' else 'Numbered list'
            runs = node.findall('.//' + ns + 'r')
            def run_text(run):
                return ''.join(('\n' if child.tag == ns+'br' else child.text or '')
                               for child in run if child.tag in (ns+'t', ns+'br'))
            text = ''.join(run_text(run) for run in runs)
            bold = bool(runs) and all(switched_on(run.find(ns + 'rPr'), 'b') for run in runs)
            italic = bool(runs) and all(switched_on(run.find(ns + 'rPr'), 'i') for run in runs)
            underline = bool(runs) and all(switched_on(run.find(ns + 'rPr'), 'u') for run in runs)
            first = runs[0].find('./' + ns + 'rPr') if runs else None
            fonts = first.find(ns+'rFonts') if first is not None else None
            size = first.find(ns+'sz') if first is not None else None
            parsed_runs = []
            for run in runs:
                props = run.find(ns+'rPr')
                rf = props.find(ns+'rFonts') if props is not None else None
                rs = props.find(ns+'sz') if props is not None else None
                rh = props.find(ns+'highlight') if props is not None else None
                parsed_runs.append(TextRun(run_text(run),
                    switched_on(props, 'b'),
                    switched_on(props, 'i'),
                    switched_on(props, 'u'),
                    rf.get(ns+'ascii', '') if rf is not None else '',
                    points(rs) if rs is not None else 0,
                    rh.get(ns+'val', '') if rh is not None else ''))
            document.paragraphs.append(Paragraph(text, kind, bold, italic, underline,
                fonts.get(ns+'ascii', '') if fonts is not None else '',
                points(size) if size is not None else 0, table_id, row, column,
                parsed_runs if len(parsed_runs) > 1 or any(r.highlight for r in parsed_runs) else [],
                align.get(ns+'val', 'left') if align is not None else 'left',
                int(spacing.get(ns+'line', '0')) if spacing is not None else 0,
                spacing.get(ns+'lineRule', 'auto') if spacing is not None else 'auto'))
        body = root.find(ns+'body')
        for block in body:
            if block.tag == ns+'p':
                parse_paragraph(block)
            elif block.tag == ns+'tbl':
                table_id = max((p.table_id for p in document.paragraphs), default=0) + 1
                for row, tr in enumerate(block.findall(ns+'tr'), 1):
                    for column, tc in enumerate(tr.findall(ns+'tc'), 1):
                        cells = tc.findall(ns+'p')
                        if len(cells) != 1:
                            raise ValueError('This table has a cell structure this editor cannot preserve.')
                        parse_paragraph(cells[0], table_id, row, column)
        return document

    @property
    def path(self):
        # An opened file keeps its real name until the user renames the document.
        if self.source_path is not None and self.source_path.stem == self.title:
            return self.source_path
        safe = document_filename(self.title)
        return self.folder / (safe + '.docx')

    def save(self):
        self.apply_new_document_style()
        parts = []
        def paragraph_xml(p):
            properties = ''
            if p.kind.startswith('Heading '):
                properties = '<w:pStyle w:val="Heading' + p.kind[-1] + '"/>'
            elif p.kind in ('Bulleted list', 'Numbered list'):
                properties = '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="' + ('1' if p.kind == 'Bulleted list' else '2') + '"/></w:numPr>'
            if p.alignment != 'left':
                properties += '<w:jc w:val="' + p.alignment + '"/>'
            if p.line_spacing_twips:
                properties += f'<w:spacing w:line="{p.line_spacing_twips}" w:lineRule="{p.line_spacing_rule}"/>'
            def run_xml(run):
                runstyle = ('<w:rPr>' + ('<w:b/>' if run.bold else '') + ('<w:i/>' if run.italic else '')
                            + ('<w:u w:val="single"/>' if run.underline else '')
                            + ('<w:rFonts w:ascii="' + html.escape(run.font, quote=True) + '" w:hAnsi="' + html.escape(run.font, quote=True) + '"/>' if run.font else '')
                            + ('<w:sz w:val="' + str(int(round(run.size*2))) + '"/>' if run.size else '') + '</w:rPr>')
                if run.highlight:
                    runstyle = runstyle.replace('</w:rPr>', '<w:highlight w:val="' + html.escape(run.highlight, quote=True) + '"/></w:rPr>')
                lines = run.text.split('\n')
                content = '<w:br/>'.join('<w:t xml:space="preserve">' + html.escape(line, quote=False) + '</w:t>'
                                           for line in lines)
                return '<w:r>' + runstyle + content + '</w:r>'
            self.reconcile_runs(p)
            runs = p.runs or [TextRun(p.text, p.bold, p.italic, p.underline, p.font, p.size)]
            return '<w:p><w:pPr>' + properties + '</w:pPr>' + ''.join(map(run_xml, runs)) + '</w:p>'
        index = 0
        while index < len(self.paragraphs):
            p = self.paragraphs[index]
            if not p.table_id:
                parts.append(paragraph_xml(p))
                index += 1
                continue
            table_id = p.table_id
            group = []
            while index < len(self.paragraphs) and self.paragraphs[index].table_id == table_id:
                group.append(self.paragraphs[index]); index += 1
            columns = max(cell.column for cell in group)
            rows = max(cell.row for cell in group)
            if len(group) != columns * rows:
                raise ValueError('The table has missing cells.')
            cells = {(cell.row,cell.column):cell for cell in group}
            borders = '<w:tblBorders>' + ''.join(f'<w:{side} w:val="single" w:sz="4" w:color="808080"/>' for side in ('top','bottom','left','right','insideH','insideV')) + '</w:tblBorders>'
            table = '<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>' + borders + '</w:tblPr><w:tblGrid>' + '<w:gridCol w:w="2400"/>'*columns + '</w:tblGrid>'
            for row in range(1, rows+1):
                table += '<w:tr>' + ''.join('<w:tc><w:tcPr><w:tcW w:w="2400" w:type="dxa"/></w:tcPr>' + paragraph_xml(cells[row,col]) + '</w:tc>' for col in range(1,columns+1)) + '</w:tr>'
            parts.append(table + '</w:tbl>')
        doc = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="' + XMLNS + '"><w:body>' + ''.join(parts) + '<w:sectPr/></w:body></w:document>'
        temporary = self.path.with_suffix('.writing.docx')
        try:
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as z:
                for name, value in [('[Content_Types].xml', CONTENT_TYPES), ('_rels/.rels', ROOT_RELS), ('word/document.xml', doc), ('word/styles.xml', STYLES), ('word/numbering.xml', NUMBERING), ('word/_rels/document.xml.rels', DOC_RELS)]:
                    z.writestr(name, value)
            keep_previous(self.path)
            temporary.replace(self.path)
        except BaseException:
            # A file open in Word, or a full disk: the half-made copy must not stay
            # behind, where it would be listed as one more document.
            try: temporary.unlink()
            except OSError: pass
            raise
        return self.path

    def save_as(self, folder, title):
        """Save a copy as folder/title.docx and keep working in the copy. The first file is not changed.

        On an error the document keeps its old name and folder. A file already at the
        target is replaced only after the user said Yes; save() keeps its previous copy."""
        previous = (self.folder, self.title, self.source_path)
        self.folder, self.title, self.source_path = Path(folder), title, None
        try:
            self.folder.mkdir(parents=True, exist_ok=True)   # a first copy into Documents, Voice Companion
            self.save()
        except BaseException:
            self.folder, self.title, self.source_path = previous
            raise
        # Undo must not rename the copy back: every kept step now belongs to the copy.
        for step in self.undo_steps + self.redo_steps:
            step.title = self.title
        return self.path

    # Undo and Redo ------------------------------------------------------------

    def _undo_extra(self):
        """More state that Undo restores; an email draft adds its addresses and subject."""
        return None

    def _restore_extra(self, extra):
        pass

    def _snapshot(self, label, title=None):
        return UndoStep(self.title if title is None else title, copy.deepcopy(self.paragraphs), self.cursor,
                        label, copy.deepcopy(self._undo_extra()))

    def _same_as_now(self, step):
        return step.title == self.title and step.paragraphs == self.paragraphs and step.extra == self._undo_extra()

    def _push_undo(self, step):
        steps = self.undo_steps
        # A step that changed nothing (a command that stopped after the checkpoint) is dropped.
        if steps and self._same_as_now(steps[-1]):
            steps.pop()
        steps.append(step)
        self.redo_steps.clear()
        while len(steps) > UNDO_LIMIT or len(steps) > 1 and sum(s.size() for s in steps) > UNDO_TEXT_LIMIT:
            steps.pop(0)

    def checkpoint(self, label=None):
        """Remember the document before a change, so Undo can bring it back."""
        self._push_undo(self._snapshot(label or undo_name(getattr(self, '_undo_command', ''))))
        self.last_change_type = 'edit'

    @property
    def last_change(self):
        """The state before the most recent change, or None (older code reads it)."""
        return self.undo_steps[-1] if self.undo_steps else None

    def _restore_step(self, step):
        """Put the document back as the step has it. False if a rename back is blocked."""
        old_path = self.path
        current_title = self.title
        self.title = step.title
        if self.path != old_path and self.path.exists():
            self.title = current_title
            return False
        self.paragraphs, self.cursor = step.paragraphs, min(step.cursor, max(0, len(step.paragraphs) - 1))
        self._restore_extra(step.extra)
        self.selection = None
        self.insertion_position = None
        self.navigation_position = None
        self.changed()
        if self.path != old_path:
            old_path.unlink(missing_ok=True)
        return True

    def undo_or_redo(self, redo=False):
        source, target = (self.redo_steps, self.undo_steps) if redo else (self.undo_steps, self.redo_steps)
        while source and self._same_as_now(source[-1]):
            source.pop()
        if not source:
            word = 'redo' if redo else 'undo'
            return ('Nothing more to ' + word + '.') if target else ('There is nothing to ' + word + '.')
        step = source[-1]
        now = self._snapshot(step.label)
        if not self._restore_step(step):
            return 'I cannot ' + ('redo' if redo else 'undo') + ' the rename because another document has that name.'
        source.pop()
        target.append(now)
        self.last_change_type = 'edit'
        done = ('Redid ' if redo else 'Undid ') + step.label + '. '
        if not self.paragraphs:
            return done + self.EMPTY_NOTICE
        p = self.paragraphs[self.cursor]
        return done + (self.describe_cell() if p.table_id else f'Paragraph {self.cursor + 1} of {len(self.paragraphs)}. ' + (p.text or 'Empty paragraph.'))

    @staticmethod
    def spacing_description(p):
        if not p.line_spacing_twips:
            return 'default line spacing'
        if p.line_spacing_rule == 'auto':
            multiplier = Decimal(p.line_spacing_twips) / Decimal(240)
            return f'{multiplier.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP):f} line spacing'
        points = Decimal(p.line_spacing_twips) / Decimal(20)
        return f'{"exactly" if p.line_spacing_rule == "exact" else "at least"} {points.normalize():f} points line spacing'

    @staticmethod
    def validated_line_spacing(amount, rule):
        try:
            number = Decimal(amount)
        except InvalidOperation:
            return None, 'Please say a number such as 1.5 or 18 points.'
        if rule == 'auto':
            if number < Decimal('0.5') or number > 10 or number.as_tuple().exponent < -2:
                return None, 'Choose line spacing from 0.5 to 10, with up to two decimal places.'
            twips = int((number * 240).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        else:
            if number < 4 or number > 144 or number.as_tuple().exponent < -1:
                return None, 'Choose from 4 to 144 points, in half-point steps.'
            twips = int(number * 20)
        return twips, None

    def set_line_spacing(self, amount, rule='auto', whole_document=False):
        if not self.paragraphs:
            return 'The document is empty. Write a paragraph first.'
        twips, error = self.validated_line_spacing(amount, rule)
        if error:
            return error
        self.drop_stale_selection()
        self.checkpoint()
        for p in (self.paragraphs if whole_document else self.paragraphs[self.selection.first_paragraph:self.selection.last_paragraph+1] if self.selection else [self.paragraphs[self.cursor]]):
            p.line_spacing_twips = twips
            p.line_spacing_rule = rule
        self.changed()
        return 'Set ' + self.spacing_description(self.paragraphs[self.cursor]) + (' throughout the document.' if whole_document else ' for this paragraph.')

    @staticmethod
    def reconcile_runs(p):
        if not p.runs:
            return
        original = ''.join(run.text for run in p.runs)
        if original == p.text:
            return
        styles = [run for run in p.runs for _ in run.text]
        fallback = TextRun('', p.bold, p.italic, p.underline, p.font, p.size)
        new_styles = []
        for op, a, b, c, d in SequenceMatcher(None, original, p.text, autojunk=False).get_opcodes():
            if op == 'equal':
                new_styles.extend(styles[a:b])
            elif op in ('replace', 'insert'):
                inherited = styles[a] if a < len(styles) else (styles[a-1] if a else fallback)
                new_styles.extend([inherited] * (d-c))
        p.runs = VoiceDocument.compress_runs(p.text, new_styles)

    @staticmethod
    def compress_runs(text, styles):
        runs = []
        for character, style in zip(text, styles):
            attributes = (style.bold, style.italic, style.underline, style.font, style.size, style.highlight)
            if runs and (runs[-1].bold, runs[-1].italic, runs[-1].underline, runs[-1].font, runs[-1].size, runs[-1].highlight) == attributes:
                runs[-1].text += character
            else:
                runs.append(TextRun(character, *attributes))
        return runs

    def format_word(self, **changes):
        if not self.paragraphs or not self.paragraphs[self.cursor].text.strip():
            return 'There is no word here.'
        p = self.paragraphs[self.cursor]
        words = list(re.finditer(r'\S+', p.text))
        selected = words[min(self.word_cursor, len(words)-1)]
        self.checkpoint()
        self.reconcile_runs(p)
        base = TextRun('', p.bold, p.italic, p.underline, p.font, p.size)
        styles = [run for run in p.runs for _ in run.text] if p.runs else [base] * len(p.text)
        for i in range(selected.start(), selected.end()):
            styles[i] = copy.copy(styles[i])
            for key, value in changes.items():
                setattr(styles[i], key, value)
        p.runs = self.compress_runs(p.text, styles)
        selected_index = min(self.word_cursor, len(words)-1)
        self.changed()
        self.word_cursor = selected_index
        return 'Formatted word ' + selected.group() + '. ' + ', '.join(f'{k}: {v}' for k,v in changes.items()) + '.'

    def use_new_document_style(self, font='', size=0, alignment='left', spacing=None):
        """Set the look of a new document before it has any text.

        An empty document has no paragraph to format yet, so the choices are kept
        and applied as soon as the first text is written.
        """
        self.default_font = font or ''
        self.default_size = size or 0
        twips = 0
        if spacing:
            twips, error = self.validated_line_spacing(spacing, 'auto')
            if error: twips = 0
        self.pending_layout = (alignment or 'left', twips)
        self.apply_new_document_style()

    def apply_new_document_style(self):
        if not self.paragraphs:
            return
        if self.pending_layout:
            alignment, twips = self.pending_layout
            self.pending_layout = None
            for p in self.paragraphs:
                if p.alignment == 'left': p.alignment = alignment
                if not p.line_spacing_twips and twips: p.line_spacing_twips, p.line_spacing_rule = twips, 'auto'
        for p in self.paragraphs:
            for item in [p] + list(p.runs):
                if self.default_font and not item.font: item.font = self.default_font
                if self.default_size and not item.size: item.size = self.default_size

    def changed(self):
        self.sentence_cursor = self.word_cursor = 0
        self.save()

    def append_text(self, text, literal=False):
        if not literal: text = prepare_dictation(text)
        if not text: return 'I did not hear text to add.'
        self.checkpoint('typing: ' + undo_words(text))
        self.selection = None
        if not self.paragraphs:
            self.paragraphs.append(Paragraph(''))
            self.cursor = 0
        current = self.paragraphs[self.cursor].text
        offset = self.insertion_position[1] if self.insertion_position and self.insertion_position[0] == self.cursor else len(current)
        offset = min(offset,len(current))
        before,after = current[:offset],current[offset:]
        inserted = ('' if not before or before[-1].isspace() else ' ') + text
        if after and not after[0].isspace() and after[0] not in '.,;:!?': inserted += ' '
        self.paragraphs[self.cursor].text = before + inserted + after
        self.insertion_position = (self.cursor,offset+len(inserted))
        self.navigation_position = None
        self.changed()
        self.last_change_type = 'dictation'
        return 'Added: ' + text

    def table_cell(self):
        if not self.paragraphs or not self.paragraphs[self.cursor].table_id:
            return None
        return self.paragraphs[self.cursor]

    def table_move(self, row_delta=0, column_delta=0):
        cell = self.table_cell()
        if cell is None:
            return 'You are not in a table. Say create table with two rows and two columns.'
        for index, candidate in enumerate(self.paragraphs):
            if (candidate.table_id, candidate.row, candidate.column) == (
                    cell.table_id, cell.row + row_delta, cell.column + column_delta):
                self.cursor = index
                return self.describe_cell()
        return 'That is the edge of the table. ' + self.describe_cell()

    def describe_cell(self):
        cell = self.table_cell()
        if cell is None:
            return 'You are not in a table.'
        return f'Row {cell.row}, column {cell.column}. ' + (cell.text or 'Empty cell.')

    def selected_text(self, selection=None):
        selection = selection or self.selection
        if selection is None:
            return ''
        pieces = [p.text for p in self.paragraphs[selection.first_paragraph:selection.last_paragraph+1]]
        pieces[0] = pieces[0][selection.first_offset:]
        pieces[-1] = pieces[-1][:selection.last_offset] if len(pieces) > 1 else pieces[-1][:selection.last_offset-selection.first_offset]
        return '\n'.join(pieces)

    def _location(self, offset):
        for index, p in enumerate(self.paragraphs):
            if offset <= len(p.text):
                return index, offset
            offset -= len(p.text)+1
        return len(self.paragraphs)-1, len(self.paragraphs[-1].text)

    def _phrase_spans(self, phrase):
        whole = '\n'.join(p.text for p in self.paragraphs)
        if not whole or not phrase.strip():
            return []
        escaped = re.escape(phrase.strip())
        before = r'(?<!\w)' if phrase[0].isalnum() else ''
        after = r'(?!\w)' if phrase[-1].isalnum() else ''
        matches = list(re.finditer(before + escaped + after, whole, re.I))
        if not matches:
            words = re.findall(r'\w+', phrase)
            if not words:
                return []
            expression = r'(?<!\w)' + r'[\W_]+'.join(map(re.escape, words)) + r'(?!\w)'
            matches = list(re.finditer(expression, whole, re.I))
        return [TextSelection(*self._location(m.start()), *self._location(m.end())) for m in matches]

    def _select_phrase(self, phrase, action):
        spans = self._phrase_spans(phrase)
        if not spans:
            return 'I could not find that exact text. Say read paragraph and try the words you hear.'
        if len(spans) > 1:
            self.selection_candidates = spans
            self.selection_index = 0
            self.selection_action = action
            return f'I found {len(spans)} matches. ' + self.match_context(spans, 0) + '. Say next, previous, that one, or cancel selection.'
        return self._apply_selection_action(spans[0], action)

    def replacement_context(self):
        return self.match_context(self.replacement_candidates, self.replacement_index)

    def match_context(self, candidates, index):
        span = candidates[index]
        paragraph = self.paragraphs[span.first_paragraph].text
        sentences = list(re.finditer(r'\S.*?(?:[.!?](?=\s|$)|$)', paragraph, re.DOTALL))
        sentence = next((m for m in sentences if m.start() <= span.first_offset < m.end()), None)
        start, end = (sentence.start(), sentence.end()) if sentence else (0, len(paragraph))
        # Keep a long sentence brief, centering the excerpt on the match.
        start = max(start, span.first_offset - 70)
        end = min(end, span.first_offset + 90)
        context = ' '.join(paragraph[start:end].split())
        return context + f'  {index + 1} of {len(candidates)}. Paragraph {span.first_paragraph + 1}'

    def confirm_replacement(self):
        selected = self.replacement_candidates[self.replacement_index]
        replacement = self.replacement_text
        self.replacement_candidates = []
        self.replacement_text = ''
        self.selection_candidates, self.selection_action = [], ''
        return self._apply_selection_action(selected, 'replace:' + replacement)

    def _select_range(self, start_phrase, end_phrase, action='select'):
        starts = self._phrase_spans(start_phrase)
        ends = self._phrase_spans(end_phrase)
        choices = []
        for start in starts:
            first_global = sum(len(p.text)+1 for p in self.paragraphs[:start.first_paragraph]) + start.first_offset
            later = [end for end in ends if
                     sum(len(p.text)+1 for p in self.paragraphs[:end.first_paragraph]) + end.first_offset >= first_global]
            if later:
                end = later[0]
                choices.append(TextSelection(start.first_paragraph, start.first_offset,
                                             end.last_paragraph, end.last_offset))
        if not choices:
            return 'I could not find that start and end in order. Say read document and try again.'
        if len(choices) > 1:
            self.selection_candidates = choices
            self.selection_index = 0
            self.selection_action = action
            return f'I found {len(choices)} possible ranges. ' + self.match_context(choices, 0) + '. Say next, previous, that one, or cancel selection.'
        return self._apply_selection_action(choices[0], action)

    @staticmethod
    def _character_styles(p):
        VoiceDocument.reconcile_runs(p)
        base = TextRun('', p.bold, p.italic, p.underline, p.font, p.size)
        return [copy.copy(run) for run in p.runs for _ in run.text] if p.runs else [copy.copy(base) for _ in p.text]

    def crosses_table_cells(self, selected):
        return (selected.first_paragraph != selected.last_paragraph and
                any(p.table_id for p in self.paragraphs[selected.first_paragraph:selected.last_paragraph+1]))

    def _replace_selection(self, replacement):
        selected = self.selection
        if self.crosses_table_cells(selected):
            # Joining cells would leave the table with missing cells, which cannot be saved.
            raise ValueError(TABLE_SELECTION_NOTICE)
        first = self.paragraphs[selected.first_paragraph]
        last = self.paragraphs[selected.last_paragraph]
        a, b = selected.first_offset, selected.last_offset
        prefix, suffix = first.text[:a], last.text[b:]
        prefix_styles = self._character_styles(first)[:a]
        suffix_styles = self._character_styles(last)[b:]
        inherited = prefix_styles[-1] if prefix_styles else TextRun('')
        pieces = ['\n'] if replacement == '\n' else replacement.split('\n')
        self.checkpoint()
        first.text = prefix + pieces[0] + (suffix if len(pieces) == 1 else '')
        first.runs = self.compress_runs(first.text, prefix_styles + [inherited]*len(pieces[0]) +
                                        (suffix_styles if len(pieces) == 1 else []))
        new_paragraphs = []
        for middle in pieces[1:-1]:
            p = Paragraph(middle, line_spacing_twips=first.line_spacing_twips,
                          line_spacing_rule=first.line_spacing_rule)
            new_paragraphs.append(p)
        if len(pieces) > 1:
            tail = copy.deepcopy(last) if selected.last_paragraph > selected.first_paragraph else Paragraph('')
            tail.text = pieces[-1] + suffix
            tail.runs = self.compress_runs(tail.text, [inherited]*len(pieces[-1]) + suffix_styles)
            new_paragraphs.append(tail)
        self.paragraphs[selected.first_paragraph+1:selected.last_paragraph+1] = new_paragraphs
        self.cursor = selected.first_paragraph + len(new_paragraphs)
        self.insertion_position = (self.cursor,len(prefix)+len(pieces[0]) if len(pieces)==1 else len(pieces[-1]))
        self.navigation_position = None
        self.selection = None
        self.changed()

    def _apply_selection_action(self, selected, action):
        self.selection = selected
        self.cursor = selected.first_paragraph
        content = self.selected_text()
        if action in ('insert_before', 'insert_after'):
            position = (selected.first_paragraph, selected.first_offset) if action == 'insert_before' else (selected.last_paragraph, selected.last_offset)
            self.cursor = position[0]
            self.insertion_position = self.navigation_position = position
            self.selection = None
            self.dictating = True
            self.reading.set_text('\n'.join(p.text for p in self.paragraphs))
            self.reading.position = sum(len(p.text)+1 for p in self.paragraphs[:self.cursor]) + position[1]
            return 'Insert ' + action.split('_')[1] + ' ' + content + '.'
        if action.startswith('replace:'):
            if self.crosses_table_cells(selected):
                self.selection = None
                return TABLE_SELECTION_NOTICE
            replacement = action[len('replace:'):]
            self._replace_selection(replacement)
            return 'Replaced ' + content + ' with ' + replacement + '.'
        if action in ('select', 'find'):
            return ('Found and selected: ' if action == 'find' else 'Selected: ') + content
        if action in ('cut', 'delete') and any(p.table_id for p in self.paragraphs[selected.first_paragraph:selected.last_paragraph+1]) and selected.first_paragraph != selected.last_paragraph:
            if action == 'cut': copy_to_clipboard(content)
            self.checkpoint()
            for index in range(selected.first_paragraph,selected.last_paragraph+1):
                p = self.paragraphs[index]
                start = selected.first_offset if index == selected.first_paragraph else 0
                end = selected.last_offset if index == selected.last_paragraph else len(p.text)
                styles = self._character_styles(p)
                p.text = p.text[:start] + p.text[end:]
                p.runs = self.compress_runs(p.text,styles[:start]+styles[end:])
            self.selection = None
            self.insertion_position = (selected.first_paragraph,selected.first_offset)
            self.changed()
            return ('Cut: ' if action == 'cut' else 'Deleted: ') + content + '. Table cells remain. Say undo to restore the text.'
        if action in ('copy', 'cut'):
            copy_to_clipboard(content)
            if action == 'copy':
                return 'Copied: ' + content
        if action in ('cut', 'delete'):
            self._replace_selection('')
            return ('Cut: ' if action == 'cut' else 'Deleted: ') + content + '. Say undo to restore it.'
        style = {'bold': ('bold', True), 'underline': ('underline', True),
                 'italicize': ('italic', True), 'highlight': ('highlight', 'yellow')}[action]
        word_position = self.word_cursor
        self.checkpoint()
        for index in range(selected.first_paragraph, selected.last_paragraph+1):
            p = self.paragraphs[index]
            styles = self._character_styles(p)
            start = selected.first_offset if index == selected.first_paragraph else 0
            end = selected.last_offset if index == selected.last_paragraph else len(p.text)
            for position in range(start, end):
                setattr(styles[position], style[0], style[1])
            p.runs = self.compress_runs(p.text, styles)
        self.changed()
        self.word_cursor = word_position
        return action.capitalize() + ' applied to: ' + content

    def process(self, raw):
        self.drop_stale_selection()
        text = raw.strip()
        command = re.sub(r'[.!?]+$', '', text.lower()).strip()
        if not command:
            return 'I did not hear anything.'
        literal = re.fullmatch(r'(?:type|dictate) literally\s+(.+)', text, re.I | re.S)
        if literal: return self.append_text(literal[1], literal=True)
        if command == 'type new line': return self.append_text('new line', literal=True)
        command = ' '.join(command.split())
        self._undo_command = command   # names the change if this command makes one
        if self.replacement_candidates:
            if command in ('first item','last item','start of list','end of list'):
                self.replacement_index=0 if command in ('first item','start of list') else len(self.replacement_candidates)-1
                return ('Start of list. ' if self.replacement_index==0 else 'End of list. ')+self.replacement_context()
            if command in ('cancel', 'cancel replacement', 'never mind', 'no'):
                self.replacement_candidates = []
                self.replacement_text = ''
                return 'Replacement canceled. Text unchanged.'
            if command in ('next', 'next match', 'previous', 'previous match'):
                step = 1 if command.startswith('next') else -1
                new=self.replacement_index+step
                if not 0<=new<len(self.replacement_candidates):return 'Start of list.' if new<0 else 'End of list.'
                self.replacement_index = new
                return self.replacement_context()
            if command in ('read sentence', 'read context', 'read match', 'where am i'):
                return self.replacement_context()
            if command in ('that one', 'confirm that', 'okay', 'ok', 'confirm', 'yes'):
                return self.confirm_replacement()
            pick = re.fullmatch(r'(first|second|third|fourth|fifth|\d+)(?: match)?|match (\d+)', command)
            if pick:
                value = pick[1] or pick[2]
                number = int(value) if value.isdigit() else {'first':1,'second':2,'third':3,'fourth':4,'fifth':5}[value]
                if not 1 <= number <= len(self.replacement_candidates): return 'That match is not available.'
                self.replacement_index = number - 1
                return self.confirm_replacement()
            return 'Say next, previous, that one, or cancel replacement.'
        navigation = document_navigation_request(text)
        line_source = self.navigation_position or self.insertion_position
        if navigation:
            command = navigation
            self.selection = None
            self.selection_candidates, self.selection_action = [], ''
            self.selection_start_phrase = None
            self.insertion_position = None
            self.navigation_position = None
            self.pending_spacing = False
            self.pending_spacing_candidate = None
        if navigation in ('next paragraph','previous paragraph'):
            if not self.paragraphs: return 'The document is empty.'
            old = self.cursor
            self.cursor = max(0,min(len(self.paragraphs)-1,self.cursor+(1 if navigation=='next paragraph' else -1)))
            self.word_cursor = self.sentence_cursor = 0
            self.navigation_position = (self.cursor,0)
            self.reading.set_text('\n'.join(p.text for p in self.paragraphs))
            self.reading.position = sum(len(p.text)+1 for p in self.paragraphs[:self.cursor])
            # At the first or last paragraph, say so; repeating it alone sounded like a move.
            edge = '' if self.cursor != old else 'End of document. ' if navigation == 'next paragraph' else 'Beginning of document. '
            return edge + self.process('read current paragraph')
        if navigation in ('start of line','end of line'):
            if not self.paragraphs: return 'The document is empty.'
            p = self.paragraphs[self.cursor]
            offset = min(line_source[1], len(p.text)) if line_source and line_source[0] == self.cursor else len(p.text)
            start = p.text.rfind('\n', 0, offset) + 1
            end = p.text.find('\n', offset)
            end = len(p.text) if end < 0 else end
            self.insertion_position = self.navigation_position = (self.cursor, start if navigation == 'start of line' else end)
            return ('Start' if navigation == 'start of line' else 'End') + ' of line. ' + (p.text[start:end].strip() or 'Blank.')
        if command in FORMAT_READ_COMMANDS:
            return self.formatting_report(FORMAT_READ_COMMANDS[command])
        name = re.fullmatch(NAME_PATTERN, ' '.join(text.split()), re.I | re.S)
        if name:
            self.selection_candidates, self.selection_action = [], ''
            if not ' '.join(name[1].split()).strip(' .'): return 'Please say a document name after the naming command.'
            proposed, problem = checked_document_name(name[1])
            if problem: return problem
            old_path, previous_title = self.path, self.title
            new_path = self.folder / (proposed + '.docx')
            if new_path != old_path and any(p.name.casefold() == new_path.name.casefold() for p in self.folder.glob('*.docx')):
                return 'A document with that name already exists. Please choose another name.'
            self.title = proposed
            try: self.save()
            except OSError:
                self.title = previous_title
                return 'I could not save that file name. Your document remains open under its previous name.'
            save_as = command.startswith('save ')
            if save_as:
                for step in self.undo_steps + self.redo_steps: step.title = self.title
            else:
                self._push_undo(self._snapshot('the new name', title=previous_title))
            self.last_change_type = 'edit'
            if not save_as and new_path != old_path:
                try: old_path.unlink(missing_ok=True)
                except OSError: return 'Document named ' + new_path.name + '. The previous file could not be removed and remains on this computer.'
            return ('Saved document as ' if save_as else 'Document named ') + self.path.name + '.'
        if command in START_COMMANDS + END_COMMANDS:
            self.selection = None
            self.selection_candidates, self.selection_action = [], ''
            if not self.paragraphs: return 'The document is empty.'
            at_start = command in START_COMMANDS
            self.cursor = 0 if at_start else len(self.paragraphs)-1
            p = self.paragraphs[self.cursor]
            offset = 0 if at_start else len(p.text)
            self.insertion_position = self.navigation_position = (self.cursor, offset)
            self.word_cursor = 0 if at_start else max(0,len(re.findall(r'\S+',p.text))-1)
            self.sentence_cursor = 0 if at_start else max(0,len(re.findall(r'\S.*?(?:[.!?](?=\s|$)|$)',p.text,re.S))-1)
            self.reading.set_text('\n'.join(p.text for p in self.paragraphs), reset=True)
            if not at_start: self.reading.position = len(self.reading.text)
            return ('Beginning' if at_start else 'End')+' of document. '+(self.describe_cell() if p.table_id else p.text or 'Empty paragraph.')
        if command in LINE_BREAK_COMMANDS:
            self.selection_candidates, self.selection_action = [], ''
            if self.selection and self.selected_text():
                selected = self.selection
                if selected.first_paragraph != selected.last_paragraph and any(p.table_id for p in self.paragraphs[selected.first_paragraph:selected.last_paragraph+1]):
                    previous = self._snapshot(undo_name('new line'))
                    kept = list(self.undo_steps)
                    self._apply_selection_action(selected,'delete')
                    result = self.process('new line')
                    # One step for the whole change, as the user sees it.
                    self.undo_steps[:] = kept
                    self._push_undo(previous)
                    return result
                self._replace_selection('\n')
                return 'New line.'
            self.checkpoint()
            if not self.paragraphs:
                self.paragraphs.append(Paragraph('')); self.cursor = 0
            p = self.paragraphs[self.cursor]
            offset = self.insertion_position[1] if self.insertion_position and self.insertion_position[0] == self.cursor else len(p.text)
            p.text = p.text[:offset] + '\n' + p.text[offset:]
            self.insertion_position = (self.cursor,offset+1)
            self.changed()
            return 'New line.'
        if command in ('scratch','scratch that'):
            self.selection_candidates, self.selection_action = [], ''
            if self.selection and self.selected_text(): return self._apply_selection_action(self.selection,'delete')
            if self.last_change_type != 'dictation' or self.last_change is None:
                return 'There is no recent dictated text or selected text to scratch. Say undo for another change.'
            return self.process('undo')
        if self.pending_spacing:
            if command in ('cancel', 'cancel spacing', 'never mind'):
                self.pending_spacing = False
                self.pending_spacing_candidate = None
                return 'Canceled custom spacing. The paragraph was not changed.'
            if command in ('yes', 'yes please', 'okay', 'ok', 'confirm that', 'set it'):
                if self.pending_spacing_candidate is None:
                    return 'Please say the spacing value first, such as one point seven five.'
                value, rule = self.pending_spacing_candidate
                result = self.set_line_spacing(value, rule)
                if result.startswith('Set '):
                    self.pending_spacing = False
                    self.pending_spacing_candidate = None
                return result
            if command in ('no', 'no thanks', 'change it', 'try again', 'change spacing'):
                self.pending_spacing_candidate = None
                return 'Okay. Say the spacing value again, or say cancel.'
            if command in ('single space', 'single spaced', 'single spacing',
                           'double space', 'double spaced', 'double spacing'):
                result = self.set_line_spacing('1' if command.startswith('single') else '2')
                if result.startswith('Set '):
                    self.pending_spacing = False
                    self.pending_spacing_candidate = None
                return result
            if command in ('custom spacing', 'set custom spacing'):
                return 'Say a number such as one point seven five, or exactly eighteen points. I will ask if I heard it right.'
            match = re.fullmatch(r'(exactly |exact |at least )?(.+?)( points?)?', command)
            prefix, amount, points = match.groups()
            value = spoken_number(amount)
            if value is None:
                return 'I did not hear a spacing number. Say a number such as one point seven five, or exactly eighteen points. Say cancel to leave this choice.'
            rule = 'atLeast' if prefix == 'at least ' else 'exact' if prefix or points else 'auto'
            twips, error = self.validated_line_spacing(value, rule)
            if error:
                return error + ' Say another value, or say cancel.'
            self.pending_spacing_candidate = (value, rule)
            preview = copy.copy(self.paragraphs[self.cursor])
            preview.line_spacing_twips, preview.line_spacing_rule = twips, rule
            return 'I heard ' + self.spacing_description(preview) + '. Is that right? Say yes or no. You can also say cancel.'
        if command in ('single space', 'single spaced', 'single spacing'):
            return self.set_line_spacing('1')
        if command in ('double space', 'double spaced', 'double spacing'):
            return self.set_line_spacing('2')
        if command in ('custom spacing', 'set custom spacing', 'change custom spacing'):
            if not self.paragraphs:
                return 'Write a paragraph first, then say custom spacing.'
            self.pending_spacing = True
            self.pending_spacing_candidate = None
            return 'What spacing would you like for this paragraph? Say a number such as one point seven five, or exactly eighteen points. I will repeat it before changing the document.'
        structure = structural_selection_request(command)
        if structure:
            self.selection_candidates, self.selection_action = [], ''
            if not self.paragraphs:
                return 'The document is empty. There is no text to select.'
            kind, reference = structure
            if kind == 'all':
                selected = TextSelection(0, 0, len(self.paragraphs)-1, len(self.paragraphs[-1].text))
                self._apply_selection_action(selected, 'select')
                return 'Selected all text in the document. ' + str(len(self.paragraphs)) + (' paragraph.' if len(self.paragraphs) == 1 else ' paragraphs.')
            numbers = {'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'ten':10}
            if kind != 'paragraph':
                paragraph = self.paragraphs[self.cursor]
                patterns = {'character': r'[\s\S]', 'word': r'\S+',
                            'sentence': r'\S.*?(?:[.!?](?=\s|$)|$)', 'line': r'[^\n]+(?:\n|$)|\n'}
                spans = list(re.finditer(patterns[kind], paragraph.text, re.DOTALL))
                if not spans: return 'There is no ' + kind + ' here.'
                position = self.word_cursor if kind == 'word' else self.sentence_cursor if kind == 'sentence' else 0
                position_source = self.navigation_position or self.insertion_position
                if position_source and position_source[0] == self.cursor:
                    offset = position_source[1]
                    position = next((i for i,span in enumerate(spans) if span.start() <= offset < span.end()),len(spans)-1 if offset >= spans[-1].end() else position)
                if self.selection and self.selection.first_paragraph == self.cursor:
                    position = next((i for i, span in enumerate(spans) if span.start() <= self.selection.first_offset < span.end()), len(spans)-1 if self.selection.first_offset >= spans[-1].end() else position)
                if reference in ('current', 'this'): index = position
                elif reference == 'last': index = len(spans)-1
                elif reference == 'next': index = position+1
                elif reference == 'previous': index = position-1
                else: index = (int(reference) if reference.isdigit() else PARAGRAPH_ORDINALS.get(reference, numbers.get(reference, 0)))-1
                if not 0 <= index < len(spans):
                    return 'There is no ' + reference + ' ' + kind + ' in this paragraph.'
                span = spans[index]
                selected = TextSelection(self.cursor, span.start(), self.cursor, span.end())
                result = self._apply_selection_action(selected, 'select')
                if kind == 'word': self.word_cursor = index
                if kind == 'sentence': self.sentence_cursor = index
                return kind.capitalize() + ' ' + str(index+1) + '. ' + result
            if reference in ('current', 'this'): index = self.cursor
            elif reference == 'last': index = len(self.paragraphs)-1
            elif reference == 'next': index = self.cursor+1
            elif reference == 'previous': index = self.cursor-1
            else: index = (int(reference) if reference.isdigit() else PARAGRAPH_ORDINALS.get(reference, numbers.get(reference, 0)))-1
            if not 0 <= index < len(self.paragraphs):
                return ('There is no ' + reference + ' paragraph here.' if reference in ('next','previous') else
                        'That paragraph number is not available. The document has ' + str(len(self.paragraphs)) + ' paragraphs.')
            selected = TextSelection(index, 0, index, len(self.paragraphs[index].text))
            self.word_cursor = self.sentence_cursor = 0
            return 'Paragraph ' + str(index+1) + '. ' + self._apply_selection_action(selected, 'select')
        if self.selection_candidates:
            if command in ('first item','last item','start of list','end of list'):
                self.selection_index=0 if command in ('first item','start of list') else len(self.selection_candidates)-1
                return ('Start of list. ' if self.selection_index==0 else 'End of list. ')+self.match_context(self.selection_candidates,self.selection_index)
            if command in ('cancel', 'cancel selection', 'never mind', 'no'):
                self.selection_candidates = []
                self.selection_action = ''
                return 'Selection canceled. The text was not changed.'
            if command in ('next', 'next match', 'previous', 'previous match'):
                step = 1 if command.startswith('next') else -1
                new=self.selection_index+step
                if not 0<=new<len(self.selection_candidates):return 'Start of list.' if new<0 else 'End of list.'
                self.selection_index = new
                return self.match_context(self.selection_candidates, self.selection_index)
            if command in ('read sentence', 'read context', 'read match', 'where am i'):
                return self.match_context(self.selection_candidates, self.selection_index)
            if command in ('that one', 'confirm that', 'okay', 'ok', 'confirm', 'yes'):
                selected, action = self.selection_candidates[self.selection_index], self.selection_action
                self.selection_candidates, self.selection_action = [], ''
                return self._apply_selection_action(selected, action)
            words = {'first':1, 'second':2, 'third':3, 'fourth':4, 'fifth':5}
            pick = re.fullmatch(r'(first|second|third|fourth|fifth|\d+)(?: match)?', command)
            pick = pick or re.fullmatch(r'match (\d+)', command)
            if pick:
                number = words.get(pick.group(1), int(pick.group(1)) if pick.group(1).isdigit() else 0)
                if not 1 <= number <= len(self.selection_candidates):
                    return 'That match number is not available. Say another number or cancel.'
                selected, action = self.selection_candidates[number-1], self.selection_action
                self.selection_candidates, self.selection_action = [], ''
                return self._apply_selection_action(selected, action)
            return 'Say next, previous, that one, or cancel selection.'
        if command in ('what is selected', 'say selection', 'read selection', 'selected text', 'read selected text'):
            return 'Selected: ' + self.selected_text() if self.selection else 'No text is selected.'
        if command in ('clear selection', 'unselect', 'deselect', 'cancel selection'):
            self.selection = None
            return 'Selection cleared.'
        start_mark = re.fullmatch(r'(?:set )?selection start (?:at|on) (.+)', text, re.I)
        if start_mark:
            phrase = start_mark.group(1)
            if not self._phrase_spans(phrase):
                return 'I could not find those starting words.'
            self.selection_start_phrase = phrase
            return 'Start marked at ' + phrase + '. Say selection end at followed by the ending words.'
        end_mark = re.fullmatch(r'(?:set )?selection end (?:at|on) (.+)', text, re.I)
        if end_mark:
            if not getattr(self, 'selection_start_phrase', None):
                return 'Say selection start at followed by the starting words first.'
            result = self._select_range(self.selection_start_phrase, end_mark.group(1))
            if result.startswith(('Selected:', 'I found')):
                self.selection_start_phrase = None
            return result
        if command == 'delete current paragraph':
            command = 'delete paragraph'
        if command in ('delete line', 'delete current line'):
            if not self.paragraphs:
                return 'The document is empty.'
            p = self.paragraphs[self.cursor]
            position_source = self.navigation_position or self.insertion_position
            offset = min(position_source[1], len(p.text)) if position_source and position_source[0] == self.cursor else 0
            # A paragraph can hold several lines separated by line breaks; delete only this one.
            start = p.text.rfind('\n', 0, offset) + 1
            end = p.text.find('\n', offset)
            if end == -1:
                end = len(p.text)
                if start > 0: start -= 1
            else:
                end += 1
            line = p.text[start:end].strip('\n')
            self.selection = TextSelection(self.cursor, start, self.cursor, end)
            self._apply_selection_action(self.selection, 'delete')
            return 'Deleted line: ' + (line or 'blank line') + '. Say undo to restore it.'
        if command == 'delete current word':
            command = 'delete word'
        if command in ('copy', 'cut') and self.selection and self.selected_text():
            # "Copy" alone right after "Select all" means the selection, as Control+C does.
            # With nothing selected it stays text (see the caller).
            return self._apply_selection_action(self.selection, command)
        current = re.fullmatch(r'(select|highlight|bold|underline|italicize|copy|cut|delete) (?:the )?(?:current |this )?(word|sentence|paragraph|line|selection|that)', command)
        if current and current.group(2) in ('selection','that'):
            if not self.selection:
                return 'No text is selected. Say select followed by the words first.'
            return self._apply_selection_action(self.selection, current.group(1))
        if current and current.group(2) not in ('selection','that') and self.paragraphs and command != 'delete paragraph':
            p = self.paragraphs[self.cursor]
            if current.group(2) == 'word':
                words = list(re.finditer(r'\S+', p.text))
                if not words:
                    return 'There is no word here.'
                match = words[min(self.word_cursor,len(words)-1)]
                selected = TextSelection(self.cursor,match.start(),self.cursor,match.end())
            elif current.group(2) == 'sentence':
                sentences = list(re.finditer(r'\S.*?(?:[.!?](?=\s|$)|$)',p.text,re.DOTALL))
                if not sentences:
                    return 'There is no sentence here.'
                match = sentences[min(self.sentence_cursor,len(sentences)-1)]
                selected = TextSelection(self.cursor,match.start(),self.cursor,match.end())
            else:
                selected = TextSelection(self.cursor,0,self.cursor,len(p.text))
            return self._apply_selection_action(selected, current.group(1))
        insert = re.fullmatch(r'insert (before|after) (.+)', text.rstrip('.!?').strip(), re.I)
        if insert:
            return self._select_phrase(insert[2], 'insert_' + insert[1].lower())
        range_match = re.fullmatch(r'(?:select|highlight|bold|underline|italicize|copy|cut|delete) (?:from )?(.+?) (?:through|to) (.+)', text, re.I)
        if range_match:
            action = range_match.group(0).split()[0].lower()
            return self._select_range(range_match.group(1), range_match.group(2), action)
        named_action = re.fullmatch(r'(select|highlight|bold|underline|italicize|copy|cut|find|delete) (.+)', command, re.I)
        if named_action and named_action.group(2).lower() not in ('word','sentence','paragraph','line','table','row','column','cell','selected','selection','that','this'):
            phrase = re.sub(r'^the (?:word|phrase|sentence) ', '', named_action.group(2), flags=re.I)
            return self._select_phrase(phrase, named_action.group(1).lower())
        if command in ('paste', 'paste that', 'paste text'):
            pasted = paste_from_clipboard()
            if not pasted:
                return 'Nothing has been copied or cut yet.'
            if '\n' in pasted and self.table_cell():
                return 'Paste one line at a time inside a table cell.'
            if not self.paragraphs:
                self.paragraphs.append(Paragraph(''))
                self.cursor = 0
            if self.selection is None:
                self.selection = TextSelection(self.cursor,len(self.paragraphs[self.cursor].text),
                                               self.cursor,len(self.paragraphs[self.cursor].text))
            if self.crosses_table_cells(self.selection):
                return TABLE_SELECTION_NOTICE
            self._replace_selection(pasted)
            return 'Pasted: ' + pasted + '. Say undo to remove it.'
        request = reading_request(command)
        if request == ('paragraph', 0) and self.paragraphs:
            # The shared reading cursor skips blank paragraphs, which would move the
            # editing position to a neighbour and send dictation to the wrong place.
            self.reading.set_text('\n'.join(p.text for p in self.paragraphs))
            self.reading.position = self.reading.continuation = sum(len(p.text)+1 for p in self.paragraphs[:self.cursor])
            self.word_cursor = self.sentence_cursor = 0
            p = self.paragraphs[self.cursor]
            if p.table_id:
                return self.describe_cell()
            return p.text.strip() or 'Empty paragraph.'
        if request:
            if not any(p.text.strip() for p in self.paragraphs):
                return self.EMPTY_NOTICE   # as every other command says it
            self.reading.set_text('\n'.join(p.text for p in self.paragraphs))
            unit, direction = request
            if unit!='read' and self.paragraphs:
                current_base = sum(len(p.text)+1 for p in self.paragraphs[:self.cursor])
                current_end = current_base+len(self.paragraphs[self.cursor].text)
                if not current_base <= self.reading.position <= current_end: self.reading.position = current_base
            result = self.reading.read(direction == 'top') if unit == 'read' else self.reading.move(unit, direction)
            base = 0
            local = 0
            for i,p in enumerate(self.paragraphs):
                if self.reading.position <= base+len(p.text) or i==len(self.paragraphs)-1:
                    self.cursor = i
                    local = min(len(p.text),max(0,self.reading.position-base))
                    break
                base += len(p.text)+1
            if navigation or unit!='read' and direction!=0:self.navigation_position=(self.cursor,local)
            if unit in ('paragraph','line'):
                self.word_cursor = self.sentence_cursor = 0
            if unit == 'word':
                self.word_cursor = len(re.findall(r'\S+', self.paragraphs[self.cursor].text[:self.reading.position - sum(len(p.text)+1 for p in self.paragraphs[:self.cursor])])) if self.paragraphs else 0
                self.word_cursor = max(0, self.word_cursor)
            if unit == 'sentence' and self.paragraphs:
                self.sentence_cursor = max(0, len(re.findall(r'\S.*?[.!?](?=\s|$)', self.paragraphs[self.cursor].text[:local])) )
            return result
        if command in UNDO_REQUESTS:
            return self.undo_or_redo(UNDO_REQUESTS[command] == 'redo')
        if command in ('save document', 'save my document', 'finish document', 'save', 'save it'):
            self.dictating = False
            self.save()
            return 'Saved the Word document as ' + self.path.name + '.'
        if command in ('start dictation', 'dictate', 'continue writing'):
            self.dictating = True
            return 'Dictation is on. Say new paragraph for the next paragraph, or pause dictation.'
        if command in ('pause dictation', 'stop dictation'):
            self.dictating = False
            return 'Dictation paused.'
        if command == 'new paragraph':
            if self.table_cell():
                return 'You are in a table. Say next cell, or say leave table to write after it.'
            self.checkpoint()
            if not self.paragraphs:
                # An empty document has nowhere to put the cursor yet: create the first paragraph.
                self.paragraphs.append(Paragraph(''))
                self.cursor = 0
                self.insertion_position = None
                self.selection = None
                self.changed()
                return 'New paragraph.'
            current = self.paragraphs[self.cursor]
            self.paragraphs.insert(self.cursor + 1, Paragraph('',
                kind=current.kind if current.kind.endswith('list') else 'Normal',
                alignment=current.alignment,
                line_spacing_twips=current.line_spacing_twips,
                line_spacing_rule=current.line_spacing_rule))
            self.cursor += 1
            self.insertion_position = None
            self.selection = None
            self.changed()
            return 'New paragraph.'
        if command in ('read document', 'read all'):
            return ' '.join(p.text for p in self.paragraphs) or 'The document is empty.'
        if command in ('read paragraph', 'read current paragraph', 'where am i'):
            if not self.paragraphs:
                return 'The document is empty.'
            p = self.paragraphs[self.cursor]
            if p.table_id:
                return self.describe_cell()
            return (f'Paragraph {self.cursor + 1} of {len(self.paragraphs)}. ' if command=='where am i' else '')+(p.text or 'Empty paragraph.')
        if command in ('read sentence', 'read current sentence', 'next sentence', 'previous sentence'):
            if not self.paragraphs:
                return 'The document is empty.'
            sentences = re.split(r'(?<=[.!?])\s+', self.paragraphs[self.cursor].text.strip())
            sentences = [s for s in sentences if s]
            if not sentences:
                return 'This paragraph is empty.'
            if command == 'next sentence':
                self.sentence_cursor = min(len(sentences) - 1, self.sentence_cursor + 1)
            elif command == 'previous sentence':
                self.sentence_cursor = max(0, self.sentence_cursor - 1)
            self.sentence_cursor = min(self.sentence_cursor, len(sentences) - 1)
            return sentences[self.sentence_cursor]
        if command in ('read word', 'next word', 'previous word', 'spell word'):
            if not self.paragraphs:
                return 'The document is empty.'
            words = self.paragraphs[self.cursor].text.split()
            if not words:
                return 'This paragraph is empty.'
            if command == 'next word':
                self.word_cursor = min(len(words) - 1, self.word_cursor + 1)
            elif command == 'previous word':
                self.word_cursor = max(0, self.word_cursor - 1)
            self.word_cursor = min(self.word_cursor, len(words) - 1)
            word = words[self.word_cursor]
            return (' '.join(word) if command == 'spell word' else word)
        if command in ('delete word', 'remove word') or command.startswith('replace word with '):
            if not self.paragraphs or not self.paragraphs[self.cursor].text.strip():
                return 'There is no word here.'
            p = self.paragraphs[self.cursor]
            words = list(re.finditer(r'\S+', p.text))
            index = min(self.word_cursor, len(words) - 1)
            word = words[index]
            replacement = clean_dictation(text[len('replace word with '):].strip().rstrip('.,;:!?').strip()) if command.startswith('replace word with ') else ''
            if command.startswith('replace word with ') and not replacement:
                return 'Please say the replacement word.'
            if replacement:
                end = word.end() - (len(word.group()) - len(word.group().rstrip('.,;:!?')))
                return self._apply_selection_action(TextSelection(self.cursor, word.start(), self.cursor, end), 'replace:' + replacement)
            self.checkpoint()
            p.text = (p.text[:word.start()] + replacement + p.text[word.end():]).strip()
            p.text = re.sub(r' {2,}', ' ', p.text)
            self.changed()
            return ('Replaced ' + word.group() + ' with ' + replacement + '. ' if replacement else 'Deleted ' + word.group() + '. ') + (p.text or 'Paragraph is now empty.')
        if command in ('delete sentence', 'remove sentence') or command.startswith('replace sentence with '):
            if not self.paragraphs or not self.paragraphs[self.cursor].text.strip():
                return 'There is no sentence here.'
            p = self.paragraphs[self.cursor]
            spans = list(re.finditer(r'\S.*?(?:[.!?](?=\s|$)|$)', p.text, re.DOTALL))
            index = min(self.sentence_cursor, len(spans) - 1)
            sentence = spans[index]
            replacement = text[len('replace sentence with '):].strip() if command.startswith('replace sentence with ') else ''
            if command.startswith('replace sentence with ') and not replacement:
                return 'Please say the replacement sentence.'
            self.checkpoint()
            p.text = (p.text[:sentence.start()] + replacement + p.text[sentence.end():]).strip()
            p.text = re.sub(r' {2,}', ' ', p.text)
            self.changed()
            return ('Replaced the sentence. ' if replacement else 'Deleted the sentence. ') + (p.text or 'Paragraph is now empty.')
        match = re.match(r'^(change|replace) (.+?) (?:to|with) (.+)$', text, re.I)
        if match and (match[2].lower().strip() in ('paragraph', 'current paragraph', 'the paragraph', 'this paragraph') or
                      match[1].lower() == 'change' and match[2].lower().strip() in FORMAT_CHANGE_TARGETS):
            match = None
        if match:
            before, after = match[2], match[3]
            after = clean_dictation(after.strip().rstrip('.,;:!?').strip())
            if not after: return 'Please say the replacement text.'
            spans = self._phrase_spans(before)
            if not spans: return 'I could not find ' + before + ' in the text.'
            self.replacement_candidates = spans
            self.replacement_index = 0
            self.replacement_text = after
            if len(spans) == 1: return self.confirm_replacement()
            return self.replacement_context() + '. Say next, previous, that one, or cancel replacement.'
        if command in ('previous paragraph', 'go back a paragraph'):
            self.cursor = max(0, self.cursor - 1)
            self.sentence_cursor = self.word_cursor = 0
            return self.process('read paragraph')
        if command in ('next paragraph', 'go forward a paragraph'):
            self.cursor = min(len(self.paragraphs) - 1, self.cursor + 1) if self.paragraphs else 0
            self.sentence_cursor = self.word_cursor = 0
            return self.process('read paragraph')
        if command in ('delete paragraph', 'remove paragraph'):
            if not self.paragraphs:
                return 'The document is empty.'
            if self.table_cell():
                return 'In a table, say clear cell or delete table. A table cell cannot be removed by deleting a paragraph.'
            self.checkpoint()
            removed = self.paragraphs.pop(self.cursor)
            self.cursor = min(self.cursor, max(0, len(self.paragraphs) - 1))
            self.changed()
            return 'Deleted paragraph: ' + removed.text
        whole_paragraph = re.match(r'replace (?:the |current |this )?paragraph with (.+)$', text, re.I | re.S)
        if whole_paragraph:
            if not self.paragraphs:
                return 'The document is empty.'
            self.checkpoint()
            self.paragraphs[self.cursor].text = whole_paragraph[1]
            self.changed()
            return 'Replaced the paragraph with: ' + self.paragraphs[self.cursor].text
        create = re.fullmatch(r'(?:create|insert|add) (?:a )?table with (\d+|one|two|three|four|five|six|seven|eight|nine|ten) rows? and (\d+|one|two|three|four|five|six|seven|eight|nine|ten) columns?', command)
        if create:
            words = dict(zip('one two three four five six seven eight nine ten'.split(), range(1, 11)))
            rows, columns = (words.get(n, int(n) if n.isdigit() else 0) for n in create.groups())
            if not (1 <= rows <= 20 and 1 <= columns <= 10 and rows*columns <= 100):
                return 'Please choose up to 20 rows, 10 columns, and 100 cells.'
            if self.table_cell():
                return 'Leave this table before creating another table.'
            self.checkpoint()
            table_id = max((p.table_id for p in self.paragraphs), default=0) + 1
            cells = [Paragraph('', table_id=table_id, row=r, column=c)
                     for r in range(1, rows+1) for c in range(1, columns+1)]
            insertion = self.cursor + 1 if self.paragraphs else 0
            self.paragraphs[insertion:insertion] = cells
            self.cursor = insertion
            self.changed()
            return f'Created a table with {rows} rows and {columns} columns. Row 1, column 1. Speak to fill this cell; say next cell to move.'
        if command in ('read table', 'review table'):
            cell = self.table_cell()
            if cell is None:
                return 'You are not in a table.'
            cells = [p for p in self.paragraphs if p.table_id == cell.table_id]
            return f'Table, {max(p.row for p in cells)} rows and {max(p.column for p in cells)} columns. ' + ' '.join(
                f'Row {p.row}, column {p.column}: {p.text or "empty"}.' for p in cells)
        if command in ('add row', 'insert row', 'add row below'):
            cell = self.table_cell()
            if cell is None:
                return 'You are not in a table.'
            cells = [p for p in self.paragraphs if p.table_id == cell.table_id]
            rows, columns = max(p.row for p in cells), max(p.column for p in cells)
            if rows >= 20 or len(cells)+columns > 100:
                return 'The table is at its size limit.'
            self.checkpoint()
            insert_at = next((i for i,p in enumerate(self.paragraphs)
                              if p.table_id == cell.table_id and p.row > cell.row),
                             self.cursor + columns-cell.column+1)
            for p in cells:
                if p.row > cell.row:
                    p.row += 1
            self.paragraphs[insert_at:insert_at] = [Paragraph('', table_id=cell.table_id, row=cell.row+1, column=c)
                                                     for c in range(1,columns+1)]
            self.cursor = insert_at
            self.changed()
            return 'Added a row. ' + self.describe_cell()
        if command in ('add column', 'insert column', 'add column right'):
            cell = self.table_cell()
            if cell is None:
                return 'You are not in a table.'
            cells = [p for p in self.paragraphs if p.table_id == cell.table_id]
            rows, columns = max(p.row for p in cells), max(p.column for p in cells)
            if columns >= 10 or len(cells)+rows > 100:
                return 'The table is at its size limit.'
            self.checkpoint()
            table_id, target_row, target_column = cell.table_id, cell.row, cell.column+1
            for row in range(rows, 0, -1):
                in_row = [(i,p) for i,p in enumerate(self.paragraphs)
                          if p.table_id == cell.table_id and p.row == row]
                insert_at = next((i for i,p in in_row if p.column > cell.column), in_row[-1][0]+1)
                for _,p in in_row:
                    if p.column > cell.column:
                        p.column += 1
                self.paragraphs.insert(insert_at, Paragraph('', table_id=cell.table_id, row=row, column=cell.column+1))
            self.cursor = next(i for i,p in enumerate(self.paragraphs)
                               if (p.table_id,p.row,p.column) == (table_id,target_row,target_column))
            self.changed()
            return 'Added a column. ' + self.describe_cell()
        if command in ('delete row', 'remove row'):
            cell = self.table_cell()
            if cell is None:
                return 'You are not in a table.'
            cells = [p for p in self.paragraphs if p.table_id == cell.table_id]
            rows = max(p.row for p in cells)
            if rows == 1:
                return 'This is the only row. Say delete table if you want to remove the whole table.'
            table_id, row, column = cell.table_id, cell.row, cell.column
            self.checkpoint()
            self.paragraphs = [p for p in self.paragraphs if not (p.table_id == table_id and p.row == row)]
            for p in self.paragraphs:
                if p.table_id == table_id and p.row > row:
                    p.row -= 1
            self.cursor = next(i for i,p in enumerate(self.paragraphs) if
                               (p.table_id,p.row,p.column) == (table_id,min(row,rows-1),column))
            self.changed()
            return 'Deleted the row. Say undo to restore it. ' + self.describe_cell()
        if command in ('delete column', 'remove column'):
            cell = self.table_cell()
            if cell is None:
                return 'You are not in a table.'
            cells = [p for p in self.paragraphs if p.table_id == cell.table_id]
            columns = max(p.column for p in cells)
            if columns == 1:
                return 'This is the only column. Say delete table if you want to remove the whole table.'
            table_id, row, column = cell.table_id, cell.row, cell.column
            self.checkpoint()
            self.paragraphs = [p for p in self.paragraphs if not (p.table_id == table_id and p.column == column)]
            for p in self.paragraphs:
                if p.table_id == table_id and p.column > column:
                    p.column -= 1
            self.cursor = next(i for i,p in enumerate(self.paragraphs) if
                               (p.table_id,p.row,p.column) == (table_id,row,min(column,columns-1)))
            self.changed()
            return 'Deleted the column. Say undo to restore it. ' + self.describe_cell()
        if command in ('read cell', 'what cell', 'where in table'):
            return self.describe_cell()
        if command in ('next cell', 'move right a cell'):
            cell = self.table_cell()
            if cell is None:
                return 'You are not in a table.'
            for index in range(self.cursor+1, len(self.paragraphs)):
                if self.paragraphs[index].table_id == cell.table_id:
                    self.cursor = index
                    return self.describe_cell()
            # At the last (or first) cell, say where you stay, as at the other table edges.
            return 'End of table. ' + self.describe_cell()
        if command in ('previous cell', 'move left a cell'):
            cell = self.table_cell()
            if cell is None:
                return 'You are not in a table.'
            for index in range(self.cursor-1, -1, -1):
                if self.paragraphs[index].table_id == cell.table_id:
                    self.cursor = index
                    return self.describe_cell()
            # At the last (or first) cell, say where you stay, as at the other table edges.
            return 'Start of table. ' + self.describe_cell()
        if command in ('next row', 'row down'):
            return self.table_move(row_delta=1)
        if command in ('previous row', 'row up'):
            return self.table_move(row_delta=-1)
        if command in ('next column', 'column right'):
            return self.table_move(column_delta=1)
        if command in ('previous column', 'column left'):
            return self.table_move(column_delta=-1)
        if command in ('clear cell', 'delete cell contents'):
            if not self.table_cell():
                return 'You are not in a table.'
            self.checkpoint()
            self.paragraphs[self.cursor].text = ''
            self.changed()
            return self.describe_cell()
        if command in ('delete table', 'remove table'):
            cell = self.table_cell()
            if cell is None:
                return 'You are not in a table.'
            self.checkpoint()
            self.paragraphs = [p for p in self.paragraphs if p.table_id != cell.table_id]
            self.cursor = min(self.cursor, max(0, len(self.paragraphs)-1))
            self.changed()
            return 'Deleted the table. Say undo to restore it.'
        if command in ('leave table', 'after table'):
            cell = self.table_cell()
            if cell is None:
                return 'You are not in a table.'
            end = next((i for i in range(self.cursor+1, len(self.paragraphs)) if self.paragraphs[i].table_id != cell.table_id), len(self.paragraphs))
            if end == len(self.paragraphs):
                self.checkpoint()
                self.paragraphs.append(Paragraph(''))
                self.changed()
            self.cursor = end
            return 'After the table. Speak to write a paragraph.'
        heading = re.fullmatch(r'(?:make this |make paragraph |set |apply )?(?:a )?heading (?:level )?([1-6]|one|two|three|four|five|six)', command)
        if heading:
            levels = {'one':1,'two':2,'three':3,'four':4,'five':5,'six':6}
            level = levels.get(heading.group(1), heading.group(1))
            return self.format_current(kind=f'Heading {level}')
        if command in ('make this a bulleted list', 'bullet list', 'bulleted list', 'make this a bullet'):
            return self.format_current(kind='Bulleted list')
        if command in ('make this a numbered list', 'numbered list', 'number list'):
            return self.format_current(kind='Numbered list')
        if command in ('remove list', 'end list', 'make this a normal paragraph'):
            return self.format_current(kind='Normal')
        alignment = re.fullmatch(r'(?:align|justify) (?:this |paragraph )?(left|right|center|centre|justified)', command)
        if alignment:
            value = {'centre':'center', 'justified':'both'}.get(alignment.group(1), alignment.group(1))
            return self.format_current(alignment=value)
        if command in ('read line spacing', 'what is the line spacing', 'what line spacing', 'line spacing status'):
            return self.spacing_description(self.paragraphs[self.cursor]) if self.paragraphs else 'The document is empty.'
        if command == 'read document line spacing':
            if not self.paragraphs:
                return 'The document is empty.'
            descriptions = {self.spacing_description(p) for p in self.paragraphs}
            return descriptions.pop() + ' throughout the document.' if len(descriptions) == 1 else 'Line spacing varies between paragraphs. Say read line spacing in each paragraph.'
        if command in ('reset document line spacing', 'default document line spacing'):
            if not self.paragraphs:
                return 'The document is empty.'
            self.checkpoint()
            for p in self.paragraphs:
                p.line_spacing_twips = 0
                p.line_spacing_rule = 'auto'
            self.changed()
            return 'Restored default line spacing throughout the document.'
        if command in ('default line spacing', 'reset line spacing', 'remove line spacing'):
            if not self.paragraphs:
                return 'The document is empty.'
            self.checkpoint()
            self.paragraphs[self.cursor].line_spacing_twips = 0
            self.paragraphs[self.cursor].line_spacing_rule = 'auto'
            self.changed()
            return 'Restored default line spacing for this paragraph.'
        whole_document = bool(re.match(r'^(?:set |change )?(?:document|all) line spacing ', command))
        spacing_command = re.sub(r'^(?:set |change )?(?:document|all) line spacing ', 'line spacing ', command) if whole_document else command
        spacing = re.fullmatch(r'(?:set |change )?line spacing (?:to )?(?:exactly |exact |at least )?(\d+(?:\.\d+)?)(?: points?)?', spacing_command)
        if spacing:
            rule = ('atLeast' if 'at least ' in spacing_command else 'exact' if
                    ('exactly ' in spacing_command or 'exact ' in spacing_command or 'point' in spacing_command) else 'auto')
            return self.set_line_spacing(spacing.group(1), rule, whole_document)
        named_spacing = re.fullmatch(r'(?:set |change )?line spacing (?:to )?(single|double|one and a half|one point five|triple)', spacing_command)
        if named_spacing:
            value = {'single':'1', 'double':'2', 'one and a half':'1.5',
                     'one point five':'1.5', 'triple':'3'}[named_spacing.group(1)]
            return self.set_line_spacing(value, whole_document=whole_document)
        spoken_spacing = re.fullmatch(r'(?:set |change )?line spacing (?:to )?(exactly |exact |at least )?(.+?)( points?)?', spacing_command)
        if spoken_spacing:
            prefix, amount, points = spoken_spacing.groups()
            value = spoken_number(amount)
            if value is None:
                return 'Please say a number, such as one point seven five, or exactly eighteen points.'
            rule = 'atLeast' if prefix == 'at least ' else 'exact' if prefix or points else 'auto'
            return self.set_line_spacing(value, rule, whole_document)
        combined = re.fullmatch(r'(?:set|change|use) font (?!size\b)(?:name )?(?:to )?(.+?) (?:and )?(?:(?:font )?size (?:to )?)?(\d+|(?:six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy)(?:[ -](?:one|two|three|four|five|six|seven|eight|nine))?)(?: points?)?',text,re.I)
        if combined:
            name,amount = combined.groups()
            name = name.strip()
            value = spoken_number(amount)
            if not value or '.' in value or not 6 <= int(value) <= 72: return 'Choose a font size from 6 to 72 points.'
            if not re.fullmatch(r'[\w][\w .-]{0,59}',name,re.UNICODE): return 'Please say a font name using letters and numbers.'
            return self.format_current(font=name,size=int(value))
        font = re.fullmatch(r'(?:set|change|use) font (?!size\b)(?:name )?(?:to )?(.+)', text, re.I)
        if font:
            name = font.group(1).strip()
            if not re.fullmatch(r'[\w][\w .-]{0,59}', name, re.UNICODE):
                return 'Please say a font name using letters and numbers.'
            return self.format_current(font=name)
        size = re.fullmatch(r'(?:(?:set|change|use) (?:font )?size (?:to )?|make it )(.+?)(?: points?)?', command)
        if size:
            value = spoken_number(size.group(1))
            if value is None or '.' in value: return 'Please say a whole font size from 6 to 72 points.'
            points = int(value)
            if not 6 <= points <= 72:
                return 'Choose a font size from 6 to 72 points.'
            return self.format_current(size=points)
        if command in ('make this underlined', 'underline paragraph', 'underline this'):
            return self.format_current(underline=True)
        if command in ('remove underline', 'not underlined'):
            return self.format_current(underline=False)
        word_format = {
            'bold word': ('bold', True), 'remove bold from word': ('bold', False),
            'italicize word': ('italic', True), 'italic word': ('italic', True),
            'remove italic from word': ('italic', False),
            'underline word': ('underline', True), 'remove underline from word': ('underline', False),
        }
        if command in word_format:
            key, value = word_format[command]
            return self.format_word(**{key: value})
        word_font = re.fullmatch(r'(?:set|change) word font (?!size\b)(?:to )?(.+)', text, re.I)
        if word_font:
            name = word_font.group(1).strip()
            if not re.fullmatch(r'[\w][\w .-]{0,59}', name, re.UNICODE):
                return 'Please say a font name using letters and numbers.'
            return self.format_word(font=name)
        word_size = re.fullmatch(r'(?:set|change) word size (?:to )?(\d+)(?: points?)?', command)
        if word_size:
            points = int(word_size.group(1))
            if not 6 <= points <= 72:
                return 'Choose a font size from 6 to 72 points.'
            return self.format_word(size=points)
        if command in ('what formatting', 'describe formatting', 'read formatting'):
            return self.formatting_report('all')
        if command in ('make this a heading', 'make paragraph a heading', 'heading one'):
            return self.format_current(kind='Heading 1')
        if command in ('make this normal text', 'normal text'):
            return self.format_current(kind='Normal')
        if command in ('make this bold', 'bold paragraph'):
            return self.format_current(bold=True)
        if command in ('remove bold', 'not bold'):
            return self.format_current(bold=False)
        if command in ('make this italic', 'italic paragraph'):
            return self.format_current(italic=True)
        if command in ('remove italic', 'not italic'):
            return self.format_current(italic=False)
        if command in ('list headings', 'what are the headings'):
            # "Heading 3: Plans" sounded like heading level 3; it was the paragraph number.
            headings = [(i, p) for i, p in enumerate(self.paragraphs) if p.kind.startswith('Heading')]
            return ('. '.join(f'{p.text.strip() or "Empty heading"}, heading level {p.kind[-1]}, paragraph {i + 1}'
                              for i, p in headings) + '.') if headings else 'No headings yet.'
        if command in ('next heading', 'go to next heading','previous heading','go to previous heading'):
            backwards='previous' in command
            for i in (range(self.cursor-1,-1,-1) if backwards else range(self.cursor + 1, len(self.paragraphs))):
                if self.paragraphs[i].kind.startswith('Heading'):
                    self.cursor = i
                    self.word_cursor = self.sentence_cursor = 0
                    self.navigation_position = (i,0)
                    return self.process('read paragraph')
            return 'No previous headings.' if backwards else 'No more headings.'
        if command == 'help with documents':
            return 'Speak normally to write. Say new paragraph, heading level two, bold word, underline this, set font to Arial, single space, double space, or custom spacing. Say bulleted list, numbered list, create table with two rows and two columns, next cell, read formatting, undo, redo, or save document.'
        if self.dictating:
            return self.append_text(text)
        return 'I did not recognize that document request. Say help with documents for examples.'

    def formatting_report(self, detail='all'):
        self.drop_stale_selection()
        if not self.paragraphs: return 'The document is empty.'
        selected = self.selection
        paragraphs = self.paragraphs[selected.first_paragraph:selected.last_paragraph+1] if selected else [self.paragraphs[self.cursor]]
        styles = []
        if selected:
            for index in range(selected.first_paragraph,selected.last_paragraph+1):
                p = self.paragraphs[index]
                a = selected.first_offset if index==selected.first_paragraph else 0
                b = selected.last_offset if index==selected.last_paragraph else len(p.text)
                styles.extend(self._character_styles(p)[a:b])
        else:
            p = paragraphs[0]
            position_source = self.navigation_position or self.insertion_position
            offset = position_source[1] if position_source and position_source[0]==self.cursor else next((m.start() for i,m in enumerate(re.finditer(r'\S+',p.text)) if i==self.word_cursor),0)
            characters = self._character_styles(p)
            if characters: styles=[characters[min(offset,len(characters)-1)]]
        if not styles: styles=[TextRun('',p.bold,p.italic,p.underline,p.font,p.size) for p in paragraphs]
        def unique(values):
            values=list(dict.fromkeys(values))
            return values[0] if len(values)==1 else 'mixed'
        parts = {'font':'Font '+unique([style.font or 'default' for style in styles]),
                 'size':'Font size '+unique([str(style.size)+' points' if style.size else 'default' for style in styles]),
                 'kind':'Style '+unique([p.kind for p in paragraphs]),
                 'highlight':'Highlight '+unique([style.highlight or 'none' for style in styles]),
                 'alignment':'Alignment '+unique(['justified' if p.alignment=='both' else p.alignment for p in paragraphs]),
                 'spacing':unique([self.spacing_description(p) for p in paragraphs])}
        if parts['spacing']=='mixed': parts['spacing']='Mixed line spacing'
        for name in ('bold','italic','underline'):
            values=unique([name if getattr(style,name) else 'not '+name for style in styles])
            parts[name]=('Mixed '+name) if values=='mixed' else values.capitalize()
        return '. '.join(parts[key] for key in ('font','size','kind','bold','italic','underline','highlight','alignment','spacing'))+'.' if detail=='all' else parts[detail]+'.'

    def format_current(self, **changes):
        self.drop_stale_selection()
        if not self.paragraphs:
            return 'The document is empty.'
        self.checkpoint()
        if self.selection:
            selected = self.selection
            for index in range(selected.first_paragraph, selected.last_paragraph+1):
                p = self.paragraphs[index]
                styles = self._character_styles(p)
                start = selected.first_offset if index == selected.first_paragraph else 0
                end = selected.last_offset if index == selected.last_paragraph else len(p.text)
                for name, value in changes.items():
                    if name in ('bold','italic','underline','font','size'):
                        for position in range(start,end): setattr(styles[position],name,value)
                        if start == 0 and end == len(p.text): setattr(p,name,value)
                    else: setattr(p,name,value)
                p.runs = self.compress_runs(p.text, styles)
            self.changed()
            return 'Formatted the selection. ' + ', '.join(f'{name}: {value}' for name,value in changes.items()) + '.'
        p = self.paragraphs[self.cursor]
        self.reconcile_runs(p)
        for name, value in changes.items():
            setattr(p, name, value)
            if name in ('bold','italic','underline','font','size'):
                for run in p.runs:
                    setattr(run, name, value)
        self.changed()
        details = ', '.join(f'{name.replace("kind", "style")}: {value}' for name, value in changes.items())
        return 'Formatted the current ' + ('cell' if self.table_cell() else 'paragraph') + '. ' + details + '.'
