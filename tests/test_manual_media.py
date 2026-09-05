import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

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
        self.assertEqual(1, self._run(queue_path))
        posts = json.loads(queue_path.read_text(encoding="utf-8"))
        self.assertNotIn("file", posts[0]["visual"])
        self.assertIn("file", posts[1]["visual"])

    def test_corrupt_source_fails_strict_and_is_preserved(self):
        queue_path = self._queue([single_post()])
        source = self.inbox / "p1.png"
        source.write_bytes(b"not a png")
        self.assertEqual(1, self._run(queue_path, strict=True))
        self.assertEqual(b"not a png", source.read_bytes())
        self.assertFalse((self.out_dir / "p1.png").exists())

    def test_required_typography_failure_preserves_source_and_previous_output(self):
        queue_path = self._queue([single_post(headline="REQUIRED")])
        source = self._drop("p1.png")
        self.out_dir.mkdir()
        output = self.out_dir / "p1.png"
        Image.new("RGB", (12, 12), "red").save(output)
        before = output.read_bytes()
        with patch.object(openai_gen, "render_infographic", return_value=False):
            self.assertEqual(1, self._run(queue_path, strict=True))
        self.assertEqual(before, output.read_bytes())
        self.assertTrue(source.exists())
        self.assertNotIn("file", json.loads(queue_path.read_text())[0]["visual"])
        self.assertEqual([], list(self.out_dir.glob(".*.png")))

    def test_ingest_without_new_still_does_not_replace_reel_delivery_path(self):
        post = single_post(file="assets/generated/p1.mp4", cover="assets/generated/p1-cover.png")
        post["format"] = "reel"
        queue_path = self._queue([post])
        self._run(queue_path)
        visual = json.loads(queue_path.read_text())[0]["visual"]
        self.assertEqual("assets/generated/p1.mp4", visual["file"])
        self.assertEqual("assets/generated/p1-cover.png", visual["cover"])

    def test_changed_still_invalidates_reel_and_all_reuse_descendants(self):
        post = single_post(file="assets/generated/p1.mp4", cover="assets/generated/p1-cover.png")
        post["format"] = "reel"
        copy = {"id": "copy", "format": "reel",
                "visual": {"source": "reuse", "of": "p1", "file": "copy.mp4"}}
        next_copy = {"id": "next", "format": "reel",
                     "visual": {"source": "reuse", "of": "copy", "file": "next.mp4"}}
        queue_path = self._queue([post, copy, next_copy])
        self._drop("p1.png")
        self.assertEqual(0, self._run(queue_path))
        for item in json.loads(queue_path.read_text()):
            self.assertNotIn("file", item["visual"])
            self.assertNotIn("cover", item["visual"])


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


class HeadlineScrimTests(unittest.TestCase):
    """The scrim must stay legible without crushing the art it sits on."""

    @staticmethod
    def plate(level, size=(512, 640)):
        return Image.new("RGBA", size, (level, level, level, 255))

    @staticmethod
    def mean_lum(im, box=None):
        grey = im.convert("L")
        if box:
            grey = grey.crop(box)
        hist = grey.histogram()
        total = sum(hist) or 1
        return sum(v * c for v, c in enumerate(hist)) / total

    def test_dark_plate_keeps_most_of_its_light(self):
        # The old scrim pushed 54-60% of the frame to 80-90% black regardless of
        # content, which erased already-dark art. A near-black plate has nothing
        # to spare, so it must come through nearly untouched.
        src = self.plate(12)
        out = openai_gen._bottom_scrim(src, text_top=400)
        kept = self.mean_lum(out, (0, 400, 512, 640)) / self.mean_lum(src)
        self.assertGreater(kept, 0.80, "scrim crushed an already-dark plate")

    def test_bright_plate_still_gets_a_real_scrim(self):
        # The flip side: legibility is the constraint. Bright art must be pulled
        # down or white type on it is unreadable.
        src = self.plate(220)
        out = openai_gen._bottom_scrim(src, text_top=400)
        self.assertLess(self.mean_lum(out, (0, 400, 512, 640)),
                        openai_gen.SCRIM_TARGET_LUM * 1.35)

    def test_scrim_strength_tracks_plate_brightness(self):
        alphas = [openai_gen._scrim_peak_alpha(
            self.mean_lum(self.plate(level))) for level in (10, 90, 180, 250)]
        self.assertEqual(alphas, sorted(alphas), "scrim should scale with the plate")
        self.assertLessEqual(max(alphas), openai_gen.SCRIM_MAX_ALPHA)
        self.assertGreaterEqual(min(alphas), openai_gen.SCRIM_MIN_ALPHA)

    def test_gradient_does_not_band(self):
        # The old ramp used integer alpha per row: every step boundary was a hard
        # full-width horizontal line, giving visible 5-6px stripes at the 2K
        # master. Dithering has to break those boundaries into a crosshatch.
        h, text_top, w = 2048, 1600, 256
        alpha = openai_gen._scrim_alpha_map(w, h, text_top=text_top, peak=200)
        ramp_start = text_top - int(h * openai_gen.SCRIM_RAMP_FRAC)
        rows = [tuple(alpha.getpixel((x, y)) for x in range(w))
                for y in range(ramp_start, text_top)]

        longest = run = 1
        for i in range(1, len(rows)):
            run = run + 1 if rows[i] == rows[i - 1] else 1
            longest = max(longest, run)
        self.assertLessEqual(longest, 6, f"banded: {longest}px of constant alpha")

        # The part that actually kills the stripe: most rows are not a single
        # flat alpha, so step boundaries stop being straight lines.
        dithered = sum(1 for row in rows if len(set(row)) > 1)
        self.assertGreater(dithered / len(rows), 0.5,
                           "ramp rows are flat; dither is not being applied")

    def test_scrim_is_deterministic(self):
        # Ordered dither, not RNG: identical input must render identical bytes.
        a = openai_gen._scrim_alpha_map(128, 256, text_top=150, peak=170)
        b = openai_gen._scrim_alpha_map(128, 256, text_top=150, peak=170)
        self.assertEqual(list(a.getdata()), list(b.getdata()))

    def test_scrim_accepts_a_non_rgba_plate(self):
        # Both render_* callers hand over RGBA, but the helper is called
        # directly by tooling and used to convert internally. Losing that
        # turned an RGB plate into "ValueError: image has wrong mode".
        rgb = self.plate(120, (256, 512)).convert("RGB")
        out = openai_gen._bottom_scrim(rgb, text_top=300)
        self.assertEqual("RGBA", out.mode)

    def test_top_anchored_layouts_do_not_become_full_frame_dimmers(self):
        # top_text_media_card puts its type at ~8% height; without the floor the
        # scrim would cover the whole frame at peak alpha.
        src = self.plate(200, (256, 512))
        out = openai_gen._bottom_scrim(src, text_top=20)
        self.assertGreater(self.mean_lum(out, (0, 0, 256, 100)), 150,
                           "scrim dimmed the top of the frame")


class DarknessGuardTests(unittest.TestCase):
    def test_near_black_drop_is_flagged(self):
        im = Image.new("RGB", (64, 64), (6, 6, 8))
        self.assertTrue(manual_media_ingest.warn_if_too_dark("img-1", im))

    def test_normal_drop_is_not_flagged(self):
        im = Image.new("RGB", (64, 64), (120, 118, 130))
        self.assertFalse(manual_media_ingest.warn_if_too_dark("img-2", im))

    def test_guard_reports_mean_and_shadow_share(self):
        im = Image.new("RGB", (10, 10), (0, 0, 0))
        im.paste(Image.new("RGB", (10, 5), (200, 200, 200)), (0, 0))
        mean, shadow = manual_media_ingest.darkness_report(im)
        self.assertAlmostEqual(shadow, 0.5, places=2)
        self.assertGreater(mean, 90)


FONTS_PRESENT = (openai_gen.BEBAS_NEUE_PATH.exists()
                 and openai_gen.INTER_PATH.exists()
                 and openai_gen.SPACE_GROTESK_PATH.exists())


@unittest.skipUnless(FONTS_PRESENT, "brand fonts are not installed")
class TypographyLayoutTests(unittest.TestCase):
    """Type must stay inside the safe area and keep the brand's hierarchy.

    The reported batch had headlines running to within 2.9% of the bottom edge
    on a 4.5% margin, because the measured block height left out the pre-subtext
    gap and the last line's glyph depth. These lock the measure to the draw.
    """

    LONG_SUB = ("Raspberry Pi is shipping MHS drivers, Hugging Face is adding it "
                "to LeRobot, and the whole stack lands for makers this week "
                "without a rewrite.")

    @staticmethod
    def plate(path, size, level=0):
        Image.new("RGB", size, (level, level, level)).save(path)
        return path

    @staticmethod
    def lowest_ink(path, threshold=170):
        """Bottom-most row carrying type. Rendered on a black plate, the only
        bright pixels are the glyphs themselves."""
        im = Image.open(path).convert("L")
        w, h = im.size
        px = im.load()
        for y in range(h - 1, -1, -1):
            if any(px[x, y] > threshold for x in range(w)):
                return y
        return -1

    def render(self, td, size, **visual):
        path = Path(td) / "plate.png"
        self.plate(path, size)
        spec = {"headline": "ONE DRIVER, ANY DEVICE"}
        spec.update(visual)
        self.assertTrue(openai_gen.render_editorial_drop(path, spec),
                        "renderer bailed out; fonts missing?")
        return path

    def safe_limit(self, h):
        return h - int(h * openai_gen.EDITORIAL_BOTTOM_MARGIN)

    def test_editorial_drop_keeps_every_element_inside_the_safe_area(self):
        # 1:1 feed and 9:16 vertical, with the full stack of elements present.
        for size in ((1024, 1024), (720, 1280)):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as td:
                path = self.render(
                    td, size,
                    kicker="TRY THIS WEEK",
                    subtext=self.LONG_SUB,
                    footer="SWIPE FOR MORE",
                )
                limit = self.safe_limit(size[1])
                self.assertLessEqual(
                    self.lowest_ink(path), limit + 2,
                    f"type ran past the {openai_gen.EDITORIAL_BOTTOM_MARGIN:.1%} "
                    f"safe margin at {size[0]}x{size[1]}")

    def test_footer_is_seated_inside_the_safe_area_not_on_the_edge(self):
        # The footer used to be drawn at a hard-coded 3.5% from the bottom while
        # the block above reserved a lane for it, so it sat outside the margin
        # the rest of the layout respected.
        with tempfile.TemporaryDirectory() as td:
            path = self.render(td, (1024, 1024), footer="SWIPE FOR MORE")
            self.assertLessEqual(self.lowest_ink(path), self.safe_limit(1024) + 2)

    def test_subtext_only_layout_still_clears_the_margin(self):
        with tempfile.TemporaryDirectory() as td:
            path = self.render(td, (1024, 1280), subtext="A short supporting line.")
            self.assertLessEqual(self.lowest_ink(path), self.safe_limit(1280) + 2)

    def test_headline_only_layout_still_clears_the_margin(self):
        with tempfile.TemporaryDirectory() as td:
            path = self.render(td, (1024, 1024))
            self.assertLessEqual(self.lowest_ink(path), self.safe_limit(1024) + 2)

    def drawn_lines(self, td, **visual):
        seen = []
        real = openai_gen._draw_tracked_line

        def spy(draw, x, y, line, *args, **kwargs):
            seen.append(line)
            return real(draw, x, y, line, *args, **kwargs)

        with patch.object(openai_gen, "_draw_tracked_line", spy):
            self.render(td, (1024, 1024), **visual)
        return seen

    def test_long_subtext_is_never_silently_truncated(self):
        # The draw loop used to slice sub_lines[:2] while the measure counted
        # them all, so anything wrapping to three lines lost its tail mid-word.
        with tempfile.TemporaryDirectory() as td:
            lines = self.drawn_lines(td, subtext=self.LONG_SUB)
        # Guard the guard: if the sample stops needing the full SUB_MAX_LINES
        # this test would pass without exercising anything.
        sub_lines = [l for l in lines if l.lower() in self.LONG_SUB.lower()]
        self.assertGreaterEqual(
            len(sub_lines), openai_gen.SUB_MAX_LINES,
            "sample subtext no longer wraps far enough to test truncation")
        rendered = " ".join(lines)
        for word in self.LONG_SUB.split():
            self.assertIn(word, rendered, f"subtext lost {word!r}")

    def test_subtext_keeps_its_sentence_case(self):
        # brand/visual-style.md: "Subtext: Inter, one short supporting line
        # (optional), Soft White" -- shouting it competes with the headline.
        sub = "Anthropic just handed them machines."
        with tempfile.TemporaryDirectory() as td:
            rendered = " ".join(self.drawn_lines(td, subtext=sub))
        self.assertIn(sub, rendered)

    def test_type_scale_leaves_square_masters_byte_identical(self):
        # Square posts were already approved, so the portrait fix must not move
        # a single glyph on them.
        for n in (512, 1024, 2048):
            self.assertEqual(float(n), openai_gen._type_scale(n, n))

    def test_type_scale_lifts_portrait_type_above_its_width(self):
        # Sizing from width alone set 9:16 caps at ~4.8% of frame height against
        # 8.5% on 1:1, so vertical headlines read as undersized.
        w, h = 1152, 2048
        scale = openai_gen._type_scale(w, h)
        self.assertGreater(scale, w)
        self.assertLess(scale, h)

    def font_and_draw(self, size=60):
        draw = ImageDraw.Draw(Image.new("RGB", (1024, 1024)))
        return draw, openai_gen._load_font(openai_gen.INTER_PATH, size, "Bold")

    def test_balance_lines_keeps_every_word_and_the_line_count(self):
        draw, font = self.font_and_draw()
        text = "ONE DRIVER, ANY DEVICE"
        greedy = openai_gen._wrap(draw, text, font, 520, 0.0)
        balanced = openai_gen._balance_lines(draw, text, font, 520, 0.0, greedy)
        self.assertEqual(len(greedy), len(balanced))
        self.assertEqual(text.split(), " ".join(balanced).split())

    def test_balance_lines_never_widens_the_longest_line(self):
        draw, font = self.font_and_draw()
        text = "ONE DRIVER, ANY DEVICE"
        greedy = openai_gen._wrap(draw, text, font, 520, 0.0)
        balanced = openai_gen._balance_lines(draw, text, font, 520, 0.0, greedy)
        widest = lambda ls: max(openai_gen._line_width(draw, l, font, 0.0)
                                for l in ls)
        self.assertLessEqual(widest(balanced), widest(greedy) + 1)

    def test_balance_lines_is_a_no_op_for_a_single_line(self):
        draw, font = self.font_and_draw()
        self.assertEqual(["SHORT"],
                         openai_gen._balance_lines(draw, "SHORT", font, 900, 0.0,
                                                   ["SHORT"]))


class ScrimDetailTests(unittest.TestCase):
    """Legibility follows local contrast, not just luminance."""

    def test_flat_art_is_untouched_by_the_detail_term(self):
        # A dead-band keeps previously approved renders bit-identical: smooth
        # dark art must still land on the bare minimum scrim. The threshold is
        # spelled out rather than read from the constant, so shrinking the
        # dead-band to nothing is a failure rather than a silent no-op.
        lum = 12.0
        self.assertEqual(openai_gen.SCRIM_MIN_ALPHA,
                         openai_gen._scrim_peak_alpha(lum))
        self.assertEqual(openai_gen.SCRIM_MIN_ALPHA,
                         openai_gen._scrim_peak_alpha(lum, 1.5))
        self.assertGreaterEqual(openai_gen.SCRIM_DETAIL_DEADBAND, 1.5,
                                "dead-band no longer covers near-flat art")

    def test_busy_dark_art_gets_more_scrim_than_flat_dark_art(self):
        # The reported reel was dark enough that luminance alone asked for
        # nothing, yet busy enough that white type had nothing to sit against.
        lum = 12.0
        flat = openai_gen._scrim_peak_alpha(lum, 0.5)
        busy = openai_gen._scrim_peak_alpha(lum, 3.2)
        self.assertGreater(busy, flat)

    def test_detail_term_respects_the_scrim_ceiling(self):
        for lum in (5.0, 120.0, 250.0):
            self.assertLessEqual(openai_gen._scrim_peak_alpha(lum, 90.0),
                                 openai_gen.SCRIM_MAX_ALPHA)

    def test_detail_term_is_monotonic(self):
        alphas = [openai_gen._scrim_peak_alpha(12.0, d)
                  for d in (0.0, 1.0, 2.0, 3.0, 6.0, 12.0)]
        self.assertEqual(alphas, sorted(alphas))

    def test_flat_plate_scores_no_detail(self):
        flat = Image.new("RGBA", (256, 256), (18, 18, 20, 255))
        self.assertLess(openai_gen._plate_detail(flat, 0),
                        openai_gen.SCRIM_DETAIL_DEADBAND)


if __name__ == "__main__":
    unittest.main()
