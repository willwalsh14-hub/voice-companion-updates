"""Run existing production browser tests through the installed native extension.

A temporary extension adds a fixture-only startup harness. The browser is
launched without a debugging port; the extension owns the selected tab. This
checks no real account, credentials, submission, or external website.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from browser_cdp_relay import BrowserRelay
from connected_browser import ConnectedBrowserBackend
import test_web_live
import test_consumer_web
import test_web_semantics


class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        body=b'<!doctype html><title>Destination</title><h1>Destination</h1>' if self.path=='/next' else b'<!doctype html><title>Connection fixture</title><h1>Connection fixture</h1>'
        self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(body)
    def log_message(self,*args):pass


def check(installed, runtime):
    from playwright.sync_api import sync_playwright
    import browser_cdp_relay
    os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(runtime)
    with sync_playwright() as driver: executable=driver.chromium.executable_path
    if not Path(executable).is_file():raise RuntimeError('The extension test browser is missing.')
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp)
        fixture=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
        Thread(target=fixture.serve_forever,daemon=True).start()
        relay=BrowserRelay(root/'app')
        browser_cdp_relay._RELAY=relay
        extension=root/'extension';shutil.copytree(Path(installed)/'regular_browser_extension',extension)
        url='http://127.0.0.1:'+str(fixture.server_port)+'/'
        # This file exists only in the temporary test copy, never the installer.
        (extension/'test-harness.js').write_text('chrome.tabs.create({url:'+json.dumps(url)+',active:true}).then(tab=>connectTab({...tab,url:'+json.dumps(url)+'}));',encoding='utf-8')
        with (extension/'worker.js').open('a',encoding='utf-8') as file:file.write('\nimportScripts("test-harness.js");\n')
        environment=dict(os.environ,VOICE_COMPANION_DATA_DIR=str(root/'app'))
        browser=subprocess.Popen([executable,'--headless=new','--no-sandbox','--no-first-run','--disable-default-apps',
            '--user-data-dir='+str(root/'profile'),'--disable-extensions-except='+str(extension),'--load-extension='+str(extension),
            'about:blank'],env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        backend=ConnectedBrowserBackend(root/'unused','chrome',relay)
        try:
            if not relay.connected.wait(30):raise RuntimeError('The real browser extension did not connect to the installed host.')
            from web_assistant import WebSession
            regular_session=WebSession(root/'app')
            assert regular_session.adopt_connected_browser(), 'The normal Web browser did not adopt the connected tab.'
            backend=regular_session.backend
            assert regular_session.menu.choices(regular_session,False)[0][1]=='resume'
            assert backend.regular and backend.context.pages, 'The standard backend did not adopt the extension tab.'
            context=backend.context
            class ExtensionSetup:
                @classmethod
                def setUpClass(cls):cls.persistent_context=context
                @classmethod
                def tearDownClass(cls):pass
            class ExtensionNavigation(ExtensionSetup,test_web_live.LiveWebTests):
                # A regular browser can start a popup request before debugger
                # attachment. Use a real local destination rather than relying
                # on interception of an otherwise nonexistent DNS name.
                def test_new_tab_result_is_loaded_before_virtual_view_capture(self):
                    self.page.set_content('<a target="_blank" href="'+url+'next">New tab result</a>')
                    self.session._refresh();self.session.keyboard('Quick:k');notice=self.session.keyboard('Activate')
                    self.addCleanup(self.backend.page.close)
                    self.assertIsNot(self.backend.page,self.page)
                    self.assertIn('Destination',self.session.reading.text);self.assertIn('Page loaded:',notice)
                def test_delayed_popup_after_early_dom_change_refreshes_once(self):
                    self.page.set_content('<p id="status">Ready</p><a href="'+url+'next" onclick="event.preventDefault();document.getElementById(\'status\').textContent=\'Loading\';setTimeout(()=>window.open(this.href),700)">Delayed result</a>')
                    self.session._refresh();self.session.keyboard('Quick:k');self.session.keyboard('Activate')
                    self.addCleanup(self.backend.page.close)
                    self.assertIsNot(self.backend.page,self.page);self.assertIn('Destination',self.session.reading.text)
                def test_leave_closes_all_owned_pages_and_keep_open_reuses_context(self):
                    # Dedicated-profile ownership is different for an existing
                    # user tab. Keep-open is checked here; disconnect/reconnect
                    # and preserving the selected tab are checked after the suite.
                    regular_session.leave(True)
                    self.assertIs(regular_session.backend.context,context)
                    self.assertFalse(self.page.is_closed())
            class ExtensionConsumer(ExtensionSetup,test_consumer_web.ConsumerWebTests):pass
            class ExtensionSemantics(ExtensionSetup,test_web_semantics.SemanticWebTests):pass
            suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls)
                                     for cls in (ExtensionNavigation,ExtensionConsumer,ExtensionSemantics))
            result=unittest.TextTestRunner(verbosity=2,failfast=True).run(suite)
            if not result.wasSuccessful():raise RuntimeError('Standard browser features failed through the real extension.')
            regular_session.leave(False)
            assert relay.connected.is_set(), 'Leaving the website must keep the selected extension connection.'
            assert regular_session.adopt_connected_browser(), 'The usual browser menu could not reconnect.'
            backend=regular_session.backend
            assert backend.context.pages, 'Leaving the website closed the user-selected browser tab.'
            # A dropped connection must never silently fall back to a controlled browser.
            browser.terminate();browser.wait(timeout=15)
            deadline=time.monotonic()+5
            while relay.connected.is_set() and time.monotonic()<deadline:time.sleep(.02)
            try:backend.start()
            except Exception:pass
            else:raise AssertionError('A disconnected extension was treated as a live browser.')
            print('INSTALLED EXTENSION PASSED: standard navigation, fields, tables, lists, forms, frames, popups and disconnect checks.')
        finally:
            backend.close();relay.close();fixture.shutdown();fixture.server_close()
            if browser.poll() is None:browser.kill();browser.wait()
            browser_cdp_relay._RELAY=None

if __name__=='__main__':check(Path(sys.argv[1]),Path(sys.argv[2]))
