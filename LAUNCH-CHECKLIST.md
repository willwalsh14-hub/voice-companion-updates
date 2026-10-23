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

### Accounts and store reviews (Will)
- [ ] **Gmail:** the Google app is in Testing mode, so only listed test users
      can sign in. Public launch needs Google's verification for Gmail
      scopes, which includes a third-party security assessment for
      restricted scopes. Start early; it takes weeks.
      Alternative in 0.2.153: the Thunderbird account type needs no Google
      or Microsoft approval. If Thunderbird works well for testers, Gmail and
      Outlook sign-in can stay test-only and these two items can be dropped.
- [ ] **Outlook:** complete Microsoft publisher verification for the app
      registration so users do not see an "unverified" warning.
- [x] **Browser extension:** approved and live on the Chrome Web Store
      (version 0.4.0, October 17, 2026).
- [x] **Will:** upload extension 0.4.1 (shipped in app 0.2.152-test) to the
      Chrome Web Store. Submitted for review October 21, 2026. From 0.2.153 embedded Edge is the default browser and
      does not need the extension; the extension stays for people who want
      their own Chrome or Edge, and for sites that need Google sign-in.
- [ ] **Will or a tester:** try Thunderbird email on a real PC: install
      Thunderbird, add the email account, install "Voice Companion Mail
      Connection.xpi" from the app folder (EMAIL SETUP - Helper, section 4),
      then in Companion say Add account and choose Thunderbird. Check reading,
      replying, sending, moving and deleting.
- [ ] **Will or a tester:** on a PC without WebView2 problems, check that web
      pages open in embedded Edge by default and that NVDA or JAWS do not
      fight Companion's speech in that window.
- [ ] Review `PRIVACY-POLICY.md` against what the app actually sends
      (OpenAI narration when the AI voice is on, Google and Microsoft mail APIs,
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
