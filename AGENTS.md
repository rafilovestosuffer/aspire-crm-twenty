# AGENTS.md

Operating rules for this repo live in [`CLAUDE.md`](CLAUDE.md) (read-only against
GHL, no PII, rate limits, Twenty/n8n query syntax). Build and run instructions
live in [`README.md`](README.md) and [`docs/08-local-build.md`](docs/08-local-build.md).
This file only adds cloud-environment caveats.

## Cursor Cloud specific instructions

The demonstrable application is the **self-hosted Twenty CRM + n8n stack** in
`infra/` (Docker Compose).

### GHL audit — already pulled (do not re-request a token)
The read-only GoHighLevel inventory was taken 2026-08-18 (GET only, no PII).
Findings live in [`docs/12-ghl-account-inventory.md`](docs/12-ghl-account-inventory.md);
`out/feature_audit.csv` has named evidence for 47 of 111 features. The Private
Integration token used for that pull was one-shot and **should not be stored**.
Do not ask for `GHL_TOKEN` unless the user explicitly wants a re-pull.

- Location ID (from the GHL URL, not a secret): `62iXlYxxHYUv14IS2LjG`
- Account timezone: `America/New_York` — set `TZ=America/New_York` on the new
  stack (and keep Twenty datetime writes as UTC `Z` via `.toUTC().toISO()`).
- A re-pull still needs a *new* PIT in `.env` (`GHL_TOKEN` + `GHL_LOCATION`).
  Cloudflare blocks urllib's default User-Agent (error 1010); `/surveys/`
  rejects `limit>50`; `/medias/files` requires `type`; `/calendars/events`
  must be queried once per calendar. Workflow internals are not on the public
  API. Payment subscriptions 401'd on the original token's scopes.

### Docker is required and the daemon does not auto-start
Docker (with the `fuse-overlayfs` storage driver and `iptables-legacy`) is
installed in the VM image, but no init system runs, so **start the daemon each
session** before any stack command:

```bash
sudo dockerd > /tmp/dockerd.log 2>&1 &   # run in a tmux session so it persists
```

The `ubuntu` user is in the `docker` group, but group membership is **not picked
up by the persistent/login shells** in this VM. So either prefix docker-touching
commands with `sudo`, or wrap them so a child process gets the group:

```bash
sg docker -c './infra/up.sh'
sg docker -c 'python3 scripts/prove_workflows.py'
```

Child processes inherit the group, so wrapping the outer script/command is
enough — no need to wrap every nested `docker` call.

### n8n container always reports "unhealthy" here — it is a false negative
Inside the `n8n` container, `localhost` resolves to IPv6 `::1`, but n8n binds
IPv4 (`N8N_LISTEN_ADDRESS=0.0.0.0`), so the compose healthcheck
(`wget localhost:5678/healthz`) is refused and the container is marked
`unhealthy`. n8n is actually fine: `/healthz` returns 200 on the host port and on
`127.0.0.1` inside the container, and all 11 workflows deploy/run. Consequences:

- `scripts/stack_verify.py` prints one FAIL (`container: n8n unhealthy`) and
  exits non-zero even when everything works.
- `infra/rebuild.sh` (the one-shot "zero to proven") therefore **halts at the
  "Verify every layer" gate**. To get a full build, run the steps individually
  from the README quick start instead of `rebuild.sh`, and treat the n8n
  container-health line as expected. The authoritative end-to-end proof is
  `scripts/prove_workflows.py` (expect all checks to pass).

Do not "fix" this by switching the n8n listen address to `::` — the compose
comment warns it crash-loops on hosts without IPv6.

### First-run timing and Twenty browser onboarding
- Twenty's first boot runs migrations (~2–5 min); `infra/up.sh` waits for health.
  `scripts/seed_demo_data.py` takes several minutes (448 records).
- `scripts/bootstrap_workspace.py` creates the user/workspace/API key over the
  API, but the **browser UI still requires completing a one-time onboarding**
  the first time you open `http://localhost:3000`: fill the "Create profile"
  name fields, then choose "Continue without sync" and "Skip" the team-invite
  step. Login is `admin@aspiretss.com` / `AspireDemo2026!` (same for n8n).

### Local mail stays on the host
The local build points n8n SMTP at the **Mailpit** container
(`http://localhost:8025`); nothing leaves the host. Live Gmail/Workspace sending
and mailbox sync require external OAuth/app-password secrets — see
`docs/08-local-build.md` ("Live Gmail demo"). Do not point SMTP at a real relay
and re-run `prove_workflows.py` without `--live-email`.
