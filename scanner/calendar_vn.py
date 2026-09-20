"""Vietnam trading calendar (Asia/Ho_Chi_Minh)."""
from datetime import datetime, date, timedelta, timezone

VN_TZ = timezone(timedelta(hours=7))

# Official HOSE/HNX closed sessions. 2026 source: HOSE announcement (12 sessions).
# Add future years with the EXTRA_HOLIDAYS repo variable (format YYYY-MM-DD, comma separated).
HOLIDAYS = {
    # 2026
    "2026-01-01",
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
    "2026-04-27",
    "2026-04-30", "2026-05-01",
    "2026-08-31", "2026-09-01", "2026-09-02",
    # 2027 (fixed-date holiday only; add Tet/Hung Kings when announced)
    "2027-01-01",
}


def now_vn():
    return datetime.now(VN_TZ)


def today_vn():
    return now_vn().date()


def _holidays(extra=()):
    return HOLIDAYS | {x.strip() for x in extra if x.strip()}


def is_trading_day(d: date, extra=()):
    return d.weekday() < 5 and d.isoformat() not in _holidays(extra)


def prev_trading_day(d: date, extra=()):
    d = d - timedelta(days=1)
    while not is_trading_day(d, extra):
        d -= timedelta(days=1)
    return d


def next_trading_day(d: date, extra=()):
    d = d + timedelta(days=1)
    while not is_trading_day(d, extra):
        d += timedelta(days=1)
    return d


def trading_days_back(end: date, n: int, extra=()):
    """List of the last n trading days up to and including `end` (if it's a trading day)."""
    out, d = [], end
    while len(out) < n:
        if is_trading_day(d, extra):
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def last_completed_session(extra=()):
    """The most recent trading day whose session has finished (after 15:05 VN time)."""
    n = now_vn()
    d = n.date()
    if is_trading_day(d, extra) and (n.hour, n.minute) >= (15, 5):
        return d
    return prev_trading_day(d, extra)
