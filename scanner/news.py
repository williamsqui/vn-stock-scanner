"""Keyword reading of official announcements/news titles (Vietnamese + English)."""
import unicodedata
from datetime import date, timedelta


def _norm(s):
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.replace("đ", "d")


# Put on / taken off exchange watch lists
EXCLUDE_ON = ["dien canh bao", "dien kiem soat", "han che giao dich", "tam ngung giao dich", "dinh chi giao dich",
              "ngung giao dich", "huy niem yet", "huy dang ky giao dich", "bi dua vao dien", "suspension",
              "trading halt", "delist", "[susp]", "under warning", "under control", "restricted trading"]
EXCLUDE_OFF = ["ra khoi dien", "khoi dien canh bao", "khoi dien kiem soat", "go bo", "duoc giao dich tro lai",
               "giao dich tro lai", "removed from", "resume"]

POSITIVE = ["lai ky luc", "loi nhuan tang", "tang truong", "vuot ke hoach", "hoan thanh ke hoach", "co tuc",
            "trung thau", "ky hop dong", "hop dong moi", "dang ky mua", "mua vao", "mua co phieu quy",
            "nang hang", "tang von", "khoi cong", "profit rises", "record profit", "dividend", "buyback",
            "contract", "beat", "[div]", "lai tang", "doanh thu tang", "mo rong"]
NEGATIVE = ["thua lo", "lo rong", "lo luy ke", "loi nhuan giam", "giam manh", "dang ky ban", "ban ra", "thoai von",
            "xu phat", "vi pham", "khoi to", "bat tam giam", "dieu tra", "cham nop", "ngoai tru", "tu choi y kien",
            "no qua han", "tron thue", "cuong che", "giai the", "pha san", "loss", "fine", "violation",
            "investigation", "arrest", "qualified opinion", "sell-off", "margin call", "call margin"]
HYPE = ["ca map", "lua ga", "sieu co phieu", "x2 tai khoan", "bung no", "chac chan tang", "tin don", "don thoi"]


def analyse(items, asof=None, lookback_days=45):
    """Return dict: score_sub (0..1), positives, negatives, exclude(bool), exclude_reason."""
    asof = asof or date.today()
    cutoff = (asof - timedelta(days=lookback_days)).isoformat()
    long_cutoff = (asof - timedelta(days=200)).isoformat()
    pos, neg, hype = [], [], []
    watch = []   # (date, on/off, title)
    for it in items or []:
        t = it.get("title") or ""
        n = _norm(t)
        d = it.get("date") or ""
        if d and d < long_cutoff:
            continue
        on = any(k in n for k in EXCLUDE_ON)
        off = any(k in n for k in EXCLUDE_OFF)
        if on or off:
            watch.append((d, "off" if off else "on", t))
        if d and d < cutoff:
            continue
        if any(k in n for k in POSITIVE):
            pos.append(t)
        if any(k in n for k in NEGATIVE):
            neg.append(t)
        if any(k in n for k in HYPE):
            hype.append(t)
    exclude, reason = False, ""
    if watch:
        watch.sort(key=lambda x: x[0] or "")
        last = watch[-1]
        if last[1] == "on":
            exclude, reason = True, last[2][:140]
    sub = 0.5 + 0.08 * min(len(pos), 4) - 0.12 * min(len(neg), 4) - 0.1 * min(len(hype), 2)
    return {"sub": max(0.0, min(1.0, sub)), "positives": pos[:3], "negatives": neg[:3], "hype": hype[:2],
            "exclude": exclude, "exclude_reason": reason, "count": len(items or [])}
