"""Write tools/input-parity-inventory.md: every user action with its voice and keyboard way.

  python3 tools/input_parity_inventory.py

Menu-driven areas (main menu, Word processor, document menu, File manager,
Options, web browser menu, media player, and the Alt menus of other screens) are read from the app's own tables
through voice_companion/input_parity.py, so they stay current. The new direct
keys come from input_parity.SCREEN_KEYS. Everything else is listed by hand
below; keep it in step with the user guide.

Status values:
  Both       a spoken command and a key, shortcut or menu path already existed
  Added: ... the voice or keyboard way that this work added
  N/A - ...  one of the two ways does not make sense; the reason is given
The command box (Alt+T) is not counted as a keyboard way: every action has a
key, a shortcut, or a menu item reached with arrows and Enter.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'voice_companion'
TARGET = Path(__file__).resolve().parent / 'input-parity-inventory.md'

# (area, action, voice, keyboard, status)
# Status: 'Both' (already had both ways), 'Added: ...' (what this work added), or
# 'N/A - reason' (one way does not make sense). No row may need the command box alone.
HAND = [
    # Start, wake and commands
    ('Start, wake and commands', 'Wake up the microphone', '"Wake up"', 'Any arrow key or Enter while asleep; Ctrl+F12', 'Added: Ctrl+F12'),
    ('Start, wake and commands', 'Put the microphone to sleep', '"Go to sleep"', 'Ctrl+F12 on any main screen', 'Added: Ctrl+F12'),
    ('Start, wake and commands', 'Hear the time', '"What time is it?", "What\'s the time?", "Time"', 'F12 or Insert+F12 on any screen, even while asleep', 'Added: F12, Insert+F12 and the spoken request'),
    ('Start, wake and commands', 'Hear the day and date', '"What\'s the date?", "Today\'s date", "What day is it?", "Date"', 'F12 or Insert+F12 twice quickly on any screen', 'Added: F12 twice and the spoken request'),
    ('Start, wake and commands', 'Hear the version', '"What version?"', 'Ctrl+V on the main menu', 'Added: Ctrl+V'),
    ('Start, wake and commands', 'Type a command', 'N/A', 'Alt+T, type, Enter', 'N/A - this is the keyboard way to give any spoken command'),
    ('Start, wake and commands', 'Normal mode, Commands only, Dictation only', '"Normal mode", "Commands only", "Dictation only"', 'Alt+O, Input options, Command and dictation mode', 'Both'),
    ('Start, wake and commands', 'Hear the input mode', '"What mode?"', 'Ctrl+M on the main menu', 'Added: Ctrl+M'),
    ('Start, wake and commands', 'Write words that sound like commands', '"Type literally ..."', 'Type the words', 'Both'),
    ('Start, wake and commands', 'Answer Yes', '"Yes"', 'Alt+Y; Home; arrows to Yes, Enter', 'Both'),
    ('Start, wake and commands', 'Answer No', '"No"', 'Alt+N; End; arrows to No, Enter', 'Both'),
    ('Start, wake and commands', 'Cancel a question', '"Cancel"', 'Escape', 'Both'),
    ('Start, wake and commands', 'Move in any list', '"Next", "Previous"', 'Up Arrow, Down Arrow', 'Both'),
    ('Start, wake and commands', 'First or last item in a list', '"Start of list", "End of list"', 'Home, End', 'Both'),
    ('Start, wake and commands', 'Choose the item you hear', '"That one", "Okay", "Confirm"', 'Enter', 'Both'),
    ('Start, wake and commands', 'Find a menu item by name', 'Say the item name', 'Type its first letter', 'Both'),
    ('Start, wake and commands', 'Return to the main menu', '"Main menu"', 'Escape until the main menu', 'Both'),
    ('Start, wake and commands', "Open the current screen's menu", '"Email menu", "Notepad menu", "Help menu", ...', 'Alt (Alt again closes it)', 'Added: Alt on Email, drafts, Notepad, web pages, Help, lessons and Document reader'),
    ('Start, wake and commands', 'Move between radio or podcast actions and results', '"Radio menu", "Podcasts menu", "Results"', 'Alt; Tab', 'Added: Alt'),
    # Help and lessons
    ('Help and lessons', 'Open Help', '"Help"', 'F1 on the main menu; main menu, Help, Enter', 'Added: F1 on the main menu'),
    ('Help and lessons', 'Help for the current screen', '"Help with documents", "Help with email", ...', 'F1', 'Added: F1 on email, notes, web, media, reader and menu screens'),
    ('Help and lessons', 'Choose a topic or part', '"Next", "Previous", "That one"', 'Arrows, Home, End, Enter', 'Both'),
    ('Help and lessons', 'Next or previous block', '"Next paragraph", "Previous paragraph", "Continue reading"', 'Down Arrow, Up Arrow', 'Both'),
    ('Help and lessons', 'First or last block', '"First block", "Last block"', 'Home, End', 'Both'),
    ('Help and lessons', 'Move by sentence', '"Next sentence", "Previous sentence"', 'Ctrl+Down Arrow, Ctrl+Up Arrow in a topic', 'Added: Ctrl+Down, Ctrl+Up'),
    ('Help and lessons', 'Move by word', '"Next word", "Previous word"', 'Ctrl+Right Arrow, Ctrl+Left Arrow', 'Both'),
    ('Help and lessons', 'Move by character', '"Next character", "Previous character"', 'Right Arrow, Left Arrow', 'Both'),
    ('Help and lessons', 'Back to parts or topics', '"Back to subsections", "Back to topics"', 'Escape', 'Both'),
    ('Help and lessons', 'Close Help', '"Close help"', 'Escape from the topic list', 'Both'),
    ('Help and lessons', 'Read the release notes', '"What\'s new"', 'Help, End (last topic), Enter', 'Both'),
    ('Help and lessons', 'Start the audio tutorial', '"Start tutorial"', 'Ctrl+T on the main menu', 'Added: Ctrl+T'),
    ('Help and lessons', 'Choose a course or lesson', 'Course name, "Next lesson", "Previous lesson"', 'Arrows, Enter', 'Both'),
    ('Help and lessons', 'Repeat a lesson', '"Repeat"', 'Ctrl+Shift+R', 'Added: Ctrl+Shift+R'),
    ('Help and lessons', 'Lesson list', '"Lesson list"', 'Ctrl+L', 'Added: Ctrl+L'),
    ('Help and lessons', 'Command list for a course', '"List commands"', 'F1 in the tutorial', 'Added: F1'),
    ('Help and lessons', 'Find a lesson', '"Find lesson ..."', 'Ctrl+F, type, Enter', 'Added: Ctrl+F'),
    ('Help and lessons', 'Explain a command', '"Explain command ..."', 'Ctrl+Shift+F, type, Enter', 'Added: Ctrl+Shift+F'),
    ('Help and lessons', 'Continue a long explanation', '"Continue reading"', 'Ctrl+Down Arrow', 'Added: Ctrl+Down'),
    ('Help and lessons', 'Back one level / course list', '"Go back", "Courses"', 'Escape', 'Both'),
    ('Help and lessons', 'Leave the tutorial', '"Close tutorial", "Main menu"', 'Escape from the course list', 'Both'),
    # Speech and voices
    ('Speech and voices', 'Silence speech', '"Be quiet", "Stop talking"', 'Control', 'Both'),
    ('Speech and voices', 'Resume paused speech', '"Resume"', 'N/A', 'N/A - Control stops speech rather than pausing it; reading keys such as Down Arrow read again'),
    ('Speech and voices', 'Faster, slower, louder, quieter, pitch', '"Faster", "Rate 4", "Volume 50", "Higher pitch"', 'Alt+O, Voice options, Tab, arrows, OK', 'Both'),
    ('Speech and voices', 'Choose a synthesizer and voice', '"Voice list", "Next voice", "Okay"', 'Alt+O, Synthesizer options', 'Both'),
    ('Speech and voices', 'Use SAPI, eSpeak or an AI voice', '"Use SAPI", "Use eSpeak", "Use AI voice"', 'Alt+O, Synthesizer options, Synthesizer', 'Both'),
    ('Speech and voices', 'AI voice setup for a trainer', '"AI voice setup"', 'Tab to the AI voice setup button, Enter', 'Both'),
    ('Speech and voices', 'Pause or resume radio and podcasts anywhere', '"Pause audio", "Resume audio"', 'Ctrl+P', 'Both'),
    # Reading and editing text
    ('Reading and editing text', 'Read from the top', '"Read from top"', 'Ctrl+Shift+R in a document, draft, note or reader', 'Added: Ctrl+Shift+R in drafts, notes and the reader'),
    ('Reading and editing text', 'Continue reading', '"Continue reading"', 'Down Arrow', 'Both'),
    ('Reading and editing text', 'Move by character, word, line, paragraph', '"Next word", "Previous line", ...', 'Arrows, Ctrl+arrows', 'Both'),
    ('Reading and editing text', 'Move by sentence', '"Next sentence", "Previous sentence"', 'Document menu: Alt, 5, then the item', 'Both'),
    ('Reading and editing text', 'Start or end of a line', '"Go to start of line", "Go to end of line"', 'Home, End', 'Added: voice'),
    ('Reading and editing text', 'Start or end of the text', '"Go to beginning of document", "Go to end of document"', 'Ctrl+Home, Ctrl+End', 'Both'),
    ('Reading and editing text', 'Select with movement', '"Select next word", "Select paragraph", ...', 'Shift with a movement key', 'Both'),
    ('Reading and editing text', 'Select all', '"Select all"', 'Ctrl+A', 'Both'),
    ('Reading and editing text', 'Hear the selection', '"What is selected"', 'Insert+Shift+Down Arrow', 'Both'),
    ('Reading and editing text', 'Select named words', '"Select red fox", "Select cat through dog"', 'Ctrl+Alt+S, type, Enter', 'Both'),
    ('Reading and editing text', 'Copy, cut, paste', '"Copy selection", "Cut selection", "Paste"', 'Ctrl+C, Ctrl+X, Ctrl+V', 'Both'),
    ('Reading and editing text', 'Scratch the selection or last dictation', '"Scratch that"', 'Ctrl+Shift+Z', 'Both'),
    ('Reading and editing text', 'Undo', '"Undo"', 'Ctrl+Z', 'Both'),
    ('Reading and editing text', 'Replace words', '"Replace cat with dog"', 'Ctrl+H, type, Enter', 'Both'),
    ('Reading and editing text', 'Insert before or after words', '"Insert before ...", "Insert after ..."', 'Document menu: Alt, 2, then the item', 'Both'),
    ('Reading and editing text', 'Choose among matches', '"Next", "Previous", "That one", "Match 3"', 'Arrows, Home, End, Enter; Escape cancels', 'Both'),
    ('Reading and editing text', 'Page Up and Page Down in a document', 'N/A', 'Page Up, Page Down', 'N/A - moves by a screen of text; say "Next paragraph" instead'),
    # Notepad
    ('Notepad', 'Start a note', '"Write a note"', 'Main menu, Notepad, Enter', 'Both'),
    ('Notepad', 'Add words', 'Speak the words', 'Type the words, Enter', 'Both'),
    ('Notepad', 'Read the note', '"Read from top"', 'Ctrl+Shift+R', 'Added: Ctrl+Shift+R'),
    ('Notepad', 'Save the note', '"Save note"', 'Ctrl+S', 'Added: Ctrl+S'),
    ('Notepad', 'List saved notes', '"List notes"', 'Ctrl+O', 'Both'),
    ('Notepad', 'Open a saved note', '"Open note 2"', 'Ctrl+O, arrows, Enter', 'Both'),
    ('Notepad', 'Save and go back', '"Go back"', 'Escape', 'Both'),
    ('Notepad', 'Notepad help', '"Help with notepad"', 'F1', 'Added: F1'),
    ('Notepad', 'Start or end of the typing line', 'N/A', 'Home, End in the typing box', 'N/A - speech always adds to the end of the note'),
    # Email setup
    ('Email setup', 'Add an account', '"Add account"', 'Alt+O, Email options, Add an email account', 'Both'),
    ('Email setup', 'Choose Thunderbird (for Gmail, Outlook and others) or Yahoo', 'Say the service name', 'T or Y, or arrows, Enter', 'Both'),
    ('Email setup', 'Thunderbird setup', '"Thunderbird"', 'T or arrows to Thunderbird, Enter', 'Both'),
    ('Email setup', 'List, switch or check the account', '"List email accounts", "Switch to Yahoo", "Which email account?"', 'Alt+O, Email options, Sending account', 'Both'),
    ('Email setup', 'Switch account from a list', '"Switch account", then the number, name or address', 'Email menu: Alt, 6, 2; arrows, Enter', 'Added: account list to choose from'),
    ('Email setup', 'Remove an email account', '"Remove email account", then "Yes"', 'Email menu: Alt, 6, 4; arrows, Enter, Y', 'Added: voice and Alt menu item'),
    ('Email setup', 'List contacts / find a contact', '"List contacts", "Find contact Bob"', 'Ctrl+K in email, arrows, Enter', 'Added: Ctrl+K'),
    ('Email setup', 'Add a contact', '"Add contact Bob as bob@example.com"', 'Ctrl+Shift+K in the inbox, type, Enter', 'Added: Ctrl+Shift+K'),
    # Write email
    ('Email: write', 'New message', '"Write an email", "New message"', 'Ctrl+N in email', 'Both'),
    ('Email: write', 'Move between To, CC, BCC, Subject, body', '"Go to CC", "Go to Subject", ...', 'Tab, Shift+Tab', 'Both'),
    ('Email: write', 'Enter an address or subject', '"Email to ...", "Subject is ..."', 'Type in the box, Enter', 'Both'),
    ('Email: write', 'Read a header back', '"Read recipient", "Read subject"', 'Tab to the box (it is read)', 'Both'),
    ('Email: write', 'Bold, italic, underline', '"Make this bold", "Toggle italic", ...', 'Ctrl+B, Ctrl+I, Ctrl+U in the message', 'Added: Ctrl+B, Ctrl+I, Ctrl+U'),
    ('Email: write', 'Read the draft', '"Read email draft"', 'Ctrl+Shift+R', 'Added: Ctrl+Shift+R'),
    ('Email: write', 'Save the draft', '"Save draft"', 'Ctrl+S', 'Added: Ctrl+S'),
    ('Email: write', 'Open a saved draft', '"Open email draft", "Open email draft Lunch"', 'Ctrl+O in the inbox, arrows, Enter', 'Added: Ctrl+O and a draft list'),
    ('Email: write', 'Contacts while writing', '"List contacts"', 'Ctrl+K', 'Added: Ctrl+K'),
    ('Email: write', 'Cancel the draft', '"Cancel draft"', 'Escape', 'Both'),
    ('Email: write', 'Send (review first)', '"Send it"', 'Ctrl+Enter or Alt+S', 'Both'),
    ('Email: write', 'Confirm or cancel sending', '"Yes", "Cancel send"', 'Alt+Y, Alt+N, arrows and Enter', 'Both'),
    ('Email: write', 'Help with writing email', '"Help with writing email"', 'F1', 'Added: F1'),
    # Read and organize
    ('Email: read and organize', 'Open the inbox', '"Email", "Check email"', 'Main menu, Email, Enter', 'Both'),
    ('Email: read and organize', 'Next or previous message', '"Next message", "Previous message"', 'Down Arrow, Up Arrow', 'Both'),
    ('Email: read and organize', 'First or last message', '"First message", "Last message"', 'Home, End', 'Both'),
    ('Email: read and organize', 'Open a message', '"That one", "Open message 1"', 'Enter', 'Both'),
    ('Email: read and organize', 'Message details', '"Message details"', 'Ctrl+I', 'Added: Ctrl+I'),
    ('Email: read and organize', 'Read the open message', '"Continue reading", "Read from beginning"', 'Arrows in the message', 'Both'),
    ('Email: read and organize', 'Back to the list', '"Back to folder"', 'Escape', 'Both'),
    ('Email: read and organize', 'Next or previous page of mail', '"Next messages", "Previous messages"', 'Page Down, Page Up', 'Added: Page Down, Page Up'),
    ('Email: read and organize', 'Refresh the list', '"List messages"', 'F5', 'Both'),
    ('Email: read and organize', 'Go to a folder', '"Go to folder", "Go to Receipts"', 'Ctrl+Y, arrows, Enter', 'Both'),
    ('Email: read and organize', 'Folders of another account', '"Go to folder", then the account', 'Ctrl+Y, arrows, Enter (Escape goes back to the accounts)', 'Added: account list first when several accounts are connected'),
    ('Email: read and organize', 'Which folder', '"Which folder"', 'Ctrl+Shift+I', 'Added: Ctrl+Shift+I'),
    ('Email: read and organize', 'Back to Inbox', '"Back to inbox"', 'Ctrl+Y, Inbox, Enter', 'Both'),
    ('Email: read and organize', 'Mark a message', '"Mark", "Select message"', 'Space', 'Both'),
    ('Email: read and organize', 'Extend the marks', '"Extend selection next", "Extend selection previous"', 'Shift+Down Arrow, Shift+Up Arrow', 'Both'),
    ('Email: read and organize', 'Mark all on the page', '"Select all messages"', 'Ctrl+A', 'Both'),
    ('Email: read and organize', 'Hear the marks', '"What is selected"', 'Ctrl+Shift+S', 'Added: Ctrl+Shift+S'),
    ('Email: read and organize', 'Clear the marks', '"Clear selection"', 'Ctrl+Shift+A', 'Added: Ctrl+Shift+A'),
    ('Email: read and organize', 'Delete', '"Delete"', 'Delete', 'Both'),
    ('Email: read and organize', 'Move to a folder', '"Move", "Move to Saved"', 'Ctrl+Shift+V', 'Both'),
    ('Email: read and organize', 'Reply, reply all, forward', '"Reply", "Reply all", "Forward"', 'Ctrl+R, Ctrl+Shift+R, Ctrl+F', 'Both'),
    ('Email: read and organize', 'Email the sender / copy the address', '"New email to sender", "Copy sender address"', 'Ctrl+Shift+N, Ctrl+Shift+C', 'Both'),
    ('Email: read and organize', 'Create, rename, delete a folder', '"Create folder ...", "Rename folder", "Delete folder"', 'Ctrl+Shift+E, F2, Ctrl+Shift+D', 'Both'),
    ('Email: read and organize', 'Move a folder', '"Move folder"', 'Ctrl+Shift+M', 'Added: Ctrl+Shift+M'),
    ('Email: read and organize', 'Links and buttons in a message', '"Next link", "Next button", "Activate current link"', 'K, B (Shift for back), Tab, Enter', 'Both'),
    ('Email: read and organize', 'List links or buttons', '"List links", "List buttons"', 'F7, F8', 'Added: F7, F8'),
    ('Email: read and organize', 'Email help', '"Help with email"', 'F1', 'Added: F1'),
    # Web
    ('Web: page reading', 'Read the page', '"Read page", "Continue reading"', 'Down Arrow', 'Both'),
    ('Web: page reading', 'Move by line, word, character, paragraph', '"Next line", "Next word", ...', 'Arrows, Ctrl+arrows', 'Both'),
    ('Web: page reading', 'Move a long way', '"Page down", "Page up"', 'Page Down, Page Up', 'Added: voice'),
    ('Web: page reading', 'Start or end of a line', '"Go to start of line", "Go to end of line"', 'Home, End', 'Added: voice'),
    ('Web: page reading', 'Top or bottom of the page', '"Top of page", "Bottom of page"', 'Ctrl+Home, Ctrl+End', 'Both'),
    ('Web: page reading', 'Next heading, link, button, form control, ...', '"Next heading", "Previous link", ...', 'H, K, B, F, X, C, E, T, R, A, L, I, D, G, M, P, S (Shift for back)', 'Both'),
    ('Web: page reading', 'Heading by level', '"Next heading level 2"', '1 to 6', 'Both'),
    ('Web: page reading', 'Find on the page', '"Find login"', 'Ctrl+F, type, Enter', 'Added: Ctrl+F'),
    ('Web: page reading', 'List links / headings', '"List links", "List headings"', 'F7, F6', 'Both'),
    ('Web: page reading', 'List fields', '"List fields"', 'F8', 'Added: F8'),
    ('Web: page reading', 'Open the link or press the button', '"Open current link", "Activate current element"', 'Enter, Space', 'Both'),
    ('Web: page reading', 'Next or previous control', '"Next control", "Previous control"', 'Tab, Shift+Tab', 'Both'),
    ('Web: page reading', 'Back, forward, refresh', '"Go back", "Go forward", "Refresh page"', 'Alt+Left, Alt+Right, F5', 'Both'),
    ('Web: page reading', 'Address or search box', '"Search", "Open website ..."', 'Ctrl+L', 'Both'),
    ('Web: page reading', 'Web browser menu', '"Leave website"', 'Escape', 'Both'),
    ('Web: page reading', 'Web help', '"Help with web"', 'F1', 'Added: F1'),
    ('Web: page reading', 'Close the browser window, even when kept open', '"Close browser window"', 'Ctrl+Shift+W', 'Added: Ctrl+Shift+W'),
    ('Web: page reading', 'Send keys to the website (app mode) or back to browse mode', '"App mode", "Browse mode", "Focus mode"', 'Insert+Space, Ctrl+Shift+Space, Ctrl+Alt+Space', 'Both'),
    ('Web: forms', 'Find and change a slider', '"Next slider"', 'Tab or Enter on the slider; arrows, Page Up, Page Down, Home, End; Escape', 'Both'),
    ('Web: forms', 'Enter a text box', '"Edit current field"', 'E, Tab, or Enter on the box', 'Both'),
    ('Web: forms', 'Save or cancel a field', '"Save field", "Cancel field editing"', 'Tab or Enter; Escape', 'Both'),
    ('Web: forms', 'Checkbox and radio button', '"Toggle checkbox", "Select radio button"', 'Space', 'Both'),
    ('Web: forms', 'Choice box options', '"Next option", "First option", "Select option ..."', 'Arrows, Home, End, first letter', 'Both'),
    ('Web: forms', 'Combo box suggestions', '"Next suggestion", "Previous suggestion"', 'Down Arrow, Up Arrow', 'Both'),
    ('Web: forms', 'Fill out a form', '"Fill out this form"', 'Tab through the fields and type each answer', 'Both'),
    ('Web: forms', 'Review the answers', '"Review form"', 'F8 opens the field list; Tab through the fields to hear each answer', 'Added: F8'),
    ('Web: forms', 'Submit', '"Review and submit form"', 'Tab to the Submit button, Enter', 'Both'),
    ('Web: forms', 'Read a table', '"Next row", "Previous column", "First row", "Last column"', 'Ctrl+Alt+arrows, Ctrl+Alt+Home/End/Page Up/Page Down', 'Both'),
    ('Web: lists', 'Find a name in a links or headings list', 'Say the name or "Next"', 'Type letters; Backspace', 'Both'),
    ('Web: lists', 'Close a list', '"Close list"', 'Escape', 'Both'),
    ('Web: favorites and history', 'Save the page as a favorite', '"New favorite", "Save favorite as ..."', 'Browser menu, New favorite', 'Both'),
    ('Web: favorites and history', 'Open, rename or delete a favorite', '"Open favorite ...", "Go to ..."', 'Browser menu, Go to / Rename / Delete favorite', 'Both'),
    ('Web: favorites and history', 'Web history', '"Web history"', 'Browser menu, Web history', 'Both'),
    ('Web: favorites and history', 'Search engine for one search or as default', '"Bing weather", "Search engine Brave Search"', 'Alt+O, Web browser options, Default search engine', 'Both'),
    ('Web: regular browser', 'Use your own Chrome or Edge', 'Web browser, "Resume where you left off"', 'Browser menu, Resume where you left off', 'Both'),
    # Radio and podcasts
    ('Radio and podcasts', 'Open Radio or Podcasts', '"Radio", "Podcasts"', 'Main menu, Media, arrows, Enter', 'Both'),
    ('Radio and podcasts', 'Switch between actions and results', '"Actions", "Results"', 'Tab, Shift+Tab', 'Both'),
    ('Radio and podcasts', 'Search', '"Search radio for jazz", "Search podcasts for history"', 'Ctrl+F', 'Both'),
    ('Radio and podcasts', 'Saved stations or subscriptions', '"List presets", "List subscriptions"', 'Ctrl+Shift+P', 'Both'),
    ('Radio and podcasts', 'Save preset / download episode', '"Save preset", "Download this episode"', 'Ctrl+D', 'Both'),
    ('Radio and podcasts', 'Remove the selected item', '"Delete preset", "Remove selected subscription"', 'Delete', 'Both'),
    ('Radio and podcasts', 'Refresh lists', '"Refresh stations", "Refresh subscriptions"', 'F5', 'Both'),
    ('Radio and podcasts', 'Play or pause, resume, stop', '"Pause audio", "Resume audio", "Stop audio"', 'Space, Ctrl+R, Ctrl+S', 'Both'),
    ('Radio and podcasts', 'Seek 15 seconds', '"Back 15 seconds", "Forward 15 seconds"', 'Ctrl+Left, Ctrl+Right', 'Both'),
    ('Radio and podcasts', 'Recording and schedule forms', '"Next field", "Time 8 PM", "Okay"', 'Tab, arrows, Enter, Escape', 'Both'),
    ('Radio and podcasts', 'Play by number', '"Play station 1", "Play episode 2"', 'Arrows to the item, Enter', 'Both'),
    ('Radio and podcasts', 'Back out of a list', '"Back to podcasts", "Radio menu"', 'Escape', 'Both'),
    ('Radio and podcasts', 'Radio or podcast help', '"Help with radio", "Help with podcasts"', 'F1', 'Added: F1'),
    # Media player extras
    ('Media player', 'Open or close the player menu', '"Player menu", "Close menu"', 'Escape or Alt', 'Both'),
    ('Media player', 'Seek any amount', '"Forward 2 minutes"', 'Left and Right move 15 seconds', 'Both'),
    ('Media player', 'Media player help', '"Help with media player"', 'F1', 'Added: F1'),
    # Document reader
    ('Document reader', 'Read the document', '"Read document"', 'Ctrl+Shift+R', 'Added: Ctrl+Shift+R'),
    ('Document reader', 'Move by line, word, character', '"Next line", "Next word"', 'Arrows, Ctrl+Left/Right', 'Both'),
    ('Document reader', 'Next heading, table, link, list, bookmark, footnote', '"Next heading", "Next table", ...', 'H, T, K, L, B, N (Shift for back)', 'Both'),
    ('Document reader', 'Follow a link', '"Follow link"', 'Enter', 'Both'),
    ('Document reader', 'Formatting and selection', '"What formatting", "What is selected"', 'Insert+F, Insert+Shift+Down Arrow', 'Both'),
    ('Document reader', 'Find text', '"Find clinic"', 'Ctrl+F, type, Enter', 'Added: Ctrl+F'),
    ('Document reader', 'Start or end of the document', '"Go to beginning of document"', 'Ctrl+Home, Ctrl+End', 'Both'),
    ('Document reader', 'Start or end of a line', 'N/A', 'Home, End', 'N/A - read-only view; say "Read line" to hear the line'),
    ('Document reader', 'Back to files', '"Back to files"', 'Escape', 'Both'),
    ('Document reader', 'Help', '"Help with document reader"', 'F1', 'Added: F1 and voice Help'),
    # Sleep, exit, power, updates, guides, problems
    ('Sleep, exit and power', 'Close Companion', '"Exit Companion"', 'Alt+F4; Escape at the main menu; main menu item', 'Both'),
    ('Sleep, exit and power', 'Restart or shut down the computer', '"Restart computer", "Shut down computer"', 'Main menu items', 'Both'),
    ('Updates', 'Check for updates', '"Check for updates"', 'Main menu item; Alt+O, Auto-update options', 'Both'),
    ('Updates', 'Accept or refuse an update', '"Yes", "No"', 'Alt+Y, Alt+N, arrows and Enter', 'Both'),
    ('Updates', 'Hear download progress', '"Status", "Update status"', 'Alt+S or F5; Status button', 'Both'),
    ('Updates', 'Cancel the download', '"Cancel update"', 'Alt+C or Escape', 'Both'),
    ('Guide files', 'Open the guide folder', '"Open user guides"', 'Ctrl+G on the main menu', 'Added: Ctrl+G'),
    ('Fix a problem', 'A command is not heard', 'N/A', 'Alt+T, type, Enter', 'N/A - this is the keyboard way to give a spoken command'),
    ('Fix a problem', 'Free text', 'Speak the words', 'Type the words', 'N/A - text entry, not a command'),
]

# Catalog rows whose status is not plain Both: (area, action) -> status.
ADDED = {('Word processor document: More commands', 'Run any document voice command'):
             'N/A - this item is the keyboard way to type any spoken command'}
ADDED_AREAS = {'Radio station actions': 'Added: voice (say the action name)'}


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
        if area == 'All command views':
            continue
        voice = row['voice'].replace('{value}', '...').strip()
        if voice.startswith('@') or voice == '...':
            voice = row['action']
        if area.startswith('Document: '):
            area = 'Word processor document: ' + area[len('Document: '):]
        status = ADDED.get((area, row['action']), ADDED_AREAS.get(area, 'Both'))
        if area.startswith('Alt menu, ') and row['keyboard'].startswith('Alt, '):
            status = 'Added: Alt menu path'
        result.append((area, row['action'], '"' + voice[:1].upper() + voice[1:] + '"', row['keyboard'], status))
    return result + HAND


def problems(data=None):
    """Rows that break the parity rule. An empty list means every action has both ways."""
    found = []
    for area, action, voice, keyboard, status in data or rows():
        if not (status == 'Both' or status.startswith('Added: ') or status.startswith('N/A - ')):
            found.append((area, action, 'unknown status ' + status))
        if not status.startswith('N/A') and ('N/A' in (voice, keyboard) or not voice or not keyboard):
            found.append((area, action, 'missing a way'))
        if not status.startswith('N/A') and keyboard.startswith('Alt+T'):
            found.append((area, action, 'keyboard way is only the command box'))
    return found


def render():
    data = rows()
    order = []
    for row in data:
        if row[0] not in order:
            order.append(row[0])
    added = sum(r[4].startswith('Added') for r in data)
    na = sum(r[4].startswith('N/A') for r in data)
    lines = ['# Voice and keyboard parity inventory', '',
             'Generated by `python3 tools/input_parity_inventory.py` (do not edit by hand).',
             "Menu, Options and media action rows come from the app's own tables; the other rows are listed",
             'in that script. A keyboard way is a key, a shortcut, or a menu item reached with arrows and Enter.',
             'The command box (Alt+T, type the command, Enter) also works everywhere but is not counted.', '',
             '## Counts', '',
             '- Actions inventoried: ' + str(len(data)),
             '- Already had both ways: ' + str(len(data) - added - na),
             '- Gaps found: ' + str(added + na),
             '- Gaps fixed (Added): ' + str(added),
             '- Not applicable (N/A, with reason): ' + str(na), '']
    # No tables: Will reads this with a screen reader. Each action is a heading with bullets.
    for area in order:
        lines += ['## ' + area, '']
        for row in data:
            if row[0] == area:
                action, voice, keyboard, status = (str(x) for x in row[1:])
                lines += ['### ' + action, '', '- Voice: ' + voice, '- Keyboard: ' + keyboard,
                          '- Status: ' + status, '']
    return '\n'.join(lines)


if __name__ == '__main__':
    bad = problems()
    for row in bad:
        print('PARITY GAP:', *row)
    TARGET.write_text(render(), encoding='utf-8')
    print('Wrote', TARGET)
    sys.exit(1 if bad else 0)
