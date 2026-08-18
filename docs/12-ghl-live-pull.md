# GHL Live Pull — 18 August 2026

Read-only. GET only, 50 requests, throttled. Location
`62iXlYxxHYUv14IS2LjG` — *Aspire Tech Services and Solution Corp*, US,
`America/New_York`.

No contact records, conversation bodies, message content, call recordings or
form-submission payloads were retrieved. Endpoints classed `volume` return
**counts, field names and categorical histograms only** — bodies are discarded
before anything is written. Raw output is in gitignored `raw/`; coverage is
`out/pull_coverage.csv`.

**37 of 43 endpoints returned data.** The token is a **sub-account** Private
Integration, so the two agency endpoints (snapshots, sub-account list) were
skipped, not failed.

Everything the public API can reach has been pulled. The remaining unknowns
(workflow internals, the suppression list) are **not reachable with a Private
Integration token at all**, so keeping this one alive buys nothing.

---

## What is actually in the account

| Area | Count | Note |
|---|---|---|
| Custom fields | **53** | **all on `contact`** — zero on opportunity |
| Custom values | 6 | Calendar Link, Support Phone/Email, Review Link, Business Hours, Privacy Link |
| Tags | 57 | heavily used as workflow state and audience segmentation |
| Users | 19 | 18 admin, 1 user; 12 agency-type, 7 account-type |
| Businesses | 19 | |
| Pipelines | 3 | 11, 8 and 4 stages |
| Calendars | 8 | 7 active, 1 inactive; 3 personal |
| Forms | **44** | 42 submissions in 90 days |
| Surveys | 3 | **0** submissions in 90 days |
| Workflows | 22 | **6 published, 16 draft** |
| Trigger links | 16 | |
| Email schedules | 10 | 3 sent, 7 draft |
| Funnels/sites | 13 | |
| Products | 2 | both DIGITAL |
| Invoices | 126 | all `sent`, all `source: workflow` |
| Media files | 188 | |
| Social accounts | 5 | 2 Google, Facebook, Instagram, LinkedIn |
| Email builder templates | **14** | the real template library — see below |
| Email/SMS templates | 0 | `/locations/{id}/templates` genuinely returns 0 |
| Campaigns | 0 | |
| Payment orders / estimates / subscriptions | 0 / 0 / n-a | subscriptions blocked by scope |

### Pipelines and stages

- **CloudSecurity-Training** (11): New Lead, Contacted, Qualified, Hot Lead, Awaiting Payment, Enrolled, Referral Opportunity, Lead Nurturing, Trial/Free Offer, Evaluation, Success
- **Cyber Security Training** (8): New Lead, Contacted, Qualified Lead, Hot Lead, Opportunity, Negotiation, Closed-Won, Closed-Lost
- **Marketing Pipeline** (4): New Lead, Contacted, Proposal Sent, Closed

Twenty's default stages are `NEW, SCREENING, MEETING, PROPOSAL, CUSTOMER` and
there is **no `WON`**. None of the three pipelines maps cleanly; all three need
an explicit stage mapping before any opportunity is migrated.

### The 22 workflows

**Published (6)** — these are the live automation surface:

| Workflow | Last updated |
|---|---|
| 1-on-1 Consultation | 2026-07-08 |
| Bootcamp Payment Automation | 2025-12-30 |
| FrontDesk AI - Post Conversation Router | 2025-10-23 |
| Training Automation Follow-up | 2026-06-23 |
| UAE Email Test | 2025-12-30 |
| n8n workflow | 2026-04-27 |

**Draft (16)** — includes 8 named `New Workflow : <timestamp>`, a 4-stage
Training funnel (Stage 1–4), two identical Splunk workshop drafts, `Test-
Training Automation Follow-up`, `Security Tips Campaign`, and a
`Recipe - Webinar Registration Confirmation & Reminders`.

Draft workflows never ran. They are DROP candidates on structural grounds and
should not consume migration effort — but confirm in the UI before deleting
anything, because `status` is the only signal the API gives.

### Channel evidence (90-day window)

From `/conversations/search`, counts only:

- 20 conversations, **all** `type: TYPE_PHONE`
- last message type: 11 email, 4 SMS, 4 live-chat info, 1 call

Small but **not empty**: email, SMS and voice are all live. That is direct
evidence for the ESCALATE items — Twenty has no SMS, no telephony and no
campaign engine, so those channels need a vendor decision regardless of how
the CRM migration goes.

Invoices are the strongest automation signal in the account: **126 invoices,
every one created by a workflow.** Whatever replaces `Bootcamp Payment
Automation` has to keep producing them.

---

## Known gaps in this pull

| Endpoint | Result | Meaning |
|---|---|---|
| `blog_authors`, `blog_categories` | 401 scope | token lacks `blogs.readonly`; `blog_sites` returned 0, so likely nothing there |
| `payment_subscriptions` | 401 scope | token lacks `payments/subscriptions.readonly`. `/payments/transactions` **is** authorised and returns `totalCount: 0`, so there is no card revenue flowing through GHL — the 126 invoices are issued, not collected here |
| `custom_menus` | 401 scope | marked unverified in the registry; low value |
| `snapshots`, `sub_accounts` | skipped | agency token only |

`templates_email` and `templates_sms` genuinely return **0** from
`/locations/{id}/templates`, re-checked with and without a `type` filter. The
template library lives at `/emails/builder` instead — **14 templates**:

Splunk-Demo-Scheduing-Confirmation · Receipt - Cloud Security Training ·
Email Signature · Training-Thanks-Webinar · Welcome Sequence Training ·
UAE - Campaign · Holiday Campaign · Splunk Certification Bootcamp 2026 ·
9 Feb 26-Webinar AI-Powered Threat Detection · Payment · Newsletter ·
23rd- May2026-Workshop-Automate SOC Level-1 · Eid Templates ·
Promotion-The Infinity AI Build Fest 2026

`editorData` is not readable back, so the **HTML** is what migrates, not the
drag-and-drop structure. Rebuilding these is manual work in whatever sending
tool replaces GHL.

**Workflow internals remain unavailable.** This pull confirms what
`docs/02-ghl-api-findings.md` predicted: `GET /workflows/` returns id, name,
status and dates — no steps, no actions, no triggers. Knowing that
`Bootcamp Payment Automation` is published says nothing about what it does.
Rebuilding those 6 workflows still needs the authenticated-session route
(`scripts/ghl_workflow_capture.py`) or manual UI reading.

---

## Three registry defects the live API exposed

Fixed in `reference/ghl_endpoints.yaml` and re-pulled successfully. The fourth
is the dangerous kind — HTTP 200 and a wrong answer:

0. **`/emails/builder`** returns its array as `builders`, not `data`. The
   registry's `list_key: data` recorded **0 templates** with no error at all.
   There are **14**. A silent zero is worse than a failure: it would have gone
   into the audit as "GHL has no templates" and nobody would have questioned
   a 200.


1. **`/calendars/events`** rejects a location-wide query — 422 *"Either of
   userId, calendarId or groupId is required"*. Now fans out over the 8
   calendars from `raw/calendars.json`. Result: **7 bookings** in 90 days
   across all calendars.
2. **`/surveys/`** caps `limit` at **50**; the registry default of 100 was a
   422. Result: 3 surveys.
3. **`/medias/files`** requires an undocumented `type` parameter. Result: 188
   files.

Also: without a `User-Agent`, Cloudflare answers **1010** to every request from
this host — which reads as an auth failure and is not one. `scripts/ghl_pull.py`
now identifies itself.

---

## What this changes for the audit

- **53 contact fields, 0 opportunity fields.** The Twenty schema's opportunity
  fields have no GHL counterpart to migrate; contact-side mapping is the whole
  job. Many of the 53 are one-off webinar survey questions (`5. Did the webinar
  meet your expectations?`) that belong in a form tool, not as CRM fields.
- **44 forms, 42 submissions in 90 days.** Most forms are dead. Names like
  `Form 31`…`Form 50`, `Application-prebuilt (1..3)` and duplicated
  `Bootcamp-January-26` entries are DROP candidates.
- **6 live workflows, not 22.** The migration surface is far smaller than the
  workflow count suggests.
- **Phone, SMS and email are all in use.** Q1 in
  `docs/01-scope-and-questions.md` — whether GHL is retained for sending — is
  now answerable with evidence, and the answer is that dropping GHL entirely
  requires replacing at least three channels.

Next: fold these counts into `out/feature_audit.csv` as the `evidence` column
for the rows they cover, replacing `unknown` where a count now decides it.

---

## Credential handling

The Private Integration token was written to gitignored `.env` (mode `600`) and
never printed, logged, or committed. `raw/` is gitignored. Delete the
integration in GHL when the audit is finished — nothing in this repository
depends on it staying alive.
