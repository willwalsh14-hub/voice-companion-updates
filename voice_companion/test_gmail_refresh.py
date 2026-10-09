import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from google.auth.exceptions import RefreshError, TransportError
from mail_accounts import AccountError, GMAIL_SCOPES, gmail_token
from mail_voice import MailSession

class GmailRefreshTests(unittest.TestCase):
    def test_refresh_failures_are_actionable_and_do_not_replace_saved_account(self):
        for failure, message in [(RefreshError('secret'), 'sign in again'),
                                 (RefreshError('secret', retryable=True), 'temporarily unavailable'),
                                 (TransportError('secret'), 'internet connection')]:
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as folder:
                config = Path(folder) / 'google-client.json'
                config.write_text(json.dumps({'installed': {'client_id': 'client'}}))
                store = Mock()
                store.load.return_value = {'client_id': 'client', 'scopes': GMAIL_SCOPES,
                                           'credentials': '{}', 'address': 'person@example.com'}
                credentials = Mock(valid=False, refresh_token='secret')
                credentials.refresh.side_effect = failure
                with patch('google.oauth2.credentials.Credentials.from_authorized_user_info', return_value=credentials):
                    with self.assertRaises(AccountError) as caught:
                        gmail_token(store, config)
                self.assertIn(message, str(caught.exception))
                self.assertNotIn('secret', str(caught.exception))
                store.save.assert_not_called()

    def test_successful_refresh_saves_new_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'google-client.json'
            config.write_text(json.dumps({'installed': {'client_id': 'client'}}))
            store = Mock()
            store.load.return_value = {'client_id': 'client', 'scopes': GMAIL_SCOPES,
                                       'credentials': '{}', 'address': 'person@example.com'}
            credentials = Mock(valid=False, refresh_token='old', token='new')
            credentials.to_json.return_value = '{"new": true}'
            with patch('google.oauth2.credentials.Credentials.from_authorized_user_info', return_value=credentials):
                self.assertEqual(gmail_token(store, config), ('person@example.com', 'new'))
            store.save.assert_called_once()

    def test_unexpected_inbox_failure_records_type_without_private_details(self):
        with tempfile.TemporaryDirectory() as folder:
            session = MailSession('gmail', folder)
            with patch.object(session, 'client', side_effect=RuntimeError('private email and secret token')):
                result = session.process('open inbox')
            self.assertIn('Email-Error.txt', result)
            detail = (Path(folder) / 'Email-Error.txt').read_text()
            self.assertIn('RuntimeError', detail)
            self.assertNotIn('private', detail)
            self.assertNotIn('secret', detail)

class MicrosoftRegistrationTests(unittest.TestCase):
    def test_bundled_registration_and_local_override(self):
        from mail_accounts import registration
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(registration(folder)['microsoft_client_id'], '599c05a4-8d91-4ee9-9e10-933905f87663')
            Path(folder, 'mail-registration.json').write_text(json.dumps({'microsoft_client_id': 'custom'}))
            self.assertEqual(registration(folder)['microsoft_client_id'], 'custom')

    def test_installed_registration_uses_executable_folder(self):
        from mail_accounts import registration
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as install:
            Path(install, 'microsoft-registration.json').write_text(json.dumps({'microsoft_client_id': 'installed'}))
            with patch('mail_accounts.sys.frozen', True, create=True), patch('mail_accounts.sys.executable', str(Path(install, 'VoiceCompanion.exe'))):
                self.assertEqual(registration(folder)['microsoft_client_id'], 'installed')
