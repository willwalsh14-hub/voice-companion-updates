# Yahoo Mail parity with Gmail

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

## Matrix

| Command or behaviour | Gmail | Yahoo before | Yahoo now | Test |
|---|---|---|---|---|
| Open inbox / Check Yahoo / Email | Lists 10 newest | Worked; fetched each message separately | One FETCH per page, newest first | `test_list_is_newest_first...` |
| List size 10-100, 1000, all | Pages of 100 | Worked; `UID SEARCH ALL` every page | Sequence-range FETCH; no full UID list | same |
| Huge mailbox | Page tokens | Failed past ~125,000 messages (imaplib refuses a reply line over 1 MB); slow before that | Works at any size | same |
| Next / previous page | Stable page tokens | Offsets shifted when new mail arrived (repeats) | Cursor allows for new mail | same |
| Next / previous / first / last message | Works | Worked | Same answers as Gmail | parity script |
| Empty folder | "Empty list..." | Worked | Same | `test_empty_folder_and_unread_flags` |
| Read message N / That one / Continue reading | Gmail body rendering | Worked; unknown charset gave a wrong "check the connection" error | Same rendering as Gmail; unknown charset read as UTF-8 (Gmail too) | `test_message_bodies_are_read_exactly_like_gmail` |
| HTML-only mail | `email_reader` | Worked | Same output as Gmail | same |
| Reading marks as read? | No (format=raw) | No (BODY.PEEK) | No | same |
| Huge message (> 5 MB) | "too large to read aloud" | Downloaded it all first | Size checked first | `test_missing_and_huge_messages` |
| Message gone | "no longer available" | Worked | Same | same |
| Message details | Summary | Worked | Same | parity script |
| Non-ASCII sender / subject (RFC 2047, raw UTF-8, folded, broken charset) | Decoded by Google | Mostly worked; possible surrogates | Decoded once, unfolded, no surrogates | `test_international_and_damaged_headers...` |
| Mark / select / select N through M / select all / clear / what is selected | Works | Worked | Same | parity script |
| Delete / Delete selected, then Yes | Moves to Trash | **Failed on Windows**: Python 3.12 keeps pre-login capabilities, MOVE looked missing; and failed if any folder name came as a literal | Works; asks capabilities again after sign-in; UIDPLUS fallback removes only chosen UIDs | `test_move_and_delete_work_when_move_is_announced_only_after_sign_in`, `test_without_move_only_the_chosen_messages_are_removed` |
| Delete inside Trash | Stays in Trash | Tried to move Trash into Trash (an error) | Stays in Trash | `test_delete_moves_to_trash_and_never_erases` |
| Permanent erase | Never | Never | Never (no EXPUNGE of other messages) | same |
| Move / Move to folder / Move selected to | Works | **Failed on Windows** (same MOVE bug) | Works | as above, parity script |
| "Yes, please." (recognizer comma) to confirm | **Failed** (all providers) | Failed | Works everywhere | parity script, `test_spoken_yes_with_punctuation...` |
| List folders / Go to folder | Labels | **Failed** if any name came as a literal (TypeError) | Literals, escapes, `\Noselect`, special-use flags handled | `test_folders_use_yahoo_mail_names...` |
| Go to spam / junk | Works (SPAM) | "Could not find that folder" (Yahoo calls it Bulk) | Spam; "junk" and "bulk" also work, all providers | parity script |
| Go to drafts / sent / trash / archive | Works | Worked (Draft spoken as "Draft") | Drafts, Sent, Trash, Archive | parity script |
| Which folder | Works | Worked | Same | parity script |
| Create / rename / move / delete folder | Works | Worked | Same; accents and spaces (modified UTF-7) | `test_folder_changes_with_spaces_and_accents` |
| Protect standard folders | Inbox, Sent, Trash, Spam... | **Bulk and Draft could be renamed or deleted** | Refused like the others | parity script |
| Reply / Reply all / Forward | Context from raw message | Worked; forward body not rendered like Gmail | Same fields as Gmail, checked key by key | `test_reply_and_forward_context_match_gmail` |
| Send new / reply / forward | Gmail API | Worked over SMTP | Adds Date and Message-ID; threads with In-Reply-To | `test_reply_threads_and_forward_quotes_the_original` |
| Sent copy | Gmail keeps one | **None** (Yahoo SMTP does not reliably save one) | IMAP APPEND to Sent, marked read; skipped if Yahoo already has that Message-ID; a failure is spoken but the send still counts | `test_sent_mail_appears_in_sent_like_gmail`, `test_no_second_sent_copy...`, `test_send_says_when_the_sent_copy_is_missing` |
| Bcc | Hidden from recipients, kept in Sent | Hidden | Hidden; kept in the Sent copy | `test_sent_mail_appears_in_sent_like_gmail` |
| Wrong or revoked app password | Gmail: sign-in message | "Could not complete... check the connection" (reading) or "result is uncertain" (sending, wrongly invites a duplicate check) | "Yahoo did not accept the saved app password... make a new Yahoo app password, then say add account." Sending: certain, not sent | `test_wrong_app_password...`, `test_refusals_are_certain...`, `test_expired_app_password_is_explained...` |
| Recipient refused | 400: certain | "Uncertain" | Certain, names the address | `test_refusals_are_certain...` |
| Some recipients refused | Bounce later | Reported as uncertain failure though sent | "Email sent... Yahoo did not accept X, so they did not get it." | `test_some_recipients_refused...` |
| Server unreachable when sending | Uncertain | Uncertain | Certain, not sent (nothing reached Yahoo) | `test_unreachable_server_is_certain...` |
| Lost reply after message data | Uncertain | Uncertain | Uncertain (unchanged) | same |
| QUIT refused after acceptance | n/a | Reported as uncertain | Counted as sent | `test_a_failed_goodbye...` |
| Timeouts | 30 s per request | 30 s socket timeout, but LOGOUT after a failure could wait again | 30 s; failed connections are closed at once | `test_silent_server_never_freezes_the_app` |
| Dropped connection | Error | Error | Retried once when nothing changed; never after a move was sent | `test_dropped_connection_*` |
| Error when a move is refused before anything changed | "Some messages may have moved" | Same | Says why and "Nothing was changed" | `test_expired_app_password_is_explained...` |
| Reply when the account cannot be reached | "Open and read a message first" (a guess) | Same | Speaks the real reason (all providers) | same |
| App password typed with spaces | n/a | Sign-in failed | Spaces ignored | `test_app_password_written_in_groups...` |
| Contacts | Local list plus Google Contacts sync | Local list only | Local list only | not changed (see below) |
| Search mail | Not offered | Not offered | Not offered | - |
| Attachments | Not read; not forwarded | Same | Same | - |
| Mark read / unread command | Not offered | Not offered | Not offered | - |

## Left as is

- **Contacts:** Gmail can sync Google Contacts. Yahoo has only the local
  address book. Yahoo offers CardDAV, but that is new work, not a fix.
- **Search, attachments, mark read or unread:** no provider has these yet.
- **Duplicate Sent copy:** if Yahoo saves its own Sent copy a few seconds
  after the app checks for one, the Sent folder could show two copies.
  The check uses the Message-ID right after sending.
- **Real Yahoo greeting:** the tests cover both a greeting with and without
  MOVE. Which one Yahoo sends today could not be confirmed from here.
