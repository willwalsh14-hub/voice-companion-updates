"""Windows CI check: read, unread, flags, Gmail's Important marker, read receipts and sending
with high importance, in a real Thunderbird, reach the (stand-in) Gmail server.

Not part of the app. Run on a Windows runner with the same Python the build uses:
  python check_thunderbird_marks_live.py WORK_FOLDER

Will, October 31, 2026: a message marked read in Voice Companion stayed unread in Gmail until
Thunderbird was restarted. This drives the real Thunderbird through Voice Companion's own add-on
connection (thunderbird_bridge), exactly as the app does, and checks what the server recorded:
  1. Installs the current Thunderbird Release from Mozilla, signature checked.
  2. A Gmail-like account on fake_gmail_server (X-GM-EXT-1, labels as folders, Sent copies).
  3. Marks read (with its Gmail conversation), unread, flagged; opening marks read; a server
     that refuses STORE twice (recovered on a fresh connection) and always (said as failed);
     a change made on the "phone" (the server) shows in Voice Companion with no restart.
  4. Important on and off (the message stays in the Inbox); a read receipt asked about and sent;
     a message sent with high importance (X-Priority) and its Sent copy marked Important.
Prints one JSON report and writes report.json in WORK_FOLDER. Exit 1 when a check fails.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMAP_PORT, SMTP_PORT = 11143, 11587
ADDRESS = 'trial@gmail.com'
PREFS = [
    'user_pref("mail.accountmanager.accounts", "account1,account2");',
    'user_pref("mail.accountmanager.defaultaccount", "account1");',
    'user_pref("mail.accountmanager.localfoldersserver", "server2");',
    'user_pref("mail.account.account1.identities", "id1");',
    'user_pref("mail.account.account1.server", "server1");',
    'user_pref("mail.account.account2.server", "server2");',
    'user_pref("mail.identity.id1.fullName", "Trial User");',
    'user_pref("mail.identity.id1.useremail", "%s");' % ADDRESS,
    'user_pref("mail.identity.id1.valid", true);',
    'user_pref("mail.identity.id1.smtpServer", "smtp1");',
    # Gmail files its own copy of sent mail, so Thunderbird is told not to (as Gmail advises).
    'user_pref("mail.identity.id1.fcc", false);',
    'user_pref("mail.identity.id1.draft_folder", "imap://trial%40gmail.com@127.0.0.1/%5BGmail%5D/Drafts");',
    'user_pref("mail.server.server1.hostname", "127.0.0.1");',
    'user_pref("mail.server.server1.port", %d);' % IMAP_PORT,
    'user_pref("mail.server.server1.socketType", 0);',
    'user_pref("mail.server.server1.authMethod", 3);',
    'user_pref("mail.server.server1.name", "%s");' % ADDRESS,
    'user_pref("mail.server.server1.type", "imap");',
    'user_pref("mail.server.server1.userName", "%s");' % ADDRESS,
    'user_pref("mail.server.server1.login_at_startup", true);',
    'user_pref("mail.server.server1.check_new_mail", false);',
    'user_pref("mail.server.server1.use_idle", false);',
    'user_pref("mail.server.server2.hostname", "Local Folders");',
    'user_pref("mail.server.server2.name", "Local Folders");',
    'user_pref("mail.server.server2.type", "none");',
    'user_pref("mail.server.server2.userName", "nobody");',
    'user_pref("mail.smtpservers", "smtp1");',
    'user_pref("mail.smtp.defaultserver", "smtp1");',
    'user_pref("mail.smtpserver.smtp1.hostname", "127.0.0.1");',
    'user_pref("mail.smtpserver.smtp1.port", %d);' % SMTP_PORT,
    'user_pref("mail.smtpserver.smtp1.authMethod", 1);',
    'user_pref("mail.smtpserver.smtp1.try_ssl", 0);',
    'user_pref("mail.smtpserver.smtp1.username", "");',
    # Read receipts: Thunderbird's "ask me" setting, for every kind of sender.
    'user_pref("mail.mdn.report.enabled", true);',
    'user_pref("mail.mdn.report.not_in_to_cc", 2);',
    'user_pref("mail.mdn.report.outside_domain", 2);',
    'user_pref("mail.mdn.report.other", 2);',
    'user_pref("mail.provider.suppress_dialog_on_startup", true);',
    'user_pref("mail.shell.checkDefaultClient", false);',
    'user_pref("mail.rights.version", 1);',
    'user_pref("mail.spotlight.firstRunDone", true);',
    'user_pref("mail.winsearch.firstRunDone", true);',
    'user_pref("mail.startup.enabledMailCheckOnce", true);',
    'user_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);',
    'user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);',
    'user_pref("app.update.auto", false);',
    'user_pref("app.update.enabled", false);',
]


def wait(check, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(1)
    return check()


def run(command, timeout=600):
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return (done.stdout + done.stderr).strip()[-1500:]
    except Exception as exc:
        return 'error: ' + type(exc).__name__ + ': ' + str(exc)


def main():
    work = Path(sys.argv[1]); work.mkdir(parents=True, exist_ok=True)
    os.environ['VOICE_COMPANION_DATA_DIR'] = str(work / 'data')
    sys.path.insert(0, str(HERE))
    import thunderbird_bridge
    import thunderbird_setup
    from build_thunderbird_addon import build
    from check_thunderbird_upgrade_live import register_host
    from fake_gmail_server import FakeGmail, SEEN, FLAGGED
    system = thunderbird_setup.WindowsSystem()
    report = {'checks': {}, 'details': {}}
    checks, details = report['checks'], report['details']

    def finish():
        ok = bool(checks) and all(checks.values())
        report['passed'] = ok
        report['server_log_tail'] = gmail.log[-160:]
        text = json.dumps(report, indent=2, default=str)
        print(text, flush=True)
        (work / 'report.json').write_text(text, encoding='utf-8')
        return 0 if ok else 1

    gmail = FakeGmail(ADDRESS)
    gmail.serve_imap(IMAP_PORT)
    gmail.serve_smtp(SMTP_PORT)
    early = gmail.deliver('Your Saturday morning trip with Uber', thread=7001)
    other = gmail.deliver('Lunch on Tuesday', sender='ann@example.com', thread=7002)
    late = gmail.deliver('Your Saturday morning trip with Uber', thread=7001)
    flag_me = gmail.deliver('Your Friday evening trip with Uber', thread=7003)
    receipt = gmail.deliver('Please confirm you got this', sender='boss@example.com', thread=7004,
                            extra_headers='Disposition-Notification-To: boss@example.com\r\n')
    keep = gmail.deliver('Your Uber One annual plan', thread=7005)

    # 1. The current Thunderbird Release, from Mozilla.
    program, _ = thunderbird_setup.find_program(system)
    if not program:
        installer = work / 'Thunderbird Setup.exe'
        system.download(thunderbird_setup.INSTALLER_URL, installer)
        checks['installer_signed_by_mozilla'] = thunderbird_setup.signed_by_mozilla(system, installer)
        details['install'] = run([str(installer), '/S'])
        program, _ = thunderbird_setup.find_program(system)
    details['program'] = str(program)
    if not program:
        checks['thunderbird_installed'] = False
        return finish()
    system.launch_background(program)
    checks['made_its_profile'] = wait(lambda: any((p / 'prefs.js').is_file() for p in thunderbird_setup.profile_candidates(system)), 180)
    system.close_program()
    if not wait(lambda: not system.running(), 60):
        run(['taskkill', '/F', '/IM', 'thunderbird.exe'])   # test setup only
        wait(lambda: not system.running(), 30)
    profile = thunderbird_setup.profile_candidates(system)[0]
    with open(profile / 'prefs.js', 'a', encoding='utf-8') as prefs:
        prefs.write('\n' + '\n'.join(PREFS) + '\n')
    package = build(work / 'Voice Companion Mail Connection.xpi')
    thunderbird_setup.install_addon(profile, package)
    register_host(work)
    relay = thunderbird_bridge.prepare(work / 'data')

    def ask(method, params=None, timeout=90):
        try:
            return relay.request(method, params or {}, timeout=timeout)
        except Exception as exc:
            return {'error': type(exc).__name__ + ': ' + str(exc)}

    system.launch_background(program)
    checks['add_on_connected'] = relay.connected.wait(180)
    details['about'] = ask('about')
    accounts = ask('accounts')
    details['accounts'] = accounts
    account = next((a for a in accounts if isinstance(a, dict) and any(i.get('email') == ADDRESS for i in a.get('identities') or [])), None) \
        if isinstance(accounts, list) else None
    checks['gmail_account_listed'] = account is not None
    if not account:
        ask('quit')
        return finish()
    inbox = ask('inbox', {'accountId': account['id']})
    details['inbox'] = inbox
    folder_id = inbox.get('id') if isinstance(inbox, dict) else None

    def rows():
        page = ask('list', {'folderId': folder_id, 'offset': 0, 'limit': 10})
        details.setdefault('lists', []).append(page if not isinstance(page, dict) or 'rows' not in page
                                               else [(r.get('subject'), r.get('unread'), r.get('flagged'), r.get('important')) for r in page['rows']])
        return page.get('rows', []) if isinstance(page, dict) else []

    found = []
    wait(lambda: found.append(rows()) or len(found[-1]) >= 6, 120)
    listed = found[-1] if found else []
    checks['inbox_listed_all_six'] = len(listed) >= 6
    by_subject = {}
    for row in listed:
        by_subject.setdefault(row.get('subject'), []).append(row['id'])
    details['ids'] = by_subject

    def ident(message):
        """The add-on's number for a server message, matched by subject and order."""
        same = [m for m in [early, other, late, flag_me, receipt, keep] if m.header_value('Subject') == message.header_value('Subject')]
        ids = sorted(by_subject.get(message.header_value('Subject'), []))
        return ids[same.index(message)] if len(ids) == len(same) else None

    def on_server(message, flag):
        return flag in message.flags

    def settle(check, seconds=20):
        return wait(check, seconds)

    if not all(ident(m) for m in (early, late, other, flag_me, receipt, keep)):
        ask('quit')
        return finish()

    # 3a. Marking read: confirmed by the server, with the Gmail conversation.
    result = ask('mark', {'ids': [ident(late)], 'read': True})
    details['mark_read'] = result
    checks['mark_read_confirmed'] = isinstance(result, dict) and result.get('server') == 'confirmed'
    checks['mark_read_reached_server'] = settle(lambda: on_server(late, SEEN))
    checks['conversation_marked_read_on_server'] = settle(lambda: on_server(early, SEEN))
    checks['conversation_named_back'] = isinstance(result, dict) and ident(early) in (result.get('conversationIds') or [])
    checks['other_conversation_left_unread'] = not on_server(other, SEEN)
    result = ask('mark', {'ids': [ident(late)], 'read': False})
    details['mark_unread'] = result
    checks['mark_unread_reached_server'] = settle(lambda: not on_server(late, SEEN))
    checks['unread_leaves_conversation'] = on_server(early, SEEN)
    result = ask('mark', {'ids': [ident(flag_me)], 'flagged': True})
    details['flag'] = result
    checks['flag_reached_server'] = settle(lambda: on_server(flag_me, FLAGGED))
    ask('mark', {'ids': [ident(flag_me)], 'flagged': False})
    checks['unflag_reached_server'] = settle(lambda: not on_server(flag_me, FLAGGED))

    # 3b. The server refuses twice: recovered on a fresh connection, with no restart.
    gmail.refuse_stores = 2
    result = ask('mark', {'ids': [ident(flag_me)], 'read': True})
    details['mark_after_refusals'] = result
    checks['recovered_after_refusals'] = isinstance(result, dict) and result.get('server') == 'confirmed' and settle(lambda: on_server(flag_me, SEEN))
    gmail.refuse_stores = 1000
    result = ask('mark', {'ids': [ident(flag_me)], 'read': False}, timeout=240)
    details['mark_always_refused'] = result
    checks['refusal_said_as_failed'] = isinstance(result, dict) and result.get('server') == 'failed'
    gmail.refuse_stores = 0
    ask('mark', {'ids': [ident(flag_me)], 'read': False})

    # 3c. Opening marks read on the server (Enter in Voice Companion).
    opened = ask('read', {'messageId': ident(other)})
    details['open'] = {k: v for k, v in opened.items() if k not in ('html', 'plain')} if isinstance(opened, dict) else opened
    checks['open_says_read'] = isinstance(opened, dict) and opened.get('read') is True and not opened.get('readNotConfirmed')
    checks['open_reached_server'] = settle(lambda: on_server(other, SEEN))

    # 3d. A change made on the phone (straight on the server) shows with no restart.
    with gmail.lock:
        other.flags.discard(SEEN)
        keep.flags.add(FLAGGED)
    seen = []
    wait(lambda: seen.append({r['id']: r for r in rows()}) or (seen[-1].get(ident(other), {}).get('unread') is True and
                                                                seen[-1].get(ident(keep), {}).get('flagged') is True), 60)
    checks['phone_change_shows_without_restart'] = bool(seen) and seen[-1].get(ident(other), {}).get('unread') is True and \
        seen[-1].get(ident(keep), {}).get('flagged') is True

    # 4a. Gmail's Important marker, on and off; the message stays in the Inbox.
    result = ask('important', {'messageId': ident(keep), 'on': True})
    details['important_on'] = result
    checks['important_on_reached_server'] = settle(lambda: '[Gmail]/Important' in gmail.labels(keep))
    listed_important = {r['id']: r for r in rows()}.get(ident(keep), {}).get('important')
    checks['list_says_important'] = listed_important is True
    result = ask('important', {'messageId': ident(keep), 'on': False})
    details['important_off'] = result
    checks['important_off_reached_server'] = settle(lambda: '[Gmail]/Important' not in gmail.labels(keep))
    checks['message_stays_in_inbox'] = 'INBOX' in gmail.labels(keep)

    # 4b. A read receipt: Thunderbird's "ask" setting, so Voice Companion asks; yes sends it.
    opened = ask('read', {'messageId': ident(receipt)})
    details['receipt_open'] = {k: v for k, v in opened.items() if k not in ('html', 'plain')} if isinstance(opened, dict) else opened
    checks['receipt_asked'] = isinstance(opened, dict) and opened.get('receipt') == 'ask'
    answered = ask('receiptAnswer', {'messageId': ident(receipt), 'send': True})
    details['receipt_answer'] = answered
    checks['receipt_sent'] = settle(lambda: any(b'disposition-notification' in m['raw'].lower() for m in gmail.smtp_messages), 60)
    details['smtp_after_receipt'] = [m['raw'][:600].decode('utf-8', 'replace') for m in gmail.smtp_messages]
    opened = ask('read', {'messageId': ident(receipt)})
    checks['receipt_not_asked_twice'] = isinstance(opened, dict) and not opened.get('receipt')

    # 4c. Sending with high importance: X-Priority for everyone; Gmail's Sent copy marked Important.
    before = len(gmail.smtp_messages)
    sent = ask('send', {'identityId': account['identities'][0]['id'], 'email': ADDRESS, 'to': ['ann@example.com'],
                        'subject': 'Urgent: call me', 'plain': 'Please call me today.', 'html': '<p>Please call me today.</p>',
                        'priority': 'high'}, timeout=180)
    details['send'] = sent
    mails = gmail.smtp_messages[before:]
    checks['sent'] = isinstance(sent, dict) and bool(sent.get('headerMessageId')) and bool(mails)
    checks['sent_with_x_priority_high'] = any(b'x-priority: 2' in m['raw'].lower() for m in mails)
    copy = gmail.find(sent.get('headerMessageId', '')) if isinstance(sent, dict) else None
    checks['gmail_sent_copy_marked_important'] = isinstance(sent, dict) and sent.get('sentImportant') in ('done', 'pending') and \
        settle(lambda: copy is not None and '[Gmail]/Important' in gmail.labels(copy), 60)

    details['quit'] = ask('quit')
    wait(lambda: not system.running(), 60)
    relay.close()
    gmail.close()
    return finish()


if __name__ == '__main__':
    raise SystemExit(main())
