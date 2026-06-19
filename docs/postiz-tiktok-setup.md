# Connecting Postiz to TikTok

How to connect the **@layer8culture** TikTok account to the self-hosted Postiz
instance so the content engine can publish its nightly TikTok videos. TikTok is its
**own** Postiz provider with a **separate developer app** from the Meta/Instagram one
— none of the Facebook/Instagram setup is reused here.

> The engine ships 4-6 reach-first TikTok videos/day (cross-posts of the day's
> Instagram Reel + dedicated Sora-2 videos). Each is a 9:16 mp4. See
> `scripts/generation-prompt.md` (TIKTOK section) and `scripts/post_to_postiz.py`.

## 0. Prerequisites

- A [TikTok for Developers](https://developers.tiktok.com/apps) account.
- Postiz reachable over **HTTPS** (the Caddy `sslip.io` domain on the VM, or a custom
  domain). TikTok rejects `http://` redirect URIs **and** pulls the video via a public
  HTTPS URL, so plain HTTP / localhost / private `/uploads` routes will fail.
- A public **HTTPS** domain you control for the app's **Terms of Service + Privacy
  Policy** and for **site verification** (the domain Postiz serves media from).

## 1. Create the TikTok app

1. Go to https://developers.tiktok.com/apps → **Connect an app** / create a new app.
2. **App name:** e.g. `Layer8 Postiz`. Tick **Web** under platforms.

## 2. Add the products

Add both to the app:

- **Login Kit** — set its redirect URI to the Postiz TikTok callback (step 3).
- **Content Posting API** — enable **Direct Post** (the engine publishes directly,
  not upload-to-drafts).

## 3. Add the OAuth redirect URI

Use your `POSTIZ_URL` (the HTTPS Postiz domain). Add this exact URI:

```
https://<your-POSTIZ_URL>/integrations/social/tiktok
```

## 4. Add scopes

Request these scopes (they match what the engine needs to create + publish video):

- `user.info.basic`
- `user.info.profile`
- `video.create`
- `video.publish`
- `video.upload`

## 5. Terms/Privacy + verify your media domain

1. Set a **Terms of Service** URL and **Privacy Policy** URL — both on a public HTTPS
   domain you control.
2. Under the app's **verified sites / URL properties**, **verify the domain Postiz
   serves uploaded media from**. TikTok fetches the video by URL (`pull_from_url`), so
   the media domain must be public over HTTPS and listed here, or publishes fail.
   (Our Postiz is fronted by Caddy for HTTPS — see `files/postiz-deployment.md`.)

## 6. Put the client credentials in Postiz

SSH into the `postiz-vm` (connection details live in the private
`files/postiz-deployment.md`, kept out of git). First check whether the env file
already has commented TikTok placeholders, so you fill them instead of duplicating:

```bash
sudo grep -ni tiktok /opt/postiz/social.env
```

Then add (or uncomment + fill) the app's **Client key** (16 chars) and **Client
secret** (32 chars) in `/opt/postiz/social.env` (e.g. `sudo nano /opt/postiz/social.env`):

```env
TIKTOK_CLIENT_ID="your 16-char client key"
TIKTOK_CLIENT_SECRET="your 32-char client secret"
```

Recreate the container so it loads the new env, then verify both vars are present:

```bash
cd /opt/postiz && sudo docker compose up -d
sudo docker exec postiz printenv | grep TIKTOK
```

## 7. Connect the account in the Postiz UI

1. Open Postiz (`POSTIZ_URL`) and log in with the admin account.
2. **Add Channel → TikTok** → authorize via the TikTok popup → approve scopes for
   **@layer8culture**.

> **Known issue:** the `/integrations/social/tiktok` connect page can occasionally
> crash ("cannot read properties of undefined" / blank page). Retry from a fresh
> incognito window.

## 8. Copy the channel ID into the engine (the `TIKTOK_CHANNEL_ID` secret)

In Postiz → **Settings → API**, copy the connected TikTok channel's **integration
ID**, then set it as a GitHub repo secret:

```bash
gh secret set TIKTOK_CHANNEL_ID --repo layer8culture/layer8-content-engine
# paste the cuid-style integration id when prompted
```

> **Use the Postiz integration ID, not a TikTok ID.** It is a **cuid-style** string
> (like the working Instagram channel's `cmqd9915w0001o5717h436ivp`), not a long
> numeric TikTok user/app id. You only get it **after** the channel is connected, from
> **Settings → API**.

`post_to_postiz.py` reads `TIKTOK_CHANNEL_ID` and fills
`INTEGRATIONS[("layer8culture", "tiktok")]` at publish time. **Until the secret is set,
TikTok posts are skipped — not errored** — so the engine keeps running and starts
publishing TikTok the moment the secret exists.

**Don't know the integration ID?** With `POSTIZ_URL` and `POSTIZ_API_KEY` set in your
shell (same values as the GitHub secrets), run the helper to print every connected
channel's integration ID:

```bash
python scripts/list_postiz_channels.py
```

## 9. Audit / visibility caveat (important)

Until your **TikTok app passes TikTok's audit**, Direct Post is **forced to
`SELF_ONLY` (private)** regardless of the privacy level Postiz sends, and you're limited
to **≤5 Direct Posts / 24h** on a **private** account. So:

- Early TikTok posts publish **privately**, and the 4-6/day target is effectively capped
  until the app is audited.
- The engine already defaults to `PUBLIC_TO_EVERYONE` + `DIRECT_POST`
  (`PLATFORM_SETTINGS["tiktok"]` in `post_to_postiz.py`); once your app is audited, that
  public visibility takes effect automatically — **no code change**.
- To make the private-only phase explicit, you can add a per-post override in the queue:
  `"tiktok_settings": { "privacy_level": "SELF_ONLY" }` (merged over the defaults).

See TikTok's
[Direct Post developer guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines#direct_post_api_-_developer_guidelines).

## App review — product & scope justifications

When you submit the TikTok app for review, the form asks: *"Explain how each product
and scope works within your app or website."* Paste-ready, accurate answers (this is a
self-hosted scheduling tool that posts the owner's own videos to the owner's own
account — keep every claim truthful and scoped to that):

**App overview (lead with this):**

> This app is a self-hosted social media scheduling tool that lets the account owner
> connect their own TikTok account and publish their own pre-produced short videos to
> it on a schedule. It does not offer public sign-in, social discovery, or access to
> any other users' content — only the authenticated owner's own account.

**Login Kit:**

> Login Kit is used solely for OAuth authorization so the account owner can connect
> their own TikTok account. The user clicks "Add TikTok channel," is redirected to
> TikTok to log in and approve scopes, and the app stores the returned access/refresh
> tokens to publish on their behalf. There is no third-party "Sign in with TikTok"
> feature — only the owner authorizing access to their own account.

**Content Posting API:**

> The Content Posting API publishes the owner's own brand videos to their own TikTok
> account via Direct Post. The user produces a 9:16 MP4 + caption, schedules it, and at
> the scheduled time the app sends the video with the user-selected privacy level,
> interaction settings (duet/stitch/comments), and the AI-generated-content disclosure
> where applicable. All content is the user's own; it never posts to other accounts.

**Scope `user.info.basic`:**

> Retrieves the authenticated user's basic profile (open ID, display name, avatar)
> after authorization, to confirm and display which TikTok account is connected and let
> the owner select the correct account to schedule posts to.

**Scope `video.upload`:**

> Transfers the owner's own video file to TikTok as part of publishing a scheduled post
> to their account — the core action of the tool.

**If the form also lists these (Postiz default scopes), justify them too:**

> - `video.publish` — finalizes and publishes the uploaded video to the owner's account.
> - `video.create` — creates the post container (caption + privacy/interaction settings)
>   for the upload.
> - `user.info.profile` — shows the connected account's profile in the dashboard so the
>   owner can confirm the right account. Only list scopes your app actually requests.

**Tips to pass review:** TikTok usually requires a **demo video** of the real flow
(connect account → pick video → set privacy/disclosure → publish → show it live). Note
that the engine's videos are AI-generated (Sora-2) and it sets the **AI-generated
content** flag (`video_made_with_ai`) — reviewers treat that disclosure as a compliance
plus. Don't overclaim multi-user/social features.

## 10. Settings the engine sends (reference)

Every TikTok video is AI-generated (Sora-2), so `video_made_with_ai` is disclosed.
Defaults (`PLATFORM_SETTINGS["tiktok"]`, mirrors Postiz's `TikTokDto`):

| field | default | note |
|---|---|---|
| `privacy_level` | `PUBLIC_TO_EVERYONE` | forced to `SELF_ONLY` until app is audited |
| `content_posting_method` | `DIRECT_POST` | publish directly |
| `duet` / `stitch` / `comment` | `true` | engagement → reach |
| `autoAddMusic` | `no` | Sora clips carry their own audio |
| `brand_content_toggle` / `brand_organic_toggle` | `false` | organic, not branded-content |
| `video_made_with_ai` | `true` | AI-generated disclosure |

Override any of these per post via `"tiktok_settings": { ... }` in the queue JSON.

## Troubleshooting

- **Publish fails / media error:** the video URL TikTok pulls must be public over HTTPS
  and the domain must be in the app's verified sites (step 5). Private `/uploads` or
  http will fail.
- **Insufficient scopes:** re-check step 4; re-connect the channel after fixing scopes.
- **Posts are private even though you set public:** that's the pre-audit restriction in
  step 9 — expected until the app is audited.

## References

- Postiz TikTok provider docs: https://docs.postiz.com/providers/tiktok
- TikTok Content Posting API: https://developers.tiktok.com/doc/content-posting-api-get-started
