"""Share document selection semantics with editable text fields."""
from document_editor import VoiceDocument, Paragraph, TextSelection, structural_selection_request


def selection_command(text):
    command = text.lower().strip().rstrip('.!?')
    return bool(structural_selection_request(command)) or command in (
        'scratch','scratch that','what is selected', 'read selection', 'read selected text', 'clear selection', 'cancel selection')


class FieldSelection:
    def __init__(self):
        self.editor = VoiceDocument(__import__('pathlib').Path(__import__('tempfile').gettempdir()), _opening=True)
        self.text = None

    def select(self, text, command, start=None, end=None):
        self.edited_text = None
        if text != self.text:
            self.editor.paragraphs = [Paragraph(part) for part in text.split('\n\n')]
            self.editor.selection = None
            self.editor.cursor = self.editor.word_cursor = self.editor.sentence_cursor = 0
            self.text = text
        def location(offset):
            base = 0
            for i, p in enumerate(self.editor.paragraphs):
                if offset <= base + len(p.text): return i, max(0, offset-base)
                base += len(p.text)+2
            return len(self.editor.paragraphs)-1, len(self.editor.paragraphs[-1].text)
        if start is not None:
            i, a = location(start); j, b = location(end if end is not None else start)
            self.editor.cursor = i
            self.editor.selection = TextSelection(i,a,j,b)
        if not text:
            return 'This field is empty. There is no text to select.', None
        normalized = command.lower().strip().rstrip('.!?')
        normalized = {'read selected text':'read selection','cancel selection':'clear selection'}.get(normalized,normalized)
        if start is not None and start == end and not structural_selection_request(normalized):
            self.editor.selection = None
        if normalized in ('scratch','scratch that'):
            selected = self.editor.selection
            if not selected or not self.editor.selected_text(): return 'No text is selected in this field.', None
            a = sum(len(p.text)+2 for p in self.editor.paragraphs[:selected.first_paragraph])+selected.first_offset
            b = sum(len(p.text)+2 for p in self.editor.paragraphs[:selected.last_paragraph])+selected.last_offset
            self.edited_text = text[:a]+text[b:]
            self.editor.selection = None
            self.text = None
            return 'Deleted selected text.', (a,a)
        result = self.editor.process(normalized)
        selected = self.editor.selection
        if selected is None: return result, None
        def absolute(index, offset):
            return sum(len(p.text)+2 for p in self.editor.paragraphs[:index])+offset
        return result, (absolute(selected.first_paragraph,selected.first_offset),
                        absolute(selected.last_paragraph,selected.last_offset))
