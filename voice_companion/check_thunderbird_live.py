"""Windows CI check: the real add-on in a real Thunderbird, through the real host and relay.

Not part of the app. Run on a Windows runner with Thunderbird installed:
  python check_thunderbird_live.py PATH_TO_THUNDERBIRD_EXE WORK_FOLDER

It makes a test profile with one placeholder email account (no real server),
installs the add-on the way Voice Companion does (thunderbird_setup.install_addon),
registers a test native host, starts Thunderbird and then checks:
  - the add-on connects and lists the placeholder account;
  - the Experiment opens the add account page, fills it in and reads it;
  - the sign-in hand-over can be armed (Thunderbird's OAuth2 module is found);
  - minimize answers.
Best effort, reported but not required: pressing Continue for a Gmail address
until Thunderbird starts Google sign-in, and whether that page came to Voice
Companion instead of the Windows browser. Prints one JSON report.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACCOUNT = '\n'.join([
    'user_pref("mail.accountmanager.accounts", "account1,account2");',
    'user_pref("mail.accountmanager.defaultaccount", "account1");',
    'user_pref("mail.accountmanager.localfoldersserver", "server2");',
    'user_pref("mail.account.account1.identities", "id1");',
    'user_pref("mail.account.account1.server", "server1");',
    'user_pref("mail.account.account2.server", "server2");',
    'user_pref("mail.identity.id1.fullName", "Trial User");',
    'user_pref("mail.identity.id1.useremail", "trial@example.com");',
    'user_pref("mail.identity.id1.valid", true);',
    'user_pref("mail.server.server1.hostname", "127.0.0.1");',
    'user_pref("mail.server.server1.name", "trial@example.com");',
    'user_pref("mail.server.server1.type", "imap");',
    'user_pref("mail.server.server1.userName", "trial");',
    'user_pref("mail.server.server1.login_at_startup", false);',
    'user_pref("mail.server.server2.hostname", "Local Folders");',
    'user_pref("mail.server.server2.name", "Local Folders");',
    'user_pref("mail.server.server2.type", "none");',
    'user_pref("mail.server.server2.userName", "nobody");',
    'user_pref("mail.provider.suppress_dialog_on_startup", true);',
    'user_pref("mail.shell.checkDefaultClient", false);',
    'user_pref("mail.rights.version", 1);',
    'user_pref("app.update.enabled", false);',
    'user_pref("app.update.auto", false);',
    'user_pref("browser.aboutConfig.showWarning", false);',
    'user_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);',
    'user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);',
    ''])


def register_host(work):
    """A test native host: Python running thunderbird_bridge.native_stream, as the packaged host does."""
    shim = work / 'mail_host.py'
    shim.write_text('import sys\nsys.path.insert(0, %r)\n' % str(HERE) +
                    'from browser_bridge_host import read_message\n'
                    'from thunderbird_bridge import native_stream\n'
                    'first = read_message(sys.stdin.buffer)\n'
                    'native_stream(first, sys.stdin.buffer, sys.stdout.buffer)\n', encoding='utf-8')
    batch = work / 'mail_host.bat'
    batch.write_text('@echo off\r\n"%s" -u "%s" %%*\r\n' % (sys.executable, shim), encoding='utf-8')
    import thunderbird_bridge
    manifest = work / 'mail-native-host.json'
    manifest.write_text(json.dumps(thunderbird_bridge.host_manifest(batch), indent=2), encoding='utf-8')
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Mozilla\NativeMessagingHosts\com.voicecompanion.mail') as key:
        winreg.SetValueEx(key, '', 0, winreg.REG_SZ, str(manifest))


def main():
    program, work = Path(sys.argv[1]), Path(sys.argv[2])
    work.mkdir(parents=True, exist_ok=True)
    os.environ['VOICE_COMPANION_DATA_DIR'] = str(work / 'data')
    sys.path.insert(0, str(HERE))
    import thunderbird_bridge
    import thunderbird_setup
    from build_thunderbird_addon import build
    report = {'thunderbird': '', 'checks': {}, 'best_effort': {}}
    ini = program.parent / 'application.ini'
    report['thunderbird'] = next((line.split('=', 1)[1].strip() for line in ini.read_text(encoding='utf-8', errors='replace').splitlines()
                                  if line.startswith('Version=')), '') if ini.is_file() else ''
    profile = work / 'profile'
    profile.mkdir(exist_ok=True)
    (profile / 'prefs.js').write_text('// test\n' + ACCOUNT, encoding='utf-8')
    package = build(work / 'Voice Companion Mail Connection.xpi')
    thunderbird_setup.install_addon(profile, package)
    register_host(work)
    relay = thunderbird_bridge.prepare(work / 'data')
    started = time.monotonic()
    thunderbird = subprocess.Popen([str(program), '-profile', str(profile), '-no-remote'])
    ok = True
    try:
        connected = relay.connected.wait(180)
        report['checks']['connected'] = connected
        report['connect_seconds'] = round(time.monotonic() - started, 1)
        report['add_on_version'] = relay.version
        if not connected:
            extensions = (profile / 'extensions.json')
            report['extensions_json'] = json.loads(extensions.read_text(encoding='utf-8')) if extensions.is_file() else None
            raise SystemExit(json.dumps(report, indent=2) + '\nThe add-on did not connect.')

        def ask(method, params=None, timeout=60):
            try:
                return relay.request(method, params or {}, timeout=timeout)
            except Exception as exc:
                return {'error': type(exc).__name__ + ': ' + str(exc)}
        accounts = ask('accounts')
        report['accounts'] = accounts
        report['checks']['lists_placeholder_account'] = any(
            i.get('email') == 'trial@example.com' for a in accounts if isinstance(a, dict) for i in a.get('identities', []))
        capture = ask('captureSignIn', {'seconds': 300})
        report['capture'] = capture
        report['checks']['sign_in_hand_over_armed'] = capture == {'capturing': True}
        opened = ask('openAccountSetup', {'email': 'voice.companion.trial@gmail.com', 'name': 'Trial User', 'show': False})
        report['open'] = opened
        report['checks']['add_account_page_opened'] = isinstance(opened, dict) and opened.get('opened') is True
        report['best_effort']['prefilled'] = isinstance(opened, dict) and opened.get('prefilled') is True
        time.sleep(3)
        page = ask('setupPage')
        report['page'] = page
        report['checks']['page_read'] = isinstance(page, dict) and page.get('open') is True
        # Best effort: Continue until Thunderbird starts Google sign-in, which should come to us.
        pages, sign_in = [page], None
        for _ in range(6):
            pressed = ask('setupContinue')
            time.sleep(8)
            pages.append({'pressed': pressed, 'page': ask('setupPage')})
            found = ask('takeSignIn')
            if isinstance(found, dict) and (found.get('url') or found.get('where')):
                sign_in = {'where': found.get('where'), 'host': found.get('url', '').split('/')[2] if found.get('url') else ''}
                break
        report['best_effort']['pages_after_continue'] = pages[1:]
        report['best_effort']['sign_in'] = sign_in
        report['minimize'] = ask('minimize')
        report['checks']['minimize_answered'] = isinstance(report['minimize'], dict) and 'minimized' in report['minimize']
        ask('captureSignIn', {'seconds': 0})
    finally:
        thunderbird.terminate()
        try: thunderbird.wait(30)
        except subprocess.TimeoutExpired: thunderbird.kill()
        relay.close()
    ok = all(report['checks'].values())
    print(json.dumps(report, indent=2))
    # Also as GitHub annotations, which can be read through the API (check-runs/<id>/annotations).
    summary = {'thunderbird': report['thunderbird'], 'add_on': report.get('add_on_version'), 'checks': report['checks'],
               'best_effort': report['best_effort'], 'page': report.get('page')}
    text = json.dumps(summary).replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
    for start in range(0, len(text), 3500):
        print('::notice title=Thunderbird trial part %d::%s' % (start // 3500 + 1, text[start:start + 3500]))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
