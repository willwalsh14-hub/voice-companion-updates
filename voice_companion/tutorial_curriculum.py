"""Spoken courses and command explanations from the current user guide."""
from pathlib import Path
import re
from onboard_help import HelpSession
from settings_model import CATEGORIES, fields

COURSES = (
 ('basics','Getting started',('getting started','basics','start','getting started lesson'), (
  ('Open the app', 'Open Voice Companion from its desktop icon or the Start menu. Wait for the update check. Say "Wake up" when it says it is ready. You hear the main menu. Say "Next" to hear a choice and "That one" to open it. You can also use Down Arrow and Enter. Say "Main menu" to return to the start.'),
  ('Choose voice or keyboard', 'You can speak a command or use the keyboard. Press Alt T to reach the command box. Type a command and press Enter. In a menu, arrows move and Enter opens. Home and End reach the list ends. Escape goes back. The keyboard works while the microphone sleeps. Press Control to silence speech.'),
  ('Speak text and give commands', 'Normal mode accepts commands and spoken text. Say "Commands only" while you review so stray speech is not added to your work. Say "Normal mode" when you want to write again. "Dictation only" puts most spoken words into text. Say "Type literally new line" to write those words rather than make a line break. Say "What mode?" if you are unsure.'),
  ('Get help and stop', 'Say "Help" for the guide. "Help with documents" opens document help. Say "Close help" to return to your task. "Go to sleep" pauses voice requests and keeps your place. "Wake up" continues there. "Exit Companion" closes the app after its save and exit questions. "Shut down computer" affects Windows too, so listen to that question before saying Yes.'),
 ), ('GETTING STARTED','USING VOICE COMMANDS','SPEECH AND VOICES','USING HELP AND LESSONS')),
 ('document','Word processor',('word processor','documents','document','practice document','word processor lesson'), (
  ('Create and name a file', 'Say "Create a document". Say "Name document My Letter" to name the file. Speak a short sentence. Say "Read paragraph" to check it. The file name is separate from any title you write in the letter. F2 opens the name box by keyboard. Say a name and confirm it, or type it and press Enter.'),
  ('Read and correct', 'Say "Next word" or "Previous sentence" to move. "Go to end of document" moves to the end and clears a selection. Say "Select red fox" to choose named words. If they appear more than once, hear each match and choose "That one". "What is selected" checks your choice. "Scratch" removes selected text. "Undo" reverses an edit in the document.'),
  ('Change text and spacing', 'Choose text before changing only that part. Say "Make this bold", "Set font to Arial", or "Set size 14". "Say font" reads its settings. "Heading 1" makes a main heading. "Double space" changes line spacing. "Custom spacing" asks for a value, then asks you to check it. Say Yes only when the value is right.'),
  ('Save and reopen', 'Say "Save document" to keep the current name. "Save as My Letter Copy" makes a second file and keeps the first. "Leave document" asks whether to save. Yes keeps a named file or asks for an untitled file name. No discards changes since your saved starting point. "List documents" finds files after you reopen the app. "Open document My Letter" opens that named file.'),
 ), ('WRITE A DOCUMENT','READING AND EDITING TEXT')),
 ('notepad','Notepad',('notepad','notes','note','notepad lesson','practice note'), (
  ('Write and save', 'Choose Notepad or say "Write a note". Speak a short note. Say "Read from top" to hear it and "Save note" to save it. "Go back" saves the note and returns to the main menu. Notepad keeps plain text. Use the Word processor if you need a font, heading, or table.'),
  ('Review and change', 'Say "Next word", "Previous line", or "Next paragraph" to review a note. "Select all" chooses its text. "Read selection" checks it. "Scratch" removes selected text. Say "Commands only" before reviewing by voice if you do not want ordinary speech added. Use "Normal mode" to write again. "New line" makes a line break.'),
 ), ('WRITE A NOTE','READING AND EDITING TEXT','USING VOICE COMMANDS')),
 ('email','Email',('email','mail','email lesson','practice email'), (
  ('Connect and choose an account', 'Say "Add account" and choose Gmail, Outlook or Exchange, or Yahoo. Follow the sign-in prompts with a helper if needed. Open the inbox and test a message. "Which email account?" checks the current address. "Switch to Yahoo" chooses it when only one Yahoo account is connected. With two accounts of the same type, use the full email address.'),
  ('Write a message', 'Say "Write an email". Give the address, subject, and message when asked. You can speak a saved contact name or type an address and press Enter. "Go to To", "Go to Subject", and "Go to Body" return to those parts. Use "Read recipient" and "Read subject" to check them. CC sends a copy. BCC sends a copy without showing that address to others.'),
  ('Review and send', 'Say "Send it" to start the review. Listen to the account, addresses, subject, and message. Nothing is sent before that review and your Yes. Say "Cancel send" if anything is wrong. Correct it and start the review again. With no connected account, Companion saves a draft instead of sending. If it reports an uncertain result, check your account before trying again.'),
  ('Read and organize', 'Say "Email" for the inbox. "Next message" and "Previous message" read summaries. "That one" opens the current message. "Go back" returns to its folder. "Go to folder" opens the folder list. "Mark" selects a message. "Move" or "Delete" acts on marked messages, or the current message if none are marked. Listen to the names and action before saying Yes.'),
 ), ('SET UP EMAIL','WRITE AN EMAIL','READ AND ORGANIZE EMAIL','READING AND EDITING TEXT')),
 ('web','Web browser',('web browser','web','browser','practice web browser','web browser lesson','practice browser'), (
  ('Open an address or search', 'Choose Web browser, then Search or type address. Type an address such as controlaltsuccess.com and press Enter. You do not need www or https. A phrase such as weather in Houston makes a search. Control L opens the address box while you read a page. Escape cancels entry. Listen to the page title and site before you use a result.'),
  ('Read a page', 'Use the page view in the Companion window. Up and Down read lines, including text between links. Left and Right read characters. Control Left and Right read words. H moves to a heading and Shift H moves back. K finds links. Arrows continue from the place you choose. These letters do not type while you are reading the page.'),
  ('Enter a web answer', 'Tab reaches a control. E finds a text box; press Enter to edit it. Type or speak an answer. Enter saves a single-line answer. Control Enter saves a box with several lines. Escape cancels editing and restores page keys. Space changes a checkbox. Arrows change a choice box. "Fill out this form" asks for one answer at a time.'),
  ('Check before submitting', 'Say "Review form" to hear answers. "Review and submit form" starts the review before sending. "Continue review" moves on. "Change this field" corrects an answer; give the new answer and review again. Say Yes only when all answers and the named button are right. Keyboard Enter on a Submit button uses the website directly rather than this voice review.'),
  ('Save and return to a page', 'Say "New favorite" while a page is open. Say its name, or type the name and press Enter. Go to favorite, Rename favorite, and Delete favorite each open a saved-name list. Arrows and Enter choose; one or several typed letters find a name. Say Go to and a saved name to open that favorite directly. Web history opens recent pages. Escape from page reading opens the web browser menu. With Keep browser window open on, Resume where you left off returns to that page.'),
 ), ('BROWSE THE WEB','FAVORITES AND WEB HISTORY','READING AND EDITING TEXT')),
 ('radio','Radio',('radio','radio lesson','practice radio'), (
  ('Find and play', 'Choose Radio and say "Search radio". Give a station name or type of music. Use "Next" and "Previous" to hear results. "That one" plays the chosen station. You can also say "Search radio for jazz" or "Play station 1". If two stations match a name, answer with the number you want. Try another result if a stream will not play.'),
  ('Save a station', 'From station results, Escape says Station actions. The next Escape says Radio menu. A preset is a saved station. Choose a station, then say "Save preset as Jazz". "List presets" reads your saved names. Choose with Next, Previous, and That one. "Play preset Jazz" opens a named station directly. "Delete preset Jazz" asks whether to remove it. Answer No if it is the wrong saved station.'),
  ('Pause or record', 'Use "Pause audio", "Resume audio", and "Stop audio" for playback. "Stop talking" silences Companion speech instead. Control Alt P pauses or resumes audio from any screen. Audio stops at the main menu unless Keep audio playing in the background is on. This option is off at first. "Record station 1 for 30 minutes" records a supported stream. "Schedule station 1 tomorrow at 8 PM for 30 minutes" sets a future recording. Keep Companion running and the computer awake. "List recordings" finds saved audio. A recording is a saved audio file. You can play it later. Test with a short show first so you know the station can be recorded.'),
 ), ('LISTEN TO RADIO','SAVED RADIO STATIONS','RECORD RADIO')),
 ('podcasts','Podcasts',('podcasts','podcast','podcast lesson','practice podcasts'), (
  ('Find and play a show', 'Choose Podcasts. Say "Search podcasts for history". Next and Previous read show names; That one opens a show. Next episode and Previous episode read episodes; That one plays the one you hear. "Play episode 2" chooses by number. "Back to podcasts" returns to show results. Arrows and Enter provide the same choices.'),
  ('Save shows and episodes', 'Say "Subscribe" to save the open show. "List subscriptions" reads saved shows. "Download this episode" saves an episode for use without internet. "List downloads" finds saved files and "Play download 1" plays one. New subscriptions use manual downloads until you choose otherwise. Some hosts do not allow downloads.'),
  ('Set downloads and move in audio', 'Say "Auto download 5 episodes" to keep the newest five for the open show. Old automatic downloads beyond that limit are removed after new ones succeed. "Manual downloads" stops future automatic downloads and leaves saved files. "Back 15 seconds" and "Fast forward 30 minutes" seek where supported. "Next chapter" uses chapter marks if the episode has them.'),
 ), ('LISTEN TO PODCASTS','SAVED PODCASTS AND DOWNLOADS','LISTEN TO RADIO')),
 ('options','Options',('options','settings','options lesson','settings lesson','practice options'), (
  ('Open and save options', 'Say "Options" or press Alt O. Choose a category with arrows and Enter. Tab reaches a control. Arrows change a choice; Space changes a checkbox. Choose OK to save or Cancel to keep the earlier settings. Say Next, Previous, and Okay for the same menu steps. Opening another category before OK discards unsaved changes.'),
  ('Choose settings for your tasks', 'Verbosity controls the amount of spoken help. Voice controls speed, volume, and pitch. Word processor defaults apply to new documents. Email chooses your sending account and message list size. Web browser chooses search, browser, and page-reading settings. Podcasts sets download limits for a chosen show. The following lessons explain each control by its name.'),
 ), ('CHANGE YOUR OPTIONS','SPEECH AND VOICES','USING VOICE COMMANDS')),
 ('help','Help and updates',('help and updates','help','updates','help lesson','updates lesson'), (
  ('Find a guide topic', 'Say "Help" and choose a chapter. Choose a part to read it. "Continue reading" reads more. "Back to subsections" returns to the chapter parts. "Back to topics" returns to all chapters. "Close help" returns to your work. "Help with documents" or "Help with web" opens a chapter directly.'),
  ('Read changes and update', 'Say "Release notes" to hear what changed. "Check for updates" looks for a newer version. If offered, Yes starts the update and No keeps the current version. Companion saves your work, downloads the update, checks it, installs it, and opens again. "Cancel update" stops a download in progress. "Update status" asks for progress.'),
  ('Find a guide file', 'Say "Open user guides" to open Documents, Voice Companion, User Guides. Choose text, HTML, Word, EPUB, DAISY, or braille. All have the same instructions. The DAISY copy has text rather than recorded audio. Close and reopen a guide after an update so you read the new copy. "Where are my user guides" opens the folder too.'),
 ), ('USING HELP AND LESSONS','CHECK FOR UPDATES','FIND YOUR GUIDE FILES','FIX A PROBLEM')),
)


def split_instruction(text,limit=1000):
    pieces=re.split(r'(?:(?<=[.!?])|(?<=[.!?]["”]))\s+(?=[A-Z"“0-9])',text)
    result=[];current=''
    for piece in pieces:
        if current and len(current)+len(piece)+1>limit:result.append(current);current=''
        current=(current+' '+piece).strip()
    if current:result.append(current)
    return result


def option_explanation(field):
    if field.key=='cloud_consent':return 'This checkbox allows microphone audio to be sent to Azure for recognition. It is off by default. Turn it on only if you agree and the service is set up. The app uses offline speech in web forms and settings. OK saves; Cancel keeps the earlier choice.'
    if field.kind=='action':return 'Choose this button to '+field.label.lower()+'. It opens that task and leaves Options. Choose OK first if you want to save changed settings.'
    if field.kind=='password':return 'This is a private text box. Its contents are not read aloud. Leaving it blank keeps the saved key. A helper can enter a new key here. Choose OK to save or Cancel to leave it unchanged.'
    if field.kind=='check':return 'This is a checkbox. Press Space to turn it on or off. By voice, choose the setting and say on or off. Choose OK to save or Cancel to keep the earlier choice.'
    if field.kind=='headers':return 'Choose which email details are read: From, Subject, Date, and Size. Keep at least one. Space adds or removes a detail. Alt Up and Alt Down change its place. OK saves; Cancel keeps the earlier order.'
    if field.key in ('rate','pitch'):choices='Choose a whole number from minus ten to ten. Zero is the normal setting.'
    elif field.key=='volume':choices='Choose a number from zero to one hundred for the speech volume.'
    elif field.key=='document_size':choices='Choose a text size from 6 to 72 points for new documents.'
    elif field.key=='podcast_limit':choices='Choose manual, all, or a whole number from 1 to 10,000. This limit applies to the show you selected.'
    elif field.choices:choices='Choices are '+', '.join(field.choices)+'.'
    else:choices='The choices depend on the voices, accounts, or shows available on your computer.' if field.kind=='choice' else 'Type or speak the value in this text box.'
    return choices+' Tab reaches the setting. Arrows change a listed choice. Choose OK to save or Cancel to keep the earlier value.'


def build_courses(guide=None):
    guide=Path(guide or Path(__file__).with_name('START HERE - Veteran.txt'))
    topics={t['key']:t for t in HelpSession(guide).topics}
    result=[]
    for key,name,aliases,introductions,sources in COURSES:
        lessons=[{'title':title,'text':text,'reference':False} for title,text in introductions]
        commands={}
        entries=list(introductions)
        for source in sources:
            topic=topics.get(source)
            if not topic:raise ValueError('Missing tutorial chapter: '+source)
            for part in topic.get('parts',[]):
                text='\n\n'.join(part['paragraphs'])
                for i,chunk in enumerate(split_instruction(text)):
                    lessons.append({'title':part['name']+(' part '+str(i+1) if len(text)>1000 else ''),'text':chunk,'reference':True})
                related=text
                if source=='READING AND EDITING TEXT' and part['name'] in ('Select text','Choose named words'):
                    related+='\n\n'+'\n\n'.join(next(p for p in topic['parts'] if p['name']=='Copy, cut, paste, or remove text')['paragraphs'])
                entries.append((part['name'],related))
        if key=='options':
            for category in CATEGORIES:
                for field in fields(category,{}):lessons.append({'title':category+': '+field.label,'text':category+'. '+field.label+'. '+option_explanation(field),'reference':False})
        for title,text in entries:
            for phrase in re.findall(r'["“]([^"”]+)["”]',text):
                if len(phrase)<130:
                    value=phrase.rstrip('.,!?').casefold()
                    entry=commands.setdefault(value,{'title':phrase.rstrip('.,!?'),'text':text})
                    if text not in entry['text']:entry['text']+='\n\n'+text
        result.append({'key':key,'name':name,'aliases':aliases,'lessons':lessons,'commands':list(commands.values())})
    return result
