import tempfile
import unittest
from datetime import date
from pathlib import Path

from personality.inner_life import HermanInnerLife


class HermanInnerLifeTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.gettempdir()) / "herman_inner_life_test.json"
        self.path.unlink(missing_ok=True)
        self.life = HermanInnerLife(self.path)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_state_is_persistent_and_does_not_advance_twice_in_one_day(self):
        first = self.life.snapshot(date(2026, 8, 14))
        second = self.life.snapshot(date(2026, 8, 14))
        self.assertEqual(first, second)
        self.assertTrue(self.path.exists())

    def test_elapsed_days_create_gradual_game_and_blog_progress(self):
        first = self.life.snapshot(date(2026, 8, 14))
        later = self.life.snapshot(date(2026, 8, 18))
        self.assertGreater(
            later["block_blast_sessions"] + later["retro_bowl_sessions"],
            first["block_blast_sessions"] + first["retro_bowl_sessions"],
        )
        self.assertGreaterEqual(later["blog_post_count"], first["blog_post_count"])

    def test_context_contains_approved_continuity_without_forcing_it(self):
        context = self.life.context(date(2026, 8, 14))
        self.assertIn("Block Blast", context)
        self.assertIn("Retro Bowl", context)
        self.assertIn("Invisible blog", context)
        self.assertIn("Mention at most one naturally", context)
