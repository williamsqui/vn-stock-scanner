"""Scoring model. Each signal group gives a 0..1 sub-score; the weighted average of the
groups that have data becomes 0..100, then penalties for chasing/pump patterns are applied.
Weights reflect the user's priority order. This is a heuristic, not a prediction."""
import math
import numpy as np

from .config import BAND, TARGET_RANGE, STOP_RANGE
from .indicators import sma, rsi, atr, ceiling_hit, floor_hit, consecutive_true, round_tick

WEIGHTS = {"foreign": 30, "prop": 5, "volume": 15, "chart": 30, "fundamental": 10, "news": 10}
MIN_BARS = 45


def clip(x, a=0.0, b=1.0):
    if x is None or x != x:      # NaN -> worst case, never best case
        return a
    return max(a, min(b, x))


def fmt_bn(v):
    return f"{v / 1e9:,.1f} tỷ"


# ---------------------------------------------------------------- signal groups
def foreign_signal(df, avg_val20):
    f = df["f_net_val"]
    if f.tail(10).isna().mean() > 0.3 or avg_val20 <= 0:
        return None, [], [], {}
    f = f.fillna(0)
    last5, prev10 = f.tail(5).sum(), f.iloc[-15:-5].sum() if len(f) >= 15 else 0
    last20 = f.tail(20).sum()
    r5 = last5 / (avg_val20 * 5)
    r20 = last20 / (avg_val20 * 20)
    streak_buy = consecutive_true(f > 0)
    streak_sell = consecutive_true(f < 0)
    switch = prev10 < 0 and last5 > 0 and (f.tail(3) > 0).sum() >= 2
    heavy_sell = r5 < -0.08 or streak_sell >= 5
    sub = 0.5 + 0.28 * math.tanh(r5 / 0.06) + 0.1 * math.tanh(r20 / 0.05) + 0.08 * min(streak_buy, 6) / 6
    sub += 0.1 if switch else 0
    sub -= 0.15 if heavy_sell else 0
    pos, neg = [], []
    if last5 > 0 and r5 > 0.02:
        pos.append(f"Foreign net BUY {fmt_bn(last5)} in 5 sessions ({r5 * 100:.0f}% of avg daily value) / Khối ngoại mua ròng")
    if streak_buy >= 3:
        pos.append(f"Foreign buying streak: {streak_buy} sessions in a row / Chuỗi mua ròng {streak_buy} phiên")
    if switch:
        pos.append("Foreigners switched from net selling to net buying / Khối ngoại đảo chiều sang mua")
    if heavy_sell:
        neg.append(f"Heavy foreign selling: {fmt_bn(-min(last5, 0))} net sold in 5 sessions"
                   f"{f' ({streak_sell} days in a row)' if streak_sell >= 3 else ''} / Khối ngoại bán mạnh")
    room = df["f_room"].dropna()
    info = {"f_net_5d": last5, "f_net_20d": last20, "f_streak_buy": streak_buy, "f_streak_sell": streak_sell}
    if len(room):
        info["f_room"] = float(room.iloc[-1])
        if room.iloc[-1] <= 0:
            neg.append("Foreign room is FULL (hết room) - foreign buying can't add more")
            sub -= 0.08
    return clip(sub), pos, neg, info


def prop_signal(df, avg_val20):
    p = df["prop_net_val"]
    if p.tail(10).notna().sum() < 3 or avg_val20 <= 0:
        return None, [], []
    p = p.fillna(0)
    r = p.tail(5).sum() / (avg_val20 * 5)
    sub = clip(0.5 + 0.35 * math.tanh(r / 0.04))
    pos, neg = [], []
    if r > 0.01:
        pos.append(f"Proprietary desks (tự doanh) net bought {fmt_bn(p.tail(5).sum())} in 5 sessions")
    elif r < -0.02:
        neg.append(f"Proprietary desks (tự doanh) net sold {fmt_bn(-p.tail(5).sum())} in 5 sessions")
    return sub, pos, neg


def volume_signal(df):
    v = df["volume"]
    avg20 = v.iloc[-21:-1].mean()
    if not avg20 or np.isnan(avg20):
        return None, [], [], {}
    ratio = v.iloc[-1] / avg20
    r5 = v.tail(5).mean() / avg20
    c, o = df["close"], df["open"]
    up_day = c.iloc[-1] > c.iloc[-2] and c.iloc[-1] >= o.iloc[-1]
    down_day = c.iloc[-1] < c.iloc[-2]
    sub = 0.5
    pos, neg = [], []
    if ratio >= 1.8 and up_day:
        sub += 0.3
        pos.append(f"Volume spike on an up day: {ratio:.1f}x the 20-day average / Khối lượng đột biến")
    elif ratio >= 1.3 and up_day:
        sub += 0.15
        pos.append(f"Above-average volume on an up day ({ratio:.1f}x)")
    elif ratio >= 1.8 and down_day:
        sub -= 0.3
        neg.append(f"Volume spike on a DOWN day ({ratio:.1f}x) - possible distribution / phân phối")
    if 1.2 <= r5 <= 3:
        sub += 0.1
    # up-volume vs down-volume over 10 days
    chg = c.diff().tail(10)
    upv, dnv = v.tail(10)[chg > 0].sum(), v.tail(10)[chg < 0].sum()
    if upv > 1.5 * dnv and upv > 0:
        sub += 0.1
        pos.append("More volume on up days than down days (accumulation)")
    elif dnv > 1.5 * upv and dnv > 0:
        sub -= 0.1
    return clip(sub), pos, neg, {"vol_ratio": ratio, "vol_ratio5": r5}


def chart_signal(df, exchange):
    c, h, l = df["close"], df["high"], df["low"]
    ma20, ma50 = sma(c, 20), sma(c, 50)
    r = rsi(c)
    last = c.iloc[-1]
    pos, neg, sub = [], [], 0.5
    m20, m50 = ma20.iloc[-1], ma50.iloc[-1]
    slope20 = (ma20.iloc[-1] / ma20.iloc[-6] - 1) if not np.isnan(ma20.iloc[-6]) else 0
    if last > m20 > m50:
        sub += 0.15
        pos.append("Uptrend: price above MA20 above MA50 / Xu hướng tăng")
    elif last < m20 < m50:
        sub -= 0.2
        neg.append("Downtrend: price below MA20 below MA50 / Xu hướng giảm")
    if slope20 > 0.01:
        sub += 0.05
    # higher lows (three 10-day windows)
    lows = [l.iloc[-30:-20].min(), l.iloc[-20:-10].min(), l.iloc[-10:].min()]
    if lows[0] < lows[1] < lows[2]:
        sub += 0.1
        pos.append("Higher lows over the last 30 sessions / Đáy sau cao hơn đáy trước")
    # breakout
    prior_high20 = h.iloc[-21:-1].max()
    high60 = h.tail(60).max()
    vol_ratio = df["volume"].iloc[-1] / max(df["volume"].iloc[-21:-1].mean(), 1)
    breakout = last > prior_high20 and vol_ratio >= 1.3
    if breakout:
        sub += 0.15
        pos.append(f"Breakout above the 20-day high on {vol_ratio:.1f}x volume / Vượt đỉnh 20 phiên")
    dist_high = last / high60 - 1
    if -0.06 <= dist_high <= 0 and not breakout:
        sub += 0.05
        pos.append(f"Just {abs(dist_high) * 100:.1f}% below the 60-day high")
    elif dist_high < -0.25:
        sub -= 0.1
        neg.append(f"{abs(dist_high) * 100:.0f}% below the 60-day high (weak)")
    # momentum/overextension
    rv = r.iloc[-1]
    ext20 = last / m20 - 1
    ret10 = last / c.iloc[-11] - 1
    over = False
    if 50 <= rv <= 68:
        sub += 0.07
    if rv > 80 or (rv > 75 and ret10 > 0.15) or ext20 > {"HOSE": 0.12, "HNX": 0.15, "UPCOM": 0.2}.get(exchange, 0.12):
        sub -= 0.2
        over = True
        neg.append(f"Overextended: RSI {rv:.0f}, {ext20 * 100:.0f}% above MA20 - chasing risk / Tăng nóng")
    if rv < 35:
        sub -= 0.05
    info = {"rsi": rv, "ma20": m20, "ma50": m50, "ext20": ext20, "ret10": ret10, "breakout": breakout,
            "dist_high60": dist_high, "high60": high60, "prior_high20": prior_high20, "overextended": over,
            "swing_low": l.tail(7).min()}
    return clip(sub), pos, neg, info


def fundamental_signal(fund, sector_pe):
    if not fund:
        return None, [], [], {}
    pe, g = fund.get("pe"), fund.get("profit_growth")
    if pe is None and g is None:
        return None, [], [], {}
    sub, pos, neg = 0.5, [], []
    if g is not None:
        if g > 0.3:
            sub += 0.25
            pos.append(f"Profit growth {g * 100:.0f}% year-on-year / LNST tăng mạnh")
        elif g > 0.1:
            sub += 0.12
            pos.append(f"Profit growth {g * 100:.0f}% YoY")
        elif g < -0.2:
            sub -= 0.25
            neg.append(f"Profit falling {g * 100:.0f}% YoY / LNST giảm")
    if pe is not None:
        if pe <= 0:
            sub -= 0.2
            neg.append("Loss-making (negative P/E)")
        elif sector_pe:
            rel = pe / sector_pe
            if rel < 0.85:
                sub += 0.12
                pos.append(f"P/E {pe:.1f} vs sector ~{sector_pe:.1f} (cheaper than peers)")
            elif rel > 1.6:
                sub -= 0.12
                neg.append(f"P/E {pe:.1f} vs sector ~{sector_pe:.1f} (expensive)")
        elif pe > 40:
            sub -= 0.1
            neg.append(f"High P/E {pe:.0f}")
    return clip(sub), pos, neg, {"pe": pe, "profit_growth": g, "sector_pe": sector_pe}


# ---------------------------------------------------------------- main
def base_metrics(df):
    avg_val20 = float(df["value"].tail(20).mean() or 0)
    return {"avg_val20": avg_val20, "close": float(df["close"].iloc[-1])}


def score_stock(df, exchange, cfg, market=None, fund=None, sector_pe=None, news=None, listed_shares=None,
                excluded_manual=False):
    """Returns a result dict. `df` = per-symbol daily data sorted by date."""
    res = {"exchange": exchange, "filters_failed": [], "reasons": [], "risks": [], "coverage": []}
    if df is None or len(df) < MIN_BARS:
        res.update(score=0, filters_failed=["Not enough price history (new listing or data gap)"])
        return res
    band = BAND.get(exchange, 7.0)
    m = base_metrics(df)
    last = m["close"]
    res.update(close=last, date=df["date"].iloc[-1], avg_val20=m["avg_val20"])
    mcap = listed_shares * last if listed_shares else (fund or {}).get("market_cap")
    res["market_cap"] = mcap

    # ---------- safety filters ----------
    ff = res["filters_failed"]
    if excluded_manual:
        ff.append("On your EXCLUDE_TICKERS list")
    if m["avg_val20"] < cfg.min_avg_value_bn * 1e9:
        ff.append(f"Low liquidity: avg {fmt_bn(m['avg_val20'])}/day < {cfg.min_avg_value_bn:g} tỷ")
    if mcap is not None and mcap < cfg.min_market_cap_bn * 1e9:
        ff.append(f"Small company: market cap {fmt_bn(mcap)} < {cfg.min_market_cap_bn:,.0f} tỷ")
    if last < cfg.min_price:
        ff.append(f"Price below {cfg.min_price:,.0f} VND (penny stock)")
    zero_days = int((df["volume"].tail(10) <= 0).sum())
    if zero_days >= 3:
        ff.append("No trading on several recent days (possible suspension)")
    if news and news.get("exclude"):
        ff.append("Exchange watch-list notice: " + news["exclude_reason"])

    ceil = ceiling_hit(df, band)
    flo = floor_hit(df, band)
    ceil_streak = consecutive_true(ceil)
    ceil_recent = int(ceil.tail(5).sum())
    res["ceiling_streak"] = ceil_streak

    # ---------- signals ----------
    subs = {}
    fsub, fp, fn, finfo = foreign_signal(df, m["avg_val20"])
    subs["foreign"] = fsub
    psub, pp, pn = prop_signal(df, m["avg_val20"])
    subs["prop"] = psub
    vsub, vp, vn, vinfo = volume_signal(df)
    subs["volume"] = vsub
    csub, cp, cn, cinfo = chart_signal(df, exchange)
    subs["chart"] = csub
    usub, up_, un, uinfo = fundamental_signal(fund, sector_pe)
    subs["fundamental"] = usub
    subs["news"] = news["sub"] if news and news.get("count") else None

    reasons = fp + pp + cp + vp + up_ + ([f"Positive news: {t}" for t in news["positives"][:2]] if news else [])
    risks = fn + pn + cn + vn + un + ([f"Negative news: {t}" for t in news["negatives"][:2]] if news else [])

    num_, den = 0.0, 0.0
    for k, w in WEIGHTS.items():
        if subs.get(k) is not None:
            num_ += w * subs[k]
            den += w
        else:
            res["coverage"].append(k)
    raw = 100 * num_ / den if den else 0
    # stretch around 50 so strong setups can reach the 70-90 range
    score = 50 + (raw - 50) * 1.3

    # ---------- penalties ----------
    pen = 0
    if ceil_streak >= 3:
        pen += 18
        risks.insert(0, f"Hit the ceiling price {ceil_streak} days in a row (tím {ceil_streak} phiên) - high chasing risk")
    elif ceil_streak == 2 or ceil_recent >= 3:
        pen += 10
        risks.insert(0, f"Hit the ceiling {ceil_recent} times in the last 5 sessions - chasing risk")
    if int(flo.tail(5).sum()) >= 2:
        pen += 10
        risks.append("Hit the floor price twice in the last 5 sessions")
    small = mcap is not None and mcap < 3000e9
    vol_ratio = vinfo.get("vol_ratio", 1)
    no_foreign_support = fsub is None or fsub < 0.55
    pump = (cinfo["ret10"] > 0.35 and small and no_foreign_support) or \
           (vol_ratio > 6 and small and cinfo["ret10"] > 0.15) or \
           (news and news.get("hype"))
    if pump:
        pen += 15
        risks.insert(0, "Pump-pattern warning (dấu hiệu 'lùa gà'): fast run-up in a small company without "
                        "foreign support. If you see it hyped in Zalo/Facebook/Telegram groups, stay away.")
    gap = df["open"].iloc[-1] / df["close"].iloc[-2] - 1
    if gap > 0.05:
        pen += 4
    if subs["foreign"] is None:
        score *= 0.93
        risks.append("Foreign-flow data missing for this stock - score is less reliable")
    if market and market.get("weak"):
        score *= 0.92
        risks.append(f"Weak market: only {market['breadth'] * 100:.0f}% of stocks above their 50-day average")
    score = clip(score - pen, 0, 100)
    # T+2 lock
    risks.append(f"T+2: you can't sell until the afternoon of the 3rd session. Two bad days could cost up to "
                 f"~{2 * band:.0f}% on {exchange} before you can exit.")

    res.update(score=round(score, 1), subs=subs, reasons=reasons, risks=risks,
               info={**finfo, **vinfo, **cinfo, **uinfo}, ceil_streak=ceil_streak, pump=bool(pump))
    res["plan"] = exit_plan(df, exchange, cfg, cinfo)
    return res


def exit_plan(df, exchange, cfg, cinfo):
    last = float(df["close"].iloc[-1])
    a = float(atr(df).iloc[-1])
    atr_pct = a / last * 100
    tmin, tmax = TARGET_RANGE.get(exchange, (5, 12))
    smin, smax = STOP_RANGE.get(exchange, (3, 8))
    target_pct = clip(3.5 * atr_pct, tmin, tmax)
    # respect nearby resistance (60-day high) if this isn't a breakout
    res_level = cinfo.get("high60")
    if not cinfo.get("breakout") and res_level and last < res_level:
        to_res = (res_level / last - 1) * 100
        if tmin <= to_res < target_pct:
            target_pct = max(tmin, to_res - 0.3)
    # stop: 1.5x ATR (volatility stop), tightened to the breakout level when that is closer
    stop_pct = clip(1.5 * atr_pct, smin, smax)
    if cinfo.get("breakout") and cinfo.get("prior_high20"):
        pivot_pct = (1 - cinfo["prior_high20"] * 0.985 / last) * 100
        if smin <= pivot_pct < stop_pct:
            stop_pct = pivot_pct
    entry = last
    target = round_tick(entry * (1 + target_pct / 100), exchange, "down")
    stop = round_tick(entry * (1 - stop_pct / 100), exchange, "down")
    target_pct = (target / entry - 1) * 100
    stop_pct = (1 - stop / entry) * 100
    cost = cfg.round_trip_cost_pct
    net_gain = target_pct - cost
    net_loss = stop_pct + cost
    rr = net_gain / net_loss if net_loss > 0 else 0
    return {"entry": entry, "entry_max": round_tick(entry * 1.015, exchange, "down"),
            "target": target, "stop": stop, "target_pct": target_pct, "stop_pct": stop_pct,
            "net_gain_pct": net_gain, "net_loss_pct": net_loss, "rr": rr, "atr_pct": atr_pct,
            "hold_days": f"3-{cfg.hold_days_max} sessions"}


def market_regime(closes_by_symbol):
    """Breadth: share of liquid stocks trading above their 50-day average."""
    above, n = 0, 0
    for c in closes_by_symbol:
        if len(c) >= 50:
            n += 1
            above += c.iloc[-1] > c.tail(50).mean()
    b = above / n if n else 0.5
    return {"breadth": b, "weak": b < 0.35, "strong": b > 0.6, "n": n}
