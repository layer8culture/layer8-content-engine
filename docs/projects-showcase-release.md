# Projects showcase release

The homepage Projects section reuses the approved four-card content and supplied
assets in `site/projects/`. Its section is fetched after the exported Next page
has hydrated, then inserted between `#build` and `#stream`. This avoids changing
the serialized React tree that owns the homepage and shared navigation. The small
Projects script adds a matching `/#projects` link to the existing shared
navigation on pages that already contain it; it does not replace the site shell.

The section is client-rendered, so it is not present in the raw homepage HTML and
is unavailable when JavaScript is disabled. Search engines that render JavaScript
can discover it, but this is a less reliable SEO path than server-rendering it
from the authoritative Next.js source. The old upstream Next source is not the
production source of truth; do not rebuild or replace the complete export from it.
If the authoritative Next application source is restored, prefer moving this
section into its normal homepage component and remove the post-hydration
integration after verifying the generated React payload.

The supplied Radio GIF is used only while its card is visible and motion is
enabled. Pausing or requesting reduced motion switches to the supplied poster;
the Sonar radar also stops when offscreen or motion is paused. Keyboard behavior
uses native links and a native button, and the existing responsive menu remains
the owner of its open/closed state.

## Rebuild and validation

```powershell
python scripts\build_visual_guides.py
python scripts\build_projects_showcase.py
python -m unittest tests.test_visual_guides tests.test_projects_showcase
python scripts\validate_site_artifact.py
```

The complete-site artifact validator compares all baseline files with the
preserved deployment revision, permits only the five guide documents and their
shared assets, the Projects assets, and the exact expected Projects script/style
tags on existing shared-navigation pages. Production deployment remains manual
and requires the workflow's `DEPLOY` confirmation after a verified full-site
backup. The same workflow can target the existing `preview` environment first;
use `verify_live_site.py` against that preview hostname before dispatching the
production target. Run `verify_live_site.py` against the production URL after
deployment.
