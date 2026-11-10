import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from production import ProductionManager, CloudProductions, RenderFailure, choose_voice, parse_design, author_prompt, render_isolated


def design():
    return {'title': 'The moving Earth', 'direction': 'An illustrated globe becomes a day/night diagram.',
        'language': 'en', 'narration': 'Earth turns, bringing each place into daylight and then night.',
        'source': "import React from 'react'; import {AbsoluteFill,useCurrentFrame} from 'remotion'; "
                  "export default function Video(){const f=useCurrentFrame();return <AbsoluteFill>" 
                  "<svg><circle r={100} cx={200+f} cy={400}/></svg></AbsoluteFill>}"}


class ProductionTests(unittest.TestCase):
    def test_cloud_job_is_spawned_and_survives_web_shutdown(self):
        from unittest.mock import Mock
        jobs={}
        worker=Mock()
        with tempfile.TemporaryDirectory() as root, patch('modal.Dict.from_name',return_value=jobs), patch('modal.Function.from_name',return_value=worker):
            cloud=CloudProductions(root,lambda:None,lambda:None)
            job=cloud.start('An eclipse',15,'auto')
            worker.spawn.assert_called_once_with(job['id'],'An eclipse',15,'auto')
            self.assertTrue(cloud.active)
            with self.assertRaises(RuntimeError):
                cloud.start('A duplicate film',15,'auto')
            asyncio.run(cloud.close())
            self.assertEqual(cloud.status(job['id'])['state'],'queued')
            jobs[job['id']].update(state='ready')
            self.assertFalse(cloud.active)

    def test_claude_authors_executable_animation_not_a_shape_timeline(self):
        result = parse_design(json.dumps(design()))
        self.assertIn('export default', result['source'])
        self.assertIn('ACTUAL ANIMATION CODE', author_prompt('Earth', 15))
        with self.assertRaises(ValueError):
            parse_design(json.dumps({**design(), 'source': 'x' * 250}))

    def test_language_selects_neural_voice_and_rejects_android_voice_ids(self):
        self.assertEqual(choose_voice('auto', 'ar-LB'), 'ar-LB-RamiNeural')
        self.assertEqual(choose_voice('auto', 'en'), 'en-US-AndrewMultilingualNeural')
        with self.assertRaises(ValueError):
            choose_voice('android-installed-voice', 'en')

    def test_finished_video_retains_natural_speech_duration_and_original_source(self):
        async def scenario(root):
            calls = []
            async def author(prompt):
                calls.append(prompt)
                return json.dumps(design())
            async def voice(text, selected, path):
                path.write_bytes(b'neural speech')
            def render(image, film, audio, seconds, output):
                self.assertEqual(seconds, 17)
                self.assertEqual(film['source'], design()['source'])
                output.write_bytes(b'valid test fixture')
            manager = ProductionManager(root, author, object(), synth=voice, render=render, probe=lambda _:16.2)
            job = manager.start('Earth', 15, 'auto')
            with self.assertRaises(RuntimeError):
                manager.start('Another film', 15, 'auto')
            await manager.task
            state = manager.status(job['id'])
            self.assertEqual(state['state'], 'ready')
            self.assertEqual((state['width'], state['height'], state['fps']), (1080, 1920, 30))
            self.assertEqual(state['voice'], 'en-US-AndrewMultilingualNeural')
            self.assertEqual(len(calls), 1)
            with self.assertRaises(FileNotFoundError):
                manager.status('../../home/.claude')
        with tempfile.TemporaryDirectory() as root:
            asyncio.run(scenario(root))

    def test_render_failure_requests_real_code_repair_preserving_recorded_narration(self):
        async def scenario(root):
            prompts, sources = [], []
            async def author(prompt):
                prompts.append(prompt)
                film = design()
                if len(prompts)>1:
                    film['source'] += '\n// repaired'
                    film['narration'] = 'A changed narration must not replace already recorded speech.'
                return json.dumps(film)
            async def voice(text, selected, path):
                path.write_bytes(b'neural speech')
            def render(image, film, audio, seconds, output):
                sources.append(film['source'])
                if len(sources)==1:
                    raise RenderFailure('TSX compile failure')
                self.assertEqual(film['narration'], design()['narration'])
                output.write_bytes(b'video')
            manager=ProductionManager(root,author,object(),synth=voice,render=render,probe=lambda _:10)
            job=manager.start('Earth',15,'auto')
            await manager.task
            self.assertEqual(manager.status(job['id'])['state'],'ready')
            self.assertIn('TSX compile failure',prompts[1])
            self.assertNotEqual(sources[0],sources[1])
        with tempfile.TemporaryDirectory() as root:
            asyncio.run(scenario(root))

    def test_voice_failure_is_visible_without_using_phone_tts(self):
        async def scenario(root):
            async def author(_):return json.dumps(design())
            async def voice(*_):raise RuntimeError('Neural voice unavailable')
            manager=ProductionManager(root,author,object(),synth=voice)
            job=manager.start('Earth',15,'auto')
            await manager.task
            state=manager.status(job['id'])
            self.assertEqual(state['state'],'failed')
            self.assertEqual(state['message'],'Neural voice unavailable')
        with tempfile.TemporaryDirectory() as root:
            asyncio.run(scenario(root))

    def test_restarted_service_marks_abandoned_work_as_interrupted(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'productions'/('a'*32)
            path.mkdir(parents=True)
            (path/'status.json').write_text(json.dumps({'id':'a'*32,'state':'rendering'}))
            manager=ProductionManager(root,None,None)
            self.assertEqual(manager.status('a'*32)['state'],'failed')

    def test_generated_code_runs_without_network_secrets_or_credential_volume(self):
        with patch('modal.Sandbox.create',side_effect=RuntimeError('intercepted')) as create:
            with patch('modal.App.lookup',return_value=object()):
                with self.assertRaisesRegex(RuntimeError,'intercepted'):
                    render_isolated(object(), design(), Path('/tmp/test.mp3'),15,Path('/tmp/test.mp4'))
        args=create.call_args.kwargs
        self.assertTrue(args['block_network'])
        self.assertEqual(args['volumes'],{})
        self.assertEqual(args['secrets'],[])


if __name__=='__main__':
    unittest.main()
