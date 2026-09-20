"""Realistic mock market + fake HTTP that returns the same raw JSON shapes the real
sources return (SSI FastConnect, Vietcap, CafeF). Lets us test the whole pipeline offline."""
import math
import random
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs

from scanner.calendar_vn import trading_days_back, is_trading_day

VN = timezone(timedelta(hours=7))
BAND = {"HOSE": 0.07, "HNX": 0.10, "UPCOM": 0.15}


def tick(p, ex):
    t = 100 if ex != "HOSE" else (10 if p < 10000 else 50 if p < 50000 else 100)
    return round(p / t) * t


class Clock:
    def __init__(self, now):
        self.now = now


class Market:
    """Generates daily bars with foreign/prop flows. Scenario stocks have scripted behaviour."""

    def __init__(self, end=date(2026, 10, 16), n_days=220, seed=7):
        self.rng = random.Random(seed)
        self.days = trading_days_back(end, n_days)
        self.pick_day = date(2026, 9, 18)   # the as-of session for the first morning scan (Fri)
        self.stocks = {}
        self.bars = {}
        self.news = {}
        self._make_universe()
        for s in self.stocks:
            self._simulate(s)

    def _make_universe(self):
        r = self.rng
        letters = "ABCDEFGHIKLMNPQRSTUVX"
        n = {"HOSE": 90, "HNX": 40, "UPCOM": 40}
        used = set()
        scen = {"AAA": ("HOSE", "accum_breakout"), "FFF": ("HNX", "good_then_fail"), "BBB": ("UPCOM", "pump"),
                "CCC": ("HOSE", "warned"), "DDD": ("HOSE", "foreign_dump"), "EEE": ("HOSE", "illiquid"),
                "GGG": ("HOSE", "overextended"), "HHH": ("HOSE", "suspended"), "KKK": ("HOSE", "steady_up")}
        for s, (ex, sc) in scen.items():
            used.add(s)
            self.stocks[s] = {"exchange": ex, "scenario": sc, "price": r.uniform(20000, 60000),
                              "avg_value": 60e9 if sc not in ("illiquid", "pump") else (2e9 if sc == "illiquid" else 25e9),
                              "listed": 400e6 if sc != "pump" else 20e6, "sector": "Công nghiệp", "pe": 11, "growth": 0.3}
        self.stocks["BBB"]["price"] = 12000
        for ex, k in n.items():
            for _ in range(k):
                while True:
                    s = "".join(r.choice(letters) for _ in range(3))
                    if s not in used:
                        break
                used.add(s)
                val = math.exp(r.gauss(math.log(15e9), 1.3))
                price = math.exp(r.gauss(math.log(25000), 0.7))
                self.stocks[s] = {"exchange": ex, "scenario": "random", "price": price, "avg_value": val,
                                  "listed": max(10e6, val * 250 / price * r.uniform(0.5, 3)),
                                  "sector": r.choice(["Ngân hàng", "Bất động sản", "Thép", "Công nghiệp", "Bán lẻ"]),
                                  "pe": r.uniform(5, 30), "growth": r.gauss(0.1, 0.3)}

    def _simulate(self, s):
        info = self.stocks[s]
        r = random.Random(sum(ord(c) * 131 ** k for k, c in enumerate(s)))
        ex, sc = info["exchange"], info["scenario"]
        band = BAND[ex]
        p = info["price"]
        bars = []
        pi = self.days.index(self.pick_day)
        for i, d in enumerate(self.days):
            ref = p
            k = i - pi   # days relative to pick day
            drift, vol_mult, f_bias = 0.0, 1.0, 0.0
            sigma = 0.018
            if sc == "accum_breakout":
                drift = 0.002 if k < -25 else 0.0005
                f_bias = 0.08 if -8 <= k <= 0 else 0.0
                if -25 <= k < 0:
                    sigma = 0.008
                if k == 0:
                    drift, vol_mult, sigma = 0.045, 2.6, 0.002
                if 1 <= k <= 8:
                    drift, sigma, f_bias = 0.022, 0.01, 0.05
            elif sc == "good_then_fail":
                drift = 0.002 if k < -20 else 0.0005
                f_bias = 0.06 if -6 <= k <= 0 else 0
                sigma = 0.01 if -20 <= k <= 0 else 0.018
                if k == 0:
                    drift, vol_mult, sigma = 0.05, 2.2, 0.002
                if 1 <= k <= 6:
                    drift, sigma, f_bias = -0.03, 0.01, -0.05
            elif sc == "steady_up":
                drift, sigma, f_bias = 0.003, 0.012, 0.03
            elif sc == "pump":
                if -3 <= k <= 0:
                    drift, vol_mult, sigma = band, 5, 0.0
            elif sc == "overextended":
                if -9 <= k <= 0:
                    drift, vol_mult, sigma = 0.04, 1.8, 0.006
            elif sc == "foreign_dump":
                drift, f_bias = -0.004, -0.12 if k > -10 else -0.02
            chg = drift + r.gauss(0, sigma)
            chg = max(-band * 0.99, min(band, chg))
            close = tick(ref * (1 + chg), ex)
            ceil_, floor_ = tick(ref * (1 + band) - 49, ex), tick(ref * (1 - band) + 49, ex)
            close = min(max(close, floor_), ceil_)
            if sc == "pump" and -3 <= k <= 0:
                close = ceil_
            op = tick(ref * (1 + r.gauss(0, 0.004)), ex)
            op = min(max(op, floor_), ceil_)
            hi = max(op, close) * (1 + abs(r.gauss(0, 0.005)))
            lo = min(op, close) * (1 - abs(r.gauss(0, 0.005)))
            hi, lo = min(tick(hi, ex), ceil_), max(tick(lo, ex), floor_)
            value = info["avg_value"] * math.exp(r.gauss(0, 0.35)) * vol_mult
            vol = int(value / close / 100) * 100
            if sc == "suspended" and k >= -4:
                vol, value, op, hi, lo, close = 0, 0, ref, ref, ref, ref
            value = vol * close
            fnet = value * (f_bias + r.gauss(0, 0.04))
            fbuy = value * 0.08 + max(fnet, 0)
            fsell = value * 0.08 + max(-fnet, 0)
            prop = value * (0.02 if sc in ("accum_breakout", "steady_up") else r.gauss(0, 0.02))
            bars.append({"date": d, "open": op, "high": hi, "low": lo, "close": close, "volume": vol, "value": value,
                         "ref": ref, "ceiling": ceil_, "floor": floor_, "fbuy": fbuy, "fsell": fsell,
                         "fbv": int(fbuy / close) if close else 0, "fsv": int(fsell / close) if close else 0,
                         "room": info["listed"] * 0.2, "prop": prop})
            p = close
        self.bars[s] = bars
        pd_ = self.pick_day
        self.news[s] = [{"date": pd_ - timedelta(days=12), "title": "Báo cáo tài chính quý 2: lợi nhuận tăng 35%"}]
        if sc == "warned":
            self.news[s].append({"date": pd_ - timedelta(days=5),
                                 "title": f"HOSE: Quyết định đưa cổ phiếu {s} vào diện cảnh báo"})
        if sc == "foreign_dump":
            self.news[s].append({"date": pd_ - timedelta(days=3), "title": "Cổ đông lớn đăng ký bán 5 triệu cổ phiếu"})

    def bars_upto(self, s, d):
        return [b for b in self.bars[s] if b["date"] <= d]


class FakeHttp:
    """Answers requests the way each real API does. `clock.now` limits data to completed sessions."""

    def __init__(self, market, clock, fail=()):
        self.m, self.clock, self.fail = market, clock, set(fail)
        self.s = type("S", (), {"headers": {}})()
        self.calls = 0

    def _asof(self):
        n = self.clock.now
        d = n.date()
        if not (is_trading_day(d) and (n.hour, n.minute) >= (15, 0)):
            d -= timedelta(days=1)
            while not is_trading_day(d):
                d -= timedelta(days=1)
        return d

    def get(self, url, params=None, **kw):
        return self._route("GET", url, params or {}, None)

    def post(self, url, json=None, **kw):
        return self._route("POST", url, {}, json)

    def _route(self, method, url, params, body):
        from scanner.net import SourceError
        self.calls += 1
        host = urlparse(url).netloc
        for f in self.fail:
            if f in url:
                raise SourceError("HTTP 403")
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        q.update({k: str(v) for k, v in params.items()})
        m, asof = self.m, self._asof()
        path = urlparse(url).path
        if host == "fc-data.ssi.com.vn":
            if path.endswith("AccessToken"):
                return {"status": 200, "message": "Success", "data": {"accessToken": "tok"}}
            if path.endswith("DailyStockPrice"):
                fd = datetime.strptime(q["fromDate"], "%d/%m/%Y").date()
                td = datetime.strptime(q["toDate"], "%d/%m/%Y").date()
                out = []
                syms = [q["symbol"]] if q.get("symbol") else [s for s, i in m.stocks.items() if i["exchange"] == q.get("market")]
                for s in syms:
                    for b in m.bars[s]:
                        if fd <= b["date"] <= td and b["date"] <= asof:
                            out.append({"tradingdate": b["date"].strftime("%d/%m/%Y"), "symbol": s,
                                        "ceilingprice": str(b["ceiling"]), "floorprice": str(b["floor"]),
                                        "refprice": str(b["ref"]), "openprice": str(b["open"]),
                                        "highestprice": str(b["high"]), "lowestprice": str(b["low"]),
                                        "closeprice": str(b["close"]), "totalmatchvol": str(b["volume"]),
                                        "totalmatchval": str(round(b["value"])),
                                        "foreignbuyvoltotal": str(b["fbv"]), "foreignsellvoltotal": str(b["fsv"]),
                                        "foreignbuyvaltotal": str(round(b["fbuy"])), "foreignsellvaltotal": str(round(b["fsell"])),
                                        "netbuysellval": str(round(b["fbuy"] - b["fsell"])),
                                        "foreigncurrentroom": str(int(b["room"]))})
                # add a covered warrant row that must be ignored
                out.append({"tradingdate": fd.strftime("%d/%m/%Y"), "symbol": "CFPT2401", "closeprice": "1200"})
                pi, ps = int(q.get("pageIndex", 1)), int(q.get("pageSize", 1000))
                return {"status": 200, "message": "Success", "totalRecord": len(out), "data": out[(pi - 1) * ps: pi * ps]}
            if path.endswith("SecuritiesDetails"):
                rep = [{"Symbol": s, "SecType": "ST", "Exchange": i["exchange"], "ListedShare": str(int(i["listed"]))}
                       for s, i in m.stocks.items() if i["exchange"] == q.get("market")]
                return {"status": 200, "data": [{"RType": "y", "ReportDate": "x", "TotalNoSym": len(rep), "RepeatedInfo": rep}]}
        if host == "trading.vietcap.com.vn":
            if path.endswith("getAll"):
                return [{"id": 1, "symbol": s, "type": "STOCK", "board": "HSX" if i["exchange"] == "HOSE" else i["exchange"],
                         "organName": f"Công ty {s}"} for s, i in m.stocks.items()] + \
                       [{"id": 2, "symbol": "E1VFVN30", "type": "ETF", "board": "HSX"}]
            if path.endswith("gap-chart"):
                out = []
                for s in body["symbols"]:
                    if s not in m.bars:
                        continue
                    bs = [b for b in m.bars_upto(s, asof) if b["volume"] > 0 or True][-body["countBack"]:]
                    out.append({"symbol": s,
                                "o": [b["open"] / 1000 for b in bs], "h": [b["high"] / 1000 for b in bs],
                                "l": [b["low"] / 1000 for b in bs], "c": [b["close"] / 1000 for b in bs],
                                "v": [b["volume"] for b in bs],
                                "t": [str(int(datetime(b["date"].year, b["date"].month, b["date"].day, 2, tzinfo=timezone.utc).timestamp())) for b in bs]})
                return out
            if path.endswith("getList"):
                out = []
                today_session = self.clock.now.date()
                for s in body["symbols"]:
                    if s not in m.bars:
                        continue
                    bs = m.bars_upto(s, asof)
                    b = bs[-1]
                    fresh = b["date"] == today_session
                    out.append({"listingInfo": {"symbol": s, "ceiling": b["ceiling"], "floor": b["floor"], "refPrice": b["ref"],
                                                "board": "HSX" if m.stocks[s]["exchange"] == "HOSE" else m.stocks[s]["exchange"],
                                                "listedShare": int(m.stocks[s]["listed"])},
                                "matchPrice": {"symbol": s, "matchPrice": b["close"], "openPrice": b["open"],
                                               "highest": b["high"], "lowest": b["low"],
                                               "accumulatedVolume": b["volume"] if fresh else 0,
                                               "accumulatedValue": b["value"] / 1e6 if fresh else 0,
                                               "foreignBuyVolume": b["fbv"], "foreignSellVolume": b["fsv"],
                                               "currentRoom": int(b["room"])},
                                "bidAsk": {"bidPrices": [], "askPrices": []}})
                return out
        if host == "iq.vietcap.com.vn":
            if "search-bar" in path:
                return {"data": [{"code": s, "name": s, "icbLv2": {"name": i["sector"]}} for s, i in m.stocks.items()]}
            sym = q.get("ticker") or path.split("/company/")[-1].split("/")[0]
            if path.endswith("/v1/news"):
                return {"data": {"content": [{"newsTitle": n["title"], "publicDate": n["date"].isoformat() + "T08:00:00"}
                                             for n in m.news.get(sym, []) if n["date"] <= asof]}}
            if path.endswith("/v1/events"):
                return {"data": {"content": [{"eventTitle": "Trả cổ tức bằng tiền 10%", "eventCode": "DIV",
                                              "publicDate": (asof - timedelta(days=20)).strftime("%Y-%m-%d")}]}}
            if path.endswith("statistics-financial"):
                i = m.stocks.get(sym, {})
                return {"data": [{"year": 2026, "quarter": 2, "pe": i.get("pe"), "pb": 1.5, "roe": 0.15,
                                  "netProfitGrowth": i.get("growth", 0) * 100},
                                 {"year": 2026, "quarter": 1, "pe": 10}]}
            if "details" in path:
                return {"data": {"sectorVn": m.stocks.get(sym, {}).get("sector")}}
        if host == "cafef.vn":
            sym = q["Symbol"]
            if sym not in m.bars:
                return {"Data": {"TotalCount": 0, "Data": []}}
            bs = [b for b in m.bars_upto(sym, asof)][-60:][::-1]
            if "GDKhoiNgoai" in path:
                return {"Data": {"TotalCount": len(bs), "Data": [
                    {"Ngay": b["date"].strftime("%d/%m/%Y"), "KLMua": b["fbv"], "GtMua": b["fbuy"], "KLBan": b["fsv"],
                     "GtBan": b["fsell"], "GTDGRong": b["fbuy"] - b["fsell"], "RoomConLai": b["room"]} for b in bs]},
                    "Success": True}
            if "GDTuDoanh" in path:
                return {"Data": {"Data": {"ListDataTudoanh": [
                    {"Symbol": sym, "Date": b["date"].isoformat() + "T00:00:00", "KLcpMua": 1000, "KlcpBan": 800,
                     "GtMua": max(b["prop"], 0) + 1e8, "GtBan": max(-b["prop"], 0) + 1e8} for b in bs[:20]]}}}
        raise SourceError(f"HTTP 404 {host}{path}")
