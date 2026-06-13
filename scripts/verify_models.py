#!/usr/bin/env python3
"""Verify Higgsfield model slugs against the live API.

WHY THIS EXISTS
---------------
The Higgsfield API has NO catalog/list endpoint and the Python SDK exposes no
list/models method (confirmed 2026-06-12 against docs.higgsfield.ai). The only
browseable catalog is the gallery UI at https://cloud.higgsfield.ai. To avoid
guessing slugs (we've already hit "Model not found" for
bytedance/seedream/v4/text-to-image and nano-banana/text-to-image), this script
probes a curated list of candidate slugs using YOUR credentials and reports:

  * EXISTS    - the slug is a real model (submit was accepted/queued, or failed
                only on credits/validation rather than "model not found")
  * MISSING   - the API returned "Model not found" -> do not use this slug
  * REF OK    - the model also accepted an `input_images` reference argument

Run it, read the table, then set IMAGE_MODEL / REFERENCE_IMAGE_MODEL (or the
HF_REFERENCE_MODEL env var) in higgsfield_gen.py to a slug marked EXISTS
(and REF OK for the reference model).

Requires: pip install higgsfield-client
Env vars: HF_API_KEY + HF_API_SECRET  (or HF_KEY="key:secret")

Usage:
  python scripts/verify_models.py
  python scripts/verify_models.py extra/slug/one extra/slug/two
"""
import sys
import higgsfield_client as hf

# Candidate slugs to probe. Add more on the command line. The first group are
# documented in official docs; the rest are common community guesses worth
# confirming or ruling out for this account.
CANDIDATES = [
    "higgsfield-ai/soul/standard",
    "reve/text-to-image",
    "flux-pro/kontext/max/text-to-image",
    "flux-pro/kontext/max",
    "bytedance/seedream/v4/text-to-image",  # known MISSING - sanity check
    "nano-banana/text-to-image",            # known MISSING - sanity check
]

# A tiny 1x1 PNG data URL is not accepted as a reference; reference probing uses
# a real uploaded image if one is provided, else skips the REF check.
REF_IMAGE = None  # set to a local path to test input_images support


def classify_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "model not found" in msg or "not found" in msg:
        return "MISSING"
    if "credit" in msg:
        return "EXISTS (no credits)"
    if "unauthor" in msg or "forbidden" in msg or "401" in msg or "403" in msg:
        return "AUTH? (check credentials)"
    # Validation errors / bad args mean the route exists -> the model is real.
    return "EXISTS (validation)"


def probe(slug: str, ref_url: str | None) -> None:
    args = {"prompt": "probe", "resolution": "720p", "aspect_ratio": "1:1"}
    # First: does the model exist at all?
    status = _submit_status(slug, args)
    line = f"{slug:<45} {status}"
    # Second: if it exists and we have a reference image, does it accept it?
    if ref_url and status.startswith("EXISTS"):
        ref_args = dict(args, input_images=[ref_url])
        ref_status = _submit_status(slug, ref_args)
        line += f"   | input_images -> {ref_status}"
    print(line)


def _submit_status(slug: str, args: dict) -> str:
    try:
        controller = hf.submit(slug, arguments=args)
        # If submit returned without raising, the model exists and was queued.
        try:
            controller.cancel()
        except Exception:
            pass
        return "EXISTS (queued)"
    except hf.CredentialsMissedError:
        return "AUTH? (no credentials set)"
    except hf.HiggsfieldClientError as e:
        return classify_error(e)
    except Exception as e:  # noqa: BLE001 - report anything unexpected
        return f"ERROR ({type(e).__name__}: {e})"


def main(argv: list[str]) -> None:
    slugs = CANDIDATES + argv
    ref_url = None
    if REF_IMAGE:
        try:
            ref_url = hf.upload_file(REF_IMAGE)
            print(f"Uploaded reference image: {ref_url}\n")
        except Exception as e:  # noqa: BLE001
            print(f"Could not upload REF_IMAGE ({e}); skipping input_images checks.\n")
    print(f"Probing {len(slugs)} candidate slugs against the live Higgsfield API:\n")
    for slug in slugs:
        probe(slug, ref_url)
    print(
        "\nDone. Set IMAGE_MODEL / REFERENCE_IMAGE_MODEL in higgsfield_gen.py to a "
        "slug marked EXISTS (and 'input_images -> EXISTS' for references)."
    )


if __name__ == "__main__":
    main(sys.argv[1:])
