"""
Tradovate REST adapter — the execution path for MyFundedFutures, Tradeify and
other Tradovate-cleared prop accounts.                              [UNTESTED]

Written from Tradovate's public API reference (api.tradovate.com/#tag/Orders)
without an account to run against. What you must do before trusting it:
  1. Get API credentials from Tradovate (an API "app" gives you cid + sec;
     access requires the API add-on on your account — check the current fee
     and whether your prop firm enables it; some require a written request).
  2. Point BASE at the demo host first, run with qty=1, watch every fill.
  3. Confirm the contract symbol format your account expects ("MESZ6").

Endpoints used (all under {base}/v1):
  POST auth/accesstokenrequest    -> accessToken (expires; we renew per call batch)
  GET  account/list               -> [{id, name, ...}]
  GET  cashBalance/getcashbalancesnapshot?accountId=... -> {totalCashValue, realizedPnL, ...}
  GET  position/list              -> [{contractId, netPos, netPrice, ...}]
  GET  contract/find?name=MESZ6   -> {id, name}
  POST order/placeorder           -> {orderId}
  POST order/placeoso             -> bracket: entry + linked stop
  POST order/cancelorder          -> {}
  POST order/liquidateposition    -> {}
  GET  order/list                 -> [...]

Every order carries isAutomated=true (CME Rule 575 / firm fair-play policy).
"""
from __future__ import annotations
import os, time
import datetime as dt

import requests
import pandas as pd

import config as C
from broker.base import Broker, Position, OrderResult

DEMO = "https://demo.tradovateapi.com"
LIVE = "https://live.tradovateapi.com"


class TradovateBroker(Broker):
    name = "tradovate"

    def __init__(self, base: str | None = None, account_name: str | None = None):
        self.base = (base or os.environ.get("TRADOVATE_BASE", DEMO)).rstrip("/")
        self.creds = {
            "name": os.environ["TRADOVATE_USER"], "password": os.environ["TRADOVATE_PASS"],
            "appId": os.environ.get("TRADOVATE_APP_ID", "SPXBeaterFutures"),
            "appVersion": "1.0", "cid": int(os.environ["TRADOVATE_CID"]), "sec": os.environ["TRADOVATE_SEC"],
            "deviceId": os.environ.get("TRADOVATE_DEVICE_ID", "spxf-runner"),
        }
        self.token = None; self.token_exp = 0.0
        self.account_name = account_name or os.environ.get("TRADOVATE_ACCOUNT")
        self.account_id = None; self.account_spec = None
        self._contract_ids: dict[str, int] = {}

    # ---- plumbing --------------------------------------------------------
    def _auth(self):
        if self.token and time.time() < self.token_exp - 60:
            return
        r = requests.post(f"{self.base}/v1/auth/accesstokenrequest", json=self.creds, timeout=15)
        r.raise_for_status(); j = r.json()
        if "accessToken" not in j:
            raise RuntimeError(f"tradovate auth failed: {j}")
        self.token = j["accessToken"]
        self.token_exp = time.time() + 75 * 60
        if self.account_id is None:
            accts = self._get("account/list")
            acct = next((a for a in accts if not self.account_name or a["name"] == self.account_name), None)
            if not acct:
                raise RuntimeError(f"account {self.account_name!r} not found in {[a['name'] for a in accts]}")
            self.account_id = acct["id"]; self.account_spec = acct["name"]

    def _h(self):
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

    def _get(self, path, **params):
        self._auth()
        r = requests.get(f"{self.base}/v1/{path}", headers=self._h(), params=params, timeout=15)
        r.raise_for_status(); return r.json()

    def _post(self, path, body):
        self._auth()
        r = requests.post(f"{self.base}/v1/{path}", headers=self._h(), json=body, timeout=15)
        r.raise_for_status(); return r.json()

    def contract_id(self, symbol: str) -> int:
        if symbol not in self._contract_ids:
            j = self._get("contract/find", name=symbol)
            self._contract_ids[symbol] = j["id"]
        return self._contract_ids[symbol]

    # ---- Broker API -------------------------------------------------------
    def account(self) -> dict:
        j = self._get("cashBalance/getcashbalancesnapshot", accountId=self.account_id)
        real = float(j.get("realizedPnL", 0.0)); tot = float(j.get("totalCashValue", 0.0))
        return {"balance": tot, "day_pnl": real + float(j.get("openPnL", 0.0)), "equity": tot + float(j.get("openPnL", 0.0))}

    def position(self, symbol) -> Position:
        cid = self.contract_id(symbol)
        for p in self._get("position/list"):
            if p.get("contractId") == cid and p.get("accountId") == self.account_id:
                return Position(int(p.get("netPos", 0)), float(p.get("netPrice") or 0.0))
        return Position()

    def place_market(self, symbol, side, qty, tag) -> OrderResult:
        j = self._post("order/placeorder", {
            "accountSpec": self.account_spec, "accountId": self.account_id,
            "action": "Buy" if side > 0 else "Sell", "symbol": symbol, "orderQty": qty,
            "orderType": "Market", "isAutomated": True, "clOrdId": f"SPXF-{tag}"[:32]})
        return OrderResult(str(j.get("orderId", "")), "orderId" in j, str(j))

    def place_stop(self, symbol, side, qty, stop_px, tag) -> OrderResult:
        j = self._post("order/placeorder", {
            "accountSpec": self.account_spec, "accountId": self.account_id,
            "action": "Buy" if side > 0 else "Sell", "symbol": symbol, "orderQty": qty,
            "orderType": "Stop", "stopPrice": round(stop_px, 2), "isAutomated": True,
            "clOrdId": f"SPXF-stop-{tag}"[:32]})
        return OrderResult(str(j.get("orderId", "")), "orderId" in j, str(j))

    def place_bracket(self, symbol, side, qty, stop_px, tag) -> OrderResult:
        """Entry market order with a linked (OSO) protective stop — atomic, preferred."""
        j = self._post("order/placeoso", {
            "accountSpec": self.account_spec, "accountId": self.account_id,
            "action": "Buy" if side > 0 else "Sell", "symbol": symbol, "orderQty": qty,
            "orderType": "Market", "isAutomated": True,
            "bracket1": {"action": "Sell" if side > 0 else "Buy", "orderType": "Stop",
                         "stopPrice": round(stop_px, 2)}})
        return OrderResult(str(j.get("orderId", "")), "orderId" in j, str(j))

    def cancel_all(self, symbol) -> int:
        cid = self.contract_id(symbol); n = 0
        for o in self._get("order/list"):
            if o.get("contractId") == cid and o.get("ordStatus") in ("Working", "Suspended", "PendingNew"):
                self._post("order/cancelorder", {"orderId": o["id"], "isAutomated": True}); n += 1
        return n

    def resting_stop_qty(self, symbol) -> int:
        cid = self.contract_id(symbol); q = 0
        for o in self._get("order/list"):
            if o.get("contractId") == cid and o.get("ordStatus") == "Working" and o.get("orderType") in ("Stop", "StopLimit"):
                q += int(o.get("orderQty", 0))
        return q

    def flatten(self, symbol) -> OrderResult:
        self.cancel_all(symbol)
        j = self._post("order/liquidateposition", {"accountId": self.account_id,
                                                   "contractId": self.contract_id(symbol), "admin": False})
        return OrderResult("", True, str(j))

    def last_price(self, symbol) -> float:
        # Tradovate market data is websocket-only (md.tradovateapi.com); use the
        # last completed bar from your data feed instead of a REST quote.
        b = self.bars_today(symbol)
        return float(b["close"].iloc[-1]) if len(b) else 0.0

    def bars_today(self, symbol) -> pd.DataFrame:
        """Tradovate bars come over the websocket 'md/getChart' request. Until
        that client is written, the runner should be given an external 1-minute
        feed (Databento live, $32.65/mo) via live.runner --feed databento."""
        raise NotImplementedError("use an external 1-minute feed with the Tradovate adapter")
