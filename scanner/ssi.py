"""SSI FastConnect Data (official). Needs SSI_CONSUMER_ID / SSI_CONSUMER_SECRET secrets.
Docs: https://guide.ssi.com.vn/ssi-products/fastconnect-data/api-specs"""
from .net import Http, SourceError, pick, num, parse_date, find_list_of_dicts, is_stock_symbol

BASE = "https://fc-data.ssi.com.vn/api/v2/Market/"


class SSI:
    name = "SSI FastConnect"

    def __init__(self, consumer_id, consumer_secret, http=None):
        if not consumer_id or not consumer_secret:
            raise SourceError("SSI keys not set")
        self.cid, self.secret = consumer_id, consumer_secret
        self.http = http or Http(min_interval=1.05, timeout=30)
        self.token = None

    def _auth(self):
        js = self.http.post(BASE + "AccessToken",
                            json={"consumerID": self.cid, "consumerSecret": self.secret})
        tok = pick(js.get("data") or {}, "accessToken") if isinstance(js, dict) else None
        if not tok:
            raise SourceError("SSI login failed: " + str(pick(js, "message", default=""))[:80])
        self.token = tok
        self.http.s.headers["Authorization"] = "Bearer " + tok

    def _get(self, path, params):
        if not self.token:
            self._auth()
        js = self.http.get(BASE + path, params=params)
        status = str(pick(js, "status", default="200"))
        if status not in ("200", "Success", "success"):
            msg = str(pick(js, "message", default=""))
            if "token" in msg.lower() or status == "401":
                self._auth()
                js = self.http.get(BASE + path, params=params)
            else:
                raise SourceError(f"SSI status {status}")
        return js

    def daily_market(self, d, exchange):
        """All securities of one exchange for one day (dd/mm/yyyy). Returns canonical rows."""
        ds = d.strftime("%d/%m/%Y")
        rows, page = [], 1
        while page <= 10:
            js = self._get("DailyStockPrice", {"symbol": "", "market": exchange, "fromDate": ds,
                                               "toDate": ds, "pageIndex": page, "pageSize": 1000})
            data = js.get("data") if isinstance(js, dict) else None
            if not isinstance(data, list):
                data = find_list_of_dicts(js) or []
            for r in data:
                row = parse_daily_stock_price(r, exchange)
                if row:
                    rows.append(row)
            total = num(pick(js, "totalRecord"), 0) or 0
            if len(data) < 1000 or page * 1000 >= total:
                break
            page += 1
        return rows

    def symbol_history(self, symbol, start, end):
        """Up to 30 days per call per SSI docs; loops windows."""
        from datetime import timedelta
        out, a = [], start
        while a <= end:
            b = min(end, a + timedelta(days=29))
            js = self._get("DailyStockPrice", {"symbol": symbol, "market": "", "fromDate": a.strftime("%d/%m/%Y"),
                                               "toDate": b.strftime("%d/%m/%Y"), "pageIndex": 1, "pageSize": 100})
            data = js.get("data") if isinstance(js, dict) else None
            if not isinstance(data, list):
                data = find_list_of_dicts(js) or []
            for r in data:
                row = parse_daily_stock_price(r, None)
                if row:
                    out.append(row)
            a = b + timedelta(days=1)
        return out

    def listed_shares(self, exchange):
        out = {}
        for page in range(1, 11):
            js = self._get("SecuritiesDetails", {"market": exchange, "symbol": "", "pageIndex": page, "pageSize": 1000})
            items = find_list_of_dicts(js) or []
            for it in items:
                sym = str(pick(it, "Symbol", default="")).upper()
                if is_stock_symbol(sym):
                    out[sym] = {"listed_shares": num(pick(it, "ListedShare")),
                                "sec_type": pick(it, "SecType", default="")}
            if len(items) < 1000:
                break
        return out


def parse_daily_stock_price(r, exchange):
    sym = str(pick(r, "Symbol", default="")).upper().strip()
    if not is_stock_symbol(sym):
        return None
    d = parse_date(pick(r, "TradingDate"))
    close = num(pick(r, "ClosePrice"))
    if not d or not close:
        return None
    f_buy_val = num(pick(r, "ForeignBuyValTotal"))
    f_sell_val = num(pick(r, "ForeignSellValTotal"))
    net_val = num(pick(r, "NetBuySellVal"))
    if net_val is None and f_buy_val is not None and f_sell_val is not None:
        net_val = f_buy_val - f_sell_val
    vol = num(pick(r, "TotalMatchVol"), 0.0) or num(pick(r, "TotalTradedVol"), 0.0)
    val = num(pick(r, "TotalMatchVal"), 0.0) or num(pick(r, "TotalTradedValue"), 0.0)
    return {
        "date": d, "symbol": sym, "exchange": exchange or pick(r, "Market", default=""),
        "open": num(pick(r, "OpenPrice"), close), "high": num(pick(r, "HighestPrice"), close),
        "low": num(pick(r, "LowestPrice"), close), "close": close,
        "volume": vol, "value": val,
        "ref": num(pick(r, "RefPrice")), "ceiling": num(pick(r, "CeilingPrice")), "floor": num(pick(r, "FloorPrice")),
        "f_buy_vol": num(pick(r, "ForeignBuyVolTotal")), "f_sell_vol": num(pick(r, "ForeignSellVolTotal")),
        "f_buy_val": f_buy_val, "f_sell_val": f_sell_val, "f_net_val": net_val,
        "f_room": num(pick(r, "ForeignCurrentRoom")),
    }
