# Visual field guides

The five public guides use the approved visual field guide format. The production
artifact is `site/`; it is a mix of exported Next pages and progressively enhanced
static guides, not the old `styles.css` website. The shared navigation/footer markup,
font assets and shared CSS come directly from the deployed Next export.

## Provenance and rebuilding

`scripts/build_visual_guides.py` uses deployment revision
`b7423550086bbfadd38a98b71e96ff3060fc27f8` as its immutable content/template input.
Azure deployment run `30958611132` successfully shipped that complete artifact on
August 4, 2026. All 93 publicly served files were compared byte-for-byte with
production before this release; the configuration is preserved from that deployed
revision. The original VM source and local Next backup are incomplete for these
five guides and must not replace this full artifact.

```powershell
git fetch --depth=1 origin b7423550086bbfadd38a98b71e96ff3060fc27f8
python scripts\build_visual_guides.py
python -m unittest tests.test_visual_guides
python scripts\validate_site_artifact.py
python scripts\verify_live_site.py --url https://www.layer8culture.io
```

All original explanations, prompts, checklists, reading links, metadata and anchors
remain available. Topic diagrams are distinct: workflow stages, MCP connections,
accountable agent roles, prompting transformations, and the fluency ladder.
Native links, disclosure elements and a native mobile-menu dialog supply keyboard
behavior. Without JavaScript, all topics and original references are available.
Motion stops offscreen, on user pause, and when reduced motion is requested.

Only the guide HTML and its two shared enhancement assets change. Existing Next
runtime and non-guide routes remain byte-identical. Guide HTML intentionally does
not hydrate with its obsolete React Flight payload, which would restore the old
guide. The guide library's links are ordinary document links. Old exported payloads
are retained as baseline files but are not used by the refreshed documents.

Weekly generation preserves reviewed visual guide pages instead of overwriting
them with the legacy template. To change guide content, update the visual builder
and its original reference deliberately; queue generation and human approval
remain unchanged.

## Backup and rollback

Before deploying, create a new backup outside the repository:

```powershell
python scripts\site_backup.py --destination C:\backups\layer8-before-release --revision b7423550086bbfadd38a98b71e96ff3060fc27f8
```

The command archives the full source revision, the full deployable `site/`, verifies
every public file against production, records SHA256 hashes, and writes a rollback
script. `--original-checkout` optionally preserves the dirty local site, campaign
source and relevant generation/deployment scripts as separate reference material.
These references are not the rollback artifact. No credentials are archived.

```powershell
& C:\backups\layer8-before-release\rollback.ps1 -BackupPath C:\backups\layer8-before-release
```

Add `-VerifyOnly` to check the complete rollback artifact without deploying it.

Rollback verifies every baseline file checksum and redeploys the entire original
export to the existing Azure Static Web App. The deployment credential is fetched
in memory and removed from the environment afterward. CLI package version is
pinned; preview environments are optional, not separate infrastructure.

The deploy workflow is manual-only and requires `DEPLOY`. Its artifact validator
refuses missing baseline files or non-guide changes. Direct SWA CLI deployment must
run the same validator and use a complete artifact, never just the guide directory.
