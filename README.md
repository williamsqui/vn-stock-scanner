# 🇻🇳 VN Stock Scanner

Scans HOSE, HNX and UPCoM on trading days, scores each stock 0–100 and emails you **up to 3 picks**. Each pick comes with the reasons behind its score, the risks, a suggested position size, and an exit plan (target and stop-loss). If nothing is good enough, you get a short "no picks – closest calls" email instead.

> **Signal scanner only, not financial advice.** A score is a rough ranking, not a probability. The track record in each email shows how each score range has *actually* done. Trust that more than the score, and always use the stop-loss.

---

## What you get

| Email | When | What's in it |
|---|---|---|
| **Morning picks** | 08:15 on trading days | Picks based on yesterday's full data (foreign flows included), plus open picks and the track record. **Only these picks are recorded in the track record.** |
| **After-close summary** | 15:45 on trading days | Today's market, how open picks are doing, and a preview for tomorrow |
| **Check a stock** | When you press the button | Full scored breakdown of any ticker, including which safety filters it fails |
| **Check my position** | When you press the button | HOLD / TIGHTEN STOP / TAKE PROFIT / SELL, with reasons and new stop and target prices |
| **Data self-test** | When you press the button | Which data sources work from GitHub's servers |

On weekends and on the 2026 holidays (1/1, Tết 16–20/2, 27/4, 30/4–1/5, 31/8–2/9) nothing is sent.

### Schedule trade-offs
- **Morning (08:15):** you get the complete previous session's data, and you have time to place orders before the 9:00 open. This is the main email.
- **After close (15:45):** the freshest data and a good end-of-day review. You can't act on it until tomorrow, so its picks are only a preview.
- **No midday email:** foreign-flow numbers are incomplete during the session and intraday signals are noisy. If you want one later, add an 11:40 cron job that runs `close`. It will be labelled as using the previous session's data.

---

## How a stock is scored

| Signal group | Weight | What it looks at |
|---|---|---|
| Foreign flows (khối ngoại) | 30 | 5- and 20-day net buying vs. normal trading value, buying streaks, switches from selling to buying, heavy selling (warning), foreign room left |
| Chart | 30 | Price above MA20 above MA50, higher lows, breakout above the 20-day high on volume, distance from the 60-day high, overextension (RSI, % above MA20) |
| Volume | 15 | Volume spike vs. the 20-day average on up days (good) or down days (bad), up-volume vs. down-volume |
| Fundamentals | 10 | Profit growth year-on-year, P/E vs. the median of sector peers |
| News & announcements | 10 | Official news and events (dividends, contracts, profit growth vs. fines, investigations, insider selling) |
| Proprietary desks (tự doanh) | 5 | 5-day net buying, when CafeF has the data |

If a signal has no data, its weight is left out and the email tells you so.

**Penalties:**
- Ceiling price hit 2 or more days in a row (tím liên tục = chasing risk)
- Floor days
- The **"lùa gà" pump pattern**: a fast run-up in a small company without foreign buying, or with extreme volume
- A weak overall market (fewer than 35% of stocks above their MA50)

**Safety filters (a stock must pass all of them):**
- Average trading value ≥ **10 tỷ/day**
- Market cap ≥ **1,000 tỷ**
- Price ≥ 5,000 VND
- Not suspended
- No recent exchange notice putting it on the warning, controlled, restricted or suspended list (cảnh báo / kiểm soát / hạn chế / tạm ngừng)
- Not on your `EXCLUDE_TICKERS` list

**A pick also needs:**
- A score of at least `MIN_SCORE` (70)
- Reward:risk of at least 1.5 **after fees and tax**
- Fewer than 3 ceiling days in a row
- No pump warning

**Exit plan:**
- **Target:** about 3.5× the stock's average daily range (ATR), within realistic limits: HOSE 5–12%, HNX 6–15%, UPCoM 7–18%. The 60-day high is also respected.
- **Stop:** about 1.5× ATR (HOSE 3–8%).
- **Buy zone:** don't chase more than 1.5% above yesterday's close.
- **Holding time:** up to 15 sessions.
- **T+2:** your shares arrive on the afternoon of the 3rd session, so the stop can't protect you before then. The email shows that worst case.

**Position size:**
- Risk at most 2% of your bankroll per trade (entry to stop, plus fees).
- At most 40% of your bankroll in one stock, and at most 95% in total across that day's picks.
- Sizes are rounded to 100-share lots, or an odd lot (lô lẻ) when your bankroll is small.

**Fees:** 0.15% to buy, 0.15% to sell and 0.1% sales tax by default. VNDIRECT's online DTA account is 0.10%, so set `FEE_BUY_PCT` and `FEE_SELL_PCT` to `0.1` if that's your account.

---

## Data sources (the honest version)

| Source | Needs | Gives | Notes |
|---|---|---|---|
| **SSI FastConnect Data** (official) | Free SSI account + API key | Daily prices, **foreign buy/sell, net value, foreign room** for every stock | Best source. SSI's docs mention registering a fixed IP address, and GitHub's servers don't have one. If SSI rejects GitHub, the scanner switches to free data automatically. |
| **Vietcap** public endpoints (the ones `vnstock` uses) | Nothing | Prices, today's foreign flows, listing, news/events, P/E, sectors | Unofficial, so it can change. It's used as a fallback and for news and fundamentals. |
| **CafeF** data pages | Nothing | Foreign-flow history, proprietary (tự doanh) trading | Unofficial and best-effort |

**What the scanner can't do:**
- **It can't read Zalo, Facebook or Telegram groups.** They're closed. It detects the *price patterns* that pump schemes leave behind and warns you. If a stock is being hyped in those groups, treat that as a red flag yourself.
- **There's no free official feed of the warning/controlled/restricted lists.** The scanner reads company announcements for these notices, which works most of the time. For extra safety, paste HOSE/HNX's quarterly list of stocks that aren't eligible for margin into `EXCLUDE_TICKERS`.

---

## Setup (about 30 minutes, one step at a time)

Replace `YOUR-USERNAME` in every link below with your GitHub username.

### 1. Create the repository
1. Open https://github.com/new
2. For **Repository name**, type `vn-stock-scanner`.
3. Choose **Public**. Public repos get free GitHub Actions minutes.
4. Tick **Add a README file**.
5. Tap **Create repository**.

### 2. Upload the files
Unzip `vn-stock-scanner.zip` on your computer. Then, for each link below, open it, drag in the files listed, and tap **Commit changes**.

| Open this link | Drag in these files (from the zip) |
|---|---|
| `https://github.com/YOUR-USERNAME/vn-stock-scanner/upload/main` | `README.md`, `requirements.txt` |
| `https://github.com/YOUR-USERNAME/vn-stock-scanner/upload/main/scanner` | Everything in the `scanner` folder (15 files, `__init__.py` to `vietcap.py`) |
| `https://github.com/YOUR-USERNAME/vn-stock-scanner/upload/main/tests` | Everything in the `tests` folder (3 files). Optional: these are the offline tests. |

### 3. Create the 3 workflow files
Windows hides the `.github` folder, so create these files on GitHub instead. For each one:

1. Open the link.
2. Paste the full file contents from the chat (or from the zip).
3. Tap **Commit changes**.

- `https://github.com/YOUR-USERNAME/vn-stock-scanner/new/main?filename=.github/workflows/scan.yml`
- `https://github.com/YOUR-USERNAME/vn-stock-scanner/new/main?filename=.github/workflows/check-stock.yml`
- `https://github.com/YOUR-USERNAME/vn-stock-scanner/new/main?filename=.github/workflows/check-position.yml`

If a link doesn't fill in the name, go to **Add file → Create new file** and type the name exactly, including `.github/workflows/`.

### 4. Add the Secrets (private)
Open `https://github.com/YOUR-USERNAME/vn-stock-scanner/settings/secrets/actions`. For each row, tap **New repository secret**:

| Name | Value |
|---|---|
| `GMAIL_ADDRESS` | Your Gmail address |
| `GMAIL_APP_PASSWORD` | Your 16-letter Gmail app password (the same one as the meme scanner) |
| `STATE_PASSWORD` | Any long random phrase. It encrypts your track record. **Never change it**, or the old track record can't be read. |
| `BANKROLL_VND` | e.g. `30000000`. It's a secret so it never appears in public logs. |
| `EMAIL_TO` | *(optional)* A different address to send to. Separate several with commas. |
| `SSI_CONSUMER_ID` | *(optional)* From SSI (see step 7) |
| `SSI_CONSUMER_SECRET` | *(optional)* From SSI (see step 7) |

### 5. Settings you can change any time (Variables)
Open the same page, switch to the **Variables** tab, and tap **New repository variable**. Only add the ones you want to change; the rest use these defaults.

| Name | Default | Meaning |
|---|---|---|
| `MIN_SCORE` | 70 | Minimum score for a pick |
| `MAX_PICKS` | 3 | Maximum picks per email |
| `RISK_PER_TRADE_PCT` | 2 | % of your bankroll lost if the stop hits |
| `MAX_POSITION_PCT` | 40 | Maximum % of your bankroll in one stock |
| `MIN_REWARD_RISK` | 1.5 | Required reward:risk after fees |
| `HOLD_DAYS_MAX` | 15 | Sell after this many sessions if neither target nor stop hits |
| `FEE_BUY_PCT` / `FEE_SELL_PCT` | 0.15 / 0.15 | Broker fee per trade (VNDIRECT DTA = 0.1) |
| `SELL_TAX_PCT` | 0.1 | Tax on sales |
| `MIN_AVG_VALUE_BN` | 10 | Minimum average daily trading value (tỷ VND) |
| `MIN_MARKET_CAP_BN` | 1000 | Minimum market cap (tỷ VND) |
| `MIN_PRICE_VND` | 5000 | Skip penny stocks |
| `EXCHANGES` | HOSE,HNX,UPCOM | Which exchanges to scan |
| `EXCLUDE_TICKERS` | *(empty)* | e.g. `ABC,XYZ`. Never pick these. |
| `EXTRA_HOLIDAYS` | *(empty)* | Add 2027 holidays when announced, e.g. `2027-02-05,2027-02-08` |
| `SEND_EMPTY_EMAIL` | true | Send the "no picks – closest calls" email |
| `ALLOW_ODD_LOT` | true | Suggest odd lots (under 100 shares) when needed |
| `LANGUAGE` | both | `en`, `vi` or `both` |
| `DATA_SOURCE` | auto | `auto` (SSI first, then free), `ssi` or `free` |

Email times are set in cron-job.org (step 8).

### 6. First test
1. Open `https://github.com/YOUR-USERNAME/vn-stock-scanner/actions`.
2. Tap **VN Scanner - scan → Run workflow**, choose **selftest**, then tap **Run workflow**.
3. Within about 2 minutes you'll get a **Data self-test** email showing which sources work.
4. Then run it again with **morning** and tick **force**. The first run downloads about 6 months of history and can take 5–10 minutes. Later runs take 1–3 minutes.

On a phone, use your browser with **Desktop site** turned on. The GitHub app may not show the Run workflow button.

### 7. SSI FastConnect key (optional, recommended)
1. Open an SSI account (free, done online through the SSI iBoard/Pro app, with eKYC).
2. Register for **FastConnect Data**. SSI's guide says to ask your SSI broker or contact SSI; you'll get an email when it's approved.
3. In **iBoard → Dịch vụ API (API Service)**, create a connection key. Copy the **ConsumerID** and **ConsumerSecret**. They're shown only once.
4. Add them as the `SSI_CONSUMER_ID` and `SSI_CONSUMER_SECRET` secrets, then run **selftest** again.
5. If SSI asks for a fixed IP, you can skip it. The scanner works on free data, and you can say so in the SSI request.

Keys expire after 1 year. SSI emails you 7 days before.

### 8. Schedule with cron-job.org
Use the same GitHub token as your meme scanner. If it's a fine-grained token, edit it at https://github.com/settings/tokens and add the `vn-stock-scanner` repo with **Actions: Read and write** permission.

Create **2 cron jobs**:

| Setting | Morning job | Close job |
|---|---|---|
| URL | `https://api.github.com/repos/YOUR-USERNAME/vn-stock-scanner/actions/workflows/scan.yml/dispatches` | same |
| Schedule | Custom: **08:15**, Mon–Fri | Custom: **15:45**, Mon–Fri |
| Time zone | **Asia/Ho_Chi_Minh** | **Asia/Ho_Chi_Minh** |
| Request method | POST | POST |
| Headers | `Authorization: Bearer YOUR_TOKEN`<br>`Accept: application/vnd.github+json`<br>`X-GitHub-Api-Version: 2022-11-28` | same |
| Body | `{"ref":"main","inputs":{"session":"morning"}}` | `{"ref":"main","inputs":{"session":"close"}}` |

A successful trigger returns **204**. The scanner skips weekends and holidays by itself.

---

## Using the buttons
- **Check a stock:** Actions → *VN Scanner - check a stock* → Run workflow → type e.g. `FPT`.
- **Check my position:** Actions → *VN Scanner - check my position* → Run workflow. Fill in:
  - the ticker
  - **either** your buy price (`95500` or `95.5`) **or** your gain % (`6`, `-3`)
  - optionally your shares, **or** your position in VND (`20000000`, or `20` for 20 million)
  - optionally the buy date

## Privacy (public repo)
- Logs only show counts ("Scanned 1,540 stocks; Picks: 2"), never tickers or prices.
- Picks and the track record are saved **encrypted** (`data/state.enc`) with your `STATE_PASSWORD`.
- Price history is kept in GitHub's Actions cache, not in the repo.
- Button inputs are read directly from GitHub's event file, so they don't appear in logs. GitHub still stores them with the run, so don't treat them as fully secret.

## Track record
- A morning pick counts as **bought** at the next session's open, but only if the price trades within the buy zone. If it gaps above the zone, it's counted as *not filled*.
- From the T+2 session onwards, a pick closes at the **target**, at the **stop** (if both happen on the same day, the stop is assumed), or after `HOLD_DAYS_MAX` sessions (*timed out*).
- Returns include fees and tax.
- Results are grouped by score range. Until there are about 20 closed picks, treat the numbers as rough.

## Offline tests
With Python 3.11+ installed: `pip install -r requirements.txt` then `python -m tests.run_tests`. This runs 72 checks against a simulated market.

## Troubleshooting
- **No email:** check the Actions tab. A red ❌ usually means a missing secret. You'll also get a "VN Scanner | ERROR" email with details.
- **Self-test shows FAILED for Vietcap and CafeF:** those sites may be blocking GitHub's servers. Add the SSI key.
- **"Track record is OFF":** add `STATE_PASSWORD`. If you changed it, put the old one back.
- **Holidays for 2027:** add the dates to `EXTRA_HOLIDAYS` when HOSE announces them (usually in December).
