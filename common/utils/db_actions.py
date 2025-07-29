from db import SessionLocal
from models import BackendGame, BackendAccount, Log, Deposit, AutomationResult, BackendSession, ReferralBonus, WheelSpin
from sqlalchemy.orm import joinedload
from sqlalchemy import desc

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



def insert_log(log_type, description, source_url=None):
    db = SessionLocal()
    try:
        log = Log(type=log_type, description=description, source_url=source_url)
        db.add(log)
        db.commit()
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
):
    db = SessionLocal()
    try:
        result = AutomationResult(
            user_id=user_id,
            description=description,
            task_id=task_id,
            status=status,
            data=data,
            backend_id=backend_id
        )
        db.add(result)
        db.commit()
        db.refresh(result)
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


def finalize_status(t, status, id_to_update=None):
    if t == "referral_freeplay":
        mark_referral_bonus_status(id_to_update, status)
    elif t == "reward_freeplay":
        mark_spin_status(id_to_update, status)
