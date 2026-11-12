import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import actions_film as af
from autopilot_check import busy_slots, check
from slots import due_slot, publish_at, slot_label
from test_actions import DESIGN, fake_author, fake_join, fake_render, fake_synth, review_result

BEIRUT = {'timezone': 'Asia/Beirut', 'days': [0, 1, 2, 3, 4, 5, 6], 'times': ['09:00', '17:30']}
AT = lambda h, m: datetime(2026, 11, 12, h, m, tzinfo=timezone.utc)  # Thursday; Beirut is UTC+2


class SlotTests(unittest.TestCase):
    def test_due_window_starts_before_the_post_time(self):
        self.assertIsNone(due_slot(BEIRUT, [], AT(5, 30)))                       # 07:30 Beirut: too early
        self.assertEqual(slot_label(due_slot(BEIRUT, [], AT(6, 17))), '2026-11-12T09:00+02:00')
        self.assertIsNone(due_slot(BEIRUT, [], AT(14, 17)))                      # 16:17: 73 min early
        self.assertEqual(slot_label(due_slot(BEIRUT, [], AT(14, 37))), '2026-11-12T17:30+02:00')
        # Any minute works: 09:05 is due from 08:00 and is published at exactly 09:05.
        odd = {**BEIRUT, 'times': ['09:05']}
        self.assertEqual(slot_label(due_slot(odd, [], AT(6, 7))), '2026-11-12T09:05+02:00')
        self.assertEqual(publish_at('2026-11-12T09:05+02:00', AT(6, 7)), '2026-11-12T07:05:00Z')
        self.assertIsNone(due_slot(BEIRUT, [], AT(11, 0)))                       # 13:00: nothing due

    def test_claimed_busy_and_unselected_days_are_skipped(self):
        label = '2026-11-12T09:00+02:00'
        self.assertIsNone(due_slot(BEIRUT, [{'slot': label}], AT(6, 17)))
        self.assertIsNone(due_slot(BEIRUT, [], AT(6, 17), busy={label}))
        self.assertIsNone(due_slot({**BEIRUT, 'days': [0]}, [], AT(6, 17)))

    def test_late_runs_still_post_but_not_scheduled(self):
        self.assertEqual(slot_label(due_slot(BEIRUT, [], AT(8, 0))), '2026-11-12T09:00+02:00')  # 1h late
        self.assertIsNone(due_slot(BEIRUT, [], AT(10, 0)))                                      # 3h late: skipped
        self.assertEqual(publish_at('2026-11-12T09:00+02:00', AT(6, 17)), '2026-11-12T07:00:00Z')
        self.assertIsNone(publish_at('2026-11-12T09:00+02:00', AT(6, 55)))


class CheckTests(unittest.TestCase):
    def write(self, root, **settings):
        path = Path(root) / 'settings.json'
        path.write_text(json.dumps({'enabled': True, 'domain': 'Space', **BEIRUT, **settings}))
        return path

    def test_check_dispatches_the_due_slot_once(self):
        calls = []

        def call(method, path, body=None):
            calls.append((method, path, body))
            return {'workflow_runs': runs} if method == 'GET' else None
        runs = []
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'ideas.json').write_text(json.dumps([{'topic': 'a'}, {'topic': 'b'}, {'topic': 'c'}]))
            message = check(self.write(root), Path(root) / 'h.json', call=call, now=AT(6, 17))
            self.assertIn('2026-11-12T09:00+02:00', message)
            self.assertEqual(calls[-1][2], {'ref': 'main', 'inputs': {'brief': '', 'slot': '2026-11-12T09:00+02:00'}})
            runs = [{'display_title': 'Autopilot film 2026-11-12T09:00+02:00', 'status': 'in_progress'}]
            calls.clear()
            self.assertEqual(check(self.write(root), Path(root) / 'h.json', call=call, now=AT(6, 17)), 'No post time is due.')
            self.assertEqual([c[0] for c in calls], ['GET'])
            self.assertEqual(check(self.write(root, enabled=False), Path(root) / 'h.json', call=call), 'Autopilot is off.')

    def test_failed_runs_do_not_block_a_retry(self):
        self.assertEqual(busy_slots([{'display_title': 'Autopilot film X', 'status': 'completed', 'conclusion': 'failure'},
                                     {'display_title': 'Autopilot film Y', 'status': 'completed', 'conclusion': 'success'},
                                     {'display_title': 'Film abcd', 'status': 'queued'}]), {'Y'})


class SlotFilmTests(unittest.TestCase):
    def test_slot_film_is_scheduled_and_claimed(self):
        with tempfile.TemporaryDirectory() as root:
            settings = Path(root) / 'settings.json'
            settings.write_text(json.dumps({'enabled': True, 'domain': 'Space', 'publish': True, 'privacy': 'public',
                                            'writer': 'claude', **BEIRUT}))
            topic = {'topic': 'Moon', 'angle': 'Tides', 'reason': 'r', 'brief': 'Explain tides.',
                     'evidence': [{'title': 'T', 'url': 'https://example.com', 'signal': 's'}]}
            args = af.parse_args(['--renderer', root, '--out', str(Path(root) / 'out'), '--seed', 's', '--autopilot',
                                  str(settings), '--history', str(Path(root) / 'h.json'), '--slot', '2026-11-12T09:00+02:00',
                                  '--ideas', str(Path(root) / 'ideas.json')])
            result = asyncio.run(af.create(args, author=fake_author(topic, DESIGN, search=True), review=review_result(True),
                render=fake_render(), writers=[], synth=fake_synth, probe=lambda f: 3.0, join=fake_join, now=AT(6, 17).timestamp()))
            self.assertEqual(result['state'], 'ready', result)
            self.assertEqual((result['slot'], result['publishAt']), ('2026-11-12T09:00+02:00', '2026-11-12T07:00:00Z'))
            history = json.loads((Path(root) / 'out' / 'history.json').read_text())
            self.assertEqual(history[-1]['slot'], '2026-11-12T09:00+02:00')

    def test_retry_reuses_the_film_claude_already_wrote(self):
        with tempfile.TemporaryDirectory() as root:
            settings = Path(root) / 'settings.json'
            settings.write_text(json.dumps({'enabled': True, 'domain': 'Space', 'publish': True, 'privacy': 'public', **BEIRUT}))
            previous = Path(root) / 'previous'
            previous.mkdir()
            # What the failed first try left behind (design.json also holds timing fields added later).
            (previous / 'design.json').write_text(json.dumps({**DESIGN, 'cues': [{'start': 0, 'end': 1, 'text': 'x'}],
                                                              'voiceNote': 'x'}))
            (previous / 'topic.json').write_text(json.dumps({'topic': 'Moon', 'angle': 'Tides', 'reason': 'r',
                                                             'brief': 'Explain tides.', 'evidence': []}))

            async def claude(prompt):
                raise AssertionError('Claude must not be used on a retry')
            args = af.parse_args(['--renderer', root, '--out', str(Path(root) / 'out'), '--seed', 's', '--autopilot',
                                  str(settings), '--history', str(Path(root) / 'h.json'), '--slot', '2026-11-12T09:00+02:00',
                                  '--ideas', str(Path(root) / 'ideas.json'), '--previous', str(previous)])
            result = asyncio.run(af.create(args, author=claude, review=None, render=fake_render(), writers=[],
                synth=fake_synth, probe=lambda f: 3.0, join=fake_join, now=AT(6, 17).timestamp()))
            self.assertEqual(result['state'], 'ready', result)
            self.assertEqual((result['writer'], result['brief']), ('claude', 'Explain tides.'))
            self.assertIn('reused', result['writerDetail'])
            history = json.loads((Path(root) / 'out' / 'history.json').read_text())
            self.assertEqual((history[-1]['slot'], history[-1]['topic']), ('2026-11-12T09:00+02:00', 'Moon'))

    def test_unusable_earlier_files_are_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(af.saved_film(''))
            self.assertIsNone(af.saved_film(root))
            (Path(root) / 'design.json').write_text('{"title": "x"}')
            self.assertIsNone(af.saved_film(root))


class YouTubeScheduleTests(unittest.TestCase):
    def test_scheduled_upload_is_private_with_publish_time(self):
        from youtube import YouTube
        bodies = []
        service = MagicMock()
        def insert(part, body, media_body):
            bodies.append(body)
            request = MagicMock()
            request.next_chunk.return_value = (None, {'id': 'vid1'})
            return request
        service.videos.return_value.insert.side_effect = insert
        store = {'youtube-token': {'refresh_token': 'r'}}
        class Store(dict):
            def put(self, key, value, skip_if_exists=False):
                if skip_if_exists and key in self:
                    return False
                self[key] = value
                return True
        creds = MagicMock(expired=False, valid=True)
        with tempfile.TemporaryDirectory() as root, \
                patch('google.oauth2.credentials.Credentials.from_authorized_user_info', return_value=creds), \
                patch('googleapiclient.discovery.build', return_value=service), \
                patch('googleapiclient.http.MediaFileUpload'):
            video = Path(root) / 'v.mp4'
            video.write_bytes(b'v')
            receipt = YouTube(Store(store)).upload('a' * 32, video, DESIGN, 'public', publish_at='2026-11-12T07:00:00Z')
        snippet = bodies[0]['snippet']
        self.assertTrue(snippet['description'].split('\n\n')[1].startswith('#Shorts'))
        self.assertEqual((snippet['categoryId'], snippet['defaultLanguage']), ('27', 'en'))
        self.assertEqual(bodies[0]['status']['privacyStatus'], 'private')
        self.assertEqual(bodies[0]['status']['publishAt'], '2026-11-12T07:00:00Z')
        self.assertEqual(receipt['scheduledFor'], '2026-11-12T07:00:00Z')


if __name__ == '__main__':
    unittest.main()


class IdeaRefillTests(unittest.TestCase):
    def test_low_idea_list_is_refilled_at_most_every_six_hours(self):
        from autopilot_check import refill_ideas
        calls = []
        recent = []

        def call(method, path, body=None):
            calls.append((method, path, body))
            return {'workflow_runs': recent} if method == 'GET' else None
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'ideas.json'
            path.write_text(json.dumps([{'topic': 'a'}]))
            self.assertTrue(refill_ideas(path, call, 'main', AT(6, 17).timestamp()))
            self.assertEqual(calls[-1][1], '/actions/workflows/shorts-studio-ideas.yml/dispatches')
            recent.append({'created_at': '2026-11-12T04:00:00Z', 'conclusion': 'success'})
            self.assertFalse(refill_ideas(path, call, 'main', AT(6, 17).timestamp()))
            path.write_text(json.dumps([{'topic': x} for x in 'abc']))
            recent.clear()
            self.assertFalse(refill_ideas(path, call, 'main', AT(6, 17).timestamp()))
