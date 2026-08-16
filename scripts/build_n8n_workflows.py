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
import contextlib
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "n8n" / "workflows"

# Non-greedy so "{{ a }} x {{ b }}" is two placeholders, not one.
_PLACEHOLDER = re.compile(r"\{\{(.*?)\}\}", re.S)


def q(expr: str) -> str:
    """
    Interpolate a value into a Twenty filter, URL-encoded.

    Filter values go into a query string, and Twenty's parser treats `,` `:`
    `[` `]` structurally. A raw value containing any of them — or a space, or a
    bracket — corrupts the query into a 400. That is how the error handler's
    own dedupe query broke on the workflow name "SYS Failure Probe (dev)":
    the failure was logged, the handler then errored on the next node, and the
    alert was never sent. A safety net that fails silently on some inputs is
    worse than none, because it is trusted.

    Values that reach these filters include user-typed emails and a slug taken
    straight off a public URL, so this is not only about awkward names.
    """
    return "{{ encodeURIComponent(" + expr + ") }}"


def _stable_id(*parts: str) -> str:
    """A deterministic 12-digit node id, identical on every run."""
    h = hashlib.sha1("\x00".join(parts).encode("utf-8")).hexdigest()
    return f"{int(h[:15], 16) % (10**12):012d}"

# Credential names the workflows expect to exist in n8n. Created once, by hand,
# in n8n → Credentials. Never inlined into a node.
CRED_TWENTY = "Twenty API"          # Header Auth: Authorization = Bearer <key>
CRED_SMTP = "Aspire SMTP"           # Send Email (SMTP)

TWENTY = "={{ $env.TWENTY_BASE_URL }}"

# Chat alerts go to an *incoming webhook*, not the Slack API node.
#
# The Slack node needs an OAuth app with a bot token, which cannot be created
# headlessly and cannot be exercised at all without a live workspace — which is
# exactly why these workflows sat unproven. An incoming webhook is a plain
# JSON POST, so the same node works against Slack, Mattermost, Discord
# (`/slack` suffix) and Google Chat, and can be pointed at a local sink to
# prove the path before any workspace exists.
ALERT_CHANNEL = "#crm-alerts"
SALES_CHANNEL = "#sales"
FORM_PATH = "aspire-contact"          # public form: /form/aspire-contact
ALERT_HOOK = "={{ $env.ALERT_WEBHOOK_URL }}"
SALES_HOOK = "={{ $env.SALES_WEBHOOK_URL || $env.ALERT_WEBHOOK_URL }}"


# --------------------------------------------------------------------------
# Node helpers
# --------------------------------------------------------------------------

def _js_literal(value) -> str:
    """
    Render a request body as a JavaScript object literal for n8n.

    String interpolation is not enough: `{"clickCount": "{{ $json.x }}"}`
    evaluates to the STRING "1", and Twenty rejects a string where a number
    belongs. Emitting real JS and letting JSON.stringify serialise it preserves
    numbers, booleans and nulls.

    Three string cases, and getting the third wrong is a silent failure:

    - `"={{ expr }}"`  — the whole string is one expression -> the bare `expr`.
    - `"text {{ a }} more {{ b }}"` — literal text *interpolated* with
      expressions. This must become a JS template literal. Left as a JSON
      string it is emitted inside `JSON.stringify(...)`, where `{{ }}` means
      nothing, and the braces are delivered verbatim to the recipient.
    - anything else — a plain literal.
    """
    if isinstance(value, dict):
        inner = ", ".join(f"{json.dumps(k)}: {_js_literal(v)}"
                          for k, v in value.items())
        # The trailing space is load-bearing. n8n ends an expression at the
        # first `}}`, so a nested literal — `{"phones": {"primary": $x}}` —
        # terminates it early and the rest, `) }}`, is parsed as code:
        # "invalid syntax". Twenty's composite fields (name, emails, phones,
        # links) are all nested, so this would hit most write nodes.
        # Closing as `} }` keeps the braces from ever being adjacent.
        return "{" + inner + " }"
    if isinstance(value, list):
        return "[" + ", ".join(_js_literal(v) for v in value) + "]"
    if isinstance(value, str):
        expr = value[1:] if value.startswith("=") else value
        spans = list(_PLACEHOLDER.finditer(expr))
        if not spans:
            return json.dumps(value)
        if len(spans) == 1 and spans[0].span() == (0, len(expr)):
            return spans[0].group(1).strip()        # "={{ $json.x }}" -> "$json.x"
        # Interpolated: rebuild as a template literal, escaping only the
        # literal segments so an expression is never mangled.
        out: list[str] = []
        pos = 0
        for m in spans:
            lit = expr[pos:m.start()]
            out.append(lit.replace("\\", "\\\\").replace("`", "\\`")
                          .replace("${", "\\${"))
            out.append("${" + m.group(1).strip() + "}")
            pos = m.end()
        tail = expr[pos:]
        out.append(tail.replace("\\", "\\\\").replace("`", "\\`")
                       .replace("${", "\\${"))
        return "`" + "".join(out) + "`"
    return json.dumps(value)


# Canvas geometry. These exist because the workflows get shown to people who do
# not read n8n: a correct workflow that renders as one 4,400px strip of
# unlabelled nodes cannot be explained in a meeting.
NODE_DX = 220        # horizontal step between nodes
ROW_DY = 190         # vertical step between branch rows inside a phase
BAND_DY = 620        # vertical step between wrapped bands
WRAP_AT = 1300       # start a new band once a band is this wide
NOTE_PAD = 40        # sticky note margin around its nodes
MIN_NOTE_W = 560     # a note narrower than this wraps its text into a column
                     # so tall it runs down over the nodes it is labelling

# n8n sticky note colours, by index. Chosen so a reader can tell the phases
# apart at a glance and so the two that matter — consent, and failure — do not
# look like ordinary steps.
NOTE_COLOR = {
    "capture": 5,    # blue    — something arrives
    "record": 4,     # green   — the CRM is written
    "consent": 3,    # red     — the compliance gate
    "decide": 6,     # purple  — logic, scoring, routing
    "notify": 2,     # orange  — something leaves the system
    "plumbing": 7,   # grey    — bookkeeping, error handling
}


def _caption_height(title: str, body: str, width: int) -> int:
    """Space a sticky note needs above its nodes for the caption to fit.

    Guessed rather than measured — n8n renders the markdown, not this script —
    so it errs generous. Too much space leaves a gap; too little puts the text
    on top of the nodes, which is what a fixed value did.
    """
    # 9px per character, measured off a real render rather than assumed: at
    # 6.6 the estimate was a third short, every caption wrapped further than
    # predicted, and the text sat on the nodes.
    chars = max(int((width - 32) / 9.0), 16)
    lines = 0
    for para in body.split("\n"):
        lines += max(1, -(-len(para) // chars))     # ceil
    return 60 + lines * 20 + 20                     # title + body + padding


class Flow:
    """Builds one n8n workflow.

    Nodes run left to right. `phase()` groups them under a labelled sticky note
    and wraps the canvas onto a new band when a band gets too wide, so the whole
    workflow fits a screen and reads as a sequence of named steps rather than a
    wall of nodes.
    """

    def __init__(self, name: str, description: str, error_workflow: bool = True):
        self.name = name
        self.description = description
        self.nodes: list[dict] = []
        self.connections: dict = {}
        self.error_workflow = error_workflow
        self._x = 0
        self._band = 0
        self._phases: list[dict] = []
        self._fan: int | None = None
        self._phase: dict | None = None

    # ---- visual grouping ---------------------------------------------------

    def phase(self, title: str, body: str, kind: str = "plumbing") -> None:
        """Open a labelled group. Everything added until the next phase() sits
        inside it, under a sticky note explaining what this part does.

        `body` is read by people who have never seen n8n. Write what the step
        achieves for the business, not which node performs it — the node names
        are already on screen underneath.
        """
        prev = self._phase
        self._close_phase()
        # A phase of one or two nodes is narrower than its own caption. Push the
        # cursor along so the note that is about to close has room for its text,
        # otherwise the words wrap into a tall column and run down over the
        # nodes they label.
        if prev and prev["nodes"]:
            self._x = max(self._x, prev["x0"] + MIN_NOTE_W)
        if self._x >= WRAP_AT:          # wrap between phases, never inside one
            self._band += 1
            self._x = 0
        self._phase = {"title": title, "body": body,
                       "color": NOTE_COLOR.get(kind, 7),
                       "x0": self._x, "band": self._band, "nodes": []}

    @contextlib.contextmanager
    def fan(self):
        """Nodes added in this block are alternatives, not consecutive steps.

        They share one column and stack downwards. Without it, a switch's four
        branches each advance the cursor as well as the row, so they cascade
        diagonally across the canvas and every line back to the join crosses
        the whole workflow. Compact and readable is the difference between a
        diagram someone can follow in a meeting and one they cannot.
        """
        start = self._x
        self._fan = start
        try:
            yield
        finally:
            self._fan = None
            self._x = start + NODE_DX

    def _close_phase(self) -> None:
        if self._phase and self._phase["nodes"]:
            self._phases.append(self._phase)
        self._phase = None

    def _base_y(self) -> int:
        return 300 + self._band * BAND_DY

    def sticky_notes(self) -> list[dict]:
        """Render each phase as a sticky note sized to the nodes it contains."""
        self._close_phase()
        notes = []
        for i, p in enumerate(self._phases):
            xs = [n["position"][0] for n in p["nodes"]]
            ys = [n["position"][1] for n in p["nodes"]]
            # Half the padding on each side, so consecutive notes leave a gap
            # instead of overlapping. A note that laps the next one puts a node
            # visibly inside two groups, which is exactly the confusion these
            # are meant to remove.
            width = max((max(xs) - min(xs)) + NODE_DX, MIN_NOTE_W) - NOTE_PAD // 2
            head = _caption_height(p["title"], p["body"], width)
            x0, y0 = min(xs) - NOTE_PAD // 2, min(ys) - head
            height = (max(ys) - min(ys)) + head + ROW_DY - 30
            notes.append({
                # No "{{" anywhere in this text: the lint reads every string
                # parameter looking for expressions, and prose is not one.
                "parameters": {"content": f"## {p['title']}\n{p['body']}",
                               "height": height, "width": width,
                               "color": p["color"]},
                "id": _stable_id(self.name, f"note:{p['title']}"),
                "name": f"{i + 1}. {p['title']}",
                "type": "n8n-nodes-base.stickyNote",
                "typeVersion": 1,
                "position": [x0, y0],
            })
        return notes

    def add(self, name: str, ntype: str, params: dict, *, version: int = 1,
            creds: dict | None = None, row: int = 0) -> str:
        node = {
            "parameters": params,
            # Stable across processes. Python's hash() is salted per
            # interpreter (PYTHONHASHSEED), so using it here regenerated a
            # different id for every node on every run — the committed JSON
            # churned on each build and real changes were invisible in review.
            "id": _stable_id(self.name, name),
            "name": name,
            "type": ntype,
            "typeVersion": version,
            "position": [self._x if self._fan is None else self._fan,
                         self._base_y() + row * ROW_DY],
        }
        if creds:
            node["credentials"] = creds
        self.nodes.append(node)
        if self._phase is not None:
            self._phase["nodes"].append(node)
        if self._fan is None:      # inside a fan the column is held
            self._x += NODE_DX
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
            # n8n evaluates jsonBody only when the WHOLE parameter is an
            # expression, i.e. the string starts with "=". Putting "=" on the
            # inner values instead sends them as literal text — the request
            # body arrives as {"clickCount": "={{ $json.clickCount }}"} and
            # Twenty rejects it. So: strip the inner "=" prefixes and mark the
            # whole payload as one expression.
            if isinstance(body, dict):
                params["jsonBody"] = ("={{ JSON.stringify("
                                      + _js_literal(body) + ") }}")
            else:
                params["jsonBody"] = body
        n = self.add(name, "n8n-nodes-base.httpRequest", params, version=4,
                     creds={"httpHeaderAuth": {"name": CRED_TWENTY}}, row=row)
        # Twenty's ceiling is 100 requests per MINUTE, so a burst that trips it
        # stays tripped for a good part of that minute. Three tries two seconds
        # apart covers six seconds and gives up well inside the window — which
        # is how a renewal run died on "receiving too many requests" right
        # after the seeder had saturated the limit. 5 x 5s is n8n's maximum and
        # covers 20s, enough for the sweeps and renewal batches measured here.
        self.nodes[-1]["retryOnFail"] = True
        self.nodes[-1]["maxTries"] = 5
        self.nodes[-1]["waitBetweenTries"] = 5000
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

    def notify(self, name: str, channel: str, text: str, *, row: int = 0,
               hook: str = ALERT_HOOK) -> str:
        """
        Post a chat message to an incoming webhook.

        `text` is a Slack-formatted string; `channel` is advisory (Slack honours
        it only for legacy hooks) but is sent so a sink or a router can tell
        alerts from sales notifications.

        Alerting must never take the workflow down with it: if the chat vendor
        is unreachable, the run it was reporting on still has to finish, so
        this node continues on error rather than raising.
        """
        n = self.add(name, "n8n-nodes-base.httpRequest", {
            "method": "POST",
            "url": hook,
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": ("={{ JSON.stringify("
                         + _js_literal({"channel": channel, "text": text})
                         + ") }}"),
            "options": {},
        }, version=4, row=row)
        self.nodes[-1]["retryOnFail"] = True
        self.nodes[-1]["maxTries"] = 3
        self.nodes[-1]["waitBetweenTries"] = 2000
        self.nodes[-1]["onError"] = "continueRegularOutput"
        return n

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
            # Sticky notes last so they paint behind the nodes, not over them.
            # They are never connected and never execute — they exist only to
            # make the canvas readable to someone who does not use n8n.
            "nodes": self.nodes + self.sticky_notes(),
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

    f.phase("When any automation fails",
            "Every other workflow points its failures here, so there is one\n"
            "place that knows something went wrong. Without it a workflow\n"
            "can fail quietly for weeks and the first anyone hears is a\n"
            "customer asking why nobody replied.", "capture")

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

    f.phase("Write it down in the CRM",
            "The failure becomes a record in Twenty — which workflow, when,\n"
            "and the error. It lives with the business data rather than in a\n"
            "log file nobody opens, so failures can be reported on like\n"
            "anything else.", "record")

    log = f.twenty("Log failure", "POST", "/rest/automationRuns", {
        "workflowName": "={{ $json.workflowName }}",
        "status": "FAILED",
        "errorMessage": "={{ $json.message }}",
        "n8nExecutionId": "={{ $json.executionId }}",
        "startedAt": "={{ $now.toISO() }}",
    })

    f.phase("Alert, but do not spam",
            "If the same workflow already failed several times in the last\n"
            "hour, log it but stay quiet. One broken integration can\n"
            "otherwise produce hundreds of messages overnight, and the team\n"
            "learns to ignore the channel — worse than no alerting at all.",
            "notify")

    recent = f.twenty("Recent failures?", "GET",
                      "/rest/automationRuns?filter=workflowName[eq]:"
                      "{{ encodeURIComponent($json.workflowName) }},status[eq]:FAILED"
                      "&order_by=startedAt[DescNullsLast]&limit=5")

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
    alert = f.notify("Alert", ALERT_CHANNEL,
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

    f.phase("Every email in the system starts here",
            "No other workflow is allowed to send email directly. They all\n"
            "call this one. That is deliberate: it means the consent check\n"
            "below cannot be forgotten by whoever builds the next workflow,\n"
            "because they never touch the mail server themselves.", "capture")

    trig = f.add("When called", "n8n-nodes-base.executeWorkflowTrigger",
                 {"workflowInputs": {"values": [
                     {"name": "personId"}, {"name": "templateKey"},
                     {"name": "mergeData"}]}}, version=1.1)

    person = f.twenty("Get person", "GET",
                      "/rest/people/{{ $json.personId }}")

    consent = f.twenty("Get consent", "GET",
                       "/rest/consentRecords?filter=personId[eq]:"
                       "{{ encodeURIComponent($('When called').first().json.personId) }}"
                       ",channel[eq]:EMAIL"
                       "&order_by=effectiveAt[DescNullsLast]&limit=1")

    f.phase("The consent gate",
            "Read this person's most recent consent record for this channel.\n"
            "Opted in: the mail goes. Anything else — opted out, bounced,\n"
            "never asked — it does not, and we write down why. There is no\n"
            "setting to skip this and no way around it.", "consent")

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

    f.phase("Send, and keep the receipt",
            "Fill in the template and hand it to the mail server. Whether it\n"
            "was sent or refused, a message log record is written either way,\n"
            "so a refusal is evidence rather than silence. That log is what\n"
            "we would show if someone asked whether we emailed a contact.",
            "notify")

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

    # The form path is set in BOTH places on purpose. The node resolves its URL
    # as `path || options.path || $webhookId`, but the top-level `path` property
    # only exists at typeVersion <= 2.1; from 2.2 it moved into `options`. Set
    # just one and the other version silently falls through to $webhookId — the
    # form still works, at an unguessable UUID URL that no campaign can link to.
    # Both are the same value, so whichever the running version reads is right.
    f.phase("A lead arrives",
            "Someone fills in the form on our website. n8n hosts that form\n"
            "itself, so there is no third-party form tool to pay for or to\n"
            "trust with our data. The next step cleans up what they typed —\n"
            "lower-cases the email, strips spaces from the phone — so the\n"
            "same person is recognised however they type it.", "capture")

    trig = f.add("Public form", "n8n-nodes-base.formTrigger", {
        "path": FORM_PATH,
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
        "options": {"path": FORM_PATH, "appendAttribution": False},
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

    f.phase("Is this person already known?",
            "Look them up by email before creating anything. Without this,\n"
            "a contact who fills in the form twice becomes two records and\n"
            "the sales history is split across both. Known: update them.\n"
            "New: create them. Either way, one record per human.", "record")

    find = f.twenty("Find person", "GET",
                    "/rest/people?filter=emails.primaryEmail[eq]:"
                    "{{ encodeURIComponent($json.email) }}&limit=1")

    exists = f.code("Exists?", """
// Idempotency: forms get double-submitted and webhooks get retried. Search
// before create, always — cleaning duplicates later costs far more.
const found = ($input.first().json.data?.people || [])[0];
return [{ json: { ...$('Normalise').first().json,
                  existingId: found?.id || null } }];
""".strip())

    branch = f.iff("New person?", "={{ $json.existingId }}", "notExists", "")

    # One definition of "the person this run is about", used by every node
    # downstream. Written out per-node it drifted: the consent record and the
    # form submission were created with no person at all, so the consent gate
    # could never find them and every send would have sailed through the check
    # it exists to fail.
    person_id = ("={{ $('Exists?').first().json.existingId || "
                 "$('Create person').first().json.data.createPerson.id }}")

    # Two alternatives, one column: new person or existing person, never both.
    with f.fan():
        create = f.twenty("Create person", "POST", "/rest/people", {
            "name": {"firstName": "={{ $json.firstName }}",
                     "lastName": "={{ $json.lastName }}"},
            "emails": {"primaryEmail": "={{ $json.email }}"},
            "phones": {"primaryPhoneNumber": "={{ $json.phone }}"},
        })
        update = f.twenty("Update person", "PATCH",
                          "/rest/people/{{ $json.existingId }}",
                          {"phones": {"primaryPhoneNumber": "={{ $json.phone }}"}},
                          row=1)

    merge = f.add("Merge", "n8n-nodes-base.merge",
                  {"mode": "append", "options": {}}, version=3)

    # Match the company on email domain, not on typed company name: people
    # write "Meridian", "Meridian Defense" and "meridian defense systems" for
    # the same account, and each spelling would create another record.
    f.phase("Attach them to the right company",
            "Match on the email domain, not the company name they typed.\n"
            "People write Meridian, Meridian Defense and meridian defense\n"
            "systems for the same account, and each spelling would create\n"
            "another company. The domain is the same every time.", "record")

    find_co = f.twenty("Find company", "GET",
                       "/rest/companies"
                       "?filter=domainName.primaryLinkUrl[ilike]:"
                       "%{{ encodeURIComponent($('Normalise').first().json.emailDomain) }}%&limit=1")

    co_branch = f.code("Company exists?", """
const found = ($input.first().json.data?.companies || [])[0];
return [{ json: { ...$('Normalise').first().json, companyId: found?.id || null } }];
""".strip())

    new_co = f.iff("New company?", "={{ $json.companyId }}", "notExists", "")

    create_co = f.twenty("Create company", "POST", "/rest/companies", {
        "name": "={{ $json.companyName }}",
        "domainName": {"primaryLinkUrl": "={{ 'https://' + $json.emailDomain }}"},
    })
    co_merge = f.add("Company ready", "n8n-nodes-base.merge",
                     {"mode": "append", "options": {}}, version=3, row=1)

    company_id = ("={{ $('Company exists?').first().json.companyId || "
                  "$('Create company').first().json.data.createCompany.id }}")

    link_co = f.twenty("Link person to company", "PATCH",
                       "/rest/people/" + person_id.replace("={{ ", "{{ "),
                       {"companyId": company_id})

    f.phase("Record consent — the compliance step",
            "We write down that this person agreed to be contacted: when,\n"
            "through which channel, and what they were shown. Nothing in\n"
            "this system may email anyone without a record like this. It is\n"
            "the evidence we would produce if a regulator ever asked, and\n"
            "we sell compliance, so we hold ourselves to it.", "consent")

    consent = f.twenty("Record consent", "POST", "/rest/consentRecords", {
        "channel": "EMAIL",
        "status": "={{ $('Normalise').first().json.consent ? 'OPTED_IN' : 'PENDING' }}",
        "source": "FORM_SUBMISSION",
        "effectiveAt": "={{ $now.toISO() }}",
        "proof": "={{ 'n8n form execution ' + $execution.id }}",
        "personId": person_id,
    })

    submission = f.twenty("Log submission", "POST", "/rest/formSubmissions", {
        "submittedAt": "={{ $now.toISO() }}",
        "consentGiven": "={{ $('Normalise').first().json.consent }}",
        "payload": "={{ JSON.stringify($('Normalise').first().json) }}",
        "personId": person_id,
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
// $env, not process.env: with N8N_RUNNERS_ENABLED the Code node executes
// in a sandboxed task runner where `process` does not exist at all.
const REPS = String($env.ASPIRE_REP_IDS || '').split(',').filter(Boolean);
const hash = [...d.email].reduce((a, c) => a + c.charCodeAt(0), 0);

return [{ json: { ...d, score,
  band: score >= 60 ? 'HOT' : score >= 30 ? 'WARM' : 'COLD',
  assigneeId: REPS.length ? REPS[hash % REPS.length] : null }}];
""".strip())

    f.phase("Score it and give it an owner",
            "Rate the lead against rules anyone can read — company size,\n"
            "what they asked for, whether it is a work email — then hand it\n"
            "to the next sales rep in turn and raise a task with a due date.\n"
            "Nothing sits in an inbox waiting to be noticed.", "decide")

    task = f.twenty("Create task", "POST", "/rest/tasks", {
        "title": "={{ 'Follow up: ' + $json.firstName + ' ' + $json.lastName + "
                 "' (' + $json.band + ', score ' + $json.score + ')' }}",
        "status": "TODO",
        "dueAt": "={{ $now.plus({ days: 1 }).toISO() }}",
        # Twenty's task body is `bodyV2`, a RICH_TEXT composite: it takes
        # {"markdown": ...} and rejects a bare string with a 400. A plain
        # `body` key is silently not a field at all.
        "bodyV2": {"markdown": "={{ $json.interest + '\\n\\n' + $json.message }}"},
    })

    f.phase("Reply, tell sales, keep the receipt",
            "Send the acknowledgement (through the one workflow allowed to\n"
            "touch email, which checks consent again first), ping the sales\n"
            "channel, and log that this run happened. If any step above had\n"
            "failed, that log is how we would know.", "notify")

    ack = f.add("Send acknowledgement", "n8n-nodes-base.executeWorkflow", {
        "workflowId": {"__rl": True, "value": "VEND Send Email", "mode": "list"},
        "workflowInputs": {"value": {
            "personId": person_id,
            "templateKey": "lead_ack",
            "mergeData": "={{ {} }}",
        }},
        "options": {},
    }, version=1.2)

    notify = f.notify("Notify sales", SALES_CHANNEL, hook=SALES_HOOK,
                      text="=:inbox_tray: *New lead — {{ $('Score and route').first().json.band }}* "
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
    # Person settled, then company, then everything that references both.
    f.chain(merge, find_co, co_branch, new_co)
    f.connect(new_co, create_co, out=0)
    f.connect(create_co, co_merge)
    f.connect(new_co, co_merge, out=1)
    f.chain(co_merge, link_co, consent, submission, score, task, ack, notify, log)
    return f


# --------------------------------------------------------------------------
# 4. Tracked link redirect — replaces GHL trigger links, no vendor
# --------------------------------------------------------------------------

def wf_tracked_link() -> Flow:
    f = Flow("MSG Tracked Link Redirect",
             "Serves /t/:slug, records the click on trackedLink, then 302s to "
             "the destination. Full replacement for GHL trigger links.")

    # A static path with a query parameter, not a `:slug` path parameter.
    # n8n registers `t/:slug` in the database but will not match `t/abc`
    # against it — every request 404s with "webhook is not registered".
    # Tracked links conventionally look like /t?s=abc123 anyway.
    f.phase("Someone clicks a link we sent",
            "Our emails use a link that points here rather than straight at\n"
            "the destination. That is how click tracking works in every\n"
            "marketing tool; this is the same idea without the vendor.",
            "capture")

    trig = f.add("Click", "n8n-nodes-base.webhook", {
        "path": "t",
        "httpMethod": "GET",
        "responseMode": "responseNode",
        "options": {},
    }, version=2)

    lookup = f.twenty("Find link", "GET",
                      "/rest/trackedLinks?filter=slug[eq]:"
                      "{{ encodeURIComponent($json.query.s) }}&limit=1")

    check = f.code("Resolve", """
const link = ($input.first().json.data?.trackedLinks || [])[0];
const q = $('Click').first().json.query || {};   // ?s=<slug>&p=<personId>
return [{ json: {
  found: !!link,
  id: link?.id || null,
  destination: link?.destinationUrl?.primaryLinkUrl || link?.destinationUrl || '',
  clickCount: (link?.clickCount || 0) + 1,
  personId: q.p || null,
}}];
""".strip())

    gate = f.iff("Known link?", "={{ $json.found }}", "equals", "true")

    f.phase("Count it, then get out of the way",
            "Add one to the click count and send the visitor on to where\n"
            "they were going. An unrecognised link gets a 404 rather than a\n"
            "redirect, so a guessed or edited URL cannot bounce someone\n"
            "through our domain to a site of their choosing.", "record")

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

    f.phase("Every morning, check what is coming up",
            "Nothing happens when a renewal approaches — no system emits an\n"
            "event for a date getting nearer. So we go and look, every day,\n"
            "and work out how many days are left on each subscription.\n"
            "GoHighLevel cannot do this at all.", "capture")

    trig = f.add("Daily 06:00 UTC", "n8n-nodes-base.scheduleTrigger",
                 {"rule": {"interval": [{"field": "cronExpression",
                                         "expression": "0 6 * * *"}]}}, version=1.2)

    fetch = f.twenty("Get active subscriptions", "GET",
                     # Bound the window in the query, not in code: the old
                     # version pulled every subscription and filtered in JS,
                     # which is a full table scan every morning forever.
                     "/rest/serviceSubscriptions"
                     "?filter=status[in]:[ACTIVE,RENEWAL_DUE],"
                     "renewalDate[lte]:{{ encodeURIComponent($now.plus({ days: 90 }).toISO()) }}"
                     "&order_by=renewalDate[AscNullsLast]&limit=200")

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

    f.phase("Escalate at 90, 60, 30 and 7 days",
            "Each milestone does something different and more urgent: start\n"
            "the conversation, confirm they intend to renew, open a renewal\n"
            "opportunity in the pipeline, then escalate. A renewal is never\n"
            "lost because nobody remembered it.", "decide")

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
        # Three rules give outputs 0-2. The 7-day case is everything that
        # reaches here, and it needs an output of its own: `fallbackOutput: 3`
        # names an output that does not exist and the node throws
        # "The ouput 3 is not allowed" at runtime. "extra" creates it.
        "options": {"fallbackOutput": "extra"},
    }, version=3)

    # One column, four rows: these are alternatives, not consecutive steps.
    # Laid out in sequence they march diagonally across the canvas and every
    # line back to the batch loop crosses the whole workflow.
    with f.fan():
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
        a7 = f.notify("Urgent escalation", ALERT_CHANNEL,
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

    f.phase("Look for what did NOT happen",
            "The hardest thing to automate is absence. No event fires when a\n"
            "deal goes quiet, a customer misses a meeting, or an invoice is\n"
            "not paid — so once a day we ask the CRM those three questions\n"
            "directly.", "capture")

    trig = f.add("Daily 07:00 UTC", "n8n-nodes-base.scheduleTrigger",
                 {"rule": {"interval": [{"field": "cronExpression",
                                         "expression": "0 7 * * *"}]}}, version=1.2)

    stale = f.twenty("Stale opportunities", "GET",
                     # Twenty's default pipeline ends at CUSTOMER; there is
                     # no WON stage, and the wrong enum is a 400 at runtime.
                     "/rest/opportunities?filter=stage[neq]:CUSTOMER&limit=200")
    noshow = f.twenty("Possible no-shows", "GET",
                      "/rest/appointments?filter=status[eq]:BOOKED&limit=200", row=1)
    overdue = f.twenty("Overdue invoices", "GET",
                       # `in` takes a bracketed array; a bare comma list 400s.
                       "/rest/invoices?filter=status[in]:[SENT,PARTIALLY_PAID]"
                       "&limit=200", row=2)

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

    f.phase("Only speak up if there is something to say",
            "A digest goes to the team when there is something in it. A\n"
            "quiet day sends nothing, because a daily message that is\n"
            "usually empty is one people stop opening.", "notify")

    gate = f.iff("Anything to report?", "={{ $json.summary }}", "notEquals",
                 "Nothing outstanding.")
    report = f.notify("Daily sweep", SALES_CHANNEL, hook=SALES_HOOK,
                      text="=:mag: *Daily sweep*\n{{ $json.summary }}")
    quiet = f.noop("All clear", row=1)
    log = f.log_run("Log run", "OPS Scheduled Sweeps")

    f.chain(trig, stale, noshow, overdue, assess, gate)
    f.connect(gate, report, out=0)
    f.connect(gate, quiet, out=1)
    f.connect(report, log)
    f.connect(quiet, log)
    return f


# --------------------------------------------------------------------------

def wf_alert_sink() -> Flow:
    """
    Dev-only receiver for the chat webhook.

    Alerts are the part of an automation nobody tests, because testing them
    normally means owning a Slack workspace. This stands in for one: it accepts
    the same JSON payload Slack's incoming webhooks accept and files it in
    Twenty as an automationRun, so `ALERT_WEBHOOK_URL` can be pointed here and
    the whole alert path verified end to end.

    Not deployed to production — `n8n_deploy.py` skips DEV_ONLY unless --dev.
    """
    f = Flow("SYS Alert Sink (dev)",
             "Dev stand-in for Slack. Records anything posted to the chat "
             "webhook as an automationRun so alerting can be proven locally.",
             error_workflow=False)

    trig = f.add("Incoming alert", "n8n-nodes-base.webhook", {
        "path": "alert-sink",
        "httpMethod": "POST",
        "responseMode": "lastNode",
        "options": {},
    }, version=2)

    shape = f.code("Shape", """
const b = $input.first().json.body || {};
return [{ json: {
  channel: b.channel || '(default)',
  // Slack accepts `text` or `blocks`; only `text` is used here.
  text: String(b.text || '').slice(0, 2000),
}}];
""".strip())

    record = f.twenty("Record alert", "POST", "/rest/automationRuns", {
        "workflowName": "={{ 'ALERT ' + $json.channel }}",
        "status": "SUCCESS",
        "startedAt": "={{ $now.toISO() }}",
        "n8nExecutionId": "={{ $execution.id }}",
        # errorMessage is automationRun's only free-text field; the alert body
        # goes there rather than adding a column used by one dev-only workflow.
        "errorMessage": "={{ $json.text }}",
    })

    f.chain(trig, shape, record)
    return f


def wf_failure_probe() -> Flow:
    """
    Dev-only workflow whose entire job is to fail.

    The error handler is the safety net for everything else, and it was the one
    workflow never exercised: on a freshly built stack nothing had failed, so
    it had no executions and the audit correctly reported it unproven. Waiting
    for a real failure to find out whether failure handling works is the wrong
    order.

    Running this fires the same path a genuine failure does — error trigger,
    automationRun FAILED, alert — so `prove_workflows.py` can assert on it.
    """
    f = Flow("SYS Failure Probe (dev)",
             "Throws on purpose so the error handler can be proven. Deployed "
             "and activated only with --dev.")

    # A webhook, not a manual trigger. n8n only invokes `settings.errorWorkflow`
    # for PRODUCTION executions — a manual run fails in the editor and the
    # error workflow is never called, so a probe run by hand proves nothing
    # about the path a real failure takes.
    trig = f.add("Fail on request", "n8n-nodes-base.webhook", {
        "path": "fail-probe",
        "httpMethod": "GET",
        "options": {},
    }, version=2)
    boom = f.code("Fail deliberately", """
// The message is asserted on by prove_workflows.py.
throw new Error('deliberate failure probe');
""".strip())
    f.chain(trig, boom)
    return f


# Workflows that exist only to make local verification possible. They are
# generated and committed so the proof is reproducible, but never deployed to
# production, where the real chat webhook replaces them.
DEV_ONLY = {"SYS Alert Sink (dev)", "SYS Failure Probe (dev)"}

BUILDERS = [wf_error_handler, wf_send_email, wf_lead_form,
            wf_tracked_link, wf_renewal, wf_sweeps, wf_alert_sink,
            wf_failure_probe]


def lint(flow: Flow) -> list[str]:
    """
    Static checks for the failure modes that only show up at runtime.

    Every one of these was a live defect first. A generated library is only
    trustworthy if the generator refuses to emit the shapes that broke before.
    """
    problems: list[str] = []
    for node in flow.nodes:
        where = f"{flow.name} / {node['name']}"
        for key, val in (node.get("parameters") or {}).items():
            if not isinstance(val, str) or "{{" not in val:
                continue
            # A string may hold several placeholders — that is ordinary
            # interpolation. What breaks is a `}}` *inside* one of them: n8n
            # closes the placeholder there, so the expression it actually
            # evaluates is truncated. Non-greedy matching sees exactly what
            # n8n sees, and truncation shows up as leftover open braces.
            for m in _PLACEHOLDER.finditer(val):
                seen = m.group(1)
                if seen.count("{") != seen.count("}"):
                    problems.append(
                        f"{where}: `{key}` truncates at a nested `}}}}` — n8n "
                        f"would evaluate `{seen.strip()[:60]}...`")
            if val.count("{{") != val.count("}}"):
                problems.append(f"{where}: `{key}` has unbalanced braces")
        # A raw value interpolated into a filter corrupts the query string.
        for key, val in (node.get("parameters") or {}).items():
            if key != "url" or not isinstance(val, str) or "filter=" not in val:
                continue
            tail = val.split("filter=", 1)[1].split("&", 1)[0]
            for m in _PLACEHOLDER.finditer(tail):
                if "encodeURIComponent" not in m.group(1):
                    problems.append(
                        f"{where}: filter value `{m.group(1).strip()[:40]}` is "
                        "interpolated raw — wrap it in encodeURIComponent()")

        # A body sent as a plain string never gets evaluated.
        if node.get("type") == "n8n-nodes-base.httpRequest":
            body = (node.get("parameters") or {}).get("jsonBody")
            if isinstance(body, str) and "{{" in body and not body.startswith("="):
                problems.append(f"{where}: jsonBody has placeholders but is not "
                                "an expression (missing leading `=`)")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the n8n workflow library")
    ap.add_argument("--list", action="store_true", help="list workflows, write nothing")
    args = ap.parse_args()

    flows = [b() for b in BUILDERS]

    if args.list:
        for fl in flows:
            print(f"  {fl.name:32} {len(fl.nodes):>2} nodes")
        return 0

    problems = [p for fl in flows for p in lint(fl)]
    if problems:
        print("Refusing to write — generated workflows would fail at runtime:\n",
              file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    for fl in flows:
        # Punctuation out of filenames: "SYS Alert Sink (dev)" would
        # otherwise become sys-alert-sink-(dev).json, which needs quoting
        # in every shell command that touches it.
        slug = re.sub(r"[^a-z0-9]+", "-", fl.name.lower()).strip("-")
        dest = OUT / f"{slug}.json"
        dest.write_text(json.dumps(fl.to_dict(), indent=2), encoding="utf-8")
        print(f"  {fl.name:32} {len(fl.nodes):>2} nodes → {dest.relative_to(ROOT)}")

    print(f"\n{len(flows)} workflow(s) written to {OUT.relative_to(ROOT)}")
    print("Import order matters: SYS Error Handler and VEND Send Email first —"
          "\nthe others reference them by name.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
