import json
import unittest
from unittest.mock import Mock, patch

from claude_usage import UsageBlocked, read_usage
from test_notify import Opener
from usage_report import refresh_usage, report_usage


class UsageReportTests(unittest.TestCase):
    def test_reports_exact_percentages_without_credentials(self):
        opener = Opener()
        self.assertTrue(report_usage({'sessionPercent': 72.125, 'weeklyPercent': 90.5, 'token': 'private'},
            source={'STUDIO_PAGE_URL': 'https://studio.example', 'STUDIO_NOTIFY_KEY': 'key'}, opener=opener))
        request = opener.requests[0]
        body = json.loads(request.data)
        self.assertEqual(body['sessionPercent'], 72.125)
        self.assertEqual(body['weeklyPercent'], 90.5)
        self.assertNotIn('token', body)
        self.assertEqual(request.full_url, 'https://studio.example/api/claude-usage/report')
        self.assertTrue(body['checkedAt'] > 0)

    def test_reading_above_creation_threshold_is_still_displayed(self):
        import io
        data = {'five_hour': {'utilization': 91.25}, 'seven_day': {'utilization': 83.5}}
        result = read_usage(source={'CLAUDE_CODE_OAUTH_TOKEN': 'private'},
                            open_url=lambda *a, **k: io.BytesIO(json.dumps(data).encode()))
        self.assertEqual(result['sessionPercent'], 91.25)

    def test_report_is_optional_and_failures_do_not_break_creation(self):
        self.assertFalse(report_usage(source={}))
        self.assertFalse(report_usage(opener=Opener(fail=True),
            source={'STUDIO_PAGE_URL': 'https://studio.example', 'STUDIO_NOTIFY_KEY': 'key'}))

    def test_refresh_only_reads_usage_and_reports_success_or_error(self):
        with patch('usage_report.read_usage', return_value={'sessionPercent': 50, 'weeklyPercent': 75}), \
             patch('usage_report.report_usage', return_value=True) as report:
            self.assertTrue(refresh_usage())
            report.assert_called_once_with({'sessionPercent': 50, 'weeklyPercent': 75})
        with patch('usage_report.read_usage', side_effect=UsageBlocked('Usage unavailable')), \
             patch('usage_report.report_usage', return_value=True) as report:
            self.assertTrue(refresh_usage())
            report.assert_called_once_with(message='Usage unavailable')
