# Daily sweep log

Every bug fix and design question from the nightly sweeps, newest first. Will reads
this, or asks for it, instead of getting a message each night.

The format for each night:
- a heading with the date and the version
- under it, a "Fixed" list and a "Questions for Will" list

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
