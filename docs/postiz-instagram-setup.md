# Connecting Postiz to Instagram

How to connect an Instagram account to the self-hosted Postiz instance so the
content engine can publish to it. Both brand accounts — **@layer8culture** (main)
and **@Layer8CultureRadio** (lofi) — use the **same** Meta app; you just run the
"Add Channel" step once per account.

> The **Facebook Business** method below is recommended because it's the only one
> that supports attaching audio to Reels (the engine publishes Reels). If an account
> isn't linked to a Facebook Page, use the [Standalone](#alternative-instagram-standalone)
> method instead.

## 0. Prerequisites

- Each Instagram account is a **Business or Creator** account
  (Instagram app → Settings → Account type).
- For the Facebook Business method: the IG account is linked to a **Facebook Page**.
- A [Meta for Developers](https://developers.facebook.com/apps/) account.

## 1. Create one Meta app

1. Go to https://developers.facebook.com/apps/creation/ and pick a business portfolio.
2. App type **Other** → **Business** → fill in details → **Create App**.
   One app covers Instagram + Facebook and both brand accounts. Public use later
   requires business verification.

## 2. Set up "Login for Business"

In the app dashboard, add/set up **Instagram → Login for Business**
(Facebook Login for Business).

## 3. Add the OAuth redirect URI

Use the Postiz domain (the `POSTIZ_URL` secret — the Caddy `sslip.io` HTTPS domain
on the VM, or a custom domain once configured). Add this exact URI:

```
https://<your-POSTIZ_URL>/integrations/social/instagram
```

(Standalone method uses `.../integrations/social/instagram-standalone`.)

## 4. Request permissions/scopes

Under advanced/App permissions, request:

- `instagram_basic`
- `pages_show_list`
- `pages_read_engagement`
- `business_management`
- `instagram_content_publish`
- `instagram_manage_comments`
- `instagram_manage_insights`

## 5. Put app credentials in Postiz

Copy **App ID** and **App Secret** (App settings → Basic) into
`/opt/postiz/social.env` on the `postiz-vm`:

```env
FACEBOOK_APP_ID="your app id"
FACEBOOK_APP_SECRET="your app secret"
```

Restart Postiz so it picks up the env:

```bash
cd /opt/postiz && sudo docker compose up -d
```

## 6. (Dev mode only) add the IG account as a tester

While the app is unverified, only test accounts can connect:

1. App dashboard → **App Roles → Roles → Add People → Instagram Tester** → enter the
   handle(s): `layer8culture` and `Layer8CultureRadio`.
2. On each Instagram account, accept the invite at Settings →
   **Apps and websites** (https://www.instagram.com/accounts/manage_access/).

## 7. Connect the account in the Postiz UI

1. Open Postiz (`POSTIZ_URL`) and log in with the admin account.
2. **Add Channel → Instagram** → authorize via the login popup → pick the Page / IG
   account → approve scopes.
3. Repeat **Add Channel** for the second account (@Layer8CultureRadio).

## 8. Copy each channel ID back into the engine

In Postiz → **Settings → API**, copy each connected channel's **integration ID**:

- **@layer8culture** → paste into `scripts/post_to_postiz.py` →
  `INTEGRATIONS[("layer8culture", "instagram")]`, replacing `REPLACE_ME`.
- **@Layer8CultureRadio** → set it as the GitHub repo secret **`LOFI_IG_CHANNEL_ID`**
  (Settings → Secrets → Actions). No code edit needed — the engine reads it at
  publish time and falls back to a placeholder (skips the post) when unset.

Merging an approval PR then schedules each post to the correct account.

## Alternative: Instagram Standalone

If an account isn't tied to a Facebook Page, use **Instagram → API setup with
Instagram Business Login** instead:

1. Redirect URI: `https://<your-POSTIZ_URL>/integrations/social/instagram-standalone`.
2. Put the creds in `/opt/postiz/social.env` as `INSTAGRAM_APP_ID` /
   `INSTAGRAM_APP_SECRET`, then `sudo docker compose up -d`.
3. In Postiz: **Add Channel → Instagram (Standalone)**.

Note: standalone requires a professional IG account and **cannot** attach audio to
Reels.

## Going live (lifting dev-mode limits)

For long-term use beyond test accounts, submit the app for **Meta App Review** with
the scopes above and complete **business verification**. Until then, the
Instagram-Tester route (step 6) keeps things working.

## References

- Postiz Instagram provider docs: https://docs.postiz.com/providers/instagram
- Instagram Graph API: https://developers.facebook.com/docs/instagram-api
