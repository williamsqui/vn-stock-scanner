"""Small HTTP helper with retries and polite pacing, plus tolerant JSON field helpers.
Nothing here prints tickers, so public run logs stay clean."""
import re
import time
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


class SourceError(Exception):
    pass


class Http:
    def __init__(self, referer=None, min_interval=0.25, timeout=25, retries=3):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                               "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8"})
        if referer:
            self.s.headers.update({"Referer": referer, "Origin": referer.rstrip("/")})
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self._last = 0.0
        self.calls = 0

    def _pace(self):
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    def request(self, method, url, **kw):
        err = None
        for attempt in range(self.retries):
            self._pace()
            try:
                self.calls += 1
                r = self.s.request(method, url, timeout=self.timeout, **kw)
                if r.status_code == 429 or r.status_code >= 500:
                    err = SourceError(f"HTTP {r.status_code}")
                    time.sleep(2 + attempt * 4)
                    continue
                if r.status_code >= 400:
                    raise SourceError(f"HTTP {r.status_code}")
                return r.json()
            except (requests.RequestException, ValueError) as e:
                err = SourceError(type(e).__name__)
                time.sleep(1 + attempt * 2)
        raise err or SourceError("request failed")

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)


# ---------- tolerant field helpers ----------

def num(v, default=None):
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "null", "None", "NaN"):
        return default
    m = re.match(r"^\(?(-?[\d.]+)\)?", s)
    try:
        return float(m.group(1)) if m else default
    except ValueError:
        return default


def lower_keys(d):
    return {str(k).lower(): v for k, v in d.items()} if isinstance(d, dict) else {}


def pick(d, *names, default=None):
    """Return first present key (case-insensitive) from dict d."""
    ld = lower_keys(d)
    for n in names:
        v = ld.get(n.lower())
        if v is not None and v != "":
            return v
    return default


def find_list_of_dicts(obj, depth=0):
    """Find the largest list of dicts anywhere inside a JSON structure."""
    best = None
    if depth > 6:
        return None
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj[:5]):
            best = obj
        for x in obj[:3]:
            cand = find_list_of_dicts(x, depth + 1)
            if cand and (best is None or len(cand) > len(best)):
                best = cand
    elif isinstance(obj, dict):
        for v in obj.values():
            cand = find_list_of_dicts(v, depth + 1)
            if cand and (best is None or len(cand) > len(best)):
                best = cand
    return best


def parse_date(v):
    """Accepts dd/mm/yyyy, yyyy-mm-dd, yyyymmdd, /Date(ms)/, epoch s/ms. Returns 'YYYY-MM-DD' or None."""
    import datetime as dt
    if v is None:
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        if x > 1e11:
            x /= 1000
        return (dt.datetime.utcfromtimestamp(x) + dt.timedelta(hours=7)).date().isoformat()
    s = str(v).strip()
    m = re.search(r"/Date\((\d+)", s)
    if m:
        return parse_date(int(m.group(1)))
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    m = re.match(r"^(\d{4})(\d{2})(\d{2})$", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    if s.isdigit():
        return parse_date(int(s))
    return None


def is_stock_symbol(s):
    return isinstance(s, str) and bool(re.fullmatch(r"[A-Z][A-Z0-9]{2}", s.strip().upper()))
