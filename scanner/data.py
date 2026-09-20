"""Collects and stores market data. Price history is kept in the GitHub Actions cache
(not in git), so the public repo doesn't grow and nothing about picks is exposed."""
import json
import os
from datetime import date, timedelta

import pandas as pd

from .calendar_vn import trading_days_back, last_completed_session, today_vn
from .net import SourceError

COLS = ["date", "symbol", "exchange", "open", "high", "low", "close", "volume", "value", "ref", "ceiling", "floor",
        "f_buy_vol", "f_sell_vol", "f_buy_val", "f_sell_val", "f_net_val", "f_room", "prop_net_val"]


class DataManager:
    def __init__(self, settings, ssi=None, vietcap=None, cafef=None, log=print):
        self.cfg = settings
        self.ssi, self.vc, self.cf = ssi, vietcap, cafef
        self.log = log
        os.makedirs(settings.cache_dir, exist_ok=True)
        self.store_path = os.path.join(settings.cache_dir, "prices.csv.gz")
        self.meta_path = os.path.join(settings.cache_dir, "meta.json")
        self.df = self._load()
        self.meta = self._load_meta()
        self.status_flags = {}
        self.source_report = {}
        self.used_source = None

    # ---------- persistence ----------
    def _load(self):
        if os.path.exists(self.store_path):
            try:
                df = pd.read_csv(self.store_path, dtype={"symbol": str, "date": str, "exchange": str})
                for c in COLS:
                    if c not in df.columns:
                        df[c] = None
                return df[COLS]
            except Exception:
                self.log("Price cache unreadable - rebuilding.")
        return pd.DataFrame(columns=COLS)

    def _load_meta(self):
        try:
            with open(self.meta_path) as f:
                return json.load(f)
        except Exception:
            return {"symbols": {}, "sectors": {}, "updated": None}

    def save(self):
        keep = set(d.isoformat() for d in trading_days_back(today_vn(), self.cfg.history_days + 20,
                                                            self.cfg.extra_holidays))
        df = self.df[self.df["date"].isin(keep)] if len(self.df) else self.df
        df.sort_values(["symbol", "date"]).to_csv(self.store_path, index=False, compression="gzip")
        with open(self.meta_path, "w") as f:
            json.dump(self.meta, f)

    def upsert(self, rows, prefer_new=True):
        """Insert/merge rows. prefer_new=False only fills gaps (used for fallback sources)."""
        if not rows:
            return
        new = pd.DataFrame(rows)
        for c in COLS:
            if c not in new.columns:
                new[c] = None
        new = new[COLS]
        if len(self.df) == 0:
            self.df = new.drop_duplicates(["date", "symbol"], keep="last")
            return
        old = self.df.set_index(["date", "symbol"])
        new = new.drop_duplicates(["date", "symbol"], keep="last").set_index(["date", "symbol"])
        both = old.index.intersection(new.index)
        if len(both):
            # new non-null values win; keep old values where new is missing
            merged = new.loc[both].combine_first(old.loc[both]) if prefer_new \
                else old.loc[both].combine_first(new.loc[both])
            old = old.drop(both)
            new = pd.concat([new.drop(both), merged])
        self.df = pd.concat([old, new]).reset_index()[COLS]

    # ---------- universe / meta ----------
    def refresh_meta(self, force=False):
        upd = self.meta.get("updated")
        if not force and upd and (date.fromisoformat(upd) > today_vn() - timedelta(days=6)) and self.meta["symbols"]:
            return
        syms = {}
        if self.vc:
            try:
                syms = self.vc.listing()
                self.source_report["Vietcap listing"] = f"OK ({len(syms)} stocks)"
            except Exception as e:
                self.source_report["Vietcap listing"] = f"FAILED ({type(e).__name__})"
        if self.ssi:
            for ex in ("HOSE", "HNX", "UPCOM"):
                try:
                    for s, info in self.ssi.listed_shares(ex).items():
                        syms.setdefault(s, {"exchange": ex, "name": s})
                        syms[s]["exchange"] = ex
                        if info.get("listed_shares"):
                            syms[s]["listed_shares"] = info["listed_shares"]
                    self.source_report[f"SSI securities {ex}"] = "OK"
                except Exception as e:
                    self.source_report[f"SSI securities {ex}"] = f"FAILED ({type(e).__name__})"
        if syms:
            for s, info in syms.items():
                old = self.meta["symbols"].get(s, {})
                old.update({k: v for k, v in info.items() if v is not None})
                self.meta["symbols"][s] = old
            self.meta["updated"] = today_vn().isoformat()
        if self.vc:
            try:
                sec = self.vc.sectors()
                if sec:
                    self.meta["sectors"] = sec
            except Exception:
                pass

    def universe(self):
        if len(self.df):
            # fill gaps in the symbol list from the price data itself
            last = self.df.dropna(subset=["exchange"]).drop_duplicates("symbol", keep="last")
            for s, ex in zip(last["symbol"], last["exchange"]):
                if ex in ("HOSE", "HNX", "UPCOM"):
                    self.meta["symbols"].setdefault(s, {"exchange": ex, "name": s})
                    self.meta["symbols"][s].setdefault("exchange", ex)
        return {s: i for s, i in self.meta["symbols"].items() if i.get("exchange") in self.cfg.exchanges}

    # ---------- price/flow updates ----------
    def update(self):
        """Bring the store up to the last completed session. Returns the as-of date string."""
        asof = last_completed_session(self.cfg.extra_holidays)
        days = trading_days_back(asof, self.cfg.history_days, self.cfg.extra_holidays)
        have = self.df.groupby("date")["symbol"].count().to_dict() if len(self.df) else {}
        prefer_ssi = self.ssi is not None and self.cfg.data_source in ("auto", "ssi")
        ok = False
        if prefer_ssi:
            ok = self._update_ssi(days, have)
        if not ok and self.vc is not None and self.cfg.data_source != "ssi":
            ok = self._update_vietcap(days, have, asof)
        elif ok and self.vc is not None and self.cfg.data_source != "ssi" and \
                (self.df["date"].max() or "") < asof.isoformat():
            # SSI hasn't published the latest session yet: fill just the gap from the free source
            have = self.df.groupby("date")["symbol"].count().to_dict()
            if self._update_vietcap(days, have, asof):
                self.used_source = "SSI FastConnect + Vietcap (latest day)"
        if not ok and len(self.df) == 0:
            raise SourceError("No data source worked")
        last = self.df["date"].max() if len(self.df) else None
        return last

    def _update_ssi(self, days, have):
        full = max(have.values()) if have else 0
        missing = [d for d in days if have.get(d.isoformat(), 0) < max(1, full * 0.7) or
                   self._foreign_missing(d.isoformat())]
        # always refresh the last session (final foreign numbers can arrive late)
        if days[-1] not in missing:
            missing.append(days[-1])
        fetched = 0
        try:
            for d in missing:
                rows = []
                for ex in ("HOSE", "HNX", "UPCOM"):
                    rows += self.ssi.daily_market(d, ex)
                self.upsert(rows)
                fetched += len(rows)
            self.source_report["SSI daily prices + foreign flows"] = f"OK ({len(missing)} days, {fetched} rows)"
            if len(self.df) == 0:
                return False           # SSI answered but gave nothing usable -> try the free source
            # 0 rows for the newest day just means SSI hasn't published it yet (stale check handles it)
            self.used_source = "SSI FastConnect"
            return True
        except Exception as e:
            self.source_report["SSI daily prices + foreign flows"] = f"FAILED ({type(e).__name__}: {str(e)[:60]})"
            return False

    def _foreign_missing(self, d):
        sub = self.df[self.df["date"] == d]
        return len(sub) > 0 and sub["f_net_val"].isna().mean() > 0.5

    def _update_vietcap(self, days, have, asof):
        uni = list(self.universe().keys())
        if not uni:
            self.source_report["Vietcap prices"] = "FAILED (no symbol list)"
            return False
        existing = sorted(have.keys())
        if existing and existing[-1] >= days[-6].isoformat():
            n = 8
        else:
            n = len(days) + 5
        try:
            rows = self.vc.history(uni, days=n)
            for r in rows:
                r["exchange"] = self.meta["symbols"].get(r["symbol"], {}).get("exchange")
            # drop partial (today, still trading) bars
            rows = [r for r in rows if r["date"] and r["date"] <= asof.isoformat()]
            self.upsert(rows, prefer_new=False)      # never overwrite SSI data with Vietcap data
            self.source_report["Vietcap price history"] = f"OK ({len(rows)} rows)"
        except Exception as e:
            self.source_report["Vietcap price history"] = f"FAILED ({type(e).__name__})"
            return False
        # Board snapshot: only meaningful after the close of `asof`
        try:
            brows, status = self.vc.board(uni)
            live = [r for r in brows if r.get("volume")]
            if today_vn() == asof and live:
                for r in live:
                    r["date"] = asof.isoformat()
                    if r.get("listed_shares"):
                        self.meta["symbols"].setdefault(r["symbol"], {})["listed_shares"] = r["listed_shares"]
                    r.pop("listed_shares", None)
                self.upsert(live, prefer_new=False)
                self.source_report["Vietcap board (foreign flows today)"] = f"OK ({len(live)} stocks)"
            else:
                for r in brows:
                    if r.get("listed_shares"):
                        self.meta["symbols"].setdefault(r["symbol"], {})["listed_shares"] = r["listed_shares"]
                self.source_report["Vietcap board (foreign flows today)"] = "skipped (market not closed today)"
            self.status_flags = status
        except Exception as e:
            self.source_report["Vietcap board (foreign flows today)"] = f"FAILED ({type(e).__name__})"
        self.used_source = "Vietcap (free)"
        return True

    # ---------- per-symbol ----------
    def series(self, symbol):
        s = self.df[self.df["symbol"] == symbol].sort_values("date").reset_index(drop=True)
        for c in COLS[3:]:
            s[c] = pd.to_numeric(s[c], errors="coerce")
        return s

    def ensure_symbol(self, symbol):
        """For 'Check a stock': fetch/refresh one symbol without touching the rest of the store."""
        end = last_completed_session(self.cfg.extra_holidays)
        ex = self.meta["symbols"].get(symbol, {}).get("exchange")
        if self.ssi and self.cfg.data_source in ("auto", "ssi"):
            try:
                rows = self.ssi.symbol_history(symbol, end - timedelta(days=int(self.cfg.history_days * 1.5)), end)
                for r in rows:
                    r["exchange"] = r.get("exchange") or ex
                if rows:
                    self.upsert(rows)
                if len(rows) >= 30:
                    return
            except Exception:
                pass
        if self.vc and self.cfg.data_source != "ssi":
            try:
                rows = self.vc.history([symbol], days=self.cfg.history_days)
                rows = [r for r in rows if r["date"] and r["date"] <= end.isoformat()]   # no half-finished day
                for r in rows:
                    r["exchange"] = ex
                self.upsert(rows, prefer_new=False)
            except Exception:
                pass

    def enrich_flows(self, symbol, asof):
        """Fill foreign history from CafeF when the store lacks it, and add prop trading."""
        s = self.series(symbol)
        end = date.fromisoformat(asof)
        if self.cf is None:
            return
        need_foreign = len(s) == 0 or s.tail(20)["f_net_val"].isna().mean() > 0.3
        if need_foreign:
            try:
                rows = self.cf.foreign(symbol, end)
                for r in rows:
                    r["symbol"] = symbol
                self.upsert([r for r in rows if r["date"] in set(s["date"])])
                self.source_report["CafeF foreign history"] = "OK"
            except Exception as e:
                self.source_report["CafeF foreign history"] = f"FAILED ({type(e).__name__})"
        try:
            rows = self.cf.prop(symbol, end)
            for r in rows:
                r["symbol"] = symbol
            self.upsert([r for r in rows if r["date"] in set(s["date"])])
            self.source_report["CafeF proprietary (tự doanh)"] = "OK" if rows else "no data"
        except Exception as e:
            self.source_report["CafeF proprietary (tự doanh)"] = f"FAILED ({type(e).__name__})"

    def news(self, symbol):
        if not self.vc:
            return []
        try:
            return self.vc.news(symbol)
        except Exception:
            return []

    def fundamentals(self, symbol):
        if not self.vc:
            return {}
        try:
            return self.vc.fundamentals(symbol)
        except Exception:
            return {}

    def sector_of(self, symbol):
        return self.meta.get("sectors", {}).get(symbol)

    def peers(self, symbol, liquid, n=8):
        sec = self.sector_of(symbol)
        if not sec:
            return []
        return [s for s in liquid if s != symbol and self.meta["sectors"].get(s) == sec][:n]
