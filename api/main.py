# api/main.py

from __future__ import annotations
from uuid import uuid4
import asyncio
from typing import Optional, Literal, Dict, Any

from fastapi import FastAPI, HTTPException, status, Header, Request, Depends

from .schemas import (
    CreateAccountRequest,
    RechargeAccountRequest,
    WithdrawAccountRequest,
    ReadAccountRequest,
    RechargeFreeplayRequest, ResetPasswordRequest,
)
from settings import APP_KEY, API_DELAY_SECONDS
from .tasks import invoke_action
from common.utils.db_actions import (
    get_order,
    insert_automation_result,
    get_backend_account,
    get_backend,
    get_referral_bonus,
    get_spin,
    get_automation_result,
    insert_automation_request,
)

# ---- App setup ----

app = FastAPI(
    title="Casino Automation API",
    version="1.0.0",
    description="Run casino automation tasks via HTTP API.",
)



@app.middleware("http")
async def delay_request(request: Request, call_next):
    await asyncio.sleep(API_DELAY_SECONDS)
    return await call_next(request)


# ---- Types / helpers ----

ActionName = Literal[
    "create-account",
    "recharge-account",
    "withdraw-account",
    "read-account",
    "freeplay-account",
    "reset-password"
]

def _check_app_key(x_app_key: Optional[str]) -> None:
    if x_app_key != APP_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid APP_KEY",
        )

def require_app_key(x_app_key: Optional[str] = Header(None)) -> bool:
    _check_app_key(x_app_key)
    return True

def _enqueue_action(
    *,
    backend_key: str,
    action: ActionName,
    description: str,
    queue_kwargs: Dict[str, Any],
    request_type: Literal["create", "recharge", "withdraw", "read", "freeplay", "reset-password"],
    payload: Dict[str, Any],
    user_id: Optional[int] = None,
    order_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Centralizes Celery scheduling + logging so each endpoint stays tiny.
    Preserves all side-effects and response shape.
    """

    task_id = str(uuid4())

    backend = get_backend(backend_key)

    insert_automation_result(
        task_id=task_id,
        description=description,
        user_id=user_id,
        backend_id=backend.id,
        order_id=order_id,
    )

    insert_automation_request(
        task_id=task_id,
        request_type=request_type,
        payload={"action": action, **payload},
    )

    task = invoke_action.apply_async(
        args=[backend_key, action],
        kwargs=queue_kwargs,
        queue=backend_key,
        task_id=task_id
    )

    # Response shape must remain the same
    return {
        "status": "scheduled",
        "task_id": task.id,
        **payload,
        "action": action,
    }


# ---- Routes ----

@app.post("/automation/create-account")
async def create_account(
    req: CreateAccountRequest,
    _: bool = Depends(require_app_key),
):
    return _enqueue_action(
        backend_key=req.backend,
        action="create-account",
        description="Initiate account creation",
        queue_kwargs={},
        request_type="create",
        payload=req.dict(),
        user_id=None,
    )


@app.post("/automation/recharge-account")
async def recharge_account(
    req: RechargeAccountRequest,
    x_order_id: Optional[str] = Header(None),
):
    if not x_order_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing order header")

    order = get_order(x_order_id)

    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")

    if not order.user:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User not found")

    if order.status != "finished":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Order not finished")

    if order.automation_status == "finished":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Already processed")

    automation_result = get_automation_result(x_order_id)
    if automation_result and automation_result.status == "pending":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Task for this order id is already running",
        )

    return _enqueue_action(
        backend_key=req.backend,
        action="recharge-account",
        description="Initiate account recharge",
        queue_kwargs={"account_id": req.account_id, "count": req.count, "order_id": x_order_id},
        request_type="recharge",
        payload=req.dict(),
        user_id=order.user.id,
        order_id=x_order_id,
    )


@app.post("/automation/withdraw-account")
async def withdraw_account(
    req: WithdrawAccountRequest,
):
    # Keep same behavior: no APP_KEY check and use redeem_id from payload
    return _enqueue_action(
        backend_key=req.backend,
        action="withdraw-account",
        description="Initiate account withdrawal",
        queue_kwargs={
            "account_id": req.account_id,
            "count": req.count,
            "redeem_request_id": req.redeem_id,
        },
        request_type="withdraw",
        payload=req.dict(),
        user_id=None,
    )


@app.post("/automation/read-account")
async def read_account(
    req: ReadAccountRequest,
    _: bool = Depends(require_app_key),
):
    return _enqueue_action(
        backend_key=req.backend,
        action="read-account",
        description="Initiate account read",
        queue_kwargs={"account_id": req.account_id},
        request_type="read",
        payload=req.dict(),
        user_id=None,
    )


@app.post("/automation/freeplay")
async def recharge_freeplay(
    req: RechargeFreeplayRequest,
):
    backend_account = get_backend_account(req.account_id)
    if not backend_account or not backend_account.user:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Account not found")

    user = backend_account.user
    t = req.type
    count: Optional[int] = None
    id_to_update: Optional[int] = None

    if t == "referral_freeplay":
        referral_bonus = get_referral_bonus(user.id)
        if referral_bonus.status != "processed":
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
        if not user.freeplay_amount:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "This user is not eligible for signup freeplay")
        count = user.freeplay_amount

    # count must be present by here
    return _enqueue_action(
        backend_key=req.backend,
        action="freeplay-account",
        description="Initiate freeplay transfer",
        queue_kwargs={
            "account_id": req.account_id,
            "count": int(count),
            "t": t,
            "id_to_update": id_to_update,
        },
        request_type="freeplay",
        payload=req.dict(),
        user_id=user.id,
    )

@app.post("/automation/reset-password")
async def reset_password(req: ResetPasswordRequest):
    backend_account = get_backend_account(req.account_id)
    if not backend_account or not backend_account.user:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Account not found")

    return _enqueue_action(
        backend_key=req.backend,
        action="reset-password",
        description="Initiate password reset",
        queue_kwargs={
            "account_id": req.account_id,
        },
        request_type="reset-password",
        payload=req.dict(),
        user_id=backend_account.user.id,
    )