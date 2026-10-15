# Integrated regular-browser connection (testing)

See SETUP.txt for installation, commands and current limits. The Windows
installer includes a console-mode native host and registers its exact extension
origin for Chrome and Edge under HKCU. The browser still requires the user to
load/enable the extension; this package does not silently install an extension.

The service worker retains a native-messaging port after the popup closes.
The host passes JSON over a local authenticated Windows named pipe to the
running Companion app. Page data stays in memory, with no TCP listener, saved
page content, cookies, history APIs or imported browser profiles. Only control
names, roles and state are used for feedback, excluding input field values.
The app uses its current synthesizer for commands sent from Companion. Popup
reading uses the native host's SAPI voice. Native actions are sent once.

The extension attaches only to the user-selected regular tab through the
supported debugger API. It does not launch a controlled browser, change browser
identity or bypass website security. PayPal sign-in has not been verified.

References:
https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging
https://developer.chrome.com/docs/extensions/reference/api/debugger
https://www.w3.org/WAI/ARIA/apg/patterns/radio/
