"""Track record of past picks, stored ENCRYPTED in data/state.enc so the public repo
doesn't reveal them. Key = your STATE_PASSWORD secret."""
import base64
import hashlib
import json
import os

from cryptography.fernet import Fernet, InvalidToken

BUCKETS = [(80, 101, "80-100"), (70, 80, "70-79"), (60, 70, "60-69"), (0, 60, "<60")]


def _fernet(password):
    key = base64.urlsafe_b64encode(hashlib.sha256(("vnscan:" + password).encode()).digest())
    return Fernet(key)


class State:
    def __init__(self, path, password):
        self.path, self.password = path, password
        self.data = {"picks": [], "version": 1}
        self._orig = None
        self.ok = bool(password)
        self.error = None
        if os.path.exists(path) and password:
            try:
                with open(path, "rb") as f:
                    self.data = json.loads(_fernet(password).decrypt(f.read()))
                self._orig = json.dumps(self.data, sort_keys=True)
            except InvalidToken:
                self.ok = False
                self.error = "STATE_PASSWORD doesn't match the saved track record (was it changed?)."
            except Exception as e:
                self.ok = False
                self.error = f"Couldn't read track record ({type(e).__name__})."

    def save(self):
        if not self.ok:
            return False
        if self._orig is not None and json.dumps(self.data, sort_keys=True) == self._orig:
            return True          # nothing changed - don't create a new commit
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "wb") as f:
            f.write(_fernet(self.password).encrypt(json.dumps(self.data, separators=(",", ":")).encode()))
        return True

    # ---------------------------------------------------------------
    def add_pick(self, p):
        pid = f"{p['signal_date']}:{p['symbol']}"
        if any(x["id"] == pid for x in self.data["picks"]):
            return
        self.data["picks"].append({"id": pid, "status": "open", **p})

    def open_picks(self):
        return [p for p in self.data["picks"] if p["status"] in ("open", "pending")]

    def evaluate(self, series_fn, cfg):
        """Update open picks with new price bars. series_fn(symbol) -> DataFrame."""
        changed = []
        cost = cfg.round_trip_cost_pct
        for p in self.data["picks"]:
            if p["status"] not in ("open", "pending"):
                continue
            df = series_fn(p["symbol"])
            if df is None or len(df) == 0:
                continue
            bars = df[df["date"] > p["signal_date"]].reset_index(drop=True)
            if len(bars) == 0:
                p["status"] = "pending"
                continue
            b0 = bars.iloc[0]
            if b0["low"] > p["entry_max"]:
                p.update(status="no_fill", exit_date=b0["date"])
                changed.append(p)
                continue
            o = b0["open"]
            entry = min(float(o), p["entry_max"]) if o == o and o else p["entry_max"]
            p["entry_fill"] = entry
            done = False
            last_i = max(2, cfg.hold_days_max - 1)     # T0..T(n-1) = n sessions held
            for i in range(2, len(bars)):
                b = bars.iloc[i]
                exit_px, why = None, None
                if i == 2:
                    # shares arrive in the T+2 afternoon: we only know the close for sure
                    if b["close"] <= p["stop"]:
                        exit_px, why = float(b["close"]), "stop"
                    elif b["close"] >= p["target"]:
                        exit_px, why = p["target"], "target"
                elif b["open"] <= p["stop"]:
                    exit_px, why = float(b["open"]), "stop"
                elif b["low"] <= p["stop"]:
                    exit_px, why = p["stop"], "stop"
                elif b["high"] >= p["target"]:
                    exit_px, why = p["target"], "target"
                if exit_px is None and i >= last_i:
                    exit_px, why = float(b["close"]), "expired"
                if exit_px is not None:
                    p.update(status=why, exit_price=exit_px, exit_date=b["date"], days=i,
                             ret_net_pct=round((exit_px / entry - 1) * 100 - cost, 2))
                    changed.append(p)
                    done = True
                    break
            if not done:
                p["status"] = "open"
                p["last_close"] = float(bars.iloc[-1]["close"])
                p["unrealised_pct"] = round((p["last_close"] / entry - 1) * 100 - cost, 2)
                p["days"] = len(bars) - 1
        return changed

    def stats(self):
        rows = []
        closed_all = [p for p in self.data["picks"] if p["status"] in ("target", "stop", "expired")]
        for lo, hi, name in BUCKETS:
            ps = [p for p in closed_all if lo <= p["score"] < hi]
            n = len(ps)
            if n == 0 and name == "<60":
                continue
            hit = sum(p["status"] == "target" for p in ps)
            stop = sum(p["status"] == "stop" for p in ps)
            exp = sum(p["status"] == "expired" for p in ps)
            avg = sum(p.get("ret_net_pct", 0) for p in ps) / n if n else None
            rows.append({"bucket": name, "n": n, "hit": hit, "stop": stop, "expired": exp,
                         "hit_rate": hit / n * 100 if n else None, "avg_ret": avg})
        n_all = len(closed_all)
        tot = {"n": n_all, "open": len(self.open_picks()),
               "hit_rate": sum(p["status"] == "target" for p in closed_all) / n_all * 100 if n_all else None,
               "avg_ret": sum(p.get("ret_net_pct", 0) for p in closed_all) / n_all if n_all else None,
               "no_fill": sum(p["status"] == "no_fill" for p in self.data["picks"])}
        return rows, tot

    def recent_closed(self, n=5):
        c = [p for p in self.data["picks"] if p["status"] in ("target", "stop", "expired")]
        return sorted(c, key=lambda p: p.get("exit_date", ""), reverse=True)[:n]
