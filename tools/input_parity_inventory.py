"""Write tools/input-parity-inventory.md: every user action with its voice and keyboard way.

  python3 tools/input_parity_inventory.py

Menu-driven areas (main menu, Word processor, document menu, File manager,
Options, web browser menu, media player) are read from the app's own tables
through voice_companion/input_parity.py, so they stay current. The new direct
keys come from input_parity.SCREEN_KEYS. Everything else is listed by hand
below; keep it in step with the user guide.

Status values:
  Both       a spoken command and a key, shortcut or menu path
  Added      the voice or keyboard way was added on this branch
  Typed      keyboard way is the command box: Alt+T, type the command, Enter
  N/A        one of the two ways does not make sense; the reason is given
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'voice_companion'
TARGET = Path(__file__).resolve().parent / 'input-parity-inventory.md'
TYPED = 'Alt+T, type the command, Enter'

# (area, action, voice, keyboard, status)
HAND = [
    # Getting started and voice commands
    ('Start, wake and commands', 'Wake up the microphone', '"Wake up"', 'Any arrow key or Enter while asleep', 'Both'),
    ('Start, wake and commands', 'Hear the version', '"What version?"', TYPED, 'Typed'),
    ('Start, wake and commands', 'Enter a command with the keyboard', 'N/A', 'Alt+T, type, Enter', 'N/A - this is the keyboard way to give any spoken command'),
    ('Start, wake and commands', 'Commands only / Dictation only / Normal mode', '"Commands only", "Dictation only", "Normal mode"', 'Alt+O, Input options, Command and dictation mode', 'Both'),
    ('Start, wake and commands', 'Hear the input mode', '"What mode?"', TYPED, 'Typed'),
    ('Start, wake and commands', 'Write words that sound like commands', '"Type literally ..."', 'Type the words', 'Both'),
    ('Start, wake and commands', 'Answer Yes', '"Yes"', 'Alt+Y, or arrows to Yes and Enter, or Home', 'Both'),
    ('Start, wake and commands', 'Answer No', '"No"', 'Alt+N, or arrows to No and Enter, or End', 'Both'),
    ('Start, wake and commands', 'Cancel a question', '"Cancel"', 'Escape', 'Both'),
    ('Start, wake and commands', 'Move in any list', '"Next", "Previous"', 'Up and Down Arrow', 'Both'),
    ('Start, wake and commands', 'First or last item in a list', '"Start of list", "End of list"', 'Home, End', 'Both'),
    ('Start, wake and commands', 'Choose the item you hear', '"That one", "Okay", "Confirm"', 'Enter', 'Both'),
    ('Start, wake and commands', 'Find a menu item by its first letter', 'Say the item name', 'Type the first letter', 'Both'),
    ('Start, wake and commands', 'Return to the main menu', '"Main menu"', 'Escape (repeat until the main menu)', 'Both'),
    # Help and lessons
    ('Help and lessons', 'Open the guide', '"Help"', 'F1 on the main menu (Added); main menu, Help, Enter', 'Added'),
    ('Help and lessons', 'Open help for the current screen', '"Help with documents", "Help with email", ...', 'F1 on every main screen', 'Added'),
    ('Help and lessons', 'Choose a topic or part', '"Next", "Previous", "That one"', 'Arrows, Home, End, Enter', 'Both'),
    ('Help and lessons', 'Next or previous block', '"Next paragraph", "Previous paragraph", "Continue reading"', 'Down Arrow, Up Arrow', 'Both'),
    ('Help and lessons', 'First or last block', '"First block", "Last block"', 'Home, End', 'Both'),
    ('Help and lessons', 'Move by word', '"Next word", "Previous word"', 'Control+Right Arrow, Control+Left Arrow', 'Both'),
    ('Help and lessons', 'Move by character', '"Next character", "Previous character"', 'Right Arrow, Left Arrow', 'Both'),
    ('Help and lessons', 'Move by sentence', '"Next sentence", "Previous sentence"', TYPED, 'Typed'),
    ('Help and lessons', 'Back to parts or topics', '"Back to subsections", "Back to topics"', 'Escape', 'Both'),
    ('Help and lessons', 'Close help', '"Close help"', 'Escape from the topic list', 'Both'),
    ('Help and lessons', 'Read the release notes', '"What\'s new"', 'Help, End (last topic), Enter', 'Both'),
    ('Help and lessons', 'Start the audio tutorial', '"Start tutorial"', 'Main menu, Audio tutorial, Enter', 'Added'),
    ('Help and lessons', 'Choose a course or lesson', 'Course name, "Next lesson", "Previous lesson"', 'Arrows and Enter', 'Both'),
    ('Help and lessons', 'Repeat a lesson', '"Repeat"', TYPED, 'Typed'),
    ('Help and lessons', 'Lesson list, find lesson, list or explain commands', '"Lesson list", "Find lesson ...", "List commands", "Explain command ..."', TYPED, 'Typed'),
    ('Help and lessons', 'Go back one level in the tutorial', '"Go back"', 'Escape', 'Both'),
    ('Help and lessons', 'Leave the tutorial', '"Close tutorial", "Main menu"', 'Escape from the course list', 'Both'),
    # Speech and voices
    ('Speech and voices', 'Silence speech', '"Be quiet", "Stop talking"', 'Control', 'Both'),
    ('Speech and voices', 'Resume paused speech', '"Resume"', TYPED, 'Typed'),
    ('Speech and voices', 'Faster, slower, louder, quieter, pitch', '"Faster", "Rate 4", "Volume 50", "Higher pitch"', 'Alt+O, Voice options, Tab, arrows, OK', 'Both'),
    ('Speech and voices', 'Choose a synthesizer and voice', '"Voice list", "Next voice", "Okay"', 'Alt+O, Synthesizer options', 'Both'),
    ('Speech and voices', 'Use SAPI, eSpeak or an AI voice', '"Use SAPI", "Use eSpeak", "Use AI voice"', 'Alt+O, Synthesizer options, Synthesizer', 'Both'),
    ('Speech and voices', 'Pause, resume or stop audio anywhere', '"Pause audio", "Resume audio", "Stop audio"', 'Control+P (pause or resume)', 'Both'),
    # Reading and editing text
    ('Reading and editing text', 'Read from the top / continue', '"Read from top", "Continue reading"', 'Control+Shift+R (document, draft, note, reader) ; Down Arrow', 'Added'),
    ('Reading and editing text', 'Move by character, word, line, paragraph', '"Next word", "Previous line", ...', 'Arrows, Control+arrows', 'Both'),
    ('Reading and editing text', 'Move by sentence', '"Next sentence", "Previous sentence"', 'Document menu: Alt, 5 (Read and navigate)', 'Both'),
    ('Reading and editing text', 'Start or end of a line', '"Go to start of line", "Go to end of line"', 'Home, End', 'Added'),
    ('Reading and editing text', 'Start or end of the text', '"Go to beginning of document", "Go to end of document"', 'Control+Home, Control+End', 'Both'),
    ('Reading and editing text', 'Select by keys', '"Select next word", "Select paragraph", ...', 'Shift with a movement key', 'Both'),
    ('Reading and editing text', 'Select all', '"Select all"', 'Control+A', 'Both'),
    ('Reading and editing text', 'Hear the selection', '"What is selected", "Read selection"', 'Insert+Shift+Down Arrow (document, reader)', 'Both'),
    ('Reading and editing text', 'Select named words', '"Select red fox", "Select cat through dog"', 'Control+Alt+S, type the words, Enter', 'Both'),
    ('Reading and editing text', 'Copy, cut, paste', '"Copy selection", "Cut selection", "Paste"', 'Control+C, Control+X, Control+V', 'Both'),
    ('Reading and editing text', 'Remove the selection or last dictation', '"Scratch that"', 'Control+Shift+Z (document); Delete', 'Both'),
    ('Reading and editing text', 'Undo', '"Undo"', 'Control+Z', 'Both'),
    ('Reading and editing text', 'Replace words', '"Replace cat with dog"', 'Control+H, type, Enter', 'Both'),
    ('Reading and editing text', 'Insert before or after words', '"Insert before ...", "Insert after ..."', 'Document menu: Alt, 2 (Edit)', 'Both'),
    ('Reading and editing text', 'Choose among matches', '"Next", "Previous", "That one", "Match 3"', 'Arrows, Home, End, Enter, Escape', 'Both'),
    # Notepad
    ('Notepad', 'Start a note', '"Write a note"', 'Main menu, Notepad, Enter', 'Both'),
    ('Notepad', 'Add words', 'Speak the words', 'Type the words, Enter', 'Both'),
    ('Notepad', 'Read the note', '"Read from top"', 'Control+Shift+R', 'Added'),
    ('Notepad', 'Save the note', '"Save note"', 'Control+S', 'Added'),
    ('Notepad', 'List saved notes', '"List notes"', 'Control+O', 'Both'),
    ('Notepad', 'Open a saved note', '"Open note 2"', 'Control+O, arrows, Enter', 'Both'),
    ('Notepad', 'Save and go back', '"Go back"', 'Escape', 'Both'),
    ('Notepad', 'Notepad help', '"Help with notepad"', 'F1', 'Added'),
    ('Notepad', 'Start or end of a line', 'N/A', 'Home, End in the typing box', 'N/A - speech always adds to the end of the note'),
    # Email setup
    ('Email setup', 'Add an account', '"Add account"', 'Alt+O, Email options, Add an email account', 'Both'),
    ('Email setup', 'Choose the service (Gmail, Outlook, Yahoo, Thunderbird)', 'Say the service name', 'G, O, Y or T, or arrows, then Enter', 'Both'),
    ('Email setup', 'Thunderbird account setup', '"Thunderbird"', 'T or arrows, Enter', 'Both'),
    ('Email setup', 'List or switch accounts', '"List email accounts", "Switch to Yahoo"', 'Alt+O, Email options, Sending account; or ' + TYPED, 'Both'),
    ('Email setup', 'Which account is in use', '"Which email account?"', TYPED, 'Typed'),
    ('Email setup', 'Add, list or find contacts', '"Add contact Bob as ...", "List contacts", "Find contact Bob"', TYPED, 'Typed'),
    ('Email setup', 'Connect Google Contacts', '"Connect Google Contacts"', TYPED, 'Typed'),
    # Composing
    ('Email: write', 'New message', '"Write an email", "New message"', 'Control+N in email', 'Both'),
    ('Email: write', 'Move between To, CC, BCC, Subject, body', '"Go to CC", "Go to Subject", ...', 'Tab, Shift+Tab', 'Both'),
    ('Email: write', 'Enter an address or subject', '"Email to ...", "Subject is ..."', 'Type in the box, Enter', 'Both'),
    ('Email: write', 'Read a header back', '"Read recipient", "Read subject"', 'Tab to the box (it is read)', 'Both'),
    ('Email: write', 'Write and format the body', 'Speak; "Make this bold", ...', 'Type; Control+B, Control+I, Control+U', 'Added'),
    ('Email: write', 'Read the draft', '"Read email draft"', 'Control+Shift+R', 'Added'),
    ('Email: write', 'Save the draft', '"Save draft"', 'Control+S', 'Added'),
    ('Email: write', 'Open a saved draft', '"List email drafts", "Open email draft ..."', TYPED, 'Typed'),
    ('Email: write', 'Cancel the draft', '"Cancel draft"', 'Escape', 'Both'),
    ('Email: write', 'Send (review first)', '"Send it"', 'Control+Enter or Alt+S', 'Both'),
    ('Email: write', 'Confirm or cancel sending', '"Yes", "Cancel send"', 'Alt+Y, Alt+N, arrows and Enter', 'Both'),
    ('Email: write', 'Help with writing email', '"Help with writing email"', 'F1', 'Added'),
    # Reading mail
    ('Email: read and organize', 'Open the inbox', '"Email", "Check email"', 'Main menu, Email, Enter', 'Both'),
    ('Email: read and organize', 'Next or previous message', '"Next message", "Previous message"', 'Down Arrow, Up Arrow', 'Both'),
    ('Email: read and organize', 'First or last message', '"First message", "Last message"', 'Home, End', 'Both'),
    ('Email: read and organize', 'Open a message', '"That one", "Open message 1"', 'Enter', 'Both'),
    ('Email: read and organize', 'Message details', '"Message details"', 'Control+I', 'Added'),
    ('Email: read and organize', 'Read the open message', '"Continue reading", "Read from beginning"', 'Arrows in the message', 'Both'),
    ('Email: read and organize', 'Back to the list', '"Back to folder"', 'Escape', 'Both'),
    ('Email: read and organize', 'Next or previous page of mail', '"Next messages", "Previous messages"', 'Page Down, Page Up', 'Added'),
    ('Email: read and organize', 'Refresh the list', '"List messages"', 'F5', 'Both'),
    ('Email: read and organize', 'Go to a folder', '"Go to folder", "Go to Receipts"', 'Control+Y, arrows, Enter', 'Both'),
    ('Email: read and organize', 'Which folder', '"Which folder"', TYPED, 'Typed'),
    ('Email: read and organize', 'Back to Inbox', '"Back to inbox"', 'Control+Y, Inbox, Enter', 'Both'),
    ('Email: read and organize', 'Mark a message', '"Mark", "Select message"', 'Space', 'Both'),
    ('Email: read and organize', 'Extend the marks', '"Extend selection next", "Extend selection previous"', 'Shift+Down Arrow, Shift+Up Arrow', 'Both'),
    ('Email: read and organize', 'Mark all on the page', '"Select all messages"', 'Control+A', 'Both'),
    ('Email: read and organize', 'Clear marks / hear marks', '"Clear selection", "What is selected"', TYPED, 'Typed'),
    ('Email: read and organize', 'Delete', '"Delete"', 'Delete', 'Both'),
    ('Email: read and organize', 'Move to a folder', '"Move", "Move to Saved"', 'Control+Shift+V', 'Both'),
    ('Email: read and organize', 'Reply, reply all, forward', '"Reply", "Reply all", "Forward"', 'Control+R, Control+Shift+R, Control+F', 'Both'),
    ('Email: read and organize', 'Email the sender / copy address', '"New email to sender", "Copy sender address"', 'Control+Shift+N, Control+Shift+C', 'Both'),
    ('Email: read and organize', 'Create, rename, delete a folder', '"Create folder ...", "Rename folder", "Delete folder"', 'Control+Shift+E, F2, Control+Shift+D', 'Both'),
    ('Email: read and organize', 'Move a folder', '"Move folder ... into ..."', TYPED, 'Typed'),
    ('Email: read and organize', 'Links and buttons in a message', '"Next link", "Next button", "Activate current link"', 'K, B (Shift for back), Tab, Enter', 'Both'),
    ('Email: read and organize', 'List links or buttons', '"List links", "List buttons"', TYPED, 'Typed'),
    ('Email: read and organize', 'Email help', '"Help with email"', 'F1', 'Added'),
    # Web
    ('Web: page reading', 'Read the page', '"Read page", "Continue reading"', 'Down Arrow', 'Both'),
    ('Web: page reading', 'Move by line, word, character, paragraph', '"Next line", "Next word", ...', 'Arrows, Control+arrows', 'Both'),
    ('Web: page reading', 'Move a long way', '"Page down", "Page up"', 'Page Down, Page Up', 'Added'),
    ('Web: page reading', 'Start or end of a line', '"Go to start of line", "Go to end of line"', 'Home, End', 'Added'),
    ('Web: page reading', 'Top or bottom of the page', '"Top of page", "Bottom of page"', 'Control+Home, Control+End', 'Both'),
    ('Web: page reading', 'Next heading, link, button, form control, ...', '"Next heading", "Previous link", ...', 'H, K, B, F, X, C, E, T, R, A, L, I, D, G, M, P, S (Shift for back)', 'Both'),
    ('Web: page reading', 'Heading by level', '"Next heading level 2"', '1 to 6', 'Both'),
    ('Web: page reading', 'Find on the page', '"Find login"', TYPED, 'Typed'),
    ('Web: page reading', 'List links / headings', '"List links", "List headings"', 'F7, F6', 'Both'),
    ('Web: page reading', 'List fields', '"List fields"', TYPED, 'Typed'),
    ('Web: page reading', 'Open the link or press the button', '"Open current link", "Activate current element"', 'Enter, Space', 'Both'),
    ('Web: page reading', 'Next or previous control', '"Next control", "Previous control"', 'Tab, Shift+Tab', 'Both'),
    ('Web: page reading', 'Back, forward, refresh', '"Go back", "Go forward", "Refresh page"', 'Alt+Left, Alt+Right, F5', 'Both'),
    ('Web: page reading', 'Address or search box', '"Search", "Open website ..."', 'Control+L', 'Both'),
    ('Web: page reading', 'Web browser menu', '"Leave website", "Browser menu"', 'Escape', 'Both'),
    ('Web: page reading', 'Web help', '"Help with web"', 'F1', 'Added'),
    ('Web: forms', 'Enter a text box', '"Edit current field"', 'E, Tab, or Enter on the box', 'Both'),
    ('Web: forms', 'Save or cancel a field', '"Save field", "Cancel field editing"', 'Tab or Enter; Escape', 'Both'),
    ('Web: forms', 'Checkbox and radio button', '"Toggle checkbox", "Select radio button"', 'Space', 'Both'),
    ('Web: forms', 'Choice box options', '"Next option", "First option", "Select option ..."', 'Arrows, Home, End, first letter', 'Both'),
    ('Web: forms', 'Edit combo box suggestions', '"Next suggestion", "Previous suggestion"', 'Down Arrow, Up Arrow', 'Both'),
    ('Web: forms', 'Fill out, review and submit a form by voice', '"Fill out this form", "Review form", "Review and submit form"', TYPED, 'Typed'),
    ('Web: forms', 'Read a table', '"Next row", "Previous column", "First row", "Last column"', 'Control+Alt+arrows, Control+Alt+Home/End/Page Up/Page Down', 'Both'),
    ('Web: lists', 'Find a name in a links or headings list', 'Say the name or "Next"', 'Type letters; Backspace', 'Both'),
    ('Web: lists', 'Close a list', '"Close list"', 'Escape', 'Both'),
    ('Web: favorites and history', 'Save the page as a favorite', '"New favorite", "Save favorite as ..."', 'Browser menu, New favorite', 'Both'),
    ('Web: favorites and history', 'Open, rename or delete a favorite', '"Open favorite ...", "Go to ..."', 'Browser menu, Go to / Rename / Delete favorite', 'Both'),
    ('Web: favorites and history', 'Web history', '"Web history"', 'Browser menu, Web history', 'Both'),
    ('Web: favorites and history', 'Search engine for one search or as default', '"Bing weather", "Search engine Brave Search"', 'Alt+O, Web browser options, Default search engine', 'Both'),
    ('Web: regular browser', 'Legacy regular browser screen keys', 'N/A', 'Up, Down, Home, End, Enter, Space', 'N/A - screen no longer opened; the regular browser uses the web reading keys above'),
    # Radio and podcasts
    ('Radio and podcasts', 'Open Radio or Podcasts', '"Radio", "Podcasts"', 'Main menu, Media, arrows, Enter', 'Both'),
    ('Radio and podcasts', 'Switch between actions and results', '"Actions", "Results"', 'Tab, Shift+Tab', 'Both'),
    ('Radio and podcasts', 'Every action in the actions list', 'Action name or its command', 'Arrows and Enter', 'Both'),
    ('Radio and podcasts', 'Search', '"Search radio for jazz", "Search podcasts for history"', 'Control+F', 'Both'),
    ('Radio and podcasts', 'Saved stations or subscriptions', '"List presets", "List subscriptions"', 'Control+Shift+P', 'Both'),
    ('Radio and podcasts', 'Save preset / download episode', '"Save preset", "Download this episode"', 'Control+D', 'Both'),
    ('Radio and podcasts', 'Remove the selected item', '"Delete preset", "Remove selected subscription"', 'Delete', 'Both'),
    ('Radio and podcasts', 'Refresh lists', '"Refresh stations", "Refresh subscriptions"', 'F5', 'Both'),
    ('Radio and podcasts', 'Play or pause, resume, stop', '"Pause audio", "Resume audio", "Stop audio"', 'Space, Control+R, Control+S', 'Both'),
    ('Radio and podcasts', 'Seek 15 seconds', '"Back 15 seconds", "Forward 15 seconds"', 'Control+Left, Control+Right', 'Both'),
    ('Radio and podcasts', 'Recording and schedule forms', '"Next field", "Time 8 PM", "Okay"', 'Tab, arrows, Enter, Escape', 'Both'),
    ('Radio and podcasts', 'Play by number', '"Play station 1", "Play episode 2"', 'Arrows to the item, Enter', 'Both'),
    ('Radio and podcasts', 'Back out of a list', '"Back to podcasts", "Radio menu"', 'Escape', 'Both'),
    ('Radio and podcasts', 'Radio or podcast help', '"Help with radio", "Help with podcasts"', 'F1', 'Added'),
    # Media player extras
    ('Media player', 'Open or close the player menu', '"Player menu", "Close menu"', 'Escape or Alt', 'Both'),
    ('Media player', 'Seek any amount', '"Forward 2 minutes"', 'Left and Right move 15 seconds', 'Both'),
    ('Media player', 'Media player help', '"Help with media player"', 'F1', 'Added'),
    # Document reader
    ('Document reader', 'Read the document', '"Read document"', 'Control+Shift+R', 'Added'),
    ('Document reader', 'Move by line, word, character', '"Next line", "Next word"', 'Arrows, Control+Left/Right', 'Both'),
    ('Document reader', 'Next heading, table, link, list, bookmark, footnote', '"Next heading", "Next table", ...', 'H, T, K, L, B, N (Shift for back)', 'Both'),
    ('Document reader', 'Follow a link', '"Follow link"', 'Enter', 'Both'),
    ('Document reader', 'Formatting and selection', '"What formatting", "What is selected"', 'Insert+F, Insert+Shift+Down Arrow', 'Both'),
    ('Document reader', 'Find text', '"Find clinic"', 'Control+F, type, Enter', 'Added'),
    ('Document reader', 'Start or end of the document', '"Go to beginning of document"', 'Control+Home, Control+End', 'Both'),
    ('Document reader', 'Start or end of a line', 'N/A', 'Home, End', 'N/A - read-only view; say "Read line" to hear the line'),
    ('Document reader', 'Back to files', '"Back to files"', 'Escape', 'Both'),
    ('Document reader', 'Open help', '"Help", "Help with document reader"', 'F1', 'Added'),
    # Sleep, exit, power, updates, guides, problems
    ('Sleep, exit and power', 'Put the microphone to sleep', '"Go to sleep"', TYPED, 'Typed'),
    ('Sleep, exit and power', 'Close Companion', '"Exit Companion"', 'Alt+F4; Escape at the main menu; main menu item', 'Both'),
    ('Sleep, exit and power', 'Restart or shut down the computer', '"Restart computer", "Shut down computer"', 'Main menu items', 'Both'),
    ('Updates', 'Check for updates', '"Check for updates"', 'Main menu item; Alt+O, Auto-update options', 'Both'),
    ('Updates', 'Accept or refuse an update', '"Yes", "No"', 'Alt+Y, Alt+N, arrows and Enter', 'Both'),
    ('Updates', 'Hear download progress', '"Status", "Update status"', 'Alt+S or F5; Status button', 'Both'),
    ('Updates', 'Cancel the download', '"Cancel update"', 'Alt+C or Escape', 'Both'),
    ('Guide files', 'Open the guide folder', '"Open user guides"', TYPED, 'Typed'),
    ('Fix a problem', 'Type a command that is not heard', 'N/A', 'Alt+T, type, Enter', 'N/A - keyboard fallback for speech'),
    ('Fix a problem', 'Free text and typing', 'Speak the words', 'Type the words', 'N/A - both are text entry, not commands'),
]

DOCUMENT_GROUP = {'File': 1, 'Edit': 2, 'Format': 3, 'Insert': 4, 'Read and navigate': 5, 'Dictation': 6, 'More commands': 7, 'Table': 8}


def rows():
    sys.path.insert(0, str(SOURCE))
    stubs = ROOT / 'tools' / 'linux-test-stubs'
    import importlib.util
    if not importlib.util.find_spec('vosk'):
        sys.path.append(str(stubs))
    os.environ.setdefault('VOICE_COMPANION_DATA_DIR', str(Path(__import__('tempfile').mkdtemp())))
    from input_parity import catalog
    from contextlib import redirect_stdout
    import io
    with redirect_stdout(io.StringIO()):
        menu = catalog()
    result = []
    for row in menu:
        area = row['area']
        voice = row['voice']
        if '{value}' in voice:
            voice = voice.replace('{value}', '...')
        if voice.startswith('@'):
            voice = row['action']
        status = 'Both'
        keyboard = row['keyboard']
        if area.startswith('Document: '):
            area = 'Word processor document'
        if area == 'All command views':
            continue
        result.append((area, row['action'], '"' + voice[:1].upper() + voice[1:] + '"', keyboard, status))
    result.append(('Main menu', 'Audio tutorial (new item)', '"Start tutorial"', 'Arrows to Audio tutorial, Enter', 'Added'))
    return result + HAND


def render():
    data = rows()
    order = []
    for row in data:
        if row[0] not in order:
            order.append(row[0])
    statuses = [r[4].split(' ')[0] for r in data]
    lines = ['# Voice and keyboard parity inventory', '',
             'Generated by `python3 tools/input_parity_inventory.py`. Menu areas come from the app\'s own tables;',
             'other areas are listed by hand in that script. "Typed" means the keyboard way is the command box:',
             'press Alt+T, type the same words you would say, and press Enter. That box works on every screen,',
             'so every spoken command also has a keyboard way.', '',
             '| Count | Number |', '| --- | --- |',
             '| Actions inventoried | ' + str(len(data)) + ' |',
             '| Both ways already | ' + str(statuses.count('Both')) + ' |',
             '| Gaps fixed on this branch (Added) | ' + str(statuses.count('Added')) + ' |',
             '| Keyboard by typed command only | ' + str(statuses.count('Typed')) + ' |',
             '| Not applicable | ' + str(statuses.count('N/A')) + ' |', '']
    for area in order:
        lines += ['## ' + area, '', '| Action | Voice | Keyboard | Status |', '| --- | --- | --- | --- |']
        for row in data:
            if row[0] == area:
                lines.append('| ' + ' | '.join(str(x).replace('|', '/') for x in row[1:]) + ' |')
        lines.append('')
    return '\n'.join(lines)


if __name__ == '__main__':
    TARGET.write_text(render(), encoding='utf-8')
    print('Wrote', TARGET)
