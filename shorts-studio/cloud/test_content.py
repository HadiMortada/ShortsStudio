import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import content
from actions_film import next_idea
from ideas import clean_ideas
from production import ProductionManager
from test_production import design


class MatchTests(unittest.TestCase):
    def test_blocked_words_in_english_and_arabic(self):
        avoid = content.DEFAULT_AVOID
        self.assertEqual(content.find('A map of Israel', avoid), ['israel'])
        self.assertEqual(content.find('خريطة إسرائيل', avoid), ['israel'])
        self.assertEqual(content.find('Beers of the world', avoid), ['alcohol'])
        self.assertEqual(content.find('الكحول', avoid), ['alcohol'])
        self.assertEqual(content.find('A drug dealer', avoid), ['drugs'])
        # Ordinary words that only contain a blocked word, and medicines, are fine.
        self.assertEqual(content.find('Strumming a guitar in the spring', avoid), [])
        self.assertEqual(content.find('Antibiotic drugs save lives', avoid), [])

    def test_owner_list_and_own_words(self):
        self.assertEqual(content.avoid_list({}), content.DEFAULT_AVOID)
        self.assertEqual(content.avoid_list({'avoid': []}), [])
        self.assertEqual(content.find('Pigs can swim', ['pig']), ['pig'])
        self.assertEqual(content.find('A drunk sailor', ['israel']), [])
        rules = content.rules_text(['israel', 'pig'])
        self.assertIn('Israel', rules)
        self.assertIn('- pig', rules)


class FilmTests(unittest.TestCase):
    def run_film(self, brief, answers, avoid=content.DEFAULT_AVOID):
        async def scenario(root):
            prompts = []

            async def author(prompt):
                prompts.append(prompt)
                return json.dumps(answers[min(len(prompts) - 1, len(answers) - 1)])

            async def voice(text, selected, path):
                path.write_bytes(b'a')

            def render(image, film, audio, seconds, output):
                output.write_bytes(b'video')
            manager = ProductionManager(root, author, object(), synth=voice, render=render, probe=lambda _: 5.0)
            job = manager.start(brief, 15, 'auto', options={'avoid': avoid})
            await manager.task
            return manager.status(job['id']), prompts
        with tempfile.TemporaryDirectory() as root:
            return asyncio.run(scenario(root))

    def test_writer_gets_the_rules_and_a_clean_film_is_made(self):
        state, prompts = self.run_film('Why is the sky blue?', [design()])
        self.assertEqual(state['state'], 'ready')
        self.assertIn('CONTENT RULES', prompts[0])
        self.assertIn('Israel', prompts[0])

    def test_a_film_that_breaks_the_rules_is_rewritten_once(self):
        bad = {**design(), 'narration': 'Earth turns while people drink beer at night.'}
        state, prompts = self.run_film('Earth at night', [bad, design()])
        self.assertEqual(state['state'], 'ready')
        self.assertEqual(len(prompts), 2)
        self.assertIn('broke the CONTENT RULES (alcohol', prompts[1])

    def test_a_film_that_still_breaks_the_rules_is_stopped(self):
        bad = {**design(), 'title': 'Israel at night'}
        state, prompts = self.run_film('Earth at night', [bad])
        self.assertEqual(state['state'], 'failed')
        self.assertIn('Never in my films', state['message'])
        self.assertIn('Israel', state['message'])

    def test_a_blocked_idea_never_reaches_the_writer(self):
        state, prompts = self.run_film('The history of whisky', [design()])
        self.assertEqual(state['state'], 'failed')
        self.assertEqual(prompts, [])


class IdeaTests(unittest.TestCase):
    def test_blocked_ideas_are_dropped_and_skipped(self):
        raw = {'ideas': [{'topic': t, 'brief': t} for t in
                         ('Why cats purr', 'How wine is made', 'Why the sky is blue', 'How bees dance')]}
        topics = [x['topic'] for x in clean_ideas(raw, 'claude', content.DEFAULT_AVOID)]
        self.assertEqual(topics, ['Why cats purr', 'Why the sky is blue', 'How bees dance'])
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'ideas.json'
            path.write_text(json.dumps([{'topic': 'Casino tricks'}, {'topic': 'Why cats purr'}]))
            self.assertEqual(next_idea(path, content.DEFAULT_AVOID)['topic'], 'Why cats purr')
            self.assertEqual(next_idea(path)['topic'], 'Casino tricks')
