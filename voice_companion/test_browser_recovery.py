import tempfile,unittest
from unittest.mock import Mock,patch
from web_assistant import WebSession
from test_web_assistant import FakeBackend


class BrowserRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.backend=FakeBackend();self.session=WebSession(self.temp.name,self.backend)
        self.session.open('https://example.com/')

    def test_snapshot_retries_navigation_reads_without_repeating_actions(self):
        original=self.backend.snapshot
        with patch.object(self.backend,'snapshot',side_effect=[RuntimeError('Execution context was destroyed, most likely because of a navigation'),original()]) as read:
            self.assertIn('Example account',self.session._refresh(wait=False))
            self.assertEqual(read.call_count,2)
        self.assertEqual(self.backend.activated,[])

    def test_ready_wait_retries_document_replacement(self):
        self.backend.wait_ready=Mock(side_effect=[RuntimeError('Cannot find context with specified id'),None])
        self.assertIn('Example account',self.session._refresh())
        self.assertEqual(self.backend.wait_ready.call_count,2)

    def test_unrelated_errors_are_not_silently_retried(self):
        with patch.object(self.backend,'snapshot',side_effect=RuntimeError('Unexpected failure')) as read:
            with self.assertRaisesRegex(RuntimeError,'Unexpected failure'):self.session._refresh(wait=False)
            self.assertEqual(read.call_count,1)

    def test_visible_frame_change_during_capture_discards_old_controls(self):
        before={'url':self.backend.url,'epoch':'one','frame_identity':[('https://code.example/','old')]}
        after={**before,'frame_identity':[('https://code.example/','new')]}
        old=self.backend.snapshot();new={**old,'text':'Choose another way'}
        self.backend.document_stamp=Mock(side_effect=[before,after,after,after])
        self.backend.wait_ready=Mock()
        with patch.object(self.backend,'snapshot',side_effect=[old,new]) as read:
            self.session._refresh(wait=False)
            self.assertEqual(self.session.reading.text,'Choose another way')
            self.assertEqual(read.call_count,2)
        self.assertEqual(self.backend.activated,[])

    def test_f5_bypasses_broken_capture_and_clears_pending_submission(self):
        self.backend.page=Mock(url='https://example.com/');self.backend.start=Mock()
        self.session.submit_pending={'old':'form'}
        with patch.object(self.session,'_sync_browser_tab',side_effect=RuntimeError('Execution context was destroyed')):
            result=self.session.keyboard('Refresh')
        self.assertIn('Example account',result);self.assertIsNone(self.session.submit_pending)
        self.backend.page.reload.assert_called_once()
        self.assertEqual(self.backend.activated,[])

    def test_f5_recovers_original_address_after_connection_restart(self):
        self.backend.page=Mock(url='https://example.com/');fresh=Mock(url='about:blank')
        self.backend.start=lambda:setattr(self.backend,'page',fresh)
        self.assertIn('Example account',self.session.keyboard('Refresh'))
        fresh.goto.assert_called_once_with('https://example.com/',wait_until='domcontentloaded',timeout=30000)
        fresh.reload.assert_not_called();self.assertEqual(self.backend.activated,[])

    def test_failed_reload_reports_reload_failure(self):
        self.backend.page=Mock();self.backend.page.reload.side_effect=RuntimeError('Disconnected')
        self.assertIn('page reload did not finish',self.session.keyboard('Refresh'))


if __name__=='__main__':unittest.main()
