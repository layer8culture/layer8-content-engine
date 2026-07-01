# AIGraph Analytics

This folder stores AIGraph SEO, AEO, and crawl-readiness snapshots.

## Files

- `latest.json`, current machine-readable audit output.
- `latest.md`, current human-readable audit output.
- `baseline-prelaunch.json`, public baseline captured before the first AIGraph site changes were deployed.
- `baseline-prelaunch.md`, human-readable prelaunch baseline.

## Current baseline

The prelaunch public baseline found that `robots.txt`, `sitemap.xml`, `llms.txt`, and future AIGraph routes were being served by the Azure Static Web Apps navigation fallback as homepage HTML. The first implementation wave fixes this by adding real static files and excluding crawler files from fallback routing.
