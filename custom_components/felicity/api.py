# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
"""Minimal async client for the Felicity Open API."""
from __future__ import annotations

import asyncio
import base64
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_der_public_key

from .const import API, PUBLIC_KEY


def history_time(t: str | None) -> datetime | None:
    """History timestamps (and a live record's createTime) are Beijing time (UTC+8); returns an aware UTC datetime."""
    try:
        return (datetime.strptime((t or "").replace("T", " "), "%Y-%m-%d %H:%M:%S") - timedelta(hours=8)).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


# (voltage, current, power) of one record: their ratio tells whether the record's powers are in W or kW
_VI_PAIRS = (("emsVoltage", "emsCurrent", "emsPower"), ("acROutVolt", "acROutCurr", "acTotalOutAppaPower"),
             ("acRInVolt", "acRInCurr", "acRInPower"))


def is_power(key: str) -> bool:
    return "power" in key.lower() and "factor" not in key.lower()


def powers_in_kw(record: dict) -> bool | None:
    """Whether a record's powers are in kW, judged from a voltage × current pair (models and endpoints differ:
    the documentation lists kW for T-REX and W for IVGM). None: too little flow to tell."""
    for v, i, p in _VI_PAIRS:
        try:
            va, pw = abs(float(record[v]) * float(record[i])), abs(float(record[p]))
        except (KeyError, TypeError, ValueError):
            continue
        if va >= 300 and pw > 0:
            return pw / va < 0.05
    return None


def scale_powers(record: dict, factor: float) -> None:
    for k, v in record.items():
        if is_power(k):
            try:
                record[k] = float(v) * factor
            except (TypeError, ValueError):
                pass


class FelicityApiError(Exception):
    """The API (or the network on the way to it) failed."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class FelicityAuthError(FelicityApiError):
    """The account was rejected (wrong password, not activated)."""


TOKEN_EXPIRED = (998, 999)  # "the token has expired" / "you need to log in again"


def encrypt_password(password: str) -> str:
    key = load_der_public_key(base64.b64decode(PUBLIC_KEY))
    return base64.b64encode(key.encrypt(password.encode(), padding.PKCS1v15())).decode()


class FelicityClient:
    def __init__(self, session: aiohttp.ClientSession, user: str, password: str) -> None:
        self._session = session
        self._user = user
        self._password = password
        self._token: str | None = None
        self._refresh: str | None = None
        self._expires = 0.0
        self._lock = asyncio.Lock()

    async def _request(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None,
                       auth: bool = True, retry: bool = True) -> Any:
        headers = {"Lang": "en_US"}
        if auth:
            await self._ensure_token()
            headers["Authorization"] = self._token
        try:
            async with self._session.request(method, API + path, params=params, json=body, headers=headers,
                                             timeout=aiohttp.ClientTimeout(total=30)) as resp:
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:  # ValueError: an HTML error page instead of JSON
            raise FelicityApiError(f"{path}: {err!r}") from err
        code = data.get("code") if isinstance(data, dict) else None
        if auth and retry and code in TOKEN_EXPIRED:  # the server dropped the token before its stated expiry
            self._token = None
            return await self._request(method, path, params=params, body=body, retry=False)
        if code != 200:
            raise FelicityApiError(f"{path}: {data}", code)
        return data.get("data")

    async def login(self) -> None:
        try:
            d = await self._request("POST", "/openApi/sec/login", auth=False,
                                    body={"userName": self._user, "password": encrypt_password(self._password)})
        except FelicityApiError as err:
            if str(err.code).startswith("1002"):  # 1002006 wrong password, 1002001 not activated
                raise FelicityAuthError(str(err), err.code) from err
            raise
        self._store(d)

    def _store(self, d: dict) -> None:
        self._token = d["token"]
        self._refresh = d.get("refreshToken")
        self._expires = int(d.get("tokenExpireTime") or 0) / 1000

    async def _ensure_token(self) -> None:
        async with self._lock:  # the poll and the panel's views may need a new token at the same moment
            if self._token and time.time() < self._expires - 300:
                return
            if self._token and self._refresh:
                try:
                    self._store(await self._request("POST", "/openApi/sec/refreshToken", auth=False,
                                                    body={"refreshToken": self._refresh}))
                    return
                except FelicityApiError:
                    pass
            await self.login()

    async def devices(self) -> list[dict]:
        d = await self._request("GET", "/openApi/devices/list", params={"pageNum": 1, "pageSize": 50})
        return (d or {}).get("dataList") or []

    async def latest(self, sns: list[str]) -> list[dict]:
        d = await self._request("GET", "/openApi/data/devicesDataHistory",
                                params={"deviceSnList": ",".join(sns), "queryType": 0})
        if isinstance(d, list):
            return d
        return (d or {}).get("dataList") or []

    async def basic(self, sn: str) -> dict:
        return await self._request("GET", f"/openApi/data/deviceDataBasic/{sn}") or {}

    async def settings(self, sn: str) -> dict:
        """Current remote-control setting values (read only)."""
        return await self._request("GET", f"/openApi/cmd/deviceSetting/{sn}") or {}

    async def set_settings(self, sn: str, content: dict) -> dict:
        """Send a remote-control command; returns {'id': command id, 'deviceSn': sn}."""
        return await self._request("POST", "/openApi/cmd/deviceSetting", body={"deviceSn": sn, "content": content}) or {}

    async def settings_params(self, type_code: str, sub_type_code: str) -> list[dict]:
        """Definitions (name, range, unit, options) of the remote-control settings for a device type."""
        return await self._request("GET", "/openApi/cmd/deviceSetting/params",
                                   params={"typeCode": type_code, "subTypeCode": sub_type_code}) or []

    async def history(self, sn: str, date: str) -> list[dict]:
        """All realtime records of one device for a day (date = YYYY-MM-DD), oldest first. Pages are fetched in parallel."""
        async def page(n):
            return await self._request("GET", f"/openApi/data/deviceDataHistory/{sn}",
                                       params={"dateStr": f"{date} 00:00:00", "pageNum": n, "pageSize": 300}) or {}
        first = await page(1)
        out: list[dict] = list(first.get("dataList") or [])
        total = min(int(first.get("totalPage") or 1), 20)
        if total > 1:
            for d in await asyncio.gather(*(page(n) for n in range(2, total + 1))):
                out += d.get("dataList") or []
        out.sort(key=lambda r: r.get("deviceDataTime") or "")
        return out

    async def energy(self, sn: str, dimension: str, date: str) -> list[dict]:
        """deviceDataEnergy records; dimension: day | month | year | total, date: 'YYYY-MM-DD HH:MM:SS'."""
        out: list[dict] = []
        page = 1
        while True:
            d = await self._request("GET", "/openApi/data/deviceDataEnergy", params={
                "deviceSn": sn, "timeDimension": dimension, "dateStr": date, "pageNum": page, "pageSize": 300})
            rows = (d or {}).get("records") or []
            out += rows
            if page >= int((d or {}).get("pages") or 1) or not rows or page >= 20:
                break
            page += 1
        return out

    async def energy_today(self, sn: str, today: str) -> dict:
        """Today's totals (today = YYYY-MM-DD in the home's time zone); {} until the cloud has a record for it."""
        records = await self.energy(sn, "day", f"{today} 00:00:00")
        return next((r for r in records if str(r.get("dataTime", "")).startswith(today)), {})
