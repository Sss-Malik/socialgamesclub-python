# Deploy notes: yolo, cashfrenzy, cashmachine

Bringing backends 13, 14 and 15 live. The code is merged to `main`; **the code alone does nothing** — none of these three will work until the `backend_games` rows carry credentials and the worker is subscribed to the new queues.

Do the steps in order. Step 1 (database) is the one that actually gates function.

> **No credentials in this file.** Placeholders are written as `<YOLO_USERNAME>` etc. Fill them in at the DB, never here — `backends/*/config.py` ships empty `USERNAME`/`PASSWORD` constants deliberately, so the DB row is the only source.

---

## 0. What was added

| | yolo | cashfrenzy | cashmachine |
|---|---|---|---|
| `backend_games.id` | 13 | 14 | 15 |
| `backend_games.name` | `yolo` | `cashfrenzy` | `cashmachine` |
| Celery queue | `yolo` | `cashfrenzy` | `cashmachine` |
| Vendor host | `agent.yolo-777.com` | `agentserver.cashfrenzy777.com` | `agentserver.cashmachine777.com` |
| Auth | Laravel session cookie + CSRF | JWT bearer | JWT bearer |
| Account prefix | `userYL…` | `userCF…` | `userCM…` |

`name`, the queue name and `config.BACKEND_NAME` must be the **same string**. `id` must equal `config.BACKEND_ID`. A mismatch fails silently — tasks route to a queue nothing consumes, or logs attach to the wrong backend.

---

## 1. Database (required — do this first)

Laravel owns this schema; there are no migrations in this repo, so these are hand-run against the production MySQL (`social_gc_prod` on Hostinger).

### If the rows already exist

```sql
UPDATE backend_games SET
  name                 = 'yolo',
  api_base_url         = 'https://agent.yolo-777.com',
  backend_url          = 'https://agent.yolo-777.com/admin/auth/login',
  username             = '<YOLO_USERNAME>',
  password             = '<YOLO_PASSWORD>',
  accounts_creation_pd = '5',
  deleted_at           = NULL
WHERE id = 13;

UPDATE backend_games SET
  name                 = 'cashfrenzy',
  api_base_url         = 'https://agentserver.cashfrenzy777.com',
  backend_url          = 'https://agentserver.cashfrenzy777.com/admin/login',
  username             = '<CASHFRENZY_USERNAME>',
  password             = '<CASHFRENZY_PASSWORD>',
  accounts_creation_pd = '5',
  deleted_at           = NULL
WHERE id = 14;

UPDATE backend_games SET
  name                 = 'cashmachine',
  api_base_url         = 'https://agentserver.cashmachine777.com',
  backend_url          = 'https://agentserver.cashmachine777.com/admin/login',
  username             = '<CASHMACHINE_USERNAME>',
  password             = '<CASHMACHINE_PASSWORD>',
  accounts_creation_pd = '5',
  deleted_at           = NULL
WHERE id = 15;
```

### If they don't exist

Same values as an `INSERT`, with `id` set explicitly — it is **not** auto-assignable, because `config.BACKEND_ID` is hardcoded and used as the `backend_id` foreign key on every log and automation result:

```sql
INSERT INTO backend_games
  (id, name, api_base_url, backend_url, username, password, accounts_creation_pd, created_at, updated_at)
VALUES
  (13, 'yolo',        'https://agent.yolo-777.com',                  'https://agent.yolo-777.com/admin/auth/login',            '<YOLO_USERNAME>',        '<YOLO_PASSWORD>',        '5', NOW(), NOW()),
  (14, 'cashfrenzy',  'https://agentserver.cashfrenzy777.com',       'https://agentserver.cashfrenzy777.com/admin/login',      '<CASHFRENZY_USERNAME>',  '<CASHFRENZY_PASSWORD>',  '5', NOW(), NOW()),
  (15, 'cashmachine', 'https://agentserver.cashmachine777.com',      'https://agentserver.cashmachine777.com/admin/login',     '<CASHMACHINE_USERNAME>', '<CASHMACHINE_PASSWORD>', '5', NOW(), NOW());
```

### Column notes

| Column | Value | Why |
|---|---|---|
| `username` / `password` | **required** | The agent-portal login. Empty ⇒ `ValueError: <N>Client requires base_url, username, and password.` on first use. This is deliberate fail-fast, not a bug. |
| `api_base_url` | **required** | Host only, no trailing slash, no path. The client appends `/api/...` or `/admin/...` itself. |
| `backend_url` | recommended | cashmachine derives its API base from this if `api_base_url` is empty. Harmless for the other two. |
| `accounts_creation_pd` | **required** | How many accounts one `create-account` run makes. Cast with `int()` — a non-numeric or NULL value raises. `5` matches existing backends; tune to taste. |
| `api_agent_id` / `api_secret_key` | leave NULL | Only juwa/juwa2/gamevault use that scheme. These three authenticate by username/password. |
| `binding_key` | leave NULL | 2FA seed, Playwright backends only. |
| `deleted_at` | must be NULL | `get_backend()` filters on it; a non-NULL value makes the backend invisible and every task fails with an `AttributeError` on `None`. |

### Verify the rows

```sql
SELECT id, name, api_base_url,
       username <> '' AS has_user,
       password <> '' AS has_pass,
       accounts_creation_pd, deleted_at
FROM backend_games WHERE id IN (13,14,15);
```

All three must show `has_user = 1`, `has_pass = 1` and `deleted_at = NULL`.

---

## 2. Vendor IP whitelisting (do before, or first traffic fails)

All egress leaves from the single production Elastic IP — there are no proxies anywhere in this codebase. These are three brand-new outbound destinations.

Some vendors authenticate partly by source IP: juwa, juwa2 and gamevault all required the IP to be whitelisted per-operator. Whether these three do is **unknown until tested**. Ask each operator to whitelist the production Elastic IP.

The failures do not describe themselves — see step 4 for how to tell them apart.

---

## 3. Deploy

The compose file bind-mounts the repo (`.:/app`), so code is live-mounted — but the Python processes only pick up changes on restart, and the `-Q` change **requires a worker restart specifically**, since queue subscriptions are set at worker startup.

```bash
ssh -i socialgamesclub-prod.pem ubuntu@<ELASTIC_IP>
cd /opt/socialgamesclub

git pull                     # or rsync from a laptop if the deploy key isn't on the repo
docker compose restart       # web + worker; worker picks up the 3 new queues
docker compose ps            # 3 up, web healthy
```

No rebuild is needed — this change added no dependencies. `docker compose build` is only necessary if `requirements.txt` changed, which it did not.

### Confirm the worker subscribed

```bash
docker compose exec worker celery -A celery_app.celery_app inspect active_queues \
  | grep -E "yolo|cashfrenzy|cashmachine"
```

All three must appear. If they don't, the worker is running old code — check that `docker-compose.yml`'s `-Q` list contains 16 entries (`default` + 15 backends) and restart again.

---

## 4. Verify each backend end to end

`read-backend` is the safest live probe: it only reads the agent balance, moves no money, and exercises the full auth path (login → session persistence → authenticated request).

```bash
for b in yolo cashfrenzy cashmachine; do
  echo "--- $b ---"
  curl -s -X POST http://localhost:8000/automation/read-backend \
    -H "x-app-key: $APP_KEY" -H "Content-Type: application/json" \
    -d "{\"backend\": \"$b\"}"
  echo
done
```

Each returns `{"status":"scheduled","task_id":"…"}` immediately — that only means it enqueued. The real result lands in the database:

```sql
SELECT ar.task_id, bg.name, ar.status, ar.description, ar.duration_seconds, ar.created_at
FROM automation_results ar JOIN backend_games bg ON bg.id = ar.backend_id
WHERE ar.backend_id IN (13,14,15)
ORDER BY ar.created_at DESC LIMIT 10;
```

Want `status = 'success'`. Then confirm a balance was actually written:

```sql
SELECT backend_id, remaining_balance, updated_at FROM backend_balances WHERE backend_id IN (13,14,15);
```

Follow with `create-account` on one backend once `read-backend` is green, and check `backend_accounts` for a new `userYL…` / `userCF…` / `userCM…` row.

---

## 5. Interpreting failures

| Symptom | Meaning |
|---|---|
| `ValueError: …Client requires base_url, username, and password` | The `backend_games` row has an empty `username` or `password`. Step 1 was skipped or incomplete. |
| `AttributeError: 'NoneType' object has no attribute 'id'` | `get_backend()` found no row — wrong `name`, or `deleted_at` is set. |
| Task enqueues, `automation_results` stays `pending` forever | Worker isn't consuming that queue. Re-check step 3. |
| yolo: `login failed (HTTP 200)` or `could not find _token on login page` | Bad credentials, or the vendor changed the Dcat login page. |
| cashfrenzy / cashmachine: `status_code=401` / `410` on login | Bad credentials. (A 410 on a *later* call is normal token expiry and self-heals — the client re-logs in and retries once.) |
| Any of them: connection timeouts or auth errors that don't match the above | Suspect IP whitelisting. Confirm by curling the vendor root from the box; if the host answers but the API rejects you, it's whitelisting. |
| `502 Bad Gateway` from a vendor host | Usually the vendor's own origin being down, not us. Compare against another vendor before escalating. |

### Log locations

```bash
docker compose logs --tail=200 worker | grep -Ei "yolo|cashfrenzy|cashmachine"
```

Per-backend file logs are under `backends/<name>/logs/` inside the container. Application-level logs also land in the `logs` table, keyed by `backend_id` and `task_id`.

---

## 6. Rollback

The three backends are additive — no existing backend's behaviour changed, and no shared code was modified. To disable one without a deploy:

```sql
UPDATE backend_games SET deleted_at = NOW() WHERE id = 15;   -- example: cashmachine
```

That makes `get_backend()` skip it; any queued task then fails fast instead of hitting the vendor. To take all three out, set `deleted_at` on 13, 14 and 15.

A full code rollback is `git revert` of the merge commit plus `docker compose restart`, but that is rarely the right lever — the DB flag above is faster and reversible.

---

## 7. Known follow-ups

Not blockers for this deploy, tracked separately:

- `process_recharge_operation` in `common/utils/db_actions.py` can double-refund if a backend account is soft-deleted mid-recharge: the refund commits, then `db.refresh(None)` raises into the backend's `except`, which refunds again. Affects all 15 backends, pre-existing.
- `backends/river/utils/credentials.py` raises `NameError` unconditionally (its guard reads `account_id` before assignment). Currently harmless — the function is imported but never called, as river reads account names from the vendor's own dialog. It would detonate if anyone wires it up.
- `backends/goldentreasure/config.py` still has a live password committed. The three new backends deliberately do not.
