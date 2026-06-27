# The Real Estate Deal Lab - Visual Style

## Color system

- Navy Deep: `#070F1C`
- Navy: `#0E2034`
- Gold: `#E0A82E`
- Gold Soft: `#F0C662`
- Foreground: `#E8EDF4`
- Muted Slate: `#8FA0B5`

## Base prompt

"Create a premium real estate deal-room visual for The Real Estate Deal Lab. Dark navy background, refined gold accents, serious investor education mood, clean editorial composition, subtle document and numbers texture, professional real estate operator energy, trust and clarity. Use deal-room elements such as pro forma sheets, property photos, marked-up contracts, calculator, blueprint lines, closing table, Florida residential exterior silhouettes, or a private strategy desk. The image should feel premium, practical, and grounded, not flashy."

## Negative prompt

"Avoid luxury flex imagery, mansions as status symbols, Lamborghinis, cash piles, guru seminar energy, fake urgency, cluttered office, cheap clipart, cartoon house icons, garbled text, rendered words, fake logos, random neon colors, sci-fi, lofi, anime, Afrofuturism, and Layer8Culture visual motifs."

## Composition

- Leave clean lower-left or lower-center negative space for composited headline type.
- Keep visuals simple enough to read on mobile.
- Use navy and gold as the dominant brand contrast.
- Do not use a composited wordmark unless a future Deal Lab asset is explicitly added.

## Visual formats

### Deal breakdown
Use marked-up deal sheets, ARV/rehab/rent number blocks, property silhouette, calculator, and a premium desk. Headline should ask a practical deal question.

### Deal Analyzer
Use calculator, scorecard, clean numeric cards, and a subtle 0-100 gauge motif. Avoid fake app screenshots.

### Method carousel
Use one visual idea per slide. Steps: Find, Analyze, Structure, Close, Scale.

### Contractor-to-investor
Use work boots, blueprint, tool belt, hardhat, property exterior, and a clean investment worksheet. Make the transition from trade skill to ownership feel premium.

### Community / Skool
Use private deal room, roundtable, notebook, Zoom call grid as abstract shapes, and worksheet stacks. Do not render app UI.

### Advisory
Use high-touch strategy desk, clean contract, underwriting notes, and private consultation mood. Keep it premium and restrained.

## Typography

OpenAI visuals should not render text. `openai_gen.py` composites headlines using the engine font stack. Headlines should be short, direct, and deal-specific.

## File naming

Generated files should continue using post IDs under `assets/generated/`.
