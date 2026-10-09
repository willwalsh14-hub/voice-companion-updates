VOICE COMPANION — EXPERIMENTAL WINDOWS PROTOTYPE

This is source code and a repeatable Windows installer builder, not a finished installer. The latest speech, email, and guided browser changes have not yet been built or tested on Windows. The client should not have to install Microsoft Word, Python, a speech model, or another program once a Windows build is packaged. An executable must first be produced on a Windows build computer by double-clicking BUILD INSTALLER - Double click.cmd. The builder, not the client, installs the open-source build tools and bundles the offline speech model. Check redistribution notices and test the Windows executable before distributing it.

Current experimental capabilities after a successful Windows build:
- Offline wake phrases: "Wake up."
- "Go to sleep" ignores task requests but continues local wake-word listening. "Shut down Companion" exits, stopping the mic. After full shutdown it must be launched again.
- "Create a document" opens a simple self-voicing Word-compatible editor. "Name document sample letter," "Start dictation," "New line," "New paragraph," "Pause dictation," "Read paragraph," "Read sentence," "Next sentence," "Next word," "Spell word," "Replace word with goodbye," "Replace sentence with ...," "Delete word," "Delete sentence," "Change hello to goodbye," "Previous paragraph," "Next paragraph," "Make this a heading," "Make this bold," "Replace paragraph with ...," and "Save document" are examples. Files autosave as .docx in your Windows Documents folder, inside Voice Companion. Say "Open documents" to open it. Existing documents from the older app data folder are copied there without deleting the originals. Microsoft Word is not needed to create them.
- "Write an email" creates a local unsent draft with recipient, subject, and the same body editing commands as documents. "List email drafts" and "Open email draft NAME" reopen it after restarting. "Send email" reviews the full message when a supported account is connected; "Confirm send email" makes one send attempt. Without a connected account it saves the draft without sending. Say "Write an email," "Create a document," or "Write a note" to switch directly between writing tasks; current work is saved first. Draft data stays local; contacts lookup is not connected. Gmail, Microsoft, and Yahoo sending require helper setup and live verification.
- "Write a note" supports basic dictation. An unfinished note is saved before sleep or shutdown. Notes save to %LOCALAPPDATA%\VoiceCompanion\notes.txt.
- A guided browser session can read page text in short pieces, offer links by natural-language description, save local favorites, and ask for simple text form fields one by one. This is untested on Windows and not universal website access. Podcast pages still open externally. Calendar, Contacts, and Drive are NOT connected.

The current document editor supports paragraphs, whole-paragraph heading, bold, and italic formatting, and one-level voice undo during the current session. It keeps three recent saved recovery copies of document files and email draft data for trainer-assisted recovery. It protects existing document names from accidental overwrite when renaming; it removes the old filename after a successful rename, and Undo restores the previous name during that session. It can reopen the simple Word files it created by name. It cannot safely edit arbitrary Word files with complex formatting, provide all Word editing features, or browse independently. No paid speech service is included in this prototype. The intended design keeps a bundled offline wake phrase and fallback; optional cloud dictation can be added after comparing real-world accuracy, ongoing cost, and privacy. The program uses the Windows default microphone at its supported rate, tries mono then stereo input, and converts the audio to mono 16 kHz for recognition. It briefly ignores microphone input after each spoken reply to reduce self-triggering through speakers. The audio queue is bounded; if recognition falls behind and loses input, the app discards incomplete audio and asks the user to repeat the request. Accuracy and startup with built-in and USB microphones still require real Windows testing. The program calls Windows SAPI directly for spoken confirmations and responses. The voice and audible output must be checked with JAWS off on the target computer.

Windows build (for a developer or trainer, not for the client):
The simple route is to extract this ZIP on Windows 11 and double-click BUILD INSTALLER - Double click.cmd. Read BUILD ON WINDOWS - READ ME.txt first. The builder checks for Python 3.12 and, if missing, attempts to install it through Windows Package Manager. It then runs the Parakeet build and the packaged checks. It opens the Windows Setup installer only if those checks pass. The builder now opens Setup, waits for installation, and checks the installed version and runtime. This path has not been run on Windows in this development environment. Manual developer route:
1. On a Windows 11 build computer, install Python 3.12 with the Python launcher (py). Internet access is required while building.
2. Open PowerShell in this source folder. Run: .\build-windows.ps1 -Parakeet
3. The script makes a clean build environment, downloads and bundles both offline models, packages a windowed VoiceCompanion.exe with visible status and spoken output and a separate console VoiceCompanion-Diagnostics.exe, and runs release checks, bundled-model checks, and document and email draft workflow checks against the packaged EXE. It may take a while and need several gigabytes of free space. The Parakeet model files alone total roughly 640 MB after completed downloads; the portable folder will be larger.
4. Copy VoiceCompanion-Setup.exe from the builder folder to a Windows 11 test computer. This is the only file to give the client; the builder removes staging EXEs after the install check passes. Double-click the installer once; it installs the included components. On the final page, the checked "Open Voice Companion now" option launches the app when Finish is selected. A trainer may uncheck it. For later use, open it from the Start menu and use START HERE - Veteran.txt. The test computer should not need Word, Python, pip, Inno Setup, or a speech service subscription. The installer copies the packaged dependencies and both offline models; it does not run pip or download them on David’s computer. The guided Firefox runtime is bundled and selected by default; Chrome, Brave, and Edge require the selected browser to be installed. Confirm this on a clean Windows 11 test machine. First run check-setup.cmd in the installed program folder to check the bundled models, default microphone, and spoken output. With JAWS off, the helper must press Y only if Voice Companion's test sentence was actually audible; otherwise press N. Then run test-microphone.cmd. The veteran says "Wake up. Create a document" during its seven-second capture. It reports both recognizers and fails if the offline wake listener misses "Wake up" or, in a Parakeet build, Parakeet misses "document." The temporary recording is held in memory and is not saved. Then test wake, sleep, shutdown, dictation, and document saving during normal app use with a real microphone.
5. If a smaller Vosk-only build is desired, run .\build-windows.ps1 without -Parakeet. Never treat a passed build check as proof that hands-free recognition is reliable.

There is no completed Windows executable in this ZIP. The build script itself has not been executed on Windows. Check redistribution notices before wider distribution. The model downloads and the source code require a builder; the veteran should receive the built folder and the simple guide, not the source ZIP.
An optional .github/workflows/windows-build.yml is included for a developer who places this source at the root of a GitHub repository. It runs the Parakeet build and packaged checks on a GitHub Windows Server runner, then keeps the portable folder as a short-lived workflow artifact if all checks pass. It has not been run yet. Passing there does not replace testing the microphone, speaker, sleep, and shutdown on a Windows 11 computer with the intended user.
Use TRAINER TEST CHECKLIST.txt on each real Windows 11 test computer. Record pass, fail, or not tested and the exact failure; the builder copies it into the portable folder. The five START HERE - Veteran formats are the same short user guide; the HTML version is linked from the Start menu.
Privacy: Recognition happens locally through Vosk. Podcast searches contact Apple's catalog. Web searches open Google. Notes and Word documents stay on the computer. Google's Gmail, Calendar, Contacts and Drive sync will require user sign-in, account authorization, and an internet connection in a later build. Gmail mail connection is implemented in source but requires a registered client ID and live testing; Calendar, Contacts, and Drive are not connected. Cloud speech is optional in source code; the offline build omits the Azure SDK and includes no cloud account or credentials. A future cloud build needs its own dependency, service account, and tests.

Experimental cloud speech path: The source also has an optional Microsoft Azure Speech adapter. A support person must configure VOICE_COMPANION_AZURE_KEY and VOICE_COMPANION_AZURE_REGION on Windows. The key is never bundled in the EXE. When configured, the offline wake phrase starts cloud recognition; cloud failure triggers spoken fallback to offline recognition. This code has not been tested with a live account, and a free-tier quota is not a promise of no billing. The default build bundles Vosk. The optional -Parakeet build bundles both Vosk for wake/sleep/shutdown and Parakeet for spoken requests and dictation after waking. If Parakeet fails, an utterance falls back to Vosk. The Parakeet model loaded and transcribed a short sample recording in a Linux source test, but has not been run with a Windows microphone or tested in a Windows executable. The speech model is spelled Parakeet. NVIDIA Parakeet TDT 0.6B v3 is an offline speech recognition candidate (CC BY 4.0 model); its experimental adapter is integrated in the optional build. Its CPU speed, memory needs, wake-word behavior, and accuracy with this client's built-in microphone must be tested on his Windows 11 computer. A speech model alone does not provide voice control, text-to-speech, web browsing, or Google sync.

Offline microphone controls: Say "go to sleep" to stop acting on task requests. A local Vosk listener remains active solely so "wake up" can wake it again. Say "shut down companion" to close the app and stop its microphone stream entirely; voice alone cannot restart a fully closed process. These phrases are checked with local Vosk even while cloud recognition is running. Test their reliability on the actual microphone before client use.

DOCUMENTATION RULE FOR FUTURE BUILDS
Every change to a spoken phrase, workflow, supported feature, or limitation must be reflected in START HERE - Veteran.txt in the same update. Run build_user_guides.py to regenerate and validate HTML, EPUB, Word, and DAISY 3; the Windows builder runs it automatically before packaging. Do not hand-edit generated guides. Check the veteran's guide against the working program before distributing any test build. Describe only tested behavior; keep instructions short, spoken, and free of developer setup steps. The current spoken "help with documents" gives a brief list of examples. The editor now stores multiple formatted text runs within paragraphs and paragraph line spacing; check both across save and reopen before distribution. Fuller help from inside the app is planned for a later stage; it is not part of this build.
EMAIL AND BROWSER DEVELOPMENT TARGET (September 26, 2026)

Current: email drafts can be labeled Gmail, Outlook, or Yahoo, and the three
mail websites can be opened in the default browser. Voice prompted Gmail and Microsoft OAuth account connection and private-window Yahoo app-password SMTP sending are
implemented in source with multiple encrypted account records, but have no registered app IDs or live Windows account test in this archive. Without that setup, no message is transmitted.
The source also has voice mailbox listing, reading, paging, selection, moving,
Trash, reply drafts, forwarding drafts, and confirmed folder creation, rename,
move, and empty-folder deletion for Gmail, Microsoft, and Yahoo. None has been
tested with a live account. Web searches and confirmed HTTPS addresses now open a separate guided browser profile. It reads page text and offers likely links and simple text fields by voice. This path has mock-backed source tests only; it has not run on Windows or live sites. Passwords and final financial or application submission require direct review.

The source contains isolated Gmail API and Microsoft Graph mail adapters
and Yahoo IMAP/SMTP adapters, with tests for the message body, recipient, response, and uncertain
network failure. They are wired to a two-step spoken review and confirmation, with
Windows DPAPI-protected account storage and browser sign-in. A distributor
must supply registered public client IDs, and live provider testing is still
required before client use. Microsoft Graph also covers compatible Microsoft 365/Exchange
Online accounts when the tenant permits it. Yahoo uses a helper-entered app
password and encrypted SMTP. Never treat a request timeout as proof that a
message was not sent; check the account and recipient before any manual retry.

Next: supply registered public desktop app IDs for Google and Microsoft,
complete OAuth consent/publishing requirements, and test each service live on
Windows. The account owner or AT helper handles browser sign-in. Yahoo needs
a separate app password generated in Yahoo Account Security; the normal
account password is never used. No credential is bundled in the executable. A saved
local draft must stay local until the user reviews the recipient, subject,
body, and sending account and gives a separate explicit send command. Confirm
success only after the service acknowledges the send; preserve the draft on
any error.

Browser target: a normal Windows browser experience, including opening URLs,
tabs, back/forward, finding and reading page content, links, buttons, form
fields, entering and correcting text, file downloads/uploads, and website
sign-in. Secure sites such as banks must retain their normal browser security
and user authentication; passwords, one-time codes, CAPTCHAs, and transaction
approval cannot be bypassed. Prefer a real supported browser with an
accessibility-aware control layer rather than attempting to recreate web
standards in the voice-command program. Announce page and field context,
require an explicit review and confirmation before submitting consequential
forms, and let the user switch to keyboard or screen reader at any point.
Automated interaction may be limited by individual sites; test across sites.

Email provider target: support Microsoft personal mail (Outlook and Hotmail),
Microsoft 365/Exchange Online, Gmail, Yahoo, and standards-based IMAP/SMTP
accounts where the provider allows them. OAuth where offered; provider-approved
app passwords only where required and permitted. Some managed Exchange servers
and proprietary email systems may need a dedicated integration or administrator
approval. Do not promise universal provider compatibility before connection
and send/receive tests. Test each voice command on Windows with screen readers
and real microphone audio.

The build-time release checks live in smoke_tests.py. The copied test folder includes check-setup.cmd for the trainer. It reports the selected Windows default microphone and its sample rate, checks bundled models, and speaks a test sentence. Set the desired microphone as the Windows default before launching the program. A successful automated check cannot establish recognition accuracy, microphone quality, or accessibility on the test PC.

Parakeet integration check: on September 25, 2026, the specified ONNX model loaded offline in the development environment and the app adapter transcribed an 8.3-second public spoken sample. This confirms the API path; it does not measure this veteran's dictation accuracy or prove Windows audio and packaging behavior.

The guided browser reads the full visible body up to 200,000 characters and detects open shadow-root controls and readable frames. Very long pages or inaccessible content still need a helper. The local address book can import and add Google Contacts after separate People API consent; Gmail mail authorization remains separate.

Voice list now chooses a synthesizer first, then a voice. Local eSpeak NG US and UK English is prepared by Quick Test and included by the Windows installer builder. Windows speech remains the default. AI voice remains optional. Live Windows voice and timing checks are required.

PUBLIC TEST DISTRIBUTION
Distribute the finished VoiceCompanion-Setup.exe, not the inner VoiceCompanion.exe. Setup includes all five guide formats and creates Documents\Voice Companion\User Guides plus desktop and Start menu shortcuts. Say Open user guides inside the app to open that folder. The app also refreshes the guide copies on startup.

UPDATE DISTRIBUTION
Startup update checks and spoken Yes/No installation are implemented. The update endpoint is configured for willwalsh14-hub/voice-companion-updates; the finished Windows installer and matching manifest must still be published. BUILD INSTALLER - Double click.cmd now publishes automatically after the packaged and installed checks pass. The builder prepares GitHub sign-in before the long build starts; later runs reuse it. A new build computer needs sign-in with repository write permission. Recipients running Setup do not need GitHub. If publishing fails after a successful build, use PUBLISH UPDATE - Double click.cmd to retry. Uploads are verified before the release becomes latest, and published versions are never overwritten. UPDATES - Trainer.txt has the setup instructions. Each client needs the first update-enabled installer once. Network failures leave normal app use available. The guides are updated by the same installer. Live Windows upgrades have not been tested here.

Build and publishing progress: version 0.2.77 uses the same publisher for BUILD INSTALLER and PUBLISH UPDATE. Stage messages and elapsed waiting messages appear in the console. Publishing progress is saved in VoiceCompanion-Publish-Log.txt. The builder verifies a publication confirmation for the current checked installer before reporting full success.
