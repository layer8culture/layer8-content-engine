# AIGraph Google Search Console Indexing Checklist

The domain is verified under `layer8culture@gmail.com`.

## Submit sitemap

1. Open Google Search Console.
2. Select the domain property for `layer8culture.io`, usually shown as `sc-domain:layer8culture.io`.
3. Go to **Sitemaps**.
4. Submit:

```text
https://www.layer8culture.io/sitemap.xml
```

If you are in a URL-prefix property for `https://www.layer8culture.io/`, submit only:

```text
sitemap.xml
```

If you are in a URL-prefix property for `https://layer8culture.io/` without `www`, switch to the domain property or add a separate URL-prefix property for `https://www.layer8culture.io/`.

## If Search Console says "Couldn't fetch"

The sitemap is publicly accessible:

- `https://www.layer8culture.io/sitemap.xml`
- HTTP status: 200
- Content type: `text/xml`
- Listed in `https://www.layer8culture.io/robots.txt`

Troubleshooting steps:

1. Confirm you selected `sc-domain:layer8culture.io`, not a non-www URL-prefix property.
2. Delete the failed sitemap submission.
3. Re-submit `https://www.layer8culture.io/sitemap.xml` in the domain property.
4. If using the `https://www.layer8culture.io/` URL-prefix property, submit `sitemap.xml`.
5. Wait 10-30 minutes and refresh. Search Console can show "Couldn't fetch" temporarily even when the file is public.
6. Use URL Inspection on `https://www.layer8culture.io/sitemap.xml` and click **Test live URL**.

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
