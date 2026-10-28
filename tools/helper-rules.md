# Rules for every Voice Companion helper 

Voice Companion is a Windows voice-and-keyboard app for blind users. It is meant to compete with Dolphin GuideConnect. The owner, Will, is fully blind, an adaptive technology specialist, and very demanding about polish. Every spoken word matters.

## Ground rules
- **CLAUDE.md:** read it at the root of your worktree first, and follow it exactly. That means:
  - no tables
  - plain words at an 8th-grade reading level in anything users hear or read
  - a test that fails before each fix
  - atomic writes
  - never lose user data
  - `python3 tools/run_checks.py --compare main` must say "No new failures"
  - routing snapshot changes must be intended, and listed in your report with the reason
- **What not to do:**
  - Do NOT bump the version.
  - Do NOT edit release-notes.json.
  - Do NOT merge.
  - Do NOT open a pull request.
- **Commits:** commit in small steps and push after each one (`git push -u origin <your branch>`), so nothing is lost if the machine restarts. End each commit message with:

  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01LUwzrzXCWDR2utM1sQafPE

## Working alongside other helpers
Four other helpers work in parallel on other branches. To keep the merge easy:
- Keep edits to the shared files small and in place: companion.py, input_parity.py, screen_menus.py, task_navigation.py, the guide, and build-windows.ps1.
- Do not reformat or reorder code you are not changing.
- Do not regenerate tools/input-parity-inventory.md, tools/routing-snapshot.json or the guide HTML unless your own tests need it. I regenerate them after merging. Do update them if a test you run requires it.
- New test files go in the release test list in voice_companion/build-windows.ps1. Append them at the end of the list.

## Bug hunting standard
- Read the code yourself, carefully.
- For each real defect:
  1. Reproduce it with a test.
  2. Fix the cause.
  3. Keep the test.
- Look for these kinds of defects:
  - wrong or missing speech
  - silent failures
  - exceptions on empty or edge states
  - inconsistent wording between screens
  - keys that do nothing or do the wrong thing
  - voice commands that clash with dictation in writing fields
  - focus or Escape going to the wrong place
  - lost text
  - race conditions
  - unclear error messages
- Do not change deliberate design to make a test pass. Read the existing tests; they record intended behavior.
- Do not invent features beyond your brief.

## Final report
End with a report that lists:
- each bug found, with what the user would have noticed, and the fix
- design flaws you fixed
- issues you saw but did not fix, and why
- the routing snapshot changes, with the reason for each
- the test results
- anything Windows-only that is untested

Be honest. Keep the report plain, with no tables.
