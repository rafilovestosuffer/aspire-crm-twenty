# Tier 5 — Manual Sweep Checklist

For everything no API serves. Roughly 15–20 pages, half a day if you do not get
distracted reading old workflows.

**Copy page text, not screenshots.** Text is searchable, diffable and pasteable
into the audit. An image is none of those, and six weeks from now you will need
to search this for a phone number you half-remember.

Paste findings into `raw/manual_sweep.md` (gitignored). Record **"checked —
nothing configured"** explicitly. An unticked box means nobody looked, which is a
completely different fact from "nothing there", and only one of them is safe.

---

## A. Sending & deliverability *(Cluster A — decides the ESP choice)*

- [ ] **Settings → Email Services** — dedicated sending domain? Which domain?
- [ ] DKIM / SPF / DMARC records currently published
- [ ] Mailgun or other ESP already connected behind GHL?
- [ ] **Sending volume per month** — needed to price any replacement ESP
- [ ] **Unsubscribe / suppression list size, and how to export it**
      ⚠️ Highest-severity item in the audit. See `docs/03`. Confirm the export
      path *works* before any termination date is agreed — not that it exists
- [ ] Email footer / compliance blocks (physical address, unsubscribe wording)

## B. Phone & SMS *(Cluster B — longest lead times in the project)*

- [ ] **Settings → Phone Numbers** — list every number and what it is used for
- [ ] Which numbers appear on the website, in email signatures, on contracts,
      in Google Business? Each is a live external dependency
- [ ] **A2P 10DLC registration status** — brand and campaign registered?
- [ ] Current telephony provider behind LC Phone (Twilio sub-account?)
- [ ] SMS volume per month
- [ ] IVR / call flows configured
- [ ] Call recording enabled? Retention period? Consent notice wording?
- [ ] Voicemail drop audio files — download them

## C. Domains & hosting *(Cluster C — determines how big Cluster C really is)*

- [ ] **Settings → Domains** — every domain pointed at GHL
- [ ] **Is aspiretss.com actually served from GHL, or only funnel subdomains?**
      This single answer can remove most of Cluster C from the project
- [ ] Same question for aspireelearning.com
- [ ] URL redirects configured
- [ ] Tracking codes / pixels installed (GA4, Meta, LinkedIn)

## D. Integrations *(where hidden automation lives)*

- [ ] **Settings → Integrations** — every connected service
- [ ] Stripe / PayPal / Authorize.net connected?
- [ ] Google (Calendar, Ads, Business Profile), Meta, LinkedIn
- [ ] QuickBooks / Shopify
- [ ] **Marketplace apps installed** — no API exposes these, list by hand
- [ ] **Zapier / Make connections touching GHL**
      ⚠️ Ask teammates directly. These often sit in a personal account and are
      invisible from inside GHL. Absence of evidence is not evidence of absence
- [ ] Inbound webhooks *into* GHL from external systems — these break silently
      at cutover unless repointed
- [ ] Existing API keys / private integrations, and who owns each

## E. AI features *(prompts are assets — export them regardless of disposition)*

- [ ] **Voice AI agents** — copy every prompt verbatim
- [ ] Conversation AI bot — prompt, training data, escalation rules
- [ ] Content AI usage
- [ ] Which AI features consume credits, and monthly spend

## F. Memberships & courses *(Cluster G — likely duplicate)*

- [ ] Courses / memberships configured, and **active member count**
- [ ] Client portal in use?
- [ ] Communities in use?
- [ ] Certificates issued?
- [ ] **Overlap with aspireelearning.com** — if A-SAT already lives there,
      this whole cluster is probably DROP

## G. Reporting *(what anyone actually looks at)*

- [ ] Custom dashboards configured — and **who opens them, how often**
- [ ] Scheduled/emailed reports going to anyone
- [ ] Attribution reporting in active use?
- [ ] A dashboard nobody has opened in 90 days is a DROP candidate

## H. Users & access

- [ ] User list with roles — cross-check against `raw/users.json`
- [ ] Anyone outside the company with access (contractors, agencies)?
- [ ] SSO configured?
- [ ] Mobile app — **does anyone actually use it?** Twenty has no native mobile
      app, so this is a real gap only if the answer is yes

## I. Workflow execution evidence *(fills the API gap)*

`docs/02` §2: execution counts are not available via API, and the discard pass
depends on them.

- [ ] For each published workflow, open it and record enrolment/execution count
      over the window
- [ ] **Re-check anything named like renewal, annual, review or compliance at
      365 days, not 90** — Aspire sells annual engagements, and a yearly
      workflow looks dead in a quarterly window (`docs/01` Q5)

---

## Agency-only *(skip if Snapshots is absent from the menu)*

- [ ] Agency View → Account Snapshots — create a snapshot of the live sub-account
- [ ] Snapshot Assets → **Export CSV** → save to `raw/snapshot_assets.csv`
- [ ] Diff against `out/pull_coverage.csv`; anything present in the snapshot but
      absent from the pull joins this checklist
- [ ] Sub-account list — confirm how many are in audit scope
- [ ] SaaS mode / rebilling — only relevant if Aspire resells GHL to clients

---

## Closing the sweep

Every box is ticked or explicitly marked not-applicable. Then re-run:

```bash
python scripts/build_audit.py
```

and confirm the `unknown` count has dropped. **The audit is finished when that
number reaches zero** — not when the interesting parts are done.
