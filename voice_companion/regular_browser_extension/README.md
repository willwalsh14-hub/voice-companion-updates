# Standard browser engine through a persistent regular browser link

SETUP.txt explains setup and use. The extension is a transport for the existing
BrowserBackend/WebSession engine, not a second accessibility implementation.
All page reading, element navigation, field editing, forms, tables, lists and
menus remain in the standard app code. The limited 0.2.137 connection menu is
removed. CDP messages use full duplex native messaging and an authenticated
Windows named pipe. A token-protected, origin-rejecting WebSocket bound only
to 127.0.0.1 connects the local Playwright driver to that pipe. No debugging
port is opened on the user's browser and no browser is launched by the app.

The extension links normal HTTP/HTTPS tabs after one-time setup. It follows
the active tab, remembers a paused connection, retries when the app starts,
and excludes browser settings pages. It tracks child debugger sessions for
cross-process frames. Browser cookie-storage APIs are not exposed. Browser-global operations outside the needed
connection protocol are rejected. Page messages cannot invoke the bridge.
Protocol contents and tokens are not logged. Private field handling uses the
existing standard browser protections. The app does not import a browser
profile, change browser identity or bypass site security.

Downloads (downloads.js) use the "downloads" permission. The extension tells
Companion the file name, address, progress and end of each Chrome download, so
Companion keeps one download list for every browser. Companion can ask Chrome to
pause, resume or cancel a download. No file contents and no data: addresses are
sent, and pages cannot send these requests.

The installer packages the native host and extension with a fixed origin.
The user enables the extension once. Existing
unpacked installations need Reload after this update. Disconnecting leaves
normal browser tabs open. A lost extension never silently falls back to a
controlled browser. Live PayPal sign-in remains unverified.

check_extension_browser.py launches a disposable browser without a debugging
port, adds a fixture-only startup harness to a temporary extension copy, then
runs the existing navigation, consumer and semantic suites through the real
installed native host. The test harness is never shipped in the extension.

Protocol design references:
https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging
https://developer.chrome.com/docs/extensions/reference/api/debugger
Playwright's installed browserModel/CDP relay source in its coreBundle.js.
