"""HTML emails (mobile-friendly) and Gmail sending."""
import html
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

LANG = "both"

CSS = """
body{margin:0;background:#f3f4f6;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#111827}
.wrap{max-width:640px;margin:0 auto;padding:12px}
.card{background:#fff;border-radius:12px;padding:14px 16px;margin:10px 0;border:1px solid #e5e7eb}
h1{font-size:20px;margin:6px 0 2px} h2{font-size:18px;margin:0 0 6px} h3{font-size:15px;margin:12px 0 4px}
.muted{color:#6b7280;font-size:13px} .small{font-size:12px;color:#6b7280}
.score{display:inline-block;font-weight:700;border-radius:999px;padding:3px 10px;color:#fff}
.g{background:#15803d}.y{background:#b45309}.r{background:#b91c1c}.b{background:#1d4ed8}
table{border-collapse:collapse;width:100%;font-size:14px} td,th{padding:5px 6px;border-bottom:1px solid #f0f0f0;text-align:left}
ul{margin:4px 0 4px 18px;padding:0} li{margin:3px 0;font-size:14px}
.pill{display:inline-block;background:#eef2ff;color:#3730a3;border-radius:6px;padding:1px 6px;font-size:12px;margin-right:4px}
.warn{background:#fef2f2;border-color:#fecaca}
.verdict{font-size:22px;font-weight:800}
"""


def L(en, vi):
    if LANG == "en":
        return en
    if LANG == "vi":
        return vi
    return f"{en} <span class='muted'>/ {vi}</span>"


def e(s):
    return html.escape(str(s))


def vnd(x):
    return f"{x:,.0f} ₫" if x is not None else "-"


def price(x):
    return f"{x:,.0f}" if x is not None else "-"


def pct(x, sign=True):
    if x is None:
        return "-"
    return f"{x:+.1f}%" if sign else f"{x:.1f}%"


def score_badge(s):
    cls = "g" if s >= 75 else "b" if s >= 65 else "y" if s >= 50 else "r"
    return f"<span class='score {cls}'>{s:.0f}/100</span>"


DISCLAIMER_EN = ("Signal scanner only - not financial advice. No score is a guarantee; "
                 "always use your stop-loss and only risk money you can afford to lose.")
DISCLAIMER_VI = "Chỉ là công cụ lọc tín hiệu, không phải khuyến nghị đầu tư."


def page(title, body):
    foot = L(DISCLAIMER_EN, DISCLAIMER_VI)
    return (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,"
            f"initial-scale=1'><style>{CSS}</style></head><body><div class='wrap'>{body}"
            f"<p class='small'>{foot}</p></div></body></html>")


# ---------------------------------------------------------------- blocks
def pick_card(rank, sym, r, size):
    p = r["plan"]
    subs = r.get("subs", {})
    sub_txt = " ".join(f"<span class='pill'>{k} {v * 100:.0f}</span>" for k, v in subs.items() if v is not None)
    missing = ", ".join(r.get("coverage", []))
    reasons = "".join(f"<li>{e(x)}</li>" for x in r["reasons"][:7]) or "<li>-</li>"
    risks = "".join(f"<li>{e(x)}</li>" for x in r["risks"][:6])
    size_html = ""
    if size.get("shares"):
        size_html = (f"<tr><td>{L('Suggested size', 'Khối lượng gợi ý')}</td><td><b>{size['shares']:,} "
                     f"{L('shares', 'cp')}</b> ≈ {vnd(size['value'])} ({size['pct_bankroll']:.0f}% "
                     f"{L('of bankroll', 'vốn')})</td></tr>"
                     f"<tr><td>{L('If stop hits', 'Nếu chạm cắt lỗ')}</td><td style='color:#b91c1c'>−{vnd(size['loss_at_stop'])}</td></tr>"
                     f"<tr><td>{L('If target hits', 'Nếu chạm mục tiêu')}</td><td style='color:#15803d'>+{vnd(size['gain_at_target'])}"
                     f" <span class='small'>(after fees & tax)</span></td></tr>")
    note = f"<p class='small'>{e(size.get('note', ''))}</p>" if size.get("note") else ""
    return f"""
<div class='card'>
 <h2>#{rank} {e(sym)} <span class='muted'>{e(r['exchange'])}</span> &nbsp;{score_badge(r['score'])}</h2>
 <div class='small'>{L('Close', 'Giá đóng cửa')} {price(r['close'])} · {L('Avg value', 'GTGD TB')} {r['avg_val20'] / 1e9:,.1f} tỷ/day
 {f"· Mcap {r['market_cap'] / 1e9:,.0f} tỷ" if r.get('market_cap') else ''}</div>
 <div style='margin:6px 0'>{sub_txt}</div>
 <h3>✅ {L('Why it scored well', 'Lý do')}</h3><ul>{reasons}</ul>
 <h3>⚠️ {L('Risks', 'Rủi ro')}</h3><ul>{risks}</ul>
 <h3>🎯 {L('Plan', 'Kế hoạch')}</h3>
 <table>
  <tr><td>{L('Buy zone', 'Vùng mua')}</td><td><b>{price(p['entry'])} – {price(p['entry_max'])}</b>
      <span class='small'>{L("don't chase above", 'không mua đuổi trên')} {price(p['entry_max'])}</span></td></tr>
  <tr><td>{L('Target', 'Chốt lời')}</td><td><b style='color:#15803d'>{price(p['target'])}</b> ({pct(p['target_pct'])},
      {L('net', 'ròng')} {pct(p['net_gain_pct'])})</td></tr>
  <tr><td>{L('Stop-loss', 'Cắt lỗ')}</td><td><b style='color:#b91c1c'>{price(p['stop'])}</b> (−{p['stop_pct']:.1f}%,
      {L('net', 'ròng')} −{p['net_loss_pct']:.1f}%)</td></tr>
  <tr><td>{L('Reward : risk', 'Lãi : lỗ')}</td><td>{p['rr']:.1f} : 1 · {L('hold', 'nắm giữ')} {e(p['hold_days'])}</td></tr>
  {size_html}
 </table>{note}
 <p class='small'>{L('Exit rules: sell at target, or at the stop (from the T+2 afternoon), or after',
                     'Thoát: chốt ở mục tiêu, cắt lỗ (từ chiều T+2), hoặc sau')} {e(p['hold_days'].split('-')[-1])}
 {L('if neither hits. Move the stop to break-even once up ~5%.', 'nếu chưa chạm. Dời cắt lỗ về hòa vốn khi lãi ~5%.')}
 {f"<br>Signals without data: {e(missing)}" if missing else ''}</p>
</div>"""


def track_block(rows, tot, recent):
    if not tot["n"] and not tot["open"]:
        return (f"<div class='card'><h3>📊 {L('Track record', 'Thành tích')}</h3><p class='muted'>"
                f"{L('No closed picks yet. Stats appear here after picks hit their target, stop or time limit.', 'Chưa có dữ liệu.')}"
                f"</p></div>")
    trs = "".join(
        f"<tr><td>{r['bucket']}</td><td>{r['n']}</td><td>{pct(r['hit_rate'], False)} ({r['hit']})</td>"
        f"<td>{r['stop']}</td><td>{r['expired']}</td><td>{pct(r['avg_ret'])}</td></tr>" for r in rows)
    rec = "".join(f"<li>{e(p['symbol'])} ({p['score']:.0f}) → {e(p['status'])} {pct(p.get('ret_net_pct'))} "
                  f"<span class='small'>{e(p.get('exit_date', ''))}</span></li>" for p in recent)
    warn = ""
    if tot["n"] < 20:
        warn = f"<p class='small'>{L('Small sample - treat these numbers as rough until there are 20+ closed picks.', 'Mẫu nhỏ, chưa đáng tin cậy.')}</p>"
    return f"""<div class='card'><h3>📊 {L('Track record (closed picks)', 'Thành tích các mã đã chọn')}</h3>
<table><tr><th>Score</th><th>Picks</th><th>{L('Hit target', 'Đạt MT')}</th><th>Stop</th><th>{L('Timed out', 'Hết hạn')}</th><th>{L('Avg net', 'TB ròng')}</th></tr>{trs}</table>
<p class='small'>{L('Total', 'Tổng')}: {tot['n']} closed · {tot['open']} open · {L('overall hit rate', 'tỷ lệ đạt')} {pct(tot['hit_rate'], False)} ·
{L('avg net return', 'lợi nhuận TB')} {pct(tot['avg_ret'])} · {tot['no_fill']} {L('not filled (gapped above buy zone)', 'không khớp')}</p>
{f'<ul>{rec}</ul>' if rec else ''}{warn}</div>"""


def open_picks_block(open_picks):
    if not open_picks:
        return ""
    open_picks = sorted(open_picks, key=lambda p: p["signal_date"], reverse=True)[:12]
    lis = "".join(
        f"<li><b>{e(p['symbol'])}</b> ({p['score']:.0f}) {L('since', 'từ')} {e(p['signal_date'])}: "
        f"{pct(p.get('unrealised_pct'))} {L('now', 'hiện tại')} · {L('target', 'MT')} {price(p['target'])} · "
        f"{L('stop', 'CL')} {price(p['stop'])}</li>" for p in open_picks)
    return f"<div class='card'><h3>📂 {L('Open picks', 'Mã đang theo dõi')}</h3><ul>{lis}</ul></div>"


def closest_block(closest):
    if not closest:
        return ""
    lis = "".join(f"<li><b>{e(s)}</b> {score_badge(r['score'])} – {e(why)}</li>" for s, r, why in closest)
    return f"<div class='card'><h3>🔍 {L('Closest calls (not picks)', 'Suýt đạt')}</h3><ul>{lis}</ul></div>"


def market_block(mkt, asof, source, n_scanned, n_passed):
    b = mkt.get("breadth", 0.5) * 100
    mood = L("weak", "yếu") if mkt.get("weak") else L("strong", "tốt") if mkt.get("strong") else L("neutral", "trung tính")
    return (f"<div class='card'><div class='small'>{L('Data as of', 'Dữ liệu ngày')} <b>{e(asof)}</b> · "
            f"{L('source', 'nguồn')}: {e(source)} · {n_scanned} {L('stocks scanned', 'mã đã quét')}, "
            f"{n_passed} {L('passed safety filters', 'qua bộ lọc an toàn')}<br>"
            f"{L('Market breadth', 'Độ rộng thị trường')}: {b:.0f}% {L('of stocks above MA50', 'mã trên MA50')} ({mood})</div></div>")


# ---------------------------------------------------------------- send
def send(cfg, subject, html_body, log=print):
    os.makedirs(cfg.out_dir, exist_ok=True)
    fname = os.path.join(cfg.out_dir, subject.split("|")[0].strip().replace(" ", "_").replace("/", "-")[:40] + ".html")
    with open(fname, "w", encoding="utf-8") as f:
        f.write(html_body)
    if cfg.dry_run:
        log("Dry run: email built, not sent.")
        return True
    if not cfg.gmail_user or not cfg.gmail_pass:
        log("Email not sent: GMAIL_ADDRESS / GMAIL_APP_PASSWORD secrets missing.")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, cfg.gmail_user, cfg.email_to
    msg.attach(MIMEText("Open this email in an HTML-capable app to see the report.", "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    for attempt in range(3):
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
                s.login(cfg.gmail_user, cfg.gmail_pass)
                s.sendmail(cfg.gmail_user, [x.strip() for x in cfg.email_to.split(",")], msg.as_string())
            log("Email sent.")
            return True
        except Exception as ex:
            log(f"Email attempt {attempt + 1} failed: {type(ex).__name__}")
    return False
