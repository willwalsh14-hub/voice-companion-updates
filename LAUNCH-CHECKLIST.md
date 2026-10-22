# Voice Companion launch checklist

Status as of October 21, 2026 (version 0.2.152-test). Items marked **Will**
need you or an account only you control; everything else Claude can do in
pull requests. Update this file as items are finished.

## Must do before launch

### Privacy of the beta and the code
- [ ] **Move the source code to a private repository; keep this one for releases only. (Will + Claude)**
  This repository is public, so anyone can read the code, the GitHub issues,
  and download the installer. Installed apps check for updates at
  `github.com/willwalsh14-hub/voice-companion-updates/releases/latest/...`,
  which only works without sign-in while this repository is public. So:
  1. Will creates a new private repository (for example `voice-companion`).
  2. Claude moves the source and build workflow there, and changes the
     workflow to publish releases to this public repository using a
     fine-grained token stored as a secret (`publish_release.py` already
     takes the target repository from the update feed address).
  3. This repository keeps only releases, so the update address in every
     installed copy keeps working and no app change is needed.
  4. Bug reports move to the private repository so testers' reports are not public.
- [ ] Until then, remind testers that GitHub issues here are public.

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
- [ ] **Outlook:** complete Microsoft publisher verification for the app
      registration so users do not see an "unverified" warning.
- [ ] **Browser extension:** publish 0.4.1 or later to the Chrome Web Store
      (and Edge Add-ons) so users never need Developer mode or "Load unpacked".
- [ ] Review `PRIVACY-POLICY.md` against what the app actually sends
      (OpenAI narration when the AI voice is on, Google and Microsoft mail APIs,
      station and podcast directories, GitHub for updates).

### Reliability work (Claude)
- [ ] Split command handling in `companion.py` into smaller parts with an
      explicit priority order, without changing behaviour. (In progress,
      separate pull request.)
- [ ] Reduce shared global state in `companion.py` so tests and features do
      not leak into each other.
- [ ] Write all settings files atomically (`speech-settings.json`,
      `email-list-size.txt`, `browser-choice.txt`); a power cut mid-write
      currently resets speech settings to defaults.
- [ ] Replace hand-made test fakes with real library behaviour where it
      matters (mail providers, file system, browser).
- [ ] Add an opt-in "Send problem report" command that saves a short,
      private diagnostic file (version, last commands, error) that a tester
      can attach to a bug report. No document or email contents.

## Should do before launch

Possible problems found in the October 2026 review but not yet confirmed or fixed:
- [ ] A manual "check for updates" leaves a flag set, so a later automatic
      update offer can interrupt any screen, including document editing.
- [ ] HTML email with an unclosed `<head>` reads as blank.
- [ ] Two email drafts whose titles differ only in punctuation share one file.
- [ ] Opening a Gmail list of 1,000 messages makes about 1,000 separate
      requests (slow, can hit Google's rate limit).
- [ ] Google Contacts sync stops completely if one contact fails.
- [ ] One malformed scheduled radio recording repeats an error every 10 seconds.
- [ ] The update helper does not restart the app if the old copy is still
      running after 30 seconds.
- [ ] Speech and media message queues can raise an error if their worker
      thread has stopped.

## Later
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
