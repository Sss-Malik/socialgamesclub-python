"""
Juwa backend: direct HTTP API implementation.

Mirrors the gamevault transition: replaces Playwright automation with direct
calls to juwa's external HTTP API, preserving all DB side effects, webhook
trigger points, email sends, and response / description strings so Laravel
needs no changes.
"""

import json
import logging

from common.utils.emails import send_email
from common.utils.logger import get_backend_logger
from common.utils.ensure_directories import ensure_directories
from common.utils.save_credentials import save_credentials

from backends.juwa2.config import (
    BACKEND_NAME,
    BACKEND_ID,
    DATA_DIR,
    LOGS_DIR,
    CAPTCHA_DIR,
)
from backends.juwa2.utils.credentials import generate_credentials
from backends.juwa2.api_client import (
    JuwaAPIError,
    build_client_from_backend,
)

from common.utils.db_actions import (
    get_backend,
    get_backend_and_account,
    insert_backend_account,
    insert_log,
    update_automation_result,
    update_game_id_by_username,
    set_game_id_if_null,
    update_password_by_username,
    update_backend_balance,
    restore_wallet_balance,
    process_recharge_operation,
    insert_log_and_update_automation_result,
    process_freeplay_operation,
)


# ---------------------------------------------------------------------------
# Error-code classification
# ---------------------------------------------------------------------------

_RECHARGE_GENERIC_FAIL_CODES = {2, 11, 12, 13, 400}
_WITHDRAW_FAIL_CODES = {2, 11, 14, 15, 16, 17, 400}
_FREEPLAY_GENERIC_FAIL_CODES = {2, 11, 400}

CREATE_ACCOUNT_MAX_RETRIES = 20
_RECHARGE_ELIGIBLE_THRESHOLD = 20
_FREEPLAY_ELIGIBLE_THRESHOLD = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_user_id(backend_account, client, logger: logging.Logger) -> str:
    if backend_account.game_id:
        return str(backend_account.game_id)

    logger.info("game_id missing for %s; resolving via getUserID", backend_account.username)
    code, msg, data = client.get_user_id(account_name=backend_account.username)
    if code != 0 or not data.get("user_id"):
        raise JuwaAPIError(code, f"Could not resolve user_id for {backend_account.username}: {msg}")

    user_id = str(data["user_id"])
    set_game_id_if_null(backend_account.username, user_id)
    return user_id


def _call_with_stale_id_retry(api_call, backend_account, client, logger: logging.Logger):
    code, msg, data = api_call()
    if code != 8:
        return code, msg, data

    logger.warning("juwa returned code 8 for %s; re-resolving user_id", backend_account.username)
    code2, msg2, data2 = client.get_user_id(account_name=backend_account.username)
    if code2 != 0 or not data2.get("user_id"):
        return code, msg, data
    new_user_id = str(data2["user_id"])
    set_game_id_if_null(backend_account.username, new_user_id)
    backend_account.game_id = new_user_id
    return api_call(new_user_id=new_user_id)


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

        hard_stop = False
        for i in range(count):
            logger.info("Creating account %d of %d", i + 1, count)
            created = False

            for attempt in range(CREATE_ACCOUNT_MAX_RETRIES):
                account_id, password = generate_credentials()
                logger.debug("Generated credentials: %s", account_id)

                code, msg, data = client.add_user(account=account_id, login_pwd=password)

                if code == 0:
                    user_id = data.get("user_id")
                    save_credentials(account_id, password, logger, DATA_DIR)
                    insert_backend_account(
                        username=account_id,
                        password=password,
                        backend_id=BACKEND_ID,
                        game_id=user_id,
                    )
                    logger.info("Account created successfully: %s (user_id=%s)", account_id, user_id)
                    created = True
                    break

                if code == 20:
                    logger.warning(
                        "Account %s already exists (attempt %d/%d); regenerating.",
                        account_id, attempt + 1, CREATE_ACCOUNT_MAX_RETRIES,
                    )
                    continue

                if code == 18:
                    insert_log(
                        "error",
                        f"juwa rejected account format: {account_id} ({msg})",
                        source_url=None, backend_id=BACKEND_ID, task_id=task_id,
                    )
                    hard_stop = True
                    break

                insert_log(
                    "warning",
                    f"Unexpected create account response: ({code}) {msg}",
                    source_url=None, backend_id=BACKEND_ID, task_id=task_id,
                )
                break

            if hard_stop:
                break

            if not created:
                insert_log(
                    "warning",
                    f"Create-account iteration {i + 1} exhausted {CREATE_ACCOUNT_MAX_RETRIES} retries on code 20.",
                    source_url=None, backend_id=BACKEND_ID, task_id=task_id,
                )

        if hard_stop:
            insert_log_and_update_automation_result(
                log_type="error",
                log_description=f"Account creation halted: account format rejected on {BACKEND_NAME}",
                task_id=task_id,
                source_url=None,
                backend_id=BACKEND_ID,
                result_status="failed",
                result_description=f"Account creation halted: account format rejected on {BACKEND_NAME}",
                screenshot_url=None,
            )
        else:
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
            backend_id=BACKEND_ID,
            result_status="failed",
            result_description=f"Error during account creation: {e}",
            screenshot_url=None,
        )
    finally:
        logger.info("Create-account action completed.")
        insert_log("info", "Create account action completed", source_url=None, backend_id=BACKEND_ID, task_id=task_id)


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

        code, msg, data = client.agent_balance()
        if code != 0:
            raise JuwaAPIError(code, msg)

        balance_value = data.get("agent_balance", 0)
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
        insert_log("info", "Read backend action completed", source_url=None, backend_id=backend_game.id, task_id=task_id)


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
            source_url=None, backend_id=BACKEND_ID, account_id=backend_account.id, task_id=task_id,
        )

        user_id = _resolve_user_id(backend_account, client, logger)

        def _call(new_user_id=None):
            return client.user_balance(user_id=new_user_id or user_id)
        code, msg, data = _call_with_stale_id_retry(_call, backend_account, client, logger)

        if code != 0:
            raise JuwaAPIError(code, msg)

        balance_value = data.get("user_balance", "")
        out = {
            "id": user_id,
            "account": account_id,
            "balance": balance_value,
            "created_at": "",
            "login_count": "",
            "last_login": "",
            "last_login_ip": "",
        }
        if not backend_account.game_id:
            update_game_id_by_username(account_id, user_id)
        update_automation_result(
            task_id=task_id,
            status="success",
            description="Account information.",
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
            source_url=None, backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id,
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
            source_url=None, backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id,
        )

        user_id = _resolve_user_id(backend_account, client, logger)

        def _balance_call(new_user_id=None):
            return client.user_balance(user_id=new_user_id or user_id)
        bcode, bmsg, bdata = _call_with_stale_id_retry(_balance_call, backend_account, client, logger)
        if bcode != 0:
            raise JuwaAPIError(bcode, bmsg)
        balance = bdata.get("user_balance", "0")

        try:
            balance_num = float(balance)
        except (TypeError, ValueError):
            balance_num = 0.0

        if balance_num > _RECHARGE_ELIGIBLE_THRESHOLD:
            logger.info("Available balance is not recharge eligible. Aborting")
            process_recharge_operation(
                order_id=order_id,
                task_id=task_id,
                account_id=backend_account.id,
                backend_id=BACKEND_ID,
                page_url=None,
                log_data={
                    "type": "warning",
                    "description": f"Customer balance ineligible for recharge: {balance}",
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

        def _recharge_call(new_user_id=None):
            return client.recharge(
                user_id=new_user_id or user_id, amount=count, order_id=order_id,
            )
        code, msg, _data = _call_with_stale_id_retry(_recharge_call, backend_account, client, logger)

        if code == 0:
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
                    "description": "Recharge successful",
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

        if code == 6:
            logger.error("Recharge failed: backend balance insufficient.")
            send_email(
                subject="Recharge failed",
                body=f"Recharge failed for account: {account_id} because of insufficient balance on {BACKEND_NAME}.",
            )
            log_description = "Backend balance insufficient - Wallet balance restored"
            result_description = f"Insufficient backend balance for {BACKEND_NAME}"
        elif code == 9:
            log_description = f"User account frozen: {msg} - Wallet balance restored"
            result_description = f"User account frozen: {msg}"
        elif code == 10:
            log_description = f"User is in game: {msg} - Wallet balance restored"
            result_description = f"User is in game: {msg}"
        elif code in _RECHARGE_GENERIC_FAIL_CODES:
            log_description = f"Recharge failed on {BACKEND_NAME}: ({code}) {msg} - Wallet balance restored"
            result_description = f"Unexpected recharge response on {BACKEND_NAME}"
        else:
            log_description = f"Unexpected recharge response: ({code}) {msg} - Wallet balance restored"
            result_description = f"Unexpected recharge response on {BACKEND_NAME}"

        process_recharge_operation(
            order_id=order_id,
            task_id=task_id,
            account_id=backend_account.id,
            backend_id=BACKEND_ID,
            page_url=None,
            log_data={"type": "warning", "description": log_description},
            order_status="failed",
            automation_status="failed",
            automation_result_fields={
                "status": "failed",
                "description": result_description,
            },
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
            source_url=None, backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id,
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
            source_url=None, backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id,
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
            source_url=None, backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id,
        )

        user_id = _resolve_user_id(backend_account, client, logger)

        def _withdraw_call(new_user_id=None):
            return client.withdraw(
                user_id=new_user_id or user_id, amount=count, order_id=order_id,
            )
        code, msg, _data = _call_with_stale_id_retry(_withdraw_call, backend_account, client, logger)

        if code == 0:
            logger.info("Withdraw successful.")
            insert_log_and_update_automation_result(
                log_type="info",
                log_description=f"Withdrawal successful for account: {account_id}",
                task_id=task_id,
                source_url=None,
                backend_id=BACKEND_ID,
                account_id=backend_account.id,
                result_status="success",
                result_description="Withdraw successful.",
                redeem_request_id=redeem_request_id,
                redeem_request_status="processed",
                order_id=order_id,
                wallet_detail_status="finished",
                add_to_wallet=True,
                add_to_wallet_amount=requested_amount,
            )
            return

        if code == 7:
            logger.error("Withdrawal failed due to insufficient customer balance.")
            log_description = "Insufficient customer balance."
            result_description = "Insufficient customer balance."
        elif code == 9:
            log_description = f"User account frozen: {msg}"
            result_description = f"User account frozen: {msg}"
        elif code == 10:
            log_description = f"User is in game: {msg}"
            result_description = f"User is in game: {msg}"
        elif code in _WITHDRAW_FAIL_CODES:
            log_description = f"Withdraw failed on {BACKEND_NAME}: ({code}) {msg}"
            result_description = f"Withdraw failed ({code}): {msg}"
        else:
            log_description = f"Unexpected withdrawal response: ({code}) {msg}"
            result_description = "Unexpected withdrawal response."

        insert_log_and_update_automation_result(
            log_type="warning",
            log_description=log_description,
            task_id=task_id,
            source_url=None,
            backend_id=BACKEND_ID,
            account_id=backend_account.id,
            result_status="failed",
            result_description=result_description,
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
            source_url=None, backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id,
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
            source_url=None, backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id,
        )

        user_id = _resolve_user_id(backend_account, client, logger)

        def _balance_call(new_user_id=None):
            return client.user_balance(user_id=new_user_id or user_id)
        bcode, bmsg, bdata = _call_with_stale_id_retry(_balance_call, backend_account, client, logger)
        if bcode != 0:
            raise JuwaAPIError(bcode, bmsg)
        balance = bdata.get("user_balance", "0")

        try:
            balance_num = float(balance)
        except (TypeError, ValueError):
            balance_num = 0.0

        if balance_num >= _FREEPLAY_ELIGIBLE_THRESHOLD:
            logger.info("Available balance is not freeplay eligible. Aborting")
            insert_log_and_update_automation_result(
                log_type="warning",
                log_description="Available balance is not freeplay eligible. Aborting",
                task_id=task_id,
                backend_id=BACKEND_ID,
                source_url=None,
                account_id=backend_account.id,
                result_status="failed",
                result_data={"balance": balance},
                result_description="Available balance is not freeplay eligible. Aborting",
            )
            return

        synthetic_order_id = f"fp:{task_id}"

        def _recharge_call(new_user_id=None):
            return client.recharge(
                user_id=new_user_id or user_id, amount=count, order_id=synthetic_order_id,
            )
        code, msg, _data = _call_with_stale_id_retry(_recharge_call, backend_account, client, logger)

        if code == 0:
            logger.info("Freeplay recharge successful.")
            insert_log_and_update_automation_result(
                log_type="info",
                log_description=f"Freeplay Recharge successful for account: {account_id}",
                task_id=task_id,
                source_url=None,
                backend_id=BACKEND_ID,
                account_id=backend_account.id,
                result_status="success",
                result_description="Freeplay Recharge successful",
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

        if code == 6:
            logger.error("Freeplay failed: backend balance insufficient.")
            send_email(
                subject="Recharge failed",
                body=f"Recharge failed for account: {account_id} because of insufficient balance on {BACKEND_NAME}.",
            )
            log_description = "Backend balance insufficient"
            result_description = f"Insufficient backend balance for {BACKEND_NAME}"
        elif code == 9:
            log_description = f"User account frozen: {msg}"
            result_description = f"User account frozen: {msg}"
        elif code == 10:
            log_description = f"User is in game: {msg}"
            result_description = f"User is in game: {msg}"
        elif code in _FREEPLAY_GENERIC_FAIL_CODES:
            log_description = "Form submission error. Please try again"
            result_description = f"Form submission error on {BACKEND_NAME}"
        else:
            log_description = f"Unexpected recharge response: ({code}) {msg}"
            result_description = f"Unexpected recharge response on {BACKEND_NAME}"

        insert_log_and_update_automation_result(
            log_type="warning",
            log_description=log_description,
            task_id=task_id,
            source_url=None,
            backend_id=BACKEND_ID,
            account_id=backend_account.id,
            result_status="failed",
            result_description=result_description,
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
            source_url=None, backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id,
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
            source_url=None, backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id,
        )

        _, password = generate_credentials()
        user_id = _resolve_user_id(backend_account, client, logger)

        def _call(new_user_id=None):
            return client.reset_password(user_id=new_user_id or user_id, login_pwd=password)
        code, msg, _data = _call_with_stale_id_retry(_call, backend_account, client, logger)

        if code == 0:
            logger.info("Password reset successful.")
            insert_log_and_update_automation_result(
                log_type="info",
                log_description=f"Password reset successful for account {account_id}",
                task_id=task_id,
                source_url=None,
                backend_id=BACKEND_ID,
                account_id=backend_account.id,
                result_status="success",
                result_description="Password reset successful.",
                result_data={"password": password},
            )
            update_password_by_username(username=account_id, new_password=password)
        else:
            logger.warning("Password reset failed. Unhandled reset response: (%s) %s", code, msg)
            insert_log_and_update_automation_result(
                log_type="error",
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
            source_url=None, backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id,
        )


action_create_account_user = action_create_account
