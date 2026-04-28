import hashlib
import logging
import time
from typing import Any, Dict, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class GamevaultAPIError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(f"gamevault api error (code={code}): {msg}")
        self.code = code
        self.msg = msg


class GamevaultClient:
    DEFAULT_TIMEOUT = 30

    def __init__(
        self,
        agent_id: str,
        secret_key: str,
        base_url: str,
        logger: Optional[logging.Logger] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        if not agent_id or not secret_key or not base_url:
            raise ValueError(
                "GamevaultClient requires agent_id, secret_key, and base_url "
                "(seed backend_games.api_* columns for gamevault)."
            )
        self.agent_id = str(agent_id)
        self.secret_key = str(secret_key)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.logger = logger or logging.getLogger("casino_automation.gamevault.api")

        self.session = requests.Session()
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            backoff_factor=0.5,
            status_forcelist=(502, 503, 504),
            allowed_methods=frozenset(["POST"]),
            raise_on_status=False,
        )
        self.session.mount("http://", HTTPAdapter(max_retries=retry))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    @staticmethod
    def _sign(agent_id: str, timestamp: str, secret_key: str) -> str:
        raw = f"{agent_id}:{timestamp}:{secret_key}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _request(
        self, endpoint: str, form_data: Optional[Dict[str, Any]] = None
    ) -> Tuple[int, str, Dict[str, Any]]:
        timestamp = str(int(time.time()))
        token = self._sign(self.agent_id, timestamp, self.secret_key)

        payload: Dict[str, Any] = {
            "agent_id": self.agent_id,
            "timestamp": timestamp,
            "token": token,
        }
        if form_data:
            payload.update({k: v for k, v in form_data.items() if v is not None})

        files = {k: (None, str(v)) for k, v in payload.items()}

        url = f"{self.base_url}{endpoint}"
        self.logger.debug("gamevault api -> %s", endpoint)
        resp = self.session.post(url, files=files, timeout=self.timeout)
        resp.raise_for_status()

        body = resp.json()
        code = int(body.get("code", -1))
        msg = str(body.get("msg", ""))
        data = body.get("data") or {}
        if not isinstance(data, (dict, list)):
            data = {}
        self.logger.debug("gamevault api <- %s code=%s msg=%s", endpoint, code, msg)
        return code, msg, data if isinstance(data, dict) else {"_list": data}

    # --- typed endpoints ---------------------------------------------------

    def add_user(self, account: str, login_pwd: str):
        return self._request("/api/external/addUser", {"account": account, "login_pwd": login_pwd})

    def recharge(self, user_id: str, amount, order_id: str):
        return self._request(
            "/api/external/recharge",
            {"user_id": str(user_id), "amount": str(amount), "order_id": str(order_id)},
        )

    def withdraw(self, user_id: str, amount, order_id: str):
        return self._request(
            "/api/external/withdraw",
            {"user_id": str(user_id), "amount": str(amount), "order_id": str(order_id)},
        )

    def user_balance(self, user_id: str):
        return self._request("/api/external/userBalance", {"user_id": str(user_id)})

    def agent_balance(self):
        return self._request("/api/external/agentBalance", {})

    def get_user_id(self, account_name: str):
        return self._request("/api/external/getUserID", {"account_name": account_name})

    def reset_password(self, user_id: str, login_pwd: str):
        return self._request(
            "/api/external/resetPassword",
            {"user_id": str(user_id), "login_pwd": login_pwd},
        )

    def player_offline(self, user_id: str):
        return self._request("/api/external/playerOffline", {"user_id": str(user_id)})


def build_client_from_backend(backend, logger: Optional[logging.Logger] = None) -> GamevaultClient:
    return GamevaultClient(
        agent_id=backend.api_agent_id,
        secret_key=backend.api_secret_key,
        base_url=backend.api_base_url,
        logger=logger,
    )
