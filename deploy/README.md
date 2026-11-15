# Deploying the DocAgent backend

The backend (API + agent, four MCP servers, Postgres) runs with Docker Compose on one
DigitalOcean droplet (2 GB RAM, Ubuntu). Caddy terminates TLS for `api.meetmoeed.com` and is
the only service with published ports. The web app is deployed separately (Vercel).

```
internet ──443──▶ caddy ──▶ api ──▶ extraction · validation · sqlstore · reference
                                 └─▶ postgres          (internal Docker network only)
```

| Path on the server | What |
|---|---|
| `/opt/docagent` | git checkout (owned by `deploy`); `docker-compose.prod.yml` lives here |
| `/opt/docagent/.env` | production secrets and settings, mode `600`, **never committed** |
| `/opt/docagent/.image-tag` | commit SHA of the running image |
| `/opt/docagent/.deploy-history` | one line per successful deploy |
| `/var/backups/docagent` | nightly `pg_dump` files, 7 days kept |
| `/var/log/docagent` | backup and cleanup logs (rotated weekly) |
| `/etc/cron.d/docagent` | copy of `deploy/cron` |

Docker volumes: `pgdata` (database), `uploads` (uploaded files and cached page images; they
expire with their 24 h session), `caddy_data` (TLS certificates).

All commands below run on the server as `deploy`, from `/opt/docagent`. `deploy/dc` is
`docker compose -f docker-compose.prod.yml` pinned to the deployed image.

## First-time setup

1. **User and SSH.** As root: create `deploy` with sudo and your SSH key, check that
   `ssh deploy@<host>` works, then set `PermitRootLogin no` and `PasswordAuthentication no`
   (a drop-in in `/etc/ssh/sshd_config.d/`) and reload sshd. Use `User deploy` in your SSH
   config from then on.
2. **Server.** `sudo bash deploy/setup-server.sh` (after cloning, step 3) installs Docker and
   the Compose plugin, adds a 2 GB swap file, enables UFW (22, 80, 443 only) and unattended
   security upgrades, and creates the directories, cron job and logrotate config. It is safe
   to re-run. Note that Docker-published ports bypass UFW, which is why only Caddy publishes
   any.
3. **Code.** `git clone https://github.com/moeedkundi/doc-agent.git /opt/docagent`
4. **Settings.** Create `/opt/docagent/.env` from `deploy/env.prod.example` and
   `chmod 600 .env`. Generate each `<random>` with `openssl rand -hex 24`; use the same value
   for `POSTGRES_PASSWORD` and the password in `DATABASE_URL`. Copy `GEMINI_API_KEY` without
   echoing it to the terminal or shell history.
5. **DNS.** `api.meetmoeed.com` must point at the droplet before the first start, so Caddy
   can get its certificate.
6. **Start.** `deploy/deploy.sh` — builds the image, runs migrations (the one-shot `migrate`
   service also sets the app/reader role passwords from `.env`), starts everything and waits
   for `/api/health`.

Check: `curl https://api.meetmoeed.com/api/health` shows `"status":"ok"` and all four MCP
servers `"ok"`.

## Update

```bash
deploy/deploy.sh                # deploy origin/master
deploy/deploy.sh <sha-or-tag>   # deploy a specific commit
```

The script fetches, checks out the ref, builds `docagent-backend:<sha>` (skipped if that
image exists), runs pending migrations, recreates changed containers and waits for the API
health check. On failure it prints the rollback command. The last 5 images are kept.

Changing only `.env`: `deploy/dc up -d` (containers with changed settings are recreated).

## Roll back

```bash
cat .deploy-history             # previous SHAs
deploy/deploy.sh <previous-sha> # usually instant: the image is still there
```

Migrations only go forward. If the release you're leaving added a migration the older code
can't live with, restore the backup taken before that deploy (below) after rolling back.
Take one first if in doubt: `deploy/backup.sh`.

## Backups and restore

`deploy/backup.sh` runs nightly at 03:15 UTC (cron) and writes
`/var/backups/docagent/docagent-<UTC timestamp>.dump` (`pg_dump -Fc`), deleting files older
than 7 days. Run it by hand before risky changes. Copy dumps off the server if you need
history beyond a week.

Restore (stops the services, replaces the database, starts them again; asks to confirm):

```bash
deploy/restore.sh /var/backups/docagent/docagent-20261115-031500.dump
```

Uploaded files are not backed up: they belong to sessions that expire after 24 h.

## Scheduled jobs (`deploy/cron`)

| When (UTC) | Job | Log |
|---|---|---|
| 03:15 daily | `deploy/backup.sh` | `/var/log/docagent/backup.log` |
| :07 hourly | `deploy/cleanup.sh`: deletes sessions past their 24 h lifetime with their records, runs, uploaded files, cached page images and checkpoints; sweeps orphan upload files | `/var/log/docagent/cleanup.log` |

After changing `deploy/cron`: `sudo install -m 644 deploy/cron /etc/cron.d/docagent`.

## Operations

```bash
deploy/dc ps                       # status and health
deploy/dc logs -f --tail=100 api   # logs (any service)
docker stats --no-stream           # memory against the limits
deploy/dc restart api              # restart one service
```

Restart policies are `unless-stopped`: services come back after a crash or a reboot.
Memory limits (MB): api 512, extraction 448, postgres 320, sqlstore 160, validation 128,
reference 128, caddy 64.

**Spend cap.** `DAILY_BUDGET_USD` caps the public demo's LLM spend per UTC day (the gateway
refuses calls past it and the UI switches to recorded runs). To take the demo off the LLM
entirely, set `REPLAY_ONLY=true` in `.env` and `deploy/dc up -d`.

**Security notes.** The MCP servers and Postgres have no published ports; they are reachable
only from containers on the internal network, and the MCP servers also require the shared
secret. Caddy serves only `/api/*` and answers 404 elsewhere.
