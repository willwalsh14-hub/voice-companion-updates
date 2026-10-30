import json, time
from playwright.sync_api import sync_playwright
import youtube_page as yp
def note(title, text):
    print('::warning title=' + title[:70] + '::' + str(text).replace('%', '%25').replace('\r', '').replace('\n', '%0A')[:1500], flush=True)
URLS = ['https://www.youtube.com/results?search_query=cats', 'https://www.youtube.com/results?search_query=cats&sp=EgIQAg%3D%3D',
        'https://www.youtube.com/', 'https://www.youtube.com/@NASA/videos', 'https://www.youtube.com/playlist?list=PL590L5WQmH8fJ54F369BLDSqIwcs-TCfs',
        'https://www.youtube.com/feed/history', 'https://www.youtube.com/feed/channels']
import traceback
try:
  with sync_playwright() as p:
      b = p.chromium.launch(channel='chrome', headless=False, args=['--window-position=-32000,-32000'])
      ctx = b.new_context(locale='en-US')
      page = ctx.new_page()
      for url in URLS:
          try:
              page.goto(url, wait_until='domcontentloaded', timeout=45000)
              yt = yp.YouTubePage(lambda: page)
              yt.wait_ready(15000)
              items, facts = yt.read_list()
              summary = 'source=%s signedIn=%r n=%d token=%s promo=%r first=%s' % (facts.get('source'), facts.get('signedIn'), len(items),
                  bool(facts.get('token')), (facts.get('promo') or '')[:80], ' | '.join(yp.spoken_item(i)[:90] + ' [' + i['kind'] + ']' for i in items[:4]))
              total = len(items); tok = facts.get('token')
              for n in range(3):
                  if not tok: break
                  more, tok = yt.next_part(tok, '/results' in url)
                  total += len(more)
                  summary += '%%0Apart %d: +%d (%s)' % (n + 2, len(more), ' | '.join(i['title'][:40] for i in more[:2]))
              summary += '%0Atotal=' + str(total)
              if not items:
                  keys = page.evaluate("()=>{const d=window.ytInitialData;if(!d)return 'no data';const seen=new Set();const w=(o,depth)=>{if(!o||typeof o!=='object'||depth>14)return;if(Array.isArray(o)){o.slice(0,3).forEach(x=>w(x,depth+1));return;}for(const k in o){if(/Renderer$|ViewModel$/.test(k))seen.add(k);w(o[k],depth+1);}};w(d.contents,0);return [...seen].join(',')}")
                  summary += '%0Akeys=' + str(keys)[:600]
              note(url[24:], summary)
          except Exception as e:
              note(url[24:], 'ERROR ' + repr(e)[:400])
      b.close()

except Exception:
    note('crash', traceback.format_exc())
