"""Apply the approved concise homepage copy to the existing Next export."""
from __future__ import annotations

import argparse
from pathlib import Path

SERVER_COPY = (
    (
        "Layer8Culture is a creator-tech platform exploring how AI, creativity, culture, and human behavior shape the future. "
        "We believe the next generation of technology won’t just be defined by the people building the models, but by the people "
        "who understand how to apply them creatively, communicate them culturally, and push them into the mainstream. In the AI era, "
        "people shouldn’t only consume technology. They should learn it, experiment with it, and help shape where it goes next.",
        "Layer8Culture brings AI, creativity, and culture together. We help creators, developers, and curious minds learn emerging "
        "technology by experimenting with it — not just consuming it.",
    ),
    (
        "Built through live experimentation, visual storytelling, and public creation, Layer8Culture exists to make emerging "
        "technology feel more human, accessible, and participatory. This is a space for builders, creators, developers, and curious "
        "minds learning in real time through AI workflows, digital products, automation, design, and creative systems. The goal is "
        "not just to talk about the future. The goal is to actively build it in public while bringing culture into the process.",
        "Through live builds and visual storytelling, we explore AI workflows, digital products, automation, and design. We make "
        "technology more human and accessible by building in public.",
    ),
    (
        "We empower creators, developers, and entrepreneurs to leverage AI, build real products, and own the future. "
        "Four pillars. No shortcuts.",
        "AI fluency, real products, and creative independence for creators, developers, and entrepreneurs. Four pillars. No shortcuts.",
    ),
    (
        "The May 29 flagship stream became the case study. Sponsored by",
        "In the May 29 flagship stream, sponsored by",
    ),
    (
        ", we taught Claude Code CLI beside the owner and turned real marketing bottlenecks into working software in public.",
        ", we taught Claude Code CLI alongside the owner and turned real marketing bottlenecks into working software. "
        "Figures below are reported case-study results; projected savings are not a guarantee.",
    ),
    (
        "Layer8Culture helps small-business owners learn by building. Start with fluency. Leave with clearer systems, "
        "stronger taste, and a practical next build.",
        "Small-business owners: learn by building. Start with AI fluency and leave with clearer systems and a practical next build.",
    ),
    (
        "A four-hour hands-on room for small-business owners. Cohorts of 10-20 learn what to automate, how to work with AI tools, "
        "and where judgment still matters.",
        "A four-hour, hands-on workshop for 10–20 small-business owners. Learn what to automate, how to use AI tools, "
        "and where judgment matters.",
    ),
    (
        "Focused done-with-you sessions that turn one repeatable workflow into a working prototype, internal tool, or campaign system.",
        "Turn one repeatable workflow into a working prototype, internal tool, or campaign system in focused, done-with-you sessions.",
    ),
    (
        "We map the bottlenecks, choose the first useful build, and help your team leave with language, confidence, and a path to ship.",
        "Map your bottlenecks, choose a useful first build, and give your team the confidence and a path to ship.",
    ),
)

CLIENT_COPY = (
    (
        "Layer8Culture is the human layer of technology. We build, we create, we teach, we execute.",
        "Learn AI by building. Live experiments, practical workflows, and hands-on workshops for creators, "
        "developers, and small-business owners.",
    ),
    (
        "Real conversations. Real builds. Real impact. No green-room gloss. We stream the work in progress.",
        "Real conversations and live builds. Watch the work in progress, ask questions, and build along.",
    ),
    ("Set Reminder", "Get show updates"),
    ("Add to Calendar", "Watch on YouTube"),
    (
        "Broadcast schedule, drops, and behind-the-scenes work — straight from the build. No spam. No newsletter cosplay.",
        "Get the broadcast schedule, new drops, and behind-the-scenes builds. Interested in workshops? "
        "Join the list for updates.",
    ),
    ("Join the Culture", "Explore the guides"),
)

COPY_CHANGES = (*SERVER_COPY, *CLIENT_COPY)
FLIGHT_SPLIT_COPY = (
    (
        "We map the bottlenecks, choose the first useful build, and help your",
        "Map your bottlenecks, choose a useful first build, and give your",
    ),
    (
        " team leave with language, confidence, and a path to ship.",
        " team the confidence and a path to ship.",
    ),
)
FLIGHT_PAYLOADS = {
    "index.txt",
    "__next.__PAGE__.txt",
    "__next._full.txt",
}
HOMEPAGE_CHUNK = "_next/static/chunks/03u7~qd09kzk9.js"
PREVIEW_MARKERS = ("PREVIEW NOTES", "Design preview only", "Measurement notes &")


def transform_copy(source: bytes) -> bytes:
    text = source.decode("utf-8")
    for old, new in COPY_CHANGES:
        text = text.replace(old, new)
    for old, new in FLIGHT_SPLIT_COPY:
        text = text.replace(old, new)
    text = text.replace(
        'href="#join" class="group inline-flex items-center justify-center gap-3 bg-layer8-blue',
        'href="/guides/" class="group inline-flex items-center justify-center gap-3 bg-layer8-blue',
    )
    text = text.replace(
        'href:"#join",className:"group inline-flex items-center justify-center gap-3 bg-layer8-blue',
        'href:"/guides/",className:"group inline-flex items-center justify-center gap-3 bg-layer8-blue',
    )
    return text.encode("utf-8")


def transform_baseline(relative: str, source: bytes) -> bytes:
    if relative == "index.html" or relative in FLIGHT_PAYLOADS or relative == HOMEPAGE_CHUNK:
        return transform_copy(source)
    return source


def refine_site(root: Path) -> None:
    homepage = root / "index.html"
    if not homepage.is_file():
        raise FileNotFoundError(homepage)
    homepage_source = homepage.read_bytes()
    payloads = [root / name for name in sorted(FLIGHT_PAYLOADS) if (root / name).is_file()]
    chunk = root / HOMEPAGE_CHUNK
    if not chunk.is_file():
        raise FileNotFoundError(chunk)
    all_source = (
        homepage_source
        + b"\n".join(path.read_bytes() for path in payloads)
        + chunk.read_bytes()
    )
    missing = [
        old for old, new in SERVER_COPY
        if old.encode("utf-8") not in all_source and new.encode("utf-8") not in all_source
    ]
    missing.extend(
        old for old, new in CLIENT_COPY
        if old.encode("utf-8") not in all_source and new.encode("utf-8") not in all_source
    )
    missing.extend(
        old for old, new in FLIGHT_SPLIT_COPY
        if old.encode("utf-8") not in all_source and new.encode("utf-8") not in all_source
    )
    if missing:
        raise RuntimeError(f"Homepage baseline copy changed unexpectedly: {missing[0][:80]}")

    homepage.write_bytes(transform_copy(homepage_source))
    for payload in payloads:
        payload.write_bytes(transform_copy(payload.read_bytes()))
    chunk.write_bytes(transform_copy(chunk.read_bytes()))

    result = homepage.read_text(encoding="utf-8")
    if any(marker in result for marker in PREVIEW_MARKERS):
        raise RuntimeError("Preview controls or review notes must not ship.")
    if any(new not in result for _, new in COPY_CHANGES):
        raise RuntimeError("Homepage export is missing approved concise copy.")
    flight_output = "\n".join(
        path.read_text(encoding="utf-8") for path in [homepage, *payloads]
    )
    if any(old in flight_output for old, _ in FLIGHT_SPLIT_COPY):
        raise RuntimeError("A split Flight payload still contains stale homepage copy.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=Path, default=Path("site"))
    args = parser.parse_args()
    refine_site(args.site)
    print("Applied concise homepage copy to the existing Next HTML, route payloads, and client chunk.")


if __name__ == "__main__":
    main()
