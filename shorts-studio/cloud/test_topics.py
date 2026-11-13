import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import actions_film as af
from ideas import lanes_to_fill
from slots import due_label, parse_slot, publish_at, split_slot, topic_settings, topics_of
from test_actions import DESIGN, fake_author, fake_join, fake_render, fake_synth, review_result

SETTINGS = {'enabled': True, 'timezone': 'Asia/Beirut', 'domain': 'Science', 'language': 'English', 'voice': 'auto',
            'days': [0, 1, 2, 3, 4, 5, 6], 'times': ['18:00'], 'publish': True, 'privacy': 'public',
            'topics': [
                {'id': 'science', 'name': 'Science', 'domain': 'Physics in everyday life', 'times': ['09:00'],
                 'instagram': ['17841400000000001'], 'publish': True},
                {'id': 'history', 'name': 'History', 'domain': 'History of discoveries', 'times': ['18:30'],
                 'language': 'Arabic', 'voice': 'ar-LB-RamiNeural', 'instagram': ['17841400000000002'], 'publish': False}]}
AT = lambda h, m: datetime(2026, 11, 14, h, m, tzinfo=timezone.utc)  # Beirut is UTC+2


class TopicTests(unittest.TestCase):
    def test_each_topic_has_its_own_settings(self):
        lanes = dict(topics_of(SETTINGS))
        self.assertEqual(set(lanes), {'science', 'history'})
        self.assertEqual((lanes['history']['voice'], lanes['history']['timezone']), ('ar-LB-RamiNeural', 'Asia/Beirut'))
        self.assertEqual(topic_settings(SETTINGS, 'gone'), None)
        self.assertEqual(topics_of({'domain': 'x'})[0][0], '', 'settings without topics are one topic')

    def test_the_due_post_time_names_its_topic(self):
        self.assertEqual(due_label(SETTINGS, [], AT(6, 30)), '2026-11-14T09:00+02:00#science')
        self.assertEqual(due_label(SETTINGS, [], AT(16, 0)), '2026-11-14T18:30+02:00#history')
        claimed = [{'slot': '2026-11-14T09:00+02:00#science'}]
        self.assertIsNone(due_label(SETTINGS, claimed, AT(6, 30)), 'a made post time is not made again')
        label = '2026-11-14T09:00+02:00#science'
        self.assertEqual(split_slot(label), ('2026-11-14T09:00+02:00', 'science'))
        self.assertEqual(parse_slot(label).hour, 9)
        self.assertEqual(publish_at(label, AT(6, 0)), '2026-11-14T07:00:00Z')

    def test_a_film_uses_its_topic_and_its_accounts(self):
        with tempfile.TemporaryDirectory() as root:
            settings = Path(root) / 'settings.json'
            settings.write_text(json.dumps(SETTINGS))
            ideas = Path(root) / 'ideas.json'
            ideas.write_text(json.dumps([{'id': 'a', 'topic': 'Why sky is blue', 'brief': 'Sky.', 'lane': 'science'},
                                         {'id': 'b', 'topic': 'Who invented paper', 'brief': 'Paper.', 'lane': 'history'}]))
            args = af.parse_args(['--renderer', root, '--out', str(Path(root) / 'out'), '--seed', 's', '--autopilot', str(settings),
                                  '--history', str(Path(root) / 'h.json'), '--ideas', str(ideas),
                                  '--slot', '2026-11-14T18:30+02:00#history'])
            result = asyncio.run(af.create(args, author=fake_author(DESIGN), review=review_result(True), render=fake_render(),
                writers=[], synth=fake_synth, probe=lambda f: 3.0, join=fake_join, now=AT(16, 0).timestamp()))
            self.assertEqual(result['state'], 'ready', result)
            self.assertEqual((result['ideaId'], result['brief']), ('b', 'Paper.'), 'the history idea is made')
            self.assertEqual(result['instagram'], ['17841400000000002'])
            self.assertFalse(result['publish'], 'this topic does not post to YouTube')
            self.assertEqual(result['slot'], '2026-11-14T18:30+02:00#history')

    def test_a_removed_topic_makes_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            settings = Path(root) / 'settings.json'
            settings.write_text(json.dumps(SETTINGS))
            self.assertIn('removed', af.autopilot_request(settings, Path(root) / 'h.json', False, slot='2026-11-14T09:00+02:00#gone'))

    def test_ideas_are_found_for_topics_that_run_low(self):
        existing = [{'topic': f'Idea {i}', 'lane': 'science'} for i in range(4)]
        self.assertEqual([lane for lane, _ in lanes_to_fill(SETTINGS, existing)], ['history'])
        self.assertEqual([lane for lane, _ in lanes_to_fill(SETTINGS, existing, 'science')], ['science'])
        untagged = [{'topic': f'Old {i}'} for i in range(3)]
        self.assertEqual([lane for lane, _ in lanes_to_fill(SETTINGS, untagged)], ['history'], 'untagged ideas belong to the first topic')
