import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import manual_media  # noqa: E402
import manual_media_ingest  # noqa: E402
import openai_gen  # noqa: E402

# Whether the openai SDK is installed here, so the reload test can restore state.
SDK_AT_IMPORT = openai_gen.OPENAI_SDK_AVAILABLE


def single_post(post_id="p1", **visual):
    base = {"source": "openai", "openai_prompt": "a cinematic scene", "aspect": "1:1"}
    base.update(visual)
    return {"id": post_id, "account": "layer8culture", "format": "single",
            "visual": base}


def carousel_post(post_id="c1", slides=None, **visual):
    base = {
        "source": "openai",
        "aspect": "1:1",
        "typography_preset": "editorial_drop",
        "slides": slides if slides is not None else [
            {"openai_prompt": "slide one", "headline": "ONE"},
            {"openai_prompt": "slide two", "headline": "TWO"},
        ],
    }
    base.update(visual)
    return {"id": post_id, "account": "layer8culture", "format": "carousel",
            "visual": base}


class PlanImagesTests(unittest.TestCase):
    def test_single_post_yields_one_spec_named_after_the_post(self):
        specs = manual_media.plan_images([single_post()])
        self.assertEqual(1, len(specs))
        self.assertEqual("p1", specs[0].image_id)
        self.assertEqual("p1.png", specs[0].filename)
        self.assertEqual("1:1", specs[0].aspect)
        self.assertEqual("1024x1024", specs[0].size)

    def test_story_and_reel_default_to_vertical_like_openai_gen(self):
        posts = [
            {"id": "s1", "format": "story",
             "visual": {"source": "openai", "openai_prompt": "x"}},
            {"id": "r1", "format": "reel",
             "visual": {"source": "openai", "openai_prompt": "x"}},
        ]
        specs = manual_media.plan_images(posts)
        self.assertEqual(["9:16", "9:16"], [s.aspect for s in specs])
        self.assertEqual(["1024x1536", "1024x1536"], [s.size for s in specs])

    def test_explicit_aspect_wins_over_vertical_default(self):
        post = {"id": "r1", "format": "reel",
                "visual": {"source": "openai", "openai_prompt": "x", "aspect": "1:1"}}
        self.assertEqual("1:1", manual_media.plan_images([post])[0].aspect)

    def test_carousel_yields_one_numbered_spec_per_slide(self):
        specs = manual_media.plan_images([carousel_post()])
        self.assertEqual(["c1-1", "c1-2"], [s.image_id for s in specs])
        self.assertEqual([1, 2], [s.slide_index for s in specs])
        self.assertEqual(["c1", "c1"], [s.post_id for s in specs])
        # Slides inherit post-level defaults.
        self.assertEqual("editorial_drop", specs[0].visual["typography_preset"])
        self.assertEqual("1:1", specs[1].aspect)

    def test_slide_overrides_beat_post_level_defaults(self):
        post = carousel_post(slides=[
            {"openai_prompt": "s", "aspect": "9:16",
             "typography_preset": "brand_title_card"},
        ])
        spec = manual_media.plan_images([post])[0]
        self.assertEqual("9:16", spec.aspect)
        self.assertEqual("brand_title_card", spec.visual["typography_preset"])

    def test_non_openai_sources_and_promptless_posts_are_skipped(self):
        posts = [
            {"id": "reuse1", "format": "reel",
             "visual": {"source": "reuse", "of": "master", "aspect": "9:16"}},
            {"id": "lib1", "format": "single",
             "visual": {"source": "library", "library_hint": "default"}},
            {"id": "noprompt", "format": "single", "visual": {"source": "openai"}},
            {"id": "", "format": "single",
             "visual": {"source": "openai", "openai_prompt": "x"}},
            single_post("keeper"),
        ]
        specs = manual_media.plan_images(posts)
        self.assertEqual(["keeper"], [s.image_id for s in specs])

    def test_carousel_slide_without_prompt_is_skipped(self):
        post = carousel_post(slides=[
            {"openai_prompt": "s1"}, {"headline": "no prompt"}, {"openai_prompt": "s3"},
        ])
        specs = manual_media.plan_images([post])
        self.assertEqual(["c1-1", "c1-3"], [s.image_id for s in specs])


class CarouselSlideVisualsTests(unittest.TestCase):
    def test_defaults_merge_and_none_slide_values_do_not_override(self):
        visual = {
            "aspect": "9:16", "quality": "low", "overlay_position": "lower-center",
            "logo_subtle": True,
            "slides": [{"openai_prompt": "s", "headline": None, "quality": "high"}],
        }
        merged = manual_media.carousel_slide_visuals(visual)[0]
        self.assertEqual("9:16", merged["aspect"])
        self.assertEqual("high", merged["quality"])
        self.assertEqual("lower-center", merged["overlay_position"])
        self.assertTrue(merged["logo_subtle"])
        self.assertNotIn("headline", merged)

    def test_matches_openai_gen_defaults(self):
        merged = manual_media.carousel_slide_visuals(
            {"slides": [{"openai_prompt": "s"}]},
            openai_gen.IMAGE_QUALITY, openai_gen.DEFAULT_OVERLAY_POSITION)[0]
        self.assertEqual(openai_gen.IMAGE_QUALITY, merged["quality"])
        self.assertEqual(openai_gen.DEFAULT_OVERLAY_POSITION,
                         merged["overlay_position"])

    def test_no_slides_returns_empty(self):
        self.assertEqual([], manual_media.carousel_slide_visuals({"aspect": "1:1"}))


class PromptPackTests(unittest.TestCase):
    def test_pack_path_sits_next_to_the_queue(self):
        self.assertEqual(
            Path("queue/2026-08-18.prompts.md"),
            manual_media.prompt_pack_path(Path("queue/2026-08-18.json")))

    def test_pack_lists_every_filename_prompt_and_save_path(self):
        specs = manual_media.plan_images([single_post(), carousel_post()])
        body = manual_media.build_prompt_pack(specs, Path("queue/2026-08-18.json"))
        self.assertIn("`p1.png`", body)
        self.assertIn("`c1-1.png`", body)
        self.assertIn("`c1-2.png`", body)
        self.assertIn("a cinematic scene", body)
        self.assertIn("slide two", body)
        self.assertIn("assets/manual-inbox/p1.png", body)
        self.assertIn("**3 image(s)**", body)
        self.assertIn("scripts/manual_media_ingest.py queue/2026-08-18.json", body)

    def test_prompt_block_carries_size_and_no_text_rule(self):
        spec = manual_media.plan_images([single_post(aspect="9:16")])[0]
        block = manual_media.copy_prompt(spec)
        self.assertIn("Aspect ratio: 9:16 portrait (vertical). Generate at 1024x1536.",
                      block)
        self.assertIn("Render no text", block)
        self.assertIn("lower third", block)

    def test_clean_area_note_follows_preset_and_format(self):
        editorial = manual_media.plan_images(
            [single_post(typography_preset="editorial_drop")])[0]
        self.assertIn("bottom half", manual_media.copy_prompt(editorial))
        upper = manual_media.plan_images(
            [single_post(overlay_position="upper-left")])[0]
        self.assertIn("upper third", manual_media.copy_prompt(upper))
        reel = manual_media.plan_images([
            {"id": "r", "format": "reel",
             "visual": {"source": "openai", "openai_prompt": "x"}}])[0]
        self.assertIn("animated text beats", manual_media.copy_prompt(reel))

    def test_prompt_containing_backticks_is_fenced_safely(self):
        spec = manual_media.plan_images(
            [single_post(openai_prompt="a ``` fenced ``` prompt")])[0]
        body = manual_media.build_prompt_pack([spec], Path("queue/x.json"))
        self.assertIn("````text", body)

    def test_empty_queue_says_nothing_to_generate(self):
        body = manual_media.build_prompt_pack([], Path("queue/x.json"))
        self.assertIn("Nothing to generate", body)

    def test_write_prompt_pack_writes_next_to_the_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "2026-08-18.json"
            queue_path.write_text("[]", encoding="utf-8")
            specs = manual_media.plan_images([single_post()])
            written = manual_media.write_prompt_pack(specs, queue_path)
            self.assertEqual(queue_path.with_name("2026-08-18.prompts.md"), written)
            self.assertIn("`p1.png`", written.read_text(encoding="utf-8"))


class FindSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.inbox = Path(self.tmp.name)
        self.spec = manual_media.plan_images([single_post()])[0]

    def _touch(self, name):
        path = self.inbox / name
        Image.new("RGB", (8, 8)).save(path)
        return path

    def test_missing_file_returns_none(self):
        self.assertIsNone(self.spec.find_source(self.inbox))

    def test_png_is_preferred_over_other_extensions(self):
        self._touch("p1.jpg")
        png = self._touch("p1.png")
        self.assertEqual(png, self.spec.find_source(self.inbox))

    def test_jpg_and_webp_are_accepted(self):
        jpg = self._touch("p1.jpg")
        self.assertEqual(jpg, self.spec.find_source(self.inbox))

    def test_case_insensitive_stem_fallback(self):
        upper = self.inbox / "P1.PNG"
        Image.new("RGB", (8, 8)).save(upper)
        self.assertEqual(upper, self.spec.find_source(self.inbox))

    def test_missing_inbox_directory_is_not_an_error(self):
        self.assertIsNone(self.spec.find_source(self.inbox / "nope"))


class FitToAspectTests(unittest.TestCase):
    def test_wide_image_is_cropped_to_square(self):
        out = manual_media_ingest.fit_to_aspect(Image.new("RGB", (1536, 1024)), "1:1")
        self.assertEqual((1024, 1024), out.size)

    def test_square_image_is_cropped_to_vertical(self):
        # Cropping only ever trims, so a square becomes 9:16 by losing width.
        out = manual_media_ingest.fit_to_aspect(Image.new("RGB", (1024, 1024)), "9:16")
        self.assertEqual((576, 1024), out.size)

    def test_matching_aspect_is_left_alone(self):
        im = Image.new("RGB", (1024, 1536))
        self.assertIs(im, manual_media_ingest.fit_to_aspect(im, "2:3"))

    def test_unparseable_aspect_falls_back_to_square(self):
        out = manual_media_ingest.fit_to_aspect(Image.new("RGB", (800, 400)), "wide")
        self.assertEqual((400, 400), out.size)


class NormalizeResolutionTests(unittest.TestCase):
    def test_small_image_is_upscaled_to_the_master_long_edge(self):
        with patch.object(openai_gen, "IMAGE_LONG_EDGE", 2048), \
                patch.object(openai_gen, "IMAGE_2K", True):
            out = manual_media_ingest.normalize_resolution(Image.new("RGB", (512, 512)))
        self.assertEqual((2048, 2048), out.size)

    def test_oversized_image_is_downscaled_and_keeps_ratio(self):
        with patch.object(openai_gen, "IMAGE_LONG_EDGE", 2048), \
                patch.object(openai_gen, "IMAGE_2K", True):
            out = manual_media_ingest.normalize_resolution(Image.new("RGB", (4096, 2048)))
        self.assertEqual((2048, 1024), out.size)

    def test_disabled_2k_leaves_the_image_untouched(self):
        im = Image.new("RGB", (600, 600))
        with patch.object(openai_gen, "IMAGE_2K", False):
            self.assertIs(im, manual_media_ingest.normalize_resolution(im))


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inbox = self.root / "inbox"
        self.inbox.mkdir()
        self.out_dir = self.root / "generated"

    def _drop(self, name, size=(1536, 1024)):
        path = self.inbox / name
        Image.new("RGB", size, (10, 12, 28)).save(path)
        return path

    def _queue(self, posts):
        path = self.root / "2026-08-18.json"
        path.write_text(json.dumps(posts, indent=2), encoding="utf-8")
        return path

    def _run(self, queue_path, **kwargs):
        with patch.object(openai_gen, "IMAGE_LONG_EDGE", 512), \
                patch.object(openai_gen, "IMAGE_2K", True):
            return manual_media_ingest.main(
                str(queue_path), self.inbox, self.out_dir, **kwargs)

    def test_dropped_image_is_cropped_branded_and_written(self):
        spec = manual_media.plan_images([single_post()])[0]
        source = self._drop("p1.png")
        with patch.object(openai_gen, "IMAGE_LONG_EDGE", 512), \
                patch.object(openai_gen, "IMAGE_2K", True):
            out = manual_media_ingest.ingest_image(spec, source, self.out_dir)
        self.assertTrue(out.exists())
        with Image.open(out) as im:
            self.assertEqual((512, 512), im.size)

    def test_single_post_queue_is_updated_with_posix_path(self):
        queue_path = self._queue([single_post()])
        self._drop("p1.png")
        self.assertEqual(0, self._run(queue_path))
        visual = json.loads(queue_path.read_text(encoding="utf-8"))[0]["visual"]
        self.assertEqual((self.out_dir / "p1.png").as_posix(), visual["file"])
        self.assertNotIn("\\", visual["file"])

    def test_consumed_sources_move_out_of_the_inbox(self):
        queue_path = self._queue([single_post()])
        self._drop("p1.png")
        self._run(queue_path)
        self.assertFalse((self.inbox / "p1.png").exists())
        self.assertTrue(
            (self.inbox / manual_media.INGESTED_DIRNAME / "p1.png").exists())

    def test_keep_flag_leaves_sources_in_place(self):
        queue_path = self._queue([single_post()])
        self._drop("p1.png")
        self._run(queue_path, keep=True)
        self.assertTrue((self.inbox / "p1.png").exists())

    def test_complete_carousel_is_wired_up_in_slide_order(self):
        queue_path = self._queue([carousel_post()])
        self._drop("c1-2.png")
        self._drop("c1-1.png")
        self._run(queue_path)
        visual = json.loads(queue_path.read_text(encoding="utf-8"))[0]["visual"]
        self.assertEqual([(self.out_dir / "c1-1.png").as_posix(),
                          (self.out_dir / "c1-2.png").as_posix()], visual["files"])
        self.assertEqual(visual["files"][0], visual["file"])

    def test_partial_carousel_is_not_wired_up(self):
        queue_path = self._queue([carousel_post()])
        self._drop("c1-1.png")
        self.assertEqual(0, self._run(queue_path))
        visual = json.loads(queue_path.read_text(encoding="utf-8"))[0]["visual"]
        self.assertNotIn("files", visual)
        self.assertNotIn("file", visual)

    def test_strict_exits_non_zero_when_images_are_missing(self):
        queue_path = self._queue([single_post()])
        self.assertEqual(1, self._run(queue_path, strict=True))
        self.assertEqual(0, self._run(queue_path))

    def test_dry_run_changes_nothing(self):
        queue_path = self._queue([single_post()])
        before = queue_path.read_text(encoding="utf-8")
        self._drop("p1.png")
        self.assertEqual(0, self._run(queue_path, dry_run=True))
        self.assertEqual(before, queue_path.read_text(encoding="utf-8"))
        self.assertTrue((self.inbox / "p1.png").exists())
        self.assertFalse((self.out_dir / "p1.png").exists())

    def test_queue_without_openai_visuals_is_a_no_op(self):
        queue_path = self._queue([
            {"id": "reuse1", "format": "reel",
             "visual": {"source": "reuse", "of": "master"}}])
        before = queue_path.read_text(encoding="utf-8")
        self.assertEqual(0, self._run(queue_path))
        self.assertEqual(before, queue_path.read_text(encoding="utf-8"))

    def test_unreadable_drop_does_not_abort_the_batch(self):
        queue_path = self._queue([single_post("p1"), single_post("p2")])
        (self.inbox / "p1.png").write_text("not an image", encoding="utf-8")
        self._drop("p2.png")
        self.assertEqual(0, self._run(queue_path))
        posts = json.loads(queue_path.read_text(encoding="utf-8"))
        self.assertNotIn("file", posts[0]["visual"])
        self.assertIn("file", posts[1]["visual"])


class ManualModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_manual_run_writes_the_pack_and_never_touches_the_queue(self):
        queue_path = self.root / "2026-08-18.json"
        posts = [single_post()]
        queue_path.write_text(json.dumps(posts, indent=2), encoding="utf-8")
        before = queue_path.read_text(encoding="utf-8")
        openai_gen.main(str(queue_path), manual=True, inbox=self.root / "inbox")
        self.assertEqual(before, queue_path.read_text(encoding="utf-8"))
        pack = queue_path.with_name("2026-08-18.prompts.md")
        self.assertIn("`p1.png`", pack.read_text(encoding="utf-8"))

    def test_missing_credentials_fall_back_to_manual_instead_of_the_api(self):
        queue_path = self.root / "2026-08-19.json"
        queue_path.write_text(json.dumps([single_post()], indent=2), encoding="utf-8")
        with patch.object(openai_gen, "image_backend_available", return_value=False), \
                patch.object(openai_gen, "_make_image_client") as client:
            openai_gen.main(str(queue_path), inbox=self.root / "inbox")
        client.assert_not_called()
        self.assertTrue(queue_path.with_name("2026-08-19.prompts.md").exists())

    def test_image_backend_env_switch_forces_manual(self):
        queue_path = self.root / "2026-08-20.json"
        queue_path.write_text(json.dumps([single_post()], indent=2), encoding="utf-8")
        with patch.object(openai_gen, "MANUAL_BACKEND", True), \
                patch.object(openai_gen, "_make_image_client") as client:
            openai_gen.main(str(queue_path), inbox=self.root / "inbox")
        client.assert_not_called()
        self.assertTrue(queue_path.with_name("2026-08-20.prompts.md").exists())

    def test_backend_is_unavailable_without_credentials_or_sdk(self):
        with patch.object(openai_gen, "OPENAI_SDK_AVAILABLE", False):
            self.assertFalse(openai_gen.image_backend_available())
        with patch.object(openai_gen, "OPENAI_SDK_AVAILABLE", True), \
                patch.object(openai_gen, "azure_configured", return_value=False), \
                patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False):
            self.assertFalse(openai_gen.image_backend_available())
        with patch.object(openai_gen, "OPENAI_SDK_AVAILABLE", True), \
                patch.object(openai_gen, "azure_configured", return_value=True):
            self.assertTrue(openai_gen.image_backend_available())


class OptionalSdkImportTests(unittest.TestCase):
    def test_openai_gen_imports_without_the_openai_package(self):
        with patch.dict(sys.modules, {"openai": None}):
            try:
                module = importlib.reload(openai_gen)
                self.assertFalse(module.OPENAI_SDK_AVAILABLE)
                self.assertFalse(module.image_backend_available())
                self.assertTrue(issubclass(module.OpenAIError, Exception))
                # The typography renderers the ingest script reuses still load.
                self.assertTrue(callable(module.render_infographic))
                self.assertTrue(callable(module.render_editorial_drop))
            finally:
                pass
        importlib.reload(openai_gen)  # restore the real SDK state for other tests
        self.assertEqual(SDK_AT_IMPORT, openai_gen.OPENAI_SDK_AVAILABLE)


if __name__ == "__main__":
    unittest.main()
