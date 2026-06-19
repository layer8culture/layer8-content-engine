# Connecting Postiz to YouTube

How to connect **both** brand YouTube channels — **@layer8culture** and
**@Layer8CultureRadio** — to the self-hosted Postiz instance so the content engine can
**auto-publish Shorts** to them (and so you can also schedule uploads by hand).

> **Scope.** The engine now **cross-posts its 9:16 reels as YouTube Shorts** to both
> channels — layer8culture 1-2/day, lofi the loop-reel each run (see
> `scripts/generation-prompt.md` / `generation-prompt-lofi.md` and
> `scripts/post_to_postiz.py`). It still **also** drives traffic *to* YouTube via the
> Instagram "Now Live on YouTube" promo posts (`scripts/fetch_youtube.py`). Before
> relying on auto-publishing, read [The forced-private limitation](#the-forced-private-limitation-read-this-first).

## The forced-private limitation (read this first)

Because Postiz is **self-hosted**, the OAuth app is **your own** Google Cloud project.
YouTube's Data API forces **every video uploaded by an unverified app to `private`** —
regardless of the visibility you request — until that project passes Google's OAuth
**verification + security assessment audit** (a months-long, often costly process).

What this means in practice:

- You **can** connect YouTube to Postiz and the engine will auto-upload Shorts today.
- Each uploaded Short lands **private**. To make it visible you either:
  1. **Manually flip it to Public/Unlisted** in YouTube Studio after Postiz uploads it
     (the current workflow — the engine requests `public`, so once verified no change is
     needed), or
  2. Complete Google's **OAuth app verification + security assessment** so uploads post
     at the requested visibility (only worth it at scale).

This forced-private behavior is expected; plan to flip the Shorts in YouTube Studio until
the Google app is verified.

## 0. Prerequisites

- A Google account that **manages** both YouTube channels: **@layer8culture** and
  **@Layer8CultureRadio** (lofi channel id `UC0AQjSCaU9ByaU90XabBbHQ`). One Google Cloud
  OAuth app (one `YOUTUBE_CLIENT_ID/SECRET`) serves both — you just run the "Add Channel"
  + integration-ID steps once per channel.
- Access to the [Google Cloud Console](https://console.cloud.google.com/).
- Admin access to the Postiz VM (`/opt/postiz` on `postiz-vm`).

## 1. Create a Google Cloud project

1. Open the [Credentials – APIs & Services](https://console.cloud.google.com/apis/credentials)
   page and accept the terms.
2. **Create Project** → name it (e.g. `layer8-postiz-youtube`) → **Create**.

## 2. Configure the OAuth consent screen

1. **APIs & Services → OAuth consent screen.**
2. User type: **External** (required — Brand/managed channels won't work with
   "Internal").
3. Fill app name, support email, and developer contact. You do **not** need to publish
   the app.
4. **Test users → Add users:** add the Google account that manages the channel. While
   the app is unpublished, only listed test users can authorize it.

## 3. Create the OAuth client ID

1. **APIs & Services → Credentials → Create Credentials → OAuth client ID.**
2. Application type: **Web application.**
3. Under **Authorized redirect URIs**, add the Postiz redirect URI — use the Postiz
   domain (the `POSTIZ_URL` secret — the Caddy `sslip.io` HTTPS domain):

   ```
   https://<your-POSTIZ_URL>/integrations/social/youtube
   ```

4. **Create.** Copy the **Client ID** and **Client secret**.

## 4. Enable the YouTube APIs

**APIs & Services → Enabled APIs & Services → + Enable APIs and Services**, then enable
each of:

- **YouTube Data API v3** (uploads + channel management)
- **YouTube Analytics API**
- **YouTube Reporting API**

## 5. Put the credentials in Postiz

Add the client ID/secret to `/opt/postiz/social.env` on the `postiz-vm`:

```env
YOUTUBE_CLIENT_ID="your client id"
YOUTUBE_CLIENT_SECRET="your client secret"
```

Restart Postiz so it picks up the env:

```bash
cd /opt/postiz && sudo docker compose up -d
```

## 6. Brand-account extra steps (Google Workspace–managed channels)

If the YouTube channel is a **Brand account** managed under a Google Workspace
organization, you must also trust the app at the org level:

1. App must be **External** with a **test user** added (steps 2 above) — it does not
   need to be published.
2. Go to [admin.google.com](https://admin.google.com) and sign in as a Workspace admin.
3. **Security → Access and data control → API Controls → Manage Third-Party App
   Access.**
4. **Configure new App** → paste your **Client ID** → select your app.
5. Set its scopes / Google Data Access to **Trusted** → **Save**.
6. **Wait at least ~5 hours** for the change to propagate before connecting in Postiz.

(If your channel is a plain personal YouTube account, you can skip this section.)

## 7. Connect BOTH channels in the Postiz UI

1. Open Postiz (`POSTIZ_URL`) and log in with the admin account.
2. **Add Channel → YouTube** → authorize via the Google login popup with the account that
   manages the channel → approve the requested scopes. Pick **@layer8culture**.
3. **Add Channel → YouTube** again and connect **@Layer8CultureRadio**. (Same OAuth app;
   both managing Google accounts must be **test users** on the consent screen — section 2.)
4. Both channels now appear as Postiz channels.

> Remember: uploads arrive **private** (see the limitation above) until you flip them in
> YouTube Studio or complete Google verification.

## 8. Put each channel's integration ID into the engine

In Postiz → **Settings → API**, copy each connected YouTube channel's **integration ID**
(a cuid-style string, not a numeric YouTube id), then set them as GitHub repo secrets so
the engine publishes Shorts to each:

```bash
gh secret set YOUTUBE_LAYER8_CHANNEL_ID --repo layer8culture/layer8-content-engine
gh secret set YOUTUBE_LOFI_CHANNEL_ID  --repo layer8culture/layer8-content-engine
# paste the matching cuid when prompted
```

`post_to_postiz.py` reads these and fills `INTEGRATIONS[("layer8culture","youtube")]` /
`[("lofi","youtube")]` at publish time. **Until a secret is set, that brand's YouTube
Shorts are skipped — not errored** — so the rest of the engine keeps running. (These are
distinct from `YT_CHANNEL_ID`, the RSS channel id `fetch_youtube.py` uses for promos.)

**Don't know the integration ID?** With `POSTIZ_URL` + `POSTIZ_API_KEY` set in your shell,
run `python scripts/list_postiz_channels.py` to print every connected channel's id.

## Troubleshooting

### "Access blocked: app has not completed verification" / can't authorize
The Google account you're authorizing with isn't a **test user** on the OAuth consent
screen, or the consent screen isn't configured. Add the account under **OAuth consent
screen → Test users** (step 2) and retry.

### "redirect_uri_mismatch"
The redirect URI in the Google OAuth client must match Postiz exactly:
`https://<your-POSTIZ_URL>/integrations/social/youtube` (correct scheme/host, no
trailing slash). Update it under **Credentials → your OAuth client → Authorized
redirect URIs**.

### The channel doesn't appear / authorization succeeds but nothing connects
For a **Brand account**, the Workspace **trusted-app** step (section 6) usually hasn't
propagated yet — give it ~5 hours. Also confirm all three YouTube APIs (section 4) are
enabled.

### Uploaded videos are stuck on "private"
Expected for an unverified app — see
[the limitation](#the-forced-private-limitation-read-this-first). Flip visibility in
YouTube Studio, or pursue Google verification.

### Quota
The YouTube Data API has a default daily quota; a single upload costs ~1600 units of
the default 10,000/day, so a handful of uploads per day is fine. Request more in the
Cloud Console if you scale up.

## References

- Postiz YouTube provider docs: https://docs.postiz.com/providers/youtube
- Google — Obtaining authorization credentials:
  https://developers.google.com/youtube/registering_an_application
- YouTube Data API v3: https://developers.google.com/youtube/v3
