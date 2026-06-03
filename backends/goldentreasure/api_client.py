"""
Golden Treasure agent API client.

Pure-HTTP implementation (no browser) of the agent portal at
https://agent.goldentreasure.mobi. The full reverse-engineering spec this
implements lives at:

    /Applications/development/goldentreasure-standalone/goldentreasure_api_findings.md

Crypto summary (all verified against the live server):
  - Every request body is signed: sign = MD5(<sorted non-empty values> + stime + SECRET).
  - Login username/password are AES-128-ECB encrypted, key = "123" + stime + "abc".
  - Authenticated requests require x-token + x-time headers; x-token is the
    AES-128-ECB encryption of the session token keyed by "xtu" + x-time.
  - The host is behind Cloudflare and needs a full browser header set.

Error handling:
  - code 20000 == success.
  - codes -3 / -17 / 52 mean the session is dead → re-login transparently, retry once.
  - code 167 is rate limiting → sleep and retry a few times.
"""

import base64
import hashlib
import json
import logging
import time
import urllib.parse
from typing import Any, Dict, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad


class GoldenTreasureAPIError(Exception):
    def __init__(self, code: Any, message: str):
        super().__init__(f"goldentreasure api error (code={code}): {message}")
        self.code = code
        self.message = message


# Response envelope: the front-end checks `20000 === code`.
SUCCESS_CODE = 20000

# Session is dead — re-login and retry once.
_AUTH_FAIL_CODES = {-3, -17, 52}

# Rate limit — back off and retry.
_RATE_LIMIT_CODE = 167


# Verified Cloudflare-passing header set (spec §2 / §9). The sec-ch-ua values
# are matched to the Chrome 148 User-Agent — keep them consistent if changed.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)
_SEC_CH_UA = '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"'

# Any stable 32-hex string works; the server does not strictly validate it (spec §6).
_X_FINGERPRINT = "db3bb59096022abb85b4612d53387101"


class GoldenTreasureClient:
    DEFAULT_TIMEOUT = 30
    _RATE_LIMIT_MAX_RETRIES = 3

    def __init__(
        self,
        *,
        base_url: str,
        origin: str,
        sign_secret: str,
        username: str,
        password: str,
        rate_limit_delay: int = 5,
        logger: Optional[logging.Logger] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        if not base_url or not origin or not sign_secret or not username or not password:
            raise ValueError(
                "GoldenTreasureClient requires base_url, origin, sign_secret, "
                "username, and password."
            )
        self.base_url = base_url.rstrip("/")
        self.origin = origin.rstrip("/")
        self.sign_secret = sign_secret
        self.username = username
        self.password = password
        self.rate_limit_delay = rate_limit_delay
        self.timeout = timeout
        self.logger = logger or logging.getLogger("casino_automation.goldentreasure.api")

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

        self._token: Optional[str] = None

    # -- crypto primitives -------------------------------------------------

    @staticmethod
    def _md5(s: str) -> str:
        return hashlib.md5(s.encode("utf-8")).hexdigest()

    @staticmethod
    def _aes_b64(plaintext: str, key: str) -> str:
        """AES-128-ECB / PKCS7, key used as raw 16 ASCII bytes -> base64."""
        cipher = AES.new(key.encode("utf-8"), AES.MODE_ECB)
        return base64.b64encode(cipher.encrypt(pad(plaintext.encode("utf-8"), 16))).decode()

    def _sign(self, body: Dict[str, Any]) -> Tuple[str, int]:
        """Return (sign, stime) per spec §3.

        Concatenate every non-empty value (except `stime`) in ASCII-sorted
        key order, append stime and the shared secret, then MD5.
        """
        stime = body.get("stime") or int(time.time())
        concat = "".join(
            str(body[k])
            for k in sorted(body)
            if k != "stime" and body[k] not in ("", None)
        )
        return self._md5(concat + str(stime) + self.sign_secret), stime

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        h = {
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": self.origin,
            "Referer": self.origin + "/",
            "User-Agent": _USER_AGENT,
            "sec-ch-ua": _SEC_CH_UA,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "x-fingerprint": _X_FINGERPRINT,
        }
        if extra:
            h.update(extra)
        return h

    # -- transport ---------------------------------------------------------

    def _post(self, path: str, body: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        body = dict(body)
        sign, stime = self._sign(body)
        body["sign"], body["stime"] = sign, stime
        # The front-end serializes with no spaces.
        raw = json.dumps(body, separators=(",", ":")).encode("utf-8")

        self.logger.debug("goldentreasure api -> %s", path)
        resp = self.session.post(
            self.base_url + path, data=raw, headers=headers, timeout=self.timeout,
        )
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            raise GoldenTreasureAPIError(
                resp.status_code,
                f"non-json response from {path}: {resp.text[:200]}",
            )

    # -- auth --------------------------------------------------------------

    def login(self) -> Dict[str, Any]:
        """POST /api/user/login. Stores the session token on success."""
        stime = int(time.time())
        key = "123" + str(stime) + "abc"  # exactly 16 chars -> AES-128
        body = {
            "username": self._aes_b64(self.username.strip(), key),
            "password": self._aes_b64(self.password, key),
            "stime": stime,  # MUST equal the AES-key timestamp
            "auth_code": "",
        }
        r = self._post("/user/login", body, self._headers())
        code = r.get("code")
        if code != SUCCESS_CODE:
            raise GoldenTreasureAPIError(code, str(r.get("message", "login failed")))
        token = r.get("token")
        if not token:
            raise GoldenTreasureAPIError(code, "login response missing token")
        self._token = str(token)
        self.logger.info("goldentreasure: login successful")
        return r

    def ensure_token(self) -> None:
        if not self._token:
            self.login()

    def _auth(
        self,
        path: str,
        params: Dict[str, Any],
        *,
        _retry_auth: bool = True,
        _rate_limit_attempt: int = 0,
    ) -> Tuple[Any, str, Dict[str, Any]]:
        """Authenticated call: adds body `token` plus x-token / x-time headers.

        Transparently re-logs-in once on auth-failure codes and retries with
        backoff on rate-limit code 167.
        """
        self.ensure_token()

        body = dict(params, token=self._token)
        xtime = int(time.time() * 1000)  # 13-digit millisecond epoch
        xtoken = self._aes_b64(self._token, "xtu" + str(xtime))  # 16-char key
        headers = self._headers({
            "x-token": urllib.parse.quote(xtoken, safe=""),
            "x-time": str(xtime),
        })

        r = self._post(path, body, headers)
        code = r.get("code")
        msg = str(r.get("message", ""))

        self.logger.debug("goldentreasure api <- %s code=%s msg=%s", path, code, msg)

        if code in _AUTH_FAIL_CODES and _retry_auth:
            self.logger.warning(
                "goldentreasure: auth code %s on %s; re-logging in and retrying once.",
                code, path,
            )
            self._token = None
            self.login()
            return self._auth(path, params, _retry_auth=False,
                              _rate_limit_attempt=_rate_limit_attempt)

        if code == _RATE_LIMIT_CODE and _rate_limit_attempt < self._RATE_LIMIT_MAX_RETRIES:
            wait = self.rate_limit_delay * (_rate_limit_attempt + 1)
            self.logger.warning(
                "goldentreasure: rate limited (167) on %s; sleeping %ss before retry %d/%d.",
                path, wait, _rate_limit_attempt + 1, self._RATE_LIMIT_MAX_RETRIES,
            )
            time.sleep(wait)
            return self._auth(path, params, _retry_auth=_retry_auth,
                              _rate_limit_attempt=_rate_limit_attempt + 1)

        return code, msg, r

    # -- typed endpoints ---------------------------------------------------

    def agent_balance(self) -> Tuple[Any, str, Dict[str, Any]]:
        """POST /api/user/CurScore → body carries 'LimitNum'."""
        return self._auth("/user/CurScore", {})

    def create_player(
        self,
        *,
        account: str,
        pwd: str,
        score: Any = "0",
        name: str = "",
        phone: str = "",
        tel_area_code: str = "",
        remark: str = "",
    ) -> Tuple[Any, str, Dict[str, Any]]:
        """POST /api/account/savePlayer. `pwd` is sent in plaintext."""
        return self._auth("/account/savePlayer", {
            "account": account,
            "pwd": pwd,
            "score": str(score),
            "name": name,
            "phone": phone,
            "tel_area_code": tel_area_code,
            "remark": remark,
        })

    def player_balance(self, account: str) -> Tuple[Any, str, Dict[str, Any]]:
        """POST /api/account/getPlayerScore → body carries 'curScore'."""
        return self._auth("/account/getPlayerScore", {"account": account})

    def list_players(self) -> Tuple[Any, str, Dict[str, Any]]:
        """POST /api/account/getPlayerList → body carries 'playerList'."""
        return self._auth("/account/getPlayerList", {})

    def enter_score(
        self,
        *,
        account: str,
        score: int,
        remark: str = "",
        user_type: str = "player",
    ) -> Tuple[Any, str, Dict[str, Any]]:
        """POST /api/account/enterScore — positive recharges, negative withdraws."""
        return self._auth("/account/enterScore", {
            "account": account,
            "score": str(score),
            "remark": remark,
            "user_type": user_type,
        })

    def recharge(self, *, account: str, amount: int, remark: str = "") -> Tuple[Any, str, Dict[str, Any]]:
        return self.enter_score(account=account, score=abs(int(amount)), remark=remark)

    def withdraw(self, *, account: str, amount: int, remark: str = "") -> Tuple[Any, str, Dict[str, Any]]:
        return self.enter_score(account=account, score=-abs(int(amount)), remark=remark)

    def reset_password(
        self,
        *,
        account: str,
        pwd: str,
        name: str = "",
        phone: str = "",
        remark: str = "",
        tel_area_code: str = "",
    ) -> Tuple[Any, str, Dict[str, Any]]:
        """POST /api/account/updatePlayer. `pwd` is sent in plaintext."""
        return self._auth("/account/updatePlayer", {
            "account": account,
            "pwd": pwd,
            "name": name,
            "phone": phone,
            "remark": remark,
            "tel_area_code": tel_area_code,
        })


def build_client_from_backend(backend, logger: Optional[logging.Logger] = None) -> GoldenTreasureClient:
    """Construct a GoldenTreasureClient from a BackendGame row.

    Prefers DB-seeded values; falls back to config constants when absent.
    """
    from backends.goldentreasure.config import (
        API_BASE_URL,
        ORIGIN,
        SIGN_SECRET,
        USERNAME,
        PASSWORD,
        RATE_LIMIT_DELAY_SECONDS,
    )

    return GoldenTreasureClient(
        base_url=backend.api_base_url or API_BASE_URL,
        origin=ORIGIN,
        sign_secret=SIGN_SECRET,
        username=backend.username or USERNAME,
        password=backend.password or PASSWORD,
        rate_limit_delay=RATE_LIMIT_DELAY_SECONDS,
        logger=logger,
    )
