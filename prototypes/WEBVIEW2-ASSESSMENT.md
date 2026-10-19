# Edge WebView2 assessment

The installed Microsoft Edge WebView2 runtime successfully hosted an isolated local test page without a browser extension. Companion's existing BrowserBackend.snapshot parsed its headings and links. Playwright filled a labelled input and activated a button, and the Chromium accessibility tree exposed heading, link, textbox and button roles.

This proves the existing guided browsing engine can operate against an embedded Edge surface. It does not yet establish parity with the shipping browser. This prototype is not enabled or bundled in build 148.

The test used a separate disposable profile, a loopback fixture and Microsoft's WebView2 SDK 1.0.4258.31. The SDK package SHA256 was 56f7f4b8bf9aee4b8efefbbdd4f67d5f74ebd1b100ed0806da71bf76af481aa9. WebView2Host.cs accepts only loopback fixture addresses and denies permissions, popups and downloads. Its development CDP port is loopback-only and must not become a permanently exposed production endpoint.

Before replacing the extension path:

- Connect browser choice, lifetime, focus, window closing and speech keyboard routing to an owned WebView2 host.
- Implement accessible address, tab, history, download and permission interfaces. Handle popups without losing the original task.
- Run the existing guided browser, forms, frame, shadow DOM, media and extension-parity fixtures against WebView2.
- Test real authentication and account workflows. Microsoft documents that Google Authentication is disabled in embedded webviews; normal-browser sign-in may still be needed.
- Package and verify the Evergreen WebView2 prerequisite, profile isolation and process cleanup. Replace the development CDP endpoint with a private transport or an authenticated local broker.
- Test NVDA/JAWS focus and spoken feedback on actual websites before making WebView2 the default.

Sources:

- [Microsoft: Differences between Edge and WebView2](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/browser-features)
- [Microsoft: Automate and test WebView2](https://learn.microsoft.com/en-us/microsoft-edge/webview2/how-to/webdriver)
- [Microsoft WebView2 SDK](https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.4258.31)
