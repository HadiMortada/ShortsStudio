import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import actions_film as af
import ideas
from actions_deliver import deliver
from test_actions import DESIGN, FakeContents, fake_author, fake_join, fake_render, fake_synth, review_result

RAW = {'ideas': [{'topic': f'Idea {i}', 'angle': 'hook', 'brief': f'Explain idea {i}.', 'reason': 'curious',
                  'score': s, 'evidence': [{'title': 'T', 'url': 'https://example.com'}, {'title': 'bad', 'url': 'http://x'}]}
                 for i, s in enumerate([40, 90, 70])]}


class Writer:
    name = 'Fake'
    def __init__(self, reply):
        self.reply, self.prompts = reply, []
    def complete(self, prompt):
        self.prompts.append(prompt)
        return self.reply, 'm'


class IdeaTests(unittest.TestCase):
    def test_ideas_are_cleaned_and_merged_after_the_owners_list(self):
        new = ideas.clean_ideas(RAW, 'claude')
        self.assertEqual(len(new[0]['evidence']), 1)
        existing = [{'id': 'mine', 'topic': 'My own topic'}, {'id': 'x', 'topic': 'Idea 2'}]
        merged = ideas.merge(existing, new)
        self.assertEqual([x['topic'] for x in merged], ['My own topic', 'Idea 2', 'Idea 1', 'Idea 0'])
        with self.assertRaises(ValueError):
            ideas.clean_ideas({'ideas': [{'topic': 'only one'}]}, 'claude')

    def test_prompt_uses_subject_history_and_queue(self):
        prompt = ideas.idea_prompt({'domain': 'Space', 'language': 'Arabic'}, [{'topic': 'Eclipses'}], [{'topic': 'Comets'}])
        for part in ('Space', 'Arabic', 'Eclipses', 'Comets', 'WebSearch'):
            self.assertIn(part, prompt)

    def test_backup_suggests_ideas_without_web_research(self):
        writer = Writer(json.dumps(RAW))
        result = ideas.backup_ideas({'domain': 'Space'}, [], [], writers=[writer])
        self.assertEqual(result[0]['source'], 'backup')
        self.assertNotIn('Use WebSearch', writer.prompts[0])

    def test_claude_ideas_use_the_cli(self):
        import os
        from unittest.mock import patch

        async def run(args, **kw):
            return 0, json.dumps({'type': 'result', 'result': json.dumps(RAW)}), ''
        with patch.dict(os.environ, {'STUDIO_AI': 'claude', 'CLAUDE_CODE_OAUTH_TOKEN': 't'}), \
                patch('shutil.which', return_value='/bin/claude'):
            result = asyncio.run(ideas.claude_ideas({}, [], [], run=run))
        self.assertEqual([x['source'] for x in result], ['claude'] * 3)


class IdeaFilmTests(unittest.TestCase):
    def setUp(self):
        usage = patch('actions_film.require_usage', return_value={'sessionPercent': 20, 'weeklyPercent': 30})
        usage.start()
        self.addCleanup(usage.stop)

    def test_autopilot_makes_the_top_idea_and_delivery_removes_it(self):
        with tempfile.TemporaryDirectory() as root:
            settings = Path(root) / 'settings.json'
            settings.write_text(json.dumps({'enabled': True, 'domain': 'Space'}))
            queue = Path(root) / 'ideas.json'
            queue.write_text(json.dumps([{'id': 'top1', 'topic': 'Why is Mars red?', 'brief': 'Explain why Mars is red.'},
                                         {'id': 'next', 'topic': 'Comets'}]))
            author = fake_author(DESIGN)
            args = af.parse_args(['--renderer', root, '--out', str(Path(root) / 'out'), '--seed', 'i',
                                  '--autopilot', str(settings), '--history', str(Path(root) / 'h.json'),
                                  '--ideas', str(queue), '--scheduled'])
            result = asyncio.run(af.create(args, author=author, review=review_result(True), render=fake_render(),
                                           writers=[], synth=fake_synth, probe=lambda f: 3.0, join=fake_join))
            self.assertEqual((result['state'], result['ideaId']), ('ready', 'top1'))
            self.assertIn('Explain why Mars is red.', author.prompts[0])
            self.assertNotIn('Choose ONE fresh short-video topic', author.prompts[0])
            history = json.loads((Path(root) / 'out' / 'history.json').read_text())
            self.assertEqual(history[-1]['topic'], 'Why is Mars red?')
            api = FakeContents()
            api.files['shorts-studio/autopilot/ideas.json'] = ('s', queue.read_bytes())
            api.files['shorts-studio/autopilot/history.json'] = ('h', b'[]')
            deliver(Path(root) / 'out', api=api, source={})
            self.assertEqual([x['id'] for x in api.json('shorts-studio/autopilot/ideas.json')], ['next'])


    def test_claude_picks_the_idea_the_numbers_favour_and_weak_ones_are_deleted(self):
        with tempfile.TemporaryDirectory() as root:
            settings = Path(root) / 'settings.json'
            settings.write_text(json.dumps({'enabled': True, 'domain': 'Space'}))
            queue = Path(root) / 'ideas.json'
            queue.write_text(json.dumps([{'id': 'top1', 'topic': 'Black hole maths', 'brief': 'Explain black hole maths.'},
                                         {'id': 'best', 'topic': 'Why is Mars red?', 'brief': 'Explain why Mars is red.'},
                                         {'id': 'mine', 'topic': 'My own idea', 'source': 'you'},
                                         {'id': 'keep', 'topic': 'Comets'}]))
            views = Path(root) / 'views.json'
            views.write_text(json.dumps({'top': [{'title': 'Why Mars is red', 'views': 3_000_000, 'channel': 'c'}]}))
            prompts = []

            async def chooser(prompt):
                prompts.append(prompt)
                return json.dumps({'pick': 'best', 'why': 'Mars questions get millions of views.',
                                   'remove': [{'id': 'top1', 'why': 'Maths topics got 40 views/day here'},
                                              {'id': 'mine', 'why': 'weak'}, {'id': 'best', 'why': 'x'}, {'id': 'nope'}]})
            author = fake_author(DESIGN)
            args = af.parse_args(['--renderer', root, '--out', str(Path(root) / 'out'), '--seed', 'i',
                                  '--autopilot', str(settings), '--history', str(Path(root) / 'h.json'),
                                  '--ideas', str(queue), '--views', str(views), '--scheduled'])
            result = asyncio.run(af.create(args, author=author, review=review_result(True), render=fake_render(), chooser=chooser,
                                           writers=[], synth=fake_synth, probe=lambda f: 3.0, join=fake_join))
            self.assertIn('3.0M views: "Why Mars is red"', prompts[0])
            self.assertIn('"addedBy": "owner"', prompts[0])
            self.assertEqual((result['state'], result['ideaId']), ('ready', 'best'))
            self.assertIn('Explain why Mars is red.', author.prompts[0])
            self.assertEqual([x['id'] for x in result['ideasRemoved']], ['top1'])  # never the owner's idea or the pick
            self.assertEqual(result['ideaChoice'], 'Mars questions get millions of views.')
            api = FakeContents()
            api.files['shorts-studio/autopilot/ideas.json'] = ('s', queue.read_bytes())
            api.files['shorts-studio/autopilot/history.json'] = ('h', b'[]')
            deliver(Path(root) / 'out', api=api, source={})
            self.assertEqual([x['id'] for x in api.json('shorts-studio/autopilot/ideas.json')], ['mine', 'keep'])

    def test_without_evidence_or_when_the_review_fails_the_top_idea_is_made(self):
        pool = [{'id': 'a', 'topic': 'A'}, {'id': 'b', 'topic': 'B'}]

        async def broken(prompt):
            raise RuntimeError('no')
        with patch('analytics.evidence_text', return_value=''):
            self.assertIsNone(asyncio.run(af.choose_idea(broken, pool, {})))
        with patch('analytics.evidence_text', return_value='LESSONS'):
            self.assertIsNone(asyncio.run(af.choose_idea(broken, pool, {})))

            async def stranger(prompt):
                return '{"pick": "zzz", "remove": []}'
            self.assertIsNone(asyncio.run(af.choose_idea(stranger, pool, {})))

            async def none_good(prompt):
                return '{"pick": null, "why": "All weak", "remove": [{"id": "a", "why": "few views"}, {"id": "b", "why": "few"}]}'
            idea, removed, why = asyncio.run(af.choose_idea(none_good, pool, {}))
            self.assertIsNone(idea)
            self.assertEqual([x['id'] for x in removed], ['a', 'b'])

            async def lazy(prompt):
                return '{"pick": null}'
            self.assertEqual(asyncio.run(af.choose_idea(lazy, pool, {}))[0]['id'], 'a')


if __name__ == '__main__':
    unittest.main()
