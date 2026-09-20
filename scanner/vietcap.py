"""Vietcap (VCI) public web endpoints - the same ones the free `vnstock` library uses.
No account needed. Unofficial: they can change without notice, so every parser is tolerant."""
from datetime import datetime, timedelta, timezone
from .net import Http, SourceError, pick, num, parse_date, find_list_of_dicts, is_stock_symbol, lower_keys

TRADING = "https://trading.vietcap.com.vn/api/"
IQ = "https://iq.vietcap.com.vn/api/iq-insight-service"


def fix_scale(price):
    """Vietcap sometimes returns prices in thousand VND."""
    if price is None:
        return None
    return price * 1000 if 0 < price < 500 else price


def fix_value(value, volume, price):
    """Normalise traded value to VND whatever unit the API used."""
    if not volume or not price:
        return value or 0.0
    est = volume * price
    if not value:
        return est
    ratio = est / value
    for f in (1e9, 1e6, 1e3):
        if ratio > f * 0.3:
            return value * f
    return value


class Vietcap:
    name = "Vietcap (free)"

    def __init__(self, http=None, iq_http=None):
        self.http = http or Http(referer="https://trading.vietcap.com.vn/", min_interval=0.35)
        self.iq = iq_http or Http(referer="https://trading.vietcap.com.vn/", min_interval=0.35)

    # ---------- listing ----------
    def listing(self):
        js = self.http.get(TRADING + "price/symbols/getAll")
        items = js if isinstance(js, list) else (find_list_of_dicts(js) or [])
        out = {}
        for it in items:
            sym = str(pick(it, "symbol", default="")).upper()
            typ = str(pick(it, "type", default="STOCK")).upper()
            board = str(pick(it, "board", "exchange", default="")).upper().replace("HSX", "HOSE")
            if is_stock_symbol(sym) and typ == "STOCK" and board in ("HOSE", "HNX", "UPCOM"):
                out[sym] = {"exchange": board, "name": pick(it, "organName", "organShortName", default=sym)}
        if not out:
            raise SourceError("empty listing")
        return out

    def sectors(self):
        """symbol -> sector name (ICB level 2/3 where available)."""
        js = self.iq.get(IQ + "/v2/company/search-bar?language=1")
        out = {}
        for c in (js.get("data") or []) if isinstance(js, dict) else []:
            sym = str(pick(c, "code", default="")).upper()
            sec = pick(c, "icbLv2", "icbLv3", "icbLv1", "sector")
            if isinstance(sec, dict):
                sec = pick(sec, "name", "enName", "code")
            if is_stock_symbol(sym) and sec:
                out[sym] = str(sec)
        return out

    # ---------- prices ----------
    def history(self, symbols, days=130, end=None, batch=40):
        """Daily OHLCV for many symbols (no foreign data). Returns canonical rows."""
        end = end or datetime.now(timezone.utc)
        to_ts = int((end + timedelta(days=1)).timestamp())
        rows = []
        symbols = list(symbols)
        for i in range(0, len(symbols), batch):
            chunk = symbols[i:i + batch]
            try:
                js = self.http.post(TRADING + "chart/OHLCChart/gap-chart",
                                    json={"timeFrame": "ONE_DAY", "symbols": chunk, "to": to_ts, "countBack": days})
            except SourceError:
                if batch > 1:          # server may not accept big batches: retry smaller (40 -> 5 -> 1)
                    rows += self.history(chunk, days, end, batch=max(1, batch // 8))
                continue
            data = js if isinstance(js, list) else (js.get("data") if isinstance(js, dict) else []) or []
            for k, blk in enumerate(data):
                if not isinstance(blk, dict):
                    continue
                sym = str(pick(blk, "symbol", default=chunk[k] if k < len(chunk) else "")).upper()
                rows += parse_gap_chart(sym, blk)
        return rows

    def board(self, symbols, batch=100):
        """Today's price board incl. foreign buy/sell and room. Returns canonical rows (date = today)."""
        rows, status = [], {}
        symbols = list(symbols)
        for i in range(0, len(symbols), batch):
            js = self.http.post(TRADING + "price/symbols/getList", json={"symbols": symbols[i:i + batch]})
            for it in (js if isinstance(js, list) else find_list_of_dicts(js) or []):
                row, st = parse_board_item(it)
                if row:
                    rows.append(row)
                    if st:
                        status[row["symbol"]] = st
        return rows, status

    # ---------- company ----------
    def news(self, symbol, days=60):
        f = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        t = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")
        out = []
        try:
            js = self.iq.get(f"{IQ}/v1/news?ticker={symbol}&fromDate={f}&toDate={t}&languageId=1&page=0&size=40")
            for n in find_list_of_dicts(js) or []:
                title = pick(n, "newsTitle", "title", "newsShortContent", default="")
                d = parse_date(pick(n, "publicDate", "publishDate", "displayDate1", "createdDate", "updateDate"))
                if title:
                    out.append({"date": d, "title": str(title).strip(), "kind": "news"})
        except SourceError:
            pass
        try:
            js = self.iq.get(f"{IQ}/v1/events?ticker={symbol}&fromDate={f}&toDate={t}"
                             "&eventCode=DIV,ISS,DDIND,DDINS,DDRP,AGME,EGME,AIS,SUSP,MOVE,OTHE&page=0&size=40")
            for n in find_list_of_dicts(js) or []:
                title = pick(n, "eventTitle", "eventNameVi", "title", "eventName", default="")
                code = pick(n, "eventCode", "eventListCode", default="")
                d = parse_date(pick(n, "publicDate", "displayDate1", "exrightDate", "recordDate", "issueDate"))
                if title or code:
                    out.append({"date": d, "title": f"{title} [{code}]".strip(), "kind": "event", "code": str(code)})
        except SourceError:
            pass
        return out

    def fundamentals(self, symbol):
        out = {}
        js = self.iq.get(f"{IQ}/v1/company/{symbol}/statistics-financial")
        rows = find_list_of_dicts(js) or ([js.get("data")] if isinstance(js, dict) and isinstance(js.get("data"), dict) else [])
        if rows:
            # most recent period first if sortable
            def key(r):
                return (num(pick(r, "year", "yearReport"), 0), num(pick(r, "quarter", "lengthReport"), 0))
            rows = sorted(rows, key=key, reverse=True)
            r0 = rows[0]
            out["pe"] = num(pick(r0, "pe", "priceToEarning", "p_e"))
            out["pb"] = num(pick(r0, "pb", "priceToBook"))
            out["roe"] = num(pick(r0, "roe"))
            g = num(pick(r0, "netProfitGrowth", "npatGrowth", "profitGrowth", "postTaxProfitGrowth", "epsGrowth"))
            if g is None and len(rows) >= 5:
                # YoY: compare same quarter one year earlier
                e0, e4 = num(pick(rows[0], "eps", "netProfit")), num(pick(rows[4], "eps", "netProfit"))
                if e0 is not None and e4 not in (None, 0):
                    g = (e0 - e4) / abs(e4)
            if g is not None and abs(g) > 2:   # looks like percent (e.g. 25.0) rather than a fraction
                g = g / 100
            out["profit_growth"] = g
            out["market_cap"] = num(pick(r0, "marketCap", "marketCapital"))
        try:
            d = self.iq.get(f"{IQ}/v1/company/details?ticker={symbol}")
            dd = d.get("data") if isinstance(d, dict) else None
            if isinstance(dd, dict):
                out.setdefault("market_cap", None)
                out["market_cap"] = out["market_cap"] or num(pick(dd, "marketCap", "marketCapital"))
                out["sector"] = pick(dd, "sectorVn", "sector", "icbNameLv2")
                out["listed_shares"] = num(pick(dd, "issueShare", "listedShare", "outstandingShare"))
        except SourceError:
            pass
        mc = out.get("market_cap")
        if mc and mc < 1e7:        # given in billions
            out["market_cap"] = mc * 1e9
        return out


def parse_gap_chart(sym, blk):
    t, o, h, l, c, v = (blk.get(k) or [] for k in ("t", "o", "h", "l", "c", "v"))
    rows = []
    for i in range(min(len(t), len(c))):
        close = fix_scale(num(c[i]))
        if not close:
            continue
        d = parse_date(num(t[i]))
        vol = num(v[i], 0.0) if i < len(v) else 0.0
        rows.append({"date": d, "symbol": sym,
                     "open": fix_scale(num(o[i])) if i < len(o) else close,
                     "high": fix_scale(num(h[i])) if i < len(h) else close,
                     "low": fix_scale(num(l[i])) if i < len(l) else close,
                     "close": close, "volume": vol, "value": vol * close})
    return rows


def parse_board_item(it):
    li = it.get("listingInfo") or {}
    mp = it.get("matchPrice") or {}
    flat = {**lower_keys(li), **lower_keys(mp)}
    sym = str(pick(li, "symbol", "code", default=pick(mp, "symbol", default=""))).upper()
    if not is_stock_symbol(sym):
        return None, None
    close = fix_scale(num(pick(mp, "matchPrice", "price", "lastPrice", "closePrice")))
    ref = fix_scale(num(pick(li, "refPrice", "referencePrice", "reference")))
    close = close or ref
    if not close:
        return None, None
    vol = num(pick(mp, "accumulatedVolume", "totalVolume", "totalMatchVolume"), 0.0)
    val = fix_value(num(pick(mp, "accumulatedValue", "totalValue", "totalMatchValue"), 0.0), vol, close)
    fbv = num(pick(mp, "foreignBuyVolume", "foreignBuyVol"), 0.0)
    fsv = num(pick(mp, "foreignSellVolume", "foreignSellVol"), 0.0)
    fbval = num(pick(mp, "foreignBuyValue", "foreignBuyVal"))
    fsval = num(pick(mp, "foreignSellValue", "foreignSellVal"))
    fbval = fix_value(fbval, fbv, close) if fbval else fbv * close
    fsval = fix_value(fsval, fsv, close) if fsval else fsv * close
    ex = str(pick(li, "board", "exchange", "floor", default="")).upper().replace("HSX", "HOSE")
    row = {"date": datetime.now(timezone(timedelta(hours=7))).date().isoformat(), "symbol": sym, "exchange": ex,
           "open": fix_scale(num(pick(mp, "openPrice", "open"))) or close,
           "high": fix_scale(num(pick(mp, "highest", "highestPrice", "high"))) or close,
           "low": fix_scale(num(pick(mp, "lowest", "lowestPrice", "low"))) or close,
           "close": close, "volume": vol, "value": val, "ref": ref,
           "ceiling": fix_scale(num(pick(li, "ceiling", "ceilingPrice"))),
           "floor": fix_scale(num(pick(li, "floor", "floorPrice"))),
           "f_buy_vol": fbv, "f_sell_vol": fsv, "f_buy_val": fbval, "f_sell_val": fsval,
           "f_net_val": fbval - fsval,
           "f_room": num(pick(mp, "currentRoom", "foreignRoom", "remainRoom", "foreignCurrentRoom"),
                         num(pick(li, "currentRoom", "foreignRoom"))),
           "listed_shares": num(pick(li, "listedShare", "listedShares", "issueShare", "totalListedShare"))}
    status = {k: v for k, v in flat.items() if ("status" in k or "halt" in k or "warning" in k) and v not in (None, "")}
    return row, status
