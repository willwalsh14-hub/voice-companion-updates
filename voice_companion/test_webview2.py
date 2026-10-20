"""Run shared real-page semantics and consumer tasks against the actual owned WebView2."""
from pathlib import Path
import os,tempfile,unittest,time
from webview2_backend import WebView2Backend
from test_web_live import LiveWebTests
from test_consumer_web import ConsumerWebTests
from test_web_semantics import SemanticWebTests


class WebViewFixture:
    @classmethod
    def setUpClass(cls):
        host=Path(os.getenv('VOICE_COMPANION_WEBVIEW2_HOST',str(Path(__file__).parent/'webview2'/'VoiceCompanion-WebView2.exe')))
        if os.name!='nt' or not host.is_file():
            if os.getenv('VOICE_COMPANION_REQUIRE_WEBVIEW2'):raise RuntimeError('Build the WebView2 host before testing.')
            raise unittest.SkipTest('Windows WebView2 host is not built.')
        cls.temp_profile=tempfile.TemporaryDirectory(prefix='Companion-WebView2-',ignore_cleanup_errors=True)
        cls.adapter=WebView2Backend(cls.temp_profile.name,True,host.resolve())
        cls.adapter.start();cls.driver=cls.adapter.playwright;cls.browser=cls.adapter.remote_browser;cls.persistent_context=cls.adapter.context

    @classmethod
    def tearDownClass(cls):
        cls.adapter.close();cls.temp_profile.cleanup()


class EmbeddedLiveTests(WebViewFixture,LiveWebTests):
    def test_private_browser_transport_rejects_wrong_token_and_web_origins(self):
        from websockets.sync.client import connect
        from websockets.exceptions import InvalidStatus
        endpoint=self.adapter.host.endpoint
        for address,origin in ((endpoint.rsplit('/',1)[0]+'/wrong-token',None),(endpoint,'https://fixture.test')):
            with self.subTest(origin=origin),self.assertRaises(InvalidStatus) as failure:
                with connect(address,origin=origin,open_timeout=3):pass
            self.assertEqual(failure.exception.response.status_code,403)

    def test_native_tab_selection_tracks_the_page_used_by_voice(self):
        page=self.context.new_page()
        try:
            page.goto('https://fixture.test/next');page.bring_to_front()
            self.assertTrue(self.adapter.sync_active_tab())
            self.assertIs(self.adapter.page,page)
            self.page.bring_to_front();self.adapter.sync_active_tab()
            self.assertIs(self.adapter.page,self.page)
        finally:page.close()

    def test_z_closing_first_native_tab_preserves_other_tabs(self):
        initial=next(page for page in self.context.pages if page is not self.page)
        initial.close()
        another=self.context.new_page()
        try:
            another.goto('https://fixture.test/next')
            self.backend.page=another
            self.assertIn('Destination',self.backend.snapshot()['text'])
            self.page.bring_to_front()
            self.backend.page=self.page
            self.assertIn('Fixture heading',self.backend.snapshot()['text'])
        finally:another.close()

    def test_leave_closes_all_owned_pages_and_keep_open_reuses_context(self):
        from web_assistant import WebSession
        profile=tempfile.TemporaryDirectory(prefix='Companion-WebView2-lifecycle-',ignore_cleanup_errors=True);self.addCleanup(profile.cleanup)
        owned=WebView2Backend(profile.name,True,self.adapter.executable,driver=self.driver);self.addCleanup(owned.close)
        owned.start();owned.context.route('https://fixture.test/**',lambda route:route.fulfill(content_type='text/html',body='<title>Kept page</title><h1>Kept page</h1>'))
        session=WebSession(self.temp.name,owned);session.open('https://fixture.test');second=owned.context.new_page();session.leave(True)
        self.assertIn('Kept page',session.resume());self.assertFalse(second.is_closed());pages=list(owned.context.pages);session.leave(False)
        self.assertTrue(all(page.is_closed() for page in pages));self.assertIsNone(owned.context)
class EmbeddedConsumerTests(WebViewFixture,ConsumerWebTests):pass
class EmbeddedSemanticTests(WebViewFixture,SemanticWebTests):pass


if __name__=='__main__':unittest.main()
