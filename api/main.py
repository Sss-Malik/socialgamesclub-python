# api/main.py

import importlib
from fastapi import FastAPI, HTTPException, status, Header
from .schemas import (
    CreateAccountRequest,
    RechargeAccountRequest,
    WithdrawAccountRequest,
    ReadAccountRequest,
    RechargeFreeplayRequest,
)
from settings import APP_KEY
from .tasks import invoke_action
from common.utils.db_actions import get_order, insert_automation_result, get_backend_account

app = FastAPI(
    title="Casino Automation API",
    version="1.0.0",
    description="Run casino automation tasks via HTTP API."
)

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
    insert_automation_result(
        task_id=task.id,
        description="Initiate account creation",
        user_id=None
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
    x_app_key: str = Header(None)
):
    _check_app_key(x_app_key)

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
    insert_automation_result(
        task_id=task.id,
        description="Initiate account recharge",
        user_id=order.user.id
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
    x_app_key: str = Header(None)
):
    _check_app_key(x_app_key)

    task = invoke_action.apply_async(
        args=[req.backend, "withdraw-account"],
        kwargs={"account_id": req.account_id, "count": req.count},
        queue=req.backend
    )
    insert_automation_result(
        task_id=task.id,
        description="Initiate account withdrawal",
        user_id=None
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
    x_app_key: str = Header(None)
):
    _check_app_key(x_app_key)

    task = invoke_action.apply_async(
        args=[req.backend, "read-account"],
        kwargs={"account_id": req.account_id},
        queue=req.backend
    )
    insert_automation_result(
        task_id=task.id,
        description="Initiate account read",
        user_id=None
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
    x_app_key: str = Header(None)
):

    backend_account = get_backend_account(req.account_id)
    if not backend_account or not backend_account.user:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Account not found")

    user = backend_account.user
    if user.freeplay_transferred:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Freeplay already transferred")
    if user.freeplay_amount == 0:
        return {"status": "skipped", "reason": "Freeplay amount is 0"}

    count = user.freeplay_amount or 1
    task = invoke_action.apply_async(
        args=[req.backend, "freeplay-account"],
        kwargs={"account_id": req.account_id, "count": count},
        queue=req.backend
    )
    insert_automation_result(
        task_id=task.id,
        description="Initiate freeplay transfer",
        user_id=user.id
    )
    return {
        "status": "scheduled",
        "task_id": task.id,
        **req.dict(),
        "action": "freeplay-account"
    }
