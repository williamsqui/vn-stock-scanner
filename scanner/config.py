"""Settings. Every value can be changed from GitHub -> Settings -> Secrets and variables
-> Actions -> Variables, without touching the code."""
import os


def _num(name, default):
    raw = os.environ.get(name, "")
    raw = raw.strip().replace(",", "").replace("_", "") if raw else ""
    if raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _bool(name, default):
    raw = (os.environ.get(name) or "").strip().lower()
    if raw == "":
        return default
    return raw in ("1", "true", "yes", "y", "on")


def _list(name, default=""):
    raw = os.environ.get(name) or default
    return [x.strip().upper() for x in raw.replace(";", ",").replace(" ", ",").split(",") if x.strip()]


class Settings:
    def __init__(self):
        # Money
        self.bankroll = _num("BANKROLL_VND", 30_000_000)
        self.risk_per_trade_pct = _num("RISK_PER_TRADE_PCT", 2.0)
        self.max_position_pct = _num("MAX_POSITION_PCT", 40.0)
        self.allow_odd_lot = _bool("ALLOW_ODD_LOT", True)
        # Fees (percent of trade value)
        self.fee_buy_pct = _num("FEE_BUY_PCT", 0.15)
        self.fee_sell_pct = _num("FEE_SELL_PCT", 0.15)
        self.sell_tax_pct = _num("SELL_TAX_PCT", 0.10)
        # Picks
        self.min_score = _num("MIN_SCORE", 70)
        self.max_picks = int(_num("MAX_PICKS", 3))
        self.min_reward_risk = _num("MIN_REWARD_RISK", 1.5)
        self.hold_days_max = int(_num("HOLD_DAYS_MAX", 15))
        self.send_empty_email = _bool("SEND_EMPTY_EMAIL", True)
        # Safety filters
        self.min_avg_value_bn = _num("MIN_AVG_VALUE_BN", 10)        # avg daily traded value, billion VND
        self.min_market_cap_bn = _num("MIN_MARKET_CAP_BN", 1000)    # billion VND
        self.min_price = _num("MIN_PRICE_VND", 5000)
        self.exchanges = _list("EXCHANGES", "HOSE,HNX,UPCOM")
        self.exclude = set(_list("EXCLUDE_TICKERS"))
        self.extra_holidays = _list("EXTRA_HOLIDAYS")               # e.g. 2027-01-01,2027-02-05
        # Email
        self.language = (os.environ.get("LANGUAGE") or "both").strip().lower()
        self.gmail_user = os.environ.get("GMAIL_ADDRESS", "").strip()
        self.gmail_pass = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
        self.email_to = (os.environ.get("EMAIL_TO") or self.gmail_user).strip()
        # Data
        self.data_source = (os.environ.get("DATA_SOURCE") or "auto").strip().lower()
        self.ssi_id = os.environ.get("SSI_CONSUMER_ID", "").strip()
        self.ssi_secret = os.environ.get("SSI_CONSUMER_SECRET", "").strip()
        self.state_password = os.environ.get("STATE_PASSWORD", "").strip()
        self.history_days = int(_num("HISTORY_DAYS", 130))
        self.data_dir = os.environ.get("DATA_DIR", "data")
        self.cache_dir = os.environ.get("CACHE_DIR", "cache")
        self.dry_run = _bool("DRY_RUN", False)          # build emails but don't send
        self.out_dir = os.environ.get("OUT_DIR", "out")

    @property
    def round_trip_cost_pct(self):
        return self.fee_buy_pct + self.fee_sell_pct + self.sell_tax_pct


BAND = {"HOSE": 7.0, "HNX": 10.0, "UPCOM": 15.0}
# realistic swing target ranges (percent) and max stop distance by exchange
TARGET_RANGE = {"HOSE": (5.0, 12.0), "HNX": (6.0, 15.0), "UPCOM": (7.0, 18.0)}
STOP_RANGE = {"HOSE": (3.0, 8.0), "HNX": (3.5, 9.0), "UPCOM": (4.0, 10.0)}
