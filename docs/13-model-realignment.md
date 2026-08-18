# Realigning the Model to the Business GHL Shows

The object model and the first nine workflows were designed before anyone had
read the GoHighLevel account. The live pull (18 Aug 2026, location
`62iXlYxx`) shows they were aimed at the wrong business. This records what the
evidence says, the two decisions taken, and how to overturn them.

## The mismatch

Every term the original model was built on, counted across the real GHL
vocabulary — custom fields, tags, products, forms, funnels, workflow names,
templates and pipeline stages:

| Assumed | Occurrences |
|---|---|
| `socaas`, `mdr`, `secaas`, `digital forensics`, `incident response` | **0** |
| `cmmc`, `nist 800`, `soc 2`, `iso 27001`, `hipaa`, `pci dss` | **0** |
| `mrr`, `renewal`, `seats`, `audit`, `compliance`, `managed` | **0** |

| Actual | Occurrences |
|---|---|
| `training` | 31 |
| `webinar` | 22 |
| `splunk` | 18 |
| `bootcamp` | 9 |
| `siem` | 8 |
| `payment` | 5 |

Aspire runs a cybersecurity **training and certification** funnel: webinar or
workshop, then nurture, then a meeting with a trainer, then bootcamp
enrolment paid through a FastPayDirect link, then a cohort.

The certification catalogue is explicit in the field `Your Chosen
Certification`: SC-200, SC-100, AZ-900, SC-900, Splunk Core Certified User /
Power User / Advanced Power User, GCP Cloud Digital Leader.

## Usage evidence

**200 form submissions all-time across 46 forms. 26 forms have never received
one.** The live handful:

| Submissions | Form |
|---|---|
| 40 | Training - USA-Nov-2025 - Student |
| 36 | Training - USA-Mar2025 |
| 27 | n8n test form |
| 25 | Webinar-Qradar-Splunk |
| 16 | Automate SOC Level-1 with Agentic AI + Splunk SIEM |
| 16 | Webinar-Registration |
| 10 | Training - USA-Mar2025-Download-Sylabus |
| 9 | Bootcamp-January-26 |

Submission volume is lumpy and event-driven — 44 in Dec 2025, 34 in May 2026,
1 in Feb 2026 — which is what a webinar business looks like, not a steady
inbound pipeline.

## Decision A — keep the service objects, do not delete them

**Taken: A2.** `serviceSubscription`, `complianceEngagement` and
`phishingBaseline` stay provisioned.

They hold no data today and the GHL account has no trace of them. The
argument for deleting them is real. They stay anyway because:

- Deleting a provisioned object in Twenty is destructive and awkward to
  reverse; leaving three empty objects costs nothing.
- Aspire sells security services. Absence from **GoHighLevel** is not absence
  from the business — GHL is the marketing system, and a SOC contract would
  not necessarily appear in it.
- The training objects are additive. Nothing had to be removed to fit them.

What was **not** acceptable was leaving the automation pretending. See
`SUB Renewal Escalation` below.

To overturn: delete those three blocks from `reference/twenty_schema.yaml`,
drop `wf_renewal` from `BUILDERS`, and remove the renewal branch from
`SYS Daily Health Check`.

## Decision B — workflow logic is inferred, not captured

**Taken: B2.** The six published GHL workflows were rebuilt from surrounding
evidence, not from their internals.

`GET /workflows/` returns name, status and dates only. Confirmed again this
session; the standing feature request behind it is unresolved since 2022
(`docs/02-ghl-api-findings.md`). Capturing internals needs an authenticated
browser session, which needs an operator at a keyboard.

Every rebuilt workflow is therefore an **inference from evidence** — form
names and submission counts, custom field keys and options, tag vocabulary,
trigger-link destinations, calendar names, template names. Each is listed
below with what it was inferred from, so it can be checked against the GHL UI
in minutes rather than re-derived.

| GHL workflow (published) | Rebuilt as | Inferred from |
|---|---|---|
| Bootcamp Payment Automation | `ENR Bootcamp Enrolment` | 6 FastPayDirect tier links, 126 workflow-issued invoices, `CST-USA-2026` invoice template, `Bootcamp-January-26` form |
| Training Automation Follow-up | `LEAD Nurture Sequence` (existing) + `EVT Post-Webinar Follow-up` | `Training-*` forms, `training-interested` / `watched training webinar` tags |
| 1-on-1 Consultation | `BOOK Trainer Appointment` | `1-on-1 Cyber Security Career Consultation` and 7 other calendars, `booked an appointment` tag |
| FrontDesk AI - Post Conversation Router | `AI FrontDesk Handover` | the 7 `fdai_*` contact fields |
| UAE Email Test | not rebuilt | a test; `dubai` tag is the only trace |
| n8n workflow | not rebuilt | already n8n's job |

The 16 draft workflows were never published and are not rebuilt. Eight are
named `New Workflow : <timestamp>`.

**Verify before cutover.** An inference that is 90% right is
indistinguishable from one that is 100% right until go-live day.

## What was verified here, and what was not

This machine has no Twenty or n8n running, so nothing below was executed
against a live stack.

| Check | Result |
|---|---|
| `build_n8n_workflows.py` — lints and emits | 19 workflows, clean |
| `validate_workflow_queries.py --offline` | 85 Twenty calls, 0 problems |
| Write bodies vs schema | 48 bodies, all fields declared, all dates `.toUTC().toISO()` |
| Enum options build | 31 objects, 232 options |
| Negative control | injected `orderBy=`, an unknown field and `limit=500`; all three caught |

`--offline` was added to the validator for exactly this situation. It checks
against `reference/twenty_schema.yaml` rather than a running instance, so it
cannot see drift or anything provisioned by hand, and it says so in its own
output rather than claiming a live pass.

**Still required before this is trusted:** `twenty_provision.py` against a
real instance to create the six new objects, then
`validate_workflow_queries.py` without `--offline`, then
`prove_workflows.py`. The new proof suites — `webinar`, `enrol`, `ai`,
`tags` — have never been executed.

## SUB Renewal Escalation

Kept, but it no longer reports success over an empty table. It now
distinguishes *"scanned, nothing due"* from *"scanned, nothing exists"* and
records the latter as a `dormant` run, so the daily health check can tell the
difference between an automation that is working and one that has nothing to
work on. If the service line becomes real, it starts producing without a
code change.
