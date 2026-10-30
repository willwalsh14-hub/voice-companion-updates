"""The Chrome Web Store step, run against a fake store that behaves like the real API (v2).

The fake follows the published reference:
https://developer.chrome.com/docs/webstore/api/reference/rest
- upload refuses a package whose version is not higher than the store's,
  and refuses a manifest with a "key" field, as the real store does;
- fetchStatus reports publishedItemRevisionStatus / submittedItemRevisionStatus
  with distributionChannels[].crxVersion;
- every call needs the Chrome Web Store access token.
Sign-in is faked too: GitHub's OIDC token endpoint, Google STS and
IAM Credentials for Workload Identity Federation, or the OAuth token
endpoint for a service account key.
"""
import base64
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import chrome_store_publish as store

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / 'voice_companion' / 'regular_browser_extension'
PUBLISHER = 'pub-123'
ITEM = store.STORE_EXTENSION_ID
PROVIDER = 'projects/42/locations/global/workloadIdentityPools/github/providers/voice-companion'
ACCOUNT = 'chrome-store@vc-project.iam.gserviceaccount.com'
STORE_TOKEN = 'store-access-token'


def version_key(text):
    return tuple(int(part) for part in text.split('.')) + (0,) * (4 - len(text.split('.')))


class FakeGoogle:
    """One fake for GitHub OIDC, Google sign-in and the Chrome Web Store."""
    def __init__(self, published='0.4.1', pending=None, async_upload=False, fail_upload=None):
        self.published, self.pending = published, pending
        self.async_upload, self.fail_upload = async_upload, fail_upload
        self.calls, self.uploaded = [], None
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def body(self):
                return self.rfile.read(int(self.headers.get('Content-Length') or 0))

            def do_GET(self):
                fake.calls.append(('GET', self.path, self.headers.get('Authorization')))
                return fake.get(self)

            def do_POST(self):
                data = self.body()
                fake.calls.append(('POST', self.path, self.headers.get('Authorization')))
                return fake.post(self, data)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.base = 'http://127.0.0.1:%d' % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def endpoints(self):
        return store.Endpoints(store=self.base, upload=self.base, sts=self.base + '/sts/v1/token',
                               iam=self.base + '/iam', oauth=self.base + '/oauth/token')

    def environment(self, **extra):
        env = {'CWS_PUBLISHER_ID': PUBLISHER, 'CWS_WORKLOAD_IDENTITY_PROVIDER': PROVIDER,
               'CWS_SERVICE_ACCOUNT': ACCOUNT,
               'ACTIONS_ID_TOKEN_REQUEST_URL': self.base + '/github/token?api-version=2.0',
               'ACTIONS_ID_TOKEN_REQUEST_TOKEN': 'github-request-token'}
        env.update(extra)
        return {key: value for key, value in env.items() if value is not None}

    def status(self):
        result = {'name': f'publishers/{PUBLISHER}/items/{ITEM}', 'itemId': ITEM, 'publicKey': 'KEY'}
        if self.published:
            result['publishedItemRevisionStatus'] = {
                'state': 'PUBLISHED', 'distributionChannels': [{'deployPercentage': 100, 'crxVersion': self.published}]}
        if self.pending:
            version, state = self.pending
            result['submittedItemRevisionStatus'] = {
                'state': state, 'distributionChannels': [{'deployPercentage': 100, 'crxVersion': version}]}
        if self.async_upload == 'done':
            result['lastAsyncUploadState'] = 'SUCCEEDED'
        elif self.async_upload == 'waiting':
            result['lastAsyncUploadState'] = 'IN_PROGRESS'
        return result

    def get(self, handler):
        path = urlparse(handler.path)
        if path.path == '/github/token':
            query = parse_qs(path.query)
            if handler.headers.get('Authorization') != 'Bearer github-request-token':
                return handler.reply(401, {'message': 'Bad credentials'})
            self.audience = query.get('audience', [''])[0]
            return handler.reply(200, {'value': 'github-oidc-jwt'})
        if handler.headers.get('Authorization') != 'Bearer ' + STORE_TOKEN:
            return handler.reply(401, {'error': {'code': 401, 'message': 'Request had invalid authentication credentials.',
                                                 'status': 'UNAUTHENTICATED'}})
        if path.path == f'/v2/publishers/{PUBLISHER}/items/{ITEM}:fetchStatus':
            status = self.status()
            if self.async_upload == 'waiting':
                self.async_upload = 'done'
                self.published_after_upload()
            return handler.reply(200, status)
        return handler.reply(404, {'error': {'code': 404, 'message': 'Requested entity was not found.', 'status': 'NOT_FOUND'}})

    def published_after_upload(self):
        pass

    def post(self, handler, data):
        path = urlparse(handler.path).path
        if path == '/sts/v1/token':
            request = json.loads(data)
            assert request['subjectToken'] == 'github-oidc-jwt', request
            assert request['audience'] == '//iam.googleapis.com/' + PROVIDER, request
            assert request['grantType'] == 'urn:ietf:params:oauth:grant-type:token-exchange'
            assert request['subjectTokenType'] == 'urn:ietf:params:oauth:token-type:jwt'
            return handler.reply(200, {'access_token': 'federated-token', 'token_type': 'Bearer', 'expires_in': 3600})
        if path.startswith('/iam/v1/projects/-/serviceAccounts/') and path.endswith(':generateAccessToken'):
            # Only the account the pool may impersonate is allowed, as in real IAM.
            if handler.headers.get('Authorization') != 'Bearer federated-token' or ACCOUNT not in path:
                return handler.reply(403, {'error': {'code': 403, 'message': 'Permission iam.serviceAccounts.getAccessToken denied.',
                                                     'status': 'PERMISSION_DENIED'}})
            request = json.loads(data)
            assert request['scope'] == ['https://www.googleapis.com/auth/chromewebstore'], request
            return handler.reply(200, {'accessToken': STORE_TOKEN, 'expireTime': '2026-10-29T12:00:00Z'})
        if path == '/oauth/token':
            form = parse_qs(data.decode())
            assert form['grant_type'] == ['urn:ietf:params:oauth:grant-type:jwt-bearer'], form
            self.assertion = form['assertion'][0]
            return handler.reply(200, {'access_token': STORE_TOKEN, 'token_type': 'Bearer', 'expires_in': 3600})
        if handler.headers.get('Authorization') != 'Bearer ' + STORE_TOKEN:
            return handler.reply(401, {'error': {'code': 401, 'message': 'Request had invalid authentication credentials.',
                                                 'status': 'UNAUTHENTICATED'}})
        if path == f'/upload/v2/publishers/{PUBLISHER}/items/{ITEM}:upload':
            if self.fail_upload:
                return handler.reply(400, {'error': {'code': 400, 'message': self.fail_upload, 'status': 'INVALID_ARGUMENT'}})
            with zipfile.ZipFile(io.BytesIO(data)) as package:
                manifest = json.loads(package.read('manifest.json'))
                names = package.namelist()
            if 'key' in manifest:
                return handler.reply(400, {'error': {'code': 400, 'message': 'key field is not allowed in manifest.',
                                                     'status': 'INVALID_ARGUMENT'}})
            if self.published and version_key(manifest['version']) <= version_key(self.published):
                return handler.reply(400, {'error': {'code': 400, 'status': 'INVALID_ARGUMENT', 'message':
                                                     'The version of the uploaded package must be larger than the published version.'}})
            self.uploaded = {'manifest': manifest, 'names': names}
            if self.async_upload:
                self.async_upload = 'waiting'
                return handler.reply(200, {'name': f'publishers/{PUBLISHER}/items/{ITEM}', 'itemId': ITEM,
                                           'uploadState': 'IN_PROGRESS'})
            return handler.reply(200, {'name': f'publishers/{PUBLISHER}/items/{ITEM}', 'itemId': ITEM,
                                       'crxVersion': manifest['version'], 'uploadState': 'SUCCEEDED'})
        if path == f'/v2/publishers/{PUBLISHER}/items/{ITEM}:publish':
            if self.uploaded is None:
                return handler.reply(400, {'error': {'code': 400, 'message': 'No draft to publish.', 'status': 'FAILED_PRECONDITION'}})
            self.pending = (self.uploaded['manifest']['version'], 'PENDING_REVIEW')
            return handler.reply(200, {'name': f'publishers/{PUBLISHER}/items/{ITEM}', 'itemId': ITEM, 'state': 'PENDING_REVIEW'})
        return handler.reply(404, {'error': {'code': 404, 'message': 'Requested entity was not found.', 'status': 'NOT_FOUND'}})


class ExtensionCopy:
    """A copy of the real extension with a chosen version."""
    def __init__(self, version):
        self.folder = Path(tempfile.mkdtemp(prefix='vc-extension-'))
        shutil.copytree(EXTENSION, self.folder, dirs_exist_ok=True)
        manifest = json.loads((self.folder / 'manifest.json').read_text(encoding='utf-8'))
        manifest['version'] = version
        (self.folder / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')

    def close(self):
        shutil.rmtree(self.folder, ignore_errors=True)


class StorePublishTests(unittest.TestCase):
    def setUp(self):
        self.lines = []

    def run_step(self, fake, version='0.4.9', env=None):
        copy = ExtensionCopy(version)
        self.addCleanup(copy.close)
        result = store.run(copy.folder, fake.environment() if env is None else env, fake.endpoints(),
                           output=self.lines.append, poll_seconds=0)
        return result

    def fake(self, **options):
        fake = FakeGoogle(**options)
        self.addCleanup(fake.close)
        return fake

    def annotations(self):
        return '\n'.join(self.lines)

    def test_higher_version_is_uploaded_and_submitted_for_review_with_workload_identity(self):
        fake = self.fake(published='0.4.1')
        status, message = self.run_step(fake, '0.4.9')
        self.assertEqual(status, 'submitted', message)
        self.assertIn('0.4.9', message)
        self.assertIn('review', message.lower())
        self.assertEqual(fake.audience, 'https://iam.googleapis.com/' + PROVIDER)
        # The store refuses a "key" field: it was removed from the uploaded copy only.
        self.assertNotIn('key', fake.uploaded['manifest'])
        self.assertIn('key', json.loads((EXTENSION / 'manifest.json').read_text(encoding='utf-8')))
        self.assertIn('worker.js', fake.uploaded['names'])
        for private in ('test-worker.cjs', 'README.md', 'SETUP.txt', 'extension-id.txt'):
            self.assertNotIn(private, fake.uploaded['names'])
        self.assertIn('::notice title=Chrome Web Store - submitted for review::', self.annotations())
        # The store token is hidden in GitHub's logs.
        self.assertIn('::add-mask::' + STORE_TOKEN, self.annotations())

    def test_same_or_lower_version_is_not_uploaded(self):
        for local in ('0.4.1', '0.4.0', '0.3.9'):
            with self.subTest(local=local):
                fake = self.fake(published='0.4.1')
                status, message = self.run_step(fake, local)
                self.assertEqual(status, 'skipped', message)
                self.assertIn('0.4.1', message)
                self.assertIsNone(fake.uploaded)
                self.assertFalse([call for call in fake.calls if ':upload' in call[1] or ':publish' in call[1]])

    def test_versions_compare_as_numbers_not_text(self):
        fake = self.fake(published='0.4.9')
        status, message = self.run_step(fake, '0.4.10')
        self.assertEqual(status, 'submitted', message)
        self.assertTrue(store.newer('0.4.10', '0.4.9'))
        self.assertFalse(store.newer('0.4', '0.4.0'))
        self.assertTrue(store.newer('1', '0.99.99.99'))

    def test_version_already_in_review_is_not_uploaded_again(self):
        fake = self.fake(published='0.4.1', pending=('0.4.9', 'PENDING_REVIEW'))
        status, message = self.run_step(fake, '0.4.9')
        self.assertEqual(status, 'skipped', message)
        self.assertIn('review', message)
        self.assertIsNone(fake.uploaded)

    def test_older_version_still_in_review_waits_instead_of_cancelling_it(self):
        # Cancelling would restart review on every release; the next run uploads instead.
        fake = self.fake(published='0.4.1', pending=('0.4.5', 'PENDING_REVIEW'))
        status, message = self.run_step(fake, '0.4.9')
        self.assertEqual(status, 'skipped', message)
        self.assertIn('0.4.5', message)
        self.assertIn('review', message)
        self.assertIsNone(fake.uploaded)
        self.assertFalse([call for call in fake.calls if 'cancelSubmission' in call[1]])

    def test_rejected_or_cancelled_submission_does_not_block_a_new_upload(self):
        for state in ('REJECTED', 'CANCELLED'):
            with self.subTest(state=state):
                fake = self.fake(published='0.4.1', pending=('0.4.5', state))
                status, message = self.run_step(fake, '0.4.9')
                self.assertEqual(status, 'submitted', message)

    def test_item_never_published_still_uploads(self):
        fake = self.fake(published=None)
        status, message = self.run_step(fake, '0.4.9')
        self.assertEqual(status, 'submitted', message)

    def test_upload_still_processing_is_followed_with_fetch_status(self):
        fake = self.fake(published='0.4.1', async_upload=True)
        status, message = self.run_step(fake, '0.4.9')
        self.assertEqual(status, 'submitted', message)

    def test_missing_setup_skips_cleanly_and_says_what_is_missing(self):
        fake = self.fake()
        cases = {
            'publisher': ({}, 'CWS_PUBLISHER_ID'),
            'sign-in': ({'CWS_PUBLISHER_ID': PUBLISHER}, 'CWS_WORKLOAD_IDENTITY_PROVIDER'),
            'oidc permission': ({'CWS_PUBLISHER_ID': PUBLISHER, 'CWS_WORKLOAD_IDENTITY_PROVIDER': PROVIDER,
                                 'CWS_SERVICE_ACCOUNT': ACCOUNT}, 'id-token: write'),
            'half set up': ({'CWS_PUBLISHER_ID': PUBLISHER, 'CWS_WORKLOAD_IDENTITY_PROVIDER': PROVIDER}, 'CWS_SERVICE_ACCOUNT'),
        }
        for name, (env, words) in cases.items():
            with self.subTest(name):
                self.lines.clear()
                status, message = self.run_step(fake, env=env)
                self.assertEqual(status, 'skipped', message)
                self.assertIn(words, message)
                self.assertIn('tools/chrome-store-publishing.md', message)
                self.assertIn('::notice title=Chrome Web Store - skipped::', self.annotations())
        self.assertFalse(fake.calls, 'Nothing is sent to Google until sign-in is set up.')

    def test_store_errors_are_reported_and_never_raise(self):
        fake = self.fake(published='0.4.1', fail_upload='Package is invalid. Details: Manifest file is invalid.')
        status, message = self.run_step(fake, '0.4.9')
        self.assertEqual(status, 'failed')
        self.assertIn('Package is invalid', message)
        self.assertIn('::warning title=Chrome Web Store - not sent::', self.annotations())
        self.assertNotIn(STORE_TOKEN, message)

    def test_sign_in_refused_is_reported_plainly(self):
        fake = self.fake()
        env = fake.environment(CWS_SERVICE_ACCOUNT='someone-else@vc-project.iam.gserviceaccount.com')
        status, message = self.run_step(fake, env=env)
        self.assertEqual(status, 'failed')
        self.assertIn('sign', message.lower())
        self.assertIn('iam.serviceAccounts.getAccessToken', message)

    def test_unreachable_store_is_a_warning_not_a_crash(self):
        fake = self.fake()
        endpoints = store.Endpoints(store='http://127.0.0.1:9', upload='http://127.0.0.1:9', sts=fake.base + '/sts/v1/token',
                                    iam=fake.base + '/iam', oauth=fake.base + '/oauth/token')
        copy = ExtensionCopy('0.4.9')
        self.addCleanup(copy.close)
        status, message = store.run(copy.folder, fake.environment(), endpoints, output=self.lines.append, poll_seconds=0)
        self.assertEqual(status, 'failed')
        self.assertIn('::warning', self.annotations())

    def test_service_account_key_fallback_signs_a_real_jwt(self):
        openssl = shutil.which('openssl')
        if not openssl:
            self.skipTest('openssl is not installed.')
        with tempfile.TemporaryDirectory() as folder:
            key_path = Path(folder) / 'key.pem'
            subprocess.run([openssl, 'genrsa', '-out', str(key_path), '2048'], check=True, capture_output=True)
            public = subprocess.run([openssl, 'rsa', '-in', str(key_path), '-pubout'], check=True, capture_output=True).stdout
            (Path(folder) / 'public.pem').write_bytes(public)
            fake = self.fake(published='0.4.1')
            key = {'type': 'service_account', 'client_email': ACCOUNT, 'private_key': key_path.read_text(),
                   'private_key_id': 'abc', 'token_uri': 'https://oauth2.googleapis.com/token'}
            env = {'CWS_PUBLISHER_ID': PUBLISHER, 'CWS_SERVICE_ACCOUNT_KEY': json.dumps(key)}
            status, message = self.run_step(fake, env=env)
            self.assertEqual(status, 'submitted', message)
            header, claims, signature = fake.assertion.split('.')
            decode = lambda part: base64.urlsafe_b64decode(part + '=' * (-len(part) % 4))
            self.assertEqual(json.loads(decode(header))['alg'], 'RS256')
            body = json.loads(decode(claims))
            self.assertEqual(body['iss'], ACCOUNT)
            self.assertEqual(body['scope'], 'https://www.googleapis.com/auth/chromewebstore')
            self.assertEqual(body['aud'], 'https://oauth2.googleapis.com/token')
            self.assertEqual(body['exp'] - body['iat'], 3600)
            (Path(folder) / 'signed').write_bytes((header + '.' + claims).encode())
            (Path(folder) / 'signature').write_bytes(decode(signature))
            verify = subprocess.run([openssl, 'dgst', '-sha256', '-verify', str(Path(folder) / 'public.pem'),
                                     '-signature', str(Path(folder) / 'signature'), str(Path(folder) / 'signed')],
                                    capture_output=True, text=True)
            self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
            # The private key is never left on disk or printed.
            self.assertNotIn('PRIVATE KEY', self.annotations())

    def test_damaged_key_is_reported_without_echoing_it(self):
        fake = self.fake()
        status, message = self.run_step(fake, env={'CWS_PUBLISHER_ID': PUBLISHER, 'CWS_SERVICE_ACCOUNT_KEY': '{"private_key": "hunter2-private'})
        self.assertEqual(status, 'failed')
        self.assertNotIn('hunter2', message + self.annotations())

    def test_store_id_matches_the_app(self):
        source = (ROOT / 'voice_companion' / 'browser_bridge_host.py').read_text(encoding='utf-8')
        self.assertIn(f"CHROME_STORE_EXTENSION_ID = '{store.STORE_EXTENSION_ID}'", source)

    def test_command_line_always_exits_zero(self):
        # The release is already public when this runs: the step must never turn it red.
        env = {key: value for key, value in os.environ.items() if not key.startswith(('CWS_', 'ACTIONS_ID_TOKEN'))}
        result = subprocess.run(['python3', str(Path(store.__file__)), '--extension', str(EXTENSION)],
                                capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('::notice title=Chrome Web Store - skipped::', result.stdout)


if __name__ == '__main__':
    unittest.main()
