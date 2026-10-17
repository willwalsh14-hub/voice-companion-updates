# Voice Companion Browser Connection Privacy Policy

Effective date: October 16, 2026

## Scope and publisher

This policy covers the Voice Companion Browser Connection extension for Google Chrome and the browser connection it provides to the Voice Companion Windows application. The project is published through the willwalsh14-hub GitHub account. The extension requires the separately installed desktop application.

## Purpose

The extension connects Chrome to Voice Companion so users can read webpages and navigate and operate page controls through spoken feedback, voice commands, and keyboard controls.

## Information handled

To provide these features, the extension and connected desktop application handle website addresses, tab identifiers and titles, page structure and accessibility information, webpage text, control states, and commands and text used to interact with pages and forms.

Depending on the pages a user opens and the actions they request, this information can include personally identifiable information, authentication information entered into sign-in forms, personal communications, financial or payment information, and health information. These categories are handled to provide the requested browsing and accessibility functions, not to build advertising profiles.

The extension does not request device geolocation. It does not provide a separate microphone recorder; voice recognition is a function of the desktop application.

## Local connection and storage

The extension passes browser commands and page information to the installed Voice Companion native messaging host on the same computer. The desktop application uses a local browser relay to provide its browsing features. The extension itself does not send page information to a developer-operated analytics or advertising server.

The extension stores its enabled or paused preference in Chrome local extension storage. Its browser transport does not maintain a persistent page-content log. The desktop application separately keeps browsing history and saved favorites locally. Website cookies, site storage, downloads, and saved credentials managed by Chrome remain subject to Chrome settings and the websites involved.

## Optional online speech and websites

Local browser communication does not mean every desktop feature operates offline. If the user enables the desktop application's optional OpenAI AI voice and grants permission to send narration text, text spoken through that voice can be sent to OpenAI for speech generation. That text may include webpage content. Users can use local speech engines instead and disable AI narration consent in the desktop application's speech settings.

The desktop application also offers optional online speech recognition subject to its consent settings; the current application disables cloud recognition in web mode and when AI-based dictation is selected. AI-based dictation uses the bundled offline speech model and local cleanup of fillers and clear repeated starts. It does not make an additional online text-cleanup request. The extension does not itself upload microphone audio.

When a user navigates to a website or submits a form, Chrome communicates with that website as part of normal browsing. Those websites and any optional online providers apply their own privacy policies. This policy does not cover unrelated desktop features such as email account synchronization.

## Use, sharing, and Limited Use

Information handled through the extension is used to provide its browser accessibility connection and the user-directed reading, navigation, and interaction features. It is not sold, used for personalized advertising, or transferred to data brokers. The extension does not provide routine developer access to users' browsing sessions.

Voice Companion Browser Connection's use and transfer of information received from Google APIs adheres to the Chrome Web Store User Data Policy, including its Limited Use requirements. Data use and transfer are limited to the disclosed user-facing functionality and permitted purposes under that policy. Information a user voluntarily includes in a support request is used to address that request.

## Retention and user controls

Browser commands and page information are processed during use; the extension does not archive page content. Its connection preference remains until changed or its extension storage is cleared. The desktop application's history, favorites, and other saved settings may remain until removed by the user; removing the extension does not automatically erase desktop application data or Chrome website data.

Users can pause the connection from the extension popup, close Voice Companion, disable or remove the extension, and manage website data in Chrome. Pausing or removing the extension stops its browser connection. Users can disable optional AI narration consent in Voice Companion.

## Security

The native messaging host allows only explicitly approved extension identities. The desktop relay uses authenticated local communication and restricts access to its browser connection. These protections reduce unauthorized access; no software can guarantee absolute security.

## Changes and contact

This policy will be updated when the extension's data practices change. The effective date identifies the latest revision.

For privacy questions or requests, open an issue at:
https://github.com/willwalsh14-hub/voice-companion-updates/issues

Issues are public. Do not include passwords, account details, private webpage content, or other sensitive information in a public issue.
