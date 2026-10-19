"""Read-only public website checks through the real Companion browser adapter."""
import argparse
import json
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
from web_assistant import BrowserBackend, WebSession

SITES = ('https://example.org/', 'https://www.w3.org/WAI/', 'https://fullcircleat.com/')


def audit():
    results = []
    with tempfile.TemporaryDirectory() as folder, sync_playwright() as driver:
        browser = driver.firefox.launch(headless=True)
        try:
            for url in SITES:
                entry = {'url': url, 'status': 'blocked', 'checks': []}
                context = browser.new_context()
                try:
                    page = context.new_page()
                    page.set_default_timeout(8000)
                    response = page.goto(url, timeout=25000, wait_until='domcontentloaded')
                    if response is not None and response.status >= 400:
                        entry['reason'] = 'HTTP ' + str(response.status)
                        continue
                    entry['status'] = 'failed'
                    backend = BrowserBackend(Path(folder)/'profile')
                    backend.page = page
                    backend.context = context
                    backend.start = lambda: None
                    session = WebSession(folder, backend)
                    session.auto_read = False
                    session._refresh()
                    snapshot = session.snapshot
                    if snapshot.get('challenge') or not snapshot.get('text', '').strip():
                        entry['status'] = 'blocked'
                        entry['reason'] = 'Verification or no readable content'
                        continue
                    entry['title'] = snapshot.get('title')
                    entry['status'] = 'passed'
                    for key in ('PageTop', 'Quick:h', 'Quick:k', 'Read:Down', 'Read:Up', 'PageBottom'):
                        feedback = session.keyboard(key)
                        valid = (0 <= session.reading.position <= len(session.reading.text)
                                 and session.reading.position == session.browse.position)
                        entry['checks'].append({'action': key, 'passed': valid, 'feedback': feedback[:160]})
                        if not valid:
                            entry['status'] = 'failed'
                except Exception as exc:
                    # Infrastructure/website failures stay distinct from failed app assertions.
                    entry['reason'] = type(exc).__name__ + ': ' + str(exc)[:500]
                finally:
                    context.close()
                    results.append(entry)
        finally:
            browser.close()
    return {'kind': 'Read-only public-site Companion audit', 'sites': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report = audit()
    Path(args.output).write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    raise SystemExit(1 if any(s['status'] == 'failed' for s in report['sites'])
                     or not any(s['status'] == 'passed' for s in report['sites']) else 0)
