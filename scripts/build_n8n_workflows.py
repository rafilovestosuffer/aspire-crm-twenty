#!/usr/bin/env python3
"""
Generate the n8n workflow library for the Aspire build.

Emits importable workflow JSON into n8n/workflows/. Generated rather than
hand-written so every workflow shares the same conventions: the same error
workflow, the same Twenty credential, the same automationRun logging, the same
node naming. Hand-maintaining twenty JSON files diverges within a fortnight.

Import:  n8n → Workflows → Import from File
   or:   python scripts/n8n_deploy.py   (pushes via the n8n REST API)

Usage:
    python scripts/build_n8n_workflows.py
    python scripts/build_n8n_workflows.py --list
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "n8n" / "workflows"

# Credential names the workflows expect to exist in n8n. Created once, by hand,
# in n8n → Credentials. Never inlined into a node.
CRED_TWENTY = "Twenty API"          # Header Auth: Authorization = Bearer <key>
CRED_SMTP = "Aspire SMTP"           # Send Email (SMTP)
CRED_SLACK = "Aspire Slack"         # Slack API

TWENTY = "={{ $env.TWENTY_BASE_URL }}"
ALERT_CHANNEL = "#crm-alerts"
SALES_CHANNEL = "#sales"


# --------------------------------------------------------------------------
# Node helpers
# --------------------------------------------------------------------------

class Flow:
    """Builds one n8n workflow: nodes laid out left to right, wired in order."""

    def __init__(self, name: str, description: str, error_workflow: bool = True):
        self.name = name
        self.description = description
        self.nodes: list[dict] = []
        self.connections: dict = {}
        self.error_workflow = error_workflow
        self._x = 0

    def add(self, name: str, ntype: str, params: dict, *, version: int = 1,
            creds: dict | None = None, row: int = 0) -> str:
        node = {
            "parameters": params,
            "id": f"{abs(hash((self.name, name))) % (10**12):012d}",
            "name": name,
            "type": ntype,
            "typeVersion": version,
            "position": [self._x, 300 + row * 180],
        }
        if creds:
            node["credentials"] = creds
        self.nodes.append(node)
        self._x += 220
        return name

    def connect(self, src: str, dst: str, *, out: int = 0) -> None:
        conn = self.connections.setdefault(src, {"main": []})
        while len(conn["main"]) <= out:
            conn["main"].append([])
        conn["main"][out].append({"node": dst, "type": "main", "index": 0})

    def chain(self, *names: str) -> None:
        for a, b in zip(names, names[1:]):
            self.connect(a, b)

    # ---- reusable node factories -------------------------------------------

    def twenty(self, name: str, method: str, path: str, body: dict | None = None,
               *, row: int = 0) -> str:
        """An authenticated call to Twenty's REST API, with retry on 429."""
        params: dict = {
            "method": method,
            "url": f"={{{{ $env.TWENTY_BASE_URL }}}}{path}",
            "authentication": "genericCredentialType",
            "genericAuthType": "httpHeaderAuth",
            "options": {"response": {"response": {"neverError": False}}},
        }
        if body is not None:
            params["sendBody"] = True
            params["specifyBody"] = "json"
            params["jsonBody"] = json.dumps(body) if isinstance(body, dict) else body
        n = self.add(name, "n8n-nodes-base.httpRequest", params, version=4,
                     creds={"httpHeaderAuth": {"name": CRED_TWENTY}}, row=row)
        # Transient failures against Twenty are common at the 100/min ceiling.
        self.nodes[-1]["retryOnFail"] = True
        self.nodes[-1]["maxTries"] = 3
        self.nodes[-1]["waitBetweenTries"] = 2000
        return n

    def code(self, name: str, js: str, *, row: int = 0) -> str:
        return self.add(name, "n8n-nodes-base.code", {"jsCode": js},
                        version=2, row=row)

    def iff(self, name: str, left: str, op: str, right: str, *, row: int = 0) -> str:
        return self.add(name, "n8n-nodes-base.if", {
            "conditions": {
                "options": {"caseSensitive": True, "typeValidation": "loose"},
                "conditions": [{
                    "leftValue": left, "rightValue": right,
                    "operator": {"type": "string", "operation": op},
                }],
                "combinator": "and",
            },
            "options": {},
        }, version=2, row=row)

    def slack(self, name: str, channel: str, text: str, *, row: int = 0) -> str:
        return self.add(name, "n8n-nodes-base.slack", {
            "select": "channel",
            "channelId": channel,
            "text": text,
            "otherOptions": {},
        }, version=2, creds={"slackApi": {"name": CRED_SLACK}}, row=row)

    def noop(self, name: str, *, row: int = 0) -> str:
        return self.add(name, "n8n-nodes-base.noOp", {}, row=row)

    def log_run(self, name: str, workflow: str, status: str = "SUCCESS",
                *, row: int = 0) -> str:
        """Write an automationRun record — the execution history GHL never exposed."""
        return self.twenty(name, "POST", "/rest/automationRuns", {
            "workflowName": workflow,
            "status": status,
            "startedAt": "={{ $execution.startedAt }}",
            "n8nExecutionId": "={{ $execution.id }}",
        }, row=row)

    def to_dict(self) -> dict:
        settings: dict = {"executionOrder": "v1"}
        if self.error_workflow:
            settings["errorWorkflow"] = "SYS Error Handler"
        return {
            "name": self.name,
            "nodes": self.nodes,
            "connections": self.connections,
            "settings": settings,
            "meta": {"description": self.description},
            "tags": [{"name": "aspire"}],
        }


# --------------------------------------------------------------------------
# 1. Error handler — build this first, everything else references it
# --------------------------------------------------------------------------

def wf_error_handler() -> Flow:
    f = Flow("SYS Error Handler",
             "Catches any failed workflow. Logs to Twenty automationRun and "
             "alerts Slack. Set as the error workflow on every other workflow.",
             error_workflow=False)

    trig = f.add("Error Trigger", "n8n-nodes-base.errorTrigger", {})
    extract = f.code("Extract failure", """
// n8n's error payload varies by failure mode; normalise it once here.
const e = $input.first().json;
const wf = e.workflow || {};
const ex = e.execution || {};
return [{ json: {
  workflowName: wf.name || 'unknown',
  executionId:  ex.id || '',
  nodeName:     ex.lastNodeExecuted || '',
  message:      (ex.error && ex.error.message) || e.message || 'unknown error',
  // Credit exhaustion on AI nodes returns a failure result rather than a normal
  // error. Retrying it just spends more, so flag it as its own condition.
  isBilling: /credit|quota|billing|insufficient/i.test(
               (ex.error && ex.error.message) || ''),
}}];
""".strip())

    log = f.twenty("Log failure", "POST", "/rest/automationRuns", {
        "workflowName": "={{ $json.workflowName }}",
        "status": "FAILED",
        "errorMessage": "={{ $json.message }}",
        "n8nExecutionId": "={{ $json.executionId }}",
        "startedAt": "={{ $now.toISO() }}",
    })

    recent = f.twenty("Recent failures?", "GET",
                      "/rest/automationRuns?filter[workflowName][eq]="
                      "{{ $json.workflowName }}&filter[status][eq]=FAILED&limit=5")

    dedupe = f.code("Suppress alert storm", """
// One failing workflow can fire hundreds of times. Alert on the first, then
// stay quiet for an hour — an alert nobody can act on trains people to ignore
// the channel, which is worse than no alert at all.
const runs = ($input.first().json.data?.automationRuns) || [];
const hourAgo = Date.now() - 3600_000;
const priors = runs.filter(r => new Date(r.startedAt).getTime() > hourAgo).length;
return [{ json: { ...$('Extract failure').first().json, suppress: priors > 1 } }];
""".strip())

    gate = f.iff("Should alert?", "={{ $json.suppress }}", "equals", "false")
    alert = f.slack("Alert", ALERT_CHANNEL,
                    "=:rotating_light: *{{ $json.workflowName }}* failed\n"
                    "Node: `{{ $json.nodeName }}`\n"
                    "Error: {{ $json.message }}\n"
                    "{{ $json.isBilling ? ':credit_card: *Looks like an AI credit/billing failure — retrying will not help.*' : '' }}\n"
                    "Execution: {{ $json.executionId }}")
    quiet = f.noop("Suppressed", row=1)

    f.chain(trig, extract, log, recent, dedupe, gate)
    f.connect(gate, alert, out=0)
    f.connect(gate, quiet, out=1)
    return f


# --------------------------------------------------------------------------
# 2. Send Email sub-workflow — the single place consent is enforced
# --------------------------------------------------------------------------

def wf_send_email() -> Flow:
    f = Flow("VEND Send Email",
             "Sub-workflow. Input: { personId, templateKey, mergeData }. "
             "Checks consent, renders the template, sends by SMTP, logs a "
             "messageLog. No other workflow may call SMTP directly.")

    trig = f.add("When called", "n8n-nodes-base.executeWorkflowTrigger",
                 {"workflowInputs": {"values": [
                     {"name": "personId"}, {"name": "templateKey"},
                     {"name": "mergeData"}]}}, version=1.1)

    person = f.twenty("Get person", "GET",
                      "/rest/people/{{ $json.personId }}")

    consent = f.twenty("Get consent", "GET",
                       "/rest/consentRecords?filter[personId][eq]="
                       "{{ $('When called').first().json.personId }}"
                       "&filter[channel][eq]=EMAIL&orderBy=effectiveAt[DescNullsLast]&limit=1")

    check = f.code("Consent gate", """
// The whole point of routing every send through this sub-workflow: a workflow
// author cannot forget the consent check, because they never touch SMTP.
const rows = $input.first().json.data?.consentRecords || [];
const latest = rows[0];
const person = $('Get person').first().json.data?.person || {};
const email  = person.emails?.primaryEmail;

if (!email)                       return [{ json: { send:false, reason:'no_email' }}];
if (!latest)                      return [{ json: { send:false, reason:'no_consent_record' }}];
if (latest.status !== 'OPTED_IN') return [{ json: { send:false, reason:`consent_${latest.status}` }}];

return [{ json: {
  send: true, email,
  firstName: person.name?.firstName || '',
  personId: $('When called').first().json.personId,
  templateKey: $('When called').first().json.templateKey,
  mergeData: $('When called').first().json.mergeData || {},
}}];
""".strip())

    gate = f.iff("Allowed to send?", "={{ $json.send }}", "equals", "true")

    render = f.code("Render template", """
// Templates live here, in version control, not in a vendor UI nobody can diff.
const TEMPLATES = {
  lead_ack: {
    subject: 'Thanks for getting in touch, {{firstName}}',
    body: `<p>Hi {{firstName}},</p>
<p>Thanks for reaching out to Aspire. One of our security specialists
will be in touch within one business day.</p>
<p>— Aspire Tech</p>`,
  },
  renewal_60d: {
    subject: 'Your {{serviceLine}} renewal — {{renewalDate}}',
    body: `<p>Hi {{firstName}},</p>
<p>Your {{serviceLine}} subscription renews on {{renewalDate}}.
We will reach out shortly to confirm.</p>
<p>— Aspire Tech</p>`,
  },
  appt_reminder_24h: {
    subject: 'Reminder: {{appointmentType}} tomorrow',
    body: `<p>Hi {{firstName}},</p>
<p>A reminder of your {{appointmentType}} at {{scheduledAt}}.</p>
<p>{{meetingUrl}}</p>`,
  },
};

const d = $input.first().json;
const t = TEMPLATES[d.templateKey];
if (!t) throw new Error(`Unknown templateKey: ${d.templateKey}`);

const vars = { firstName: d.firstName, ...d.mergeData };
const fill = (s) => s.replace(/\\{\\{(\\w+)\\}\\}/g, (_, k) => vars[k] ?? '');

return [{ json: { ...d, subject: fill(t.subject), html: fill(t.body) } }];
""".strip())

    send = f.add("Send by SMTP", "n8n-nodes-base.emailSend", {
        "fromEmail": "={{ $env.ASPIRE_FROM_EMAIL }}",
        "toEmail": "={{ $json.email }}",
        "subject": "={{ $json.subject }}",
        "html": "={{ $json.html }}",
        "options": {},
    }, version=2.1, creds={"smtp": {"name": CRED_SMTP}})

    log = f.twenty("Log message", "POST", "/rest/messageLogs", {
        "channel": "EMAIL",
        "direction": "OUTBOUND",
        "status": "SENT",
        "subject": "={{ $('Render template').first().json.subject }}",
        "vendor": "smtp",
        "sentAt": "={{ $now.toISO() }}",
        "personId": "={{ $('Render template').first().json.personId }}",
    })

    blocked = f.twenty("Log suppressed", "POST", "/rest/messageLogs", {
        "channel": "EMAIL",
        "direction": "OUTBOUND",
        "status": "FAILED",
        "subject": "={{ 'Blocked: ' + $json.reason }}",
        "vendor": "consent-gate",
        "sentAt": "={{ $now.toISO() }}",
    }, row=1)

    f.chain(trig, person, consent, check, gate)
    f.connect(gate, render, out=0)
    f.connect(gate, blocked, out=1)
    f.chain(render, send, log)
    return f


# --------------------------------------------------------------------------
# 3. Public lead form — n8n hosts the form itself, no vendor
# --------------------------------------------------------------------------

def wf_lead_form() -> Flow:
    f = Flow("LEAD Form Intake",
             "n8n Form Trigger hosts a public form. Dedupes, creates the "
             "person and company, records consent, scores, assigns and "
             "acknowledges. Replaces a GHL form with no third-party tool.")

    trig = f.add("Public form", "n8n-nodes-base.formTrigger", {
        "path": "aspire-contact",
        "formTitle": "Talk to Aspire Tech",
        "formDescription": "Tell us what you need and a security specialist "
                           "will reply within one business day.",
        "formFields": {"values": [
            {"fieldLabel": "First name", "requiredField": True},
            {"fieldLabel": "Last name", "requiredField": True},
            {"fieldLabel": "Work email", "fieldType": "email", "requiredField": True},
            {"fieldLabel": "Company", "requiredField": True},
            {"fieldLabel": "Phone", "fieldType": "text"},
            {"fieldLabel": "What do you need help with?",
             "fieldType": "dropdown",
             "fieldOptions": {"values": [
                 {"option": "SOC / managed detection"},
                 {"option": "CMMC or compliance"},
                 {"option": "Security awareness training"},
                 {"option": "Incident response"},
                 {"option": "Something else"}]},
             "requiredField": True},
            {"fieldLabel": "Message", "fieldType": "textarea"},
            {"fieldLabel": "I agree to be contacted by Aspire Tech",
             "fieldType": "checkbox", "requiredField": True},
        ]},
        "responseMode": "lastNode",
        "options": {"appendAttribution": False},
    }, version=2.2)

    norm = f.code("Normalise", """
// Normalise at the edge. Everything downstream can then assume clean values.
const j = $input.first().json;
const email = String(j['Work email'] || '').trim().toLowerCase();
const phoneRaw = String(j['Phone'] || '').replace(/[^0-9+]/g, '');

return [{ json: {
  firstName: String(j['First name'] || '').trim(),
  lastName:  String(j['Last name']  || '').trim(),
  email,
  phone: phoneRaw ? (phoneRaw.startsWith('+') ? phoneRaw : '+1' + phoneRaw) : '',
  companyName: String(j['Company'] || '').trim(),
  interest: j['What do you need help with?'] || '',
  message: String(j['Message'] || '').slice(0, 2000),
  consent: j['I agree to be contacted by Aspire Tech'] === true,
  emailDomain: email.split('@')[1] || '',
}}];
""".strip())

    find = f.twenty("Find person", "GET",
                    "/rest/people?filter[emails.primaryEmail][eq]={{ $json.email }}&limit=1")

    exists = f.code("Exists?", """
// Idempotency: forms get double-submitted and webhooks get retried. Search
// before create, always — cleaning duplicates later costs far more.
const found = ($input.first().json.data?.people || [])[0];
return [{ json: { ...$('Normalise').first().json,
                  existingId: found?.id || null } }];
""".strip())

    branch = f.iff("New person?", "={{ $json.existingId }}", "notExists", "")

    create = f.twenty("Create person", "POST", "/rest/people", {
        "name": {"firstName": "={{ $json.firstName }}",
                 "lastName": "={{ $json.lastName }}"},
        "emails": {"primaryEmail": "={{ $json.email }}"},
        "phones": {"primaryPhoneNumber": "={{ $json.phone }}"},
    })
    update = f.twenty("Update person", "PATCH", "/rest/people/{{ $json.existingId }}",
                      {"phones": {"primaryPhoneNumber": "={{ $json.phone }}"}}, row=1)

    merge = f.add("Merge", "n8n-nodes-base.merge",
                  {"mode": "append", "options": {}}, version=3)

    consent = f.twenty("Record consent", "POST", "/rest/consentRecords", {
        "channel": "EMAIL",
        "status": "={{ $('Normalise').first().json.consent ? 'OPTED_IN' : 'PENDING' }}",
        "source": "FORM_SUBMISSION",
        "effectiveAt": "={{ $now.toISO() }}",
        "proof": "={{ 'n8n form execution ' + $execution.id }}",
    })

    submission = f.twenty("Log submission", "POST", "/rest/formSubmissions", {
        "submittedAt": "={{ $now.toISO() }}",
        "consentGiven": "={{ $('Normalise').first().json.consent }}",
        "payload": "={{ JSON.stringify($('Normalise').first().json) }}",
    })

    score = f.code("Score and route", """
// Transparent scoring — a rule anyone can read and argue with, unlike GHL's
// opaque engagement score.
const d = $('Normalise').first().json;
const FREE = ['gmail.com','yahoo.com','hotmail.com','outlook.com','aol.com','icloud.com'];

let score = 0;
if (d.emailDomain && !FREE.includes(d.emailDomain)) score += 30; else score -= 40;
if (/cmmc|compliance|soc|incident/i.test(d.interest)) score += 25;
if (/cmmc|cui|fci|dfars|nist/i.test(d.message))       score += 20;
if (d.phone)                                          score += 10;

// Deterministic round-robin: hashing the email keeps a person with the same
// rep across re-runs, and needs no counter stored anywhere.
const REPS = (process.env.ASPIRE_REP_IDS || '').split(',').filter(Boolean);
const hash = [...d.email].reduce((a, c) => a + c.charCodeAt(0), 0);

return [{ json: { ...d, score,
  band: score >= 60 ? 'HOT' : score >= 30 ? 'WARM' : 'COLD',
  assigneeId: REPS.length ? REPS[hash % REPS.length] : null }}];
""".strip())

    task = f.twenty("Create task", "POST", "/rest/tasks", {
        "title": "={{ 'Follow up: ' + $json.firstName + ' ' + $json.lastName + "
                 "' (' + $json.band + ', score ' + $json.score + ')' }}",
        "status": "TODO",
        "dueAt": "={{ $now.plus({ days: 1 }).toISO() }}",
        "body": "={{ $json.interest + '\\n\\n' + $json.message }}",
    })

    ack = f.add("Send acknowledgement", "n8n-nodes-base.executeWorkflow", {
        "workflowId": {"__rl": True, "value": "VEND Send Email", "mode": "list"},
        "workflowInputs": {"value": {
            "personId": "={{ $('Exists?').first().json.existingId || "
                        "$('Create person').first().json.data?.createPerson?.id }}",
            "templateKey": "lead_ack",
            "mergeData": "={{ {} }}",
        }},
        "options": {},
    }, version=1.2)

    notify = f.slack("Notify sales", SALES_CHANNEL,
                     "=:inbox_tray: *New lead — {{ $('Score and route').first().json.band }}* "
                     "(score {{ $('Score and route').first().json.score }})\n"
                     "*{{ $('Score and route').first().json.firstName }} "
                     "{{ $('Score and route').first().json.lastName }}* — "
                     "{{ $('Score and route').first().json.companyName }}\n"
                     "Interest: {{ $('Score and route').first().json.interest }}\n"
                     "{{ $('Score and route').first().json.email }}")

    log = f.log_run("Log run", "LEAD Form Intake")

    f.chain(trig, norm, find, exists, branch)
    f.connect(branch, create, out=0)
    f.connect(branch, update, out=1)
    f.connect(create, merge)
    f.connect(update, merge)
    f.chain(merge, consent, submission, score, task, ack, notify, log)
    return f


# --------------------------------------------------------------------------
# 4. Tracked link redirect — replaces GHL trigger links, no vendor
# --------------------------------------------------------------------------

def wf_tracked_link() -> Flow:
    f = Flow("MSG Tracked Link Redirect",
             "Serves /t/:slug, records the click on trackedLink, then 302s to "
             "the destination. Full replacement for GHL trigger links.")

    trig = f.add("Click", "n8n-nodes-base.webhook", {
        "path": "t/:slug",
        "responseMode": "responseNode",
        "options": {},
    }, version=2)

    lookup = f.twenty("Find link", "GET",
                      "/rest/trackedLinks?filter[slug][eq]={{ $json.params.slug }}&limit=1")

    check = f.code("Resolve", """
const link = ($input.first().json.data?.trackedLinks || [])[0];
const q = $('Click').first().json.query || {};
return [{ json: {
  found: !!link,
  id: link?.id || null,
  destination: link?.destinationUrl?.primaryLinkUrl || link?.destinationUrl || '',
  clickCount: (link?.clickCount || 0) + 1,
  personId: q.p || null,
}}];
""".strip())

    gate = f.iff("Known link?", "={{ $json.found }}", "equals", "true")

    count = f.twenty("Count click", "PATCH", "/rest/trackedLinks/{{ $json.id }}",
                     {"clickCount": "={{ $json.clickCount }}"})

    redirect = f.add("Redirect", "n8n-nodes-base.respondToWebhook", {
        "respondWith": "redirect",
        "redirectURL": "={{ $('Resolve').first().json.destination }}",
        "options": {},
    }, version=1.1)

    notfound = f.add("404", "n8n-nodes-base.respondToWebhook", {
        "respondWith": "text",
        "responseBody": "Link not found",
        "options": {"responseCode": 404},
    }, version=1.1, row=1)

    f.chain(trig, lookup, check, gate)
    f.connect(gate, count, out=0)
    f.connect(gate, notfound, out=1)
    f.connect(count, redirect)
    return f


# --------------------------------------------------------------------------
# 5. Renewal escalation — pure business value, needs no vendor at all
# --------------------------------------------------------------------------

def wf_renewal() -> Flow:
    f = Flow("SUB Renewal Escalation",
             "Daily. Walks active subscriptions and escalates at 90/60/30/7 "
             "days before renewal. Impossible in GHL — it has no subscription "
             "object. Build this first: it needs no vendor.")

    trig = f.add("Daily 06:00 UTC", "n8n-nodes-base.scheduleTrigger",
                 {"rule": {"interval": [{"field": "cronExpression",
                                         "expression": "0 6 * * *"}]}}, version=1.2)

    fetch = f.twenty("Get active subscriptions", "GET",
                     "/rest/serviceSubscriptions?filter[status][in]=ACTIVE,RENEWAL_DUE&limit=200")

    window = f.code("Days until renewal", """
const subs = $input.first().json.data?.serviceSubscriptions || [];
const today = new Date(); today.setHours(0,0,0,0);
const out = [];

for (const s of subs) {
  if (!s.renewalDate) continue;
  const d = new Date(s.renewalDate); d.setHours(0,0,0,0);
  const days = Math.round((d - today) / 86400000);
  // Only act on the exact milestones, so a subscription is not chased daily
  // for three months.
  if ([90, 60, 30, 7].includes(days)) {
    out.push({ json: { id: s.id, days, name: s.name,
                       serviceLine: s.serviceLine, mrr: s.mrr,
                       renewalDate: s.renewalDate, companyId: s.companyId }});
  }
}
return out;
""".strip())

    batch = f.add("Batch 20", "n8n-nodes-base.splitInBatches",
                  {"batchSize": 20, "options": {}}, version=3)

    route = f.add("Which milestone?", "n8n-nodes-base.switch", {
        "rules": {"values": [
            {"conditions": {"options": {"caseSensitive": True, "typeValidation": "loose"},
                            "conditions": [{"leftValue": "={{ $json.days }}",
                                            "rightValue": 90,
                                            "operator": {"type": "number", "operation": "equals"}}],
                            "combinator": "and"}, "outputKey": "90 days"},
            {"conditions": {"options": {"caseSensitive": True, "typeValidation": "loose"},
                            "conditions": [{"leftValue": "={{ $json.days }}",
                                            "rightValue": 60,
                                            "operator": {"type": "number", "operation": "equals"}}],
                            "combinator": "and"}, "outputKey": "60 days"},
            {"conditions": {"options": {"caseSensitive": True, "typeValidation": "loose"},
                            "conditions": [{"leftValue": "={{ $json.days }}",
                                            "rightValue": 30,
                                            "operator": {"type": "number", "operation": "equals"}}],
                            "combinator": "and"}, "outputKey": "30 days"},
        ]},
        "options": {"fallbackOutput": 3},
    }, version=3)

    t90 = f.twenty("Task: begin renewal", "POST", "/rest/tasks", {
        "title": "={{ 'Renewal in 90 days — ' + $json.name }}",
        "status": "TODO",
        "dueAt": "={{ $now.plus({ days: 3 }).toISO() }}",
    })
    t60 = f.twenty("Task: confirm intent", "POST", "/rest/tasks", {
        "title": "={{ 'Renewal in 60 days — confirm intent — ' + $json.name }}",
        "status": "TODO",
        "dueAt": "={{ $now.plus({ days: 2 }).toISO() }}",
    }, row=1)
    o30 = f.twenty("Create renewal opportunity", "POST", "/rest/opportunities", {
        "name": "={{ 'Renewal — ' + $json.name }}",
        "stage": "NEW",
        "closeDate": "={{ $json.renewalDate }}",
        "companyId": "={{ $json.companyId }}",
    }, row=2)
    a7 = f.slack("Urgent escalation", ALERT_CHANNEL,
                 "=:warning: *Renewal in 7 days, still no opportunity*\n"
                 "{{ $json.name }} — {{ $json.serviceLine }}\n"
                 "Renews {{ $json.renewalDate }}", row=3)

    log = f.log_run("Log run", "SUB Renewal Escalation")

    f.chain(trig, fetch, window, batch)
    f.connect(batch, route, out=1)      # splitInBatches: output 1 is the loop body
    f.connect(route, t90, out=0)
    f.connect(route, t60, out=1)
    f.connect(route, o30, out=2)
    f.connect(route, a7, out=3)
    for n in (t90, t60, o30, a7):
        f.connect(n, batch)             # back into the batch loop
    f.connect(batch, log, out=0)        # output 0 fires when the loop is done
    return f


# --------------------------------------------------------------------------
# 6. Scheduled sweeps — every "nothing happened" condition in one workflow
# --------------------------------------------------------------------------

def wf_sweeps() -> Flow:
    f = Flow("OPS Scheduled Sweeps",
             "Daily. Detects the absences no system emits an event for: stale "
             "opportunities, appointment no-shows, overdue invoices. Replaces "
             "GHL's Stale Opportunity and No-Show triggers.")

    trig = f.add("Daily 07:00 UTC", "n8n-nodes-base.scheduleTrigger",
                 {"rule": {"interval": [{"field": "cronExpression",
                                         "expression": "0 7 * * *"}]}}, version=1.2)

    stale = f.twenty("Stale opportunities", "GET",
                     "/rest/opportunities?filter[stage][neq]=WON&limit=200")
    noshow = f.twenty("Possible no-shows", "GET",
                      "/rest/appointments?filter[status][eq]=BOOKED&limit=200", row=1)
    overdue = f.twenty("Overdue invoices", "GET",
                       "/rest/invoices?filter[status][in]=SENT,PARTIALLY_PAID&limit=200", row=2)

    assess = f.code("Assess", """
// Three absence checks in one pass. None of these emit an event anywhere —
// they are all "this did not happen by now", which only a query can find.
const now = Date.now();
const days = (d) => (now - new Date(d).getTime()) / 86400000;

const opps    = $('Stale opportunities').first().json.data?.opportunities || [];
const appts   = $('Possible no-shows').first().json.data?.appointments || [];
const invoices= $('Overdue invoices').first().json.data?.invoices || [];

const staleOpps = opps.filter(o => days(o.updatedAt) > 14);
const noShows   = appts.filter(a => new Date(a.scheduledAt).getTime() < now - 3600_000);
const late      = invoices.filter(i => i.dueDate && new Date(i.dueDate).getTime() < now);

return [{ json: {
  staleCount: staleOpps.length, noShowCount: noShows.length, lateCount: late.length,
  noShowIds: noShows.map(a => a.id),
  summary: [
    staleOpps.length ? `${staleOpps.length} opportunity(s) untouched >14 days` : null,
    noShows.length   ? `${noShows.length} appointment(s) past with status still Booked` : null,
    late.length      ? `${late.length} invoice(s) overdue` : null,
  ].filter(Boolean).join('\\n') || 'Nothing outstanding.',
}}];
""".strip())

    gate = f.iff("Anything to report?", "={{ $json.summary }}", "notEquals",
                 "Nothing outstanding.")
    report = f.slack("Daily sweep", SALES_CHANNEL,
                     "=:mag: *Daily sweep*\n{{ $json.summary }}")
    quiet = f.noop("All clear", row=1)
    log = f.log_run("Log run", "OPS Scheduled Sweeps")

    f.chain(trig, stale, noshow, overdue, assess, gate)
    f.connect(gate, report, out=0)
    f.connect(gate, quiet, out=1)
    f.connect(report, log)
    f.connect(quiet, log)
    return f


# --------------------------------------------------------------------------

BUILDERS = [wf_error_handler, wf_send_email, wf_lead_form,
            wf_tracked_link, wf_renewal, wf_sweeps]


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the n8n workflow library")
    ap.add_argument("--list", action="store_true", help="list workflows, write nothing")
    args = ap.parse_args()

    flows = [b() for b in BUILDERS]

    if args.list:
        for fl in flows:
            print(f"  {fl.name:32} {len(fl.nodes):>2} nodes")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    for fl in flows:
        slug = fl.name.lower().replace(" ", "-")
        dest = OUT / f"{slug}.json"
        dest.write_text(json.dumps(fl.to_dict(), indent=2), encoding="utf-8")
        print(f"  {fl.name:32} {len(fl.nodes):>2} nodes → {dest.relative_to(ROOT)}")

    print(f"\n{len(flows)} workflow(s) written to {OUT.relative_to(ROOT)}")
    print("Import order matters: SYS Error Handler and VEND Send Email first —"
          "\nthe others reference them by name.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
