# Voice Companion — rules for every working session

Voice Companion is a Windows voice and keyboard app for blind and low-vision
veterans. It is in a private beta: Will (willwalsh14-hub) and a few testers. The
people using it cannot see the screen, so a wrong word, a lost sentence or a
silent failure is a real problem for them. Work to that standard without being
asked.

## About Will (read before writing anything for him)

- Will is fully blind and an adaptive technology specialist. Accessibility,
  user interface and user experience matter most to him.
- He is picky and demanding about app design. Bugs must be squashed,
  proactively: look for holes and sloppy code without being asked, and fix
  them before testers find them. This app must be bug free before large groups
  of people use it.
- **How to present information to Will** (messages, pull requests, reports,
  guides, release notes, anything in this repository):
  - Organize by headings.
  - Label every link clearly; never "click here" or a bare address.
  - Use plain bulleted lists for notes, keyboard shortcuts and options.
  - Use numbered lists only for step-by-step instructions.
  - **Never use tables**, not even for comparisons. Use a heading or bullet
    for each item instead.
  - Short sentences in plain words.

## How work reaches users (read this first)

- **A merge to `main` that changes anything under `voice_companion/` builds and
  publishes an update to every installed copy.** `.github/workflows/windows-installer.yml`
  builds, tests and then runs `publish_release.py` on `main`.
- Publishing refuses a version that is already released. So every merge that
  touches `voice_companion/` must be a complete release:
  1. Bump the version everywhere: `APP_VERSION` in `companion.py`,
     `#define AppVersion` in `VoiceCompanion.iss`, the three `0.2.x-test`
     strings in `build-windows.ps1` (one is written `0\.2\.x-test`),
     `Guide version:` in `START HERE - Veteran.txt` and `.html`, and the
     placeholder in `.github/ISSUE_TEMPLATE/bug-report.yml`.
  2. Rewrite `voice_companion/release-notes.json` for the new version (see
     "Writing for users" below). It needs both sections, "New in this version"
     and "What works better", each with at least one topic.
  3. Update the user guide (`START HERE - Veteran.txt` and `.html`, same
     wording in both) if any command or behaviour a user relies on changed.
  4. If files in `regular_browser_extension/` changed, bump the version in its
     `manifest.json`. The release sends it to the Chrome Web Store for review,
     and store copies update by themselves. Mention Reload on the extension
     card only for copies a helper loaded by hand.
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

## How releases are run (agreed with Will, October 22, 2026)

Claude is Will's lead developer. Will wants finished, working releases without
back-and-forth, and failures fixed so they do not come back.

- **Approval.** Will may approve publishing in advance ("publish without me,
  fix failures if they arise"). With that approval, merge as soon as all three
  checks are green, fix any failure yourself, and message Will only when it is
  live or when something truly needs him. Without it, ask before merging.
- **Every failure must name itself.** `build-windows.ps1` writes the reason a
  build stopped, and the failed test names with their output, into GitHub
  annotations (`Write-BuildAnnotation`, `Invoke-UnitTests`). Read them with
  `gh api repos/willwalsh14-hub/voice-companion-updates/check-runs/<id>/annotations`.
  Never rerun or guess blind: if a step fails without a readable reason, first
  make that step report its reason. One failed run should be enough to find
  the cause.
- **New Windows-only tests are tried before they join a release.** Anything that
  cannot run on Linux (real Tk window keys, WebView2, Chrome or Windows UI
  Automation, PowerShell, NVDA/JAWS) goes first on a separate `claude/try-...`
  branch with a draft pull request, and joins a release only after it passes on
  Windows. Do not hold a release for an untested test.
- **Small releases.** A fix Will is waiting for (e.g. Gmail broken) ships alone.
  Bundle big features only when nothing urgent is waiting.
- **A test that passes on rerun is still a bug.** Find why (timing, focus,
  threads, leftover state) and fix the cause, then note it in
  LAUNCH-CHECKLIST.md.
- **Check in on long work.** Helpers can stop when a session is interrupted.
  Set a check-in (send_later) for long tasks and verify commits are still
  arriving.

## Windows test lessons

- **Tk:** create every `StringVar`/`BooleanVar`/`IntVar` with its window
  (`tk.StringVar(root, ...)`). Without it the variable joins the first Tk
  window ever made; if that window lives on another thread, the new window
  freezes. A check in `test_screen_menus.py` enforces this.
- **Tk threads:** Tk objects must be freed on the thread that made them, or
  Python stops with "Tcl_AsyncDelete: async handler deleted by the wrong
  thread". Do not keep widgets in test locals or tracebacks.
- **Never read another thread's frames eagerly.** `sys._current_frames()` in an
  assertion message is evaluated on every call, even when the test passes; if
  the window thread ends meanwhile, its Tk objects are freed on the test thread
  and Python stops with "Tcl_AsyncDelete". Collect diagnostics only after a
  failure (`if not ok: self.fail(window.debug_state())`) and release frames at once.
- **Synthetic key presses** in Tk go to the window with keyboard focus. Call
  `focus_force()` first; a child process may not be allowed to take focus.
- **The window's update loop** (`poll` in `app_window.py`) must never stop: it
  records errors in `ui_errors` and always reschedules.
- **Small screens and large text:** Windows test runners have small screens. A widget
  that gets no room is unmapped, and Windows then drops key presses sent to it. Real
  window tests can run on Linux, one test per process, with a Tk-enabled Python
  (`uv python install 3.12`) under `xvfb-run -s "-screen 0 1024x768x24"`, after
  replacing `real_window_tests.real_window` with a function that returns the class.
- **Tests that patch `sys.modules`** (e.g. `patch.dict(sys.modules, ...)`)
  clear and refill it, which can disturb other threads. Patch `builtins.__import__`
  or the specific attribute instead.

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
