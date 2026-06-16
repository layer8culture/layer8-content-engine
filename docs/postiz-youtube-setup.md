# Connecting Postiz to YouTube

How to connect the **@Layer8CultureRadio** YouTube channel to the self-hosted Postiz
instance so you can **schedule video uploads** (long-form sessions or Shorts) from
Postiz.

> **Scope.** This enables **manual scheduling** of uploads to YouTube from the Postiz
> UI. The content engine does **not** auto-generate or auto-post YouTube content — it
> drives traffic *to* YouTube via the Instagram "Now Live on YouTube" promo posts (see
> `scripts/fetch_youtube.py`). Auto-posting was deferred on purpose; see
> [The forced-private limitation](#the-forced-private-limitation-read-this-first).

## The forced-private limitation (read this first)

Because Postiz is **self-hosted**, the OAuth app is **your own** Google Cloud project.
YouTube's Data API forces **every video uploaded by an unverified app to `private`** —
regardless of the visibility you request — until that project passes Google's OAuth
**verification + security assessment audit** (a months-long, often costly process).

What this means in practice:

- You **can** connect YouTube to Postiz and schedule uploads today.
- Each uploaded video lands **private**. To publish it you either:
  1. **Manually flip it to Public/Unlisted** in YouTube Studio after Postiz uploads
     it (simplest), or
  2. Complete Google's **OAuth app verification + security assessment** so uploads
     post at the requested visibility (only worth it at scale).

This is the main reason the engine does not auto-post to YouTube.

## 0. Prerequisites

- A Google account that **manages** the @Layer8CultureRadio YouTube channel
  (channel id `UC0AQjSCaU9ByaU90XabBbHQ`).
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

## 7. Connect the channel in the Postiz UI

1. Open Postiz (`POSTIZ_URL`) and log in with the admin account.
2. **Add Channel → YouTube** → authorize via the Google login popup with the account
   that manages the channel → approve the requested scopes.
3. The @Layer8CultureRadio channel now appears as a Postiz channel; you can schedule
   uploads to it from the calendar.

> Remember: scheduled uploads arrive **private** (see the limitation above) until you
> publish them in YouTube Studio or complete Google verification.

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
