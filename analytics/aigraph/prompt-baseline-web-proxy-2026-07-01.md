# AIGraph AI Visibility Prompt Baseline

Generated: 2026-07-01T19:12:06.575131+00:00

## Summary

- Business: Layer8Culture
- Baseline type: autonomous_web_cited_proxy
- Expected runs: 25
- Completed runs: 25
- Completion rate: 100.0%
- Mention rate: 28.0%
- Citation rate: 16.0%
- Inaccurate answer count: 1

## Limitations

- This is not true engine-specific ChatGPT, Perplexity, Gemini, Claude, or Google AI Overview data.
- The baseline uses public web-cited search responses and should be replaced or supplemented when direct engine access is available.
- Some mentions were prompted by including AIGraph in the query and are marked low-confidence.

## Prompt group summary

| Group | Runs | Mention rate | Citation rate |
|---|---:|---:|---:|
| branded | 5 | 80.0% | 80.0% |
| category | 5 | 20.0% | 0.0% |
| comparison | 5 | 40.0% | 0.0% |
| informational | 5 | 0.0% | 0.0% |
| niche | 5 | 0.0% | 0.0% |

## Competitors mentioned

- OtterlyAI: 13
- Profound: 12
- Peec AI: 10
- Promptwatch: 8
- Semrush: 7
- Ahrefs Brand Radar: 6
- Twitch: 4
- TikTok: 4
- Scrunch AI: 4
- Writesonic: 3
- HubSpot: 3
- Peec: 3
- Goodie AI: 3

## Source domains

- otterly.ai: 12
- quattr.com: 6
- layer8culture.io: 5
- hashmeta.com: 4
- stackmatix.com: 4
- blog.hubspot.com: 3
- nicklafferty.com: 2
- rankability.com: 2
- surferstack.com: 2
- verbatimdigital.com: 1
- semrush.com: 1

## Interpretation

- Branded Layer8Culture visibility is the strongest current signal.
- AIGraph category and niche visibility is still weak, which is expected because the offer just launched.
- Competitor and tool-list answers are dominated by established AEO/AI visibility platforms.
- AIGraph needs more indexable explanatory content, external citations, and case-study proof before it should appear naturally in category and comparison answers.

## Recommended next actions

1. Re-run this baseline after Google indexes the new AIGraph pages.
2. Publish at least one warm-business case study with before/after prompt visibility.
3. Add external citations and profiles that clearly describe AIGraph as an AI visibility audit and monitoring offer.
4. Create deeper resources around AI visibility metrics, audit examples, and ChatGPT visibility tracking.
5. Replace proxy rows with true ChatGPT, Perplexity, Gemini, Claude, and Google AI rows when authenticated access or APIs are available.

## Status counts

- completed_proxy: 25

## How to fill results

For each prompt and engine, add an object to `results` with these fields:

```json
{
  "group": "category",
  "engine": "ChatGPT",
  "prompt": "What companies help businesses track AI search visibility?",
  "business_mentioned": false,
  "business_cited": false,
  "answer_accuracy": "unknown",
  "citations": [],
  "competitors_mentioned": [],
  "status": "completed",
  "engine_type": "true_engine_or_proxy",
  "notes": ""
}
```
