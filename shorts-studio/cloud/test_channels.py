import json
import unittest

import actions_deliver as ad
from slots import topic_settings

A, B = 'UC' + 'a' * 22, 'UC' + 'b' * 22
SOURCE = {'YOUTUBE_OAUTH_JSON': json.dumps({'refresh_token': 'default'}),
          'YOUTUBE_ACCOUNTS_JSON': json.dumps({A: {'refresh_token': 'ra'}, B: {'refresh_token': 'rb'}})}


class ChannelTests(unittest.TestCase):
    def test_upload_uses_the_chosen_channel_sign_in(self):
        self.assertEqual(ad.youtube_sign_in(SOURCE, B)['refresh_token'], 'rb')
        self.assertEqual(ad.youtube_sign_in(SOURCE)['refresh_token'], 'default')
        # A removed channel is never silently swapped for another one.
        with self.assertRaisesRegex(RuntimeError, 'no longer connected'):
            ad.youtube_sign_in(SOURCE, 'UC' + 'c' * 22)
        with self.assertRaisesRegex(RuntimeError, 'YOUTUBE_OAUTH_JSON'):
            ad.youtube_sign_in({})

    def test_each_topic_has_its_own_channel(self):
        settings = {'domain': 'Science', 'youtube': A,
                    'topics': [{'id': 'main', 'domain': 'Physics'}, {'id': 'faith', 'domain': 'Islam', 'youtube': B}]}
        self.assertEqual(topic_settings(settings, 'main')['youtube'], A)
        self.assertEqual(topic_settings(settings, 'faith')['youtube'], B)

    def test_the_autopilot_schedule_accepts_only_channel_ids(self):
        from autonomy import Schedule
        self.assertEqual(Schedule(domain='x', youtube=A).youtube, A)
        with self.assertRaises(Exception):
            Schedule(domain='x', youtube='not a channel')


if __name__ == '__main__':
    unittest.main()


class SignInExpiredTests(unittest.TestCase):
    def test_an_expired_youtube_sign_in_says_how_to_fix_it(self):
        from youtube import SIGNIN_EXPIRED, upload_problem
        raw = ("('invalid_grant: Token has been expired or revoked.', {'error': 'invalid_grant', "
               "'error_description': 'Token has been expired or revoked.'})")
        self.assertEqual(upload_problem(RuntimeError(raw)), SIGNIN_EXPIRED)
        self.assertIn('Account → YouTube', SIGNIN_EXPIRED)
        self.assertIn('Publish app', SIGNIN_EXPIRED)
        self.assertEqual(upload_problem(RuntimeError('quotaExceeded')), 'quotaExceeded')
