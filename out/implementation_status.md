# Implementation Status

Source: probed live stack. 111 catalogued features.

| Status | Count | Meaning |
|---|---|---|
| LIVE | 16 | Verified working on the running stack |
| NATIVE | 11 | Twenty does it; needs configuration only |
| MODELLED | 22 | Object exists to hold the data; no automation |
| DESIGNED | 29 | Specified in the guide; nothing built |
| DEFERRED | 33 | Deliberately out of scope, reason recorded |


### Data Model

| ID | Feature | Status | Evidence |
|---|---|---|---|
| `DM-01` | Contact custom fields | **LIVE** | provisioned object model |
| `DM-02` | Opportunity custom fields | **LIVE** | provisioned object model |
| `DM-03` | Custom objects | **LIVE** | provisioned object model |
| `DM-04` | Custom values (merge variables) | **MODELLED** | `mergeVariable` object exists, no automation |
| `DM-05` | Tags | **MODELLED** | `crmTag` object exists, no automation |
| `DM-06` | Companies / businesses | **LIVE** | provisioned object model |
| `DM-07` | DND / consent flags | **LIVE** | workflow `vend-send-email` |

### Contacts & Segmentation

| ID | Feature | Status | Evidence |
|---|---|---|---|
| `CT-01` | Smart lists / saved filters | **NATIVE** | Twenty saved views |
| `CT-02` | Bulk actions on contacts | **NATIVE** | Twenty bulk edit |
| `CT-03` | Import / export CSV | **NATIVE** | Twenty CSV import |
| `CT-04` | Duplicate detection / merge | **LIVE** | workflow `lead-form-intake` |
| `CT-05` | Manual actions queue (call/SMS tasks) | **DESIGNED** | specified in the guide only |
| `CT-06` | Notes and tasks on records | **NATIVE** | Twenty notes and tasks |

### Opportunities & Pipelines

| ID | Feature | Status | Evidence |
|---|---|---|---|
| `OP-01` | Pipelines and stages | **NATIVE** | Twenty opportunity stages |
| `OP-02` | Opportunity value / forecasting | **LIVE** | workflow `sub-renewal-escalation` |
| `OP-03` | Pipeline automation triggers | **NATIVE** | Twenty record-updated trigger |

### Conversations & Inbox

| ID | Feature | Status | Evidence |
|---|---|---|---|
| `CV-01` | Unified inbox | **NATIVE** | Twenty mailbox sync — email only, needs connecting |
| `CV-02` | SMS conversations | **DEFERRED** | SMS — carrier required, barely used |
| `CV-03` | WhatsApp | **DEFERRED** | WhatsApp — Meta approved provider required |
| `CV-04` | Facebook / Instagram DM | **DEFERRED** | social DM — barely used |
| `CV-05` | Google Business Messages | **DEFERRED** | GMB — barely used |
| `CV-06` | Live chat widget | **DEFERRED** | live chat — no vendor chosen |
| `CV-07` | Snippets / canned replies | **MODELLED** | `messageLog` object exists, no automation |

### Email & Templates

| ID | Feature | Status | Evidence |
|---|---|---|---|
| `EM-01` | Email templates | **LIVE** | workflow `vend-send-email` |
| `EM-02` | Drag-and-drop email builder | **MODELLED** | `emailTemplate` object exists, no automation |
| `EM-03` | Bulk / campaign email send | **MODELLED** | `campaign` object exists, no automation |
| `EM-04` | Drip sequences | **MODELLED** | `campaign` object exists, no automation |
| `EM-05` | Trigger links / click tracking | **LIVE** | webhook 307 + clickCount 0->1 verified |
| `EM-06` | Dedicated sending domain / DKIM / SPF | **DESIGNED** | specified in the guide only |
| `EM-07` | Unsubscribe / suppression list | **LIVE** | workflow `vend-send-email` |
| `EM-08` | Email verification | **DESIGNED** | specified in the guide only |
| `EM-09` | Scheduled email sends | **DESIGNED** | specified in the guide only |

### SMS & Telephony

| ID | Feature | Status | Evidence |
|---|---|---|---|
| `SM-01` | SMS sending | **DEFERRED** | SMS — carrier required |
| `SM-02` | Phone numbers (LC Phone) | **DEFERRED** | numbers — carrier required |
| `SM-03` | A2P 10DLC registration | **DEFERRED** | A2P — carrier required |
| `SM-04` | Inbound call routing / IVR | **DEFERRED** | IVR — carrier required |
| `SM-05` | Call recording | **DEFERRED** | call recording — carrier required |
| `SM-06` | Voicemail drop | **DEFERRED** | voicemail drop — carrier required |
| `SM-07` | Missed-call text-back | **DEFERRED** | missed-call text — carrier required |

### Voice & AI Features

| ID | Feature | Status | Evidence |
|---|---|---|---|
| `AI-01` | Voice AI agents | **DEFERRED** | Voice AI — specialist vendor |
| `AI-02` | Conversation AI bot | **DESIGNED** | specified in the guide only |
| `AI-03` | Content AI | **DESIGNED** | specified in the guide only |
| `AI-04` | Reviews AI | **DESIGNED** | specified in the guide only |
| `AI-05` | Workflow AI assistant | **DEFERRED** | authoring aid, not a running feature |

### Calendars & Scheduling

| ID | Feature | Status | Evidence |
|---|---|---|---|
| `CL-01` | Calendars and availability | **MODELLED** | `appointment` object exists, no automation |
| `CL-02` | Public booking pages | **MODELLED** | `appointment` object exists, no automation |
| `CL-03` | Round-robin / team distribution | **MODELLED** | `appointment` object exists, no automation |
| `CL-04` | Appointment reminders | **MODELLED** | `appointment` object exists, no automation |
| `CL-05` | Booking volume (evidence) | **LIVE** | workflow `ops-scheduled-sweeps` |

### Forms & Surveys

| ID | Feature | Status | Evidence |
|---|---|---|---|
| `FS-01` | Forms | **LIVE** | workflow `lead-form-intake` |
| `FS-02` | Form conditional logic | **LIVE** | workflow `lead-form-intake` |
| `FS-03` | Surveys / quizzes | **MODELLED** | `formDefinition` object exists, no automation |
| `FS-04` | Form submission volume (evidence) | **LIVE** | workflow `lead-form-intake` |
| `FS-05` | Survey submission volume (evidence) | **LIVE** | workflow `ops-scheduled-sweeps` |

### Funnels & Websites

| ID | Feature | Status | Evidence |
|---|---|---|---|
| `FW-01` | Funnels | **DEFERRED** | funnels — CMS, verify what GHL actually hosts |
| `FW-02` | Websites / pages | **DEFERRED** | websites — almost certainly not in GHL |
| `FW-03` | Order forms / upsells | **DEFERRED** | order forms — commerce surface |
| `FW-04` | Custom domains | **DEFERRED** | domains — follows hosting |
| `FW-05` | Tracking codes / pixels | **DEFERRED** | pixels — follows hosting |

### Blogs & Content

| ID | Feature | Status | Evidence |
|---|---|---|---|
| `BL-01` | Blog sites | **DEFERRED** | blogs — CMS |
| `BL-02` | Blog posts | **DEFERRED** | blog posts — CMS |
| `BL-03` | Blog authors / categories | **DEFERRED** | blog taxonomy — CMS |

### Workflows & Automation

| ID | Feature | Status | Evidence |
|---|---|---|---|
| `WF-01` | Workflow inventory | **DESIGNED** | specified in the guide only |
| `WF-02` | Workflow internals (steps and actions) | **DESIGNED** | specified in the guide only |
| `WF-03` | Workflow triggers | **DESIGNED** | specified in the guide only |
| `WF-04` | If/else and conditional branching | **DESIGNED** | specified in the guide only |
| `WF-05` | Wait / delay steps | **DESIGNED** | specified in the guide only |
| `WF-06` | Goal / exit conditions | **DESIGNED** | specified in the guide only |
| `WF-07` | Math / formula operations | **DESIGNED** | specified in the guide only |
| `WF-08` | Outbound webhooks from workflows | **DESIGNED** | specified in the guide only |
| `WF-09` | Custom code steps | **DESIGNED** | specified in the guide only |
| `WF-10` | Legacy campaigns (drip) | **MODELLED** | `campaign` object exists, no automation |
| `WF-11` | Workflow execution history / enrolment counts | **LIVE** | workflow `sys-error-handler` |

### Payments & Commerce

| ID | Feature | Status | Evidence |
|---|---|---|---|
| `PY-01` | Products and prices | **MODELLED** | `product` object exists, no automation |
| `PY-02` | Invoices | **MODELLED** | `invoice` object exists, no automation |
| `PY-03` | Estimates / quotes (CPQ) | **MODELLED** | `quote` object exists, no automation |
| `PY-04` | Subscriptions / recurring billing | **MODELLED** | `serviceSubscription` object exists, no automation |
| `PY-05` | Payment processor connections | **MODELLED** | `payment` object exists, no automation |
| `PY-06` | Coupons | **MODELLED** | `payment` object exists, no automation |
| `PY-07` | Documents and contracts (e-sign) | **DESIGNED** | specified in the guide only |

### Memberships & Courses

| ID | Feature | Status | Evidence |
|---|---|---|---|
| `MC-01` | Courses / memberships | **DEFERRED** | courses — aspireelearning.com |
| `MC-02` | Client portal | **DEFERRED** | portal — aspireelearning.com |
| `MC-03` | Communities | **DEFERRED** | communities — verify usage |
| `MC-04` | Certificates | **DEFERRED** | certificates — aspireelearning.com |

### Reputation & Reviews

| ID | Feature | Status | Evidence |
|---|---|---|---|
| `RV-01` | Review requests | **MODELLED** | `review` object exists, no automation |
| `RV-02` | Review monitoring / widgets | **MODELLED** | `review` object exists, no automation |

### Social & Ads

| ID | Feature | Status | Evidence |
|---|---|---|---|
| `SO-01` | Social planner scheduling | **MODELLED** | `socialPost` object exists, no automation |
| `SO-02` | Connected social accounts | **MODELLED** | `socialPost` object exists, no automation |
| `SO-03` | Ad manager (Google / Facebook) | **DESIGNED** | specified in the guide only |

### Reporting & Dashboards

| ID | Feature | Status | Evidence |
|---|---|---|---|
| `RP-01` | Attribution reporting | **DESIGNED** | specified in the guide only |
| `RP-02` | Call reporting | **DEFERRED** | call reporting — follows telephony |
| `RP-03` | Appointment reporting | **DESIGNED** | specified in the guide only |
| `RP-04` | Agent / user performance reporting | **DESIGNED** | specified in the guide only |
| `RP-05` | Custom dashboards | **DESIGNED** | specified in the guide only |

### Users & Permissions

| ID | Feature | Status | Evidence |
|---|---|---|---|
| `US-01` | User accounts and seats | **NATIVE** | Twenty workspace members |
| `US-02` | Roles and permission levels | **NATIVE** | Twenty roles |
| `US-03` | Teams | **NATIVE** | Twenty teams |
| `US-04` | SSO | **DESIGNED** | specified in the guide only |

### Settings & Business Profile

| ID | Feature | Status | Evidence |
|---|---|---|---|
| `ST-01` | Business profile / hours | **NATIVE** | Twenty workspace settings |
| `ST-02` | Custom menu links | **DESIGNED** | specified in the guide only |
| `ST-03` | URL redirects | **DEFERRED** | URL redirects — follows hosting |
| `ST-04` | Domains and DNS | **DEFERRED** | DNS — follows hosting |

### Integrations & Marketplace

| ID | Feature | Status | Evidence |
|---|---|---|---|
| `IN-01` | Installed marketplace apps | **DESIGNED** | specified in the guide only |
| `IN-02` | Native integrations (Stripe/Google/Meta/QuickBooks) | **DESIGNED** | specified in the guide only |
| `IN-03` | Zapier / Make connections | **DESIGNED** | specified in the guide only |
| `IN-04` | Inbound webhooks into GHL | **DESIGNED** | specified in the guide only |

### Agency & Multi-Location

| ID | Feature | Status | Evidence |
|---|---|---|---|
| `AG-01` | Sub-account count | **DEFERRED** | audit scope question, not a feature |
| `AG-02` | Snapshots | **DEFERRED** | audit instrument, not a feature |
| `AG-03` | SaaS mode / rebilling | **DEFERRED** | SaaS mode — Aspire does not resell |

### Media & Assets

| ID | Feature | Status | Evidence |
|---|---|---|---|
| `MD-01` | Media library | **DESIGNED** | specified in the guide only |

### Mobile & Field Access

| ID | Feature | Status | Evidence |
|---|---|---|---|
| `MO-01` | Mobile app | **DEFERRED** | no native mobile app in Twenty |
