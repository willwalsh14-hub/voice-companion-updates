import sys, time, tempfile, traceback, urllib.request
from pathlib import Path
import media_player
URLS = ['https://pinecast.com/listen/206dbcfd-8752-410c-b720-8a2e3eaa0620.mp3?source=rss&ext=asset.mp3',
        'https://pinecast.com/listen/c1b3000b-51e9-43e8-a36d-6c7bd12a85a1.mp3?source=rss&ext=asset.mp3']
def note(title, text):
    print('::warning title=' + title[:60] + '::' + str(text).replace('%', '%25').replace('\n', '%0A')[:900], flush=True)
local = Path(tempfile.mkdtemp(prefix='Podcast Downloads ')) / 'Main Menu - episode one.mp3'
try:
    with urllib.request.urlopen(urllib.request.Request(URLS[0], headers={'User-Agent': 'Mozilla/5.0'}), timeout=60) as r:
        local.write_bytes(r.read(3_000_000))
    note('download', 'ok %d bytes' % local.stat().st_size)
except Exception as e:
    note('download', repr(e))
for headless in (True, False):
    folder = Path(tempfile.mkdtemp(prefix='Audio Player '))
    p = media_player.MediaPlayer(folder)
    if not headless:
        orig = p.start
    for url in URLS + [local.as_uri()]:
        t = time.monotonic()
        try:
            if not headless and p.page is None:
                pass
            p.open(url, 'Episode', 'podcast')
            time.sleep(3)
            st = p.status()
            note('headless=%s %s' % (headless, url[-30:]), 'OK in %.1fs status=%r' % (time.monotonic() - t, st))
        except Exception as e:
            note('headless=%s %s' % (headless, url[-30:]), 'FAIL in %.1fs %r last=%r' % (time.monotonic() - t, e, getattr(p, 'last_start_error', '')) + ' ' + traceback.format_exc()[-500:])
    p.close()
    break
