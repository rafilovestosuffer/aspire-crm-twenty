# Deploying to the Company Server

## Tonight, in order

Everything below is explained in detail further down. This is the sequence.

```bash
# --- on the server, as a normal user with sudo ---
sudo apt update && sudo apt upgrade -y
sudo apt install -y ufw git python3 curl
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
sudo ufw --force enable                      # SSH rule FIRST or you lock yourself out

curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
exit                                          # log out and back in, or docker stays denied
```

Log back in, then:

```bash
git clone https://github.com/rafilovestosuffer/aspire-crm-twenty.git
cd aspire-crm-twenty
cp infra/.env.example infra/.env

# print four secrets, paste them into infra/.env
for k in PG_DATABASE_PASSWORD ENCRYPTION_KEY APP_SECRET N8N_ENCRYPTION_KEY; do
  printf '%s=%s\n' "$k" "$(openssl rand -base64 32)"
done

nano infra/.env        # set the four secrets + the block below
```

The minimum that must be set:

```ini
SERVER_URL=https://crm.aspiretss.com
N8N_HOST=auto.aspiretss.com
N8N_PROTOCOL=https
N8N_PUBLIC_URL=https://auto.aspiretss.com

CRM_DOMAIN=crm.aspiretss.com
AUTOMATION_DOMAIN=auto.aspiretss.com
ACME_EMAIL=it@aspiretss.com

EMAIL_SMTP_HOST=smtp-relay.gmail.com
EMAIL_SMTP_PORT=587
EMAIL_SMTP_USER=<workspace user>
EMAIL_SMTP_PASSWORD=<app password>

ALERT_WEBHOOK_URL=<real chat webhook, or leave blank>
```

Then:

```bash
./infra/preflight.sh     # 20 seconds. Fix anything it calls a blocker
./infra/deploy.sh        # ~15 minutes
```

`preflight.sh` checks RAM, disk, Docker, every required setting, that DNS
resolves **to this server**, that ports 80 and 443 are free, and that the
firewall allows them. It exists because each of those otherwise fails later,
with an error that does not name the cause — and a DNS mistake discovered
during the real certificate request costs you a Let's Encrypt rate limit.

`deploy.sh` runs ten gated steps and stops at the first failure: stack up,
worker check, workspace, object model, credentials, query validation, deploy
and activate, verify every layer, and prove the backup restores on this
machine's own disk.

### Before you start, have these

| | |
|---|---|
| DNS | Both A records added **and resolving** — `dig +short crm.aspiretss.com` |
| SMTP | Host, user, app password. Without it the deploy stops rather than half-working |
| Office IP range | For the editor allowlist in step 3 below |

### Three things immediately after

1. **Change both default passwords** — CRM and automation. They are in a public repository.
2. **Check the padlock** on the CRM. A warning means Let's Encrypt could not reach port 80.
3. **Edit the editor allowlist** in `infra/Caddyfile` — it ships matching nothing, so the editor currently refuses everyone including you. Put your office and VPN ranges in, then recreate Caddy.

---

## The detail

The laptop stack and the server stack are the same files. What changes is
`infra/.env`, one overlay, and a reverse proxy in front. Nothing is rebuilt and
nothing is untested at cutover.

Budget half a day, most of it waiting for DNS.

---

## What you need first

| | |
|---|---|
| Server | 4 cores, 8 GB RAM, 60 GB SSD minimum. 8 cores / 16 GB for real load |
| OS | Any current Linux with Docker Engine and Compose v2 |
| DNS | Two A records pointing at the server, **already propagated** |
| Ports | 80 and 443 open inbound. Nothing else needs to be |
| Access | SSH with key auth. Password auth off |

Two names, both pointing at the same server:

```
crm.aspiretss.com     →  <server ip>     the CRM
auto.aspiretss.com    →  <server ip>     forms, webhooks, automation editor
```

**Confirm DNS resolves before you start.** Let's Encrypt validates by fetching a
file over HTTP from the name you claim. If DNS is not live yet, the certificate
fails, Caddy serves an untrusted one, and browsers refuse the site — and you
will burn Let's Encrypt rate limit rediscovering that.

```bash
dig +short crm.aspiretss.com
dig +short auto.aspiretss.com
```

---

## Contabo specifics

Contabo is a VPS host: the server has a **public IPv4 address**, which is what
makes everything below work — real certificates, working public forms, inbound
webhooks. If someone describes it as "the office server" that is about who pays
for it, not where it sits on the network.

Confirm in one command, on the server:

```bash
curl -s ifconfig.me; echo
ip -4 addr show | grep inet
```

If the two agree, and the address does **not** start `192.168.`, `10.` or
`172.16.`–`172.31.`, it is public and this document applies. If they disagree,
the machine is behind NAT — use `docs/11-internal-server-deployment.md` instead.

**Getting in the first time.** Contabo emails the root password when the server
is provisioned; it can also be reset from the customer panel under *Your
Services → the VPS → Password reset*. First login:

```bash
ssh root@<your server ip>
```

**Working as root.** Contabo hands you root by default. Create an ordinary user
and stop using root over SSH:

```bash
adduser rafi
usermod -aG sudo rafi
```

Then log in as that user from now on. The install steps below assume `sudo`.

**Firewall.** Contabo does not filter traffic by default on most plans — the
`ufw` rules below are your only firewall, so do not skip them. Newer plans also
offer a Cloud Firewall in the panel; if it is enabled, allow 22, 80 and 443
there too, or the certificate request will time out with no explanation.

**DNS.** If the domain's nameservers point at Contabo, add the two A records in
the customer panel under *Domains → DNS management*. HTTP-01 only needs the
records to resolve — no API token, no provider plugin.

**Sizing.** Contabo's cheaper VPS plans are generous on disk and thin on RAM.
This stack wants 8 GB. On 4 GB, Twenty and Postgres will contend and the UI
will feel slow under any real load.

---

## Step 1 — Bring the stack up on the server

```bash
git clone https://github.com/rafilovestosuffer/aspire-crm-twenty.git
cd aspire-crm-twenty
cp infra/.env.example infra/.env
```

Generate the four secrets:

```bash
for k in PG_DATABASE_PASSWORD ENCRYPTION_KEY APP_SECRET N8N_ENCRYPTION_KEY; do
  printf '%s=%s\n' "$k" "$(openssl rand -base64 32)"
done
```

Paste those into `infra/.env`, then set:

```ini
SERVER_URL=https://crm.aspiretss.com
N8N_HOST=auto.aspiretss.com
N8N_PROTOCOL=https
N8N_PUBLIC_URL=https://auto.aspiretss.com

CRM_DOMAIN=crm.aspiretss.com
AUTOMATION_DOMAIN=auto.aspiretss.com
ACME_EMAIL=it@aspiretss.com

ALERT_WEBHOOK_URL=<the real chat webhook>
```

> **Back up `ENCRYPTION_KEY` and `N8N_ENCRYPTION_KEY` to a password manager
> before going further.** Lose the first and every secret Twenty holds — OAuth
> tokens, connected mailboxes — becomes permanently unreadable. There is no
> recovery path, and no support to call.

`SERVER_URL` must match how people actually reach the CRM, scheme included. A
mismatch produces a login redirect loop that looks like an auth bug.

## Step 2 — Edit the editor allowlist

Open `infra/Caddyfile` and replace the two placeholder ranges with your real
office and VPN ranges:

```
@untrusted not remote_ip 203.0.113.0/24 198.51.100.0/24
```

Public forms and inbound webhooks stay open — they have to be. Everything else
on `auto.` is the n8n editor, **which holds every credential in the stack**.
Exposing it to the internet is the single largest avoidable risk in this
deployment.

The placeholders match nothing routable, so if you forget this step you lock
yourself out rather than letting the world in. That is the safer direction to
fail in. If you do lock yourself out, `docker compose exec caddy` still works
over SSH.

## Step 3 — Rehearse the certificate

Before the real run, uncomment the staging line in the `Caddyfile`:

```
acme_ca https://acme-staging-v02.api.letsencrypt.org/directory
```

Start the stack:

```bash
./infra/up.sh --vps
docker compose -f infra/docker-compose.yml -f infra/docker-compose.vps.yml logs -f caddy
```

You want `certificate obtained successfully`. The browser will warn that the
certificate is untrusted — that is expected from the staging CA and means the
challenge worked. Staging has far looser rate limits, so a misconfigured DNS
record costs you nothing here.

Then comment the staging line out, and:

```bash
docker compose -f infra/docker-compose.yml -f infra/docker-compose.vps.yml \
  up -d --force-recreate caddy
```

Real certificates arrive within a minute. Renewal is automatic — there is no
cron job to add and nothing to diarise.

## Step 4 — Build the CRM

Identical to the laptop, minus the mail catcher:

```bash
python3 scripts/bootstrap_workspace.py
python3 scripts/bootstrap_n8n.py
python3 scripts/twenty_provision.py
python3 scripts/n8n_credentials.py
python3 scripts/validate_workflow_queries.py
python3 scripts/n8n_deploy.py --activate
python3 scripts/stack_verify.py
```

**Do not run `seed_demo_data.py` on the server.** It is demo data. Real records
arrive in Phase 4.

**Do not pass `--dev`.** That would deploy the alert sink, which swallows alerts
that should go to the real chat webhook, and the failure probe, which throws on
purpose.

Set real SMTP in `infra/.env` before deploying credentials, or `n8n_credentials.py`
points at a Mailpit container that does not exist here:

```ini
EMAIL_SMTP_HOST=smtp-relay.gmail.com
EMAIL_SMTP_PORT=587
EMAIL_SMTP_USER=<workspace user>
EMAIL_SMTP_PASSWORD=<app password>
```

## Step 5 — Change the default login

`bootstrap_workspace.py` creates `admin@aspiretss.com` with the documented
demo password. Change it in the CRM under Settings, and change the n8n owner
password too. Both are in a public repository.

## Step 6 — Prove the restore, here

```bash
python3 scripts/verify_restore.py
python3 scripts/verify_restore.py --database n8n
```

Backups on the laptop restoring cleanly says nothing about this machine's disk,
its volumes, or its Postgres. Run it here, on day one, and again monthly. The
date of the last success is recorded in `out/restore_verification.json`.

---

## Verification

| Check | Expected |
|---|---|
| `https://crm.aspiretss.com` | Loads, valid certificate, no warning |
| `https://auto.aspiretss.com/form/aspire-contact` | Form renders |
| `https://auto.aspiretss.com/` from an untrusted network | 403 |
| `curl -I http://crm.aspiretss.com` | 308 redirect to https |
| `python3 scripts/stack_verify.py` | All green, worker running |
| `docker compose ... ps` | `caddy`, `server`, `worker`, `n8n`, `db`, `redis`, `backup` all up |

The worker deserves its own look. Twenty's UI is perfectly healthy while the
worker is dead — and a dead worker means no scheduled workflows and no mailbox
sync, silently.

---

## Operating it

**Backups.** Nightly `pg_dump` of both databases into `infra/backups`, 30-day
retention. That directory is on the same disk as the database, which is not a
backup — it survives a bad migration, not a dead server. Copy it off the host
nightly:

```bash
rsync -az --delete infra/backups/ backup-host:/srv/aspire-crm/
```

**Upgrades.** The image tag is pinned in `infra/.env` and must stay pinned.
`latest` can run migrations you did not choose, at a time you did not choose.
To upgrade: read the release notes, verify a restore, bump `TAG`, `up -d`,
then `stack_verify.py`.

**Logs.**

```bash
docker compose -f infra/docker-compose.yml -f infra/docker-compose.vps.yml \
  logs -f --tail=100 server worker n8n caddy
```

**If the certificate fails.** Almost always DNS not resolving, or port 80
blocked. Caddy says which in its logs. It retries on its own; you do not need
to restart it.

**If the site loops on login.** `SERVER_URL` does not match the address in the
browser — usually `http` versus `https`, or a missing subdomain.

---

## Not done by this document

- **Firewall.** Allow 22, 80, 443. Deny everything else. Postgres and Redis
  must never be reachable from outside the host.
- **Unattended security updates** on the host.
- **Monitoring.** Worker liveness, workflow failure rate, disk, and backup
  freshness. `stack_verify.py` is the check; something needs to run it and
  shout.
- **A second operator.** One person who can restore this system is a single
  point of failure, and it is the most likely thing to hurt.
