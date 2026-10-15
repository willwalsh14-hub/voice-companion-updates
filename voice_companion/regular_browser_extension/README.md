# Experimental regular-browser connection

This is a development prototype, not a released replacement for the guided
browser. PayPal sign-in has **not** been verified with this connection.

The user opens an ordinary Chrome or Edge tab and chooses Connect this tab in
the extension. The extension attaches through the supported debugger API. It
does not launch an automation browser, change browser identity, import cookies,
or change CAPTCHA/security settings. Chrome displays its debugger notice.

The prototype reads the browser's accessibility tree, speaks through a local
Windows SAPI host, moves among controls, and sends one native key sequence to
activate a control. It rereads the accessibility tree after activation. Radio
buttons use Space. There are no synthetic JavaScript clicks or forced checked
properties. Browser security rules still apply, and sites can still deny access.

## Current limits

- Not connected to the main Voice Companion window, voice commands or settings.
- The popup must remain open; speech currently finishes before another request.
- Only the selected tab and its current accessibility tree are read. Full
  cross-process frame traversal, new tabs, navigation and focus recovery still
  need integration tests.
- The native host is a separate development executable. It is not registered by
  the released installer. Do not add this prototype to a production installer.
- Browser-level tests are still required on Windows, including real website
  sign-in, normal typing, radio groups, permission denial and disconnects.

## Development setup on a Windows test machine

1. Build `browser_bridge_host.py` as a console-mode, one-file executable with
   PyInstaller and pywin32. Do not use windowed mode: native messaging requires
   binary stdin/stdout.
2. Load this folder as an unpacked extension in a dedicated **regular** test
   browser profile. Record the extension ID.
3. Create a native-host manifest named `com.voicecompanion.browser.json` with
   `name` set to `com.voicecompanion.browser`, `type` set to `stdio`, `path` set
   to the full host executable path, and `allowed_origins` containing only
   `chrome-extension://THE_RECORDED_EXTENSION_ID/`. Never use wildcards.
4. Register that manifest path as the default value of the per-user native-host
   registry key under Chrome or Edge, following the browser's official native
   messaging instructions.
5. Open a local test page, open the extension, and connect. Verify page reading,
   radio selection and spoken state before attempting any account sign-in.

Uninstall the test registry key and extension after testing. The host does not
write logs, page contents, field values or sign-in data. Messages remain local;
no TCP listener or external service is used. The extension does not request
cookie, history, all-sites or externally-connectable permissions. Only its own
popup can request actions. It strips field values before sending speech data.

## Design references

- https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging
- https://developer.chrome.com/docs/extensions/reference/api/debugger
- https://developer.chrome.com/blog/remote-debugging-port
- https://www.w3.org/WAI/ARIA/apg/patterns/radio/

An extension connection is a compatibility candidate, not proof of why a site
blocked the existing backend or a guarantee that the site will permit access.
