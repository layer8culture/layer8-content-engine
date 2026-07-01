# AIGraph Google Search Console Indexing Checklist

The domain is verified under `layer8culture@gmail.com`.

## Submit sitemap

1. Open Google Search Console.
2. Select the `layer8culture.io` domain property.
3. Go to **Sitemaps**.
4. Submit:

```text
https://www.layer8culture.io/sitemap.xml
```

## Request indexing

Use **URL Inspection** and request indexing for:

- `https://www.layer8culture.io/aigraph/`
- `https://www.layer8culture.io/aigraph/ai-visibility-audit/`
- `https://www.layer8culture.io/aigraph/monthly-monitoring/`
- `https://www.layer8culture.io/aigraph/case-studies/`
- `https://www.layer8culture.io/aigraph/case-studies/layer8culture-baseline/`
- `https://www.layer8culture.io/aigraph/resources/ai-visibility-checklist/`

## Export cadence

- Export again 7-14 days after sitemap submission.
- Export again after 28 days.
- Use page filter: URLs containing `/aigraph`.
- Use query filters for:
  - `aigraph`
  - `ai visibility`
  - `answer engine optimization`
  - `aeo`
  - `chatgpt visibility`

## Import command

```powershell
python scripts\aigraph_gsc_import.py path\to\Chart.csv path\to\Queries.csv path\to\Pages.csv --output-dir analytics\aigraph
```
