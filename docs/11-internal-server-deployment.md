# Deploying to the Office Server

Written for someone who has not administered a server before. Every command is
meant to be typed exactly as written, and every one says what it does and what
you should see back.

The target here is a machine on the **office network** — a private address, no
inbound access from the internet. That is a fine place to run this, with one
real limitation you need to understand before you start.

---

## Read this first: what an internal server can and cannot do

Everything the CRM does **outbound** works normally:

- The CRM itself, for anyone in the office or on the VPN
- Sending email — renewal notices, acknowledgements, alerts
- Scheduled automation — renewal escalation, the daily sweeps
- Calling other services' APIs

Two things **cannot** work, and no configuration fixes them:

| Broken | Why |
|---|---|
| The public lead form | Someone on aspiretss.com submits it, their browser tries to reach this server, and a private address is not routable from the internet |
| Inbound webhooks | Cal.com, DocuSeal, or any vendor calling back cannot reach the server either |

This is a property of the network, not of the software. **§9 covers the fix** —
a Cloudflare Tunnel, which publishes only the form and webhook paths without
opening a single port. Do the main install first; add that when you need lead
capture.

If lead capture from the website is the thing you most want to demo, read §9
before you start, because it changes what you set up in §5.

---

## 1. Find out what you actually have

You need three facts. Ask whoever runs the office IT, or find them yourself.

**The server's address**, and whether you can reach it:

```bash
ping -c 3 192.168.1.50
```

Replace with your server's address. Three replies means it is reachable from
your laptop. `Request timeout` means it is not, and nothing else will work
until that is fixed.

**A login.** You need a username and either a password or an SSH key. If
someone set the server up for you, they have this.

**Whether it is really private.** An address starting `192.168.`, `10.`, or
`172.16.`–`172.31.` is private. Anything else may be public, in which case use
`docs/10-vps-deployment.md` instead — you get real certificates and working
public forms, which is strictly better.

## 2. Connect to it

From your laptop's terminal (Terminal on macOS, PowerShell or Windows Terminal
on Windows):

```bash
ssh rafi@192.168.1.50
```

Your username, your server's address. First connection asks:

```
The authenticity of host '192.168.1.50' can't be established.
Are you sure you want to continue connecting (yes/no/[fingerprint])?
```

Type `yes`. It asks that once, ever.

You are now typing commands **on the server**, not your laptop. Everything from
here until §8 happens there. `exit` returns you to your laptop.

Confirm what you are on:

```bash
cat /etc/os-release | head -2
```

You want `Ubuntu` or `Debian`. Something else and the install commands below
differ — stop and tell me which.

## 3. Basic server hygiene

Update the system:

```bash
sudo apt update && sudo apt upgrade -y
```

`sudo` means "as administrator" and will ask for your password. This takes a few
minutes on a server that has not been updated in a while.

Turn on the firewall. **Do the SSH rule first** — the order matters, because
enabling the firewall without it disconnects you and you will need physical
access to the machine to recover:

```bash
sudo apt install -y ufw
sudo ufw allow 22/tcp      # SSH — must be first
sudo ufw allow 80/tcp      # web
sudo ufw allow 443/tcp     # web, encrypted
sudo ufw --force enable
sudo ufw status
```

You should see the three rules and `Status: active`. The database and Redis are
deliberately not on that list; they are reachable only from inside the server.

## 4. Install Docker

Docker runs the application. One command:

```bash
curl -fsSL https://get.docker.com | sudo sh
```

Then let your user run it without `sudo` every time:

```bash
sudo usermod -aG docker $USER
```

**Log out and back in for that to take effect** — `exit`, then `ssh` again.
Check it worked:

```bash
docker run --rm hello-world
```

`Hello from Docker!` means you are ready. `permission denied` means the log out
and back in did not happen.

## 5. Choose your names

Pick two names for the two web addresses. On an internal network these do not
need to be real internet domains:

```
crm.aspire.local        the CRM
auto.aspire.local       forms and automation
```

Every machine that will use the CRM needs to resolve those names to the
server's address. Two ways:

**Best — ask IT to add two internal DNS records** pointing both names at
`192.168.1.50`. Then every machine works with no per-laptop setup.

**Quick — edit each laptop's hosts file.** Fine for a demo, tedious beyond three
people. On macOS or Linux, on the *laptop*, not the server:

```bash
sudo nano /etc/hosts
```

Add one line, save with `Ctrl+O` `Enter`, exit with `Ctrl+X`:

```
192.168.1.50   crm.aspire.local auto.aspire.local
```

On Windows the file is `C:\Windows\System32\drivers\etc\hosts`, edited in
Notepad run as Administrator.

> If the domain is real and you control its DNS, you can have proper
> browser-trusted certificates even on an internal server — see §8.

## 6. Install the CRM

Back on the server:

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

That prints four lines. Open the config and paste them in, replacing the empty
versions:

```bash
nano infra/.env
```

Also set, in the same file:

```ini
SERVER_URL=https://crm.aspire.local
N8N_HOST=auto.aspire.local
N8N_PROTOCOL=https
N8N_PUBLIC_URL=https://auto.aspire.local

CRM_DOMAIN=crm.aspire.local
AUTOMATION_DOMAIN=auto.aspire.local
```

Save with `Ctrl+O` `Enter`, exit with `Ctrl+X`.

> **Copy `ENCRYPTION_KEY` and `N8N_ENCRYPTION_KEY` into a password manager now.**
> Lose the first and every secret the CRM holds — connected mailboxes, OAuth
> tokens — becomes permanently unreadable. There is no recovery and nobody to
> ask.

`SERVER_URL` must match exactly what people type in the browser, `https://`
included. A mismatch produces a login page that loops forever and looks like a
broken password.

Now start it:

```bash
docker compose -f infra/docker-compose.yml \
               -f infra/docker-compose.internal.yml up -d
```

First run downloads about 5.5 GB and takes ten to twenty minutes depending on
the office connection. Watch it:

```bash
docker compose -f infra/docker-compose.yml \
               -f infra/docker-compose.internal.yml logs -f server
```

`Nest application successfully started` means the CRM is up. `Ctrl+C` stops
watching the log; it does not stop the server.

Then build the CRM itself:

```bash
python3 scripts/bootstrap_workspace.py
python3 scripts/bootstrap_n8n.py
python3 scripts/twenty_provision.py
python3 scripts/n8n_credentials.py
python3 scripts/validate_workflow_queries.py
python3 scripts/n8n_deploy.py --activate
python3 scripts/stack_verify.py
```

The last one should end `Stack healthy`.

Two things **not** to do here:

- **Do not run `seed_demo_data.py`.** That is demo data. This is the real system.
- **Do not pass `--dev` to the deploy.** It installs a test workflow that
  swallows real alerts, and another that fails on purpose.

## 7. Trust the certificate

Open `https://crm.aspire.local` on your laptop. The browser warns that the
certificate is not trusted. That is expected: the server issued its own, because
a public certificate authority cannot verify a server it cannot reach.

You can click through the warning for a quick look. To remove it properly, copy
the server's root certificate to each laptop once.

On the **server**, get the certificate:

```bash
docker compose -f infra/docker-compose.yml \
               -f infra/docker-compose.internal.yml \
  exec caddy cat /data/caddy/pki/authorities/local/root.crt > aspire-root.crt
```

On your **laptop**, fetch and install it:

```bash
scp rafi@192.168.1.50:~/aspire-crm-twenty/aspire-root.crt .
```

- **macOS:** double-click the file → Keychain Access opens → find "Caddy Local
  Authority" → double-click → Trust → *Always Trust*.
- **Windows:** double-click → Install Certificate → Local Machine → Place all
  certificates in the following store → *Trusted Root Certification Authorities*.
- **Ubuntu:** `sudo cp aspire-root.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates`

Restart the browser. The warning is gone and the padlock is real.

## 8. Optional: real certificates on an internal server

If `aspiretss.com` is yours and you can create DNS records, you can have
publicly-trusted certificates with no warning and nothing to install on
laptops — using a DNS-01 challenge, which proves ownership through a DNS record
instead of an inbound connection.

It needs an API token from your DNS provider and a Caddy image built with that
provider's plugin. Tell me who hosts the DNS — Cloudflare, GoDaddy, Namecheap —
and I will write that config. It is about fifteen minutes of work and removes
§7 entirely.

## 9. Making the public form work

The lead form is the most convincing thing in the demo, and on an internal
server it cannot be reached from outside. The fix is a **Cloudflare Tunnel**: a
small program on the server that makes an *outbound* connection to Cloudflare,
which then publishes a public HTTPS address routed back down that connection.
No open ports, no public IP, no firewall change.

Publish only the form and webhook paths — never the automation editor, which
holds every credential in the stack.

It is free, and it needs the domain's DNS at Cloudflare. Once you have confirmed
who manages DNS, tell me and I will add the service and the path rules to the
compose file.

Until then, lead capture is the one part of the system you demo *on a laptop*
rather than on the server.

---

## Checking it works

| Check | Expected |
|---|---|
| `https://crm.aspire.local` from an office laptop | CRM loads |
| `python3 scripts/stack_verify.py` on the server | `Stack healthy` |
| `python3 scripts/verify_restore.py` on the server | Passes — prove it here, not just on a laptop |
| `docker compose ... ps` | `caddy server worker n8n db redis backup` all up |

The **worker** deserves its own look. The CRM's interface looks completely
healthy while the worker is dead, and a dead worker means no scheduled
automation and no mailbox sync — silently.

## Day-to-day

**Change the default password.** `bootstrap_workspace.py` creates
`admin@aspiretss.com` with a password documented in a public repository. Change
it in the CRM's settings, and the n8n one too.

**Copy the backups off the server.** Nightly dumps land in `infra/backups`, on
the same disk as the database. That protects you from a bad migration, not from
a dead server:

```bash
rsync -az --delete infra/backups/ backup-host:/srv/aspire-crm/
```

**Restarting after a reboot.** Everything is set to restart automatically. If
the server was off for a while:

```bash
cd ~/aspire-crm-twenty
docker compose -f infra/docker-compose.yml \
               -f infra/docker-compose.internal.yml up -d
```

**Watching the logs.**

```bash
docker compose -f infra/docker-compose.yml \
               -f infra/docker-compose.internal.yml logs -f --tail=100
```

## When something is wrong

| What you see | What it is |
|---|---|
| Browser cannot reach the site | Name not resolving. `ping crm.aspire.local` from the laptop |
| Certificate warning after §7 | Browser not restarted, or the certificate installed on the wrong store |
| Login page loops forever | `SERVER_URL` does not match the address in the browser |
| Site loads, nothing scheduled runs | Worker down. `docker compose ... logs worker` |
| `permission denied` on docker | You did not log out and back in after §4 |
| Everything slow | Check RAM: `free -h`. This needs 6 GB free; 8 GB is comfortable |
