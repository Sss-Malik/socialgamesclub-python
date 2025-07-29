# api/main.py

import importlib
from fastapi import FastAPI, HTTPException, status, Header, Request
from .schemas import (
    CreateAccountRequest,
    RechargeAccountRequest,
    WithdrawAccountRequest,
    ReadAccountRequest,
    RechargeFreeplayRequest,
)
from settings import APP_KEY
from .tasks import invoke_action
from common.utils.db_actions import get_order, insert_automation_result, get_backend_account, get_backend, get_referral_bonus, get_spin
import asyncio

app = FastAPI(
    title="Casino Automation API",
    version="1.0.0",
    description="Run casino automation tasks via HTTP API."
)

@app.middleware("http")
async def delay_request(request: Request, call_next):
    await asyncio.sleep(2)
    response = await call_next(request)
    return response


def _check_app_key(x_app_key: str):
    if x_app_key != APP_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid APP_KEY"
        )

@app.post("/automation/create-account")
async def create_account(
    req: CreateAccountRequest,
    x_app_key: str = Header(None)
):
    _check_app_key(x_app_key)

    task = invoke_action.apply_async(
        args=[req.backend, "create-account"],
        queue=req.backend
    )
    backend = get_backend(req.backend)
    insert_automation_result(
        task_id=task.id,
        description="Initiate account creation",
        user_id=None,
        backend_id=backend.id,
    )
    return {
        "status": "scheduled",
        "task_id": task.id,
        **req.dict(),
        "action": "create-account"
    }

@app.post("/automation/recharge-account")
async def recharge_account(
    req: RechargeAccountRequest,
    x_order_id: str = Header(None),
):

    order = get_order(x_order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    if order.status != "finished":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Order not finished")
    if order.automation_status == "finished":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Already processed")

    task = invoke_action.apply_async(
        args=[req.backend, "recharge-account"],
        kwargs={
            "account_id": req.account_id,
            "count": req.count,
            "order_id": x_order_id
        },
        queue=req.backend
    )
    backend = get_backend(req.backend)
    insert_automation_result(
        task_id=task.id,
        description="Initiate account recharge",
        user_id=order.user.id,
        backend_id=backend.id,
    )
    return {
        "status": "scheduled",
        "task_id": task.id,
        **req.dict(),
        "action": "recharge-account"
    }

@app.post("/automation/withdraw-account")
async def withdraw_account(
    req: WithdrawAccountRequest,
):

    task = invoke_action.apply_async(
        args=[req.backend, "withdraw-account"],
        kwargs={"account_id": req.account_id, "count": req.count},
        queue=req.backend
    )
    backend = get_backend(req.backend)
    insert_automation_result(
        task_id=task.id,
        description="Initiate account withdrawal",
        user_id=None,
        backend_id=backend.id,
    )
    return {
        "status": "scheduled",
        "task_id": task.id,
        **req.dict(),
        "action": "withdraw-account"
    }

@app.post("/automation/read-account")
async def read_account(
    req: ReadAccountRequest,
):

    task = invoke_action.apply_async(
        args=[req.backend, "read-account"],
        kwargs={"account_id": req.account_id},
        queue=req.backend
    )
    backend = get_backend(req.backend)
    insert_automation_result(
        task_id=task.id,
        description="Initiate account read",
        user_id=None,
        backend_id=backend.id,
    )
    return {
        "status": "scheduled",
        "task_id": task.id,
        **req.dict(),
        "action": "read-account"
    }

@app.post("/automation/freeplay")
async def recharge_freeplay(
    req: RechargeFreeplayRequest,
):
    backend_account = get_backend_account(req.account_id)
    if not backend_account or not backend_account.user:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Account not found")

    user = backend_account.user
    t = req.type
    count = None
    id_to_update = None

    if t == "referral_freeplay":
        referral_bonus = get_referral_bonus(user.id)
        if referral_bonus.status != "pending":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Referral freeplay already claimed")
        count = referral_bonus.bonus_amount
        id_to_update = referral_bonus.id
    elif t == "reward_freeplay":
        spin = get_spin(user.id)
        if spin.status != "pending":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Spin already claimed")
        if spin.type != "freeplay":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Type mismatch for request")
        count = spin.reward
        id_to_update = spin.id
    elif t == "signup_freeplay":
        if user.freeplay_transferred:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Freeplay already transferred")
        if user.freeplay_amount is None or user.freeplay_amount == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "This user is not eligible for signup freeplay")
        count = user.freeplay_amount

    task = invoke_action.apply_async(
        args=[req.backend, "freeplay-account"],
        kwargs={"account_id": req.account_id, "count": int(count), "t": t, "id_to_update": id_to_update},
        queue=req.backend
    )
    backend = get_backend(req.backend)
    insert_automation_result(
        task_id=task.id,
        description="Initiate freeplay transfer",
        user_id=user.id,
        backend_id=backend.id,
    )
    return {
        "status": "scheduled",
        "task_id": task.id,
        **req.dict(),
        "action": "freeplay-account"
    }
