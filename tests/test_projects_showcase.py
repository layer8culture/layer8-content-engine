from __future__ import annotations

import unittest
from pathlib import Path

from scripts.build_projects_showcase import PROJECTS_SCRIPT, PROJECTS_STYLESHEET, integrate_document
from scripts.build_visual_guides import Document, baseline
from scripts.verify_live_site import comparable_content


class ProjectsShowcaseTests(unittest.TestCase):
    def test_live_verification_ignores_text_line_ending_differences_only(self) -> None:
        self.assertEqual(
            comparable_content(Path("index.html"), b"<main>\r\n</main>"),
            comparable_content(Path("index.html"), b"<main>\n</main>"),
        )
        self.assertNotEqual(
            comparable_content(Path("radio-loop.gif"), b"frame\r\n"),
            comparable_content(Path("radio-loop.gif"), b"frame\n"),
        )

    def test_integration_only_adds_the_expected_shared_shell_assets(self) -> None:
        homepage = baseline("site/index.html").encode("utf-8")
        integrated_homepage = integrate_document(Path("index.html"), homepage)
        self.assertEqual(integrated_homepage.count(PROJECTS_SCRIPT), 1)
        self.assertEqual(integrated_homepage.count(PROJECTS_STYLESHEET), 1)
        self.assertEqual(
            integrated_homepage.replace(PROJECTS_SCRIPT, b"").replace(PROJECTS_STYLESHEET, b""),
            homepage,
        )

        guide_library = baseline("site/guides/index.html").encode("utf-8")
        integrated_library = integrate_document(Path("guides/index.html"), guide_library)
        self.assertEqual(integrated_library.count(PROJECTS_SCRIPT), 1)
        self.assertNotIn(PROJECTS_STYLESHEET, integrated_library)
        self.assertEqual(integrated_library.replace(PROJECTS_SCRIPT, b""), guide_library)

    def test_integration_is_idempotent(self) -> None:
        homepage = baseline("site/index.html").encode("utf-8")
        integrated = integrate_document(Path("index.html"), homepage)
        self.assertEqual(integrate_document(Path("index.html"), integrated), integrated)

    def test_section_contains_the_approved_projects_and_safe_links(self) -> None:
        source = Path("site/projects/section.html").read_text(encoding="utf-8")
        document = Document(source)
        self.assertEqual(len(document.find("article")), 4)
        self.assertEqual(
            [document.text(heading) for heading in document.find("h3")[:4]],
            [
                "The Real Estate Deal Lab",
                "Green Foods Market",
                "Layer8Culture Radio",
                "Sonar",
            ],
        )
        external_links = [
            node for node in document.find("a")
            if node.attrs.get("href", "").startswith("https://")
        ]
        self.assertEqual(len(external_links), 4)
        for link in external_links:
            self.assertEqual(link.attrs.get("target"), "_blank")
            self.assertIn("noopener", link.attrs.get("rel", ""))
            self.assertIn("noreferrer", link.attrs.get("rel", ""))
        self.assertIn('data-motion-src="/projects/assets/radio-loop.gif"', source)
        self.assertIn('data-poster-src="/projects/assets/radio-poster.jpg"', source)
        self.assertIn('aria-label="Original radar diagram', source)
        self.assertNotIn("PREVIEW NOTES", source)
        self.assertNotIn("<header", source)
        self.assertNotIn("<footer", source)

    def test_motion_has_pause_offscreen_and_reduced_motion_support(self) -> None:
        css = Path("site/projects/projects.css").read_text(encoding="utf-8")
        javascript = Path("site/projects/projects.js").read_text(encoding="utf-8")
        self.assertIn("prefers-reduced-motion: reduce", css)
        self.assertIn("IntersectionObserver", javascript)
        self.assertIn('window.matchMedia("(prefers-reduced-motion: reduce)")', javascript)
        self.assertIn('window.location.hash === "#projects"', javascript)

    def test_sonar_sweep_uses_its_svg_viewbox_center_as_the_pivot(self) -> None:
        css = Path("site/projects/projects.css").read_text(encoding="utf-8")
        self.assertRegex(
            css,
            r"\.l8-projects__radar-sweep\s*\{\s*"
            r"transform-box:\s*view-box;\s*"
            r"transform-origin:\s*350px 177px;",
        )


if __name__ == "__main__":
    unittest.main()
