"""Use the complete existing browser backend with an extension-owned tab."""
from web_assistant import BrowserBackend, WebError


class ConnectedBrowserBackend(BrowserBackend):
    regular = True
    def __init__(self, folder, browser, relay):
        super().__init__(folder,relay.browser)
        self.relay = relay
        self.remote_browser = None
        self.target_ids = {}
        self.root_session = None

    def start(self):
        if not self.relay.connected.is_set():
            raise WebError('The browser extension disconnected. Reconnect the tab, then return to the Web browser menu. Your tab stays open.')
        if self.page is not None and not self.page.is_closed():return
        self.close()
        from playwright.sync_api import sync_playwright
        try:
            self.playwright = sync_playwright().start()
            self.remote_browser = self.playwright.chromium.connect_over_cdp(self.relay.endpoint,timeout=15000,no_defaults=True)
            self.context = self.remote_browser.contexts[0]
            pages = self.context.pages
            if not pages:raise WebError('No connected browser tab is available. Reconnect the tab.')
            self.page = pages[0]
            self.page.set_default_timeout(10000)
            self.root_session=self.remote_browser.new_browser_cdp_session()
            self.sync_active_tab()
        except Exception as exc:
            self.close()
            raise WebError('The regular browser connection could not open. Reconnect the tab, then open the Web browser menu again.') from exc

    def sync_active_tab(self):
        if not self.root_session or not self.context:return False
        target=self.root_session.send('VoiceCompanion.getActiveTarget').get('targetId')
        if not target:return False
        for page in self.context.pages:
            if page not in self.target_ids:
                session=self.context.new_cdp_session(page)
                try:self.target_ids[page]=session.send('Target.getTargetInfo')['targetInfo']['targetId']
                finally:session.detach()
            if self.target_ids[page]==target:
                changed=self.page is not page
                self.page=page;self.page.set_default_timeout(10000)
                return changed
        return False

    def close(self):
        self.root_session=None;self.target_ids.clear()
        remote,driver = self.remote_browser,self.playwright
        self.page = self.context = self.playwright = self.remote_browser = None
        # Closing a CDP connection disconnects the driver; it must not close
        # the user's browser window or unrelated tabs.
        for action in (remote.close if remote else None,driver.stop if driver else None):
            if action:
                try:action()
                except Exception:pass
        if remote is not None:self.relay.client_closed.wait(2)


def create_backend(folder,browser):
    from browser_cdp_relay import current
    relay = current()
    return ConnectedBrowserBackend(folder,browser,relay) if relay is not None else BrowserBackend(folder,browser)
