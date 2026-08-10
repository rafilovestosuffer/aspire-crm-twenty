# GHL API — Verified Findings

Everything here was checked against primary sources on 10 August 2026. Where a
claim could not be verified, it says so. Do not treat unverified items as facts.

---

## 1. The load-bearing finding: workflow internals are not in the public API

The HighLevel public API exposes **workflow metadata only** — id, name, status,
timestamps. It does not expose steps, actions, triggers, conditions, wait
durations, or which template a send step points at.

This is confirmed two ways:

- The documented Workflows resource contains a single read endpoint
  (`GET /workflows/`). There is no steps, versions, or actions endpoint.
- A standing feature request asking for workflow write endpoints states plainly:
  *"The platform API is read-only for workflows... There are no write endpoints
  available."* A related request has sat **"under review" since August 2022** and
  is still open as of May 2026. Requesters report resorting to Selenium and
  manual UI clicking.

**Consequence.** `GET /workflows/` answers *how many workflows exist and how many
are published*. It cannot answer *what any of them do*. Since "what do they do"
is the entire point of this audit, the automation inventory (MT-6) cannot be
completed from the public API at all.

**The route that does work** is the authenticated-session route: open one
workflow in the browser, capture the XHR that returns the step tree from
DevTools, and replay that request across the workflow list. This uses HighLevel's
internal backend, not the public API. Three consequences that must be planned for,
not discovered mid-run:

| Constraint | Effect | Handling |
|---|---|---|
| Session JWT, not a PIT | Expires (hours, not days) | Puller must checkpoint per workflow and resume, never restart |
| Undocumented and unsupported | Shape can change without notice | Store raw responses verbatim; parse in a separate pass |
| Not covered by the read-only PIT scopes | Sits outside the token governance story | Disclose it in the audit writeup rather than leaving it implicit |

This is legitimate for a one-off internal audit of your own account's
configuration. It is not a basis for anything ongoing.

---

## 2. Usage evidence — what can and cannot be measured

The audit's first move is discarding features nobody uses. That only works if
usage is measurable. It is, unevenly:

**Measurable via public API**

| Signal | Endpoint |
|---|---|
| Form submissions in window | `GET /forms/submissions` |
| Survey submissions in window | `GET /surveys/submissions` |
| Calendar bookings in window | `GET /calendars/events` |
| Live conversations by channel | `GET /conversations/search` |
| Active subscriptions, orders, invoices | `GET /payments/*`, `GET /invoices/` |
| Custom field fill rate | *not directly* — see below |

**Not measurable via public API**

- **Workflow execution counts / enrolment history.** No endpoint. This is the
  single biggest gap in the DROP-filter plan: the plan says "any workflow with no
  contacts in 90 days gets dropped," but the number that decision rests on cannot
  be fetched. It has to come from the workflow's execution-log view in the UI, or
  from the same session route as the internals.
- **Custom field fill rate.** Computing "this field is filled on 2% of contacts"
  requires reading contact records — which the no-PII rule forbids. Resolve by
  either (a) accepting a narrower signal: a field referenced by no workflow, no
  form, and no template is a DROP candidate on structural grounds alone; or
  (b) requesting an aggregate count from someone with UI access. Option (a) needs
  no PII and is the default.
- **Installed marketplace apps.** No public read endpoint. Manual enumeration.

---

## 3. Custom fields — two endpoints, different coverage

`GET /custom-fields/object-key/{key}` is documented as supporting
**Custom Objects and Company (Business) only** — the docs state it "will be
extended to other Standard Objects in the future."

For contact and opportunity fields, use `GET /locations/{locationId}/customFields`
instead. The registry uses the location endpoint for exactly this reason. If it
returns empty, that is a finding about the account, not a bug in the puller.

---

## 4. Authentication and versioning

- **Private Integration Tokens** are static bearer tokens, no refresh flow —
  correct choice for a one-off audit. Available at both agency and sub-account
  level. Displayed once at creation and never again.
- Three headers required: `Authorization: Bearer <token>`, `Version: <version>`,
  and `Content-Type` on writes (not applicable here). **Omitting `Version`
  produces an error that reads like an auth failure.**
- **Versioning has changed.** Date-based versions (`2021-07-28`, `2023-02-21`)
  still work and remain fully supported — HighLevel commits to no disruption for
  existing integrations. But there is now a **named `v3`** with additional
  endpoints. The registry defaults to `2021-07-28` for stability and allows a
  per-endpoint override. Worth a single probe on `v3` once the token exists, to
  see whether it exposes anything the audit needs — particularly for workflows.
- Rate limits: **100 requests / 10 seconds** per resource, **200,000 / day**. The
  puller runs at 80/10s deliberately, leaving headroom.
- Token rotation guidance: every 90 days, with a 7-day overlap window.

---

## 5. Snapshots — the second data source

Snapshots capture a sub-account's configuration assets (workflows, funnels,
calendars, forms, dashboards) as a reusable template. The **Snapshot Assets view
supports CSV export**, reachable from Agency View → Account Snapshots.

This matters because it lists asset categories the public API does not serve —
funnels, websites and dashboards among them. It is a *names and counts* list, not
a configuration dump: it tells you a funnel exists, not what is in it.

**Requires agency-level access.** On a sub-account-only token this route is
closed, and the funnel/website/dashboard inventory has to be collected by hand.
This is the first thing to determine — it changes the plan.

**Method:** run the API pull and the snapshot export independently, then diff
them. Anything present in the snapshot but absent from the API pull is the
manual-inspection list — and it will be short.

---

## 6. What Twenty can absorb — the hard ceiling

Verified from Twenty's own documentation:

**Workflow triggers:** record created · record updated · record created-or-updated
· record deleted · manual (Cmd+K or navbar) · schedule (cron, UTC) · inbound
webhook (GET/POST).

**Workflow actions:** create / update / delete / search / upsert record · iterator
· filter · delay · send email · form · code (JS on server-side TS logic
functions) · HTTP request · AI agent (marked coming soon).

**The ceiling that decides the ESCALATE list:** the `Send Email` action sends from
a *synced mailbox*, and the docs state **"only one recipient is possible at the
moment."** Twenty has no campaign engine, no suppression list, no domain warm-up,
no deliverability infrastructure, no SMS, no telephony, no public forms, no
landing pages, no booking pages, no invoicing or CPQ.

That is not a configuration gap. Every GHL feature that depends on *reaching a
person through a channel* has no home in Twenty and no home in n8n either — n8n
can orchestrate a send, but it cannot be the thing that sends.

---

## Sources

- [Get Workflow — HighLevel API](https://marketplace.gohighlevel.com/docs/ghl/workflows/get-workflow/)
- [API endpoints: POST/PUT/DEL for workflows — HighLevel Ideas](https://ideas.gohighlevel.com/apis/p/api-endpoints-post-put-del-for-workflows)
- [Get Custom Fields by Object Key — HighLevel API](https://marketplace.gohighlevel.com/docs/ghl/custom-fields/get-custom-fields-by-object-key)
- [Private Integration Tokens — HighLevel API](https://marketplace.gohighlevel.com/docs/Authorization/PrivateIntegrationsToken/)
- [API Versioning — HighLevel API](https://marketplace.gohighlevel.com/docs/Versioning/)
- [Snapshots Overview — HighLevel Support](https://help.gohighlevel.com/support/solutions/articles/48000982511-snapshots-overview)
- [View Snapshot Assets in GoHighLevel](https://consultevo.com/gohighlevel-view-snapshot-assets/)
- [Workflow Actions — Twenty Documentation](https://docs.twenty.com/user-guide/workflows/capabilities/workflow-actions)
- [Workflow Triggers — Twenty Documentation](https://docs.twenty.com/user-guide/workflows/capabilities/workflow-triggers)
- [What is Twenty — Twenty Documentation](https://docs.twenty.com/user-guide/getting-started/capabilities/what-is-twenty)
