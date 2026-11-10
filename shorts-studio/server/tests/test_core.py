import tempfile
import unittest
from pathlib import Path

from studio_core import validate_plan, plan_fingerprint, safe_filename, probe_video


class PlanTests(unittest.TestCase):
    def test_normalizes_scene_shapes(self):
        result = validate_plan({
            "title": "Example",
            "narration": "A trustworthy explanation with enough words for a short video today.",
            "scenes": ["Welcome", {"headline": "Deep dive", "visual": "rings"}, "Takeaway"],
        }, "Topic", 35)
        self.assertEqual(result["scenes"][0]["headline"], "Welcome")
        self.assertEqual(result["scenes"][1]["visual"], "rings")
        self.assertEqual(result["scenes"][0]["visual_direction"], "")
        self.assertEqual(result["duration_sec"], 35)
        self.assertEqual(plan_fingerprint(result), plan_fingerprint(dict(result)))

    def test_preserves_original_two_scene_contract(self):
        plan = validate_plan({"title": "Short", "narration": "Hi",
                              "scenes": ["Start", "Finish"]})
        self.assertEqual(len(plan["scenes"]), 2)

    def test_rejects_missing_scenes(self):
        with self.assertRaises(ValueError):
            validate_plan({"narration": "More than enough words to pass narration length.",
                           "scenes": ["Only one"]})

    def test_filename(self):
        self.assertEqual(safe_filename("../../video #1"), "video-1")

    def test_probe_rejects_missing_file(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                probe_video(Path(root) / "missing.mp4")


if __name__ == "__main__":
    unittest.main()
