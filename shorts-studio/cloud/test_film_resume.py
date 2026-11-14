import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import actions_film as af
from claude_usage import UsageBlocked
from film_resume import clean_request, restore_request, save_request
from test_actions import DESIGN, fake_author, fake_join, fake_render, fake_synth, review_result


class ResumeTests(unittest.TestCase):
    def test_request_whitelists_options_and_preserves_original_choices(self):
        with tempfile.TemporaryDirectory() as root:
            save_request(root, {'brief': 'Original full brief', 'seconds': 45, 'writer': 'claude',
                'publish': True, 'privacy': 'unlisted', 'instagram': '123456789', 'token': 'secret', 'weeklyUsageLimit': 100})
            args = af.parse_args(['--renderer', root, '--out', root + '/out', '--brief', 'Changed brief',
                '--resume', '--previous', root])
            restore_request(args)
            self.assertEqual((args.brief, args.seconds, args.writer, args.publish, args.privacy),
                             ('Original full brief', 45, 'claude', True, 'unlisted'))
            request = json.loads(Path(root, 'request.json').read_text())
            self.assertNotIn('token', request)
            self.assertNotIn('weeklyUsageLimit', request, 'current limits must never be restored from an old run')
        for seconds in (True, 10, 70, 30.5, '30.5'):
            with self.assertRaises(ValueError):
                clean_request({'seconds': seconds})

    def test_saved_manual_design_skips_claude_and_keeps_the_original_request(self):
        with tempfile.TemporaryDirectory() as root:
            previous = Path(root, 'previous'); previous.mkdir()
            (previous / 'design.json').write_text(json.dumps(DESIGN))
            save_request(previous, {'brief': 'Original science film', 'seconds': 30, 'writer': 'claude', 'publish': True})
            settings = Path(root, 'settings.json'); settings.write_text(json.dumps({'sound': False, 'sessionUsageLimit': 65, 'weeklyUsageLimit': 97}))
            args = af.parse_args(['--renderer', root, '--out', root + '/out', '--autopilot', str(settings),
                '--brief', 'Wrong new brief', '--resume', '--previous', str(previous)])
            author = fake_author()
            with patch('actions_film.require_usage', return_value={'sessionPercent': 1, 'weeklyPercent': 94}) as guard:
                result = asyncio.run(af.create(args, author=author, review=review_result(True), render=fake_render(),
                    synth=fake_synth, probe=lambda f: 3., join=fake_join, writers=[]))
            self.assertEqual(result['state'], 'ready', result)
            self.assertEqual(result['title'], DESIGN['title'])
            self.assertEqual(result['brief'], 'Original science film')
            self.assertTrue(result['publish'])
            self.assertEqual(author.prompts, [])
            self.assertEqual(guard.call_args.kwargs['limits']['weeklyUsageLimit'], 97)

    def test_resume_never_bypasses_a_fresh_usage_failure(self):
        with tempfile.TemporaryDirectory() as root:
            previous = Path(root, 'previous'); previous.mkdir()
            (previous / 'design.json').write_text(json.dumps(DESIGN))
            args = af.parse_args(['--renderer', root, '--out', root + '/out', '--brief', 'Science',
                '--resume', '--previous', str(previous)])
            render = fake_render()
            with patch('actions_film.require_usage', side_effect=UsageBlocked('Paused by current usage limits')):
                result = asyncio.run(af.create(args, author=fake_author(), review=None, render=render, writers=[]))
            self.assertEqual(result['state'], 'failed')
            self.assertEqual(render.calls, 0)

    def test_legacy_failure_without_files_can_retry_the_brief(self):
        with tempfile.TemporaryDirectory() as root:
            args = af.parse_args(['--renderer', root, '--out', root + '/out', '--brief', 'Legacy brief',
                '--resume', '--previous', root + '/missing'])
            restore_request(args)
            self.assertEqual(args.brief, 'Legacy brief')

    def test_missing_legacy_request_never_switches_to_an_unrelated_autopilot_film(self):
        from film_resume import capture_request
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, 'Original brief unavailable'):
                capture_request({'resume_run': '42'}, root)
            Path(root, 'design.json').write_text(json.dumps(DESIGN))
            self.assertEqual(capture_request({'resume_run': '42'}, root)['brief'], DESIGN['title'])

    def test_design_checkpoint_survives_a_cancelled_production_task(self):
        async def scenario(root):
            class PausedManager:
                def __init__(self, folder, *args, **kwargs):
                    self.root = Path(folder)
                def start(self, *args, job_id, **kwargs):
                    async def produce():
                        folder = self.root / job_id; folder.mkdir(parents=True)
                        (folder / 'design.json').write_text(json.dumps(DESIGN))
                        await asyncio.Event().wait()
                    self.task = asyncio.create_task(produce())
            out = Path(root, 'out'); out.mkdir()
            with patch('actions_film.ProductionManager', PausedManager):
                task = asyncio.create_task(af.produce(Path(root, 'work'), None, None, None, 'Science', 30,
                    'auto', 'job', {}, None, out, {}))
                await asyncio.sleep(2.1)
                self.assertEqual(json.loads((out / 'design.json').read_text())['title'], DESIGN['title'])
                self.assertFalse(task.done())
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        with tempfile.TemporaryDirectory() as root:
            asyncio.run(scenario(root))
