# Voice Companion launch checklist

Status as of October 21, 2026 (version 0.2.160-test in progress). Items marked **Will**
need you or an account only you control; everything else Claude can do in
pull requests. Update this file as items are finished.

## Must do before launch

### Privacy of the beta and the code
- Decision (Will, October 21): the code stays in this public repository.
  Anyone can read the code, the GitHub issues and the release installers.
- [ ] Remind testers that GitHub issues here are public: no passwords, email
      contents or personal details in bug reports (the bug form says this too).

### Trust and installation
- [ ] **Code-sign the installer and app (Will: buy a code-signing certificate, e.g. an OV or EV certificate or Azure Trusted Signing).**
  Without it, Windows SmartScreen warns on every install, and updates are
  trusted only through GitHub (the installer's SHA-256 comes from the same
  release). Claude then adds signing to the build and an Authenticode
  check to `app_updates.py` and `apply-update.ps1` before an update runs.
- [ ] Test the installer on a clean Windows 11 PC that has never had the
      build tools (the build guide's "Before giving it to the veteran" steps),
      with NVDA and with JAWS running, to check that speech does not clash.

- [ ] **Will or a tester (update close step, 0.2.172):** with Chrome, Edge
      and Thunderbird open and the user guide open in Word, accept an update.
      Check that you hear that Word is using a Voice Companion file, that
      Chrome, Edge and Thunderbird stay open, and that the update installs
      and Voice Companion starts again once Word is closed. The 0.2.171 update
      failed on Will's PC because Setup could not close VoiceCompanion.exe and
      four browser host processes; `close-companion.ps1` now closes them first.

### Accounts and store reviews (Will)
- [x] **Gmail and Outlook verification:** no longer needed. Will decided on
      October 26, 2026 that email works only through Thunderbird and Yahoo.
      The direct Gmail and Microsoft sign-in, Google Contacts sync and their
      app registrations were removed (branch `claude/email-thunderbird-only`).
      Saved Gmail and Microsoft accounts are removed at startup, said once.
      Will can delete the `GOOGLE_CLIENT_JSON` repository secret, and the
      Google and Microsoft app registrations, after that release is out.
- [ ] **Will or a tester (Thunderbird-only email):** on a PC that had a
      direct Gmail account, start the new version once. Check that it says
      the Gmail account was removed, then selects the Thunderbird account,
      and that saved email drafts still open and send from it.
- [x] **Browser extension:** approved and live on the Chrome Web Store
      (version 0.4.0, October 17, 2026).
- [x] **Will:** upload extension 0.4.1 (shipped in app 0.2.152-test) to the
      Chrome Web Store. Submitted for review October 21, 2026. From 0.2.153 embedded Edge is the default browser and
      does not need the extension; the extension stays for people who want
      their own Chrome or Edge, and for sites that need Google sign-in.
- [ ] **Will or a tester:** try Thunderbird email on a real PC: install
      Thunderbird, add the email account, install "Voice Companion Mail
      Connection.xpi" from the app folder (EMAIL SETUP - Helper, section 3),
      then in Companion say Add account and choose Thunderbird. Check reading,
      replying, sending, moving and deleting.
- [ ] **Will or a tester:** on a PC without WebView2 problems, check that web
      pages open in embedded Edge by default and that NVDA or JAWS do not
      fight Companion's speech in that window.
- [ ] **Will (Thunderbird 154 and newer, branch `claude/thunderbird-154`):** on
      his own PC with Thunderbird ESR and working accounts, use email once and say
      yes to the update question (or say "Update Thunderbird"). Check that every
      account still opens and sends without a new sign-in, then add the Microsoft
      365 address with "Add Thunderbird account" and check that it says "It will
      connect with Microsoft Graph". The update itself passed on a Windows runner
      with real Thunderbird ESR 153.6 to Release 159 (try PR 82). A real Graph
      sign-in, saved passwords and the Graph choice page could not be checked there.
- [ ] Review `PRIVACY-POLICY.md` against what the app actually sends
      (OpenAI narration when the AI voice is on, Yahoo mail servers,
      station and podcast directories, GitHub for updates).

- [ ] **Will or a tester (0.2.158):** try on a real PC:
      Thunderbird setup with Thunderbird closed (say yes to install and start);
      "Set up Chrome" with an existing Chrome and with a fresh install (does the
      Enable extension prompt get focus and get read?); YouTube in embedded Edge
      (speed, Skip ad, app mode with K/J/L, seek and volume sliders); Alt in
      the email inbox and in a draft.

- [ ] **Will or a tester (0.2.160):** on a real PC:
  - F12 once and twice, and Insert+F12; Control+F12 sleeps and wakes.
  - Browser sound gets quieter while Companion speaks (embedded Edge and Chrome), each ducking level, and NVDA is never lowered.
  - Add Thunderbird account with Thunderbird 153 or newer: Continue comes on after the address is filled in; Gmail sign-in opens in embedded Edge and in Chrome.
  - Back on a few sites; app mode with Control+Shift+Space on YouTube (K, J, L, sliders) feels instant.
  - Arrow and Tab into text boxes on sites Will uses; tell Claude any site where typing does not go in.
- [ ] Num Lock read as Alt (key_state.py, branch claude/numlock-alt): the Windows trial showed real Alt key presses sent by the test did not arrive as expected. Investigate before releasing.

- [ ] Window freeze in Windows release tests (0.2.158 to 0.2.160): a new window sometimes never ran its first update (thread inside Tk mainloop, 'checks done': 0). From 0.2.160 the real-window key tests run in their own process after the other release tests (real_window_tests.py). If they still freeze there, the window itself can freeze and must be fixed before launch. Leads: main-thread COM (test_file_manager Dispatch), windows left by failed tests, cross-thread activation messages.
- [ ] Alt opened the Windows system menu (Will, Oct 23): Tk's Windows Alt bindings on "all" outrank Companion's. Fix on claude/alt-windows (PR #47 trial); it likely also explains the Num Lock trial failures (PR #40, #45).

### Reliability work (Claude)
- [ ] Split command handling in `companion.py` into smaller parts with an
      explicit priority order, without changing behaviour. Safety net in place:
      `tools/routing_snapshot.py` records 4,067 phrase-and-screen results. First
      piece moved: the email inbox screen (`handle_mailbox_screen`). Next: the
      web, document and email-draft screens, then a priority table for the
      early checks in `_handle`.
- [ ] Widen the routing snapshot to more screen states (an open email message,
      a web page with a list or form field focused, a pending send review,
      media playing). Paths not reached by the snapshot are not yet protected.
- [ ] Reduce shared global state in `companion.py` so tests and features do
      not leak into each other.
- [x] Write all settings files atomically (`speech-settings.json`,
      `email-list-size.txt`, `browser-choice.txt`). Done in the routing
      safety-net pull request; ships with the next release.
- [ ] Replace hand-made test fakes with real library behaviour where it
      matters (mail providers, file system, browser).
- [ ] Add an opt-in "Send problem report" command that saves a short,
      private diagnostic file (version, last commands, error) that a tester
      can attach to a bug report. No document or email contents.

### YouTube screen (branch claude/youtube-player, not released yet)
- [ ] **Will or a tester, on the real youtube.com:** the page reader was built
      and tested on saved pages shaped like youtube.com, because the live site
      cannot be reached from the build machine. Check on a real PC, signed out
      in embedded Edge and signed in with Chrome: Home, search with a filter
      (This week, Only channels), a channel, Watch later, History, playing a
      video, K, J, L, C and R (captions), Q (quality), N (next video), ads
      (said, and Skip offered when allowed), T, U and W (each asks first), and
      reading comments. If a list says "YouTube changed its page", note which
      list; the reader then needs the new page shape added (youtube_page.py).
- [ ] **Will:** Google may refuse sign-in in guided Chrome too, because a
      program controls it ("This browser or app may not be secure"). If it
      does, sign in with your own Chrome and the extension instead; tell Claude
      so the spoken offer can say that first.

## Should do before launch

Problems found in the October 2026 review. All fixed in 0.2.153-test, with tests:
- [x] A manual "check for updates" leaves a flag set, so a later automatic
      update offer can interrupt any screen, including document editing.
- [x] HTML email with an unclosed `<head>` reads as blank.
- [x] Two email drafts whose titles differ only in punctuation share one file.
- [x] Opening a Gmail list of 1,000 messages makes about 1,000 separate
      requests (slow, can hit Google's rate limit).
- [x] Google Contacts sync stops completely if one contact fails.
- [x] One malformed scheduled radio recording repeats an error every 10 seconds.
- [x] The update helper does not restart the app if the old copy is still
      running after 30 seconds.
- [x] Speech and media message queues can raise an error if their worker
      thread has stopped. (Only the update window's queue could; fixed.)
- [x] Listing or cancelling a damaged scheduled recording stopped the app.

## Later
- [x] Main builds for 0.2.154 and 0.2.155 each failed once and passed on rerun
      (logs unreadable). The 0.2.156 pull request failed the same step, and
      the new build annotations showed a race in test_native_settings: it typed
      into the previous Options dialog before the next one was drawn. Fixed;
      most likely the same cause. If a main build fails again, read the
      annotations.
- [x] test_skip_ad_in_a_shadow_root failed on some runs and passed on others,
      on main too. Cause: the page made its Skip button with a 300 ms timer,
      which raced the first page reading on a busy computer. When the timer
      won, the button was already known and no news was said. The test now
      shows the button only after the page is read.
- [x] test_a_stalled_main_loop_writes_a_freeze_report_once failed once in a
      full Linux run (October 29, 2026) and passed alone. Cause: the freeze
      report was written in two steps, and on a busy computer the test read it
      before the thread stacks were in it. The report is now written whole,
      then put in place, so it is never seen half written.
- [ ] Known and kept by design: in Dictation only mode, a sentence starting
      with "find" still runs the editor's find command.
- [ ] A volunteer tester group beyond the private beta, with a short weekly
      check-in script.

## How bugs get fixed now
1. A tester or Will opens a GitHub issue ("Something went wrong" form), or
   tells Claude in one sentence.
2. Claude reproduces it, adds a failing test, fixes it, runs
   `tools/run_checks.py --compare main`, and opens a pull request.
3. The three GitHub checks must pass, including the Windows build.
4. Will says go; Claude bumps the version, writes release notes, merges,
   and confirms the release is live.

## 0.2.172: Thunderbird add-on check failed once, then passed (found October 28, 2026)
- quitChecks waited a fixed 5 ms for the dispatched quit. Windows timers are about 15 ms, so the wait could end first.
- The test now waits for the quit itself, up to 2 seconds.

## 0.2.172: next station after a broken one failed to start on Windows (October 28, 2026)
- test_consumer_audio: after a station that failed, the next one said it could not start.
- Likely cause: the failed stream's emptying load() interrupts the next play() with an AbortError.
- The player now tries an interrupted start again, and records why a start failed (last_start_error), so a failure names its reason.
## 0.2.173: real window tests crashed with Tcl_AsyncDelete on Windows (October 28, 2026)
- The new hint line widget was kept as self.hint_label and never released at close, so another thread freed it.
- Fixed by releasing it on the window thread. test_window_widget_release now checks that every widget kept on the window is released.

## 0.2.174: Thunderbird stand-in tests passed on a try branch but failed in the build (October 29, 2026)
- The stand-in was a copy of sys.executable named thunderbird.exe. In the build, sys.executable is a virtual environment's python.exe, which only launches the real Python as another program, so the windows belonged to python.exe.
- Fixed by copying sys._base_executable. Lesson: a try branch must run tests the way the build does (same Python, same order).

## 0.2.176: Thunderbird add-on check failed on Windows (October 29, 2026)
- The account setup test cleared the form with a fixed 40 ms timer, which raced the fill on a slow Windows runner.
- The test now clears the form after the open call returns.
## Thunderbird add account test failed only on a busy computer (found October 29, 2026)
- experimentChecks, the Thunderbird 158 form reset case, failed when the computer was busy and passed alone.
- Cause: Account Hub can clear its form during the add-on's last short wait. The add-on then answered "not filled in", and Voice Companion told the person to type the address. A real defect, not only a test problem.
- Fixed by filling the form again before answering. A new test makes the reset happen during that wait every time.

## Thunderbird 159 on a real Windows runner (October 29, 2026, try PR 82)
- Windows PowerShell started from PowerShell 7 cannot load Get-AuthenticodeSignature (inherited PSModulePath). Mozilla's and Google's installers were refused with no reason. Both checks now drop PSModulePath and name their reason.
- Thunderbird lists a Microsoft Graph account to add-ons as type "extension:graph" (prefs.js says "graph"). The fakes now use the real type.
- GitHub keeps only 10 notices and 10 warnings per step, and job logs cannot be read from Claude's session. Try workflows report results in grouped annotations.

## Podcast player without a window (October 29, 2026, branch claude/podcast-feedback)
- The radio and podcast player's Firefox now runs headless, so no window takes the keyboard.
- On Windows (try branch, PR 86) it showed no window, kept the focus, and played forward, three runs out of three.
- [ ] Build computers have no sound device. A person must listen once to confirm the sound reaches the speakers. If it does not, start Firefox with a window again, minimized and off the taskbar.

## 0.2.185: update close test failed in the main build after passing on the pull request (October 30, 2026)
- Cause: `test-update-close-windows.ps1` read the close log with `Get-Content` while the close step was still appending to it. Windows refused the read ("being used by another process"), and the error ended the test.
- Fix: `Read-Shared` opens the log with read and write sharing. A busy file counts as "not ready yet", and the wait goes on.
- 0.2.185 was merged but not published. 0.2.186 carries its changes.
