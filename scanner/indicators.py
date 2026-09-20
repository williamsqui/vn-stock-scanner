"""Technical helpers on a per-symbol daily DataFrame (sorted by date)."""
import numpy as np
import pandas as pd


def sma(s, n):
    return s.rolling(n, min_periods=max(2, int(n * 0.8))).mean()


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def ceiling_hit(df, exchange_band_pct):
    """True where the day closed at (or within a tick of) the ceiling price."""
    ref = df["ref"].where(df["ref"].notna(), df["close"].shift(1))
    ceil = df["ceiling"].where(df["ceiling"].notna(), ref * (1 + exchange_band_pct / 100))
    return (df["close"] >= ceil * 0.997) & (df["close"] > ref)


def floor_hit(df, exchange_band_pct):
    ref = df["ref"].where(df["ref"].notna(), df["close"].shift(1))
    flo = df["floor"].where(df["floor"].notna(), ref * (1 - exchange_band_pct / 100))
    return (df["close"] <= flo * 1.003) & (df["close"] < ref)


def consecutive_true(series):
    n = 0
    for v in reversed(list(series)):
        if bool(v):
            n += 1
        else:
            break
    return n


def tick_size(price, exchange):
    if exchange == "HOSE":
        if price < 10_000:
            return 10
        if price < 50_000:
            return 50
        return 100
    return 100


def round_tick(price, exchange, mode="down"):
    t = tick_size(price, exchange)
    if mode == "down":
        return float(np.floor(price / t) * t)
    if mode == "up":
        return float(np.ceil(price / t) * t)
    return float(round(price / t) * t)
