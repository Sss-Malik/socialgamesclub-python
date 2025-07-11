import importlib
import inspect
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, status, Header
from .schemas import CreateAccountRequest, RechargeAccountRequest, WithdrawAccountRequest, ReadAccountRequest, RechargeFreeplayRequest
from settings import APP_KEY
from .tasks import invoke_action

from common.utils.db_actions import get_order, insert_automation_result, get_backend_account

from celery_app import celery_app
from celery.result import AsyncResult

app = FastAPI(
    title="Casino Automation API",
    version="1.0.0",
    description="Run casino automation tasks via HTTP API."
)

def _invoke_action(backend: str, action: str, **kwargs):
    module_path = f"backends.{backend}.automation"
    try:
        mod = importlib.import_module(module_path)
    except ImportError:
        raise HTTPException(404, f"Backend '{backend}' not found")

    func_name = f"action_{action.replace('-', '_')}"
    if not hasattr(mod, func_name):
        raise HTTPException(404, f"Action '{action}' not in '{backend}'")

    func = getattr(mod, func_name)
    # signature & param validation
    sig = inspect.signature(func)
    call_args = {}
    for name, param in sig.parameters.items():
        if name in kwargs:
            call_args[name] = kwargs[name]
        else:
            raise HTTPException(400, f"Missing parameter '{name}' for action")

    # run the action
    return func(**call_args)

@app.post("/automation/create-account")
async def create_account(
    req: CreateAccountRequest,
    x_app_key: str = Header(None)
):
    if x_app_key != APP_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid APP_KEY"
        )
    task = invoke_action.delay(req.backend, "create-account")
    insert_automation_result(description="Initiate account creation", task_id=task.id)
    return {"status": "scheduled", "task_id": task.id, **req.dict(), "action": "create-account"}

@app.post("/automation/recharge-account")
async def recharge_account(
    req: RechargeAccountRequest,
    x_order_id: str = Header(None)
):

    order = get_order(x_order_id)

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found"
        )

    if order.status != "finished":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order status is not 'finished'"
        )

    if order.automation_status == "finished":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order automation_status is 'finished'"
        )

    task = invoke_action.delay(req.backend, "recharge-account", account_id=req.account_id, count=req.count, order_id=x_order_id)
    insert_automation_result(user_id=order.user.id, description="Initiate account recharge", task_id=task.id)
    return {"status": "scheduled", "task_id": task.id, **req.dict(), "action": "recharge-account"}

@app.post("/automation/withdraw-account")
async def withdraw_account(
        req: WithdrawAccountRequest,
        x_app_key: str = Header(None)
):

    if x_app_key != APP_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid APP_KEY"
        )

    task = invoke_action.delay(req.backend, "withdraw-account", account_id=req.account_id, count=req.count)
    insert_automation_result(task_id=task.id, description="Initiate account withdrawal")
    return {"status": "scheduled", "task_id": task.id, **req.dict(), "action": "withdraw-account"}


@app.post("/automation/read-account")
async def read_account(
        req: ReadAccountRequest,
        x_app_key: str = Header(None)
):
    if x_app_key != APP_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid APP_KEY"
        )

    task = invoke_action.delay(req.backend, "read-account", account_id=req.account_id)
    insert_automation_result(task_id=task.id, description="Initiate account read")
    return {"status": "scheduled", "task_id": task.id, **req.dict(), "action": "read-account"}


@app.post("/automation/freeplay")
async def recharge_freeplay(
        req: RechargeFreeplayRequest,
):
    backend_account = get_backend_account(req.account_id)

    if not backend_account or not backend_account.user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account not found"
        )

    user = backend_account.user

    if user.freeplay_transferred:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account already received freeplay"
        )

    # Handle freeplay_amount logic
    if user.freeplay_amount == 0:
        return {"status": "skipped", "reason": "Freeplay amount is 0"}

    count = user.freeplay_amount if user.freeplay_amount is not None else 1

    task = invoke_action.delay(
        req.backend,
        "freeplay-account",
        account_id=req.account_id,
        count=count
    )

    insert_automation_result(task_id=task.id, description="Initiate account freeplay", user_id=user.id)

    return {
        "status": "scheduled",
        "task_id": task.id,
        **req.dict(),
        "action": "freeplay-account"
    }

