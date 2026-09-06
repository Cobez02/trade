"""
ProjectX gateway adapter — TopstepX (Topstep Combine / XFA).             [UNTESTED]

Written from the public gateway docs (gateway.docs.projectx.com) without an
account. Topstep sells API access as an add-on (~$14.50/mo with their code as
of Sept 2026). Their rules: bots allowed in the Combine and XFA, NOT in the
Live Funded account; VPNs banned; run from your own device.

Endpoints (base https://api.topstepx.com):
  POST /api/Auth/loginKey            {userName, apiKey}          -> {token}
  POST /api/Account/search           {onlyActiveAccounts: true}  -> {accounts:[{id,name,balance,...}]}
  POST /api/Contract/search          {searchText:"MES", live:false} -> {contracts:[{id,name,...}]}
  POST /api/Order/place              {accountId, contractId, type, side, size, stopPrice, customTag}
                                     type: 1=Limit 2=Market 4=Stop 5=TrailingStop ; side: 0=Bid(buy) 1=Ask(sell)
  POST /api/Order/cancel             {accountId, orderId}
  POST /api/Order/searchOpen         {accountId}
  POST /api/Position/searchOpen      {accountId}                 -> {positions:[{contractId,type,size,averagePrice}]}
  POST /api/Position/closeContract   {accountId, contractId}
  POST /api/History/retrieveBars     {contractId, live, startTime, endTime, unit:2(minute), unitNumber:1, limit, includePartialBar}
Real-time data is a SignalR hub (rtc.topstepx.com/hubs/market); polling
retrieveBars once a minute is enough for a 30-minute-bar strategy.
"""
from __future__ import annotations
import os, time
import datetime as dt

import requests
import pandas as pd

import config as C
from broker.base import Broker, Position, OrderResult
from data import sessions as S

BASE = os.environ.get("PROJECTX_BASE", "https://api.topstepx.com")


class ProjectXBroker(Broker):
    name = "projectx"

    def __init__(self, account_name: str | None = None):
        self.user = os.environ["PROJECTX_USER"]; self.key = os.environ["PROJECTX_API_KEY"]
        self.token = None; self.token_t = 0.0
        self.account_name = account_name or os.environ.get("PROJECTX_ACCOUNT")
        self.account_id = None
        self._cids: dict[str, str] = {}

    def _auth(self):
        if self.token and time.time() - self.token_t < 20 * 60:
            return
        r = requests.post(f"{BASE}/api/Auth/loginKey", json={"userName": self.user, "apiKey": self.key}, timeout=15)
        r.raise_for_status(); j = r.json()
        if not j.get("success"):
            raise RuntimeError(f"projectx auth failed: {j}")
        self.token = j["token"]; self.token_t = time.time()
        if self.account_id is None:
            accts = self._post("Account/search", {"onlyActiveAccounts": True}).get("accounts", [])
            a = next((x for x in accts if not self.account_name or x["name"] == self.account_name), None)
            if not a:
                raise RuntimeError(f"account {self.account_name!r} not found in {[x['name'] for x in accts]}")
            self.account_id = a["id"]

    def _post(self, path, body):
        self._auth()
        r = requests.post(f"{BASE}/api/{path}", json=body, timeout=15,
                          headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"})
        r.raise_for_status(); return r.json()

    def contract_id(self, symbol: str) -> str:
        if symbol not in self._cids:
            j = self._post("Contract/search", {"searchText": symbol, "live": False})
            cs = j.get("contracts", [])
            if not cs:
                raise RuntimeError(f"no contract for {symbol}")
            # prefer exact name match, else the first (front month)
            c = next((x for x in cs if x.get("name") == symbol), cs[0])
            self._cids[symbol] = c["id"]
        return self._cids[symbol]

    def account(self) -> dict:
        accts = self._post("Account/search", {"onlyActiveAccounts": True}).get("accounts", [])
        a = next((x for x in accts if x["id"] == self.account_id), {})
        bal = float(a.get("balance", 0.0))
        return {"balance": bal, "day_pnl": float(a.get("dailyPnL", 0.0)) if "dailyPnL" in a else 0.0, "equity": bal}

    def position(self, symbol) -> Position:
        cid = self.contract_id(symbol)
        for p in self._post("Position/searchOpen", {"accountId": self.account_id}).get("positions", []):
            if p.get("contractId") == cid:
                size = int(p.get("size", 0)); sign = 1 if p.get("type") == 1 else -1   # 1=Long 2=Short
                return Position(sign * size, float(p.get("averagePrice", 0.0)))
        return Position()

    def place_market(self, symbol, side, qty, tag) -> OrderResult:
        j = self._post("Order/place", {"accountId": self.account_id, "contractId": self.contract_id(symbol),
                                       "type": 2, "side": 0 if side > 0 else 1, "size": qty,
                                       "customTag": f"SPXF-{tag}-{int(time.time())}"[:64]})
        return OrderResult(str(j.get("orderId", "")), bool(j.get("success")), str(j))

    def place_stop(self, symbol, side, qty, stop_px, tag) -> OrderResult:
        j = self._post("Order/place", {"accountId": self.account_id, "contractId": self.contract_id(symbol),
                                       "type": 4, "side": 0 if side > 0 else 1, "size": qty,
                                       "stopPrice": round(stop_px, 2),
                                       "customTag": f"SPXF-stop-{tag}-{int(time.time())}"[:64]})
        return OrderResult(str(j.get("orderId", "")), bool(j.get("success")), str(j))

    def cancel_all(self, symbol) -> int:
        cid = self.contract_id(symbol); n = 0
        for o in self._post("Order/searchOpen", {"accountId": self.account_id}).get("orders", []):
            if o.get("contractId") == cid:
                self._post("Order/cancel", {"accountId": self.account_id, "orderId": o["id"]}); n += 1
        return n

    def resting_stop_qty(self, symbol) -> int:
        cid = self.contract_id(symbol); q = 0
        for o in self._post("Order/searchOpen", {"accountId": self.account_id}).get("orders", []):
            if o.get("contractId") == cid and o.get("type") in (4, 5):
                q += int(o.get("size", 0))
        return q

    def flatten(self, symbol) -> OrderResult:
        self.cancel_all(symbol)
        j = self._post("Position/closeContract", {"accountId": self.account_id, "contractId": self.contract_id(symbol)})
        return OrderResult("", bool(j.get("success", True)), str(j))

    def last_price(self, symbol) -> float:
        b = self.bars_today(symbol)
        return float(b["close"].iloc[-1]) if len(b) else 0.0

    def bars_today(self, symbol) -> pd.DataFrame:
        o, _, _, _ = S.session_bounds(S.now_ct().date())
        j = self._post("History/retrieveBars", {
            "contractId": self.contract_id(symbol), "live": False,
            "startTime": o.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "endTime": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "unit": 2, "unitNumber": 1, "limit": 500, "includePartialBar": False})
        rows = j.get("bars", [])
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(rows).rename(columns={"t": "ts", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        return df.set_index("ts")[["open", "high", "low", "close", "volume"]].astype(float).sort_index()
