# Daily sweep log

Every bug fix and design question from the nightly sweeps, newest first. Will reads
this, or asks for it, instead of getting a message each night.

The format for each night:
- a heading with the date and the version
- under it, a "Fixed" list and a "Questions for Will" list

## October 31, 2026 (night 3, a code review of error handling): version 0.2.189-test

Release pull request 111. No new Windows-only tests, so no try branch. All three checks were green at 10:07 AM UTC, but the merge was blocked by this session's permission check, so it is not published yet. The next sweep finishes it (step 0a) once Will allows the merge.

### Fixed

#### Startup, Options, sleep and updates
- A damaged update settings file in the data folder no longer stops startup; it uses the feed that came with the app.
- A full disk while copying the user guides no longer stops startup or the installer's guide refresh.
- Damaged saved options are copied to preferences-damaged.json and said once at startup.
- Reading the update helper's ready file while it is still empty no longer says "The update could not start."
- Yes to an update from a note that cannot be saved waits, and keeps the note.
- Alt+O opens with a damaged podcast, radio or AI voice file.
- A failed Options save says it in plain words, not an error number and a path.
- Spoken setting changes (speed, volume, verbosity, punctuation, modes, voice, capitals, Start with Windows, web reading choices) still work when the file cannot be saved, and say so. F9 and the transcript switch say so too.
- Restart and Shut down keep a note that cannot be saved, and name a failing draft.
- A missing, damaged or empty guide no longer stops Help or the tutorial.
- Going to sleep with a draft or note that cannot be saved keeps the words and says so in one message.

#### Email
- New email, send, file manager, another account and reply no longer stop the app when the draft cannot be saved.
- A move or delete that stops halfway clears the open message and says to list the folder again.
- Yahoo reads flagged, answered and forwarded.
- Thunderbird's add-on update restarts Thunderbird only once when its file is in use (also found by the Calendar helper; merged into one fix).
- A damaged or locked draft says what is wrong.
- After a folder change, the Inbox is listed again.

#### Calendar and Contacts
- A slow Thunderbird is its own error ("did not answer in time"), not "not connected"; a slow change says to check before trying again.
- A Thunderbird that will not close for the add-on update says nothing changed.
- A failed read keeps the chosen address book; Control+L says what went wrong.
- A failed delete says nothing was deleted and what to try.
- A damaged answer from Thunderbird no longer stops Contacts or Calendar.
- "Email John Smith" uses the last contact list when Thunderbird is slow.

#### Documents and files
- Changes to a document open in Word or on a full disk stay in the document, say so, and no longer stop the app (new editor_process helper).
- Undo with a locked file no longer drops the step.
- A document save is flushed to the disk before it replaces the old file.
- Old documents are copied once, so deleted or renamed documents no longer come back.
- The Recent documents list is written atomically; write_text_atomic removes its temporary file on failure.
- Typing-window edits that would join or split table cells are refused with a plain message.

#### Web
- A garbled favorites or history file no longer stops the browser; a file with a byte order mark is read.
- Saving, renaming or deleting a favorite while the file is held says nothing changed.
- Download history is no longer written over after a failed read.
- Load failures say the real reason (no internet, no such site, too slow, no answer, security check).
- Keys and commands say the real browser link reason instead of "it will reconnect".
- Resume no longer blames a favorite.
- Change Address in form review no longer stops the app.
- An unexpected error in a spoken web command says to press F5 and try again, and records where in web-command-error.txt (no page text).
- No more empty names or doubled periods in web speech; "[example.com" no longer says "Invalid IPv6 URL".
- The extension popup no longer stays on "Checking" forever (extension 0.4.4; testers choose Reload).

#### Media and the window
- Broken or cut-off station and feed replies (HTTPException) no longer stop the app.
- Failed SHOUTcast recordings and cut-off podcast downloads say so; part files are removed.
- Bad station directory replies and damaged IPRD catalogs are skipped.
- Radio and Podcasts keys handle errors as voice does.
- Media errors are said in plain words (new spoken_error).
- Recording folder and disk errors are named as disk errors, and do not hide the station.
- Next and Previous chapter say the player could not respond when its browser closed.
- Damaged podcast subscriptions are skipped.
- The Media player screen says the player did not answer instead of stopping the app.
- A scheduled recording with no web address is reported as damaged.
- Counts: "1 new episode", not "1 new episodes".
- The window's update loop keeps a bounded error list and no longer floods the log.

### Questions for Will (not answered yet)

#### General
- There is no catch-all around commands: any unexpected error closes the app with "Voice Companion stopped because of a problem." Should every screen get a plain safety net, as the web commands now have?

#### Email
- A subject such as "High priority" is taken as the importance command. Should the Subject box keep it as the subject?
- The general mail error names the account even when the account is not the cause. Change the wording?

#### Calendar and Contacts
- A dropped connection retries a request once, so an add could make a second copy. Should changes skip the retry?
- Calendar says "No events." before the reason when it could not be read. Should it match "What's on my calendar"?
- Should a failed new event keep the time and name already given?

#### Documents
- Changing only the capitals of a document name says the name already exists. Allow case-only renames?
- The editor says "empty or damaged" and the reader "damaged or empty"; neither says what to do next. Reword?
- Email drafts are saved without being flushed to the disk first. Flush them like documents?

#### Web
- Allow international site names such as münchen.de?
- Keep "End of list" before "No next heading"?
- Tab on an empty page gives two messages. Intended?

#### Media
- Should media screens catch every error and say something plain, not only known error types?

## October 30, 2026 (night 2, an expert screen reader user): version 0.2.181-test

Release pull request 99. Try run for new Windows-only window tests: pull request 98.

### Fixed

#### Startup, sleep, Help and the tutorial
- Waking at the Exit, Restart or Shut down question asks it again, not "Back to exit confirm."
- Sleep with the microphone off says to press Control+F12 to wake; waking leaves out "I am listening." The tutorial uses the same words.
- "Canceled." is joined to the next message, so it is heard.
- Control+F1 or "Main menu" in Help says "Help closed."
- An update offer answered No while asleep goes back to the screen you slept on.
- The transcript notice at startup says to wake Companion first, or how to turn it off with no microphone.
- Mode phrases, "What mode?", "What version?" and "Where am I?" work in Help.
- Moving in Help and the tutorial says only the item; the hint is said once.
- The tutorial names all 12 courses; the guide names YouTube.
- "Switch to normal mode / commands only / dictation only" and "What mode am I in?" forms work.
- A test that relied on leftover sleep state now sets its own (passed alone, failed in the full run).

#### Email
- One period after a subject; a blank subject says "No subject".
- Escape from Attachments says the message you are on.
- Canceling a reply or new email goes back to the message or your place in the same folder.
- Closing a saved draft says it is still saved.
- Delete in Thunderbird Trash and Yahoo Trash says nothing changed before asking.
- Moving a message to its own folder says nothing changed.
- Marking wording is the same by key and by voice.
- An empty folder gives one message to every key.
- Spoken numbers up to one hundred work ("read message fifteen").
- "What folder am I in?" and "Go to the message list" work.

#### Calendar and Contacts
- An event or contact removed elsewhere leaves the list; the day or list is read again.
- "Where am I?" and "Repeat" keep your place; both screens know next item, previous item, top and bottom of list.
- Y and N no longer close the Calendar change list or calendar list.
- "Schedule lunch with Bob tomorrow at noon" and "Add dinner Friday at 6 PM" add the event on Calendar.
- "What's on my calendar today?" from another screen names a calendar that failed.
- "Email John" says Thunderbird's reason when contacts cannot be read.

#### Documents and files
- Reading a paragraph, line or word no longer moves your place; delete word and sentence act on the current one.
- Deleting or cutting a word or sentence leaves no extra spaces.
- No doubled periods in Cut, Deleted, Pasted, Replaced and Insert messages, or reader notes.
- Formatting is said in plain words (Italic, Heading 2, Font Arial).
- "Make it quick." and similar are written; only "make it" plus a number sets the size.
- Redo goes to the paragraph it changes; delete paragraph starts at the next paragraph.
- Reader Find drops the period speech adds; Find with no words asks for them.
- Save As suggests Documents when Desktop is missing.
- File manager says "1 item"; "What is selected" works.

#### Web
- Tab and F reach tabs, switches and spin buttons; Enter selects a tab, toggles a switch, or lets arrows change a spin button.
- Role words are the same on the page line and with quick keys ("spin button", "progress bar"; switches say on or off).
- Unlabeled controls no longer say their type twice.
- "Top of page." and "Bottom of page." at the ends.
- Fields say their value, "invalid entry", and the site's hint; passwords are never spoken.
- Saving a favorite by voice never replaces another favorite (lost data).
- "List form fields" and similar open the fields list.
- T says a table's rows and columns.

#### Media
- YouTube Page Down and Page Up reach the screen in the real window.
- Space and other player keys on the controls list keep your place.
- Keys in an empty Radio or Podcasts list say the list is empty.
- YouTube Down at the end says why the next part did not load; fewer repeat requests.
- End in the Media player says how to reach the player menu.
- A slow-starting podcast episode still opens the player controls.
- "browse feeds" is now "browse podcasts".

#### Found while shipping
- A release-note sentence was over the reading level.
- Two new Help tests expected 32 topics; Windows has 33 because What's new is built there. They now count the topics.

### Open items for the next sweep
- test_web_media_live LiveDialogTests.test_unsaved_changes_keep_the_page_and_a_second_try_leaves is still flaky (failed once on main's side tonight). Make it report which step failed and how long each took.
- Many test files set companion.SLEEP_RETURN_MODE and never restore it.
- Stopped run_checks runs leave /tmp/vc-compare-* worktrees behind.
- Helpers should each get their own scratch subfolder.
- tools/helper-rules.md has an old Claude-Session footer.

### Questions for Will (not answered yet)

#### Startup and Help
- Should Control+F12 in a Yes or No question do what "Go to sleep" does?
- Should Verbosity Low also shorten list hints?
- Should "Update status" and "Cancel update" with no update say "No update is downloading."?
- "What version?" mid-session says "The microphone is off, as you left it." Keep that?

#### Email
- Should "Back to inbox" from a reply go to the Inbox list, not the message?
- Name new drafts by subject instead of "Untitled 2"?
- Folder list wording differs between the first item and the rest.
- Rename folder lists standard folders that cannot be renamed.
- Reply to a message with no subject sends "Re: (no subject)".
- Drafts list Escape says only "List closed."; "Where am I" in a draft does not say "Email draft".
- The Delete question says "Trash or Deleted Items" instead of the real folder.
- From the main menu, "Email John", Escape, then "yes" saves the draft and opens the Inbox.

#### Calendar
- Should End go to the day's last event?
- A week summary ("What's on my calendar next week?") would be a new feature.

#### Documents
- Control+S in a note goes to the main menu; in a document it keeps you writing.
- No way to delete a saved note.
- "Recent documents" works only in the Word processor menu.
- The reader has no "spell word" or "where am I"; its Find wording differs from the editor's.
- Renaming a document leaves its old backup file behind.

#### Web
- Tab past the last control: stay put, or wrap like a browser?
- Heading list says "Chapter 2 2" for a level-2 heading named Chapter 2.
- One word for each role: "edit" or "edit field", "combo box" or "choice box"?
- Should Control+F jump to page text before matching links and buttons?
- Add lists for landmarks, buttons and tables?

#### Media
- Should "Where am I?" and "Repeat" work on Radio, Podcasts and the Media player?
- After Stop, should "What's playing?" say "Stopped"?
- Page Down and Page Up on Radio and Podcasts?
- Should Enter on a station open the player controls, as on Podcasts?
- Key presses still read spoken-command hints.
- Podcasts skip 15 seconds by key but the controls list says 30.
- A wording pass to replace "feed" in podcast messages.

## October 29, 2026 (night 1, a new veteran): version 0.2.175-test

Release pull request 81. All three checks passed. The session could not merge it, so Will merged it; 0.2.175-test was published on October 29.

### Fixed

#### Startup, Help and the tutorial
- "What mode?", Control+M and Options say Normal mode, Commands only and Dictation only, as the guide does.
- Typing "Wake up" goes back to the screen you were on.
- Sleeping during a Yes or No question (save, replace, name) asks it again on wake.
- "Help with" an unknown topic says so and reads the topic list.
- "Help with" finds main menu, keys, microphone, time, yes or no, lessons, voices, exit, transcript; ignores "the" and "my".
- "Start the tutorial", "Open the tutorial", "Start lessons" start the tutorial.
- "What version is this?" and similar say the version; "Version." in a document is text.
- F9 with a microphone that cannot start no longer says voice commands are on.
- Startup message says to wake Companion before saying start tutorial.
- Long tutorial lessons split in better places.
- Guide no longer says "Pause" silences Companion.

#### Email
- Hide Thunderbird no longer starts a closed Thunderbird.
- Changing the address no longer sends the next sentence into the subject.
- Reply subject says how to get back to the body; "Subject is…" in a reply body is written.
- Spoken subject drops the extra period.
- Inbox understands everyday words: go to the trash, open the first message, read the next message, close message, check my email, refresh.
- Unknown inbox words start with "I did not understand that."
- Clearer message for Reply from the list; plural wording fixed.
- Escape cancels the add-account question on every screen; Next, Previous and That one work there.
- After delete or move you land on the next message.
- A draft written before an account existed can be sent.
- "All messages" wording tells how to reach more mail.
- Yahoo: deleting inside Trash says nothing changed.

#### Calendar and Contacts
- "Make an appointment for Friday at 10 AM" asks for the name instead of naming it "for".
- Evening, afternoon and "at night" set the time and stay out of the name; "Friday evening" then "7" is 7 PM.
- "Schedule a doctor appointment…" and "Add a reminder…" work.
- "No" as an event or contact name asks again; "No" to a contact's email skips it.
- "Delete it", "remove it", "cancel my appointment" work.
- "Add a new contact", "go to contacts", "phone book", "my schedule", "my appointments" work.
- "Show my Outlook calendar" from the main menu: one message, Calendar first.
- "Where am I?" on Calendar says Calendar.
- Thunderbird save failures say what to do next.
- Phone-only contact is "No name"; "Saved on this computer" wording; repeating-event question wording.
- Freeze report written atomically (a test that passed on rerun; cause fixed, noted in LAUNCH-CHECKLIST).

#### Documents, notes and files
- Leaving a document Word has open no longer stops the app.
- Enter, Home and End work in List documents and the Notes list.
- Failed saves no longer leave .writing.docx or .restore.docx files.
- "Open document" a complex Word file opens it in the Document reader; damaged files named plainly.
- Escape from Open a document inside a document goes back to the document.
- Copy and Cut after Select all copy or cut.
- File manager rename accepts "dot" and a trailing spoken period.
- "dot docx" spoken at the end of a name is not added twice.
- End and beginning of document, note and email are announced.
- Reader "Go to end of document" goes to the very end.
- Empty-folder keys in File manager say the folder is empty.
- Resume document with none open stays put; deleting a blank paragraph says so.

#### Web
- Upload buttons with Chrome in the background explain how to show the window.
- "Show browser window" with your own browser says to use Alt+Tab.
- Show browser window on a page that failed to open no longer misleads.
- Spoken addresses ("v a dot gov", "w w w dot…", "V.A. dot gov") offer to open the site.
- No stray "., " before item types.
- "List links" on a page without links says "No links on this page."
- Favorites and web history are never wiped by a locked or damaged file.
- Sign-in security codes and ID expiration dates are not taken for card payments.
- Site cookies carried over when Chrome restarts.
- Fill out this form: Skip, Cancel form, "Yes, please", "Oregon." work.
- List favorites on the main menu opens a real list; web history and favorite commands on the main menu open the web browser menu.
- A card or file box appearing on a page in use no longer restarts Chrome and loses typing.

#### Media and the window
- "Play" plays the chosen station, preset, episode or YouTube video.
- YouTube Stop and Resume control a loaded video.
- "What's playing?" on YouTube with nothing playing says how to start a video.
- Damaged radio files read as empty; damaged schedule and subscriptions kept and reported.
- Preset and station names keep capitals.
- Scheduled recordings list says day and time in words.
- Correct wording for Pause radio during a media file, Delete on results, Tab during playback.
- "Play the radio" and "Play it again" no longer search for "the" or "it".
- "Open radio", "Listen to the radio", "Podcast.", "Open podcasts", "What video is this?" work.
- Command box keeps its room at 400 percent text on small screens; Tab never moves focus silently (passed twice on Windows, draft PR 80).

### Open items for the next sweep
- test_web_media_live LiveDialogTests.test_unsaved_changes_keep_the_page_and_a_second_try_leaves fails on main sometimes and passes other times. Find the cause.
- Thunderbird add-on deletes inside Trash with messages.delete(ids, false); may delete for good. Check on Windows.

### Questions for Will (not answered yet)
- Should "Pause" also silence Companion's speech when nothing is playing?
- In a Yes or No question, should F1 say the question again?
- Should "Text size 1000" say the allowed range?
- Should the main menu understand "Help me", "What can I say?" and "Go back"?
- In a new email's body, should "Subject is …" be written as text instead of changing the subject?
- In the inbox, "Open email" reopens the inbox from scratch. Should it open the current message?
- Some send words ("send my email", "send now") are written as text. Should they send?
- Should "Next Tuesday" said on a Monday mean tomorrow or a week later?
- Should "half past two" and "quarter to three" be understood as times?
- Should Undo work in notes, and should "Scratch that" in a note remove the last dictation?
- Should a spoken rename keep the old file type?
- Should recent lists drop files that are gone?
- After Escape in a web text box, later sentences can be typed into the page. Should that stop?
- On a web page, one word like "page" can match a link. Should matching be stricter?
- Should "Go back" leave Radio results at the first station?
- Should "Skip" on YouTube mean "Skip ad" when an ad shows?
