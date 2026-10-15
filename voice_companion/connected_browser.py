"""Use the complete existing browser backend with an extension-owned tab."""
from web_assistant import BrowserBackend, WebError


class ConnectedBrowserBackend(BrowserBackend):
    regular = True
    def __init__(self, folder, browser, relay):
        super().__init__(folder,relay.browser)
        self.relay = relay
        self.remote_browser = None

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
        except Exception as exc:
            self.close()
            raise WebError('The regular browser connection could not open. Reconnect the tab, then open the Web browser menu again.') from exc

    def close(self):
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
