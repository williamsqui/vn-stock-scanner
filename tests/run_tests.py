"""Offline end-to-end tests with a realistic mock market.
Run from the repo folder:  python -m tests.run_tests
Emails are written to out_test/ as HTML instead of being sent."""
import os
import shutil
import sys
from datetime import datetime, date, timedelta, timezone

os.environ.update({"DRY_RUN": "true", "STATE_PASSWORD": "test-password", "DATA_DIR": "out_test/data",
                   "CACHE_DIR": "out_test/cache", "OUT_DIR": "out_test/emails", "SSI_CONSUMER_ID": "id",
                   "SSI_CONSUMER_SECRET": "secret", "BANKROLL_VND": "30000000", "SELFTEST_TICKER": "AAA", "SELFTEST_TICKER2": "KKK"})

from scanner import calendar_vn, main as M, emailer          # noqa: E402
from scanner.config import Settings                          # noqa: E402
from scanner.ssi import SSI                                  # noqa: E402
from scanner.vietcap import Vietcap, fix_value, fix_scale     # noqa: E402
from scanner.cafef import CafeF                              # noqa: E402
from scanner.net import parse_date                           # noqa: E402
from scanner.news import analyse                             # noqa: E402
from scanner.indicators import round_tick                    # noqa: E402
from scanner.track import State                              # noqa: E402
from tests.mock_market import Market, FakeHttp, Clock        # noqa: E402

VN = timezone(timedelta(hours=7))
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


MARKET = Market()
CLOCK = Clock(datetime(2026, 9, 21, 8, 15, tzinfo=VN))
FAILING = set()


def set_now(dt):
    CLOCK.now = dt
    calendar_vn.now_vn = lambda: CLOCK.now
    M.now_vn = lambda: CLOCK.now


def fake_sources(cfg):
    http = FakeHttp(MARKET, CLOCK, fail=FAILING)
    ssi = SSI(cfg.ssi_id, cfg.ssi_secret, http=http) if cfg.ssi_id and cfg.data_source in ("auto", "ssi") else None
    return ssi, Vietcap(http=http, iq_http=http), CafeF(http=http)


M.build_sources = fake_sources


def emails():
    d = "out_test/emails"
    return sorted(os.listdir(d)) if os.path.exists(d) else []


def read_email(prefix):
    d = "out_test/emails"
    fs = [f for f in os.listdir(d) if f.startswith(prefix)]
    return open(os.path.join(d, sorted(fs, key=lambda f: os.path.getmtime(os.path.join(d, f)))[-1]), encoding="utf-8").read()


def unit_tests():
    print("\n== Unit tests")
    check("holiday 2026-09-02 closed", not calendar_vn.is_trading_day(date(2026, 9, 2)))
    check("Tet 2026-02-17 closed", not calendar_vn.is_trading_day(date(2026, 2, 17)))
    check("normal Monday open", calendar_vn.is_trading_day(date(2026, 9, 21)))
    check("weekend closed", not calendar_vn.is_trading_day(date(2026, 9, 20)))
    check("prev trading day skips National Day", calendar_vn.prev_trading_day(date(2026, 9, 3)) == date(2026, 8, 28))
    check("parse dd/mm/yyyy", parse_date("04/05/2020") == "2020-05-04")
    check("parse /Date(ms)/", parse_date("/Date(1758240000000)/") is not None)
    check("parse ISO", parse_date("2026-09-18T00:00:00") == "2026-09-18")
    check("value in millions normalised", abs(fix_value(1234.5, 50000, 24690) - 1234.5e6) < 1e6)
    check("value already VND unchanged", fix_value(1.2345e9, 50000, 24690) == 1.2345e9)
    check("price in thousands scaled", fix_scale(24.5) == 24500)
    check("HOSE tick <10k", round_tick(9_987, "HOSE") == 9_980)
    check("HOSE tick 10-50k", round_tick(24_987, "HOSE") == 24_950)
    check("HOSE tick >=50k", round_tick(95_555, "HOSE") == 95_500)
    check("HNX tick", round_tick(12_345, "HNX") == 12_300)
    a = analyse([{"date": "2026-09-10", "title": "HOSE: Quyết định đưa cổ phiếu XYZ vào diện cảnh báo"}], date(2026, 9, 18))
    check("news: warning list -> exclude", a["exclude"])
    a = analyse([{"date": "2026-08-10", "title": "Đưa cổ phiếu XYZ vào diện cảnh báo"},
                 {"date": "2026-09-10", "title": "Đưa cổ phiếu XYZ ra khỏi diện cảnh báo"}], date(2026, 9, 18))
    check("news: removed from warning -> ok", not a["exclude"])
    a = analyse([{"date": "2026-09-10", "title": "Lợi nhuận quý 2 tăng trưởng 40%, vượt kế hoạch"}], date(2026, 9, 18))
    check("news: positive", a["sub"] > 0.5)
    pn = M.parse_number
    for raw, kind, want in [("95500", "price", 95500), ("95.5", "price", 95.5), ("95,5", "price", 95.5),
                            ("95.500", "price", 95500), ("1.234.000", "count", 1234000), ("20,000,000", "count", 20e6),
                            ("-3%", "pct", -3), ("+6,5%", "pct", 6.5), ("1.000", "count", 1000), ("", "pct", None),
                            ("1.234,5", "price", 1234.5)]:
        check(f"parse_number {raw!r}", pn(raw, kind) == want, str(pn(raw, kind)))
    try:
        pn("abc")
        check("parse_number rejects junk", False)
    except M.InputError:
        check("parse_number rejects junk", True)
    from scanner.scoring import clip
    check("clip(NaN) is worst case", clip(float("nan")) == 0.0)
    s = State("out_test/tmp/state.enc", "pw1")
    s.add_pick({"signal_date": "2026-09-18", "symbol": "XYZ", "score": 70, "entry_max": 1, "target": 2, "stop": 0.5,
                "exchange": "HOSE", "entry_ref": 1})
    s.save()
    raw = open("out_test/tmp/state.enc", "rb").read()
    check("state file is encrypted (no ticker in bytes)", b"XYZ" not in raw)
    check("state decrypts with right password", len(State("out_test/tmp/state.enc", "pw1").data["picks"]) == 1)
    check("wrong password detected", not State("out_test/tmp/state.enc", "wrong").ok)


def scan_tests():
    print("\n== Morning scan via SSI (bootstrap from empty cache)")
    set_now(datetime(2026, 9, 21, 8, 15, tzinfo=VN))       # Monday morning; as-of Fri 18/09
    rc = M.main(["scan", "--session", "morning"])
    check("scan exit code 0", rc == 0)
    html = read_email("VN_Scanner")
    check("morning email built", "Morning picks" in html)
    check("AAA (foreign accumulation + breakout) picked", "#1 AAA" in html or "#2 AAA" in html or "#3 AAA" in html)
    for bad, why in [("BBB", "pump/ceilings"), ("CCC", "warning list"), ("EEE", "illiquid"), ("HHH", "suspended"),
                     ("DDD", "foreign dump")]:
        check(f"{bad} not picked ({why})", f"#1 {bad}" not in html and f"#2 {bad}" not in html and f"#3 {bad}" not in html)
    check("covered warrant rows ignored", "CFPT" not in html)
    check("email has exit plan", "Stop-loss" in html and "Target" in html)
    check("email shows sizing in shares", "shares" in html)
    check("track record section present", "Track record" in html)
    st = State("out_test/data/state.enc", "test-password")
    check("picks recorded in encrypted state", len(st.data["picks"]) >= 1, str(len(st.data["picks"])))
    check("cache saved", os.path.exists("out_test/cache/prices.csv.gz"))

    print("\n== Holiday / weekend runs do nothing")
    n_before = len(emails())
    set_now(datetime(2026, 9, 20, 8, 15, tzinfo=VN))
    M.main(["scan", "--session", "morning"])
    check("Sunday: no email", len(emails()) == n_before)

    print("\n== After-close run, SSI blocked -> free fallback (Vietcap + CafeF)")
    FAILING.add("fc-data.ssi.com.vn")
    set_now(datetime(2026, 9, 21, 15, 45, tzinfo=VN))
    rc = M.main(["scan", "--session", "close"])
    html = read_email("VN_Scanner_close")
    check("close run ok on fallback", rc == 0 and "After-close summary" in html)
    check("fallback source named", "Vietcap" in html)
    check("close email not stale", "could not be refreshed" not in html)
    FAILING.clear()

    print("\n== All sources down -> warning, picks not recorded")
    FAILING.update({"fc-data.ssi.com.vn", "vietcap.com.vn", "cafef.vn"})
    n_before = len(State("out_test/data/state.enc", "test-password").data["picks"])
    set_now(datetime(2026, 9, 23, 8, 15, tzinfo=VN))
    rc = M.main(["scan", "--session", "morning"])
    html = read_email("VN_Scanner")
    check("stale warning shown", "could not be refreshed" in html)
    check("no picks recorded on stale data", len(State("out_test/data/state.enc", "test-password").data["picks"]) == n_before)
    FAILING.clear()

    print("\n== Simulate 3 weeks of mornings -> track record")
    d = date(2026, 9, 22)
    while d <= date(2026, 10, 14):
        if calendar_vn.is_trading_day(d):
            set_now(datetime(d.year, d.month, d.day, 8, 15, tzinfo=VN))
            M.main(["scan", "--session", "morning"])
        d += timedelta(days=1)
    st = State("out_test/data/state.enc", "test-password")
    statuses = {p["symbol"] + "@" + p["signal_date"]: p["status"] for p in st.data["picks"]}
    print("   picks:", statuses)
    aaa = [p for p in st.data["picks"] if p["symbol"] == "AAA"]
    check("AAA pick hit target", aaa and aaa[0]["status"] == "target", str(aaa[:1]))
    closed = [p for p in st.data["picks"] if p["status"] in ("target", "stop", "expired")]
    check("several picks closed", len(closed) >= 2, str(len(closed)))
    html = read_email("VN_Scanner")
    check("track table filled", "Hit target" in html and "closed" in html)
    for p in closed:
        if p["status"] == "target":
            ok = abs(p["ret_net_pct"] - ((p["target"] / p["entry_fill"] - 1) * 100 - 0.4)) < 0.05
            check(f"net return includes fees ({p['symbol']})", ok)
            break


def check_tests():
    print("\n== Check a stock")
    set_now(datetime(2026, 9, 21, 20, 0, tzinfo=VN))
    M.main(["check-stock", "aaa"])
    html = read_email("VN_Scanner_check")
    check("check-stock email", "AAA" in html and "Signal breakdown" in html)
    M.main(["check-stock", "BBB"])
    html = read_email("VN_Scanner_check")
    check("BBB flagged (filters or pump)", "Not a pick" in html and ("ceiling" in html or "Pump" in html or "Small company" in html))
    M.main(["check-stock", "CCC"])
    html = read_email("VN_Scanner_check")
    check("CCC shows watch-list notice", "watch-list" in html)
    M.main(["check-stock", "ZZZ"])
    html = read_email("VN_Scanner_check")
    check("unknown ticker handled", "Could not get enough price history" in html)

    import json
    os.makedirs("out_test/tmp", exist_ok=True)
    json.dump({"inputs": {"ticker": " kkk "}}, open("out_test/tmp/ev.json", "w"))
    os.environ["GITHUB_EVENT_PATH"] = "out_test/tmp/ev.json"
    M.main(["from-event", "check-stock"])
    check("check-stock via GitHub event file", "KKK" in read_email("VN_Scanner_check"))
    json.dump({"inputs": {"ticker": "KKK", "buy_price": "50.5", "gain_pct": "", "shares": "300", "position_vnd": "",
                          "buy_date": ""}}, open("out_test/tmp/ev.json", "w"))
    M.main(["from-event", "check-position"])
    html = read_email("VN_Scanner_position")
    check("check-position via event file (price typed in thousands)", "50,500" in html and "300 shares" in html)

    json.dump({"inputs": {"ticker": "FFF", "buy_price": "", "gain_pct": "-3%", "shares": "", "position_vnd": "20",
                          "buy_date": "18/09/2026"}}, open("out_test/tmp/ev.json", "w"))
    rc = M.main(["from-event", "check-position"])
    html = read_email("VN_Scanner_position")
    check("negative gain '-3%' via event works", rc == 0 and "-3.0%" in html, html[:300])
    json.dump({"inputs": {"ticker": "FFF", "buy_price": "abc"}}, open("out_test/tmp/ev.json", "w"))
    M.main(["from-event", "check-position"])
    check("bad input -> friendly email", "read the number" in read_email("VN_Scanner_position"))

    print("\n== Check my position")
    set_now(datetime(2026, 9, 30, 20, 0, tzinfo=VN))
    last_aaa = [b for b in MARKET.bars["AAA"] if b["date"] <= date(2026, 9, 30)][-1]["close"]
    M.main(["check-position", "AAA", "--buy", str(round(last_aaa / 1.2 / 1000, 1)), "--shares", "200"])
    html = read_email("VN_Scanner_position")
    check("big winner -> TAKE PROFIT or TIGHTEN", "TAKE PROFIT" in html or "TIGHTEN STOP" in html, html[:0])
    last_fff = [b for b in MARKET.bars["FFF"] if b["date"] <= date(2026, 9, 30)][-1]["close"]
    M.main(["check-position", "FFF", "--buy", str(last_fff * 1.15), "--value", "10"])
    html = read_email("VN_Scanner_position")
    check("big loser -> SELL", ">SELL" in html)
    M.main(["check-position", "KKK", "--gain", "2", "--value", "8000000"])
    html = read_email("VN_Scanner_position")
    check("small gain on steady stock -> verdict given", any(v in html for v in ("HOLD", "TIGHTEN", "TAKE PROFIT", "SELL")))


def selftest_tests():
    print("\n== Self-test")
    set_now(datetime(2026, 9, 21, 20, 0, tzinfo=VN))
    M.main(["selftest"])
    html = read_email("VN_Scanner_self-test")
    check("selftest email", "Data self-test" in html and "FAILED" not in html, html[html.find("FAILED") - 200: html.find("FAILED") + 50])
    FAILING.update({"cafef.vn"})
    M.main(["selftest"])
    html = read_email("VN_Scanner_self-test")
    check("selftest reports failures", "FAILED" in html)
    FAILING.clear()


def log_privacy_test():
    print("\n== Logs contain no tickers")
    import io
    import contextlib
    buf = io.StringIO()
    set_now(datetime(2026, 10, 15, 8, 15, tzinfo=VN))
    with contextlib.redirect_stdout(buf):
        M.main(["scan", "--session", "morning"])
    out = buf.getvalue()
    leaked = [s for s in MARKET.stocks if f" {s}" in out or f"{s} " in out or f"{s}:" in out]
    check("no tickers in run log", not leaked, str(leaked) + out[:500])
    print("   sample log:\n   " + out.strip().replace("\n", "\n   "))


if __name__ == "__main__":
    shutil.rmtree("out_test", ignore_errors=True)
    unit_tests()
    scan_tests()
    check_tests()
    selftest_tests()
    log_privacy_test()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:", FAIL)
    sys.exit(1 if FAIL else 0)
