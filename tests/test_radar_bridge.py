import copy
import unittest
from wamo_radar import public_edition
from wamo_runtime import patch_status_ui


def inputs():
    report = dict(edition='US', schema_version=1, release_key='r1', analysis_fingerprint='f1',
                  generated_at_utc='2026-09-12T00:00:00+00:00', data_ready=True,
                  analysis_ready=True, problems=[], signal_count=1,
                  expected_sessions={'US-NYSE': {'date': '20260911'}},
                  coverage={'US-NYSE': {'target': 1, 'price_confirmed': 1, 'as_of': '20260911'}},
                  signals=[dict(key='NYS:TEST', market='US-NYSE', symbol='TEST', name='Test',
                                as_of='20260911', status='ok', signal_active=True,
                                flags={'close_63': True, 'close_ath': None},
                                private_value='do not publish')], industries=[])
    status = dict(report_ready=True, release_policy_version=3, problems=[],
                  release_key='r1', analysis_fingerprint='f1')
    return report, status


class RadarTests(unittest.TestCase):
    def test_existing_detail_can_be_opened_without_replacing_its_implementation(self):
        html = '<body><script>function openDetail(x){ return x; }</script></body>'
        updated = patch_status_ui(html, 'KR')
        self.assertIn('window.WAMO_OPEN_DETAIL', updated)
        self.assertIn('function openDetail(x){ return x; }', updated)
        self.assertEqual(patch_status_ui(updated, 'KR'), updated)
    def test_only_allowlisted_fields_exported_and_unknown_stays_null(self):
        r, s = inputs()
        out = public_edition(r, s, {'US-NYSE': '20260911'})
        self.assertNotIn('private_value', out['signals'][0])
        self.assertIsNone(out['signals'][0]['flags']['close_ath'])

    def test_stale_mismatched_or_unready_report_is_rejected(self):
        for change in ({'report_ready': False}, {'analysis_fingerprint': 'other'}, {'problems': ['failure']}):
            r, s = inputs()
            s.update(change)
            with self.assertRaises(ValueError):
                public_edition(r, s, {'US-NYSE': '20260911'})
        r, s = inputs()
        with self.assertRaises(ValueError):
            public_edition(r, s, {'US-NYSE': '20260914'})

    def test_duplicate_or_wrong_date_candidate_is_rejected(self):
        for wrong_date in (True, False):
            r, s = inputs()
            if wrong_date:
                r['signals'][0]['as_of'] = '20260910'
            else:
                r['signals'].append(copy.deepcopy(r['signals'][0]))
                r['signal_count'] = 2
            with self.assertRaises(ValueError):
                public_edition(r, s, {'US-NYSE': '20260911'})
