"""Windows CI check: Voice Companion moves a real Thunderbird ESR to the current Release.

Not part of the app. Run on a Windows runner (administrator, no Thunderbird yet), with
the same Python the build uses:
  python check_thunderbird_upgrade_live.py WORK_FOLDER

  1. Installs Thunderbird ESR from Mozilla (thunderbird-esr-latest-ssl), signature checked.
  2. Starts it once so it makes its own profile and installs.ini, the way a person's does.
     Adds placeholder accounts (IMAP and Exchange, no real servers) and the add-on the way
     Voice Companion does, and a test native host.
  3. Runs Voice Companion's own update (thunderbird_upgrade.Upgrade) with the real Windows
     edge: closing through the add-on, the backup, Mozilla's signed Release installer into
     the same folder, starting it, and the before-and-after account check.
  4. Checks the result in the running Thunderbird through the add-on: Release channel,
     version 155 or newer, the same profile, the same accounts, add account page with its
     connection choices. Best effort, reported: a Microsoft Graph account's type, what
     winget and Chocolatey offer, where Mozilla's download links lead.
Prints one JSON report and writes report.json in WORK_FOLDER.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib import request

HERE = Path(__file__).resolve().parent
ESR_URL = 'https://download.mozilla.org/?product=thunderbird-esr-latest-ssl&os=win64&lang=en-US'
ACCOUNTS = [
    'user_pref("mail.accountmanager.accounts", "account1,account2,account3");',
    'user_pref("mail.accountmanager.defaultaccount", "account1");',
    'user_pref("mail.accountmanager.localfoldersserver", "server3");',
    'user_pref("mail.account.account1.identities", "id1");',
    'user_pref("mail.account.account1.server", "server1");',
    'user_pref("mail.account.account2.identities", "id2");',
    'user_pref("mail.account.account2.server", "server2");',
    'user_pref("mail.account.account3.server", "server3");',
    'user_pref("mail.identity.id1.fullName", "Trial User");',
    'user_pref("mail.identity.id1.useremail", "trial@example.com");',
    'user_pref("mail.identity.id1.valid", true);',
    'user_pref("mail.identity.id2.fullName", "Trial User");',
    'user_pref("mail.identity.id2.useremail", "trial@work.example");',
    'user_pref("mail.identity.id2.valid", true);',
    'user_pref("mail.server.server1.hostname", "127.0.0.1");',
    'user_pref("mail.server.server1.name", "trial@example.com");',
    'user_pref("mail.server.server1.type", "imap");',
    'user_pref("mail.server.server1.userName", "trial");',
    'user_pref("mail.server.server1.login_at_startup", false);',
    'user_pref("mail.server.server2.hostname", "127.0.0.1");',
    'user_pref("mail.server.server2.name", "trial@work.example");',
    'user_pref("mail.server.server2.type", "ews");',
    'user_pref("mail.server.server2.userName", "trial@work.example");',
    'user_pref("mail.server.server2.ews_url", "https://127.0.0.1/EWS/Exchange.asmx");',
    'user_pref("mail.server.server2.login_at_startup", false);',
    'user_pref("mail.server.server3.hostname", "Local Folders");',
    'user_pref("mail.server.server3.name", "Local Folders");',
    'user_pref("mail.server.server3.type", "none");',
    'user_pref("mail.server.server3.userName", "nobody");',
    'user_pref("mail.provider.suppress_dialog_on_startup", true);',
    'user_pref("mail.shell.checkDefaultClient", false);',
    'user_pref("mail.rights.version", 1);',
    'user_pref("mail.spotlight.firstRunDone", true);',
    'user_pref("mail.winsearch.firstRunDone", true);',
    'user_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);',
    'user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);',
]
GRAPH_ACCOUNT = [
    'user_pref("mail.account.account4.identities", "id4");',
    'user_pref("mail.account.account4.server", "server4");',
    'user_pref("mail.identity.id4.fullName", "Trial User");',
    'user_pref("mail.identity.id4.useremail", "trial@contoso.onmicrosoft.com");',
    'user_pref("mail.identity.id4.valid", true);',
    'user_pref("mail.server.server4.hostname", "graph.microsoft.com");',
    'user_pref("mail.server.server4.name", "trial@contoso.onmicrosoft.com");',
    'user_pref("mail.server.server4.type", "graph");',
    'user_pref("mail.server.server4.userName", "trial@contoso.onmicrosoft.com");',
    'user_pref("mail.server.server4.login_at_startup", false);',
]


def where_it_leads(url):
    """The address download.mozilla.org sends this product to (names the version and channel)."""
    class Stop(request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise StopIteration(newurl)
    try:
        request.build_opener(Stop()).open(request.Request(url, method='HEAD'), timeout=30)
    except StopIteration as found:
        return str(found.args[0])
    except Exception as exc:
        return 'error: ' + type(exc).__name__ + ': ' + str(exc)
    return ''


def run(command, timeout=120):
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return (done.stdout + done.stderr).strip()[-1500:]
    except Exception as exc:
        return 'error: ' + type(exc).__name__ + ': ' + str(exc)


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


def wait(check, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(1)
    return check()


def main():
    work = Path(sys.argv[1]); work.mkdir(parents=True, exist_ok=True)
    os.environ['VOICE_COMPANION_DATA_DIR'] = str(work / 'data')
    sys.path.insert(0, str(HERE))
    import thunderbird_bridge
    import thunderbird_setup
    import thunderbird_upgrade
    from build_thunderbird_addon import build
    system = thunderbird_setup.WindowsSystem()
    report = {'python': sys.executable, 'base_python': getattr(sys, '_base_executable', ''), 'checks': {}, 'best_effort': {}}
    checks, best = report['checks'], report['best_effort']
    said = []

    def progress(text):
        said.append(text); print('  said:', text, flush=True)

    # Where Mozilla's links lead, and what winget and Chocolatey offer (for the pull request).
    best['download_links'] = {'thunderbird-latest-ssl': where_it_leads(thunderbird_setup.INSTALLER_URL), 'thunderbird-esr-latest-ssl': where_it_leads(ESR_URL)}
    best['latest_release_from_mozilla'] = system.latest_release()
    best['choco_thunderbird'] = run(['choco', 'search', 'thunderbird', '--exact', '--limit-output'])
    best['winget_version'] = run(['winget', '--version'])

    # 1. Thunderbird ESR, from Mozilla, signature checked.
    installer = work / 'Thunderbird ESR Setup.exe'
    system.download(ESR_URL, installer)
    checks['esr_installer_signed_by_mozilla'] = thunderbird_setup.signed_by_mozilla(system, installer)
    print(run([str(installer), '/S'], timeout=600) or 'ESR installer finished', flush=True)
    program, version = thunderbird_setup.find_program(system)
    before_info = thunderbird_upgrade.installed(system, program, version)
    report['before_install'] = before_info._asdict() if before_info else None
    if before_info:
        report['before_install']['folder'] = str(before_info.folder)
    checks['esr_installed'] = bool(before_info and before_info.channel == 'esr')
    if not program:
        print(json.dumps(report, indent=2, default=str))
        return 1

    # 2. Its own profile, made by its first start, then accounts and the add-on.
    system.launch_background(program)
    made = wait(lambda: any((p / 'prefs.js').is_file() for p in thunderbird_setup.profile_candidates(system)), 180)
    checks['esr_made_its_profile'] = made
    system.close_program()
    if not wait(lambda: not system.running(), 60):
        run(['taskkill', '/F', '/IM', 'thunderbird.exe'])   # test setup only; Voice Companion never forces
        wait(lambda: not system.running(), 30)
    profile = thunderbird_setup.profile_candidates(system)[0]
    root = thunderbird_upgrade.profile_root(system, profile)
    report['profile'] = str(profile)
    report['installs_ini_before'] = (root / 'installs.ini').read_text(encoding='utf-8', errors='replace') if (root / 'installs.ini').is_file() else ''
    with open(profile / 'prefs.js', 'a', encoding='utf-8') as prefs:
        prefs.write('\n' + '\n'.join(ACCOUNTS) + '\n')
    package = build(work / 'Voice Companion Mail Connection.xpi')
    thunderbird_setup.install_addon(profile, package)
    register_host(work)
    relay = thunderbird_bridge.prepare(work / 'data')

    def ask(method, params=None, timeout=60):
        try:
            return relay.request(method, params or {}, timeout=timeout)
        except Exception as exc:
            return {'error': type(exc).__name__ + ': ' + str(exc)}

    system.launch_background(program)
    checks['add_on_connected_in_esr'] = relay.connected.wait(180)
    report['esr_about'] = ask('about')
    report['esr_accounts'] = ask('accounts')
    checks['esr_reports_esr_channel'] = isinstance(report['esr_about'], dict) and report['esr_about'].get('channel') == 'esr'

    # 3. Voice Companion's own update, with the real Windows edge.
    flow = thunderbird_upgrade.Upgrade(system, package, lambda: relay, progress, work / 'data', started=lambda r: said.append('(started)'))
    question = flow.check()
    report['question'] = question.message
    checks['asks_one_question'] = question.question == 'upgrade' and question.message == thunderbird_upgrade.QUESTION
    started = time.monotonic()
    result = flow.answer('upgrade', True)
    report['update_seconds'] = round(time.monotonic() - started)
    report['update_said'] = said[:]
    report['update_result'] = result.message
    report['update_report'] = flow.report
    checks['updated_and_checked'] = result.message.startswith('Thunderbird is updated to version') and 'All your email accounts are here' in result.message
    after = thunderbird_upgrade.installed(system, program)
    report['after_install'] = after._asdict() if after else None
    if after:
        report['after_install']['folder'] = str(after.folder)
    checks['release_channel_after'] = bool(after and after.channel == 'release')
    checks['version_155_or_newer'] = bool(after and after.major and after.major >= 155)
    checks['same_install_folder'] = bool(after and before_info and str(after.folder).lower() == str(before_info.folder).lower())
    report['installs_ini_after'] = (root / 'installs.ini').read_text(encoding='utf-8', errors='replace') if (root / 'installs.ini').is_file() else ''
    checks['installs_ini_unchanged'] = report['installs_ini_after'] == report['installs_ini_before']
    backups = sorted((root / thunderbird_upgrade.BACKUP_FOLDER).glob('before-update-*/backup.json'))
    checks['backup_made'] = bool(backups)

    # 4. The add-on in the running Release Thunderbird.
    checks['add_on_connected_in_release'] = relay.connected.wait(120)
    about = ask('about')
    report['release_about'] = about
    checks['about_says_release'] = isinstance(about, dict) and about.get('channel') == 'release'
    checks['same_profile_opened'] = isinstance(about, dict) and thunderbird_upgrade.same_folder(about.get('profile', ''), profile)
    accounts = ask('accounts')
    report['release_accounts'] = accounts
    before_accounts = thunderbird_upgrade.addon_accounts(report['esr_accounts']) if isinstance(report['esr_accounts'], list) else None
    after_accounts = thunderbird_upgrade.addon_accounts(accounts) if isinstance(accounts, list) else None
    checks['same_accounts'] = before_accounts is not None and before_accounts == after_accounts
    report['folders'] = {a['id']: ask('folders', {'accountId': a['id']}) for a in accounts} if isinstance(accounts, list) else {}
    checks['nothing_blocks_updates'] = not (Path(program).parent / 'distribution' / 'policies.json').exists() and \
        not system.registry('HKLM', r'Software\Policies\Mozilla\Thunderbird', 'DisableAppUpdate') and \
        'app.update' not in (profile / 'user.js').read_text(encoding='utf-8', errors='replace')
    capture = ask('captureSignIn', {'seconds': 300})
    checks['sign_in_hand_over_armed'] = capture == {'capturing': True}
    opened = ask('openAccountSetup', {'email': 'voice.companion.trial@outlook.com', 'name': 'Trial User', 'show': False})
    report['add_account_open'] = opened
    checks['add_account_page_opened'] = isinstance(opened, dict) and opened.get('opened') is True
    time.sleep(3)
    pages = [ask('setupPage')]
    for _ in range(4):
        ask('setupContinue')
        time.sleep(8)
        pages.append(ask('setupPage'))
        if any(isinstance(p, dict) and p.get('choices') for p in pages):
            break
    best['add_account_pages'] = pages
    best['connection_choices'] = [p.get('choices') for p in pages if isinstance(p, dict) and p.get('choices')]
    ask('captureSignIn', {'seconds': 0})
    report['minimize'] = ask('minimize')
    checks['minimize_answered'] = isinstance(report['minimize'], dict) and 'minimized' in report['minimize']

    # Best effort: a Microsoft Graph account, as Thunderbird 154 and newer know it.
    quit_answer = ask('quit')
    closed = wait(lambda: not system.running(), 60)
    best['quit'] = {'answer': quit_answer, 'closed': closed}
    if closed:
        text = (profile / 'prefs.js').read_text(encoding='utf-8', errors='replace')
        text = text.replace('"account1,account2,account3"', '"account1,account2,account3,account4"')
        with open(profile / 'prefs.js', 'w', encoding='utf-8') as prefs:
            prefs.write(text + '\n' + '\n'.join(GRAPH_ACCOUNT) + '\n')
        wait(lambda: not relay.connected.is_set(), 15)
        system.launch_background(program)
        if relay.connected.wait(120):
            found = ask('accounts')
            best['graph_account_listed'] = [a for a in found if isinstance(a, dict) and a.get('id') == 'account4'] if isinstance(found, list) else found
            best['graph_inbox'] = ask('inbox', {'accountId': 'account4'})
            best['graph_folders'] = ask('folders', {'accountId': 'account4'})
            best['about_with_graph'] = ask('about')
    best['winget_after'] = {'esr': run(['winget', 'list', '--id', 'Mozilla.Thunderbird.ESR', '-e', '--accept-source-agreements']),
                            'release': run(['winget', 'list', '--id', 'Mozilla.Thunderbird', '-e', '--accept-source-agreements'])}
    ask('quit')
    wait(lambda: not system.running(), 30)
    relay.close()
    ok = all(checks.values())
    report['passed'] = ok
    text = json.dumps(report, indent=2, default=str)
    print(text)
    (work / 'report.json').write_text(text, encoding='utf-8')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
