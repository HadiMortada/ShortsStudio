import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import actions_film as af
import ideas
from actions_deliver import deliver
from test_actions import DESIGN, TOPIC, FakeContents, fake_author, fake_join, fake_render, fake_synth, review_result

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
            study = asyncio.run(af.choose_idea(none_good, pool, {}))
            self.assertIsNone(study['idea'])
            self.assertEqual([x['id'] for x in study['removed']], ['a', 'b'])

            async def lazy(prompt):
                return '{"pick": null}'
            self.assertEqual(asyncio.run(af.choose_idea(lazy, pool, {}))['idea']['id'], 'a')

            # The AI is unavailable: the free backup writer studies the numbers instead.
            class Backup:
                name = 'GitHub Models'
                def complete(self, prompt):
                    return json.dumps({'pick': 'b', 'why': 'B gets views', 'fixes': ['Shorter opening'], 'remove': []}), 'gpt'
            study = asyncio.run(af.choose_idea(broken, pool, {}, writers=[Backup()]))
            self.assertEqual((study['idea']['id'], study['by'], study['fixes']), ('b', 'the free backup writer', ['Shorter opening']))

    def test_before_every_film_the_latest_numbers_are_studied_and_the_fixes_reach_the_writer(self):
        numbers = {'at': 1, 'problem': '', 'rows': [
            {'title': 'Why ice floats', 'views': 1600, 'viewsPerDay': 365.0, 'likes': 40, 'comments': 3, 'ageDays': 4.5,
             'seconds': 34, 'postedDay': 'Thursday', 'postedHour': '09:00', 'hook': 'Ice should sink.'},
            {'title': 'Leonid meteors', 'views': 400, 'viewsPerDay': 63.0, 'likes': 2, 'comments': 0, 'ageDays': 6.4,
             'seconds': 27, 'postedDay': 'Friday', 'postedHour': '19:00', 'stillWatching': {'afterHook': 0.41}}]}
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'numbers.json'
            path.write_text(json.dumps(numbers))
            self.assertEqual(af.load_numbers(str(path))['rows'][0]['views'], 1600)
            self.assertIsNone(af.load_numbers(''))
            prompts = []

            async def chooser(prompt):
                prompts.append(prompt)
                return json.dumps({'mistakes': ['Space topics: 63 views/day vs 365'], 'fixes': ['Open on the surprising result'],
                                   'pick': None, 'remove': []})
            chooser.ai_name = 'ChatGPT'
            author = fake_author(DESIGN)
            args = af.parse_args(['--renderer', root, '--out', str(Path(root) / 'out'), '--seed', 'i', '--brief', 'Why do cats purr?',
                                  '--ideas', str(Path(root) / 'none.json'), '--numbers', str(path)])
            result = asyncio.run(af.create(args, author=author, review=review_result(True), render=fake_render(), chooser=chooser,
                                           writers=[], synth=fake_synth, probe=lambda f: 3.0, join=fake_join))
            self.assertEqual(result['state'], 'ready')
            # The owner's own film: its topic stays, the review only gives fixes.
            self.assertIn('Do not change the topic', prompts[0])
            self.assertIn('typical film: 214.0 views/day', prompts[0])
            self.assertIn('41% stayed past the start', prompts[0])
            self.assertIn('Opening line: "Ice should sink."', prompts[0])
            self.assertIn("BEFORE THIS FILM, CHATGPT STUDIED THE CHANNEL'S LATEST NUMBERS", author.prompts[0])
            self.assertIn('- Open on the surprising result', author.prompts[0])
            self.assertIn('Why do cats purr?', author.prompts[0])
            self.assertEqual(result['review'], {'by': 'ChatGPT', 'mistakes': ['Space topics: 63 views/day vs 365'],
                                                'fixes': ['Open on the surprising result'], 'direction': ''})
            self.assertIn('Fix: Open on the surprising result', af.summary_lines({'state': 'ready'}, {}, {}, result))

    def test_the_ai_can_drop_the_whole_list_and_research_a_different_kind_of_topic(self):
        with tempfile.TemporaryDirectory() as root:
            settings = Path(root) / 'settings.json'
            settings.write_text(json.dumps({'enabled': True, 'domain': 'Space'}))
            queue = Path(root) / 'ideas.json'
            queue.write_text(json.dumps([{'id': 'm', 'topic': 'Leonid meteors'}]))
            views = Path(root) / 'views.json'
            views.write_text(json.dumps({'top': [{'title': 'Why Mars is red', 'views': 3_000_000, 'channel': 'c'}]}))

            async def chooser(prompt):
                return json.dumps({'pick': None, 'remove': [{'id': 'm', 'why': 'Meteor films got 63 views/day'}],
                                   'direction': 'Everyday things that behave differently in space', 'fixes': []})
            author = fake_author(TOPIC, DESIGN, search=True)
            args = af.parse_args(['--renderer', root, '--out', str(Path(root) / 'out'), '--seed', 'i',
                                  '--autopilot', str(settings), '--history', str(Path(root) / 'h.json'),
                                  '--ideas', str(queue), '--views', str(views), '--scheduled'])
            result = asyncio.run(af.create(args, author=author, review=review_result(True), render=fake_render(), chooser=chooser,
                                           writers=[], synth=fake_synth, probe=lambda f: 3.0, join=fake_join))
            self.assertEqual(result['state'], 'ready', result)
            self.assertIsNone(result['ideaId'])
            self.assertEqual([x['id'] for x in result['ideasRemoved']], ['m'])
            self.assertIn('Choose ONE fresh short-video topic', author.prompts[0])
            self.assertIn('If you choose the topic: Everyday things that behave differently in space', author.prompts[0])


if __name__ == '__main__':
    unittest.main()
