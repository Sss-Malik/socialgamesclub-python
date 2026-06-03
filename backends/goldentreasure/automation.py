"""
Golden Treasure backend: direct HTTP API implementation.

A pure-HTTP backend (no browser) modelled on the gamevault / gameroom API
backends. Preserves the same DB side effects, webhook trigger points,
email sends, and response / description strings as the other backends so
callers (Laravel etc.) need no changes.

See /Applications/development/goldentreasure-standalone/goldentreasure_api_findings.md
for the full endpoint spec.
"""

import json
import logging
import time

from common.utils.emails import send_email
from common.utils.logger import get_backend_logger
from common.utils.ensure_directories import ensure_directories
from common.utils.save_credentials import save_credentials

from backends.goldentreasure.config import (
    BACKEND_NAME,
    BACKEND_ID,
    DATA_DIR,
    LOGS_DIR,
    CAPTCHA_DIR,
    RATE_LIMIT_DELAY_SECONDS,
    RECHARGE_ELIGIBLE_THRESHOLD,
    FREEPLAY_ELIGIBLE_THRESHOLD,
)
from backends.goldentreasure.utils.credentials import generate_credentials
from backends.goldentreasure.api_client import (
    GoldenTreasureAPIError,
    SUCCESS_CODE,
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
    update_password_by_username,
    restore_wallet_balance,
    process_recharge_operation,
    process_freeplay_operation,
)


# ---------------------------------------------------------------------------
# Error-code classification (see spec §7)
# ---------------------------------------------------------------------------

_OK = SUCCESS_CODE                 # 20000
_ACCOUNT_EXISTS = 8                # savePlayer: account name already in use
_SCORE_REFUSED = 21                # enterScore: amount out of range / insufficient
_BAD_PASSWORD = 1003               # savePlayer / updatePlayer: password policy

CREATE_ACCOUNT_MAX_RETRIES = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_single_account(client, logger: logging.Logger, task_id, *, user_id=None) -> None:
    """Create one player via /savePlayer, regenerating on name collisions.

    Spaces savePlayer calls by RATE_LIMIT_DELAY_SECONDS to respect the
    server's burst limit (code 167).
    """
    for attempt in range(CREATE_ACCOUNT_MAX_RETRIES):
        if attempt > 0:
            time.sleep(RATE_LIMIT_DELAY_SECONDS)

        account_id, password = generate_credentials()
        logger.debug("Generated credentials: %s", account_id)

        code, msg, _body = client.create_player(account=account_id, pwd=password)

        if code == _OK:
            save_credentials(account_id, password, logger, DATA_DIR)
            insert_backend_account(
                username=account_id,
                password=password,
                backend_id=BACKEND_ID,
                user_id=user_id,
                is_assigned=bool(user_id),
            )
            logger.info("Account created successfully: %s", account_id)
            return

        if code == _ACCOUNT_EXISTS:
            logger.warning(
                "Account %s already exists (attempt %d/%d); regenerating.",
                account_id, attempt + 1, CREATE_ACCOUNT_MAX_RETRIES,
            )
            continue

        # Unexpected non-success (bad password, etc.) — surface it; the action
        # wrapper converts the raised exception into the standard failure path.
        insert_log(
            "warning",
            f"Unexpected create account response: ({code}) {msg}",
            source_url=None, backend_id=BACKEND_ID, task_id=task_id,
        )
        raise GoldenTreasureAPIError(code, msg)

    raise Exception(
        f"Create-account exhausted {CREATE_ACCOUNT_MAX_RETRIES} retries on code 8 (account exists)."
    )


def _player_balance_num(client, username: str) -> float:
    """Return the player's curScore as a float via /getPlayerScore."""
    code, msg, body = client.player_balance(username)
    if code != _OK:
        raise GoldenTreasureAPIError(code, msg)
    try:
        return float(body.get("curScore", 0))
    except (TypeError, ValueError):
        return 0.0


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
            if i > 0:
                time.sleep(RATE_LIMIT_DELAY_SECONDS)
            logger.info("Creating account %d of %d", i + 1, count)
            _create_single_account(client, logger, task_id, user_id=None)

        update_automation_result(
            task_id=task_id,
            status="success",
            description="Account creation successful.",
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
            task_id=task_id,
            source_url=None,
            backend_id=backend_game.id,
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
            task_id=task_id,
            status="success",
            description="Account creation successful.",
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
            task_id=task_id,
            source_url=None,
            backend_id=backend_game.id,
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

        code, msg, body = client.agent_balance()
        if code != _OK:
            raise GoldenTreasureAPIError(code, msg)

        balance_value = body.get("LimitNum", 0)
        logger.info("Agent balance: %s", balance_value)
        update_backend_balance(backend_id=backend_game.id, backend_balance=balance_value)
        update_automation_result(
            task_id=task_id,
            status="success",
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
            task_id=task_id,
            source_url=None,
            backend_id=backend_game.id,
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
            source_url=None, backend_id=BACKEND_ID,
            account_id=backend_account.id, task_id=task_id,
        )

        # getPlayerScore is a targeted single-player lookup. getPlayerList was
        # unreliable here — it does not always include the requested account.
        code, msg, body = client.player_balance(backend_account.username)
        if code != _OK:
            raise GoldenTreasureAPIError(code, msg)

        balance = body.get("curScore")

        # Preserve the field key-set used by the other backends; getPlayerScore
        # only exposes the balance, so the rest are returned as "".
        out = {
            "id": "",
            "account": account_id,
            "nickname": "",
            "balance": str(balance) if balance is not None else "",
            "created_at": "",
            "login_count": "",
            "last_login": "",
            "last_login_ip": "",
        }

        update_automation_result(
            task_id=task_id,
            status="success",
            description="Account information",
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
            task_id=task_id,
            source_url=None,
            backend_id=backend_game.id,
            result_status="failed",
            result_description=f"Error during account read: {e}",
            screenshot_url=None,
            account_id=backend_account.id,
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

        # Pre-flight eligibility guard (player must be low-balance to recharge).
        balance_num = _player_balance_num(client, backend_account.username)
        logger.info("Available balance: %s", balance_num)

        if balance_num > RECHARGE_ELIGIBLE_THRESHOLD:
            logger.info("Available balance is not recharge eligible. Aborting")
            process_recharge_operation(
                order_id=order_id,
                task_id=task_id,
                account_id=backend_account.id,
                backend_id=BACKEND_ID,
                page_url=None,
                log_data={
                    "type": "warning",
                    "description": f"Customer balance ineligible for recharge: {balance_num}",
                },
                order_status="failed",
                automation_status="failed",
                automation_result_fields={
                    "status": "failed",
                    "description": "Customer balance ineligible for recharge",
                },
                wallet_status="failed",
                restore_wallet=True,
                amount_to_restore=amount_to_deduct,
                wallet_id=wallet_id,
                bonus_transferred=False,
                restore_coupon=True,
                coupon_code=coupon_code,
            )
            return

        code, msg, _body = client.recharge(
            account=backend_account.username, amount=int(count), remark="recharge",
        )

        if code == _OK:
            logger.info("Recharge successful.")
            bonus_transferred = bool(backend_account.user and backend_account.user.bonus_received)
            process_recharge_operation(
                order_id=order_id,
                task_id=task_id,
                account_id=backend_account.id,
                backend_id=BACKEND_ID,
                page_url=None,
                log_data={
                    "type": "info",
                    "description": f"Recharge successful for account: {account_id}",
                },
                order_status="finished",
                automation_status="finished",
                automation_result_fields={
                    "status": "success",
                    "description": f"Recharge successful for account: {account_id}",
                },
                wallet_status="finished",
                restore_wallet=False,
                amount_to_restore=None,
                wallet_id=None,
                bonus_transferred=bonus_transferred,
                restore_coupon=False,
                coupon_code=coupon_code,
            )
            return

        # --- failure branches (wallet + coupon restore) ---
        if code == _SCORE_REFUSED:
            # code 21 on a recharge => amount exceeds the agent's LimitNum.
            logger.error("Recharge failed: backend balance insufficient.")
            send_email(
                subject="Recharge failed",
                body=f"Recharge failed for account: {account_id} because of insufficient balance on {BACKEND_NAME}.",
            )
            description = f"Backend balance insufficient for {BACKEND_NAME} - Wallet balance restored"
        else:
            logger.warning("Unexpected recharge response: (%s) %s", code, msg)
            description = f"Recharge failed on {BACKEND_NAME}: ({code}) {msg} - Wallet balance restored"

        process_recharge_operation(
            order_id=order_id,
            task_id=task_id,
            account_id=backend_account.id,
            backend_id=BACKEND_ID,
            page_url=None,
            log_data={"type": "warning", "description": description},
            order_status="failed",
            automation_status="failed",
            automation_result_fields={"status": "failed", "description": description},
            wallet_status="failed",
            restore_wallet=True,
            amount_to_restore=amount_to_deduct,
            wallet_id=wallet_id,
            bonus_transferred=False,
            restore_coupon=True,
            coupon_code=coupon_code,
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
            task_id=task_id,
            source_url=None,
            backend_id=backend_game.id,
            result_status="failed",
            result_description=f"WALLET_RESTORED - Error during account recharge: {e}",
            screenshot_url=None,
            account_id=backend_account.id,
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

        code, msg, _body = client.withdraw(
            account=backend_account.username, amount=int(count), remark="withdraw",
        )

        if code == _OK:
            logger.info("Withdraw successful.")
            insert_log_and_update_automation_result(
                log_type="info",
                log_description=f"Withdrawal successful for account: {account_id}",
                task_id=task_id,
                source_url=None,
                backend_id=BACKEND_ID,
                account_id=backend_account.id,
                result_status="success",
                result_description=f"Withdrawal successful for account: {account_id}",
                redeem_request_id=redeem_request_id,
                redeem_request_status="processed",
                order_id=order_id,
                wallet_detail_status="finished",
                add_to_wallet=True,
                add_to_wallet_amount=requested_amount,
            )
            return

        if code == _SCORE_REFUSED:
            # code 21 on a withdraw => amount exceeds the player's curScore.
            logger.error("Withdrawal failed due to insufficient customer balance.")
            description = "Insufficient customer balance."
        else:
            logger.warning("Unexpected withdrawal response: (%s) %s", code, msg)
            description = f"Withdraw failed on {BACKEND_NAME}: ({code}) {msg}"

        insert_log_and_update_automation_result(
            log_type="warning",
            log_description=description,
            task_id=task_id,
            source_url=None,
            backend_id=BACKEND_ID,
            account_id=backend_account.id,
            result_status="failed",
            result_description=description,
            redeem_request_id=redeem_request_id,
            redeem_request_status="failed",
            order_id=order_id,
            wallet_detail_status="failed",
            add_to_wallet=False,
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
            task_id=task_id,
            source_url=None,
            backend_id=backend_game.id,
            result_status="failed",
            result_description=f"Error during account withdrawal: {e}",
            screenshot_url=None,
            account_id=backend_account.id,
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

        balance_num = _player_balance_num(client, backend_account.username)
        logger.info("Available balance: %s", balance_num)

        if balance_num >= FREEPLAY_ELIGIBLE_THRESHOLD:
            logger.info("Available balance is not freeplay eligible. Aborting")
            insert_log_and_update_automation_result(
                log_type="warning",
                log_description="Available balance is not freeplay eligible. Aborting",
                task_id=task_id,
                backend_id=BACKEND_ID,
                source_url=None,
                account_id=backend_account.id,
                result_status="failed",
                result_data={"balance": balance_num},
                result_description="Available balance is not freeplay eligible. Aborting",
            )
            return

        code, msg, _body = client.recharge(
            account=backend_account.username, amount=int(count), remark="freeplay",
        )

        if code == _OK:
            logger.info("Freeplay recharge successful.")
            insert_log_and_update_automation_result(
                log_type="info",
                log_description=f"Freeplay Recharge successful for account: {account_id}",
                task_id=task_id,
                source_url=None,
                backend_id=BACKEND_ID,
                account_id=backend_account.id,
                result_status="success",
                result_description=f"Freeplay Recharge successful for account: {account_id}",
            )
            process_freeplay_operation(
                t=t,
                username=account_id,
                account_id=backend_account.id,
                freeplay_id=freeplay_id,
                id_to_update=id_to_update,
                backend_id=BACKEND_ID,
                task_id=task_id,
            )
            return

        if code == _SCORE_REFUSED:
            send_email(
                subject="Recharge failed",
                body=f"Recharge failed for account: {account_id} because of insufficient balance on {BACKEND_NAME}.",
            )
            logger.error("Freeplay failed: backend balance insufficient.")
            description = f"Backend balance insufficient for {BACKEND_NAME}"
        else:
            logger.warning("Unexpected recharge response: (%s) %s", code, msg)
            description = f"Unexpected recharge response: ({code}) {msg} on {BACKEND_NAME}"

        insert_log_and_update_automation_result(
            log_type="warning",
            log_description=description,
            task_id=task_id,
            source_url=None,
            backend_id=BACKEND_ID,
            account_id=backend_account.id,
            result_status="failed",
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
            task_id=task_id,
            source_url=None,
            backend_id=backend_game.id,
            result_status="failed",
            result_description=f"Error during account freeplay recharge: {e}",
            screenshot_url=None,
            account_id=backend_account.id,
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

        # Reset uses the same 6-16 alphanumeric policy as create (spec §8.6).
        _, password = generate_credentials()

        code, msg, _body = client.reset_password(
            account=backend_account.username, pwd=password,
        )

        if code == _OK:
            logger.info("Password reset successful.")
            insert_log_and_update_automation_result(
                log_type="info",
                log_description=f"Password reset successful for account: {account_id}",
                task_id=task_id,
                source_url=None,
                backend_id=BACKEND_ID,
                account_id=backend_account.id,
                result_status="success",
                result_description=f"Password reset successful for account: {account_id}",
                result_data={"password": password},
            )
            update_password_by_username(username=account_id, new_password=password)
        else:
            logger.warning("Password reset failed. Unhandled reset response: (%s) %s", code, msg)
            insert_log_and_update_automation_result(
                log_type="warning",
                log_description=f"Password reset failed. Unhandled reset response: {msg}",
                task_id=task_id,
                source_url=None,
                backend_id=BACKEND_ID,
                account_id=backend_account.id,
                result_status="failed",
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
            task_id=task_id,
            source_url=None,
            backend_id=backend_game.id,
            result_status="failed",
            result_description=f"Error during account password reset: {e}",
            screenshot_url=None,
            account_id=backend_account.id,
        )
    finally:
        logger.info("Reset-password action completed.")
        insert_log(
            "info", "Reset password action completed",
            source_url=None, backend_id=backend_game.id,
            account_id=backend_account.id, task_id=task_id,
        )
