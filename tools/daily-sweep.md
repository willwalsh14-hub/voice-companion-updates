# Daily bug sweep (agreed with Will, October 28, 2026)

Will asked for a daily fine-comb check of the whole app, with many helpers. It
runs every night, with no end date, until Will says to stop. This is the playbook.
Follow CLAUDE.md first; it overrides anything here.

## Work quietly (Will, October 28, 2026)
- Do not message Will when a sweep finishes or a release goes out. He does not want
  to be interrupted while sleeping or busy.
- Message him (SendUserMessage) only for:
  - a design question that needs his decision
  - a critical stop that needs him: his permission, his computer, an account, a
    secret, or a failure you cannot fix yourself
- Everything else goes in the release notes and in `tools/sweep-log.md`, so he can
  read it or ask for it later.

## Tools in this folder
- `tools/helper-rules.md`: the rules every helper follows. Give each helper its path.
- `tools/run_checks.py --compare main` (and `--live`): the release checks.
- `tools/wintests.sh <outfile> <test names>`: real Tk window tests on Linux under a
  virtual screen, one test per process. Use `XRES=800x600x24` for small screens.
- `tools/union_build.py <file>`: resolves merge conflicts in `build-windows.ps1`
  by keeping the test names and hidden imports from both sides.
- `tools/ship.sh <PR> <SHA> <VERSION>`: waits for the three checks, merges, and
  waits until the release is published. Exit code 1 means a pull-request check failed,
  and 2 means the publish build failed. Both print the annotations.

## Steps
1. Make sure the repository is cloned and up to date with main. Read CLAUDE.md
   and LAUNCH-CHECKLIST.md. Read the last few releases' notes and git log to see
   what changed recently. New code is where bugs hide.
2. Make one worktree and one `claude/sweep-<date>-<area>` branch per helper, from
   main. Add a fetch refspec for each branch.
3. Start the six helpers in parallel, one per area below. Each one acts as a real
   customer first, then reads the code. Tell each one:
   - fix real defects, each with a test that fails first
   - report design questions, and do not change them
   - commit and push in small steps
   - end with the report described in the helper rules
4. Set a send_later check-in for 90 minutes. Make sure commits are still arriving,
   and resume any helper that stopped.
5. When all have reported, merge the branches into `claude/release-<next version>`
   from main. Resolve conflicts with care. Then:
   - regenerate the guide HTML, the inventory and the routing snapshot
   - bump the version everywhere (CLAUDE.md lists the files)
   - write release-notes.json at 8th-grade level
6. Run the checks: `run_checks.py --compare main`, `--live --compare main`, and both
   node suites. Every result must be "No new failures".
7. New Windows-only tests go first on a `claude/try-...` branch with a short workflow
   that runs only them. Close that draft pull request when done.
8. Open the pull request and run `tools/ship.sh`. Will has given standing approval to
   publish when all checks pass. If a check fails, read the annotations, find the
   cause, fix it and ship again. A test that passes on rerun is still a bug.
9. Record every bug fix, not only the highlights:
   - in release-notes.json, under "What works better", one topic per area, in plain
     words at 8th-grade level
   - in `tools/sweep-log.md`, under the date and version: each fix as a short bullet,
     plus any design questions and whether Will has answered them
   Commit the log to main in a tools-only pull request (no release), after the
   release is live.
10. Do not message Will when the release is live. Message him only for design
    questions or critical stops (see "Work quietly"). Follow "How to present
    information to Will" in CLAUDE.md: headings, labelled links, bullets, no tables.
11. If a day finds nothing worth releasing, do not publish. Add a line to the log and
    do not message Will.

## The six areas
1. Startup, main menu, sleep and wake, microphone and input modes, Options and
   Display options, Help and the tutorial, time and date, updates, speech transcript.
2. Email: Thunderbird and Yahoo, the inbox, reading, replying, forwarding, drafts,
   folders, message details keys, attachments by voice and keys.
3. Contacts and Calendar, choosing a calendar or address book, the Thunderbird
   add-on (`node voice_companion/thunderbird_mail_extension/test-bridge.cjs`).
4. Word processor, Save As, Undo and Redo, Notepad, Document reader, File manager,
   damaged, locked and empty files.
5. Web: the guided browser, quick keys, forms, dialogs, search, favorites, history,
   downloads, the regular browser extension (`node voice_companion/regular_browser_extension/test-worker.cjs`).
6. Media (radio, podcasts, the player, local media, What's playing), plus the
   low-vision window: real-window keys on every screen at large text and small
   screens, focus, and Escape paths.

Rotate the angle each day so the same code gets fresh eyes:
- day 1, a new veteran
- day 2, an expert screen reader user
- day 3, a code review of error handling
- day 4, messy speech input
- day 5, empty and edge states
- day 6, keyboard only
- day 7, low vision
