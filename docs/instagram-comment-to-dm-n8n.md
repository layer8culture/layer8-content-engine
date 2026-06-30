# Instagram Comment-to-DM with n8n

This is the free, self-hosted setup for Layer8Culture's Instagram comment-to-DM
lead generation. It runs beside Postiz on the same VM and keeps publishing inside
the existing approval flow.

## What runs where

| Piece | Role |
| --- | --- |
| Postiz | Schedules approved Instagram posts from `queue/*.json`. |
| n8n | Receives Instagram comment webhooks, matches keywords, sends private replies, and logs outcomes. |
| Meta app | Grants Instagram comment webhook and private reply access. |
| Content engine | Generates reviewed CTA and `comment_to_dm` campaign metadata. |

## Prerequisites

1. `@layer8culture` is an Instagram Business or Creator account.
2. The account is linked to the Facebook Page used by the Meta app.
3. The Meta app has, or can request, these permissions:
   - `instagram_basic`
   - `instagram_manage_comments`
   - `pages_show_list`
   - `pages_read_engagement`
4. The app can subscribe to Instagram comment webhooks for owned media.
5. The Postiz VM has Docker Compose and HTTPS through Caddy or another reverse proxy.

## Recommended first campaign

```json
{
  "campaign": "ig_comment_dm_build_map",
  "keyword": "BUILD",
  "offer": "AI Builder Workflow Map",
  "dm_prompt": "Want the workflow map? Here it is: <approved public link>. What are you trying to build with AI right now?",
  "public_reply": "Sent. Check your DMs.",
  "route": "builder_nurture"
}
```

Caption CTA:

```text
Comment BUILD and I will send you the workflow map.
```

## VM setup outline

1. Add n8n to the Postiz VM with its own Docker Compose service.
2. Persist n8n data in a Docker volume.
3. Put n8n behind HTTPS, for example `https://n8n.layer8culture.io`.
4. Store Meta tokens and webhook secrets only in n8n credentials or VM environment files.
5. Do not commit tokens, webhook secrets, private resource links, or expiring links.

Minimal n8n service shape:

```yaml
services:
  n8n:
    image: docker.n8n.io/n8nio/n8n
    restart: unless-stopped
    ports:
      - "5678:5678"
    environment:
      - TZ=America/New_York
      - GENERIC_TIMEZONE=America/New_York
      - N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS=true
      - N8N_RUNNERS_ENABLED=true
      - WEBHOOK_URL=https://n8n.layer8culture.io/
    volumes:
      - n8n_data:/home/node/.n8n

volumes:
  n8n_data:
```

## n8n workflow

1. **Webhook verification**
   - Create a webhook endpoint for Meta verification.
   - Return the `hub.challenge` value when the verify token matches.

2. **Comment event intake**
   - Receive Instagram comment webhook payloads.
   - Extract media ID, comment ID, commenter ID, text, and timestamp.

3. **Keyword filter**
   - Normalize comment text.
   - Match the campaign keyword and safe variants, for example `BUILD`, `BUILD MAP`,
     and `WORKFLOW`.
   - Ignore comments that do not match the active campaign.

4. **Duplicate guard**
   - Store processed comment IDs in n8n data storage, SQLite, Postgres, or a simple
     append-only sheet.
   - Skip any comment ID already processed for the campaign.

5. **Private reply**
   - Call Meta's Instagram private replies endpoint for the comment.
   - Send the approved `dm_prompt` from the queue metadata or n8n campaign config.
   - Keep the message short and resource-first.

6. **Optional public reply**
   - If enabled, post the approved public reply:
     `Sent. Check your DMs.`

7. **Qualification**
   - Ask one builder question:
     `What are you trying to build with AI right now?`
   - Tag or log replies into the selected nurture route.

8. **Measurement**
   - Log post ID, campaign, keyword, comment ID, private reply status, resource URL,
     timestamp, and errors.
   - Export weekly metrics before adding automated analytics digestion.

## Repository behavior

The content engine now supports optional `comment_to_dm` metadata on Instagram posts.
The approval preview displays the keyword, offer, DM prompt, public reply, and route so
a human can review the funnel before merge.

Publishing remains unchanged. Postiz schedules the post. n8n owns the comment trigger
after the post is live.

## Safety rules

- Use only clear user action as the trigger.
- Send one private reply per comment or campaign.
- Keep opt-out handling in the DM flow.
- Do not use deceptive keywords or bait.
- Do not store sensitive tokens or private links in this repository.
