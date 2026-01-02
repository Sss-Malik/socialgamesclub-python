import json
import math
from datetime import datetime

from common.utils.notify import serialize_model, notify_webhook_async
from db import SessionLocal
from models import BackendGame, BackendAccount, Log, Deposit, AutomationResult, BackendSession, ReferralBonus, \
    WheelSpin, RedeemRequest, AutomationRequest, PersonalAccessToken, User, WalletMaster, WalletDetail, Freeplay, Coupon
from sqlalchemy.orm import joinedload
from sqlalchemy import desc, func
from fastapi.encoders import jsonable_encoder
from sqlalchemy import or_

def get_backend(name):
    db = SessionLocal()
    try:
        return db.query(BackendGame).filter(BackendGame.name == name,   BackendGame.deleted_at == None).first()
    finally:
        db.close()

def insert_backend_account(username, password, backend_id, game_id=None, user_id=None, is_assigned=False):
    db = SessionLocal()
    try:
        account = BackendAccount(
            username=username,
            password=password,
            backend_id=backend_id,
            game_id=game_id,
            user_id=user_id,
            is_assigned=is_assigned
        )
        db.add(account)
        db.commit()
        db.refresh(account)
        return account
    finally:
        db.close()

def get_backend_account(account_id):
    db = SessionLocal()
    try:
        return db.query(BackendAccount).options(joinedload(BackendAccount.user))\
            .filter(BackendAccount.username == account_id, BackendAccount.deleted_at == None)\
            .first()
    finally:
        db.close()


def get_order(order_id):
    db = SessionLocal()
    try:
        return db.query(Deposit)\
            .options(joinedload(Deposit.user))\
            .filter(Deposit.order_id == order_id)\
            .first()
    finally:
        db.close()


def update_order_automation_status(order_id: str, new_status: str):
    db = SessionLocal()
    try:
        order = db.query(Deposit).filter(Deposit.order_id == order_id).first()
        if not order:
            return None
        order.automation_status = new_status
        db.commit()
        return order
    finally:
        db.close()

def update_order_status(order_id: str, new_status: str):
    db = SessionLocal()
    try:
        order = db.query(Deposit).filter(Deposit.order_id == order_id).first()
        if not order:
            return None
        order.status = new_status
        db.commit()
        return order
    finally:
        db.close()


def update_wallet_detail_status(order_id: str, new_status: str):
    db = SessionLocal()
    try:
        wallet_detail = db.query(WalletDetail).filter(WalletDetail.order_id == order_id).first()
        if not wallet_detail:
            return None
        wallet_detail.status = new_status
        db.commit()
        return wallet_detail
    finally:
        db.close()



def insert_log(log_type, description, source_url=None, backend_id=None, account_id=None, task_id=None):
    db = SessionLocal()
    try:
        log = Log(type=log_type, description=description, source_url=source_url, backend_id=backend_id, account_id=account_id, task_id=task_id)
        db.add(log)
        db.commit()
    except Exception as e:
        db.rollback()
    finally:
        db.close()

def update_game_id_by_username(username: str, new_game_id: int):
    db = SessionLocal()
    try:
        account = db.query(BackendAccount).filter(
            BackendAccount.username == username,
            BackendAccount.deleted_at == None
        ).one_or_none()

        if not account:
            raise ValueError(f"No backend account found for username '{username}'")

        account.game_id = new_game_id
        db.commit()
        db.refresh(account)
        return account

    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def insert_automation_result(
    user_id=None,
    description=None,
    task_id=None,
    status="pending",
    data=None,
    backend_id=None,
    order_id=None
):
    db = SessionLocal()
    try:
        result = AutomationResult(
            user_id=user_id,
            description=description,
            task_id=task_id,
            status=status,
            data=data,
            backend_id=backend_id,
            order_id=order_id
        )
        db.add(result)
        db.commit()
        db.refresh(result)
        return result
    finally:
        db.close()


def get_automation_result(order_id):
    db = SessionLocal()
    try:
        result = (
            db.query(AutomationResult)
            .filter(AutomationResult.order_id == order_id)
            .order_by(AutomationResult.created_at.desc())
            .first()
        )
        return result
    finally:
        db.close()

def update_automation_result(task_id, **fields):
    db = SessionLocal()
    try:
        result = db.query(AutomationResult).filter(AutomationResult.task_id == task_id).first()
        if not result:
            return None

        for key, value in fields.items():
            if hasattr(result, key):
                setattr(result, key, value)

        db.commit()
        db.refresh(result)
        return result

    except Exception as e:
        db.rollback()
    finally:
        db.close()


def mark_freeplay_transferred(account_id: str) -> bool:
    db = SessionLocal()
    try:
        backend_account = db.query(BackendAccount).options(joinedload(BackendAccount.user))\
            .filter(
                BackendAccount.username == account_id,
                BackendAccount.deleted_at == None
            )\
            .first()

        if not backend_account or not backend_account.user:
            return False  # account or user not found

        backend_account.user.freeplay_transferred = True
        db.commit()
        return True  # successfully updated

    except Exception as e:
        db.rollback()
        print(f"Error updating freeplay_transferred: {e}")
        return False
    finally:
        db.close()


def mark_bonus_transferred(account_id: str) -> bool:
    db = SessionLocal()
    try:
        backend_account = db.query(BackendAccount).options(joinedload(BackendAccount.user))\
            .filter(
                BackendAccount.username == account_id,
                BackendAccount.deleted_at == None
            )\
            .first()

        if not backend_account or not backend_account.user:
            return False  # account or user not found

        backend_account.user.bonus_transferred = True
        db.commit()
        return True  # successfully updated

    except Exception as e:
        db.rollback()
        print(f"Error updating bonus_transferred: {e}")
        return False
    finally:
        db.close()



def get_session(session_id: int, db=None):
    external = db is not None
    db = db or SessionLocal()
    try:
        session = db.query(BackendSession).filter_by(id=session_id).first()
        return session
    finally:
        if not external:
            db.close()

def get_latest_valid_session(backend):
    db = SessionLocal()
    try:
        return db.query(BackendSession) \
            .filter_by(backend=backend, is_valid=True) \
            .order_by(BackendSession.id.desc()) \
            .first()
    finally:
        db.close()

def create_backend_session(backend, token=None, expires=None, is_valid=True, active_tasks_count=0):
    db = SessionLocal()
    try:
        session = BackendSession(
            backend=backend,
            token=token,
            expires=expires,
            is_valid=is_valid,
            active_tasks_count=active_tasks_count
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return session
    finally:
        db.close()


def invalidate_latest_session(backend):
    db = SessionLocal()
    try:
        deleted_count = db.query(BackendSession).filter_by(backend=backend).delete()
        db.commit()
        return deleted_count  # Optional: return how many were deleted
    finally:
        db.close()


def increment_active_tasks_count(session_id: int):
    db = SessionLocal()
    try:
        session = db.query(BackendSession).filter_by(id=session_id).first()
        if session:
            session.active_tasks_count = (session.active_tasks_count or 0) + 1
            db.commit()
    finally:
        db.close()


def decrement_active_tasks_count(session_id: int):
    db = SessionLocal()
    try:
        session = db.query(BackendSession).filter_by(id=session_id).first()
        if session and session.active_tasks_count and session.active_tasks_count > 0:
            session.active_tasks_count -= 1
            db.commit()
    finally:
        db.close()

def get_referral_bonus(user_id):
    db = SessionLocal()
    try:
        return db.query(ReferralBonus)\
            .options(joinedload(ReferralBonus.user))\
            .filter(ReferralBonus.referrer_user_id == user_id)\
            .order_by(desc(ReferralBonus.created_at))\
            .first()
    finally:
        db.close()

def mark_referral_bonus_status(referral_bonus_id, status):
    db = SessionLocal()
    try:
        referral_bonus = db.query(ReferralBonus).filter(ReferralBonus.id == referral_bonus_id).first()
        if not referral_bonus:
            return False

        referral_bonus.status = status
        referral_bonus.claimed_at = func.now()
        db.commit()
        return True

    except Exception as e:
        db.rollback()
        print(f"Error marking referral bonus status: {e}")
        return False
    finally:
        db.close()


def get_spin(user_id):
    db = SessionLocal()
    try:
        return db.query(WheelSpin).options(joinedload(WheelSpin.user)).filter(WheelSpin.user_id == user_id).order_by(desc(WheelSpin.created_at)).first()
    finally:
        db.close()

def mark_spin_status(spin_id, status):
    db = SessionLocal()
    try:
        spin = db.query(WheelSpin).filter(WheelSpin.id == spin_id).first()
        if not spin:
            return False
        spin.status = status
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        print(f"Error marking spin status: {e}")
        return False
    finally:
        db.close()


def mark_redeem_request_status(idx, status):
    db = SessionLocal()
    try:
        redeem_request = db.query(RedeemRequest).filter(RedeemRequest.id == idx).first()
        if not redeem_request:
            return False
        redeem_request.status = status
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        print(f"Error marking redeem request status: {e}")
        return False
    finally:
        db.close()


def insert_automation_request(task_id, request_type, payload, status_code=None):
    db = SessionLocal()
    try:
        request = AutomationRequest(
            task_id=task_id,
            type=request_type,
            payload=payload,
            status_code=status_code
        )
        db.add(request)
        db.commit()
        db.refresh(request)
        return request
    finally:
        db.close()



def finalize_status(t, status: bool, id_to_update=None):
    if t == "referral_freeplay":
        if status:
            mark_referral_bonus_status(id_to_update, "claimed")
    elif t == "reward_freeplay":
        if status:
            mark_spin_status(id_to_update, "success")


def update_password_by_username(username: str, new_password: str):
    db = SessionLocal()
    try:
        account = db.query(BackendAccount).filter(
            BackendAccount.username == username,
            BackendAccount.deleted_at == None
        ).one_or_none()

        if not account:
            raise ValueError(f"No backend account found for username '{username}'")

        account.password = new_password
        db.commit()
        db.refresh(account)
        return account

    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def get_pat(token):
    db = SessionLocal()
    try:
        pat = db.query(PersonalAccessToken).filter_by(token=token).first()
        if pat:
            return pat
        return None
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()

def get_pat_user(tokenable_id):
    db = SessionLocal()
    try:
        user = db.query(User).options(joinedload(User.wallet_master)).filter(User.id == tokenable_id).first()
        if user:
            return user
        return None
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def get_validated_backend_account(account_id, user_id):
    db = SessionLocal()
    try:
        backend_account = db.query(BackendAccount).options(joinedload(BackendAccount.user)).filter(
            BackendAccount.username == account_id,
            BackendAccount.user_id == user_id
        ).first()
        if backend_account and backend_account.user:
            return backend_account
        return None
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def deduct_wallet_balance(wallet_id, deduct_amount):
    db = SessionLocal()
    try:
        wallet = db.query(WalletMaster).filter_by(id=wallet_id).first()
        if wallet:
            wallet.balance_minor = wallet.balance_minor - deduct_amount
            db.commit()

    except Exception as e:
        db.rollback()
    finally:
        db.close()


def check_coupon_validity_and_return_amount(coupon_code: str | None = None):
    if not coupon_code:
        return 0

    db = SessionLocal()
    try:
        now = datetime.utcnow()

        coupon = (
            db.query(Coupon)
            .filter(
                Coupon.code == coupon_code,
                Coupon.status == "pending",
                or_(
                    Coupon.expires_at == None,
                    Coupon.expires_at > now
                )
            )
            .with_for_update()
            .first()
        )

        if not coupon:
            return 0

        # mark coupon as used
        coupon.status = "used"
        db.commit()

        return math.ceil(coupon.bonus_amount)

    except Exception:
        db.rollback()
        return 0
    finally:
        db.close()

def restore_wallet_balance(wallet_id, restore_amount, order_id, coupon_code = None):
    db = SessionLocal()
    status = "refunded"
    try:
        wallet = db.query(WalletMaster).filter_by(id=wallet_id).first()
        if wallet:
            wallet.balance_minor = wallet.balance_minor + restore_amount

        wallet_detail = db.query(WalletDetail).filter(WalletDetail.wallet_id == wallet_id, WalletDetail.order_id == order_id).first()
        if wallet_detail:
            wallet_detail.status = status

        if coupon_code:
            coupon = db.query(Coupon).filter(Coupon.code == coupon_code).with_for_update().first()
            if coupon:
                coupon.status = "pending"

        db.commit()
    except Exception as e:
        db.rollback()
    finally:
        db.close()




def process_recharge_operation(
    *,
    order_id: str,
    task_id: int = None,
    account_id: int = None,
    backend_id: int = None,
    page_url: str = None,
    log_data: dict = None,               # { "type": "info", "description": "...", ... }
    order_status: str = None,            # e.g., "finished" or "failed"
    automation_status: str = None,       # e.g., "finished" or "failed"
    automation_result_fields: dict = None,  # e.g., { "status": "success", "description": "Recharge successful" }
    wallet_status: str = None,        # e.g., "finished" or "failed"
    restore_wallet: bool = False,
    amount_to_restore: int = None,
    wallet_id: int = None,
    bonus_transferred: bool = False,
    restore_coupon: bool = False,
    coupon_code: str = None
):
    db = SessionLocal()
    try:
        results = {}
        backend_account = db.query(BackendAccount).options(joinedload(BackendAccount.user)).filter(
            BackendAccount.id == account_id,
            BackendAccount.deleted_at == None
        ).first()
        if backend_account and backend_account.user:
            if bonus_transferred:
                backend_account.user.bonus_transferred = True
        results["backend_account"] = backend_account

        if restore_wallet:
            wallet = db.query(WalletMaster).filter_by(id=wallet_id).first()
            if wallet:
                wallet.balance_minor = wallet.balance_minor + amount_to_restore
                results["wallet"] = wallet

        # 1. Insert log if provided
        if log_data:
            log = Log(
                type=log_data.get("type"),
                description=log_data.get("description"),
                source_url=log_data.get("source_url", str(page_url) if page_url else None),
                backend_id=backend_id,
                account_id=account_id,
                task_id=task_id,
            )
            db.add(log)
            results["log"] = log

        # 2. Update order (automation_status + status in one query)
        order = db.query(Deposit).filter(Deposit.order_id == order_id).first()
        if order:
            if automation_status is not None:
                order.automation_status = automation_status
            if order_status is not None:
                order.status = order_status
            results["order"] = order

        # 3. Update automation result if fields provided
        if task_id and automation_result_fields:
            result = db.query(AutomationResult).filter(AutomationResult.task_id == task_id).first()
            if result:
                for key, value in automation_result_fields.items():
                    if hasattr(result, key):
                        setattr(result, key, value)
                results["automation_result"] = result

        # 4. Update wallet detail if requested
        if wallet_status is not None:
            wallet_detail = db.query(WalletDetail).filter(WalletDetail.order_id == order_id).first()
            if wallet_detail:
                wallet_detail.status = wallet_status
                results["wallet_detail"] = wallet_detail

        if restore_coupon:
            coupon = db.query(Coupon).filter(Coupon.code == coupon_code).first()
            if coupon:
                coupon.status = "pending"
                results["coupon"] = coupon

        # ✅ Commit once for all
        db.commit()

        # refresh objects if needed
        for obj in results.values():
            db.refresh(obj)

        serializable_results = {k: serialize_model(v) for k, v in results.items()}
        notify_webhook_async(serializable_results, request_type="recharge")
        return results

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()



def get_backend_and_account(backend_name: str, account_id: str):
    db = SessionLocal()
    try:
        result = (
            db.query(BackendGame, BackendAccount)
            .join(BackendAccount, BackendAccount.backend_id == BackendGame.id)
            .options(joinedload(BackendAccount.user))
            .filter(
                BackendGame.name == backend_name,
                BackendGame.deleted_at == None,
                BackendAccount.username == account_id,
                BackendAccount.deleted_at == None,
            )
            .first()
        )
        if result:
            backend, account = result
            return backend, account
        return None, None
    finally:
        db.close()

def insert_automation_result_and_request(
    *,
    user_id=None,
    description=None,
    task_id=None,
    backend_id=None,
    order_id=None,
    request_type=None,
    payload=None,
    status="pending",
    status_code=None,
):
    db = SessionLocal()
    try:
        result = AutomationResult(
            user_id=user_id,
            description=description,
            task_id=task_id,
            backend_id=backend_id,
            order_id=order_id,
            status=status,
        )
        request = AutomationRequest(
            task_id=task_id,
            type=request_type,
            payload=jsonable_encoder(payload),
            status_code=status_code,
        )

        db.add(result)
        db.add(request)

        db.commit()
        db.refresh(result)
        db.refresh(request)

        return result, request
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def insert_log_and_update_automation_result(
    *,
    log_type=None,
    log_description=None,
    task_id=None,
    backend_id=None,
    source_url=None,
    account_id=None,
    result_status=None,
    screenshot_url=None,
    result_description=None,
    result_data=None,
    redeem_request_id=None,
    redeem_request_status=None,
    order_id=None,
    wallet_detail_status=None,
    add_to_wallet=None,
    add_to_wallet_amount=None
):
    db = SessionLocal()
    try:
        results = {}
        # 1. Update the AutomationResult (if exists)
        result = (
            db.query(AutomationResult)
            .filter(AutomationResult.task_id == task_id)
            .with_for_update()  # lock row to prevent race conditions
            .one_or_none()
        )

        if result:
            if result_status:
                result.status = result_status
            if result_description:
                result.description = result_description
            if result_data:
                result.data = json.dumps(result_data)
            if screenshot_url:
                result.screenshot_url = screenshot_url
        else:
            # Optionally: create one if not exists
            result = AutomationResult(
                task_id=task_id,
                backend_id=backend_id,
                status=result_status or "pending",
                description=result_description,
                screenshot_url=screenshot_url,
                data=json.dumps(result_data),
            )
            db.add(result)
        results["automation_result"] = result

        # 2. Always add a new Log
        log = Log(
            type=log_type,
            description=log_description,
            source_url=source_url,
            backend_id=backend_id,
            task_id=task_id,
            account_id=account_id,
        )
        db.add(log)
        results["log"] = log

        if redeem_request_id and redeem_request_status:
            redeem_request = (
                db.query(RedeemRequest)
                .filter(RedeemRequest.id == redeem_request_id)
                .one_or_none()
            )
            if redeem_request:
                redeem_request.status = redeem_request_status
                results["redeem_request"] = redeem_request

            if order_id and wallet_detail_status:
                wallet_detail = db.query(WalletDetail).filter(WalletDetail.order_id == order_id).first()
                if wallet_detail:
                    wallet_detail.status = wallet_detail_status
                    results["wallet_detail"] = wallet_detail

                if add_to_wallet:
                    wallet = wallet_detail.wallet
                    if wallet:
                        wallet.balance_minor += add_to_wallet_amount
                        results["wallet"] = wallet

        # 3. Commit transaction
        db.commit()

        # refresh objects if you want to return them
        for obj in results.values():
            db.refresh(obj)

        if redeem_request_id:
            serializable_results = {k: serialize_model(v) for k, v in results.items()}
            notify_webhook_async(serializable_results, request_type="redeem")
        return results

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()



def update_freeplay(idx, status):
    db = SessionLocal()
    try:
        freeplay = db.query(Freeplay).filter(Freeplay.id == idx).first()
        if not freeplay:
            return False
        freeplay.status = status
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        print(f"Error marking freeplay status: {e}")
        return False
    finally:
        db.close()

def process_freeplay_operation(
    t: str,
    username: str = None,
    account_id: str = None,
    id_to_update: int = None,
    freeplay_id: int = None,
    backend_id: int = None,
    task_id: int = None
) -> bool:
    """
    Unified handler for freeplay-related operations.
    Handles:
      - signup_freeplay  → mark user.freeplay_transferred = True
      - referral_freeplay → mark ReferralBonus as claimed
      - reward_freeplay   → mark WheelSpin as success

    Uses a single DB session and inserts a Log record only if an error occurs.
    """
    db = SessionLocal()
    try:
        if t == "signup_freeplay":
            backend_account = (
                db.query(BackendAccount)
                .options(joinedload(BackendAccount.user))
                .filter(
                    BackendAccount.username == username,
                    BackendAccount.deleted_at == None
                )
                .first()
            )

            if not backend_account or not backend_account.user:
                raise ValueError(f"BackendAccount not found for account_id={username}")

            backend_account.user.freeplay_transferred = True

        elif t == "referral_freeplay":
            referral_bonus = db.query(ReferralBonus).filter(ReferralBonus.id == id_to_update).first()
            if not referral_bonus:
                raise ValueError(f"ReferralBonus not found for id={id_to_update}")

            referral_bonus.status = "claimed"
            referral_bonus.claimed_at = func.now()

        elif t == "reward_freeplay":
            spin = db.query(WheelSpin).filter(WheelSpin.id == id_to_update).first()
            if not spin:
                raise ValueError(f"WheelSpin not found for id={id_to_update}")

            spin.status = "success"

        else:
            raise ValueError(f"Unknown freeplay operation type: '{t}'")

        freeplay = db.query(Freeplay).filter(Freeplay.id == freeplay_id).first()
        if not freeplay:
            raise ValueError(f"Freeplay not found for id={freeplay_id}")
        freeplay.status = "success"

        db.commit()
        return True

    except Exception as e:
        db.rollback()

        # Insert log directly using the same session
        try:
            error_log = Log(
                type="error",
                description=f"<DATABASE ERROR>Error processing freeplay operation '{t}': {str(e)}",
                backend_id=backend_id,
                account_id=account_id,
                task_id=task_id,
            )
            db.add(error_log)
            db.commit()  # commit log separately
        except Exception as log_ex:
            db.rollback()
            print(f"Failed to write error log: {log_ex}")

        return False

    finally:
        db.close()

