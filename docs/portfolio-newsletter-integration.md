# Portfolio ↔ Newsletter: Subscription Integration (handoff)

This document is a **self-contained brief** for building the newsletter subscribe/confirm/unsubscribe
flow in the **personal portfolio (Next.js)** repo. It is the public-facing half of a feature whose
other half (the `subscribers` table and the newsletter broadcast) lives in the
`Agentic_AI_News_Summary_Service` repo.

## Big picture

- A visitor submits an email on the portfolio's newsletter form.
- The portfolio (server-side) writes a **pending** subscriber to **Supabase Postgres** and sends a
  **double opt-in confirmation email**. The address only becomes active when they click the link.
- The newsletter service (separate, runs on a schedule) emails every **confirmed** subscriber and
  embeds a per-person unsubscribe link that points **back to the portfolio**.
- Unsubscribing happens entirely on the portfolio.

```
Browser ──POST──▶ Portfolio Next.js route ──service_role──▶ Supabase (subscribers)
                                           └──▶ transactional email (confirm / unsub-confirm)

Newsletter service (cron) ──reads confirmed subscribers──▶ sends newsletter w/ unsubscribe link → portfolio
```

## Database (shared Supabase project)

The `subscribers` table is **created and owned by the newsletter service** (via its Alembic
migrations). **Do not create or alter this table from the portfolio** — only read/write rows.
Coordinate any schema change with that repo.

Table shape (for reference):

| column | type | notes |
|---|---|---|
| `id` | bigint identity PK | |
| `email` | text unique not null | |
| `status` | text default `'pending'` | `pending` / `confirmed` / `unsubscribed` |
| `confirm_token` | text unique | you generate this |
| `unsubscribe_token` | text unique not null | you generate this; the newsletter embeds it |
| `subscribed_at` | timestamptz default now() | |
| `confirmed_at` | timestamptz null | set on confirm |
| `unsubscribed_at` | timestamptz null | set on unsubscribe |

### Access — use the `service_role` key, server-side ONLY

- RLS is **enabled with no policies** on `subscribers`. The **`anon`/publishable** key therefore has
  **no access at all** — this is deliberate, so subscriber emails (PII) can never be read with the
  public key.
- All DB access from the portfolio must use the **`service_role`** key (it bypasses RLS), and it
  must live **only in server-side code** (API route handlers / server actions / route handlers) —
  **never** shipped to the browser, never in `NEXT_PUBLIC_*`.
- Do **not** add permissive RLS policies to `subscribers` — that would expose every email to anyone
  holding the publishable key.

### Tokens

Generate both tokens server-side at insert time:
```ts
import { randomBytes } from "crypto";
const token = randomBytes(32).toString("base64url"); // URL-safe, ~43 chars
```
`unsubscribe_token` is **stable for the life of the subscription**; `confirm_token` is single-use.

## Routes to build

### 1. `POST /api/newsletter/subscribe`  (called by the form)
1. Validate the email (format; reject obvious garbage). Optionally rate-limit per IP/email.
2. Upsert by email:
   - **new** → insert `status='pending'` with fresh `confirm_token` + `unsubscribe_token`.
   - **existing `pending`** → reissue `confirm_token`, resend confirmation (idempotent).
   - **existing `confirmed`** → no-op (already subscribed).
   - **existing `unsubscribed`** → flip back to `pending`, new `confirm_token`, resend confirmation.
3. Send the **confirmation email** (see below).
4. **Always return a generic success response** ("Check your inbox to confirm") regardless of which
   branch ran — do **not** reveal whether an email already existed (prevents enumeration).

### 2. `GET /newsletter/confirm?token=<confirm_token>`  (link in the confirmation email)
- Look up by `confirm_token`. If found and not already confirmed: set `status='confirmed'`,
  `confirmed_at=now()`, and clear/expire `confirm_token`.
- Render a simple "You're subscribed 🎉" page. Idempotent (a second click still shows success).
- Invalid/expired token → friendly "link expired, subscribe again" page.
- A GET is acceptable here: the worst a link-prefetch bot can do is confirm a subscription the user
  already requested.

### 3. `GET /newsletter/unsubscribe?token=<unsubscribe_token>`  (link in every newsletter)
- **Read-only landing page.** Look up the email by token and render:
  *"Unsubscribe **you@example.com** from the Agentic AI Digest?"* with an **Unsubscribe button**.
- **Do not change any state on this GET** — email scanners/preview bots pre-fetch links, and a
  destructive GET would silently unsubscribe people. The button does the real action via POST.

### 4. `POST /newsletter/unsubscribe`  (the button, and RFC-8058 one-click)
- Body/query carries the `unsubscribe_token`. Set `status='unsubscribed'`, `unsubscribed_at=now()`.
- Send the **unsubscribe-confirmation email** (see below).
- Render a "You've been unsubscribed" page. Idempotent.
- **This same endpoint must accept the one-click POST** that mail clients send from the
  `List-Unsubscribe-Post: List-Unsubscribe=One-Click` header (the newsletter sets it). That request
  has no CSRF token and no session — the **`unsubscribe_token` is the authorization**, so token-auth
  this route (no CSRF/session required for the one-click path).

## Transactional emails (portfolio sends these)

Use any provider (Resend, SendGrid, Postmark, SMTP). Two emails:

1. **Subscription confirmation** — sent from `POST /subscribe`. Contains the confirm link:
   `https://<portfolio>/newsletter/confirm?token=<confirm_token>`.
2. **Unsubscribe confirmation** — sent from `POST /unsubscribe`. "You've been unsubscribed." May
   include an optional re-subscribe link back to the form.

(The recurring **newsletter** itself is sent by the other service, not the portfolio.)

## Security checklist

- [ ] `service_role` key only in server-side env (e.g. Vercel server env), never `NEXT_PUBLIC_*`.
- [ ] Never use the publishable/anon key for `subscribers`; never add RLS policies to it.
- [ ] Generic success on subscribe (no email-existence disclosure).
- [ ] Unsubscribe is **GET landing → POST action**; the destructive step is never a bare GET.
- [ ] Confirm/unsubscribe authorize via the unguessable token (no login needed); validate the token
      and fail closed on unknown tokens.
- [ ] Rate-limit `POST /subscribe`.
- [ ] Validate email format before insert.

## Environment variables (portfolio)

```
SUPABASE_URL=https://uiqvdoyzwnsszfnnidqy.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<server-only secret key — rotate the one shared earlier>
EMAIL_PROVIDER_API_KEY=<Resend/SendGrid/etc.>
NEWSLETTER_FROM_ADDRESS=the.agentic.times@gmail.com   # or your verified sender
```

## Edge cases to handle

| case | expected behavior |
|---|---|
| Duplicate subscribe (already confirmed) | generic success, no second confirmation email |
| Re-subscribe after unsubscribing | row flips to `pending`, new confirm email |
| Confirm with unknown/expired token | friendly "link expired" page |
| Unsubscribe with unknown token | friendly page; do not error-leak |
| One-click POST from mail client | unsubscribes via token, no CSRF/session needed |
| Double unsubscribe | idempotent success |
