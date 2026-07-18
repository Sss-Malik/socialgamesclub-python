# Integrating yolo, cashfrenzy and cashmachine

**Date:** 2026-07-18
**Status:** Approved, ready for implementation
**Source:** `/Applications/development/python/casino_automation` (branch at `0aaf98b`)

## Goal

Add three HTTP-API backends — `yolo` (id 13), `cashfrenzy` (id 14), `cashmachine` (id 15) — bringing this service from 12 to 15 backends. The implementations already exist and run in a parallel project; this is a port, not new development.

## Why a port and not a rewrite

The three backends were built from the same template as `goldentreasure`, this repo's cleanest backend. All eight action names and signatures match exactly:

```
action_create_account, action_create_account_user, action_read_backend,
action_read_account, action_recharge_account, action_withdraw_account,
action_freeplay_account, action_reset_password
```

They are pure HTTP — no Playwright, no dead `utils/actions.py`, no selector constants. Their entire shared-code dependency surface already exists here with byte-identical implementations:

| Helper | Module |
|---|---|
| `create_backend_session`, `get_latest_valid_session`, `invalidate_latest_session` | `common/utils/db_actions.py` |
| `acquire_login_lock`, `release_login_lock` | `common/utils/redis_utils.py` |
| `wait_for_valid_session` | `common/utils/poll_utils.py` |

No new dependencies: `requests`, `urllib3` and `redis` are already pinned in `requirements.txt`.

The vendor-specific logic (yolo's Dcat `_payload_` form encoding, its pagination bounds, the CSRF-419 retry, the HTML grid parsing) was reverse-engineered against live systems and is running in production on the source project. Rewriting it would risk new bugs for no benefit. So: copy verbatim, then apply a small auditable patch.

The ported clients are also better than the five HTTP clients already here. All three set `allowed_methods=frozenset(["GET"])` on their urllib3 retry policy, with an explicit comment about double-apply on mutating POSTs — the hazard the existing clients still carry.

## Architecture

Two auth families, neither new to this codebase.

| Backend | ID | Vendor stack | Auth | Session persistence |
|---|---|---|---|---|
| `cashfrenzy` | 14 | Laravel + JWT | Bearer JWT; branch on body `status_code`, not HTTP status. Dead token = 401/410 | `backend_sessions` + Redis login lock |
| `cashmachine` | 15 | Same layui panel as `gameroom` | Bearer JWT; dead token = `status_code` 410 | `backend_sessions` + Redis login lock |
| `yolo` | 13 | Laravel 7 + Dcat Admin | Laravel session cookie jar (`yolo_session`) + lazily-fetched CSRF `Dcat.token` | Whole cookie jar JSON-serialised into `backend_sessions.token` |

`cashfrenzy` and `cashmachine` are near-clones of the existing `gameroom` client.

`yolo` is genuinely different. Dcat Admin exposes no JSON player-lookup, so the client resolves players by scraping the `player_list` HTML grid with regex over `<tbody>`. It prefers an exact by-Player-ID filter (`find_by_game_id`) and falls back to a paginated by-account search bounded at 15 pages, because the Accounts filter is a partial match — a short username can be buried behind longer-named siblings on a later page. Its CSRF token is not persisted; it is re-fetchable from any admin page and cached on the instance.

## Files

12 files copied verbatim from the source, then patched:

```
backends/yolo/{__init__.py,config.py,api_client.py,automation.py}
backends/yolo/utils/{__init__.py,credentials.py}
backends/cashfrenzy/{__init__.py,config.py,api_client.py,automation.py}
backends/cashfrenzy/utils/{__init__.py,credentials.py}
backends/cashmachine/{__init__.py,config.py,api_client.py,automation.py}
backends/cashmachine/utils/credentials.py          (+ new utils/__init__.py)
```

`backends/cashmachine/utils/__init__.py` does not exist in the source. It still imports there — Python 3 resolves `utils` as a PEP 420 namespace package — but it should be created here for consistency with `yolo`, `cashfrenzy` and the nine other backends that all ship one. This is tidiness, not a fix.

## The patch

The source project has a `LeaderboardReward` feature this project does not have. Every ported `automation.py` is coupled to it and would raise `TypeError` on its recharge failure path. Severing that coupling is the bulk of the patch.

Per `automation.py`:

1. Drop `leaderboard_reward_id=None` from the `action_recharge_account` signature.
2. Drop `restore_leaderboard_reward=True, leaderboard_reward_id=leaderboard_reward_id,` from `process_recharge_operation(...)` — two call sites per file.
3. `restore_wallet_balance(wallet_id, amount_to_deduct, order_id, coupon_code, leaderboard_reward_id)` → drop the fifth argument. This project's signature takes four.
4. Drop the `backend_id=BACKEND_ID` keyword from `update_game_id_by_username(...)` and `update_password_by_username(...)`. This project's versions do not accept it. Sites: yolo 2, cashfrenzy 2, cashmachine 1.

Per `config.py`:

5. Blank the `USERNAME` / `PASSWORD` constants in `yolo` and `cashfrenzy` (they carry live agent credentials in the source). `cashmachine` already ships empty fallbacks. Credentials come from the `backend_games` row via `build_client_from_backend`, which already prefers `backend.username` / `backend.password`.

## Wiring

Three edits, following the `goldentreasure` precedent (commit `205024e`):

- `celery_app.py` — add three `Queue(...)` entries to `task_queues`.
- `celery_app.py` — add three `"backends.<n>.automation.action_*": {"queue": "<n>"}` entries to `task_routes`.
- `docker-compose.yml` — append `yolo,cashfrenzy,cashmachine` to the worker `-Q` list.

## Database

The `backend_games` rows already exist with ids 13, 14 and 15 matching each `config.BACKEND_ID`. Laravel owns this schema; there are no migrations in this repo.

Each row must carry `api_base_url`, `username`, `password` and `accounts_creation_pd`, and have `deleted_at` NULL. `get_backend()` looks the backend up by `name`, and the Celery queue name is the backend name, so `backend_games.name` must equal `config.BACKEND_NAME` exactly.

Because credentials now come from the DB (decision 5 above), a row with an empty `username` or `password` will fail fast at client construction with `ValueError: <N>Client requires base_url, username, and password.` This is intentional — better than silently authenticating as nobody.

## Testing

The live vendors are unreachable from a development machine: the three HTTP backends that IP-check are whitelisted against the production Elastic IP only, and the credentials live in the production DB. Verification is therefore static and contract-level.

**1. Dispatch contract test** (ported from source `tests/test_dispatch_contract.py`, adapted to drop `leaderboard_reward_id` from the payload).

Globs every `backends/*/automation*.py`, imports it, and asserts `action_recharge_account` can `bind_partial` the exact kwarg shape the dispatcher sends. Uses `bind_partial` rather than `bind` so the Playwright backends' decorator-injected `page` parameter is not a false positive. Covers all 15 backends, and any future one, with no hardcoded list. Includes a guard asserting discovery found a plausible number of modules, so a broken glob cannot masquerade as a passing run.

**2. Recharge failure-path test** for the three new backends — the test that actually guards this change.

The contract test cannot catch the risk this port introduces: all three actions use a `**_` catch-all and will bind anything. The real hazard is a `TypeError` raised *inside* the function body on the failure path, which is exactly where the wallet refund lives. A missed leaderboard call site would strand a customer's money and surface only in production, on a failed recharge.

So: drive each new `action_recharge_account` with a stubbed client that raises, and assert `restore_wallet_balance` is called with this project's exact four-argument signature. This follows the pattern already established in `tests/test_recharge_endpoint.py` and `tests/test_wallet_deduction.py`.

The existing 12 tests must continue to pass.

## Accepted risk

Patch item 4 removes a `backend_id` argument the source project deliberately added. Its `update_password_by_username` uses `.one_or_none()`, which raises `MultipleResultsFound` if two backends hold an account with the same username; the `backend_id` filter disambiguates.

Dropping it restores this repository's existing behaviour. It is safe here because usernames are generated with a per-backend signature (`userYL*`, `userCF*`, `userCM*`), making cross-backend collision effectively impossible, and all 12 existing backends already operate under this constraint.

Adding the optional parameter to `db_actions` would be a shared-code change affecting all 15 backends. That is deliberately out of scope: this integration keeps its blast radius at zero shared-code modifications. Recorded here so the difference is known rather than rediscovered.

## Out of scope

- Porting `LeaderboardReward` (model, `_restore_leaderboard_reward`, `db_actions` changes, the `api/main.py` ownership check).
- The `create_account_user` gap on the 10 pre-existing backends.
- The recharge money hazards in the five existing HTTP clients (POST retry replay, non-idempotent refunds).
- Any modification to the 12 existing backends.
