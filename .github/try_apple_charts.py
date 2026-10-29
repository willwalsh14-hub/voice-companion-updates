"""Try branch only: Apple's real podcast charts, read the way Companion reads them."""
import sys, tempfile
sys.path.insert(0, '.')
import media_hub
hub = media_hub.MediaHub(tempfile.mkdtemp(), player=object())
for genre in (1303, 1488, 1305, None):
    try:
        shows = hub._chart(genre)
        print('CHART', genre, len(shows), [s['name'] for s in shows[:3]])
    except Exception as exc:
        print('CHART', genre, 'FAILED', repr(exc))
for name in ('Comedy', 'Kids and family', 'Top podcasts'):
    print('SAID', hub.browse_category(name)[:160])
for url in (media_hub.APPLE_CHART.format(limit=2, genre='/genre=1487'), media_hub.APPLE_TOP_CHART.format(limit=2)):
    try:
        print('RAW', url, str(media_hub._json(url))[:500])
    except Exception as exc:
        print('RAW', url, 'FAILED', repr(exc))
