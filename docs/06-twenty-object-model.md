# Twenty Object Model — GHL Parity Layer

**25 custom objects, 132 fields, 20 relations.** Provisioned by
`scripts/twenty_provision.py` from `reference/twenty_schema.yaml`, scripted and
committed rather than clicked through the UI — so the whole workspace rebuilds in
one command when the local instance is reset, which will happen more than once.

---

## The idea in one line

**Twenty becomes the system of record for every GHL domain, including the ones it
cannot execute.** n8n plus a vendor does the sending, booking and billing; Twenty
holds the truth about what happened.

This matters because the alternative — leaving channel data in whatever vendor
sends it — recreates the exact problem this migration is meant to solve: business
truth scattered across systems that do not agree with each other.

## What this does and does not achieve

**Does:** every GHL capability gets a home in the CRM. Nothing has to live in a
vendor dashboard nobody opens. Reporting, segmentation and history stay in one
place.

**Does not:** make Twenty send an email campaign, an SMS, or take a booking. No
configuration achieves that (`docs/02` §6). These objects are the *record* side of
each capability; the *execution* side is the vendor decision in `docs/03`.

Anyone who reads this model as "GHL replaced" has misread it. The honest framing:
**the record layer is solved and buildable today; the execution layer is
procurement.**

---

## Mapping — GHL feature → Twenty object → who executes

| GHL feature | Twenty object | Executed by |
|---|---|---|
| Conversations / unified inbox | `messageLog` | ESP + CPaaS, logged back by n8n |
| Call recording & reporting | `callLog` | CPaaS |
| Email templates & builder | `emailTemplate` | ESP (HTML referenced, not stored) |
| Campaigns / bulk email | `campaign` | ESP |
| Trigger links | `trackedLink` | n8n serves the redirect |
| Forms & surveys | `formDefinition`, `formSubmission` | Form vendor → n8n webhook |
| Calendars / booking pages | `appointment` | Scheduling vendor |
| Products | `product` | — native |
| Estimates / proposals (CPQ) | `quote`, `quoteLineItem` | E-sign vendor for the document |
| Invoices | `invoice` | Finance system |
| Payments / orders | `payment` | Processor |
| Funnels / websites / blogs | `landingPage` | CMS |
| Social planner | `socialPost` | Scheduling tool |
| Reputation / reviews | `review` | Review platforms via n8n |
| Memberships / courses | `membershipEnrollment` | aspireelearning.com |
| Tags | `crmTag` | — native |
| Custom values | `mergeVariable` | — native |
| DND / suppression list | `consentRecord` | — native, **enforced by n8n at send time** |
| Workflow execution history | `automationRun` | n8n writes back |

Plus the four Aspire business objects from master context §5:
`serviceSubscription`, `complianceEngagement`, `trainingAccount`,
`phishingBaseline`.

---

## Two objects worth arguing about

**`consentRecord` — the most important object here.**
Master context §8.3 made GHL authoritative for consent, deliberately. Removing
GHL removes that home, and nothing else in the plan replaces it. This object is
the replacement: per-person, per-channel consent state with source and proof.

Three rules that must hold, or it is decoration:
1. It is populated from the GHL suppression export **before** the GHL account is
   terminated. After termination that data is gone.
2. **Every** send workflow checks it first. A consent record nothing consults
   provides no protection and false assurance, which is worse than none.
3. Vendor-side unsubscribes (ESP webhooks, SMS STOP replies) write back into it,
   or it drifts out of date within weeks.

**`automationRun` — solves this migration's hardest problem, permanently.**
The GHL public API refuses to expose workflow execution history (`docs/02` §2),
which is precisely why the discard pass needs manual UI work. Having n8n write
every run into the CRM from day one means the next person auditing this stack
queries it in one call. It costs one HTTP node per workflow to populate.

---

## Running it

```bash
python scripts/twenty_provision.py --dry-run      # every payload, sends nothing
python scripts/twenty_provision.py --only consentRecord   # one object first
python scripts/twenty_provision.py                # full provision
```

Idempotent — existing objects and fields are skipped, never duplicated, so a
partial run just gets run again. Three passes (objects → fields → relations)
because relations cannot be created before both ends exist.

Written to `out/twenty_provision_report.json`.

### If it fails

Metadata payload shapes vary between Twenty versions. Failures print the API's
own error text rather than a wrapped one. Send the error; the schema takes one
edit, not a rewrite.

The likeliest divergence is relation creation — `relationCreationPayload` is the
current shape but older versions used a separate relations endpoint. Use
`--skip-relations` to provision everything else, then fix relations separately;
scalar fields are the bulk of the value and do not depend on it.

---

## What still has to be decided by a person

- **Volume on `messageLog`.** Logging every marketing email at Aspire's send
  volume will dwarf every other object. Recommendation: log 1:1 and
  transactional messages individually; keep campaign sends aggregated on
  `campaign`. Revisit once Tier 3 gives real volumes.
- **Whether `membershipEnrollment` is needed at all.** If A-SAT already runs on
  aspireelearning.com, this is duplicate structure — drop it rather than
  maintain two truths.
- **`crmTag.migrationAction`.** Every GHL tag needs a decision: keep as tag,
  convert to a field, or drop. Tags encoding workflow state should become
  fields — that is what the column is there to force.
