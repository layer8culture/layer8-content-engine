import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.build_visual_guides import BASELINE, Document, GUIDES, baseline, build
from scripts.build_weekly_guide import write_guide


class VisualGuidesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shell = Document(baseline("site/index.html"))

    def test_all_original_content_resources_and_shell_are_preserved(self):
        for slug, (_, _, topics) in GUIDES.items():
            with self.subTest(slug=slug):
                original = Document(baseline(f"site/guides/{slug}/index.html"))
                rendered = build(slug, self.shell)
                parsed = Document(rendered)
                main = original.find("main")[0]
                sections = [node for node in main.children if node.tag == "section"]
                for section in sections[1:]:
                    self.assertIn(original.raw(section), rendered)
                for paragraph in original.find("p", sections[0]):
                    self.assertIn(original.raw(paragraph), rendered)
                for tag in ["nav", "footer"]:
                    self.assertIn(self.shell.raw(self.shell.find(tag)[0]), rendered)
                self.assertEqual(len(parsed.find("h1")), 1)
                panels = [node for node in parsed.find("section")
                          if node.attrs.get("class") == "fg-panel"]
                self.assertEqual(len(panels), len(topics))
                self.assertEqual(len(parsed.find("figure")), len(topics))
                for link in original.find("a", main):
                    href = link.attrs.get("href")
                    if href and href != "/":
                        self.assertIn(href, rendered)
                for identity in re.findall(r'\bid="([^"]+)"', original.raw(main)):
                    self.assertIn(f'id="{identity}"', rendered)
                ids = re.findall(r'\bid="([^"]+)"', rendered)
                self.assertEqual(len(ids), len(set(ids)), "Duplicate deep-link IDs")
                self.assertNotIn("self.__next_f.push", rendered)
                self.assertNotIn("concept-switcher", rendered)
                self.assertNotIn("DESIGN PREVIEW", rendered)

    def test_distinct_topic_models(self):
        self.assertEqual([len(value[2]) for value in GUIDES.values()], [8, 5, 4, 7, 4])
        self.assertEqual(len({value[0] for value in GUIDES.values()}), 5)

    def test_preserve_metadata(self):
        for slug in GUIDES:
            original = baseline(f"site/guides/{slug}/index.html")
            rendered = build(slug, self.shell)
            for metadata in re.findall(r'<(?:meta|title|link)[^>]*(?:canonical|description|og:|twitter:)[^>]*>', original):
                self.assertIn(metadata, rendered)

    def test_weekly_generation_preserves_reviewed_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "index.html"
            reviewed = '<main data-visual-guide>Reviewed</main>'
            target.write_text(reviewed, encoding="utf-8")
            with patch("scripts.build_weekly_guide.render_guide") as render:
                write_guide(target, {}, {})
                render.assert_not_called()
                self.assertEqual(target.read_text(encoding="utf-8"), reviewed)
            target.write_text("Legacy", encoding="utf-8")
            with patch("scripts.build_weekly_guide.render_guide", return_value="Updated"):
                write_guide(target, {}, {})
            self.assertEqual(target.read_text(encoding="utf-8"), "Updated")


if __name__ == "__main__":
    unittest.main()
