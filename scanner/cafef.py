"""CafeF public data pages: foreign flow history (khối ngoại) and proprietary trading (tự doanh).
Unofficial and best-effort; if CafeF changes its format the scanner simply skips these signals."""
from datetime import timedelta
from .net import Http, pick, num, parse_date, find_list_of_dicts

BASE = "https://cafef.vn/du-lieu/Ajax/PageNew/DataHistory/"


class CafeF:
    name = "CafeF (free)"

    def __init__(self, http=None):
        self.http = http or Http(referer="https://cafef.vn/", min_interval=0.4)

    def _fetch(self, page, symbol, start, end, size=60):
        params = {"Symbol": symbol, "StartDate": start.strftime("%m/%d/%Y"),
                  "EndDate": end.strftime("%m/%d/%Y"), "PageIndex": 1, "PageSize": size}
        js = self.http.get(BASE + page, params=params)
        return find_list_of_dicts(js) or []

    def foreign(self, symbol, end, days=60):
        items = self._fetch("GDKhoiNgoai.ashx", symbol, end - timedelta(days=int(days * 1.5)), end)
        out = []
        for r in items:
            d = parse_date(pick(r, "Ngay", "Date", "TradingDate"))
            if not d:
                continue
            buy = num(pick(r, "GtMua", "GTMua", "GiaTriMua"), 0.0)
            sell = num(pick(r, "GtBan", "GTBan", "GiaTriBan"), 0.0)
            net = num(pick(r, "GTDGRong", "GtRong", "GTGDRong"))
            if net is None:
                net = buy - sell
            out.append({"date": d, "f_buy_val": buy, "f_sell_val": sell, "f_net_val": net,
                        "f_buy_vol": num(pick(r, "KLMua", "KLGDMua"), 0.0),
                        "f_sell_vol": num(pick(r, "KLBan", "KLGDBan"), 0.0),
                        "f_room": num(pick(r, "RoomConLai", "Room"))})
        return out

    def prop(self, symbol, end, days=60):
        items = self._fetch("GDTuDoanh.ashx", symbol, end - timedelta(days=int(days * 1.5)), end)
        out = []
        for r in items:
            d = parse_date(pick(r, "Date", "Ngay", "TradingDate"))
            if not d:
                continue
            buy = num(pick(r, "GtMua", "GTMua", "GiaTriMua"), 0.0)
            sell = num(pick(r, "GtBan", "GTBan", "GiaTriBan"), 0.0)
            out.append({"date": d, "prop_net_val": buy - sell})
        return out
