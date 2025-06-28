# casino_automation/crud.py
from db import SessionLocal
from models import BackendGame, BackendAccount, Log

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