import importlib
import inspect
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, status, Header
from .schemas import CreateAccountRequest, RechargeAccountRequest, WithdrawAccountRequest, ReadAccountRequest
from settings import APP_KEY
from .tasks import invoke_action

from common.utils.db_actions import get_order

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
    return {"status": "scheduled", "task_id": task.id, **req.dict(), "action": "read-account"}


@app.post("/automation/results")
async def receive_webhook(request: Request):
    """
    A testing endpoint to receive and log webhook payloads from automation jobs.
    """
    payload = await request.json()
    print("📬 Webhook received:\n%s", payload)

    # For testing purposes, return the same payload back
    return {
        "status": "received",
        "received_payload": payload
    }

@app.get("/automation/tasks/{task_id}")
async def get_task_status(task_id: str):
    """
    Retrieve status and (if ready) result or error of a previously‐scheduled Celery task.
    """
    async_result = AsyncResult(task_id, app=celery_app)

    payload = {
        "task_id": task_id,
        "status": async_result.status,  # e.g. PENDING, STARTED, SUCCESS, FAILURE
    }

    if async_result.status == "SUCCESS":
        payload["result"] = async_result.result
    elif async_result.status == "FAILURE":
        # celery stores the exception instance in .result
        payload["error"] = str(async_result.result)

    return payload