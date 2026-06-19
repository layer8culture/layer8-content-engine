# Layer8Culture — Viral Short-Form Video Formats

The canonical spec for layer8culture's **scroll-stopping short-form videos** (TikTok +
YouTube Shorts + Instagram Reels). Every dedicated video the engine generates for these
surfaces follows this format. The lofi account (Layer8Culture Radio) does **not** use this
— it stays calm/atmospheric per `voice-lofi.md`.

> Method, not guesswork. Hooks are scored with the **STEPPS** virality framework
> (Jonah Berger, *Contagious*), as operationalized by the open-source **ViralLab**
> project (github.com/Funghi88/ViralLab). The engine drafts several hooks, scores them,
> and ships only the strongest.

---

## 1. The 5 viral pillars (every video maps to exactly one)
1. **AI fluency** — the gap between consuming AI and operating it; literacy as leverage.
2. **Black tech culture** — Afrofuturism, representation, culture-as-frontier in tech.
3. **Build-in-public** — the real build: the engine, the pipeline, the mess, the wins.
4. **Cinematic coding** — late-night, premium, focused builder energy; the craft as art.
5. **"AI changed my life" transformations** — consumer → operator → creator → builder
   → frontier; the before/after of learning to build with AI.

## 2. Structure (8-12s vertical 9:16)
A 3-beat arc; keep the whole thing **8-12 seconds** (Sora clip = 8 or 12s):
- **0-2s — Pattern interrupt.** A bold, controversial-enough claim or question that
  stops the scroll. The hook does the work.
- **2-7s — Cinematic transformation / visual metaphor.** Sora carries the visual story
  (a push-in, a reveal, words rearranging, a state change).
- **7-10s — Punchline / lesson / CTA.** Land the idea; end with the CTA.

## 3. Overlay text rules (HUGE, simple, controversial-enough)
The big on-screen text is **burned in by ffmpeg** (Sora output is clean) — see
`scripts/reel_gen.py` `overlay_beats_on_video`. It is supplied as
`visual.reel.overlay_beats` (below). Rules:
- **Huge and simple.** Space Grotesk, UPPERCASE, high contrast. **<= 4 words and <= 15
  characters per beat** so it renders BIG and never overflows (the renderer auto-shrinks,
  but short beats read best). Break a long idea across the 3 beats, not one long line.
- **Controversial enough to stop the scroll** — but on-brand (calm confidence, never
  hype, hustle, or fake-motivational). Tension, not clickbait lies.
- **Safe margins:** text renders in the **upper-center** band — keep the top ~12% and
  bottom ~20% clear of essential words (that's where TikTok/IG/YT UI sits).
- 1 beat per arc segment is typical: **hook (0-2)**, optional **mid cue (2-7)**,
  **punchline/CTA (7-10)**. A single dominant hook line is fine too.

## 4. Hook generation + STEPPS scoring (do this before writing the video)
For each video, **draft at least 3 candidate hooks**, then **score each 0-100** against
STEPPS and the heuristics below, and **ship only the highest**:
- **S**ocial currency — makes the viewer look smart/in-the-know to share it.
- **T**riggers — tied to a top-of-mind moment (a news beat, a daily frustration).
- **E**motion — high-arousal (awe, inspiration, righteous urgency), not flat.
- **P**ublic — visibly shareable; a line people will quote.
- **P**ractical value — a real, usable takeaway.
- **S**tories — rides a narrative (transformation, build, underdog).
Plus viral-hook heuristics: **pattern interrupt, curiosity gap, controversy, specificity
(numbers/names), transformation (before→after).** In the queue summary, note the chosen
hook and its score so reviewers see the pick.

## 5. Sora prompt style — VARIETY is the anti-generic rule
Cinematic, vertical 9:16, documentary realism, shallow depth of field, **electric blue +
deep navy** palette (warm gold accents allowed for the culture pillar). Add subtle ambient
audio. **No logos, no copyrighted music, no brand text** (on-screen words are burned in
later via `overlay_beats` — don't rely on Sora text).

**CRITICAL — no two videos in a batch may share the same scene.** The default "a person
sitting at a desk staring at a glowing monitor" is BANNED as a repeated template — it reads
as generic stock-AI b-roll. Each video gets a DISTINCT visual world. Vary the **subject**
(not always a human; use cityscapes, hands, screens, objects, abstract forms, motion), the
**composition** (close macro / wide environment / overhead / profile / moving POV), and the
**camera move**. Pull the world from the pillar:

- **AI fluency** — abstraction of intelligence/orchestration: glowing agent-graphs forming
  in mid-air, a split world of "scrolling vs building", data becoming structure. Often NO
  person, or a person as a small silhouette against a vast system.
- **Black tech culture** — Afrofuturist grandeur: a luminous African-futurist cityscape at
  golden hour, bold architecture, the standalone "8" motif as a monument or skyline element,
  warm gold + electric blue. Cultural, cinematic, aspirational.
- **Build-in-public** — the real work: macro of a terminal/cursor/PR merging, hands on a
  keyboard, a pipeline animating, a screen recording feel — raw and specific, not posed.
- **Cinematic coding** — code as art: light trails, particles, glass, reflections, extreme
  macro of type and cursors, neon-in-the-dark texture — abstract and premium, often faceless.
- **Transformations** — a clear before→after arc: a figure walking from a dim/passive space
  into a lit/active one, a state change, a path/threshold, dawn breaking. Emotion via light.

Always describe a strong **first beat** (0-2s, instant) and a clear **transformation/metaphor**
(2-7s). Bold, specific, cinematic — never a safe centered talking-head. Avoid: generic
startup office, stock "person at desk with floating UI", cheap RGB, cluttered sci-fi,
garbled screen text.

## 6. Caption + CTA
Short, native, hook-style. End with the CTA, lightly rotated around:
**"Follow Layer8Culture if you're building with AI."** (variants: "Follow @layer8culture
if you're building with AI." / "Save this if you're done just watching AI happen.").
For YouTube, also set a `youtube_title` ending with `#Shorts`.

## 7. Schema (per video, in the queue)
```
{
  "id": "YYYYMMDD-layer8culture-tiktok-n",
  "account": "layer8culture",
  "category": "<one of the 10 content categories>",
  "platform": "tiktok",            // master video; cross-post the best 1-2 to instagram + youtube
  "format": "reel",
  "schedule_time": "...",
  "text": "native caption ending with the Follow CTA",
  "hashtags": ["#Layer8Culture", "#AI", "#TechTok", "..."],
  "viral_pillar": "ai-fluency | black-tech-culture | build-in-public | cinematic-coding | transformation",
  "hook_score": 0-100,             // STEPPS score of the chosen hook (for the summary)
  "visual": {
    "source": "openai",
    "aspect": "9:16",
    "quality": "medium",
    "openai_prompt": "...the cinematic 9:16 base still Sora animates (no text)...",
    "headline": "the hook line (used for the cover frame)",
    "reel": {
      "mode": "sora",
      "sora_prompt": "...per section 5 — a strong first beat + transformation...",
      "seconds": 8,                // 8 or 12
      "overlay_beats": [
        { "text": "NOT BEHIND.", "start": 0, "end": 2 },
        { "text": "JUST EARLY.", "start": 2, "end": 7 },
        { "text": "START BUILDING", "start": 7, "end": 10 }
      ]
    }
  }
}
```
The cross-posts to Instagram (Reel) and YouTube (Short) reuse the rendered, text-baked
mp4 via `visual.source: "reuse"`, `visual.of: "<this id>"` — see the TIKTOK/YOUTUBE
sections of `scripts/generation-prompt.md`.

## 8. Template library (repeatable; rotate, never reuse verbatim within ~14 days)
These are *starting points* — when you use one, **re-cut the beats to <= 4 words / <= 15
chars each** and **give it a fresh, pillar-appropriate visual world** (never re-run the
same "creator in a studio" scene twice in a batch; see §5). Re-score the hook with STEPPS.

**T1 — "You're Not Behind. You're Early." (pillar: AI fluency / transformation)**
- Overlay beats: `NOT BEHIND` (0-2) / `JUST EARLY` (2-7) / `START BUILDING` (7-10)
- Sora: Vertical 9:16 cinematic. A Black creator alone in a dark modern studio at night,
  laptop open, blue ambient light on his face; quiet, focused, premium, minimal. As he
  types, floating holographic words appear — "consumer," "operator," "creator," "builder,"
  "frontier" — and the camera slowly pushes in as they rearrange toward one glowing idea.
  Documentary realism, shallow DoF, electric blue + dark navy, inspirational not cheesy,
  no logos, no copyrighted music; subtle keyboard sounds and a deep cinematic bass pulse.
- Caption: "Most people are still watching AI happen. Builders are already moving. Follow Layer8Culture if you're building with AI."

**T2 — "Consumer vs Builder" (pillar: AI fluency)**
- Beats: `EVERYONE'S USING AI` (0-2) / `ALMOST NOBODY'S BUILDING WITH IT` (2-7) / `BE THE 1%` (7-10)
- Sora: split-feeling studio; one side passive scrolling glow, the other an active build
  dashboard; slow push from the scroll to the terminal; electric blue + navy, premium.

**T3 — "The Eighth Layer" (pillar: Black tech culture)**
- Beats: `TECHNOLOGY HAS SEVEN LAYERS` (0-2) / `WE'RE THE EIGHTH` (2-7) / `CULTURE IS THE FRONTIER` (7-10)
- Sora: Afrofuturist studio, a builder framed from behind against a glowing futuristic
  city skyline; a subtle "8" motif in the scene; warm gold meets electric blue.

**T4 — "Built While You Slept" (pillar: build-in-public)**
- Beats: `THIS POSTED ITSELF` (0-2) / `AN AI ENGINE BUILT THE WHOLE THING` (2-7) / `A HUMAN STILL SAYS YES` (7-10)
- Sora: late-night desk, a single screen showing a content pipeline + a pull request;
  a hand hovers over "approve"; calm, documentary, electric blue.

**T5 — "One Prompt Isn't a Workflow" (pillar: cinematic coding)**
- Beats: `ONE PROMPT ISN'T A WORKFLOW` (0-2) / `AGENTS THAT KEEP WORKING` (2-7) / `LEARN TO ORCHESTRATE` (7-10)
- Sora: cinematic terminal close-up, tasks handing off across panes, slow push-in,
  shallow DoF, deep bass.

**T6 — "I Was Just a Consumer" (pillar: transformation)**
- Beats: `A YEAR AGO I JUST WATCHED` (0-2) / `NOW I BUILD WITH AI DAILY` (2-7) / `YOU CAN START TONIGHT` (7-10)
- Sora: a single creator, before/after lighting shift from dim/passive to focused/lit,
  laptop glow, premium navy/blue.

**T7 — "Nobody Is Coming to Teach You" (pillar: AI fluency, high tension)**
- Beats: `NOBODY'S COMING TO TEACH YOU AI` (0-2) / `THE BUILDERS ARE SELF-TEACHING` (2-7) / `OPEN THE LAPTOP` (7-10)
- Sora: lone builder, dark studio, determined, slow push-in to the screen's glow.

**T8 — "$1.25 an Hour" (pillar: build-in-public, specificity)**
- Beats: `THEY TRAINED A MODEL FOR $1.25/HR` (0-2) / `THE COST BARRIER IS GONE` (2-7) / `WHAT'S YOUR EXCUSE` (7-10)
- Sora: cinematic dashboard with falling cost curve as ambient motion, blue/navy, premium.

**T9 — "Build the Future in Rhythm" (pillar: cinematic coding / culture)**
- Beats: `CODE IS A CREATIVE ACT` (0-2) / `BUILD LIKE IT'S ART` (2-7) / `SHIP SOMETHING TONIGHT` (7-10)
- Sora: warm-lit Afrofuturist studio, hands on keyboard, soft city glow through a window.

**T10 — "Stop Scrolling, Start Shipping" (pillar: transformation / CTA)**
- Beats: `STOP SCROLLING AI TAKES` (0-2) / `SHIP ONE REAL THING` (2-7) / `THAT'S THE WHOLE GAME` (7-10)
- Sora: phone-glow to screen-glow transition, push-in to a terminal, electric blue, minimal.

Each template is a starting point — adapt the hook to the day's news/transcript, re-score
with STEPPS, and keep the overlay beats <= 6-8 words. Never ship a near-duplicate of a
recent post.

---
**Attribution:** STEPPS virality framework — Jonah Berger, *Contagious: Why Things Catch
On*; operationalized scoring via ViralLab (github.com/Funghi88/ViralLab).
