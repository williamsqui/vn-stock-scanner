"""Position sizing and the 'Check my position' verdict."""

from .indicators import atr, round_tick, sma


def suggest_size(plan, score, cfg, n_picks=1):
    entry, stop = plan["entry"], plan["stop"]
    cost_per_share = entry * cfg.round_trip_cost_pct / 100
    risk_per_share = max(entry - stop + cost_per_share, entry * 0.01)
    risk_budget = cfg.bankroll * cfg.risk_per_trade_pct / 100
    conf = 0.6 if score < 70 else 0.8 if score < 78 else 1.0
    max_value = cfg.bankroll * cfg.max_position_pct / 100
    # if taking several picks, never exceed ~95% of the bankroll in total
    max_value = min(max_value, cfg.bankroll * 0.95 / max(1, n_picks))
    shares = min(risk_budget * conf / risk_per_share, max_value / (entry * (1 + cfg.fee_buy_pct / 100)))
    lots = int(shares // 100) * 100
    note = ""
    if lots == 0:
        if cfg.allow_odd_lot and shares >= 1:
            lots = int(shares)
            note = "Odd lot (lô lẻ, under 100 shares): place it on the odd-lot board; fills can be slower."
        else:
            return {"shares": 0, "value": 0, "risk": 0,
                    "note": "Too expensive for your bankroll at this risk level - skip or watch only."}
    value = lots * entry
    fee_buy = value * cfg.fee_buy_pct / 100
    loss_at_stop = lots * (entry - stop) + fee_buy + lots * stop * (cfg.fee_sell_pct + cfg.sell_tax_pct) / 100
    gain_at_target = lots * (plan["target"] - entry) - fee_buy - lots * plan["target"] * (cfg.fee_sell_pct + cfg.sell_tax_pct) / 100
    return {"shares": lots, "value": value, "pct_bankroll": value / cfg.bankroll * 100,
            "loss_at_stop": loss_at_stop, "gain_at_target": gain_at_target, "fee_buy": fee_buy,
            "confidence_factor": conf, "note": note}


def position_verdict(df, exchange, scored, buy_price, cfg, shares=None, position_value=None, buy_date=None):
    """HOLD / TIGHTEN STOP / TAKE PROFIT / SELL with reasons."""
    last = float(df["close"].iloc[-1])
    if shares is None and position_value:
        shares = position_value / buy_price
    shares = shares or 0
    gain_pct = (last / buy_price - 1) * 100
    cost = cfg.round_trip_cost_pct
    net_gain_pct = gain_pct - cost
    a = float(atr(df).iloc[-1])
    ma20 = float(sma(df["close"], 20).iloc[-1])
    ma10 = float(sma(df["close"], 10).iloc[-1])
    info = scored.get("info", {})
    plan = scored.get("plan", {})
    score = scored.get("score", 0)
    rsi = info.get("rsi", 50)
    ext20 = info.get("ext20", 0)
    f_sell = info.get("f_streak_sell", 0)

    # stops
    hard_stop = round_tick(buy_price * (1 - plan.get("stop_pct", 6) / 100), exchange)
    since = df[df["date"] >= buy_date] if buy_date else df.tail(10)
    high_since = float(since["high"].max()) if len(since) else last
    trail = round_tick(high_since - 2.0 * a, exchange)
    breakeven = round_tick(buy_price * (1 + cost / 100), exchange, "up")
    stop = max(hard_stop, trail) if gain_pct > 0 else hard_stop
    if gain_pct >= 5:
        stop = max(stop, breakeven)
    stop = min(stop, round_tick(last * 0.995, exchange))
    target = plan.get("target") or round_tick(last * 1.06, exchange)
    if target <= last:
        target = round_tick(last * (1 + max(plan.get("target_pct", 6), 4) / 100), exchange)

    reasons = []
    verdict = "HOLD"
    if last <= hard_stop:
        verdict = "SELL"
        reasons.append(f"Price {last:,.0f} is at/below your stop-loss zone ({hard_stop:,.0f}).")
    elif score < 35 and last < ma20:
        verdict = "SELL"
        reasons.append(f"Scanner score has fallen to {score:.0f}/100 and price is below MA20 ({ma20:,.0f}).")
    elif f_sell >= 5 and last < ma10:
        verdict = "SELL"
        reasons.append(f"Foreign investors have net sold {f_sell} sessions in a row and price lost MA10.")
    elif net_gain_pct >= max(plan.get("target_pct", 8), 8) or rsi > 78 or ext20 > 0.18 or scored.get("ceil_streak", 0) >= 3:
        verdict = "TAKE PROFIT"
        if net_gain_pct >= 8:
            reasons.append(f"You're up {net_gain_pct:.1f}% after fees - a solid swing gain.")
        if rsi > 78 or ext20 > 0.18:
            reasons.append(f"Overheated: RSI {rsi:.0f}, {ext20 * 100:.0f}% above MA20. Consider selling half and trailing the rest.")
        if scored.get("ceil_streak", 0) >= 3:
            reasons.append("Several ceiling days in a row - these often reverse sharply.")
    elif (gain_pct > 4 and (score < 55 or last < ma10)) or (gain_pct > 0 and 35 <= score < 50):
        verdict = "TIGHTEN STOP"
        reasons.append(f"Momentum is fading (score {score:.0f}, price vs MA10 {last / ma10 * 100 - 100:+.1f}%). "
                       f"Protect the gain.")
    else:
        reasons.append(f"Trend intact (score {score:.0f}/100, price {'above' if last > ma20 else 'below'} MA20).")
    if gain_pct < 0 and verdict == "HOLD":
        reasons.append(f"You're down {abs(gain_pct):.1f}%. Stick to the stop; don't average down on a falling stock.")
    pnl = shares * (last - buy_price) - shares * buy_price * cfg.fee_buy_pct / 100 \
        - shares * last * (cfg.fee_sell_pct + cfg.sell_tax_pct) / 100
    return {"verdict": verdict, "reasons": reasons, "last": last, "gain_pct": gain_pct, "net_gain_pct": net_gain_pct,
            "stop": stop, "target": target, "shares": shares, "pnl_if_sold": pnl, "ma20": ma20, "atr": a,
            "high_since": high_since}
