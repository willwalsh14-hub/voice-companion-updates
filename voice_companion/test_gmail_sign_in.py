"""Gmail sign-in (Add account, Gmail) must always finish with a plain spoken result.

google_auth_oauthlib is replaced by a stand-in that behaves like version 1.x:
- run_local_server waits for Google's redirect; with timeout_seconds and no
  redirect, the real library fails with AttributeError on a missing address.
- Cancel on Google's page raises oauthlib's AccessDeniedError (error='access_denied').
- When the person leaves a permission box unticked, Google grants fewer scopes;
  oauthlib raises Warning('Scope has changed...') unless OAUTHLIB_RELAX_TOKEN_SCOPE is set.
"""
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import mail_accounts
from mail_accounts import AccountError, GMAIL_SCOPES, gmail_connect


class AccessDeniedError(Exception):
    error = 'access_denied'


class FakeCredentials:
    def __init__(self, granted):
        self.valid = True; self.refresh_token = 'refresh'; self.token = 'token'
        self.granted_scopes = granted

    def to_json(self):
        return json.dumps({'token': self.token, 'refresh_token': self.refresh_token})


def fake_flow_module(outcome, calls):
    class InstalledAppFlow:
        @classmethod
        def from_client_config(cls, config, scopes):
            flow = cls(); flow.scopes = scopes; return flow

        def run_local_server(self, **options):
            calls.append(options)
            if outcome == 'timeout':
                # Real library: last_request_uri stays None, then .replace() fails.
                return None.replace('http', 'https')
            if outcome == 'denied':
                raise AccessDeniedError('(access_denied) The user denied access.')
            granted = list(self.scopes) if outcome == 'ok' else ['openid', 'https://www.googleapis.com/auth/userinfo.email']
            if set(granted) != set(self.scopes) and not os.environ.get('OAUTHLIB_RELAX_TOKEN_SCOPE'):
                raise Warning('Scope has changed from "%s" to "%s".' % (' '.join(self.scopes), ' '.join(granted)))
            return FakeCredentials(granted)

    module = types.ModuleType('google_auth_oauthlib.flow'); module.InstalledAppFlow = InstalledAppFlow
    package = types.ModuleType('google_auth_oauthlib'); package.flow = module
    return {'google_auth_oauthlib': package, 'google_auth_oauthlib.flow': module}


class GmailSignInTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.client = self.folder / 'client.json'
        self.client.write_text(json.dumps({'installed': {'client_id': 'abc', 'client_secret': 's'}}), encoding='utf-8')
        self.store = mock.Mock()
        environment = mock.patch.dict(os.environ, {}, clear=False); environment.start(); self.addCleanup(environment.stop)
        os.environ.pop('OAUTHLIB_RELAX_TOKEN_SCOPE', None)
        userinfo = mock.patch.object(mail_accounts, 'secure_service_open',
                                     lambda *a, **k: io.BytesIO(json.dumps({'email': 'me@gmail.com'}).encode()))
        userinfo.start(); self.addCleanup(userinfo.stop)

    def connect(self, outcome):
        calls = []
        with mock.patch.dict(sys.modules, fake_flow_module(outcome, calls)):
            try:
                return gmail_connect(self.store, self.client), calls
            except AccountError as exc:
                return exc, calls

    def test_finished_sign_in_saves_the_account(self):
        result, calls = self.connect('ok')
        self.assertEqual(result, 'me@gmail.com')
        self.store.save.assert_called_once()

    def test_sign_in_never_waits_forever(self):
        result, calls = self.connect('timeout')
        self.assertTrue(calls[0].get('timeout_seconds'), 'sign-in must have a time limit')
        self.assertIsInstance(result, AccountError)
        self.assertIn('took too long', str(result))
        self.assertIn('add account', str(result).lower())
        self.store.save.assert_not_called()

    def test_cancel_on_google_page_is_explained(self):
        result, _ = self.connect('denied')
        self.assertIsInstance(result, AccountError)
        self.assertIn('did not allow', str(result))
        self.store.save.assert_not_called()

    def test_unticked_mail_permission_is_explained(self):
        result, _ = self.connect('partial')
        self.assertIsInstance(result, AccountError)
        self.assertIn('check every box', str(result))
        self.store.save.assert_not_called()

    def test_granted_permissions_as_one_line_are_understood(self):
        calls = []
        modules = fake_flow_module('ok', calls)
        original = modules['google_auth_oauthlib.flow'].InstalledAppFlow.run_local_server
        def as_text(flow, **options):
            credentials = original(flow, **options); credentials.granted_scopes = ' '.join(credentials.granted_scopes); return credentials
        modules['google_auth_oauthlib.flow'].InstalledAppFlow.run_local_server = as_text
        with mock.patch.dict(sys.modules, modules):
            self.assertEqual(gmail_connect(self.store, self.client), 'me@gmail.com')

    def test_browser_page_tells_the_person_to_return(self):
        _, calls = self.connect('ok')
        self.assertIn('Voice Companion', calls[0].get('success_message', ''))

    def test_scope_list_still_includes_mail(self):
        for scope in ('https://www.googleapis.com/auth/gmail.send', 'https://www.googleapis.com/auth/gmail.modify'):
            self.assertIn(scope, GMAIL_SCOPES)


if __name__ == '__main__':
    unittest.main()
