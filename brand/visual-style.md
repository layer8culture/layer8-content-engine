# Layer8Culture — Visual Style for AI Image Generation
# Encodes Sections 13 (color), 14 (type), 16 (imagery), 26 (AI prompting) of brand-guidelines-v2.md
# The engine MUST build every image prompt from these blocks.

## Color system (reference in prompts)
- Deep Black #000000 (backgrounds, negative space)
- Electric Blue #0047FF (accents, glow — intentional, never overused)
- Soft White #F5F5F5 (text, logo)
- Deep Navy Glow #050A1A (atmospheric gradients)
- Warm Walnut #3B2416 (desk surfaces, human warmth)
- Signal Red #FF4B3E (live indicators only, sparing)
- Ratio: 75% black/navy, 15% white/gray, 8% electric blue, 2% warm/red accent

## BASE PROMPT — MAIN ACCOUNT (prepend to every layer8culture generation)
"Create a cinematic Layer8Culture visual in a deep black environment with
electric blue accent lighting, premium creator-tech atmosphere, late-night
builder energy, documentary realism, human-centered AI themes, Black tech
futurism, minimal composition, strong negative space, warm practical lighting,
and high contrast. Vary the composition from post to post so the feed does not
repeat — the SCENE SPECIFICS for THIS post decide the subject. Draw from a range
of premium looks (do NOT default to the same person-at-a-desk shot every time),
for example: a confident Black creator/host presenting direct-to-camera; a close
detail of premium creator gear (mic, stream deck, dark walnut desk, the
standalone 8 motif on a hoodie or mug); an over-the-shoulder build session; an
abstract Afrofuturistic tech or data-visualization environment with glowing
electric-blue nodes and grids; or an atmospheric branded studio space. Recurring
brand cues when a scene fits: a blank neon sign panel on the wall (an unlit,
lettering-free glowing panel — do NOT render any text or letters; the wordmark is
composited in afterward), the standalone 8 motif, a bookshelf washed in blue
accent lighting. When a person appears they are a confident Black creator,
engaged and intentional, never anonymous side profiles. The image should feel
like a premium technology documentary and creative studio brand.

COMPOSITION FOR TYPOGRAPHY: leave the LOWER-LEFT / lower third of the frame as
clean, uncluttered negative space (a dark, simple area with no faces, fine
detail, or busy elements there) so a branded headline can be composited over it.
Keep the top-right corner clear for the wordmark."

## TYPOGRAPHY OVERLAY (composited after generation — do NOT ask the model to render it)
# The image models render garbled text, so openai_gen.py composites clean brand
# type onto each image, infographic / title-card style. The generation step
# supplies the words via the post's visual.headline / visual.subtext fields.
# - Headline: Space Grotesk Bold, UPPERCASE, wide tracking, large and cinematic.
#   Soft White #F5F5F5, with an optional single Electric Blue #0047FF accent word
#   (visual.accent). Keep it SHORT — ideally 2–5 words, max ~7.
# - Subtext: Inter, one short supporting line (optional), Soft White.
# - A deep-black bottom scrim is added automatically for legibility; the wordmark
#   is overlaid top-right. Because the message lives in the typography, the
#   generated background should be atmospheric and varied, not literal.

## PER-FORMAT VISUAL NOTES (single / carousel / reel / story)
# Same color system, BASE PROMPT, and NEGATIVE PROMPT apply to every format.
# - single: one image. Reserve lower-left negative space for the headline,
#   top-right for the wordmark (as above).
# - carousel: every slide is its own generation with its own headline. Keep the
#   look cohesive (same palette/energy) but give each slide a DISTINCT scene so the
#   set doesn't look like one image repeated. Slide 1 (cover) = the boldest, most
#   scroll-stopping frame; the final slide = a clean CTA frame.
# - reel: the openai_prompt makes the BASE STILL that gets animated (Ken Burns +
#   on-screen beats). Compose it 9:16 with the LOWER THIRD kept clean for the
#   headline AND the UPPER THIRD kept calm/simple — animated text "beats" are
#   composited there, so avoid faces or fine detail up top.
# - story: 9:16, single frame, simple and legible at a glance; leave room for the
#   interactive sticker/question. Treat it as a quick daily touchpoint, not a hero.

## LOFI BASE PROMPT (prepend to every lofi generation — use INSTEAD of the main BASE PROMPT)
# Per Section 20 of brand-guidelines-v2.md. Illustrated, not photorealistic.
"Create a cinematic anime-inspired, illustrated Layer8Culture LoFi scene (not
photorealistic) — a densely detailed, lived-in environment. A Black coder or
creator at work, lit by a warm desk lamp against a deep blue night. A futuristic
African city skyline glows through a large window. A blank neon sign panel on
the wall (an unlit, lettering-free glowing panel — do NOT render any text or
letters; the wordmark is composited in afterward), and the standalone 8 motif
present somewhere in the scene (on a hoodie, a mug, or the wall). Bookshelves
filled with coding books and West African-inspired objects, plants throughout,
ambient blue and gold lighting, warm and immersive, with a loopable composition.
The image should feel like a calm Afrofuturistic focus room for builders."

## NEGATIVE PROMPT (append to every generation)
"Avoid generic startup office, bright corporate lighting, cheap gamer RGB,
cluttered composition, cartoon props, excessive sci-fi armor, robots, fake
hologram overload, random neon colors, copyrighted franchise references, garbled
screen text, random figurines, tripod-mounted monitors, makeshift setups,
rendered text, signage lettering, and garbled typography."

## LOFI NEGATIVE PROMPT ADD-ON (append to the NEGATIVE PROMPT for lofi generations)
"avoid photorealism, empty minimal rooms, sparse composition"

## TECH THURSDAY ADD-ON
"livestream studio, microphone, laptop, coding dashboard, creator speaking live,
professional broadcast setup, blue-black cinematic lighting, real build session"

## LOFI ADD-ON (optional scene flavor, layered onto the LOFI BASE PROMPT)
"Afrofuturistic city view, Black coder or student, warm desk lamp, calm study
atmosphere, loopable visual, gentle motion potential, culturally intentional
objects, futuristic but grounded, ambient blue and gold lighting"

## Typography note (for graphics with text)
Space Grotesk for headlines (uppercase, wide tracking for labels), Inter for
body. Headlines large and cinematic against deep negative space.

## File naming standards (Section 27)
- Tech Thursday: tech-thursday-thumbnail-YYYY-MM-DD.png
- LoFi: layer8-lofi-<scene>-NN.png
- Sponsor: sponsor-fobflo-<use>-<size>.png
- General social: layer8-<category>-<slug>-NN.png
