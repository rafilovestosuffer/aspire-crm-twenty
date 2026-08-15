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
| Objects in the CRM | **53** — 28 Twenty standard, **25 custom** |
| Fields and relations provisioned | **177**, from a version-controlled schema, 0 failures |
| Demo dataset | **448** records, deterministic — identical on every rebuild |
| Automations | **6** live workflows, plus 2 that exist only to test the other six |
| Automated checks against the running system | **25**, all passing |

The four objects that carry the business: `serviceSubscription`,
`complianceEngagement`, `trainingAccount`, `phishingBaseline`. Two more carry
the discipline: `consentRecord` (who may be contacted, on which channel, with
proof) and `automationRun` (the execution history GHL never exposed).

### The six workflows

| Workflow | What it does |
|---|---|
| `LEAD Form Intake` | Public form → dedupe → person + company → consent record → score → owner → task → acknowledgement email → sales notification |
| `VEND Send Email` | The **only** path to SMTP. Checks consent, renders the template, logs a `messageLog`. No other workflow may send directly |
| `SUB Renewal Escalation` | Daily. 90/60/30/7 days before renewal, raises tasks and opportunities. GHL cannot do this — it has no subscription object |
| `MSG Tracked Link Redirect` | Click tracking; counts the click, then redirects |
| `OPS Scheduled Sweeps` | Finds what did *not* happen — stale opportunities, no-shows, overdue invoices |
| `SYS Error Handler` | Catches any failure, records it, alerts, and suppresses alert storms |

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
| Failure handling | A deliberate failure produces `automationRun` FAILED carrying the real error, and a delivered alert |
| Scheduled work | Renewals and sweeps run clean against the seeded data |

Run it any time. If something breaks later, it says so.

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
