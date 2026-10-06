from studio_lib import schema


def test_fixture_plan_is_valid(plan):
    assert schema.validate_plan(plan, "2026-12-30") == []


def test_bad_id_and_brand_mismatch(post):
    post["id"] = "20261230-radio-09"
    errs = schema.validate_post(post, "20261230")
    assert any("brand segment" in e for e in errs)
    post["id"] = "bad"
    assert any("id must match" in e for e in schema.validate_post(post, "20261230"))


def test_wrong_date_prefix(post):
    assert any("date prefix" in e for e in schema.validate_post(post, "20261231"))


def test_youtube_only_for_reels(post):
    post["platforms"] = ["instagram", "youtube"]
    assert any("only allowed for reels" in e for e in schema.validate_post(post, "20261230"))


def test_youtube_title_required(plan):
    reel = plan["posts"][1]
    reel.pop("youtube_title")
    assert any("youtube_title" in e for e in schema.validate_post(reel, "20261230"))


def test_caption_must_start_with_hook(post):
    post["caption"] = "Something else. " + post["caption"]
    assert any("start with the hook" in e for e in schema.validate_post(post, "20261230"))


def test_category_must_match_brand(plan):
    radio = plan["posts"][3]
    radio["category"] = "AI Fluency Education"
    assert any("category" in e for e in schema.validate_post(radio, "20261230"))


def test_carousel_slide_count(post):
    post["slides"] = post["slides"][:2]
    assert any("3-8 slides" in e for e in schema.validate_post(post, "20261230"))


def test_radio_reel_needs_loop_clip(plan):
    radio = plan["posts"][3]
    radio.update(format="reel", category="loop-reel", reel={"beats": [{"text": "NIGHT CODING"}, {"text": "PRESS PLAY"}], "cta": "Stay a while."})
    radio.pop("slides")
    assert any("loop_clip" in e for e in schema.validate_post(radio, "20261230"))


def test_hashtag_format(post):
    post["hashtags"] = ["no-hash"]
    assert any("hashtags" in e for e in schema.validate_post(post, "20261230"))


def test_duplicate_ids_rejected(plan):
    plan["posts"][1]["id"] = plan["posts"][0]["id"]
    assert schema.validate_plan(plan, "2026-12-30")
