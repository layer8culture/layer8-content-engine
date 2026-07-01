# AIGraph DNS and Analytics Notes

## Current domain posture

- Apex: `layer8culture.io`
- Canonical site: `https://www.layer8culture.io/`
- Apex behavior: HostGator-hosted Apache redirect from apex to `www`.
- Apex A record observed publicly: `192.185.46.48`
- `www.layer8culture.io` CNAME target: `green-bay-00b9e940f.7.azurestaticapps.net`
- Azure Static Web App: `swa-layer8culture`
- Azure resource group: `rg-layer8culture`
- Source repo: `layer8culture/layer8-content-engine`
- Site folder: `site/`

## AIGraph launch route

Initial AIGraph route:

- `https://www.layer8culture.io/aigraph/`

Reason:

- It uses the existing canonical domain.
- It can be deployed through the existing Azure Static Web Apps workflow.
- It creates the first case study under Layer8Culture before a separate domain or subdomain is needed.

Future options:

- `aigraph.layer8culture.io`
- separate domain after validation

## Analytics baseline stack

Selected stack:

- Azure-native baseline checks.
- Microsoft Clarity.
- Google Search Console.

## Azure-native resources created

- Log Analytics workspace: `law-aigraph-layer8culture`
- Application Insights resource: `appi-aigraph-layer8culture`
- Resource group: `rg-layer8culture`
- Region: `eastus2`

The browser telemetry connection string is embedded in `site/aigraph-analytics.js`. This value is not a server secret. It identifies the public browser telemetry destination for page views and AIGraph CTA events.

## Activation steps

### Azure-native

1. Keep `scripts/aigraph_audit.py` running weekly through GitHub Actions.
2. Review `analytics/aigraph/latest.md`.
3. Use Azure Static Web Apps metrics for request-level trend checks.
4. Use Application Insights for page views and CTA custom events from `site/aigraph-analytics.js`.
5. Add a first-party event endpoint later only if richer event storage is needed.

### Microsoft Clarity

1. Create a Clarity project for `layer8culture.io`.
2. Add the Clarity project ID to the site head or a small shared analytics include.
3. Track clicks on AIGraph CTA links with `data-aigraph-event` attributes.

### Google Search Console

1. Add a URL-prefix property for `https://www.layer8culture.io/` or a domain property for `layer8culture.io`.
2. Use either DNS verification through HostGator or an HTML verification file under `site/`.
3. Submit `https://www.layer8culture.io/sitemap.xml`.
4. Track AIGraph queries, impressions, clicks, CTR, indexed URLs, and page coverage.

## No DNS change required for the first launch

The first AIGraph release does not require HostGator changes because it launches as a path under `www.layer8culture.io`.
