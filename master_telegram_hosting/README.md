# master_telegram_hosting

A Telegram-native hosting panel MVP that lets users upload Python/Node projects, sends them through security checks and owner approval, and manages execution, logs, editing, and admin workflows entirely inside Telegram.

## Stack

- Python 3.12+
- Telethon (git/master-style install for styled button support)
- PostgreSQL 15+
- Redis 7+
- systemd transient units (`systemd-run` / `systemctl`)
- Node.js 20+ for `.js` syntax checks and runtime

## 1. Server prerequisites

On Ubuntu/Debian-like systems:

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip postgresql postgresql-contrib redis-server nodejs npm unzip
```

Make sure systemd is available and the bot user is allowed to use:

- `systemd-run`
- `systemctl`

For stricter servers, this is usually done via a controlled `sudoers` rule.

## 2. Project layout

This repo expects to live somewhere like:

```bash
/srv/master_telegram_hosting
```

Copy the repo there and create a virtualenv:

```bash
cd /srv/master_telegram_hosting
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel
pip install -r requirements.txt
```

## 3. PostgreSQL setup

Create the DB and user:

```bash
sudo -u postgres psql
```

Then inside psql:

```sql
CREATE USER tg_hosting WITH PASSWORD 'strongpassword';
CREATE DATABASE tg_hosting OWNER tg_hosting;
GRANT ALL PRIVILEGES ON DATABASE tg_hosting TO tg_hosting;
```

## 4. Redis setup

Make sure Redis is running:

```bash
sudo systemctl enable --now redis-server
sudo systemctl status redis-server
```

## 5. Environment configuration

Copy and edit the env file:

```bash
cp .env.example .env
nano .env
```

Set at minimum:

- `API_ID`
- `API_HASH`
- `BOT_TOKEN`
- `OWNER_TELEGRAM_ID`
- `DATABASE_URL`
- `REDIS_URL`
- storage paths
- runtime binary paths

## 6. Database migrations (Alembic)

This repo includes a basic Alembic bootstrap and an initial schema migration.

Run:

```bash
source .venv/bin/activate
alembic upgrade head
```

### Important note

The current MVP application still calls `Base.metadata.create_all()` on startup as a safety net during early iteration. You should still run:

```bash
alembic upgrade head
```

before starting the bot on a VPS.

## 7. Development run on a single VPS

Activate your virtualenv and start the bot:

```bash
source .venv/bin/activate
python -m app.main
```

If you want verbose logs during development:

```bash
PYTHONUNBUFFERED=1 python -m app.main
```

## 8. systemd for the hosting bot itself

Example service files are included under:

```text
deploy/systemd/
```

Typical install flow:

```bash
sudo cp deploy/systemd/master_telegram_hosting.service /etc/systemd/system/
sudo cp deploy/systemd/master_telegram_hosting-migrate.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable master_telegram_hosting.service
sudo systemctl start master_telegram_hosting.service
```

If you keep the environment file somewhere else, update `EnvironmentFile=` in the service file.

## 9. Suggested first-production checklist

1. Run `alembic upgrade head`
2. Confirm Redis is reachable
3. Confirm Postgres is reachable
4. Confirm `systemd-run` works for the bot user
5. Confirm Node.js path is correct
6. Confirm `OWNER_TELEGRAM_ID` is your actual Telegram numeric user id
7. Confirm storage directories are writable by the bot user

## 10. Security notes

This MVP already includes:

- upload-time static checks
- approval-first execution
- systemd transient units
- CPU/memory/runtime limit abstraction
- project log separation
- audit/event logging
- reconciliation after bot restart

Still recommended next:

- per-project environment-variable management
- restart-storm protection / auto-disable
- stricter systemd/sudo policy
- optional per-project Linux user isolation
