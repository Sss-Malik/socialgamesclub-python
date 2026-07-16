# AWS re-provisioning & right-sizing — design

**Date:** 2026-07-16
**Status:** Approved (design), pending provisioning
**Goal:** Re-provision this service on AWS from scratch on a minimal, affordable instance, replacing the previous `m5.2xlarge`. No high-concurrency requirement.

---

## 1. What this service is

`socialgamesclub` is the automation worker tier behind a Laravel storefront hosted on Hostinger. Laravel owns the customer-facing site, the payments, and — critically — the MySQL schema. This Python service is a *second writer* into Laravel's database.

```
Laravel (Hostinger)  ──HTTP──▶  FastAPI (:8000, this box)
      ▲                              │
      │                              ▼
      │                        Celery (Redis broker)
      │                              │
      │                              ▼
      │                    backends/<name>/automation.py
      │                     ├── 7 Playwright (Chromium)
      │                     └── 5 HTTP API clients
      │                              │
      └──webhook (notify.py)◀────────┤
                                     ├──▶ MySQL (Hostinger, shared w/ Laravel)
                                     ├──▶ S3 (error screenshots)
                                     ├──▶ SendGrid :587 (ops alerts)
                                     ├──▶ anti-captcha.com
                                     └──▶ 12 casino panels
```

Laravel also routes ECashApp payment calls *through* this box (`POST /ecashapp/forward`) purely because this box's IP is whitelisted at the merchant gateway and Hostinger's is not.

### Backend split (12 total)

| Style | Backends | Browser? | S3 screenshots? |
|---|---|---|---|
| Playwright | firekirin(1), orionstars(5), pandamaster(6), river(7), ultrapanda(8), vblink(9), milkyway(12) | Yes | Yes |
| HTTP API | gameroom(2), gamevault(3), juwa(4), juwa2(10), goldentreasure(11) | **No** | No |

The HTTP backends still contain `utils/actions.py` / `utils/session.py` files that import Playwright, but their `automation.py` never imports them. Dead code — a naive `grep playwright backends/` overcounts browser backends as 12 when the real number is 7.

---

## 2. Why the m5.2xlarge was oversized

The instance was sized around a latent bug, not around load.

`common/utils/playwright_pool.py:14-25` launches a Chromium **at module import time**. `api/dispatcher.py:7` imports `backends.<name>.automation` lazily **inside the Celery task**, i.e. inside a prefork child. Therefore *every prefork child that ever runs a Playwright task launches its own Chromium, which then lives for the life of the process*. `common/utils/browser.py:7` caches one `BrowserContext` per backend in a per-process dict that is **never evicted**, so each child can accumulate 7 contexts.

At `--concurrency=100` that is up to 100 Chromiums. That number is an artifact:

| Date | Commit | Flag | Note |
|---|---|---|---|
| 2025-07-08 | `acf1433` | `--concurrency=8` | Last *deliberate* prefork value |
| 2025-07-29 | `a63ff43` | `--concurrency=600 --pool=gevent` | 600 **greenlets** sharing one Chromium — cheap |
| 2025-07-29 | `3ba4c23` | `--concurrency=600` | `--pool=gevent` removed → 600 became **600 processes** |
| 2025-10-07 | `9f5c6ff` | `--concurrency=100` | Tuned the symptom; units never re-derived |

`600` was a gevent number. When the pool arg was dropped it silently changed units from greenlets to OS processes. The cut to 100 reduced the blast radius without revisiting the model.

`playwright_pool.py:7-8` defines `MAX_CONTEXTS` / `_CONTEXT_SEM`, but `_CONTEXT_SEM` **is never acquired anywhere in the repo** — dead code, so contexts are uncapped as well.

### Observed evidence

During the 2026-07-01 event-loop-deadlock incident (see `web-eventloop-deadlock` memory), live production measurements were:

- Host memory: **5.7 GB used of 31 GB**
- MySQL: **7 connections of 500**
- Redis: healthy
- Diagnosis: an in-process deadlock — explicitly **not** resource exhaustion

So the 32 GB box was never load-bound. Capping concurrency bounds the browser count deterministically.

### Projected footprint at `--concurrency=6`

| Component | Estimate |
|---|---|
| 6 Chromium children (base + up to 7 contexts each) | ~2.4 GB |
| uvicorn web, 2 workers | ~0.5 GB |
| Redis | ~0.05 GB |
| OS + Docker | ~0.5 GB |
| **Steady state** | **~3.5–4.5 GB** |

`t3.large` (2 vCPU / 8 GB) fits this with roughly 2x headroom.

---

## 3. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| MySQL | **Stay on Hostinger** | Laravel owns the schema; no migrations exist here. $0 extra. Needs Elastic IP allowlisted in Remote MySQL. |
| Region | **us-west-2 (Oregon)** | Matches `AWS_REGION` default in `settings.py`. Same price as us-east-1. |
| Ingress | **Plain HTTP on the Elastic IP** | Chosen explicitly. Mitigated by restricting port 8000 to Hostinger's IP only. |
| Instance | **t3.large, x86_64** | 2 vCPU / 8 GB. See §2. |
| Architecture | **Single box, existing docker-compose** | No ECS/RDS/ElastiCache/ALB/NAT. |

### Why x86_64 and not Graviton (t4g)

`t4g.large` would save ~$6/mo and Playwright's official images do support arm64 Chromium. Rejected anyway: `river` (BACKEND_ID 7) runs reCAPTCHA v3 Enterprise scoring behind `common/utils/stealth.py`, and the spoofed UA claims `Windows NT 10.0; Win64; x64`. Running on ARM shifts the genuine fingerprint surface underneath that claim (platform, hardwareConcurrency, GPU strings), which raises bot-detection risk on a backend that is already the most fragile. Not worth $6/mo.

### What we deliberately do NOT provision

| Not provisioned | Why |
|---|---|
| RDS | DB stays on Hostinger |
| ElastiCache | Redis is a container; broker-only, low volume |
| ALB / NLB | ~$18–20/mo for a single-target box; plain HTTP chosen |
| NAT Gateway | ~$32/mo; instance sits in a public subnet with an EIP |
| ECS / EKS | Single box; docker-compose is sufficient |
| Route 53 | Reaching by IP |
| CloudWatch agent | Optional; basic status checks are free |

---

## 4. AWS resource inventory

| # | Resource | Spec |
|---|---|---|
| 1 | Key pair | `socialgamesclub-prod`, ED25519 |
| 2 | Security group | `socialgamesclub-sg` — in: 22/my-IP, 8000/Hostinger-IP; out: all |
| 3 | S3 bucket | private, us-west-2, **ACLs enabled (Bucket owner preferred)**, 90-day lifecycle |
| 4 | IAM policy + role | `s3:PutObject` on `<bucket>/screenshots/*` only |
| 5 | EC2 instance | t3.large, Ubuntu 24.04 x86_64, 30 GB gp3, **IMDS hop limit 2** |
| 6 | Elastic IP | allocated + associated |

### Two gotchas that will silently break things

**(a) IMDS hop limit.** `common/utils/aws_s3.py:24` constructs `boto3.client("s3")` with no credentials, relying on the ambient chain → the IAM instance role → the instance metadata service. IMDSv2's default **hop limit is 1**. Docker's bridge network adds a hop, so containers get `NoCredentialsError` and every screenshot upload fails. **The hop limit must be 2.**

**(b) S3 ACLs.** `common/utils/aws_s3.py:30` calls `put_object(..., ACL="private")`. Buckets created today default to Object Ownership = *Bucket owner enforced*, which **disables ACLs**. Per AWS docs: *"In your PUT operations, you must either specify bucket owner full control ACLs or not specify an ACL. Otherwise, your PUT operations fail"* — a 400 `AccessControlListNotSupported`. `private` is neither. Every Playwright error path would throw.

Chosen fix: create the bucket with **ACLs enabled (Bucket owner preferred)**. Zero code change, preserves current behavior exactly, bucket still blocks all public access.

Better long-term fix (deferred — not during a migration): delete the `ACL="private"` argument and keep AWS defaults. Note that `aws_s3.py:34` returns a plain `https://<bucket>.s3.<region>.amazonaws.com/<key>` URL for a *private* object, so `AutomationResult.screenshot_url` is a link that 403s for anyone who clicks it. That is pre-existing behavior; fixing it properly means presigned URLs.

---

## 5. Pre-deploy code changes

Small, staged as one commit before deployment.

| # | File | Change | Why |
|---|---|---|---|
| 1 | `docker-compose.yml:42` | `--concurrency=100` → `--concurrency=6` | Bounds Chromium count. **The core fix.** |
| 2 | `docker-compose.yml:41` | `--loglevel=debug` → `--loglevel=info` | debug across 13 queues will fill a 30 GB disk |
| 3 | `docker-compose.yml` | Add `logging: driver: json-file, max-size: 50m, max-file: 3` to all 3 services | Docker logs are unbounded by default |
| 4 | Host | 4 GB swap file | `task_acks_late=True` with **no Celery retries** means an OOM-kill loses a task *after* `deduct_wallet_balance` has committed |

Setting `WORKER_CONCURRENCY=6` in `.env` alone is **not** sufficient — the CLI `--concurrency` flag in `docker-compose.yml` overrides `celery_app.py:87`. Both are set for consistency.

---

## 6. Console runbook

### Step 0 — Set region
Console top-right → **US West (Oregon) us-west-2**. Every resource below must be created in this region.

### Step 1 — Key pair
EC2 → Network & Security → **Key Pairs** → Create key pair
- Name: `socialgamesclub-prod`
- Type: **ED25519**
- Format: **.pem**
- Create → the `.pem` downloads once. Then locally: `chmod 400 socialgamesclub-prod.pem`

### Step 2 — Security group
EC2 → Network & Security → **Security Groups** → Create security group
- Name: `socialgamesclub-sg`
- Description: `socialgamesclub automation API`
- VPC: **default**

Inbound rules:

| Type | Protocol | Port | Source | Note |
|---|---|---|---|---|
| SSH | TCP | 22 | **My IP** | your workstation |
| Custom TCP | TCP | 8000 | `<HOSTINGER_IP>/32` | Laravel only |

Outbound: leave the default `All traffic → 0.0.0.0/0`. Required for MySQL 3306 → Hostinger, 443 → casino panels / S3 / anti-captcha, 587 → SendGrid.

> **Do not open 8000 to `0.0.0.0/0`.** FastAPI serves `/docs` and `/openapi.json` **unauthenticated** (`api/main.py` sets no `docs_url=None`), which publishes the entire API surface. Traffic is plain HTTP by choice, so source restriction is the only control protecting `APP_KEY` and Sanctum tokens.

### Step 3 — S3 bucket
S3 → **Create bucket**
- Region: **us-west-2**
- Name: `casino-automation-screenshots` (globally unique; if taken, pick another and set `AWS_S3_BUCKET_NAME` in `.env` to match)
- **Object Ownership: ACLs enabled → Bucket owner preferred** ← required, see §4(b)
- Block Public Access: **leave all four boxes checked**
- Versioning: Disable
- Encryption: SSE-S3 (default)
- Create

Then bucket → **Management** → Lifecycle rules → Create rule
- Name: `expire-screenshots`
- Prefix: `screenshots/`
- Action: *Expire current versions of objects* → **90** days
- Action: *Delete expired object delete markers or incomplete multipart uploads* → incomplete MPU after **7** days

### Step 4 — IAM policy
IAM → **Policies** → Create policy → **JSON** tab:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PutScreenshots",
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::BUCKET_NAME_HERE/screenshots/*"
    }
  ]
}
```

Replace `BUCKET_NAME_HERE`. Name: `socialgamesclub-s3-screenshots` → Create.

### Step 5 — IAM role
IAM → **Roles** → Create role
- Trusted entity type: **AWS service**
- Use case: **EC2** → Next
- Permissions: check `socialgamesclub-s3-screenshots` → Next
- Name: `socialgamesclub-ec2-role` → Create

### Step 6 — Launch EC2
EC2 → Instances → **Launch instances**
- Name: `socialgamesclub-prod`
- AMI: **Ubuntu Server 24.04 LTS (HVM), SSD Volume Type** — architecture **64-bit (x86)**, *not* Arm
- Instance type: **t3.large**
- Key pair: `socialgamesclub-prod`
- Network settings → **Edit**:
  - VPC: default; Subnet: no preference
  - Auto-assign public IP: **Enable**
  - Firewall: **Select existing security group** → `socialgamesclub-sg`
- Configure storage: **30 GiB**, **gp3**
- **Advanced details** (expand):
  - IAM instance profile: **`socialgamesclub-ec2-role`**
  - Metadata version: **V2 only (token required)**
  - **Metadata response hop limit: `2`** ← required, see §4(a)
  - Credit specification: **Unlimited** (T3 default — prevents CPU throttling during browser bursts; watch for surcharge if sustained)
- **Launch instance**

### Step 7 — Elastic IP
EC2 → Network & Security → **Elastic IPs** → Allocate Elastic IP address
- Network border group: `us-west-2` → Allocate

Select it → **Actions → Associate Elastic IP address**
- Resource type: **Instance** → `socialgamesclub-prod` → Associate

**Record this IP.** It is the identity every external party whitelists.

### Step 8 — Hostinger
1. hPanel → **Databases → Remote MySQL** → add the Elastic IP. Do **not** select "Any host".
2. Collect: DB host, DB name, DB user, DB password.
3. Laravel `.env`: point the automation API base URL at `http://<ELASTIC_IP>:8000`.

### Step 9 — ECashApp
Ask the merchant administrator to whitelist the new Elastic IP. Keep the old one active until cutover is verified, then have it removed.

### Step 10 — Budget alarm (optional, recommended)
Billing → **Budgets** → Create budget → Cost budget → monthly **$80** → alert at 80% to your email.

> Do **not** terminate the old m5.2xlarge or release its Elastic IP until the new box is verified end-to-end. The old IP is whitelisted at ECashApp, Hostinger, and possibly casino panels.

---

## 7. `.env` contents

Lives at `/opt/socialgamesclub/.env` on the instance, `chmod 600`. Never committed (`.gitignore` covers `.env`).

```dotenv
# ─── App ───────────────────────────────────────────────────────────
# Must byte-match the APP_KEY Laravel sends in the `x-app-key` header.
APP_KEY=<same shared secret Laravel uses>
APP_ENV=production
DEBUG=False
HEADLESS=True

# ─── MySQL (Hostinger, shared with Laravel) ────────────────────────
# DB_PASS has no default; settings.py:13 calls .strip('"') on it, so an
# unset value raises AttributeError at import and nothing boots.
DB_HOST=<hostinger mysql host>
DB_PORT=3306
DB_USER=<hostinger db user>
DB_PASS=<hostinger db password>
DB_NAME=<hostinger db name>

# ─── Celery / Redis ────────────────────────────────────────────────
# `redis` is the docker-compose service name, NOT localhost.
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0
# Read by celery_app.py:87, but the --concurrency flag in
# docker-compose.yml overrides it. Both are set to 6.
WORKER_CONCURRENCY=6

# ─── AWS ───────────────────────────────────────────────────────────
# No AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY: credentials come from the
# EC2 instance role via IMDS (requires metadata hop limit = 2).
AWS_REGION=us-west-2
AWS_S3_BUCKET_NAME=casino-automation-screenshots

# ─── Anti-captcha ──────────────────────────────────────────────────
ANTICAPTCHA_API_KEY=<anti-captcha.com key>

# ─── Mail (SendGrid over SMTP) ─────────────────────────────────────
# AWS throttles port 25 only; 587 needs no special request.
# MAIL_ENCRYPTION must be exactly "tls" — emails.py:119 compares == "tls".
MAIL_HOST=smtp.sendgrid.net
MAIL_PORT=587
MAIL_USERNAME=apikey
MAIL_PASSWORD=<sendgrid api key>
MAIL_ENCRYPTION=tls
MAIL_FROM_ADDRESS=<verified sendgrid sender>
MAIL_FROM_NAME=Casino Automation
ACTIVATE_EMAILS=True

# ─── Webhook back to Laravel ───────────────────────────────────────
# No defaults in settings.py:56-57. Sent as `Authorization: Bearer <secret>`.
WEBHOOK_URL=https://<your-laravel-domain>/api/automation/webhook
WEBHOOK_SECRET=<shared webhook secret Laravel validates>

# ─── ECashApp gateway forwarder ────────────────────────────────────
ECASHAPP_BASE_URL=https://www.ggusonepay.com
ECASHAPP_FORWARD_TIMEOUT=15
```

Notes:
- `MAIL_RECIPIENT` is **hardcoded** at `settings.py:40` (3 Gmail addresses), not env-driven. Changing recipients is a code change.
- `MAX_CONTEXTS` / `MAX_PAGES` are intentionally omitted. `_CONTEXT_SEM` is dead code, and under prefork each child runs one task at a time so `PAGE_SEM` is never contended. Defaults are inert.
- Values to carry over verbatim from the old box's `.env`: `APP_KEY`, `WEBHOOK_SECRET`, `ANTICAPTCHA_API_KEY`, `MAIL_PASSWORD`, and all `DB_*`.

---

## 8. Deployment (after provisioning; key handed over)

```bash
ssh -i socialgamesclub-prod.pem ubuntu@<ELASTIC_IP>
```

1. `apt update && apt upgrade -y`
2. Install Docker Engine + compose plugin (official `get.docker.com`); `usermod -aG docker ubuntu`
3. Create 4 GB swap (`/swapfile`, `swapon`, persist in `/etc/fstab`, `vm.swappiness=10`)
4. Clone repo to `/opt/socialgamesclub`
5. Write `.env` (§7), `chmod 600`
6. Apply the §5 compose changes
7. `docker compose build && docker compose up -d`
8. `systemd` unit or `restart: unless-stopped` (already present) for boot survival

### Verification (evidence, not assumption)

| Check | Command / expectation |
|---|---|
| Containers up | `docker compose ps` → 3 up, web healthy |
| DB reachable | `docker compose exec web python -c "from db import engine; engine.connect(); print('ok')"` |
| Redis | `docker compose exec redis redis-cli ping` → `PONG` |
| API | `curl -s -o /dev/null -w '%{http_code}' localhost:8000/docs` → `200` |
| **IMDS through Docker** | `docker compose exec web python -c "import boto3;print(boto3.client('sts').get_caller_identity()['Arn'])"` → the role ARN. **Fails ⇒ hop limit is still 1.** |
| **S3 write** | `docker compose exec web python -c "import boto3;boto3.client('s3',region_name='us-west-2').put_object(Bucket='<bucket>',Key='screenshots/_probe',Body=b'x',ACL='private')"` → no error. `AccessControlListNotSupported` ⇒ bucket ACLs are disabled. |
| Chromium count bounded | `docker compose exec worker bash -c 'ps aux \| grep -c "[c]hrome"'` after traffic → bounded by 6, not climbing |
| Memory | `free -m` under load → well under 8 GB |
| Auth | `curl -X POST localhost:8000/automation/read-account -H 'x-app-key: <APP_KEY>' ...` → `scheduled` |
| End-to-end | One real `read-account` per backend; confirm `automation_results` row + webhook received by Laravel |

Cutover only after a real recharge succeeds end-to-end.

---

## 9. Cost (us-west-2, approximate — confirm in console)

| Line item | Before | After |
|---|---|---|
| Instance | m5.2xlarge ~$280.32 | t3.large ~$60.74 |
| EBS 30 GB gp3 | ~$2.40 | ~$2.40 |
| Public IPv4 (in use) | ~$3.65 | ~$3.65 |
| S3 + egress | ~$1 | ~$1 |
| **Monthly** | **~$287** | **~$68** |

**~76% reduction.** A 1-year Compute Savings Plan takes t3.large to ~$38/mo (~$45 all-in) if the box is permanent. T3 Unlimited may add a small surcharge if CPU sustains above the 30%-per-vCPU baseline; monitor `CPUCreditBalance` for the first week.

---

## 10. Known issues found during this study (out of scope; log separately)

Not blockers for the migration, but they are real and were confirmed in the code:

1. **`api/main.py:361-375`** — `/automation/freeplay`: if `req.type` matches none of the four branches, `count` stays `None` and `int(count)` raises `TypeError` → unhandled 500 instead of a 400.
2. **`api/main.py:235-246`** — `deduct_wallet_balance` commits *before* the Celery enqueue; `db_actions.py:604-615` swallows its own rollback exception without re-raising. A broker failure at `:147` debits a user with no task; the endpoint has no `restore_wallet_balance` failure path.
3. **`api/tasks.py:37-74`** — the `*/10` replenish beat has no in-flight lock and re-queries the same pool. A slow Playwright backend can stack several concurrent `create-account` runs (`task_time_limit=1800`).
4. **`api/main.py:444`** — `/ecashapp/forward` returns `resp.json()`, discarding the upstream status code (a gateway 400 becomes a 200) and raising an unhandled `JSONDecodeError` → 500 if the gateway returns an HTML WAF page.
5. **`common/utils/notify.py:25-26`** — webhooks fire on a `daemon=True` thread with no retry or queue; a worker exit mid-flight means Laravel silently never learns a recharge landed.
6. **`celery_app.py:89-90`** — `task_time_limit=1800` with a comment claiming 10 minutes. It's 30.
7. **Schema drift is silent** — no migrations here; a Laravel migration renaming a column breaks this service only at query time.
8. **`package.json` / `package-lock.json`** — 351 npm packages from a stray `npx shadcn` run, committed by accident in `ea1f6c7`. Dead weight; `.dockerignore` doesn't exclude `node_modules`, `.git`, or `backends/*/data|logs`, and `Dockerfile:19` is `COPY . .`.
