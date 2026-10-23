# Browser performance on busy pages (WebView2 and others)

Will reported lag on YouTube in embedded Edge (WebView2), slow ad skipping, and
a moment where the app seemed about to crash. This file records what was
measured, what caused it, and what changed. Branch `claude/webview-performance`.

## How to measure

```
PYTHONPATH=tools/linux-test-stubs python3 tools/webview_benchmark.py              # this checkout
PYTHONPATH=tools/linux-test-stubs python3 tools/webview_benchmark.py --source DIR # another checkout
```

The benchmark page mimics a YouTube watch page: 12,800 nodes (1,600 comments,
two buttons each), a progress bar whose style and `aria-valuenow` change every
100 ms, a clock that changes every second, an ad every 6 seconds with an ad
iframe and a Skip ad button inside a shadow root, 20 more comments every 3
seconds, and a fetch every 0.5 seconds that stays open 2.5 seconds (video
segments). It drives the real `WebSession` code, polling `refresh_changed()`
the way `companion.py`'s main loop does.

It runs in Playwright's Chromium. WebView2 is Chromium (Blink and V8), so the
page-side numbers apply to it directly; the WebView2 host adds its own cost on
top (see "WebView2 host" below).

## Results (Linux container, headless Chromium)

Each measurement has a heading, with the result on `main` (0.2.157-test) and on this branch.

### First page capture (`_refresh`)

- main: **failed after 55 s**: "The page is still changing"
- This branch: 0.7 s

### One page snapshot

- main: 7.2 s
- This branch: 0.36 s

### One change check (`document_stamp`)

- main: 3.8 ms
- This branch: 2.4 ms

### `wait_ready` on the playing page

- main: 4.0 s (always hit its timeout)
- This branch: 0.2 s

### 12 s of main-loop polling

- main: one check that blocked the main loop for **59 s** and failed
- This branch: 7 checks, 25% of the time busy, worst 0.5 s

### Skip ad button found

- main: no (page never captured)
- This branch: yes

### Press Skip ad

- main: not reachable; on a smaller page a self-removing button took 10 s+
- This branch: 1.1 s total, says "Ad skipped."

A blocked main loop means keys, voice commands and speech all wait. A
minute-long freeze is very likely the "about to crash" moment.

### Where the 7.2 s snapshot went (CPU profile, one snapshot)

#### Cause 1: Trimming spaces from the whole page text on every line

- What: `newline()` ran `output.replace(/ +$/,'')` on the whole growing page text for every line (quadratic)
- Cost: 2.7 s + 0.4 s
- Fix: trailing spaces are held in a counter; no regular expression over the text

#### Cause 2: Looking up field labels one at a time

- What: `element.labels`: Blink searches the whole document once per button or field (3,200 buttons)
- Cost: 0.97 s
- Fix: labels resolved from one pass per snapshot (`withCachedLabels`, page definitions restored afterwards)

#### Cause 3: Passing the snapshot to Python value by value

- What: Playwright converting a 2.4 MB object value by value
- Cost: about 1.3 s
- Fix: the page returns one JSON string (`JSON.stringify`), parsed in Python

After the first two fixes a snapshot took 2.1 s; after the third, 0.36 to 0.6 s
(about 0.26 s of it is page-side work).

## Other causes found and fixed

1. **Every attribute change counted as a page change.** The progress bar's
   style changed ten times a second, so the page never looked settled:
   `wait_ready` always waited its full 4 s, and every 0.3 s poll did a full
   snapshot. Now position, size, opacity, transform and animation style changes
   are ignored (display and visibility changes still count). Slider values
   (`aria-valuenow`, `aria-valuetext`) still count as changes but no longer keep
   the page "unsettled".
2. **Ad frames failed the capture.** `_capture` treated a frame that appeared
   or reloaded during the snapshot like a page navigation: it ran `wait_ready`
   (4 s) and retried up to five times, then raised "still changing". Frames
   that come and go now get two short retries, then the view is kept; the
   stored state makes the next check read the frames again.
3. **Polling had no limit on its share of the main loop.** `RefreshPacer`
   (web_assistant.py, used in companion.py) keeps the 0.3 s rhythm when checks
   are cheap. After a slow read it waits three times the read's cost (at most
   2 s), and after a key press it waits twice the cost before reading again.
4. **Memory grew without limit on pages with frames.** `document_stamp` and
   `snapshot` called `frame.frame_element()` for each frame and never released
   the handle: one leaked element per frame per 0.3 s poll, held by the page
   and the driver (200 driver objects per 100 checks with two frames; now 0).
5. **A button that removes itself took over 10 s.** After the click,
   `activate` asked the driver to evaluate the (now removed) button for form
   validation and waited the full default timeout. Skip ad, Close and Accept
   cookies buttons all do this. Now it checks the button still exists first.
6. **Changes inside shadow roots were invisible** to the change counter. The
   snapshot now asks the observer to watch each open shadow root it reads.

## Ad skipping

- When a Skip ad button appears (label "Skip ad", "Skip ads", "Skip the ad",
  or "Skip" on youtube.com), the next check says once: "Skip ad button ready.
  Say skip ad to press it."
- Saying "skip ad" (any punctuation or capitals) presses it without moving the
  reading place and says "Ad skipped." Not while writing in a field.
- Enter on the Skip ad button does the same, instead of "Page loaded" and
  reading the page from the top.

## WebView2 host (`webview2_host.cs`) — not measurable on Linux

There is no .NET compiler here, so these are built and tested only by the
Windows CI ("The embedded Edge host" build step and "Embedded Edge voice and
keyboard browser parity checks", which now also run `EmbeddedPerformanceTests`).

1. **Every CDP event and result was parsed and re-serialized with
   `JavaScriptSerializer` on the window's UI thread.** That includes every
   network event (YouTube sends many per second) and every 2.4 MB snapshot
   result. The host now passes the JSON text through unchanged (rewriting only
   text that contains a raw line break, which would break the line protocol).
2. **`AddWebResourceRequestedFilter("*", All)` was always on.** WebView2 then
   holds every request (each video segment, image and script) until the UI
   thread runs the handler. The filter is now added only while the automation
   driver turns the cache off (`Network.setCacheDisabled`), which Playwright
   does only when request routing is used (tests); the app does not route.
3. The Python relay (`webview2_backend.py`) forwards host lines as they are
   instead of encoding them again (about 20 ms per large snapshot).

## Not changed, noted for later

- `activate` waits up to 3 s for page requests started after the click when
  the control is a link, a key press or a form button. Video sites start
  requests all the time, so those can take the full 3 s there. A fix needs to
  know which requests a click caused; Playwright does not expose the initiator.
  Ordinary buttons outside forms are not affected (they stop after 0.35 s).
- A shadow root attached later to an existing element, with no other change in
  the light DOM, is seen at the next snapshot, not by the change counter.
- The host's 35-second watchdog still decides when a hung embedded browser is
  given up on. With these fixes no normal call comes close to that.

## App mode and browse mode (branch `claude/guided-browsers`)

Will reported: "App mode vs browse mode is very very laggy." Measured with
`tools/webview_benchmark.py` (its app mode section), headless Chromium, before
and after this branch.

### What ran on each switch and key

- **Switching to browse mode read the whole page again** (a full snapshot,
  about 0.4 s on the busy page) before "Browse mode" could be spoken. The
  element with focus is now found in the page view Companion already has. The
  view catches up at the main loop's next check, which runs at once
  (`refresh_now`).
- **Switching to app mode moved focus to the browse cursor's element first.**
  That now happens with the first key, so the switch itself does no page work.
- **Each key sent to the page waited for an answer**: up to six 25 ms waits,
  each followed by a page read, so a key the page answers silently (K on a
  video) took about 0.2 s. A key now costs two quick page reads and the key
  press. A late answer from the page is said at the next check.
- **In app mode the main loop kept reading the whole page** whenever it
  changed (a video clock changes every second). Each read held up the next key
  for 0.4 to 1.1 s. In app mode the check now asks only for focus changes and
  page announcements; a new page is still read and announced.
- **After every key the window was refocused and given the page text again**
  (Windows: `SetForegroundWindow`, `focus_force`, comparing the whole page
  text). Page keys do not move keyboard focus, so this now happens only after
  other keys or when a page key opened another page or tab.

- **Keys typed or held faster than they were handled each waited for page
  reads.** Keys that arrive together now go to the page one after another,
  and the page is read once, after the last. A key right after another uses
  the state read after that key, so it needs one page read, not two.
- **Every page read sent the whole 40 KB focus script** (in embedded Edge it
  also went through the host twice). The script is placed in the page once
  and then called by name.

### Results (busy page, milliseconds, median unless noted)

Before is this branch before the app mode work; after is the final branch.

- Switch to app mode: 57 before, 0 after.
- Switch back to browse mode: 424 before, 3 after.
- K key (play or pause): 212 before, 6 after.
- J key: 208 before, 8 after.
- L key: 221 before, 7 after.
- Down Arrow: 220 before, 9 after.
- Tab (focus moves): 19 before, 11 after.
- Right Arrow on the seek slider: 208 before, 7 after.
- Typing a letter in the search box: 20 before, 11 after.
- Twenty keys arriving together: 433 in all before (21.7 a key), 118 after
  (5.9 a key).
- Main loop check in app mode: 499 before (worst 1,056), 13 after (worst 28);
  page reads in 10 checks: 12 before, 0 after.

Tests that keep these bounds: `test_web_modes_live.AppModeSpeedTests` (no page
read or wait on a switch, one key press and at most two page reads per key,
one read for a key right after another, a short call instead of the script,
no reads between keys that arrive together, late answers said by the next
check) and `test_web_modes.AppModeWindowTests`
(no window refocus after page keys; queued page keys are sent together).
