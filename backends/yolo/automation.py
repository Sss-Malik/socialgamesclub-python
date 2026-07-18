"""
YOLO777 backend: direct HTTP API implementation.

A pure-HTTP backend (no browser) modelled on the orionstars / gameroom API
backends. Preserves the same DB side effects, webhook trigger points,
email sends, and response / description strings as the other backends so
callers (Laravel etc.) need no changes.

See /Applications/development/yolo-standalone/yolo_api.md for the full
endpoint spec and verification log.
"""

import json
import logging

from common.utils.emails import send_email
from common.utils.logger import get_backend_logger
from common.utils.ensure_directories import ensure_directories
from common.utils.save_credentials import save_credentials

from backends.yolo.config import (
    BACKEND_NAME,
    BACKEND_ID,
    DATA_DIR,
    LOGS_DIR,
    CAPTCHA_DIR,
    RECHARGE_ELIGIBLE_THRESHOLD,
    FREEPLAY_ELIGIBLE_THRESHOLD,
)
from backends.yolo.utils.credentials import generate_credentials
from backends.yolo.api_client import (
    YoloAPIError,
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

CREATE_ACCOUNT_MAX_RETRIES = 20

# Substring matches into the server's response messages (spec §4.1, §6).
# Recharge and Redeem share the identical "insufficient" message — the
# direction is disambiguated by which operation we invoked.
_SCORE_INSUFFICIENT = "score is insufficient"
_CREATE_EXISTS = "already been taken"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_player(client, backend_account, logger: logging.Logger):
    """Return (player_id, score_str) for this BackendAccount.

    Prefers the exact by-Game-ID lookup (when game_id is cached) and falls
    back to the paginated by-account search. The by-account search is a
    partial match, so a short username like `userYL4` is otherwise buried
    behind every `userYL4*` account on a later page. On first lookup the
    Player ID is back-filled into BackendAccount.game_id.
    """
    player_id, score = client.find_player(
        backend_account.username, game_id=backend_account.game_id,
    )
    if not backend_account.game_id:
        set_game_id_if_null(backend_account.username, player_id)
    return str(player_id), score


def _score_num(score_str) -> float:
    try:
        return float(score_str)
    except (TypeError, ValueError):
        return 0.0


def _create_single_account(client, logger: logging.Logger, task_id, *, user_id=None) -> None:
    """Create one player via /admin/player_list with regenerate-on-duplicate."""
    for attempt in range(CREATE_ACCOUNT_MAX_RETRIES):
        account_id, password = generate_credentials()
        logger.debug("Generated credentials: %s", account_id)

        success, msg = client.create_player(account=account_id, password=password)

        if success:
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

        if _CREATE_EXISTS in (msg or "").lower():
            logger.warning(
                "Account %s already exists (attempt %d/%d); regenerating.",
                account_id, attempt + 1, CREATE_ACCOUNT_MAX_RETRIES,
            )
            continue

        insert_log(
            "warning",
            f"Unexpected create account response: {msg}",
            source_url=None, backend_id=BACKEND_ID, task_id=task_id,
        )
        raise YoloAPIError(msg)

    raise Exception(
        f"Create-account exhausted {CREATE_ACCOUNT_MAX_RETRIES} retries on 'already taken'."
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

        client.ensure_session()

        for i in range(count):
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

        client.ensure_session()

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

        balance_value = client.agent_score()

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

        player_id, score = _resolve_player(client, backend_account, logger)

        # Preserve the field key-set the other backends return; fields the
        # grid does not expose come back as "".
        out = {
            "account_id": account_id,
            "nickname": "",
            "balance": score,
            "register_date": "",
            "last_login": "",
            "manager": "",
            "status": "",
        }

        # Unconditional sync of the public Player ID into game_id.
        update_game_id_by_username(account_id, player_id, backend_id=BACKEND_ID)

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
            source_url=None, backend_id=BACKEND_ID,
            account_id=backend_account.id, task_id=task_id,
        )


def action_recharge_account(
    count: int, account_id: str, order_id, task_id, backend,
    wallet_id, amount_to_deduct, coupon_code=None, leaderboard_reward_id=None, **_,
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

        client.ensure_session()

        player_id, score = _resolve_player(client, backend_account, logger)
        credit_num = _score_num(score)
        logger.info("Available balance: %s", credit_num)

        if credit_num > RECHARGE_ELIGIBLE_THRESHOLD:
            logger.info("Available balance is not recharge eligible. Aborting")
            process_recharge_operation(
                order_id=order_id,
                task_id=task_id,
                account_id=backend_account.id,
                backend_id=BACKEND_ID,
                page_url=None,
                log_data={
                    "type": "warning",
                    "description": f"Customer balance ineligible for recharge: {credit_num}",
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
                restore_leaderboard_reward=True, leaderboard_reward_id=leaderboard_reward_id,
                coupon_code=coupon_code,
            )
            return

        success, msg = client.recharge(
            player_id=player_id,
            account=account_id,
            score=score,
            amount=int(count),
            remark=str(order_id) if order_id else "recharge",
        )

        if success:
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
        if _SCORE_INSUFFICIENT in (msg or "").lower():
            logger.error("Recharge failed: backend balance insufficient.")
            send_email(
                subject="Recharge failed",
                body=f"Recharge failed for account: {account_id} because of insufficient balance on {BACKEND_NAME}.",
            )
            description = f"Backend balance insufficient for {BACKEND_NAME} - Wallet balance restored"
        else:
            logger.warning("Unexpected recharge response: %s", msg)
            description = f"Recharge failed on {BACKEND_NAME}: {msg} - Wallet balance restored"

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
            restore_leaderboard_reward=True, leaderboard_reward_id=leaderboard_reward_id,
            coupon_code=coupon_code,
        )
        logger.info("Wallet balance restored")

    except Exception as e:
        restore_wallet_balance(wallet_id, amount_to_deduct, order_id, coupon_code, leaderboard_reward_id)
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
    """YOLO calls this 'redeem' (type=2); our system's external name is 'withdraw'."""
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

        client.ensure_session()

        player_id, score = _resolve_player(client, backend_account, logger)

        success, msg = client.redeem(
            player_id=player_id,
            account=account_id,
            score=score,
            amount=int(count),
            remark=str(order_id) if order_id else "withdraw",
        )

        if success:
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

        if _SCORE_INSUFFICIENT in (msg or "").lower():
            logger.error("Withdrawal failed due to insufficient customer balance.")
            description = "Insufficient customer balance."
        else:
            logger.warning("Unexpected withdrawal response: %s", msg)
            description = f"Withdraw failed on {BACKEND_NAME}: {msg}"

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

        client.ensure_session()

        player_id, score = _resolve_player(client, backend_account, logger)
        credit_num = _score_num(score)
        logger.info("Available balance: %s", credit_num)

        if credit_num >= FREEPLAY_ELIGIBLE_THRESHOLD:
            logger.info("Available balance is not freeplay eligible. Aborting")
            insert_log_and_update_automation_result(
                log_type="warning",
                log_description="Available balance is not freeplay eligible. Aborting",
                task_id=task_id,
                backend_id=BACKEND_ID,
                source_url=None,
                account_id=backend_account.id,
                result_status="failed",
                result_data={"balance": credit_num},
                result_description="Available balance is not freeplay eligible. Aborting",
            )
            return

        success, msg = client.recharge(
            player_id=player_id,
            account=account_id,
            score=score,
            amount=int(count),
            remark="freeplay",
        )

        if success:
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

        if _SCORE_INSUFFICIENT in (msg or "").lower():
            send_email(
                subject="Recharge failed",
                body=f"Recharge failed for account: {account_id} because of insufficient balance on {BACKEND_NAME}.",
            )
            logger.error("Freeplay failed: backend balance insufficient.")
            description = f"Backend balance insufficient for {BACKEND_NAME}"
        else:
            logger.warning("Unexpected recharge response: %s", msg)
            description = f"Unexpected recharge response: {msg} on {BACKEND_NAME}"

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

        client.ensure_session()

        player_id, _score = _resolve_player(client, backend_account, logger)
        # New password follows the min-6 alphanumeric policy — generate_credentials
        # produces a valid one.
        _, password = generate_credentials()

        success, msg = client.reset_password(
            player_id=player_id, account=account_id, new_password=password,
        )

        if success:
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
            update_password_by_username(username=account_id, new_password=password, backend_id=BACKEND_ID)
        else:
            logger.warning("Password reset failed. Unhandled reset response: %s", msg)
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
