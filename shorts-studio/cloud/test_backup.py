import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.error import HTTPError

import actions_film as af
import backup
from production import parse_design
from test_actions import DESIGN, fake_author, fake_join, fake_render, fake_synth, review_result

PLAN = {'title': 'How tides work', 'language': 'en', 'palette': 'ocean',
        'description': 'The Moon and the oceans.',
        'narration': 'The Moon pulls on the oceans. That pull makes a bulge of water. Earth turns under it twice a day.',
        'scenes': [{'type': 'hook', 'title': 'Why does the sea breathe?', 'icon': '🌊'},
                   {'type': 'number', 'value': '384,400', 'unit': 'km', 'title': 'Distance to the Moon'},
                   {'type': 'bars', 'title': 'Pull', 'items': [{'label': 'Moon', 'value': 100}, {'label': 'Sun', 'value': 46}]},
                   {'type': 'outro', 'title': 'Watch the tide tonight'}]}


class Writer:
    name = 'Fake'

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def complete(self, prompt):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply, 'fake-model'


class PlanTests(unittest.TestCase):
    def test_plan_is_normalized_and_bounded(self):
        plan = backup.normalize_plan(PLAN, 'tides', 30)
        self.assertEqual([s['type'] for s in plan['scenes']], ['hook', 'number', 'bars', 'outro'])
        self.assertEqual(plan['scenes'][1]['value'], 384400)
        self.assertEqual(plan['palette'], 'ocean')

    def test_scenes_missing_data_become_simpler_scenes(self):
        raw = {**PLAN, 'palette': 'neon', 'language': '??', 'scenes': [
            {'type': 'number', 'title': 'No value'}, {'type': 'donut', 'value': 150, 'title': 'Too big'},
            {'type': 'bars', 'title': 'Labels only', 'items': ['a', 'b']}, {'type': 'orbit', 'title': 'x', 'items': ['a']},
            {'type': 'compare', 'title': 'Three', 'items': ['a', 'b', 'c']}, {'type': 'unknown', 'title': 'Odd'},
            {'type': 'list', 'title': ''}, 'not a scene']}
        plan = backup.normalize_plan(raw, 'brief', 30)
        self.assertEqual([s['type'] for s in plan['scenes']],
                         ['statement', 'statement', 'list', 'statement', 'compare', 'statement'])
        self.assertEqual(len(plan['scenes'][4]['items']), 2)
        self.assertIn(plan['palette'], backup.PALETTES)
        self.assertEqual(plan['language'], 'en')

    def test_narration_is_trimmed_to_the_word_budget(self):
        raw = {**PLAN, 'narration': 'Word. ' * 200}
        plan = backup.normalize_plan(raw, 'b', 15)
        self.assertLessEqual(len(plan['narration'].split()), backup.word_limit(15, 'en'))

    def test_unusable_plans_are_rejected(self):
        for raw in ([], {**PLAN, 'scenes': PLAN['scenes'][:2]}, {**PLAN, 'narration': 'Too short'}):
            with self.assertRaises(ValueError):
                backup.normalize_plan(raw, 'b', 30)

    def test_design_embeds_plan_in_template_and_passes_validation(self):
        design = backup.design_from_plan(backup.normalize_plan(PLAN, 'b', 30), 'Fake (m)')
        self.assertNotIn('__PLAN__', design['source'])
        self.assertIn('"How tides work"', design['source'])
        self.assertIn('export default function Film', design['source'])
        self.assertEqual(parse_design(json.dumps(design))['narration'], PLAN['narration'])

    def test_template_has_one_plan_placeholder(self):
        self.assertEqual(backup.TEMPLATE.read_text().count('__PLAN__'), 1)

    def test_brief_plan_needs_no_ai(self):
        plan = backup.brief_plan('Volcanoes form where plates meet. Magma rises through cracks. '
                                 'Pressure builds until it erupts. Ash can circle the planet.', 30)
        self.assertEqual(plan['scenes'][0]['type'], 'hook')
        self.assertEqual(plan['scenes'][-1]['type'], 'outro')
        arabic = backup.brief_plan('البراكين تتشكل حيث تلتقي الصفائح. ترتفع الصهارة عبر الشقوق. يتراكم الضغط حتى يحدث الانفجار.', 30)
        self.assertEqual(arabic['language'], 'ar')
        with self.assertRaises(ValueError):
            backup.brief_plan('Too short', 30)


class WriterTests(unittest.TestCase):
    def test_github_models_tries_each_model(self):
        calls = []

        def post(url, body, headers, timeout):
            calls.append(body['model'])
            if len(calls) == 1:
                raise HTTPError(url, 429, 'rate limited', {}, None)
            self.assertEqual(headers['Authorization'], 'Bearer t')
            return {'choices': [{'message': {'content': '{"a":1}'}}]}
        content, model = backup.GitHubModels('t', post=post).complete('p')
        self.assertEqual((content, model), ('{"a":1}', backup.GITHUB_MODELS[1]))
        with self.assertRaises(backup.WriterUnavailable):
            backup.GitHubModels('').complete('p')

    def test_ask_retries_bad_output_then_moves_on(self):
        first = Writer('not json', '{"also": "bad"}')
        second = Writer(backup.WriterUnavailable('down'))
        third = Writer(json.dumps(PLAN))
        plan, used = backup.ask([first, second, third], 'p', lambda raw: backup.normalize_plan(raw, 'b', 30))
        self.assertEqual(plan['title'], 'How tides work')
        self.assertEqual(used, 'Fake (fake-model)')
        self.assertEqual((len(first.prompts), len(second.prompts)), (2, 1))
        with self.assertRaises(backup.WriterUnavailable):
            backup.ask([], 'p', dict)

    def test_backup_author_plans_once(self):
        writer = Writer('```json\n' + json.dumps(PLAN) + '\n```')
        author = backup.BackupAuthor('tides', 30, [writer])
        first = asyncio.run(author('prompt'))
        self.assertEqual(asyncio.run(author('repair this')), first)
        self.assertEqual(len(writer.prompts), 1)
        self.assertIn('tides', writer.prompts[0])

    def test_backup_author_without_writers_uses_brief_or_explains(self):
        long_brief = 'Bees dance to share directions. The angle points to flowers. The length shows distance.'
        design = json.loads(asyncio.run(backup.BackupAuthor(long_brief, 20, [])('p')))
        self.assertIn('no AI', design['direction'])
        with self.assertRaisesRegex(RuntimeError, 'too short'):
            asyncio.run(backup.BackupAuthor('Bees', 20, [])('p'))
        with self.assertRaises(backup.WriterUnavailable):
            asyncio.run(backup.BackupAuthor(long_brief, 20, [], allow_brief_plan=False)('p'))

    def test_topic_choice_validates(self):
        writer = Writer('{"topic": ""}', '{"topic": "Leap years", "brief": "Explain leap years."}')
        topic, _ = backup.choose_topic([writer], {'domain': 'Calendars'}, [{'topic': 'Eclipses'}])
        self.assertEqual(topic['topic'], 'Leap years')
        self.assertIn('Eclipses', writer.prompts[0])


class FallbackTests(unittest.TestCase):
    def setUp(self):
        usage = patch('actions_film.require_usage', return_value={'sessionPercent': 20, 'weeklyPercent': 30})
        usage.start()
        self.addCleanup(usage.stop)

    def args(self, root, *extra):
        # Never read the owner's real Up next list from the repository.
        return af.parse_args(['--renderer', root, '--out', str(Path(root) / 'out'), '--ideas', str(Path(root) / 'ideas.json'), '--seed', 'run-2', *extra])

    def run_create(self, root, *extra, author=None, writers=None, review=None):
        return asyncio.run(af.create(self.args(root, *extra), author=author, review=review or review_result(True),
            render=fake_render(), writers=writers if writers is not None else [Writer(json.dumps(PLAN))],
            synth=fake_synth, probe=lambda f: 3.0, join=fake_join))

    def test_claude_failure_switches_to_backup(self):
        async def exhausted(prompt):
            raise RuntimeError('Claude could not create the film. Check your subscription usage limits.')
        exhausted.audit = {}
        with tempfile.TemporaryDirectory() as root:
            result = self.run_create(root, '--brief', 'Tides', '--publish', author=exhausted)
            self.assertEqual((result['state'], result['writer']), ('ready', 'backup'))
            self.assertIn('usage limits', result['fallbackReason'])
            self.assertEqual(result['claudeIssue'], '')
            self.assertEqual(result['writerDetail'], 'Fake (fake-model)')
            self.assertTrue(result['publish'] and result['qualityPassed'])
            self.assertEqual(result['title'], 'How tides work')

    def test_expired_sign_in_is_reported_for_the_page(self):
        async def run(args, **kw):
            return 1, '', 'Error: OAuth token has expired'
        with tempfile.TemporaryDirectory() as root:
            result = self.run_create(root, '--brief', 'Tides', author=af.make_author('claude', {}, 'auto', run=run))
            self.assertEqual((result['writer'], result['claudeIssue']), ('backup', 'signin'))
            self.assertIn('Renew', result['fallbackReason'])

    def test_claude_success_does_not_use_backup(self):
        with tempfile.TemporaryDirectory() as root:
            writer = Writer()
            result = self.run_create(root, '--brief', 'Day', author=fake_author(DESIGN), writers=[writer])
            self.assertEqual(result['writer'], 'claude')
            self.assertEqual(writer.prompts, [])

    def test_missing_token_uses_backup_and_claude_only_refuses(self):
        with tempfile.TemporaryDirectory() as root:
            result = self.run_create(root, '--brief', 'Tides')
            self.assertEqual(result['writer'], 'backup')
            self.assertIn('not configured', result['fallbackReason'])
            self.assertEqual(result['claudeIssue'], 'signin')
            result = self.run_create(root, '--brief', 'Tides', '--writer', 'claude')
            self.assertEqual((result['state'], result['claudeIssue']), ('failed', 'signin'))
            self.assertIn('Sign in with Claude', result['message'])

    def test_backup_only_skips_claude(self):
        with tempfile.TemporaryDirectory() as root:
            claude = fake_author(DESIGN)
            result = self.run_create(root, '--brief', 'Tides', '--writer', 'backup', author=claude)
            self.assertEqual(result['writer'], 'backup')
            self.assertEqual(claude.prompts, [])

    def test_autopilot_backup_picks_topic_and_gates_publishing(self):
        with tempfile.TemporaryDirectory() as root:
            settings = Path(root) / 'settings.json'
            history = Path(root) / 'history.json'
            for publish_backup in (False, True):
                settings.write_text(json.dumps({'enabled': True, 'domain': 'Oceans', 'publish': True,
                                                'publishBackup': publish_backup}))
                writer = Writer('{"topic": "Tides", "angle": "Moon", "brief": "Explain tides."}', json.dumps(PLAN))
                result = self.run_create(root, '--autopilot', str(settings), '--history', str(history),
                                         '--scheduled', writers=[writer])
                self.assertEqual((result['state'], result['writer']), ('ready', 'backup'))
                self.assertEqual(result['publish'], publish_backup)
                self.assertEqual(bool(result['heldReason']), not publish_backup)
                self.assertIn('Explain tides.', writer.prompts[1])
                saved = json.loads((Path(root) / 'out' / 'history.json').read_text())
                self.assertEqual(saved[-1]['writer'], 'backup')
                self.assertEqual(saved[-1]['topic'], 'Tides')

    def test_autopilot_follows_the_writer_setting(self):
        with tempfile.TemporaryDirectory() as root:
            settings = Path(root) / 'settings.json'
            settings.write_text(json.dumps({'enabled': True, 'domain': 'Oceans', 'writer': 'claude'}))
            writer = Writer()
            result = self.run_create(root, '--autopilot', str(settings), '--history', str(Path(root) / 'h.json'),
                                     '--scheduled', writers=[writer])
            self.assertEqual(result['state'], 'failed')
            self.assertEqual(writer.prompts, [])
            settings.write_text(json.dumps({'enabled': True, 'domain': 'Oceans', 'writer': 'backup'}))
            claude = fake_author(DESIGN)
            writer = Writer('{"topic": "Tides", "brief": "Explain tides."}', json.dumps(PLAN))
            result = self.run_create(root, '--autopilot', str(settings), '--history', str(Path(root) / 'h.json'),
                                     '--scheduled', author=claude, writers=[writer])
            self.assertEqual((result['state'], result['writer']), ('ready', 'backup'))
            self.assertEqual(claude.prompts, [])

    def test_autopilot_without_any_writer_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as root:
            settings = Path(root) / 'settings.json'
            settings.write_text(json.dumps({'enabled': True, 'domain': 'Oceans'}))
            result = self.run_create(root, '--autopilot', str(settings), '--history', str(Path(root) / 'h.json'),
                                     writers=[])
            self.assertEqual(result['state'], 'failed')
            self.assertIn('choose a topic', result['message'])


if __name__ == '__main__':
    unittest.main()


class BackupSyncTests(unittest.TestCase):
    def test_scene_lines_become_beats(self):
        raw = {**PLAN, 'narration': '', 'scenes': [
            {'type': 'hook', 'title': 'Why does the sea breathe?', 'say': 'Have you noticed the sea rising and falling?'},
            {'type': 'statement', 'title': 'The Moon pulls', 'say': 'The Moon pulls on every ocean.'},
            {'type': 'outro', 'title': 'Watch tonight', 'say': 'Watch the tide tonight and say thanks.'}]}
        plan = backup.normalize_plan(raw, 'tides', 30)
        self.assertEqual(plan['beats'], [s['say'] for s in raw['scenes']])
        self.assertEqual(plan['narration'], ' '.join(plan['beats']))
        self.assertNotIn('say', plan['scenes'][0])
        self.assertEqual(backup.design_from_plan(plan, 'x')['beats'], plan['beats'])

    def test_plans_without_lines_get_one_beat_per_scene(self):
        plan = backup.normalize_plan(PLAN, 'tides', 30)
        self.assertEqual(len(plan['beats']), len(plan['scenes']))

    def test_template_follows_cues(self):
        source = backup.TEMPLATE.read_text()
        self.assertIn('cues.length === scenes.length', source)
        self.assertIn('cues![i].start * fps', source)
