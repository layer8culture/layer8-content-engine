from __future__ import annotations

import unittest

from scripts.build_homepage_refinement import (
    FLIGHT_PAYLOADS,
    CLIENT_COPY,
    FLIGHT_SPLIT_COPY,
    HOMEPAGE_CHUNK,
    PREVIEW_MARKERS,
    SERVER_COPY,
    transform_baseline,
)
from scripts.build_visual_guides import baseline


class HomepageRefinementTests(unittest.TestCase):
    def test_copy_transform_updates_homepage_and_flight_payloads_idempotently(self):
        for relative in ["index.html", *sorted(FLIGHT_PAYLOADS)]:
            with self.subTest(relative=relative):
                original = baseline(f"site/{relative}").encode("utf-8")
                transformed = transform_baseline(relative, original)
                self.assertEqual(transform_baseline(relative, transformed), transformed)
                text = transformed.decode("utf-8")
                for _, approved_copy in SERVER_COPY:
                    self.assertIn(approved_copy, text)
                for marker in PREVIEW_MARKERS:
                    self.assertNotIn(marker, text)

    def test_approved_copy_preserves_real_case_study_disclosure(self):
        homepage = transform_baseline(
            "index.html", baseline("site/index.html").encode("utf-8")
        ).decode("utf-8")
        self.assertIn("projected savings are not a guarantee", homepage)
        self.assertIn("alongside the owner", homepage)
        self.assertNotIn("projected savings are guaranteed", homepage.lower())
        for _, approved_copy in CLIENT_COPY:
            self.assertIn(approved_copy, homepage)
        for stale_fragment, _ in FLIGHT_SPLIT_COPY:
            self.assertNotIn(stale_fragment, homepage)
        chunk = transform_baseline(
            HOMEPAGE_CHUNK, baseline(f"site/{HOMEPAGE_CHUNK}").encode("utf-8")
        ).decode("utf-8")
        self.assertIn("Explore the guides", chunk)
        self.assertIn("Watch on YouTube", chunk)


if __name__ == "__main__":
    unittest.main()
