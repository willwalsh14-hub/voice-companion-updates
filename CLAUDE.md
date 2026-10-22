# Voice Companion — rules for every working session

Voice Companion is a Windows voice and keyboard app for blind and low-vision
veterans. It is in a private beta: Will (willwalsh14-hub) and a few testers. The
people using it cannot see the screen, so a wrong word, a lost sentence or a
silent failure is a real problem for them. Work to that standard without being
asked.

## How work reaches users (read this first)

- **A merge to `main` that changes anything under `voice_companion/` builds and
  publishes an update to every installed copy.** `.github/workflows/windows-installer.yml`
  builds, tests and then runs `publish_release.py` on `main`.
- Publishing refuses a version that is already released. So every merge that
  touches `voice_companion/` must be a complete release:
  1. Bump the version everywhere: `APP_VERSION` in `companion.py`,
     `#define AppVersion` in `VoiceCompanion.iss`, the three `0.2.x-test`
     strings in `build-windows.ps1`, and `Guide version:` in
     `START HERE - Veteran.txt` and `.html`.
  2. Rewrite `voice_companion/release-notes.json` for the new version (see
     "Writing for users" below). It needs both sections, "New in this version"
     and "What works better", each with at least one topic.
  3. Update the user guide (`START HERE - Veteran.txt` and `.html`, same
     wording in both) if any command or behaviour a user relies on changed.
  4. If files in `regular_browser_extension/` changed, bump the version in its
     `manifest.json`, and tell Will that testers must choose Reload on the
     extension card.
- Files outside `voice_companion/` (this file, `tools/`, `.github/ISSUE_TEMPLATE/`,
  `LAUNCH-CHECKLIST.md`) do not trigger a release.
- **Never merge to `main` without Will's go-ahead for that release**, and never
  while any pull-request check is red. Work on a `claude/...` branch and open a
  pull request.

## Before opening or updating a pull request

1. Reproduce the problem first. Write a test that fails before the fix.
2. Run the checks the way the Windows build does — all release tests in one
   process, compared with `main`:

   ```
   python3 tools/run_checks.py --compare main
   ```

   It must say "No new failures." About nine tests fail on Linux for
   environment reasons (Windows fonts, eSpeak binaries, missing packages);
   `--compare` separates those from real regressions. Running files one at a
   time is not enough: shared state in `companion` leaks between files.
   It also checks command routing: every command phrase and everyday sentence
   is sent to the app on seven screens and compared with
   `tools/routing-snapshot.json`. If a change in behaviour is intended, run
   `python3 tools/routing_snapshot.py --update` and list the changed records,
   and why, in the pull request. An unexplained routing change is a bug.
3. If web or browser code changed, also run `python3 tools/run_checks.py --live --compare main`
   and `node voice_companion/regular_browser_extension/test-worker.cjs`.
4. After pushing, wait for all three GitHub checks ("Build and publish Windows
   installer", "Validate updater fixes and live browsers", "Verify standard
   browser features through extension"). They cover Windows-only behaviour
   that cannot run here. If one fails and its log cannot be read from this
   session, ask Will to paste the end of the failed step's log.

## Code rules learned from earlier bugs

- **Speech is messy input.** Recognizers add periods, commas and capitals
  ("Yes, please.", "VA dot gov."). People say "forward slash", "underscore",
  "dash". Every spoken command, address and name must tolerate that.
- **Writing comes first in writing fields.** In a document, email or note, a
  sentence that only starts like a command ("Rate this product…", "Use the
  back door at night.", "Help with the dishes") is text. Only a complete,
  valid command is a command. `writing_dictation()` in `companion.py` is the
  helper for this.
- **Order matters in `companion._handle`.** It is one long chain of checks;
  a broad pattern placed early swallows later commands. When adding a
  command, check what it could intercept and what could intercept it.
- **Empty and edge states:** empty document, empty mailbox, first and last
  list item, blank paragraph, file open in another program, damaged file.
  An exception there stops the whole app.
- **Tests must match real behaviour.** Fakes must behave like the real library
  (example: `imaplib` stores capabilities as text, not bytes; a bytes fake hid
  a bug where Yahoo delete never worked). Prefer real objects and real files.
- **Never lose user data.** Write files atomically (temporary file, then
  replace). Keep the file a user opened as the file that is saved.
- **Do not change deliberate design to make a test pass.** Read the existing
  test first; it usually records an intended behaviour.

## Writing for users

- Release notes, the guide and spoken messages use plain words at an 8th-grade
  reading level or below. `test_documentation_quality` enforces this;
  `documentation_quality.report('.')` lists any sentence over target.
- Short sentences. Say what the user notices, not how the code changed.
- Spoken messages say what happened and what to do next.

## Testing on Linux

- `tools/linux-test-stubs/` holds stand-ins for Windows-only packages (`vosk`,
  `websockets`) so modules import. `tools/run_checks.py` adds them only when the
  real package is missing. Never put them on the app's own path.
- The pinned `pypdf` is 6.x; if a 5.x is installed, `document_reader` PDF
  tests fail with `Configuration` missing — that is environment, not a bug.
- Liblouis (braille) is not installed here; it can be built from the
  `liblouis-3.39.0.tar.gz` source release on GitHub when guide output needs
  checking.

## Repository facts

- Main program: `voice_companion/companion.py` (command routing in `_handle`).
- Editor: `document_editor.py`; email: `email_draft.py`, `mail_voice.py`,
  `mailbox_access.py`, `yahoo_mailbox.py`; web: `web_assistant.py`,
  `web_browse.py`, `browser_navigation.py`; media: `media_hub.py`; updates:
  `app_updates.py`, `apply-update.ps1`, `publish_release.py`.
- Launch work in progress is tracked in `LAUNCH-CHECKLIST.md`.
