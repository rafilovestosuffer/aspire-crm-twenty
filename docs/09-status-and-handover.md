# Aspire CRM — What Exists, What Is Proven, What Is Left

The single document to read before demoing this, deploying it, or explaining it
to anyone. Everything here is measured, not asserted: the numbers come from
`scripts/implementation_audit.py` and `scripts/prove_workflows.py` run against
the live stack, and they change when the system changes.

---

## 1. What this is, in one paragraph

Aspire's CRM is being moved off GoHighLevel onto software Aspire runs itself:
**Twenty** (the CRM — contacts, companies, pipeline, and a data model shaped
like Aspire's actual business) and **n8n** (the automation engine — forms, lead
routing, renewal chasing, email, alerting). Both are open source, both run on
one server, and the data lives in Aspire's own Postgres database. The build is
scripted end to end: one command goes from an empty machine to a working CRM
with the automation running and verified.

## 2. Why move at all

Three reasons, in the order they matter.

**GoHighLevel cannot model this business.** It has contacts, and it has "deals".
Aspire sells recurring SOC subscriptions, CMMC compliance engagements with audit
dates, and training accounts with seat counts and phishing scores over time.
In GHL all of that gets flattened into custom fields on a deal. Twenty holds
them as real objects with real relationships, so "which subscriptions renew in
the next 90 days, and what is their seat usage" is a saved view instead of a
spreadsheet export.

**GoHighLevel hides its own automation history.** There is no API that exposes
what a workflow did, or whether it failed. That gap is what made this audit
hard, and it is why every automation here writes an `automationRun` record back
into the CRM. Every run, success and failure, is queryable.

**Aspire is a security company.** Client data sitting in a marketing SaaS is a
harder conversation than client data sitting on infrastructure Aspire controls,
patches and backs up.

## 3. What is actually built

| | |
|---|---|
| Objects in the CRM | **59** — 28 Twenty standard, **31 custom** |
| Fields and relations provisioned | **198**, from a version-controlled schema |
| Demo dataset | **448** records, deterministic — identical on every rebuild |
| Automations | **17** live workflows, plus 2 that exist only to test the others |

Counts change when the model does; re-derive them with
`scripts/implementation_audit.py` rather than trusting this table after a
schema change.

The 18 August 2026 GHL pull moved this substantially. Six objects and eight
workflows were added to model the training funnel the account actually runs —
webinar, nurture, trainer meeting, bootcamp enrolment, cohort — which nothing
in the original design covered. See `docs/13-model-realignment.md`.

The objects that carry the business are the training funnel:
`trainingProgram`, `cohort`, `enrollment`, `webinarEvent`,
`webinarRegistration` and `aiConversation`. Two more carry the discipline:
`consentRecord` (who may be contacted, on which channel, with proof) and
`automationRun` (the execution history GHL never exposed).

`serviceSubscription`, `complianceEngagement` and `phishingBaseline` remain
provisioned and empty. They describe a managed-security business that does not
appear anywhere in the GHL account; they are kept because absence from the
marketing system is not proof of absence from the company.

### The seventeen workflows

| Workflow | What it does |
|---|---|
| `LEAD Form Intake` | Public form → dedupe → person, and a company only when the address is not a personal one → consent → score on intent → route to a queue with an SLA → task → acknowledgement → sales notification |
| `VEND Send Email` | The **only** path to SMTP. Checks consent, renders the template, logs a `messageLog`. No other workflow may send directly |
| `MSG Tracked Link Redirect` | Click tracking; counts the click, then redirects |
| `MSG Inbound Events` | Closes the consent loop: an unsubscribe or bounce flips the consent record, and a later form submission cannot quietly undo it |
| `LEAD Nurture Sequence` | Day 3, 7 and 14 after an enquiry, then stops. Stops early on a reply, an opt-out, or a real opportunity on that person's company |
| `EVT Webinar Registration` | Public webhook. Person, registration, consent, confirmation — the top of the funnel |
| `EVT Webinar Reminders` | Hourly. The 24-hour and 1-hour reminders, counted on the registration so a re-run cannot send twice |
| `EVT Post-Webinar Follow-up` | Daily. Recording to the absent, survey to the present, attendance written back onto the event |
| `ENR Bootcamp Enrolment` | Public webhook. Creates the enrolment as PAYMENT_SENT, issues the tier's payment link, raises a task. Never marks anything paid |
| `ENR Cohort Operations` | Daily. Chases unpaid enrolments every third day, sends joining instructions, recomputes seat counts from the enrolments themselves |
| `BOOK Trainer Appointment` | Daily. Reminds about tomorrow's meetings across the eight calendars and flags the ones nobody attended |
| `AI FrontDesk Handover` | Webhook for the AI receptionist. One record per conversation instead of seven fields that overwrote each other, and a task when it asks for a human |
| `SEG Tag Sync` | Daily. Classifies each tag and records whether it should stay a tag, become a field, or be dropped |
| `SUB Renewal Escalation` | Daily. 90/60/30/7 days before renewal. Dormant while no subscriptions exist, and says so rather than reporting success over an empty table |
| `OPS Scheduled Sweeps` | Finds what did *not* happen — stale opportunities, no-shows, overdue invoices |
| `SYS Error Handler` | Catches any failure, records it, alerts, and suppresses alert storms |
| `SYS Daily Health Check` | Asks every morning whether the seven scheduled jobs ran in the last 26 hours — each queried by name, not pulled off the latest page of `automationRun`. Does not check backup freshness; that is `verify_restore.py` |

## 4. What "proven" means here

Deployed is not working. These workflows sat in n8n for weeks looking correct
and were completely broken — filters the CRM silently ignored, an error handler
wired to a name that resolved to nothing, consent records created with no person
attached. All of it looked fine in the editor.

So `scripts/prove_workflows.py` does not check that workflows exist. It submits
the real form, follows the real redirect, breaks something on purpose, triggers
the real schedules, and then asks the CRM whether the records are there.

| Proves | Check |
|---|---|
| Lead capture | Person, company, linked consent, scored task, acknowledgement **to the right address** |
| Consent enforcement | A non-consenting contact → send **refused**, no mail, refusal logged with its reason |
| Click tracking | 307 to the destination, click counted |
| Failure handling | A deliberate failure produces `automationRun` FAILED carrying the real error, and a delivered alert. **Laptop `--dev` only** — the failure probe is not deployed on the VPS |
| Scheduled work | Renewals, sweeps and nurture start and finish without node errors (empty CRM is enough; they no-op) |
| Health check | `SYS Daily Health Check` runs and writes an `automationRun`. On a first-day VPS it may correctly alert that yesterday's jobs never ran |

`--production` is the VPS prove: no Mailpit, no failure probe, requires a real SMTP relay and `--live-email`. Run it only against an empty CRM.

Run it any time. If something breaks later, it says so.

### What a passing suite still does not prove

A check only proves the code it actually executed. Defects that passed every
check before they were found:

- The nurture sequence asked for email templates that did not exist. On a fresh
  database no lead is old enough to be nudged, so the send path never ran and
  the suite went green over code that would have thrown on every send.
- Every scheduled scan asked Twenty for 500 records. Twenty's ceiling is **200**
  and it returns 200 with HTTP 200 and no warning. With 32 form submissions in
  the test data, nothing noticed. The workflows now page, and
  `scripts/prove_paging.py` pushes a table past 200 and asserts the scan read
  every row — the only check here that costs a few minutes, and the only one
  that can catch this.
- The daily health check used to read the latest 20 `automationRuns` and look
  for two schedule names in that page. Form intake writes a run on every
  submit, so the schedules fell off the page and the check reported a dead
  worker on a healthy one — or missed a failed sweep. It now queries each of
  the three morning jobs by name and time. Backup freshness is still
  `verify_restore.py`, not this workflow.

Those were volume-dependent: correct on a demo dataset, broken in production
some months in. When adding a check, ask what data would have to exist for it
to execute at all.

## 5. Honest status

111 GoHighLevel features were catalogued. Every one has a disposition.

| Status | Count | Meaning |
|---|---|---|
| **LIVE** | 24 | Verified working on the running stack |
| **NATIVE** | 13 | Twenty or n8n does it out of the box; needs configuration |
| **MODELLED** | 22 | The object exists to hold the data; no automation on it yet |
| **DESIGNED** | 19 | Specified in the replacement guide; nothing built |
| **DEFERRED** | 33 | Deliberately out of scope, each with a recorded reason |

**Working or native today: 37 of 111 (33%). Nothing is "built but never run".**

That last sentence is the one that matters. The dangerous category in any
migration is code that exists, looks finished, and has never executed. It is
empty here.

The 33 deferred are mostly telephony (SMS, voice, IVR, call recording — these
need a licensed carrier, and Aspire barely uses them), website and funnel
hosting (aspiretss.com stays where it is), and course delivery
(aspireelearning.com already does it). They are not gaps; they are decisions.

The 41 still to build are real work, sized in §7. A third of those need something only Aspire can supply — our DNS, our identity provider, our Google account, our GoHighLevel token, our server.

## 6. What it costs

| | |
|---|---|
| Twenty, n8n, Postgres, Redis | £0 — open source, self-hosted |
| Server | One VPS. 4–8 cores and 8–16 GB for real load |
| Email under ~2,000 recipients/day | £0 — the existing Google Workspace relay |
| Email above that | An SMTP relay. Amazon SES is about **$0.10 per 1,000 emails** |
| Booking pages | Cal.com, self-hosted, £0 |
| Quotes and e-signature | DocuSeal, self-hosted, £0 — eIDAS/ESIGN valid |

**There is no irreducible third-party product.** Every GoHighLevel capability
Aspire uses has a native answer, an n8n answer, or a self-hostable one. The
recurring GHL subscription is replaced by a server bill.

The one number to measure before go-live is send volume, because it decides
whether the Workspace relay is enough. Everything else is settled.

## 7. What is left, and how long

Each phase has a gate. Do not start the next until it passes.

| Phase | Work | Time |
|---|---|---|
| ~~1~~ | ~~Prove the automation layer~~ | **done** |
| 2 | Native Twenty config — connect a mailbox, saved views, roles | 3 days |
| 3 | Cal.com (booking) + DocuSeal (quotes, e-signature) | 1 week |
| 4 | Data migration — **export the GHL suppression list first** | 1 week |
| 5 | Production hardening — VPS, ~~TLS~~, queue mode, monitoring, ~~a tested restore~~, runbook, second operator | 3 days |
| 6 | Parallel run, reconcile daily, then cut over | 2–3 weeks |

**6–8 weeks to production**, with a demonstrable system from week one.

### The two things that can actually hurt

**The consent list is unrecoverable.** GoHighLevel's suppression list —
everyone who has unsubscribed or bounced — does not come back after the account
is terminated. Export it and reconcile the count exactly, before any termination
date is agreed. Emailing someone who opted out is a compliance incident, and
Aspire sells compliance.

**Backups are restore-tested.** `scripts/verify_restore.py` dumps a database,
restores it into a scratch database, counts rows in both and compares exactly,
then drops the scratch copy. Both databases verified — Twenty 4,045 rows and
n8n 1,364, restored exactly. The date of the last successful restore is recorded
in `out/restore_verification.json`. Re-run it monthly; an untested backup is not
a backup.

**The deploy has been rehearsed, not just written.** `infra/deploy.sh` ran end
to end — all eleven steps, in order, on a stack brought up from the same compose
files the server uses. It found two things that would each have stopped the real
deploy dead, and both are fixed: the reverse proxy config, and Twenty's metadata
API refusing to answer when the server cannot reach its own public URL (§ the
troubleshooting table in `docs/10-vps-deployment.md`).

**The proxy rules are proven, not assumed.** `scripts/verify_proxy_rules.py`
runs the real `infra/Caddyfile` against the running stack and checks each rule
from a client outside the allowlist: the n8n editor — which holds every
credential in the stack — answers 403, while the public form, inbound webhooks
and the OAuth callback go through. Then it widens the allowlist by the one line
an operator is told to edit and confirms the editor is served. Ten checks, all
passing; recorded in `out/proxy_rule_verification.json`. This matters because
the site looks equally healthy whether that rule works or not.

Keep GoHighLevel read-only for 30–60 days after cutover.

---

## 8. Running it locally

Prerequisites: Docker, 6 GB of free RAM, Python 3.9+.

```bash
git clone https://github.com/rafilovestosuffer/aspire-crm-twenty.git
cd aspire-crm-twenty
./infra/rebuild.sh
```

Eleven gated steps, about 12 minutes, stopping at the first failure: stack up →
verify every layer → create the workspace and n8n owner → object model → demo
data → credentials → validate every CRM API call against the live schema →
deploy and activate → **run the workflows and check what they did** → verify
every layer again.

Then:

| | |
|---|---|
| CRM | http://localhost:3000 |
| Automation | http://localhost:5678 |
| Mail catcher | http://localhost:8025 |
| Public form | http://localhost:5678/form/aspire-contact |

Login for both: `admin@aspiretss.com` / `AspireDemo2026!` — change it before the
machine is on a network anyone else can reach.

Nothing leaves the machine. Email goes to a local catcher, so the send path runs
for real with no mail account and no chance of reaching a live contact.

**Back up `ENCRYPTION_KEY` and `N8N_ENCRYPTION_KEY` from `infra/.env` somewhere
other than this laptop.** Lose the first and every secret Twenty holds becomes
permanently unreadable. There is no recovery path.

### Moving to the company VPS

The laptop stack and the VPS stack are the same files. Nothing is thrown away
and nothing is untested at cutover.

```bash
docker compose -f infra/docker-compose.yml -f infra/docker-compose.vps.yml up -d
```

The overlay binds both ports to loopback so only the reverse proxy reaches them.
In `infra/.env`, set `SERVER_URL`, `N8N_HOST`, `N8N_PROTOCOL=https` and
`N8N_PUBLIC_URL` to the real hostnames, and point `ALERT_WEBHOOK_URL` at the
real chat webhook instead of the local sink. Terminate TLS at the proxy —
several Twenty and n8n features require a secure context.

## 9. The demo — 10 minutes

Order matters. Lead with the data model, land on the live form.

| # | Show | Say |
|---|---|---|
| 1 | A company record: people, subscription, compliance engagement, training account, phishing trend | "GoHighLevel can't model any of this. It forces everything into a deal." |
| 2 | *Renewals next 90 days*, the kanban, *Seats near capacity* | "Renewal exposure and upsell signals, both one click." |
| 3 | A contact timeline with a real email thread and calendar event | "This is where the inbox went for email. Native, syncs every five minutes." |
| 4 | **Fill the public form live on screen** | "Watch." Person appears scored and assigned, task raised, acknowledgement sent. **"That form is hosted by our own stack. No third-party tool."** |
| 5 | Try to send to an opted-out contact | It refuses and logs why. "This is the control we tell clients to implement." |
| 6 | Run renewal escalation | "GoHighLevel cannot do this — it has no subscription object." |
| 7 | The `automationRun` view | "Every run, success and failure. GoHighLevel exposes no execution history at all — that gap is what made the audit hard." |

Watching a record appear from a form submission does more than any document.

## 10. If someone asks a hard question

**"Is this just a hobby project that will break?"** Every claim in this document
is produced by a script that runs against the live system. There are three gates
in the build: the workflow generator refuses to emit code that would fail at
runtime, a validator checks all 28 CRM API calls against the live schema, and 25
checks exercise the real paths. They caught more than twenty defects, several of
which would have been silent in production.

**"What happens when Rafi is on holiday?"** Today, that is a real risk. It is a
Phase 5 gate: a runbook plus a second named operator who has completed one task
from it unaided. Do not skip it.

**"What if Twenty disappears?"** The data is in Aspire's own Postgres. It can be
dumped to CSV or SQL at any time. That is strictly better than the current
position, where the data is in someone else's SaaS.

**"Why not just keep GoHighLevel?"** Because it cannot model subscriptions,
compliance engagements or training seats; because it will not tell us what its
own automation did; and because it costs a recurring fee for a system Aspire
cannot inspect. The replacement runs on one server.
