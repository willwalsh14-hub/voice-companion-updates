# Yahoo Mail parity with Gmail

Note, October 26, 2026: the direct Gmail account was removed. Gmail now works
only through Thunderbird. The Gmail column below is history. The tests that
compared Yahoo with Gmail's code now check the same values directly
(`test_message_bodies_read_the_html_and_keep_the_plain_part`,
`test_reply_and_forward_context`, `test_yahoo_and_a_plain_mailbox_answer_alike`).

Every mail command a user can give, compared on Gmail (the reference) and Yahoo.
"Before" is `main` at 0.2.156-test. "Now" is branch `claude/yahoo-mailbox`.
Tests are in `voice_companion/test_yahoo_mail.py` unless named otherwise. They
drive the real `yahoo_mailbox` code through the real `imaplib` and `smtplib`
over a local socket. The servers answer like imap.mail.yahoo.com and
smtp.mail.yahoo.com: folders Inbox, Sent, Draft, Trash, Bulk and Archive,
quoted names, `/` between levels, UIDs, literals, capabilities and error
replies. Tests ran on Python 3.13 and 3.12 (the Windows build uses 3.12).

`SameCommandsSameResultsTests` runs one 39-command script on a Gmail-shaped
backend and on Yahoo, and compares every spoken answer. Only folder-name case
may differ (Gmail says INBOX, SPAM; Yahoo says Inbox, Spam).

## Every command, one by one

Each command has a heading. The bullets give what Gmail does, what Yahoo did
before this work, what Yahoo does now, and the test that checks it.

### Open inbox / Check Yahoo / Email

- Gmail: Lists 10 newest
- Yahoo before: Worked; fetched each message separately
- Yahoo now: One FETCH per page, newest first
- Test: `test_list_is_newest_first...`

### List size 10-100, 1000, all

- Gmail: Pages of 100
- Yahoo before: Worked; `UID SEARCH ALL` every page
- Yahoo now: Sequence-range FETCH; no full UID list
- Test: same as the command above

### Huge mailbox

- Gmail: Page tokens
- Yahoo before: Failed past ~125,000 messages (imaplib refuses a reply line over 1 MB); slow before that
- Yahoo now: Works at any size
- Test: same as the command above

### Next / previous page

- Gmail: Stable page tokens
- Yahoo before: Offsets shifted when new mail arrived (repeats)
- Yahoo now: Cursor allows for new mail
- Test: same as the command above

### Next / previous / first / last message

- Gmail: Works
- Yahoo before: Worked
- Yahoo now: Same answers as Gmail
- Test: parity script

### Empty folder

- Gmail: "Empty list..."
- Yahoo before: Worked
- Yahoo now: Same
- Test: `test_empty_folder_and_unread_flags`

### Read message N / That one / Continue reading

- Gmail: Gmail body rendering
- Yahoo before: Worked; unknown charset gave a wrong "check the connection" error
- Yahoo now: Same rendering as Gmail; unknown charset read as UTF-8 (Gmail too)
- Test: `test_message_bodies_are_read_exactly_like_gmail`

### HTML-only mail

- Gmail: `email_reader`
- Yahoo before: Worked
- Yahoo now: Same output as Gmail
- Test: same as the command above

### Reading marks as read?

- Gmail: No (format=raw)
- Yahoo before: No (BODY.PEEK)
- Yahoo now: No
- Test: same as the command above

### Huge message (> 5 MB)

- Gmail: "too large to read aloud"
- Yahoo before: Downloaded it all first
- Yahoo now: Size checked first
- Test: `test_missing_and_huge_messages`

### Message gone

- Gmail: "no longer available"
- Yahoo before: Worked
- Yahoo now: Same
- Test: same as the command above

### Message details

- Gmail: Summary
- Yahoo before: Worked
- Yahoo now: Same
- Test: parity script

### Non-ASCII sender / subject (RFC 2047, raw UTF-8, folded, broken charset)

- Gmail: Decoded by Google
- Yahoo before: Mostly worked; possible surrogates
- Yahoo now: Decoded once, unfolded, no surrogates
- Test: `test_international_and_damaged_headers...`

### Mark / select / select N through M / select all / clear / what is selected

- Gmail: Works
- Yahoo before: Worked
- Yahoo now: Same
- Test: parity script

### Delete / Delete selected, then Yes

- Gmail: Moves to Trash
- Yahoo before: **Failed on Windows**: Python 3.12 keeps pre-login capabilities, MOVE looked missing; and failed if any folder name came as a literal
- Yahoo now: Works; asks capabilities again after sign-in; UIDPLUS fallback removes only chosen UIDs
- Test: `test_move_and_delete_work_when_move_is_announced_only_after_sign_in`, `test_without_move_only_the_chosen_messages_are_removed`

### Delete inside Trash

- Gmail: Stays in Trash
- Yahoo before: Tried to move Trash into Trash (an error)
- Yahoo now: Stays in Trash
- Test: `test_delete_moves_to_trash_and_never_erases`

### Permanent erase

- Gmail: Never
- Yahoo before: Never
- Yahoo now: Never (no EXPUNGE of other messages)
- Test: same as the command above

### Move / Move to folder / Move selected to

- Gmail: Works
- Yahoo before: **Failed on Windows** (same MOVE bug)
- Yahoo now: Works
- Test: as above, parity script

### "Yes, please." (recognizer comma) to confirm

- Gmail: **Failed** (all providers)
- Yahoo before: Failed
- Yahoo now: Works everywhere
- Test: parity script, `test_spoken_yes_with_punctuation...`

### List folders / Go to folder

- Gmail: Labels
- Yahoo before: **Failed** if any name came as a literal (TypeError)
- Yahoo now: Literals, escapes, `\Noselect`, special-use flags handled
- Test: `test_folders_use_yahoo_mail_names...`

### Go to spam / junk

- Gmail: Works (SPAM)
- Yahoo before: "Could not find that folder" (Yahoo calls it Bulk)
- Yahoo now: Spam; "junk" and "bulk" also work, all providers
- Test: parity script

### Go to drafts / sent / trash / archive

- Gmail: Works
- Yahoo before: Worked (Draft spoken as "Draft")
- Yahoo now: Drafts, Sent, Trash, Archive
- Test: parity script

### Which folder

- Gmail: Works
- Yahoo before: Worked
- Yahoo now: Same
- Test: parity script

### Create / rename / move / delete folder

- Gmail: Works
- Yahoo before: Worked
- Yahoo now: Same; accents and spaces (modified UTF-7)
- Test: `test_folder_changes_with_spaces_and_accents`

### Protect standard folders

- Gmail: Inbox, Sent, Trash, Spam...
- Yahoo before: **Bulk and Draft could be renamed or deleted**
- Yahoo now: Refused like the others
- Test: parity script

### Reply / Reply all / Forward

- Gmail: Context from raw message
- Yahoo before: Worked; forward body not rendered like Gmail
- Yahoo now: Same fields as Gmail, checked key by key
- Test: `test_reply_and_forward_context_match_gmail`

### Send new / reply / forward

- Gmail: Gmail API
- Yahoo before: Worked over SMTP
- Yahoo now: Adds Date and Message-ID; threads with In-Reply-To
- Test: `test_reply_threads_and_forward_quotes_the_original`

### Sent copy

- Gmail: Gmail keeps one
- Yahoo before: **None** (Yahoo SMTP does not reliably save one)
- Yahoo now: IMAP APPEND to Sent, marked read; skipped if Yahoo already has that Message-ID; a failure is spoken but the send still counts
- Test: `test_sent_mail_appears_in_sent_like_gmail`, `test_no_second_sent_copy...`, `test_send_says_when_the_sent_copy_is_missing`

### Bcc

- Gmail: Hidden from recipients, kept in Sent
- Yahoo before: Hidden
- Yahoo now: Hidden; kept in the Sent copy
- Test: `test_sent_mail_appears_in_sent_like_gmail`

### Wrong or revoked app password

- Gmail: Gmail: sign-in message
- Yahoo before: "Could not complete... check the connection" (reading) or "result is uncertain" (sending, wrongly invites a duplicate check)
- Yahoo now: "Yahoo did not accept the saved app password... make a new Yahoo app password, then say add account." Sending: certain, not sent
- Test: `test_wrong_app_password...`, `test_refusals_are_certain...`, `test_expired_app_password_is_explained...`

### Recipient refused

- Gmail: 400: certain
- Yahoo before: "Uncertain"
- Yahoo now: Certain, names the address
- Test: `test_refusals_are_certain...`

### Some recipients refused

- Gmail: Bounce later
- Yahoo before: Reported as uncertain failure though sent
- Yahoo now: "Email sent... Yahoo did not accept X, so they did not get it."
- Test: `test_some_recipients_refused...`

### Server unreachable when sending

- Gmail: Uncertain
- Yahoo before: Uncertain
- Yahoo now: Certain, not sent (nothing reached Yahoo)
- Test: `test_unreachable_server_is_certain...`

### Lost reply after message data

- Gmail: Uncertain
- Yahoo before: Uncertain
- Yahoo now: Uncertain (unchanged)
- Test: same as the command above

### QUIT refused after acceptance

- Gmail: n/a
- Yahoo before: Reported as uncertain
- Yahoo now: Counted as sent
- Test: `test_a_failed_goodbye...`

### Timeouts

- Gmail: 30 s per request
- Yahoo before: 30 s socket timeout, but LOGOUT after a failure could wait again
- Yahoo now: 30 s; failed connections are closed at once
- Test: `test_silent_server_never_freezes_the_app`

### Dropped connection

- Gmail: Error
- Yahoo before: Error
- Yahoo now: Retried once when nothing changed; never after a move was sent
- Test: `test_dropped_connection_*`

### Error when a move is refused before anything changed

- Gmail: "Some messages may have moved"
- Yahoo before: Same
- Yahoo now: Says why and "Nothing was changed"
- Test: `test_expired_app_password_is_explained...`

### Reply when the account cannot be reached

- Gmail: "Open and read a message first" (a guess)
- Yahoo before: Same
- Yahoo now: Speaks the real reason (all providers)
- Test: same as the command above

### App password typed with spaces

- Gmail: n/a
- Yahoo before: Sign-in failed
- Yahoo now: Spaces ignored
- Test: `test_app_password_written_in_groups...`

### Contacts

- Gmail: Local list plus Google Contacts sync
- Yahoo before: Local list only
- Yahoo now: Local list only
- Test: not changed (see below)

### Search mail

- Gmail: Not offered
- Yahoo before: Not offered
- Yahoo now: Not offered
- Test: none

### Attachments

- Gmail: Not read; not forwarded
- Yahoo before: Same
- Yahoo now: Same
- Test: none

### Mark read / unread command

- Gmail: Not offered
- Yahoo before: Not offered
- Yahoo now: Not offered
- Test: none

## Left as is

- **Contacts:** Gmail can sync Google Contacts. Yahoo has only the local
  address book. Yahoo offers CardDAV, but that is new work, not a fix.
- **Search, attachments, mark read or unread:** no provider has these yet.
- **Duplicate Sent copy:** if Yahoo saves its own Sent copy a few seconds
  after the app checks for one, the Sent folder could show two copies.
  The check uses the Message-ID right after sending.
- **Real Yahoo greeting:** the tests cover both a greeting with and without
  MOVE. Which one Yahoo sends today could not be confirmed from here.
