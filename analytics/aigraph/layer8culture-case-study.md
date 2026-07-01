# Layer8Culture AIGraph Baseline Case Study

## Summary

Layer8Culture was used as the first AIGraph case study. The public prelaunch baseline found 42 SEO and AEO readiness issues across 10 checked URLs. The first implementation wave reduced the AIGraph audit issue count to 0 across the checked URL set.

## Before

- `robots.txt` returned homepage HTML.
- `sitemap.xml` returned homepage HTML.
- `llms.txt` was missing and rewritten to homepage HTML.
- Planned AIGraph routes were missing and rewritten to homepage HTML.
- Checked HTML pages lacked canonical links, Open Graph metadata, Twitter metadata, and JSON-LD.

## After

- `robots.txt` serves `text/plain`.
- `sitemap.xml` serves `text/xml`.
- `llms.txt` serves `text/plain`.
- AIGraph landing, audit, monitoring, case-study, and checklist pages are live.
- Checked HTML pages include canonical links, social metadata, and JSON-LD.
- `scripts/aigraph_audit.py` validates the public site with 0 current issues.

## KPI delta

| KPI | Before | After |
|---|---:|---:|
| URLs checked | 10 | 10 |
| Audit issues | 42 | 0 |
| Valid robots.txt | No | Yes |
| Valid sitemap.xml | No | Yes |
| Valid llms.txt | No | Yes |
| AIGraph offer pages | 0 | 5 |
| Checked HTML pages with JSON-LD | 0 | 7 |

## Next measurement layer

The next AIGraph case-study phase should connect Microsoft Clarity and Google Search Console, then track:

- impressions
- clicks
- CTR
- indexed AIGraph pages
- AIGraph CTA clicks
- AI visibility prompt mention rate
- citation rate
- audit inquiries
