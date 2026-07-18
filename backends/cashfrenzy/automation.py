"""
CashFrenzy backend: direct HTTP API implementation.

A pure-HTTP backend (no browser) for agentserver.cashfrenzy777.com —
the same vendor software as gameroom (Laravel + stateless JWT). Preserves
the same DB side effects, email sends, and result/description strings as
the other backends so callers (Laravel etc.) need no changes.

See /Applications/development/cashfrenzy-standalone/api_findings.md for
the full endpoint spec and verification log.
"""

import json
import logging

from common.utils.emails import send_email
from common.utils.logger import get_backend_logger
from common.utils.ensure_directories import ensure_directories
from common.utils.save_credentials import save_credentials

from backends.cashfrenzy.config import (
    BACKEND_NAME,
    BACKEND_ID,
    DATA_DIR,
    LOGS_DIR,
    CAPTCHA_DIR,
    RECHARGE_ELIGIBLE_THRESHOLD,
    FREEPLAY_ELIGIBLE_THRESHOLD,
)
from backends.cashfrenzy.utils.credentials import generate_credentials
from backends.cashfrenzy.api_client import (
    CashFrenzyAPIError,
    build_client_from_backend,
)

from common.utils.db_actions import (
    get_backend,
    get_backend_and_account,
    insert_backend_account,
    insert_log,
    insert_log_and_update_automation_result,
    update_automation_result,
    update_backend_balance,
    update_game_id_by_username,
    set_game_id_if_null,
    update_password_by_username,
    restore_wallet_balance,
    process_recharge_operation,
    process_freeplay_operation,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OK = 200

CREATE_ACCOUNT_MAX_RETRIES = 20

# Paginated by-account search bounds (by-ID search is ignored server-side, so
# we resolve uncached players by paginating the by-account search).
_SEARCH_LIMIT = 50
_SEARCH_MAX_PAGES = 10

# Substring markers in server messages (spec §6, §7, §9).
_CREATE_EXISTS = "username already exists"
_RECHARGE_INSUFFICIENT = "recharge balance is greater than available balance"
_REDEEM_INSUFFICIENT = "withdrawal amount is greater than customer balance"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_player_row(backend_account, client, logger: logging.Logger):
    """Return the full userList row for this BackendAccount via the by-account
    search, paginating to an exact Account match.

    The by-account search is a partial match, so a short username can be
    buried behind longer siblings on a later page — hence the pagination.
    (The by-ID search is ignored by this deployment, so it is not used.)
    """
    target = backend_account.username.strip().lower()
    for page in range(1, _SEARCH_MAX_PAGES + 1):
        sc, msg, body = client.user_list(
            search_content=backend_account.username, page=page, limit=_SEARCH_LIMIT,
        )
        if sc != _OK:
            raise CashFrenzyAPIError(sc, f"userList failed: {msg}")
        rows = body.get("data") or []
        for row in rows:
            if str(row.get("Account") or "").strip().lower() == target:
                return row
        if len(rows) < _SEARCH_LIMIT:
            break  # last page reached
    return None


def _resolve_player_id(backend_account, client, logger: logging.Logger) -> str:
    """Return the cashfrenzy Game ID for this BackendAccount.

    Prefers the cached BackendAccount.game_id (used directly — no search);
    falls back to the paginated by-account lookup and backfills game_id.
    """
    if backend_account.game_id:
        return str(backend_account.game_id)

    logger.info("game_id missing for %s; resolving via userList", backend_account.username)
    row = _find_player_row(backend_account, client, logger)
    if not row:
        raise CashFrenzyAPIError(
            400, f"Player not found in userList for username {backend_account.username}",
        )
    player_id = str(row.get("id") or row.get("Id"))
    set_game_id_if_null(backend_account.username, player_id)
    return player_id


def _fetch_balances(player_id: str, client) -> tuple:
    """Return (player_balance_num, agent_balance_str) via /agentMoney."""
    sc, msg, body = client.agent_money(player_id)
    if sc != _OK:
        raise CashFrenzyAPIError(sc, msg)
    data = body.get("data") or {}
    player_balance = data.get("balance", 0)
    # NOTE: the misspelled `cusBlance` is part of the server contract.
    agent_balance = data.get("cusBlance", "0")
    try:
        player_balance_num = float(player_balance)
    except (TypeError, ValueError):
        player_balance_num = 0.0
    return player_balance_num, agent_balance


def _extract_agent_money(body, fallback=None):
    """Agent balance from /agent/getMoney (data is the balance string)."""
    data = body.get("data")
    if isinstance(data, (int, float, str)) and data != "":
        return data
    if isinstance(data, dict):
        for k in ("money", "balance", "cusBlance"):
            v = data.get(k)
            if v is not None:
                return v
    direct = body.get("money")
    if direct is not None:
        return direct
    return fallback


def _create_single_account(client, logger: logging.Logger, task_id, *, user_id=None) -> None:
    """Create one player via /playerInsert with retry on duplicate."""
    for attempt in range(CREATE_ACCOUNT_MAX_RETRIES):
        account_id, password = generate_credentials()
        logger.debug("Generated credentials: %s", account_id)

        sc, msg, body = client.player_insert(
            username=account_id, password=password, money=0, nickname=account_id,
        )

        if sc == _OK:
            data = body.get("data") or {}
            player_id = data.get("id")
            save_credentials(account_id, password, logger, DATA_DIR)
            insert_backend_account(
                username=account_id,
                password=password,
                backend_id=BACKEND_ID,
                user_id=user_id,
                is_assigned=bool(user_id),
                game_id=player_id,
            )
            logger.info("Account created successfully: %s (player_id=%s)", account_id, player_id)
            return

        if _CREATE_EXISTS in (msg or "").lower():
            logger.warning(
                "Account %s already exists (attempt %d/%d); regenerating.",
                account_id, attempt + 1, CREATE_ACCOUNT_MAX_RETRIES,
            )
            continue

        insert_log(
            "warning",
            f"Unexpected create account response: ({sc}) {msg}",
            source_url=None, backend_id=BACKEND_ID, task_id=task_id,
        )
        raise CashFrenzyAPIError(sc, msg)

    raise Exception(
        f"Create-account exhausted {CREATE_ACCOUNT_MAX_RETRIES} retries on 'Username already exists'."
    )


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def action_create_account(task_id, backend, **_):
    backend_game = get_backend(BACKEND_NAME)
    count = int(backend_game.accounts_creation_pd)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Create-account action started for %d accounts.", count)

    client = build_client_from_backend(backend_game, logger)
    try:
        insert_log(
            "info",
            f"Initiating account creation for backend '{BACKEND_NAME}' with count {count}.",
            source_url=None, backend_id=BACKEND_ID, task_id=task_id,
        )

        client.ensure_token()

        for i in range(count):
            logger.info("Creating account %d of %d", i + 1, count)
            _create_single_account(client, logger, task_id, user_id=None)

        update_automation_result(
            task_id=task_id, status="success", description="Account creation successful.",
        )

    except Exception as e:
        logger.critical("Error during account creation: %s", e, exc_info=True)
        send_email(
            subject="Account creation failed",
            body=f"Critical error occurred during account creation for backend '{BACKEND_NAME}'. Please review",
        )
        insert_log_and_update_automation_result(
            log_type="error",
            log_description=f"Error during account creation: {e}",
            task_id=task_id, source_url=None, backend_id=backend_game.id,
            result_status="failed",
            result_description=f"Error during account creation: {e}",
            screenshot_url=None,
        )
    finally:
        logger.info("Create-account action completed.")
        insert_log(
            "info", "Create account action completed",
            source_url=None, backend_id=backend_game.id, task_id=task_id,
        )


def action_create_account_user(task_id, backend, user_id, **_):
    backend_game = get_backend(BACKEND_NAME)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Create-account action started for individual account")

    client = build_client_from_backend(backend_game, logger)
    try:
        insert_log(
            "info",
            f"Initiating individual account creation for backend '{BACKEND_NAME}'",
            source_url=None, backend_id=BACKEND_ID, task_id=task_id,
        )

        client.ensure_token()

        logger.info("Creating account")
        _create_single_account(client, logger, task_id, user_id=user_id)

        update_automation_result(
            task_id=task_id, status="success", description="Account creation successful.",
        )

    except Exception as e:
        logger.critical("Error during account creation: %s", e, exc_info=True)
        send_email(
            subject="Account creation failed",
            body=f"Critical error occurred during account creation for backend '{BACKEND_NAME}'. Please review",
        )
        insert_log_and_update_automation_result(
            log_type="error",
            log_description=f"Error during account creation: {e}",
            task_id=task_id, source_url=None, backend_id=backend_game.id,
            result_status="failed",
            result_description=f"Error during account creation: {e}",
            screenshot_url=None,
        )
    finally:
        logger.info("Create-account action completed.")
        insert_log(
            "info", "Create account action completed",
            source_url=None, backend_id=backend_game.id, task_id=task_id,
        )


def action_read_backend(task_id, backend, **_):
    backend_game = get_backend(BACKEND_NAME)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("read-backend action started")

    client = build_client_from_backend(backend_game, logger)

    try:
        insert_log(
            "info",
            f"Initiating backend balance read for {BACKEND_NAME}",
            source_url=None, backend_id=backend_game.id, task_id=task_id,
        )

        sc, msg, body = client.agent_balance()
        if sc != _OK:
            raise CashFrenzyAPIError(sc, msg)

        balance_value = _extract_agent_money(body, fallback=client.last_login_money)
        if balance_value is None:
            raise CashFrenzyAPIError(sc, "Could not extract agent money from response")

        logger.info("Agent balance: %s", balance_value)
        update_backend_balance(backend_id=backend_game.id, backend_balance=balance_value)
        update_automation_result(
            task_id=task_id, status="success",
            description="Backend balance read successful.",
            data=json.dumps({"balance": balance_value}),
        )

    except Exception as e:
        logger.critical("Error during backend balance read: %s", e, exc_info=True)
        send_email(
            subject="Backend balance read failed",
            body=f"Critical error occurred during backend balance read on '{BACKEND_NAME}'. Please review",
        )
        insert_log_and_update_automation_result(
            log_type="error",
            log_description=f"Error during backend balance read: {e}",
            task_id=task_id, source_url=None, backend_id=backend_game.id,
            result_status="failed",
            result_description=f"Error during backend balance read: {e}",
            screenshot_url=None,
        )
    finally:
        logger.info("Read-backend action completed.")
        insert_log(
            "info", "Read backend action completed",
            source_url=None, backend_id=backend_game.id, task_id=task_id,
        )


def action_read_account(account_id: str, task_id, backend, **_):
    backend_game, backend_account = get_backend_and_account(backend, account_id)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Read-account action started: account_id=%s", account_id)

    client = build_client_from_backend(backend_game, logger)

    try:
        insert_log(
            "info",
            f"Initiating read for account ID {account_id} on backend '{BACKEND_NAME}'",
            source_url=None, backend_id=backend_game.id,
            account_id=backend_account.id, task_id=task_id,
        )

        client.ensure_token()

        if backend_account.game_id:
            # Fast path: read the balance directly by Game ID via agentMoney.
            # Avoids the by-account partial search, which is slow (and can time
            # out) for a username with many longer-named siblings, e.g.
            # `userCF766` matching every `userCF766*` account.
            player_id = str(backend_account.game_id)
            sc, msg, body = client.agent_money(player_id)
            if sc != _OK:
                raise CashFrenzyAPIError(sc, msg)
            data = body.get("data") or {}
            balance = data.get("balance")
            out = {
                "id": player_id,
                "account": str(data.get("username") or account_id),
                "nickname": "",
                "balance": str(balance) if balance is not None else "",
                "created_at": "",
                "login_count": "",
                "last_login": "",
                "last_login_ip": "",
            }
        else:
            # Uncached: resolve via the paginated by-account search (exact match)
            # and backfill game_id so subsequent reads take the fast path above.
            row = _find_player_row(backend_account, client, logger)
            if not row:
                raise CashFrenzyAPIError(400, f"Player not found in userList for account {account_id}")

            backend_account_id = row.get("id") or row.get("Id")
            out = {
                "id": str(backend_account_id) if backend_account_id is not None else "",
                "account": str(row.get("Account") or ""),
                "nickname": str(row.get("nickname") or ""),
                "balance": str(row.get("score") if row.get("score") is not None else ""),
                "created_at": str(row.get("AddDate") or ""),
                "login_count": str(row.get("LoginCount") if row.get("LoginCount") is not None else ""),
                "last_login": str(row.get("lasttime") or ""),
                "last_login_ip": str(row.get("loginip") or ""),
            }

            if backend_account_id is not None:
                update_game_id_by_username(account_id, backend_account_id, backend_id=BACKEND_ID)

        update_automation_result(
            task_id=task_id, status="success", description="Account information",
            data=json.dumps(out),
        )
        logger.info("Account read data: %s", out)

    except Exception as e:
        logger.critical("Error during account read: %s", e, exc_info=True)
        send_email(
            subject="Account read failed",
            body=f"Critical error occurred during reading account {account_id} on backend '{BACKEND_NAME}'. Please review",
        )
        insert_log_and_update_automation_result(
            log_type="error",
            log_description=f"Error during account read: {e}",
            task_id=task_id, source_url=None, backend_id=backend_game.id,
            result_status="failed",
            result_description=f"Error during account read: {e}",
            screenshot_url=None, account_id=backend_account.id,
        )
    finally:
        logger.info("Read-account action completed.")
        insert_log(
            "info", "Read account action completed",
            source_url=None, backend_id=BACKEND_ID,
            account_id=backend_account.id, task_id=task_id,
        )


def action_recharge_account(
    count: int, account_id: str, order_id, task_id, backend,
    wallet_id, amount_to_deduct, coupon_code=None, **_,
):
    backend_game, backend_account = get_backend_and_account(backend, account_id)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Recharge-account action started: account_id=%s, count=%d", account_id, count)

    client = build_client_from_backend(backend_game, logger)

    try:
        insert_log(
            "info",
            f"Initiating recharge for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=None, backend_id=backend_game.id,
            account_id=backend_account.id, task_id=task_id,
        )

        client.ensure_token()

        player_id = _resolve_player_id(backend_account, client, logger)
        player_balance_num, agent_balance = _fetch_balances(player_id, client)
        logger.info("Available balance: %s", player_balance_num)

        if player_balance_num > RECHARGE_ELIGIBLE_THRESHOLD:
            logger.info("Available balance is not recharge eligible. Aborting")
            process_recharge_operation(
                order_id=order_id, task_id=task_id, account_id=backend_account.id,
                backend_id=BACKEND_ID, page_url=None,
                log_data={
                    "type": "warning",
                    "description": f"Customer balance ineligible for recharge: {player_balance_num}",
                },
                order_status="failed", automation_status="failed",
                automation_result_fields={
                    "status": "failed", "description": "Customer balance ineligible for recharge",
                },
                wallet_status="failed", restore_wallet=True,
                amount_to_restore=amount_to_deduct, wallet_id=wallet_id,
                bonus_transferred=False, restore_coupon=True, coupon_code=coupon_code,
            )
            return

        sc, msg, _body = client.agent_recharge(
            player_id=player_id, balance=int(count), available_balance=agent_balance, remark="recharge",
        )

        if sc == _OK:
            logger.info("Recharge successful.")
            bonus_transferred = bool(backend_account.user and backend_account.user.bonus_received)
            process_recharge_operation(
                order_id=order_id, task_id=task_id, account_id=backend_account.id,
                backend_id=BACKEND_ID, page_url=None,
                log_data={"type": "info", "description": f"Recharge successful for account: {account_id}"},
                order_status="finished", automation_status="finished",
                automation_result_fields={
                    "status": "success", "description": f"Recharge successful for account: {account_id}",
                },
                wallet_status="finished", restore_wallet=False, amount_to_restore=None,
                wallet_id=None, bonus_transferred=bonus_transferred,
                restore_coupon=False, coupon_code=coupon_code,
            )
            return

        # --- failure branches (wallet + coupon restore) ---
        if _RECHARGE_INSUFFICIENT in (msg or "").lower():
            logger.error("Recharge failed: backend balance insufficient.")
            send_email(
                subject="Recharge failed",
                body=f"Recharge failed for account: {account_id} because of insufficient balance on {BACKEND_NAME}.",
            )
            description = f"Backend balance insufficient for {BACKEND_NAME} - Wallet balance restored"
        else:
            logger.warning("Unexpected recharge response: (%s) %s", sc, msg)
            description = f"Recharge failed on {BACKEND_NAME}: ({sc}) {msg} - Wallet balance restored"

        process_recharge_operation(
            order_id=order_id, task_id=task_id, account_id=backend_account.id,
            backend_id=BACKEND_ID, page_url=None,
            log_data={"type": "warning", "description": description},
            order_status="failed", automation_status="failed",
            automation_result_fields={"status": "failed", "description": description},
            wallet_status="failed", restore_wallet=True,
            amount_to_restore=amount_to_deduct, wallet_id=wallet_id,
            bonus_transferred=False, restore_coupon=True, coupon_code=coupon_code,
        )
        logger.info("Wallet balance restored")

    except Exception as e:
        restore_wallet_balance(wallet_id, amount_to_deduct, order_id, coupon_code)
        insert_log(
            "info", "Critical error during account recharge - Wallet balance restored",
            source_url=None, backend_id=backend_game.id,
            account_id=backend_account.id, task_id=task_id,
        )
        logger.critical("Error during account recharge: %s", e, exc_info=True)
        send_email(
            subject="Account recharge failed",
            body=f"Critical error occurred during account recharge for account ID {account_id} on backend '{BACKEND_NAME}'. Please review",
        )
        insert_log_and_update_automation_result(
            log_type="error",
            log_description=f"WALLET_RESTORED - Error during account recharge: {e}",
            task_id=task_id, source_url=None, backend_id=backend_game.id,
            result_status="failed",
            result_description=f"WALLET_RESTORED - Error during account recharge: {e}",
            screenshot_url=None, account_id=backend_account.id,
        )
    finally:
        logger.info("Recharge-account action completed.")
        insert_log(
            "info", "Recharge account action completed",
            source_url=None, backend_id=backend_game.id,
            account_id=backend_account.id, task_id=task_id,
        )


def action_withdraw_account(
    count: int, account_id: str, task_id, backend,
    redeem_request_id, order_id, requested_amount, **_,
):
    """CashFrenzy calls this 'redeem'; our system's external name is 'withdraw'."""
    backend_game, backend_account = get_backend_and_account(backend, account_id)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Withdraw-account action started: account_id=%s, count=%d", account_id, count)

    client = build_client_from_backend(backend_game, logger)

    try:
        insert_log(
            "info",
            f"Initiating withdrawal for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=None, backend_id=backend_game.id,
            account_id=backend_account.id, task_id=task_id,
        )

        client.ensure_token()

        player_id = _resolve_player_id(backend_account, client, logger)
        player_balance_num, _agent_balance = _fetch_balances(player_id, client)

        sc, msg, _body = client.agent_withdraw(
            player_id=player_id, balance=int(count), customer_balance=player_balance_num, remark="withdraw",
        )

        if sc == _OK:
            logger.info("Withdraw successful.")
            insert_log_and_update_automation_result(
                log_type="info",
                log_description=f"Withdrawal successful for account: {account_id}",
                task_id=task_id, source_url=None, backend_id=BACKEND_ID,
                account_id=backend_account.id, result_status="success",
                result_description=f"Withdrawal successful for account: {account_id}",
                redeem_request_id=redeem_request_id, redeem_request_status="processed",
                order_id=order_id, wallet_detail_status="finished",
                add_to_wallet=True, add_to_wallet_amount=requested_amount,
            )
            return

        if _REDEEM_INSUFFICIENT in (msg or "").lower():
            logger.error("Withdrawal failed due to insufficient customer balance.")
            description = "Insufficient customer balance."
        else:
            logger.warning("Unexpected withdrawal response: (%s) %s", sc, msg)
            description = f"Withdraw failed on {BACKEND_NAME}: ({sc}) {msg}"

        insert_log_and_update_automation_result(
            log_type="warning",
            log_description=description,
            task_id=task_id, source_url=None, backend_id=BACKEND_ID,
            account_id=backend_account.id, result_status="failed",
            result_description=description,
            redeem_request_id=redeem_request_id, redeem_request_status="failed",
            order_id=order_id, wallet_detail_status="failed", add_to_wallet=False,
        )

    except Exception as e:
        logger.critical("Error during account withdrawal: %s", e, exc_info=True)
        send_email(
            subject="Account withdrawal failed",
            body=f"Critical error occurred during account withdrawal for account ID {account_id} on backend '{BACKEND_NAME}'. Please review",
        )
        insert_log_and_update_automation_result(
            log_type="error",
            log_description=f"Error during account withdrawal: {e}",
            task_id=task_id, source_url=None, backend_id=backend_game.id,
            result_status="failed",
            result_description=f"Error during account withdrawal: {e}",
            screenshot_url=None, account_id=backend_account.id,
        )
    finally:
        logger.info("Withdraw-account action completed.")
        insert_log(
            "info", "Withdrawal account action completed",
            source_url=None, backend_id=backend_game.id,
            account_id=backend_account.id, task_id=task_id,
        )


def action_freeplay_account(
    count: int, account_id: str, task_id, backend,
    t, id_to_update, freeplay_id, **_,
):
    backend_game, backend_account = get_backend_and_account(backend, account_id)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Freeplay-account action started: account_id=%s, count=%d", account_id, count)

    client = build_client_from_backend(backend_game, logger)

    try:
        insert_log(
            "info",
            f"Initiating recharge for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=None, backend_id=backend_game.id,
            account_id=backend_account.id, task_id=task_id,
        )

        client.ensure_token()

        player_id = _resolve_player_id(backend_account, client, logger)
        player_balance_num, agent_balance = _fetch_balances(player_id, client)
        logger.info("Available balance: %s", player_balance_num)

        if player_balance_num >= FREEPLAY_ELIGIBLE_THRESHOLD:
            logger.info("Available balance is not freeplay eligible. Aborting")
            insert_log_and_update_automation_result(
                log_type="warning",
                log_description="Available balance is not freeplay eligible. Aborting",
                task_id=task_id, backend_id=BACKEND_ID, source_url=None,
                account_id=backend_account.id, result_status="failed",
                result_data={"balance": player_balance_num},
                result_description="Available balance is not freeplay eligible. Aborting",
            )
            return

        sc, msg, _body = client.agent_recharge(
            player_id=player_id, balance=int(count), available_balance=agent_balance, remark="freeplay",
        )

        if sc == _OK:
            logger.info("Freeplay recharge successful.")
            insert_log_and_update_automation_result(
                log_type="info",
                log_description=f"Freeplay Recharge successful for account: {account_id}",
                task_id=task_id, source_url=None, backend_id=BACKEND_ID,
                account_id=backend_account.id, result_status="success",
                result_description=f"Freeplay Recharge successful for account: {account_id}",
            )
            process_freeplay_operation(
                t=t, username=account_id, account_id=backend_account.id,
                freeplay_id=freeplay_id, id_to_update=id_to_update,
                backend_id=BACKEND_ID, task_id=task_id,
            )
            return

        if _RECHARGE_INSUFFICIENT in (msg or "").lower():
            send_email(
                subject="Recharge failed",
                body=f"Recharge failed for account: {account_id} because of insufficient balance on {BACKEND_NAME}.",
            )
            logger.error("Freeplay failed: backend balance insufficient.")
            description = f"Backend balance insufficient for {BACKEND_NAME}"
        else:
            logger.warning("Unexpected recharge response: (%s) %s", sc, msg)
            description = f"Unexpected recharge response: ({sc}) {msg} on {BACKEND_NAME}"

        insert_log_and_update_automation_result(
            log_type="warning",
            log_description=description,
            task_id=task_id, source_url=None, backend_id=BACKEND_ID,
            account_id=backend_account.id, result_status="failed",
            result_description=description,
        )

    except Exception as e:
        logger.critical("Error during account freeplay recharge: %s", e, exc_info=True)
        send_email(
            subject="Account recharge failed",
            body=f"Critical error occurred during freeplay recharge for account ID {account_id} on backend '{BACKEND_NAME}'. Please review",
        )
        insert_log_and_update_automation_result(
            log_type="error",
            log_description=f"Error during account freeplay recharge: {e}",
            task_id=task_id, source_url=None, backend_id=backend_game.id,
            result_status="failed",
            result_description=f"Error during account freeplay recharge: {e}",
            screenshot_url=None, account_id=backend_account.id,
        )
    finally:
        logger.info("Freeplay-account action completed.")
        insert_log(
            "info", "Recharge account action completed",
            source_url=None, backend_id=backend_game.id,
            account_id=backend_account.id, task_id=task_id,
        )


def action_reset_password(account_id: str, task_id, backend, **_):
    backend_game, backend_account = get_backend_and_account(backend, account_id)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Reset-password action started: account_id=%s", account_id)

    client = build_client_from_backend(backend_game, logger)

    try:
        insert_log(
            "info",
            f"Initiating password reset for account ID {account_id} on backend '{BACKEND_NAME}'",
            source_url=None, backend_id=backend_game.id,
            account_id=backend_account.id, task_id=task_id,
        )

        client.ensure_token()

        player_id = _resolve_player_id(backend_account, client, logger)
        # Reset uses the same 6-16 alphanumeric policy as create.
        _, password = generate_credentials()

        sc, msg, _body = client.reset_password(player_id=player_id, password=password)

        if sc == _OK:
            logger.info("Password reset successful.")
            insert_log_and_update_automation_result(
                log_type="info",
                log_description=f"Password reset successful for account: {account_id}",
                task_id=task_id, source_url=None, backend_id=BACKEND_ID,
                account_id=backend_account.id, result_status="success",
                result_description=f"Password reset successful for account: {account_id}",
                result_data={"password": password},
            )
            update_password_by_username(username=account_id, new_password=password, backend_id=BACKEND_ID)
        else:
            logger.warning("Password reset failed. Unhandled reset response: (%s) %s", sc, msg)
            insert_log_and_update_automation_result(
                log_type="warning",
                log_description=f"Password reset failed. Unhandled reset response: {msg}",
                task_id=task_id, source_url=None, backend_id=BACKEND_ID,
                account_id=backend_account.id, result_status="failed",
                result_description=f"Password reset failed. Unhandled reset response: {msg}",
            )

    except Exception as e:
        logger.critical("Error during account password reset: %s", e, exc_info=True)
        send_email(
            subject="Account password reset failed",
            body=f"Critical error occurred during reset password for {account_id} on backend '{BACKEND_NAME}'. Please review",
        )
        insert_log_and_update_automation_result(
            log_type="error",
            log_description=f"Error during account password reset: {e}",
            task_id=task_id, source_url=None, backend_id=backend_game.id,
            result_status="failed",
            result_description=f"Error during account password reset: {e}",
            screenshot_url=None, account_id=backend_account.id,
        )
    finally:
        logger.info("Reset-password action completed.")
        insert_log(
            "info", "Reset password action completed",
            source_url=None, backend_id=backend_game.id,
            account_id=backend_account.id, task_id=task_id,
        )
