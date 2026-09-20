"""Entry point.
  python -m scanner.main scan --session morning|close
  python -m scanner.main check-stock FPT
  python -m scanner.main check-position FPT --buy 95000 --shares 200
  python -m scanner.main selftest
Run logs never print tickers or picks (the repo is public)."""
import argparse
import os
import statistics
import sys
import traceback
from datetime import date

from . import emailer
from .calendar_vn import is_trading_day, today_vn, now_vn, last_completed_session
from .config import Settings
from .data import DataManager
from .emailer import L, e, page, vnd, price, pct, score_badge
from .net import SourceError
from .news import analyse
from .scoring import score_stock, market_regime, MIN_BARS
from .sizing import suggest_size, position_verdict
from .track import State

FINALISTS = 15


def log(msg):
    print(f"[{now_vn():%H:%M:%S}] {msg}", flush=True)


def build_sources(cfg):
    ssi = vc = cf = None
    if cfg.data_source in ("auto", "ssi") and cfg.ssi_id and cfg.ssi_secret:
        from .ssi import SSI
        ssi = SSI(cfg.ssi_id, cfg.ssi_secret)
    if True:   # Vietcap/CafeF are always used for news, fundamentals and watch-list checks
        from .vietcap import Vietcap
        from .cafef import CafeF
        vc, cf = Vietcap(), CafeF()
    return ssi, vc, cf


# ================================================================ scanning
class Scanner:
    def __init__(self, cfg, dm):
        self.cfg, self.dm = cfg, dm
        self.fund_cache = {}

    def fundamentals(self, sym):
        if sym not in self.fund_cache:
            self.fund_cache[sym] = self.dm.fundamentals(sym)
        return self.fund_cache[sym]

    def sector_pe(self, sym, liquid):
        pes = []
        for p in self.dm.peers(sym, liquid, n=6):
            pe = self.fundamentals(p).get("pe")
            if pe and 0 < pe < 80:
                pes.append(pe)
        return statistics.median(pes) if len(pes) >= 3 else None

    def listed(self, sym):
        return self.dm.meta["symbols"].get(sym, {}).get("listed_shares")

    def full_score(self, sym, ex, df, market, liquid, asof):
        self.dm.enrich_flows(sym, asof)
        df = self.dm.series(sym)
        items = self.dm.news(sym)
        nw = analyse(items, date.fromisoformat(asof))
        fund = self.fundamentals(sym)
        spe = self.sector_pe(sym, liquid)
        r = score_stock(df, ex, self.cfg, market, fund, spe, nw, self.listed(sym) or fund.get("listed_shares"),
                        excluded_manual=sym in self.cfg.exclude)
        r["news_items"] = items[:5]
        r["sector"] = self.dm.sector_of(sym) or fund.get("sector")
        return r

    def run(self, holding=()):
        dm, cfg = self.dm, self.cfg
        asof = dm.df["date"].max()
        uni = dm.universe()
        groups = {s: g.sort_values("date").reset_index(drop=True) for s, g in dm.df.groupby("symbol") if s in uni}
        for s, g in groups.items():
            for c in g.columns[3:]:
                g[c] = g[c].astype(float)
        # only stocks that traded on the as-of date
        groups = {s: g for s, g in groups.items() if len(g) >= MIN_BARS and g["date"].iloc[-1] == asof}
        liquid = [s for s, g in groups.items() if g["value"].tail(20).mean() >= cfg.min_avg_value_bn * 1e9]
        market = market_regime([groups[s]["close"] for s in liquid])
        log(f"Scanned {len(groups)} stocks; {len(liquid)} liquid; breadth {market['breadth']:.2f}")
        quick = []
        errors = 0
        for s, g in groups.items():
            try:
                ex = uni[s]["exchange"]
                r = score_stock(g, ex, cfg, market, None, None, None, self.listed(s), excluded_manual=s in cfg.exclude)
            except Exception:
                errors += 1
                continue
            if not r["filters_failed"]:
                quick.append((s, r))
        if errors:
            log(f"Skipped {errors} stocks with bad data")
        quick.sort(key=lambda x: x[1]["score"], reverse=True)
        log(f"{len(quick)} passed safety filters; enriching top {min(FINALISTS, len(quick))}")
        final = []
        for s, _ in quick[:FINALISTS]:
            try:
                final.append((s, self.full_score(s, uni[s]["exchange"], groups[s], market, liquid, asof)))
            except Exception as ex:
                log(f"Enrichment error ({type(ex).__name__})")
        final.sort(key=lambda x: x[1]["score"], reverse=True)
        picks, closest = [], []
        for s, r in final:
            why = self.reject_reason(r)
            if why is None and s in holding:
                continue          # already an open pick - tracked in the 'Open picks' section
            if why is None and len(picks) < cfg.max_picks:
                picks.append((s, r))
            elif len(closest) < 3:
                closest.append((s, r, why or "Lower rank than the picks"))
        return {"asof": asof, "market": market, "picks": picks, "closest": closest,
                "n_scanned": len(groups), "n_passed": len(quick)}

    def reject_reason(self, r):
        cfg = self.cfg
        if r["filters_failed"]:
            return r["filters_failed"][0]
        if r["score"] < cfg.min_score:
            return f"Score below your minimum ({cfg.min_score:.0f})"
        if r["plan"]["rr"] < cfg.min_reward_risk:
            return f"Reward:risk only {r['plan']['rr']:.2f} after fees (need {cfg.min_reward_risk:g})"
        if r.get("ceil_streak", 0) >= 3:
            return "Too many ceiling days in a row (chasing risk)"
        if r.get("pump"):
            return "Pump-pattern warning"
        return None


def cmd_scan(cfg, session, force=False):
    today = today_vn()
    if not force and not is_trading_day(today, cfg.extra_holidays):
        log("Not a trading day (weekend/holiday) - nothing to do.")
        return 0
    ssi, vc, cf = build_sources(cfg)
    dm = DataManager(cfg, ssi, vc, cf, log=log)
    dm.refresh_meta()
    asof = dm.update()
    dm.save()
    log(f"Data source: {dm.used_source}")
    state = State(os.path.join(cfg.data_dir, "state.enc"), cfg.state_password)
    state.evaluate(dm.series, cfg)
    res = Scanner(cfg, dm).run(holding={p["symbol"] for p in state.open_picks()})
    dm.save()
    expected = last_completed_session(cfg.extra_holidays).isoformat()
    stale = (asof or "") < expected
    n_picks = len(res["picks"])
    if stale:
        log("Data not up to date - picks will not be recorded.")
    if session == "morning" and not stale:
        for s, r in res["picks"]:
            p = r["plan"]
            state.add_pick({"signal_date": res["asof"], "symbol": s, "exchange": r["exchange"],
                            "score": r["score"], "entry_ref": p["entry"], "entry_max": p["entry_max"],
                            "target": p["target"], "stop": p["stop"], "target_pct": p["target_pct"]})
    saved = state.save()
    log(f"Picks: {n_picks}; track record saved: {saved}")
    if n_picks == 0 and not cfg.send_empty_email:
        log("No picks and SEND_EMPTY_EMAIL=false - no email.")
        return 0
    html_body, subject = render_scan_email(cfg, session, res, state, dm, stale)
    emailer.send(cfg, subject, html_body, log)
    return 0


def render_scan_email(cfg, session, res, state, dm, stale):
    picks = res["picks"]
    date_str = f"{today_vn():%a %d/%m}"
    if session == "morning":
        title = L("Morning picks", "Mã chọn buổi sáng")
        subj = f"VN Scanner | {len(picks)} pick{'s' if len(picks) != 1 else ''} | {date_str}" if picks \
            else f"VN Scanner | no picks today | {date_str}"
    else:
        title = L("After-close summary", "Tổng kết cuối ngày")
        subj = f"VN Scanner close | {len(picks)} on watch for tomorrow | {date_str}"
    body = f"<h1>🇻🇳 {title}</h1><div class='muted'>{date_str} · {now_vn():%H:%M}</div>"
    if stale:
        msg = L("Data sources could not be refreshed (or the latest session is not published yet). "
                "This email uses older data from", "Không cập nhật được dữ liệu mới, đang dùng dữ liệu ngày")
        msg2 = L("Picks below are NOT recorded in the track record - treat them with extra caution.",
                 "Không ghi nhận vào thành tích.")
        body += f"<div class='card warn'>{msg} <b>{e(res['asof'])}</b>. {msg2}</div>"
    if not state.ok:
        body += (f"<div class='card warn'>{L('Track record is OFF: ', 'Chưa lưu thành tích: ')}"
                 f"{e(state.error or 'add a STATE_PASSWORD secret to enable it.')}</div>")
    body += emailer.market_block(res["market"], res["asof"], dm.used_source or "-", res["n_scanned"], res["n_passed"])
    if session == "close":
        body += f"<div class='card'><b>{L('Preview for tomorrow', 'Xem trước cho ngày mai')}</b><div class='small'>" \
                f"{L('Final picks (recorded in the track record) come in the morning email.', 'Danh sách chính thức gửi buổi sáng.')}</div></div>"
    if picks:
        for i, (s, r) in enumerate(picks, 1):
            size = suggest_size(r["plan"], r["score"], cfg, len(picks))
            body += emailer.pick_card(i, s, r, size)
        if len(picks) > 1:
            body += f"<p class='small'>{L('Sizes assume you take all picks; together they stay under 95% of your bankroll.', 'Tổng vị thế < 95% vốn.')}</p>"
    else:
        body += (f"<div class='card'><h2>😴 {L('No picks today', 'Hôm nay không có mã đạt')}</h2><p>"
                 f"{L('Nothing passed all filters with a high enough score. Sitting out is a valid position.', 'Đứng ngoài cũng là một vị thế.')}</p></div>")
    body += emailer.closest_block(res["closest"])
    body += emailer.open_picks_block(state.open_picks())
    rows, tot = state.stats()
    body += emailer.track_block(rows, tot, state.recent_closed())
    body += (f"<p class='small'>Settings: bankroll {vnd(cfg.bankroll)} · risk {cfg.risk_per_trade_pct:g}%/trade · "
             f"min score {cfg.min_score:.0f} · fees {cfg.fee_buy_pct:g}%+{cfg.fee_sell_pct:g}% + tax {cfg.sell_tax_pct:g}% · "
             f"min liquidity {cfg.min_avg_value_bn:g} tỷ/day · min mcap {cfg.min_market_cap_bn:,.0f} tỷ</p>")
    return page(subj, body), subj


# ================================================================ check a stock
def prepare_single(cfg, symbol):
    ssi, vc, cf = build_sources(cfg)
    dm = DataManager(cfg, ssi, vc, cf, log=log)
    dm.refresh_meta()
    asof = last_completed_session(cfg.extra_holidays).isoformat()
    s = dm.series(symbol)
    if len(s) < MIN_BARS or s["date"].iloc[-1] < asof:
        dm.ensure_symbol(symbol)      # refresh just this symbol (keeps cached rows if the fetch fails)
    info = dm.meta["symbols"].get(symbol, {})
    ex = info.get("exchange")
    if not ex:
        ex = "HOSE"
    return dm, ex, asof


def cmd_check_stock(cfg, symbol):
    symbol = symbol.strip().upper()
    dm, ex, asof = prepare_single(cfg, symbol)
    df = dm.series(symbol)
    if len(df) < MIN_BARS:
        html_body = page("x", f"<h1>{e(symbol)}</h1><div class='card warn'>"
                              f"{L('Could not get enough price history for this ticker. Check the spelling, or it may be newly listed.', 'Không đủ dữ liệu.')}</div>")
        emailer.send(cfg, f"VN Scanner check | {symbol} | no data", html_body, log)
        return 0
    market = _market_from_store(dm, cfg)
    r = Scanner(cfg, dm).full_score(symbol, ex, df, market, _liquid(dm, cfg), df["date"].iloc[-1])
    size = suggest_size(r["plan"], r["score"], cfg, 1)
    verdict = "Would be a PICK" if (not r["filters_failed"] and r["score"] >= cfg.min_score and r["plan"]["rr"] >= cfg.min_reward_risk
                                    and not r.get("pump") and r.get("ceil_streak", 0) < 3) else "Not a pick right now"
    body = f"<h1>🔎 {e(symbol)} <span class='muted'>{e(ex)}</span></h1><div class='muted'>{L('Full breakdown', 'Phân tích chi tiết')} · data {e(r.get('date'))}</div>"
    body += f"<div class='card'><div class='verdict'>{score_badge(r['score'])} {e(verdict)}</div>"
    if r["filters_failed"]:
        body += f"<h3>🚫 {L('Failed safety filters', 'Không qua bộ lọc')}</h3><ul>" + "".join(f"<li>{e(x)}</li>" for x in r["filters_failed"]) + "</ul>"
    body += "<h3>Signal breakdown (0-100 each)</h3><table>"
    names = {"foreign": "Foreign flows / Khối ngoại (30%)", "prop": "Prop trading / Tự doanh (5%)",
             "volume": "Volume / Khối lượng (15%)", "chart": "Chart & trend / Kỹ thuật (30%)",
             "fundamental": "Fundamentals / Cơ bản (10%)", "news": "News & announcements / Tin tức (10%)"}
    for k, n in names.items():
        v = r["subs"].get(k)
        body += f"<tr><td>{n}</td><td><b>{'no data' if v is None else f'{v * 100:.0f}'}</b></td></tr>"
    body += "</table>"
    inf = r.get("info", {})
    body += (f"<p class='small'>RSI {inf.get('rsi', 0):.0f} · MA20 {price(inf.get('ma20'))} · MA50 {price(inf.get('ma50'))} · "
             f"vol {inf.get('vol_ratio', 0):.1f}x · 10d {pct((inf.get('ret10') or 0) * 100)} · "
             f"foreign 5d {(inf.get('f_net_5d') or 0) / 1e9:+,.1f} tỷ · P/E {inf.get('pe') or '-'} · sector P/E {inf.get('sector_pe') or '-'}</p></div>")
    body += emailer.pick_card(1, symbol, r, size).replace("#1 ", "")
    if r.get("news_items"):
        body += "<div class='card'><h3>📰 Latest announcements</h3><ul>" + "".join(
            f"<li><span class='small'>{e(n.get('date') or '')}</span> {e(n['title'][:160])}</li>" for n in r["news_items"]) + "</ul></div>"
    emailer.send(cfg, f"VN Scanner check | {symbol} | {r['score']:.0f}/100", page("", body), log)
    return 0


def _liquid(dm, cfg):
    if len(dm.df) == 0:
        return []
    v = dm.df.groupby("symbol")["value"].apply(lambda x: x.astype(float).tail(20).mean())
    return list(v[v >= cfg.min_avg_value_bn * 1e9].index)


def _market_from_store(dm, cfg):
    liquid = _liquid(dm, cfg)
    if len(liquid) < 30:
        return None
    closes = [dm.df[dm.df["symbol"] == s].sort_values("date")["close"].astype(float) for s in liquid[:300]]
    return market_regime(closes)


# ================================================================ check a position
def cmd_check_position(cfg, symbol, buy=None, gain=None, shares=None, value=None, buy_date=None):
    symbol = symbol.strip().upper()
    dm, ex, asof = prepare_single(cfg, symbol)
    df = dm.series(symbol)
    if len(df) < MIN_BARS:
        emailer.send(cfg, f"VN Scanner position | {symbol} | no data",
                     page("", f"<div class='card warn'>No price data for {e(symbol)}.</div>"), log)
        return 0
    last = float(df["close"].iloc[-1])
    if buy is not None and 0 < buy < 1000:
        buy *= 1000            # typed in thousands (e.g. 95.5)
    if buy is None and gain is not None:
        buy = last / (1 + gain / 100)
    if buy is None:
        buy = last
    if not buy or buy <= 0:
        emailer.send(cfg, f"VN Scanner position | {symbol} | check inputs",
                     page("", "<div class='card warn'>Buy price must be above 0 (or gain % above -100).</div>"), log)
        return 0
    if value is not None and value < 100_000:
        value *= 1_000_000     # typed in millions
    if buy_date:
        from .net import parse_date
        buy_date = parse_date(buy_date)
    r = Scanner(cfg, dm).full_score(symbol, ex, df, _market_from_store(dm, cfg), _liquid(dm, cfg), df["date"].iloc[-1])
    v = position_verdict(dm.series(symbol), ex, r, buy, cfg, shares=shares, position_value=value, buy_date=buy_date)
    color = {"HOLD": "#1d4ed8", "TIGHTEN STOP": "#b45309", "TAKE PROFIT": "#15803d", "SELL": "#b91c1c"}[v["verdict"]]
    vi = {"HOLD": "GIỮ", "TIGHTEN STOP": "NÂNG CẮT LỖ", "TAKE PROFIT": "CHỐT LỜI", "SELL": "BÁN"}[v["verdict"]]
    body = (f"<h1>📌 {e(symbol)} {L('position check', 'kiểm tra vị thế')}</h1><div class='card'>"
            f"<div class='verdict' style='color:{color}'>{v['verdict']} <span class='muted'>/ {vi}</span></div>"
            f"<ul>{''.join(f'<li>{e(x)}</li>' for x in v['reasons'])}</ul><table>"
            f"<tr><td>{L('Your buy price', 'Giá mua')}</td><td>{price(buy)}</td></tr>"
            f"<tr><td>{L('Last close', 'Giá hiện tại')}</td><td>{price(v['last'])} ({pct(v['gain_pct'])}, "
            f"{L('after fees & tax', 'sau phí thuế')} {pct(v['net_gain_pct'])})</td></tr>"
            f"<tr><td>{L('Stop-loss now', 'Cắt lỗ')}</td><td><b style='color:#b91c1c'>{price(v['stop'])}</b></td></tr>"
            f"<tr><td>{L('Target', 'Mục tiêu')}</td><td><b style='color:#15803d'>{price(v['target'])}</b></td></tr>"
            f"<tr><td>{L('Scanner score', 'Điểm')}</td><td>{score_badge(r['score'])}</td></tr>")
    if v["shares"]:
        body += (f"<tr><td>{L('Position', 'Vị thế')}</td><td>{v['shares']:,.0f} shares ≈ {vnd(v['shares'] * v['last'])}</td></tr>"
                 f"<tr><td>{L('P/L if sold now', 'Lãi/lỗ nếu bán')}</td><td>{vnd(v['pnl_if_sold'])}</td></tr>")
    body += "</table><p class='small'>Remember T+2: shares bought in the last 2 sessions can't be sold yet.</p></div>"
    body += "<div class='card'><h3>Signals now</h3><ul>" + "".join(f"<li>✅ {e(x)}</li>" for x in r["reasons"][:5]) + \
            "".join(f"<li>⚠️ {e(x)}</li>" for x in r["risks"][:5]) + "</ul></div>"
    emailer.send(cfg, f"VN Scanner position | {symbol} | {v['verdict']}", page("", body), log)
    return 0


# ================================================================ self-test
def cmd_selftest(cfg):
    ssi, vc, cf = build_sources(cfg)
    rep = []

    def t(name, fn):
        try:
            out = fn()
            rep.append((name, "OK", out))
            log(f"{name}: OK")
        except Exception as ex:
            rep.append((name, "FAILED", f"{type(ex).__name__}: {str(ex)[:150]}"))
            log(f"{name}: FAILED")

    d = last_completed_session(cfg.extra_holidays)
    tk = (os.environ.get("SELFTEST_TICKER") or "FPT").upper()
    tk2 = (os.environ.get("SELFTEST_TICKER2") or "VNM").upper()
    if ssi:
        t("SSI login", lambda: (ssi._auth(), "token received")[1])
        t("SSI DailyStockPrice (HOSE, last session)", lambda: _describe_rows(ssi.daily_market(d, "HOSE")))
    else:
        rep.append(("SSI FastConnect", "SKIPPED", "No SSI_CONSUMER_ID / SSI_CONSUMER_SECRET secrets (free sources will be used)"))
    if vc:
        t("Vietcap listing", lambda: f"{len(vc.listing())} stocks")
        t(f"Vietcap price history ({tk}, {tk2})", lambda: _describe_rows(vc.history([tk, tk2], days=30)))
        t("Vietcap price board (foreign today)", lambda: _describe_board(vc.board([tk, tk2])))
        t(f"Vietcap news/events ({tk})", lambda: f"{len(vc.news(tk))} items")
        t(f"Vietcap fundamentals ({tk})", lambda: str(vc.fundamentals(tk)))
        t("Vietcap sectors", lambda: f"{len(vc.sectors())} stocks with sector")
    if cf:
        t(f"CafeF foreign history ({tk})", lambda: _describe_rows(cf.foreign(tk, d)))
        t(f"CafeF proprietary/tự doanh ({tk})", lambda: _describe_rows(cf.prop(tk, d)))
    st = State(os.path.join(cfg.data_dir, "state.enc"), cfg.state_password)
    rep.append(("Track record encryption", "OK" if st.ok else "FAILED",
                st.error or ("STATE_PASSWORD set" if cfg.state_password else "STATE_PASSWORD secret missing")))
    rep.append(("Trading day today?", "INFO", str(is_trading_day(today_vn(), cfg.extra_holidays))))
    rows = "".join(f"<tr><td>{e(n)}</td><td><b style='color:{'#15803d' if s == 'OK' else '#b91c1c' if s == 'FAILED' else '#6b7280'}'>{s}</b></td>"
                   f"<td class='small'>{e(o)}</td></tr>" for n, s, o in rep)
    body = (f"<h1>🧪 Data self-test</h1><div class='card'><table>{rows}</table></div>"
            f"<p class='small'>If SSI fails but Vietcap works, the scanner still runs on free data. "
            f"If everything fails, GitHub's servers may be blocked by that site - reply with this email and we'll adjust.</p>")
    emailer.send(cfg, f"VN Scanner self-test | {sum(1 for x in rep if x[1] == 'OK')}/{len(rep)} OK", page("", body), log)
    return 0


def _describe_rows(rows):
    if not rows:
        raise SourceError("0 rows returned")
    r = rows[-1]
    keys = [k for k, v in r.items() if v not in (None, 0, 0.0, "")]
    return f"{len(rows)} rows; latest {r.get('date')}; close {r.get('close')}; fields with data: {', '.join(keys)}"


def _describe_board(res):
    rows, status = res
    if not rows:
        raise SourceError("0 rows")
    r = rows[0]
    return (f"{len(rows)} rows; close {r.get('close')}; volume {r.get('volume')}; foreign buy {r.get('f_buy_vol')}; "
            f"room {r.get('f_room')}; listed {r.get('listed_shares')}; status fields: {status.get(r['symbol'])}")


# ================================================================ CLI
class InputError(ValueError):
    pass


def parse_number(x, kind="price"):
    """Understands 95500, 95.5, 95,5 (VN decimal), 95.500 / 1.234.000 / 20,000,000 (thousand separators),
    -3, -3%, +6,5%. kind: price | pct | count. Returns None for empty input, raises InputError if unreadable."""
    import re
    s = str(x or "").strip().lower()
    for junk in ("₫", "vnd", "đ", "%", " ", "\u00a0"):
        s = s.replace(junk, "")
    if s in ("", "-", "+"):
        return None
    sign = -1 if s.startswith("-") else 1
    s = s.lstrip("+-")
    if not re.fullmatch(r"[\d.,]+", s):
        raise InputError(f"Couldn't read the number '{x}'")
    if s.count(".") + s.count(",") == 0:
        v = float(s)
    elif s.count(".") > 1 or s.count(",") > 1:
        v = float(s.replace(".", "").replace(",", ""))          # 1.234.000 or 20,000,000
    elif "." in s and "," in s:
        # the last separator is the decimal one: 1.234,5 or 1,234.5
        if s.rfind(",") > s.rfind("."):
            v = float(s.replace(".", "").replace(",", "."))
        else:
            v = float(s.replace(",", ""))
    else:
        sep = "." if "." in s else ","
        whole, frac = s.split(sep)
        if len(frac) == 3 and kind != "pct":
            v = float(whole + frac)                              # 95.500 / 1,000 = thousands separator
        else:
            v = float(whole + "." + frac)                        # 95.5 / 95,5 / 6,5% = decimal
    return sign * v


def run_from_event(what):
    """Read workflow_dispatch inputs from GitHub's event file (keeps them out of the public logs)."""
    import json
    cfg = Settings()
    emailer.LANG = cfg.language if cfg.language in ("en", "vi", "both") else "both"
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as fh:
        inp = json.load(fh).get("inputs") or {}
    g = lambda k: str(inp.get(k) or "").strip()
    ticker = g("ticker").upper() or "FPT"
    try:
        if what == "check-stock":
            return cmd_check_stock(cfg, ticker)
        try:
            buy, gain = parse_number(g("buy_price"), "price"), parse_number(g("gain_pct"), "pct")
            shares, value = parse_number(g("shares"), "count"), parse_number(g("position_vnd"), "count")
        except InputError as ex:
            emailer.send(cfg, f"VN Scanner position | {ticker} | check inputs",
                         page("", f"<div class='card warn'>{e(ex)}. Examples: 95500, 95.5, 95,5, 1.234.000, -3</div>"), log)
            return 0
        return cmd_check_position(cfg, ticker, buy, gain, shares, value, g("buy_date") or None)
    except Exception as ex:
        log(f"ERROR: {type(ex).__name__}")
        emailer.send(cfg, "VN Scanner | ERROR", page("", f"<pre style='white-space:pre-wrap;font-size:12px'>{e(traceback.format_exc())}</pre>"), log)
        return 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("scan")
    a.add_argument("--session", default="morning", choices=["morning", "close"])
    a.add_argument("--force", action="store_true")
    b = sub.add_parser("check-stock")
    b.add_argument("ticker")
    c = sub.add_parser("check-position")
    c.add_argument("ticker")
    c.add_argument("--buy", default="")
    c.add_argument("--gain", default="")
    c.add_argument("--shares", default="")
    c.add_argument("--value", default="")
    c.add_argument("--buy-date", default="")
    sub.add_parser("selftest")
    d = sub.add_parser("from-event", help="read workflow_dispatch inputs from GitHub's event file (keeps them out of logs)")
    d.add_argument("what", choices=["check-stock", "check-position"])
    args = ap.parse_args(argv)
    if args.cmd == "from-event":
        return run_from_event(args.what)
    cfg = Settings()
    emailer.LANG = cfg.language if cfg.language in ("en", "vi", "both") else "both"

    try:
        if args.cmd == "scan":
            return cmd_scan(cfg, args.session, args.force)
        if args.cmd == "check-stock":
            return cmd_check_stock(cfg, args.ticker)
        if args.cmd == "check-position":
            bd = args.buy_date.strip() or None
            return cmd_check_position(cfg, args.ticker, parse_number(args.buy, "price"), parse_number(args.gain, "pct"),
                                      parse_number(args.shares, "count"), parse_number(args.value, "count"), bd)
        if args.cmd == "selftest":
            return cmd_selftest(cfg)
        ap.print_help()
        return 1
    except Exception as ex:
        # keep public logs free of data; send the details privately by email
        log(f"ERROR: {type(ex).__name__}")
        tb = traceback.format_exc()
        try:
            emailer.send(cfg, "VN Scanner | ERROR", page("", f"<h1>⚠️ Scanner error</h1><div class='card'><pre style='white-space:pre-wrap;font-size:12px'>{e(tb)}</pre></div>"), log)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
