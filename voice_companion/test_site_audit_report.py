import unittest

import consumer_site_audit


class SiteAuditAnnotationTests(unittest.TestCase):
    def test_a_failed_site_names_its_failed_checks(self):
        report = {'sites': [
            {'name': 'Good site', 'status': 'passed', 'checks': []},
            {'name': 'Bad site', 'status': 'failed', 'loaded_url': 'https://example.org/',
             'checks': [{'action': 'Quick:h', 'passed': True, 'feedback': 'Heading'},
                        {'action': 'next link', 'passed': False, 'feedback': 'No more links.\nEnd'}]},
            {'name': 'Blocked site', 'status': 'blocked', 'reason': 'CAPTCHA'}]}
        lines = consumer_site_audit.failure_annotations(report, github=True)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith('::error title=Site audit: Bad site::'))
        self.assertIn('next link -> No more links.%0AEnd', lines[0])
        self.assertNotIn('Quick:h', lines[0])

    def test_nothing_is_printed_outside_github(self):
        self.assertEqual(consumer_site_audit.failure_annotations({'sites': [{'name': 'x', 'status': 'failed'}]}, github=False), [])


if __name__ == '__main__':
    unittest.main()
