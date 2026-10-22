"""Measure Voice Companion's browser work on a busy, YouTube-like page.

Usage (from the repository root, Linux or Windows):
  python3 tools/webview_benchmark.py            # Playwright Chromium, headless
  python3 tools/webview_benchmark.py --seconds 20

The page has more than 10,000 nodes, a progress bar that changes every
100 ms, a clock that changes every second, an ad (with a Skip ad button in a
shadow root and an ad iframe) that comes and goes, lazy-loaded comments and
streaming requests that never finish quickly, like video segments.

It drives the same WebSession code the app uses, polling refresh_changed()
every 0.3 seconds as the main loop does, and prints how long the app's
main thread was busy. Results are recorded in tools/webview-performance.md.
WebView2 itself is Windows-only; Chromium runs the same page scripts and the
same CDP calls, so the numbers show the app-side cost.
"""
import argparse
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

COMMENTS = 1600  # about 8 nodes each: well over 10,000 nodes

PAGE = r'''<!doctype html><html><head><title>Busy video - VideoSite</title>
<style>.bar{height:4px;background:#c00;width:0}.hidden{display:none}</style></head><body>
<div id="masthead"><a href="/">Home</a><input aria-label="Search" role="combobox"><button>Search</button></div>
<main><div id="player" role="region" aria-label="Video player">
 <video></video>
 <div id="ad-area"></div>
 <div class="progress" role="slider" aria-label="Seek slider" aria-valuemin="0" aria-valuemax="600" aria-valuenow="0"><div class="bar" id="bar"></div><div class="bar" id="buffer"></div></div>
 <span id="time">0:00</span> / <span>10:00</span>
 <button aria-label="Pause">Pause</button><button aria-label="Mute">Mute</button>
</div>
<h1>Busy video title</h1><p id="views">1,234 views</p>
<button id="like">Like</button><button>Share</button>
<h2>Comments</h2><div id="comments"></div></main>
<script>
const comments=document.getElementById('comments');let made=0;
function addComments(n){const f=document.createDocumentFragment();for(let i=0;i<n;i++,made++){const d=document.createElement('div');d.className='comment';
 d.innerHTML='<div class="head"><a href="/user'+made+'">User '+made+'</a> <span>2 days ago</span></div><p>Comment number '+made+' says this video is good.</p><div class="tools"><button>Like comment</button><button>Reply</button></div>';f.appendChild(d);}comments.appendChild(f);}
addComments(COMMENTS);
let t=0;const bar=document.getElementById('bar'),buf=document.getElementById('buffer'),slider=document.querySelector('.progress'),clock=document.getElementById('time');
setInterval(()=>{t+=0.1;bar.style.width=(t/6)+'%';buf.style.width=Math.min(100,t/3)+'%';slider.setAttribute('aria-valuenow',String(Math.floor(t)));},100);
setInterval(()=>{const s=Math.floor(t);clock.textContent=Math.floor(s/60)+':'+String(s%60).padStart(2,'0');},1000);
// An ad comes every 6 seconds and stays 4. Its Skip button arrives after 1 second, in a shadow root.
window.skips=0;
function showAd(){const area=document.getElementById('ad-area');area.innerHTML='<div class="ad-overlay"><span>Ad 1 of 1</span><iframe src="https://ads.bench.test/ad" title="Advertisement"></iframe><div id="skip-host"></div></div>';
 setTimeout(()=>{const host=document.getElementById('skip-host');if(!host)return;const root=host.attachShadow({mode:'open'});root.innerHTML='<button class="ytp-skip-ad-button">Skip ad</button>';root.querySelector('button').onclick=()=>{window.skips++;area.innerHTML='';};},1000);
 setTimeout(()=>{area.innerHTML='';},4000);}
setInterval(showAd,6000);
// Lazy comments every 3 seconds, up to 400 more, so long runs stay comparable.
setInterval(()=>{if(made<COMMENTS+400)addComments(20);},3000);
// Streaming requests that stay open for 2.5 seconds, like video segments and stats pings.
setInterval(()=>{fetch('/segment?'+Math.random()).catch(()=>{});},500);
</script></body></html>'''.replace('COMMENTS', str(COMMENTS))


class Counter:
    def __init__(self, backend):
        self.snapshots = 0; self.stamps = 0
        snapshot, stamp = backend.snapshot, backend.document_stamp
        def counted_snapshot(*a, **k):
            self.snapshots += 1; return snapshot(*a, **k)
        def counted_stamp(*a, **k):
            self.stamps += 1; return stamp(*a, **k)
        backend.snapshot = counted_snapshot; backend.document_stamp = counted_stamp


def timed(action):
    started = time.perf_counter(); result = action(); return time.perf_counter() - started, result


def attempt(label, action):
    """Run one measurement; a failure is a result too (main raised on this page)."""
    started = time.perf_counter()
    try:
        return timed(action)
    except Exception as exc:
        print(f'{label}: FAILED after {(time.perf_counter()-started)*1000:.0f} ms ({type(exc).__name__}: {str(exc)[:80]})'); return None, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=float, default=12)
    parser.add_argument('--source', default=str(ROOT), help='repository folder whose voice_companion code is measured')
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.source) / 'voice_companion'))
    from playwright.sync_api import sync_playwright
    from web_assistant import BrowserBackend, WebSession
    driver = sync_playwright().start()
    browser = driver.chromium.launch(headless=True)
    context = browser.new_context()
    # Segment requests stay open for 2.5 seconds; the page sees them as pending fetches.
    pending = []
    def route(request_route):
        if '/segment' in request_route.request.url:
            pending.append((time.monotonic() + 2.5, request_route)); return
        request_route.fulfill(content_type='text/html', body=PAGE)
    context.route('https://bench.test/**', route)
    context.route('https://ads.bench.test/**', lambda r: r.fulfill(content_type='text/html', body='<p>Buy things now</p><script>let n=0;setInterval(()=>{document.querySelector("p").textContent="Buy things now "+(n++)},200)</script>'))
    page = context.new_page(); page.set_default_timeout(10000)

    def release():
        now = time.monotonic()
        for item in [p for p in pending if p[0] <= now]:
            pending.remove(item)
            try: item[1].fulfill(body='ok')
            except Exception: pass
    backend = BrowserBackend(tempfile.gettempdir()); backend.page = page; backend.context = context; backend.start = lambda: None
    folder = tempfile.TemporaryDirectory()
    session = WebSession(folder.name, backend); session.auto_read = False
    counter = Counter(backend)
    page.goto('https://bench.test/watch', wait_until='domcontentloaded')
    print(f"Source: {args.source}")
    print(f"Nodes: {page.evaluate('document.querySelectorAll(\"*\").length')}")
    load, _ = attempt('First page capture', lambda: session._refresh(read=False))
    if load is not None: print(f'First page capture (wait_ready + snapshot): {load*1000:.0f} ms, snapshots {counter.snapshots}')
    snap_times = [timed(backend.snapshot)[0] for _ in range(3)]
    print(f'One snapshot: median {statistics.median(snap_times)*1000:.0f} ms')
    stamp_times = [timed(backend.document_stamp)[0] for _ in range(5)]
    print(f'One change check (document_stamp): median {statistics.median(stamp_times)*1000:.1f} ms')
    wait_ready, _ = timed(backend.wait_ready)
    print(f'wait_ready on the playing page: {wait_ready*1000:.0f} ms')
    if not session.snapshot:
        # main could not capture the page at all; give polling a starting point.
        session.snapshot = backend.snapshot(); session.document_state = backend.document_stamp()
        from web_browse import BrowseCursor
        session.reading.set_text(session.snapshot.get('text', ''), reset=True); session.browse = BrowseCursor(session.snapshot)

    # Main loop: check for changes as companion.py does: every 0.3 s on main,
    # paced by RefreshPacer where it exists.
    import web_assistant
    pacer = web_assistant.RefreshPacer() if hasattr(web_assistant, 'RefreshPacer') else None
    counter.snapshots = counter.stamps = 0
    busy = 0.0; polls = 0; worst = 0.0; end = time.monotonic() + args.seconds; errors = 0; last = 0.0
    while time.monotonic() < end:
        release()
        now = time.monotonic()
        if pacer.due(now) if pacer else now - last >= .3:
            last = started = time.monotonic()
            try: session.refresh_changed()
            except Exception: errors += 1
            finished = time.monotonic(); spent = finished - started
            if pacer: pacer.done(started, finished)
            busy += spent; polls += 1; worst = max(worst, spent)
        page.wait_for_timeout(20)
    print(f'Polling {args.seconds:.0f} s: {polls} checks, {counter.snapshots} snapshots, main thread busy {busy*1000:.0f} ms '
          f'({busy/args.seconds*100:.0f}% of the time), worst check {worst*1000:.0f} ms, failed checks {errors}')

    keys = []
    for _ in range(10):
        release(); keys.append(timed(lambda: session.keyboard('Quick:b'))[0])
    print(f'Next button key (B): median {statistics.median(keys)*1000:.1f} ms')
    # Wait for the ad's Skip button (in a shadow root, after one second) and press it.
    for _ in range(80):
        release()
        if page.evaluate("!!document.getElementById('skip-host')?.shadowRoot"): break
        page.wait_for_timeout(100)
    attempt('Refresh with ad', lambda: session._refresh(read=False, wait=False))
    skip = next((e for e in (session.browse.elements if session.browse else []) if e.get('label') == 'Skip ad'), None)
    print(f'Skip ad reachable in browse elements: {bool(skip)}')
    if skip:
        session.browse.index = session.browse.elements.index(skip)
        counter.snapshots = 0
        activate = backend.activate; parts = {}
        def measured(*a, **k):
            started = time.perf_counter()
            try: return activate(*a, **k)
            finally: parts['activate'] = time.perf_counter() - started
        backend.activate = measured
        spent, result = attempt('Press Skip ad', lambda: session.keyboard('Activate'))
        if spent is not None:
            print(f"Press Skip ad: {spent*1000:.0f} ms (click and wait {parts.get('activate', 0)*1000:.0f} ms), "
                  f"snapshots {counter.snapshots}, skips {page.evaluate('window.skips')}, said: {str(result)[:70]!r}")
    for _, request_route in pending:
        try: request_route.abort()
        except Exception: pass
    context.close(); browser.close(); driver.stop(); folder.cleanup()


if __name__ == '__main__':
    main()
