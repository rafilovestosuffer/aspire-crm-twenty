# GHL account inventory — Aspire Tech Services and Solution Corp

Read-only snapshot of the live GoHighLevel sub-account, taken so the Private
Integration token can be deleted. **No writes** were issued (GET only). **No
PII** is in this file: no contact records, no message bodies, no staff emails
or phones, no form-submission payloads. Volume endpoints kept counts and
categorical histograms only.

Pulled 2026-08-18 against `services.leadconnectorhq.com` with
`Version: 2021-07-28`. 49 GET requests. 37 of 43 registered endpoints returned
data. 2 skipped (agency-level, no company token). 4 returned 401 (scope not on
this token).

Location ID (from the browser URL, not guessed): `62iXlYxxHYUv14IS2LjG`.

---

## What this changes about the plan

1. **Timezone is `America/New_York`.** Set `TZ=America/New_York` on the new
   stack. That is the exact configuration that previously broke every Twenty
   datetime write until `.toUTC().toISO()` landed. See `CLAUDE.md`.
2. **22 workflows exist; only 6 are published.** Nine of the drafts still have
   generated names (`New Workflow : <epoch>`). The live automation surface is a
   fraction of what the sidebar implies. Internals are still not on any public
   API — metadata only (id, name, status, dates).
3. **Bulk email is in use.** Three completed broadcasts in this window each
   targeted ~12.3–12.6k recipients (~3.8–4.1k `successCount`). Twenty cannot
   replace this. Disposition stays **ESCALATE**.
4. **The inbox is live on email, SMS, live chat, and voice** (from the first
   page of conversations — see caveat below). Twenty has no inbox. **ESCALATE**.
5. **No user-defined custom objects.** The three `/objects/` rows are GHL's
   system Contact / Company / Opportunity. Training, compliance, subscriptions
   and phishing still live as contact fields + pipeline stages, not objects.
6. **Calendars are in use** (8 calendars, 7 of them active, 7 events in 90
   days). Twenty has no booking engine. **ESCALATE**.
7. **Funnels/websites are in use** (13 funnels: 10 website, 3 webinar).
   **ESCALATE**.
8. **Payment subscriptions could not be read** (401, scope missing). Invoices
   exist (126, all `status=sent`, all `source=workflow`). Do not assume "no
   recurring revenue" from the 401.

---

## Account

| | |
|---|---|
| Name | Aspire Tech Services and Solution Corp |
| Timezone | `America/New_York` |
| City / state / country | New York, NY, US |
| Agency sub-account flag | `isAgencySubAccount = false` |
| Snapshot | none attached |
| Staff seats | 19 (12 agency/admin, 6 account/admin, 1 account/user) |
| Distinct location IDs on staff records | 2 (this token only reaches one) |
| Duplicate contacts | disallowed (`email` + `phone` unique) |
| SaaS / Twilio rebilling | not activated |

Every location permission flag on this account is **enabled**, including
workflows, funnels, websites, conversations, SMS/email templates, phone,
Facebook Messenger, web chat, membership, blogging, ads. "Enabled" is not
"used" — usage is the tables below.

---

## Coverage

| Endpoint | Class | Status | Count | Notes |
|---|---|---|---|---|
| location_profile | config | ok | 1 | |
| custom_fields | config | ok | 53 | all `model=contact` |
| custom_values | config | ok | 6 | names only in this doc |
| tags | config | ok | 57 | |
| users | config | ok | 19 | roles only in this doc |
| businesses | config | ok | 19 | |
| custom_objects | config | ok | 3 | all SYSTEM_DEFINED |
| pipelines | config | ok | 3 | |
| calendars | config | ok | 8 | 7 active |
| calendar_groups | config | ok | 0 | |
| calendar_events | volume | ok | 7 | fan-out: one GET per calendar; location-wide read is refused |
| forms | config | ok | 44 | |
| form_submissions | volume | ok | 42 | bodies discarded |
| surveys | config | ok | 3 | `limit` must be ≤ 50 |
| survey_submissions | volume | ok | 0 | |
| workflows | config | ok | 22 | metadata only |
| campaigns | config | ok | 0 | legacy drips empty |
| trigger_links | config | ok | 16 | |
| templates_email | config | ok | 0 | |
| templates_sms | config | ok | 0 | |
| email_builder_templates | config | ok | 0 | |
| email_schedules | config | ok | 10 | 3 completed broadcasts |
| conversations | volume | ok | 20 | **first page only** (`paginate: none`); not a total |
| funnels | config | ok | 13 | |
| blog_sites | config | ok | 0 | |
| blog_authors | config | 401 | — | token missing scope |
| blog_categories | config | 401 | — | token missing scope |
| products | config | ok | 2 | both DIGITAL |
| payment_coupons | config | ok | 1 | expired internal coupon |
| payment_providers | config | ok | 1 | `providers: []` — none connected via this endpoint |
| payment_subscriptions | volume | 401 | — | token missing scope |
| payment_orders | volume | ok | 0 | |
| invoices | volume | ok | 126 | all sent; all sourced from workflow |
| invoice_templates | config | ok | 1 | `CST-USA-2026` |
| invoice_schedules | config | ok | 0 | |
| estimates | volume | ok | 0 | |
| social_accounts | config | ok | 5 | Google (2), Facebook, Instagram, LinkedIn |
| media_files | config | ok | 0 | API requires `type` (tried `type=all`) |
| voice_ai_agents | config | ok | 0 | |
| custom_menus | config | 401 | — | unverified + missing scope |
| store_settings | config | ok | 1 | |
| snapshots | agency | skipped | — | needs `GHL_COMPANY_ID` |
| sub_accounts | agency | skipped | — | needs `GHL_COMPANY_ID` |

Live-account quirks (GET query only; the committed puller/registry were not
edited in this pass):

- Cloudflare rejects urllib's default `Python-urllib/3.x` with error 1010.
  A browser-like `User-Agent` is required.
- `/surveys/` rejects `limit > 50`.
- `/medias/files` requires `type` even though the docs list it as optional.
- `/calendars/events` refuses a location-wide read; ask once per calendar id
  and concatenate.

---

## Data model

### Custom fields (53, all on Contact)

No opportunity-scoped fields came back from
`GET /locations/{id}/customFields?model=all`. Types: TEXT 19, RADIO 13,
LARGE_TEXT 9, SINGLE_OPTIONS 5, CHECKBOX 3, NUMERICAL 2, FILE_UPLOAD 1,
MULTIPLE_OPTIONS 1.

They cluster into a few jobs, which is the real model:

- **Webinar feedback** — numbered satisfaction / SIEM / speaker questions.
- **FrontDesk AI** — `fdai_page_url`, `fdai_intent`, `fdai_appointment_interest`,
  `fdai_review_intent`, `fdai_handover_reason`, `fdai_convo_summary`, `fdai_nps`.
- **Training intake** — certification, session selection, university,
  graduation year, job title, resume upload.
- **Generic lead** — Company, Message, Industry, Country/Region, Interest.
- **Tax-accounting experiment** — current tax handling, consultation interest
  (likely a one-off funnel, not core SOC/training).

### Custom values (6 names)

Calendar Link, Review Link, Support Phone, Support Email, Business Hours,
Privacy Link. Values omitted here.

### Tags (57)

Heavy use as **workflow state and list membership**, not just labels: `hot lead`,
`warm`, `cold`, `new lead`, `auto contacted`, `booked an appointment`,
`follow up`, plus dated campaign lists (`soc_registered_may23`,
`registered splunk bootcamp jan-2026`, `webinar attendees`, Bangladesh/NY
list tags). Those should become fields or list memberships in Twenty, not 57
tags.

### Companies / businesses

19 business records. Maps to Twenty Company.

### Custom objects

Contact, Company, Opportunity — GHL system objects only. **No Aspire custom
object for subscriptions, compliance, training accounts, or phishing.** That
is why `reference/twenty_schema.yaml` exists.

---

## Pipelines (3)

| Pipeline | Stages |
|---|---|
| CloudSecurity-Training | New Lead, Contacted, Qualified, Hot Lead, Awaiting Payment, Enrolled, Referral Opportunity, Lead Nurturing, Trial/Free Offer, Evaluation, Success |
| Cyber Security Training | New Lead, Contacted, Qualified Lead, Hot Lead, Opportunity, Negotiation, Closed-Won, Closed-Lost |
| Marketing Pipeline | New Lead, Contacted, Proposal Sent, Closed |

Training is the centre of gravity. There is no SOC-subscription or CMMC
pipeline — those offers are not modelled as opportunities here.

---

## Automation

### Workflows (22) — metadata only

**Published (6):**

- 1-on-1 Consultation
- Bootcamp Payment Automation
- FrontDesk AI - Post Conversation Router
- Training Automation Follow-up
- UAE Email Test
- n8n workflow

**Draft (16):** two named workshop attendee/invitation flows, four "Training
Stage 1–4" goals (webinar → meeting → enroll → payment), Security Tips
Campaign, Recipe - Webinar Registration Confirmation & Reminders, Test-
Training Automation Follow-up, and **nine generated names**
(`New Workflow : <epoch ms>`).

The public API cannot say what any of them *do*. Capturing internals still
needs the authenticated-session route (`docs/02-ghl-api-findings.md`).

### Legacy campaigns

0. Safe DROP unless the UI still shows something the API does not.

### Trigger links (16)

Payment-tier links (Bronze / Silver / Gold / Platinum / Diamond / Titanium),
curriculum/brochure/toolkit downloads, webinar / enroll / schedule-a-trainer.
These are the click-tracking layer in front of training checkout. n8n can
reproduce them (`MSG Tracked Link Redirect` already does).

---

## Conversations & inbox

`GET /conversations/search` is registered with `paginate: none`, so **20 is
the first page, not the account total**. Do not treat 20 as volume.

On that page:

| Histogram | Values |
|---|---|
| conversation `type` | TYPE_PHONE = 20 |
| `lastMessageType` | TYPE_EMAIL 11, TYPE_SMS 4, TYPE_LIVE_CHAT_INFO_MESSAGE 4, TYPE_CALL 1 |

So email, SMS, live chat and voice are all present in recent threads. WhatsApp /
FB Messenger / GMB types were **not** on this page (taxonomy rows for those
channels currently come out `no` — that is "not on the first page", not a
proof the channel was never used). Live chat widget itself is still a UI check.

SMS templates: 0. SMS sending may still happen from workflows without a stored
template.

---

## Email

- Stored email templates: **0**. Builder templates: **0**. SMS templates: **0**.
- Scheduled broadcasts: **10** (3 complete, 7 draft). Completed runs:

| Name | Status | Targeted | Succeeded |
|---|---|---|---|
| August-2026 | complete | 12,649 | 4,073 |
| July-2026 | complete | 12,631 | 4,061 |
| Eid-ul-adha-25 May 2026 | complete | 12,394 | 3,860 |

Drafts named for garment / government / education clients and a newsletter.
This is campaign email at list scale. Twenty Send Email is one mailbox, one
recipient. **ESCALATE** — vendor for bulk send + suppression + domain warm-up.

`feature_audit.csv` still marks EM-03 unknown because the taxonomy points at a
UI check, not `email_schedules`. The schedules file is the evidence.

---

## Calendars

8 calendars, 7 active, 0 groups. Types: 5 round-robin, 2 personal, plus one
inactive "Main Service Calendar". Public names (no personal identifiers):
Book 10-Minute; Schedule a meeting with an expert; 1-on-1 Cyber Security
Career Consultation; Schedule a Meeting; Splunk Demo; plus 2 staff-named
personal calendars and the inactive main calendar.

90-day booking count via per-calendar fan-out: **7 events**. Low volume, but
the booking *surface* is still a cutover miss if it is how consultations are
sold.

---

## Forms, surveys, funnels

- **44 forms.** Mix of real intake (Contact Us, webinar registration, Splunk
  demo, bootcamp, training packages, intern application, FrontDesk/n8n test)
  and leftover generated names (`Form 31`, `Form 37`, `Form 40–45`, `Form 50`).
  42 submissions in the evidence window (payloads discarded). Several forms
  are unused DROP candidates once submission counts are joined per form in the
  UI.
- **3 surveys**, 0 submissions. DROP-candidate except the "Modernize Your SOC"
  webinar feedback survey if it is still attached to a live funnel.
- **13 funnels:** 3 webinar, 10 website (hub.aspiretss, careers, Splunk
  Bootcamp, Payment, brochure, email subscription, thank-you pages, Cloud
  Security Training). This is the public web/webinar layer. **ESCALATE**.
- Blogs: 0 sites. Author/category endpoints 401.

---

## Commerce

- Products: 2 digital training products (Cyber Security Training; Cloud
  Security Certification Training – New York Classroom).
- Coupons: 1 expired internal amount-off, usageCount 0.
- Payment providers: endpoint returned an empty `providers` list. Confirm in
  the UI — invoices are still going out.
- Orders: 0. Estimates: 0. Invoice schedules: 0.
- Invoices: **126**, every one `status=sent` and `source=workflow` (Bootcamp
  Payment Automation is the obvious published candidate).
- Subscriptions: **not readable** (401). Re-pull with
  `payments/subscriptions.readonly` before calling recurring revenue a DROP.
- Store settings present; tax-in-shipping flag readable; no PII copied here.

---

## Social

5 connected accounts: Google Business Profile × 2, Facebook page, Instagram
profile, LinkedIn page. Tokens/oauth ids not copied. Native posting / ads
reporting flags are on; usage of the posting product is a UI check.

Voice AI agents: 0.

---

## Audit CSV

`python3 scripts/build_audit.py` wrote `out/feature_audit.csv` from this pull.

| in_use | n |
|---|---|
| yes | 32 |
| no | 15 |
| unknown | 64 |

32 yes + 15 no = **47 of 111 features now have named evidence**. The remaining
64 are UI / session / missing-scope. Workflow internals (WF-02..WF-09) stay
unknown until the session capture. Criticality and effort_days stay blank —
those are judgement, not inference.

Treat `no` on WhatsApp / FB DM / GMB as **weak**: they rest on one page of
conversations. Confirm in the inbox UI before retiring them.

---

## What was not pulled (and why)

| Gap | Why | How to close |
|---|---|---|
| Workflow step/action internals | Public API is metadata-only | Authenticated session route (`docs/02-ghl-api-findings.md`) |
| Payment subscriptions | 401 missing scope | Add `payments/subscriptions.readonly`, re-GET |
| Blog authors/categories | 401 missing scope | Optional; `blog_sites` already 0 |
| Custom menus | 401 | Optional |
| Agency snapshots / sibling sub-accounts | Needs company token | Staff records show a second location id; agency token would list it |
| Conversation volume beyond page 1 | Registry `paginate: none` | UI count, or a later puller change |
| Contact/opportunity records | PII — never | Not in scope |
| This token | You said you will delete it | This document is the replacement |

---

## Token hygiene

The Private Integration token was used only as a Bearer on GET requests.
It is not written to this repo. After you delete it, nothing in git can call
GHL again. Re-running the pull requires a new token and the same location ID
from the URL (`…/location/62iXlYxxHYUv14IS2LjG/…`).
