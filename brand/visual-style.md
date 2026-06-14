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
and high contrast. The scene is a built-out premium broadcast studio (not a
makeshift setup): a blank neon sign panel on the wall (an unlit, lettering-free
glowing panel — do NOT render any text or letters; the wordmark is composited in
afterward), the standalone 8 motif on a hoodie or mug, a branded mic flag, a
stream deck on a dark wood desk, and a bookshelf washed in blue accent lighting.
A confident Black creator/host is present and engaged — direct-to-camera or
actively presenting, never anonymous side profiles. The image should feel like a
premium technology documentary and creative studio brand."

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
