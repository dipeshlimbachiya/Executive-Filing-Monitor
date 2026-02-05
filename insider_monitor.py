import requests
import time
from datetime import datetime
import os
import json

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # dotenv not installed - running in production (GitHub Actions)
    pass

# Configuration from GitHub Secrets or .env file
BOT_TOKEN_FORM4 = os.environ.get('BOT_TOKEN_FORM4')
BOT_TOKEN_FORM8K = os.environ.get('BOT_TOKEN_FORM8K')
CHAT_ID = os.environ.get('CHAT_ID')
FINNHUB_API_KEY = os.environ.get('FINNHUB_API_KEY')
MIN_AMOUNT = 100000

# File paths for data storage
DATA_DIR = 'data'
TRADES_DATA_FILE = os.path.join(DATA_DIR, 'trades_data.json')
FORM8K_DATA_FILE = os.path.join(DATA_DIR, 'form8k_data.json')
SEEN_TRADES_FILE = 'seen_trades.txt'
SEEN_8K_FILE = 'seen_8k.txt'
CIK_CACHE_FILE = os.path.join(DATA_DIR, 'cik_cache.json')

# Create data directory if it doesn't exist
os.makedirs(DATA_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# SEC EDGAR API configuration
# ---------------------------------------------------------------------------
# SEC REQUIRES a User-Agent in the format "CompanyName contact@email.com".
# A browser-style User-Agent string will get your IP blocked.
SEC_HEADERS = {
    'User-Agent': 'InsiderMonitorBot your-contact@email.com',
    'Accept-Encoding': 'gzip, deflate'
}

# ---------------------------------------------------------------------------
# CIK cache — avoids re-downloading company_tickers.json every run
# ---------------------------------------------------------------------------

def load_cik_cache():
    try:
        with open(CIK_CACHE_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_cik_cache(cache):
    with open(CIK_CACHE_FILE, 'w') as f:
        json.dump(cache, f, indent=2)

def build_cik_cache():
    """
    Download the full ticker-to-CIK mapping from SEC and cache it locally.

    Endpoint: https://www.sec.gov/files/company_tickers.json
    Format:   { "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ... }
    """
    try:
        print("📥 Downloading SEC company_tickers.json...")
        response = requests.get(
            'https://www.sec.gov/files/company_tickers.json',
            headers=SEC_HEADERS,
            timeout=30
        )
        if response.status_code == 200:
            data = response.json()
            cache = {}
            for entry in data.values():
                ticker = entry.get('ticker', '').upper()
                cik = entry.get('cik_str')
                if ticker and cik:
                    # Store as plain integer string — no zero-padding here.
                    # We pad to 10 digits only when calling the submissions endpoint.
                    cache[ticker] = str(cik)
            save_cik_cache(cache)
            print(f"   ✅ Cached {len(cache)} ticker→CIK mappings")
            return cache
        else:
            print(f"   ❌ Failed to download company_tickers.json: {response.status_code}")
            return {}
    except Exception as e:
        print(f"   ❌ Error downloading company_tickers.json: {e}")
        return {}

def get_cik(ticker, cik_cache):
    """Look up a ticker's CIK. Returns plain string like '51143'."""
    return cik_cache.get(ticker.upper())

# ---------------------------------------------------------------------------
# SEC EDGAR: resolve a ticker + filing date → direct Form 4 document URL
# ---------------------------------------------------------------------------

def fetch_sec_form4_url(symbol, filing_date, cik_cache):
    """
    Calls the SEC submissions API for the issuer company, walks the
    returned Form 4 entries, and builds the direct /Archives/ URL.

    API endpoint:
        https://data.sec.gov/submissions/CIK{10-digit-padded}.json

    The response contains parallel arrays under filings.recent:
        form[], filingDate[], accessionNumber[], primaryDocument[], ...

    SEC cross-references ownership filings, so the ISSUER's CIK endpoint
    returns Form 4s filed by that company's insiders.

    Direct document URL pattern:
        https://www.sec.gov/Archives/edgar/data/{CIK}/{acc_no_dashes}/{primaryDocument}

        CIK            = integer, no leading zeros  (e.g. 51143)
        acc_no_dashes  = accessionNumber with dashes stripped
                         e.g. "0001183474-26-000002" → "000118347426000002"
        primaryDocument = exactly as returned by the API; may include a subfolder
                         e.g. "xslF345X05/primarydocument.xml"

    Returns the direct URL string, or None on failure.
    """
    cik = get_cik(symbol, cik_cache)
    if not cik:
        print(f"      ⚠️ No CIK found for {symbol}")
        return None

    try:
        cik_padded = str(cik).zfill(10)   # submissions endpoint needs 10-digit padded CIK
        url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"

        response = requests.get(url, headers=SEC_HEADERS, timeout=15)
        if response.status_code != 200:
            print(f"      ⚠️ SEC submissions returned {response.status_code} for {symbol} (CIK {cik})")
            return None

        data = response.json()
        recent = data.get('filings', {}).get('recent', {})

        forms              = recent.get('form', [])
        filing_dates       = recent.get('filingDate', [])
        accession_numbers  = recent.get('accessionNumber', [])
        primary_docs       = recent.get('primaryDocument', [])

        best_fallback = None   # most recent Form 4, regardless of date

        for i, form in enumerate(forms):
            if form != '4':
                continue

            acc        = accession_numbers[i] if i < len(accession_numbers) else ''
            primary_doc = primary_docs[i]     if i < len(primary_docs)       else ''
            filed      = filing_dates[i]      if i < len(filing_dates)       else ''

            if not acc or not primary_doc:
                continue

            acc_no_dashes = acc.replace('-', '')
            # CIK in the Archives path must NOT have leading zeros
            direct_url = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{int(cik)}/{acc_no_dashes}/{primary_doc}"
            )

            # Remember the first (most recent) Form 4 as fallback
            if best_fallback is None:
                best_fallback = direct_url

            # Exact match on filing date — return immediately
            if filed == filing_date:
                print(f"      ✅ Resolved Form 4 URL for {symbol} (filed {filed})")
                return direct_url

        # No exact date match; return most recent Form 4
        if best_fallback:
            print(f"      ⚠️ No exact date match for {symbol}, using most recent Form 4")
            return best_fallback

        print(f"      ⚠️ No Form 4 filings found for {symbol} (CIK {cik})")
        return None

    except Exception as e:
        print(f"      ⚠️ Error fetching SEC submissions for {symbol}: {e}")
        return None


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram_alert(message, bot_token, chat_id):
    if not bot_token or not chat_id:
        print("⚠️  Telegram credentials not set, skipping alert")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        response = requests.post(url, data=data)
        response_data = response.json()
        if response_data.get('ok'):
            print(f"✅ Alert sent at {datetime.now().strftime('%H:%M:%S')}")
            return True
        else:
            print(f"❌ Telegram API Error: {response_data}")
            return False
    except Exception as e:
        print(f"❌ Error sending alert: {e}")
        return False

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def get_transaction_code_description(code):
    code_meanings = {
        'A': 'Grant/Award',
        'D': 'Disposition (Sale to Issuer)',
        'P': 'Open Market Purchase',
        'S': 'Open Market Sale',
        'F': 'Tax Payment (Shares Withheld)',
        'M': 'Option Exercise',
        'C': 'Conversion',
        'E': 'Expiration',
        'G': 'Gift',
        'H': 'Withholding',
        'I': 'Discretionary Transaction',
        'J': 'Other',
        'K': 'Equity Swap',
        'L': 'Small Acquisition',
        'U': 'Tender Offer',
        'V': 'Voluntary Transaction',
        'W': 'Acquisition/Disposition by Will',
        'X': 'Exercise of Out-of-the-Money Options',
        'Z': 'Deposit into/Withdrawal from Plan'
    }
    return code_meanings.get(code, code if code else 'Unknown')

def format_form4_alert(trade, symbol, sec_link):
    change = trade.get('change', 0)
    trade_type = "BUY" if change > 0 else "SELL"
    emoji = "🟢" if trade_type == 'BUY' else "🔴"
    arrow = "⬆️" if trade_type == 'BUY' else "⬇️"

    name = trade.get('name', 'Unknown Insider')
    shares = abs(change)

    transaction_price = trade.get('transactionPrice', 0)
    if transaction_price and transaction_price > 0:
        price = transaction_price
        total_value = shares * price
        value_display = f"<b>Price/Share:</b> ${price:.2f}\n<b>Total Value:</b> ${total_value:,.0f}"
    else:
        value_display = "<b>Price/Share:</b> Not available\n<b>Total Value:</b> Unable to calculate"

    shares_owned_after = trade.get('share', 0)
    transaction_date  = trade.get('transactionDate', 'Unknown')
    filing_date       = trade.get('filingDate', 'Unknown')
    transaction_code  = trade.get('transactionCode', '')
    transaction_desc  = get_transaction_code_description(transaction_code)

    message = f"""
{emoji} <b>FORM 4: INSIDER {trade_type}</b> {arrow}

<b>Ticker:</b> {symbol}
<b>Insider:</b> {name}
<b>Transaction Code:</b> {transaction_code} - {transaction_desc}

<b>Shares Traded:</b> {shares:,}
{value_display}
<b>Shares Owned After:</b> {shares_owned_after:,}

<b>Transaction Date:</b> {transaction_date}
<b>Filing Date:</b> {filing_date}

{'💡 Insider is buying - bullish signal' if trade_type == 'BUY' else '⚠️ Insider is selling'}

<a href="{sec_link}">View Form 4 on SEC EDGAR</a>
    """
    return message.strip()

def format_form8k_alert(filing):
    symbol      = filing.get('symbol', 'Unknown')
    filing_date = filing.get('filedDate', 'Unknown')
    form        = filing.get('form', '8-K')
    accept_time = filing.get('acceptedDate', 'Unknown')
    url         = filing.get('reportUrl', '')
    item_codes  = filing.get('items', [])

    item_descriptions = {
        '1.01': 'Important contract signed',
        '1.02': 'Major contract terminated',
        '1.03': '🚨 Bankruptcy/receivership',
        '2.01': 'Acquisition or asset sale',
        '2.02': 'Earnings release',
        '2.03': 'New debt issued',
        '2.04': 'Debt acceleration',
        '2.05': 'Restructuring/layoffs',
        '2.06': 'Asset write-downs',
        '3.01': '🚨 Delisting risk',
        '3.02': 'Private stock sale',
        '3.03': 'Shareholder rights changed',
        '4.01': 'Auditor changed',
        '4.02': '🚩 Financial restatement',
        '5.01': 'Ownership change',
        '5.02': 'CEO/CFO/Director change',
        '5.03': 'Governance rules updated',
        '5.04': 'Employee trading suspended',
        '5.05': 'Ethics code updated',
        '7.01': 'Material info disclosed',
        '8.01': 'Other material event',
        '9.01': 'Financial statements attached'
    }

    items_text = ""
    if item_codes and len(item_codes) > 0:
        items_text = "\n<b>📋 Items Filed:</b>\n"
        for code in item_codes:
            desc = item_descriptions.get(code, 'Material event')
            items_text += f"  • Item {code}: {desc}\n"
    else:
        items_text = "\n<b>📋 Items:</b> Material event requiring disclosure\n"

    message = f"""
📋 <b>FORM 8-K FILED</b>

<b>Ticker:</b> {symbol}
<b>Form Type:</b> {form}
{items_text}
<b>Filed Date:</b> {filing_date}
<b>Accepted:</b> {accept_time}

<a href="{url}">View Full Filing on SEC</a>
    """
    return message.strip()

# ---------------------------------------------------------------------------
# Finnhub API fetchers
# ---------------------------------------------------------------------------

def fetch_insider_trades(symbol):
    """Fetch insider trades using Finnhub API - Limited to top 7"""
    url = "https://finnhub.io/api/v1/stock/insider-transactions"
    params = {"symbol": symbol, "token": FINNHUB_API_KEY}
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            data = response.json().get('data', [])
            return data[:7] if data else []
        elif response.status_code == 429:
            print("   ⚠️ Rate limited - waiting...")
            time.sleep(2)
            return []
        else:
            print(f"   ❌ API error: {response.status_code}")
            return []
    except Exception as e:
        print(f"   ❌ Error fetching {symbol}: {e}")
        return []

def fetch_form8k_filings(symbol):
    """Fetch Form 8-K filings using Finnhub API - Limited to top 7"""
    url = "https://finnhub.io/api/v1/stock/filings"
    params = {"symbol": symbol, "token": FINNHUB_API_KEY, "form": "8-K"}
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            data = response.json()
            return data[:7] if data else []
        elif response.status_code == 429:
            print("   ⚠️ Rate limited - waiting...")
            time.sleep(2)
            return []
        else:
            print(f"   ❌ API error: {response.status_code}")
            return []
    except Exception as e:
        print(f"   ❌ Error fetching 8-K for {symbol}: {e}")
        return []

# ---------------------------------------------------------------------------
# 8-K item-code / report-date extraction
# ---------------------------------------------------------------------------

def extract_8k_item_codes(report_url):
    if not report_url:
        return []
    try:
        response = requests.get(report_url, headers=SEC_HEADERS, timeout=15)
        if response.status_code != 200:
            print(f"      ⚠️ Failed to fetch filing (status {response.status_code})")
            return []

        content = response.text.upper()
        import re

        all_matches = set(
            re.findall(r'ITEM\s+(\d+\.\d+)', content) +
            re.findall(r'ITEM\s+(\d+\.\d+)\.', content) +
            re.findall(r'ITEM\s+(\d+\.\d+):', content) +
            re.findall(r'\[X\]\s*ITEM\s+(\d+\.\d+)', content)
        )

        valid_items = [
            '1.01','1.02','1.03','1.04',
            '2.01','2.02','2.03','2.04','2.05','2.06',
            '3.01','3.02','3.03',
            '4.01','4.02',
            '5.01','5.02','5.03','5.04','5.05','5.06','5.07','5.08',
            '6.01','6.02','6.03','6.04','6.05',
            '7.01','8.01','9.01'
        ]

        item_codes = [code for code in all_matches if code in valid_items]
        print(f"      📝 Extracted item codes: {item_codes if item_codes else 'None found'}")
        return sorted(item_codes)
    except Exception as e:
        print(f"      ⚠️ Error extracting item codes: {e}")
        return []

def extract_8k_report_date(report_url):
    if not report_url:
        return None
    try:
        response = requests.get(report_url, headers=SEC_HEADERS, timeout=15)
        if response.status_code != 200:
            return None

        content = response.text
        import re

        match = re.search(r'CONFORMED PERIOD OF REPORT:\s*(\d{8})', content)
        if match:
            d = match.group(1)
            formatted = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
            print(f"      📅 Extracted report date: {formatted}")
            return formatted

        match = re.search(r'DATE OF REPORT.*?(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', content, re.IGNORECASE)
        if match:
            print(f"      📅 Extracted report date: {match.group(1)}")
            return match.group(1)

        match = re.search(r'Date of Report.*?(\w+ \d{1,2}, \d{4})', content, re.IGNORECASE | re.DOTALL)
        if match:
            print(f"      📅 Extracted report date: {match.group(1)}")
            return match.group(1)

        return None
    except Exception as e:
        print(f"      ⚠️ Error extracting report date: {e}")
        return None

# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def load_seen_trades():
    try:
        with open(SEEN_TRADES_FILE, 'r') as f:
            return set(line.strip() for line in f)
    except FileNotFoundError:
        return set()

def save_seen_trades(seen_trades):
    with open(SEEN_TRADES_FILE, 'w') as f:
        for trade_id in seen_trades:
            f.write(f"{trade_id}\n")

def load_seen_8k():
    try:
        with open(SEEN_8K_FILE, 'r') as f:
            return set(line.strip() for line in f)
    except FileNotFoundError:
        return set()

def save_seen_8k(seen_8k):
    with open(SEEN_8K_FILE, 'w') as f:
        for filing_id in seen_8k:
            f.write(f"{filing_id}\n")

def load_trades_data():
    try:
        with open(TRADES_DATA_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {'trades': [], 'stats': {'totalAlerts': 0, 'buys': 0, 'sells': 0, 'totalValue': 0}}

def save_trades_data(all_trades):
    buys       = sum(1 for t in all_trades if t['type'] == 'BUY')
    sells      = sum(1 for t in all_trades if t['type'] == 'SELL')
    total_value = sum(t.get('totalValue', 0) for t in all_trades)

    data = {
        'trades': all_trades,
        'stats': {
            'totalAlerts': len(all_trades),
            'buys': buys,
            'sells': sells,
            'totalValue': total_value
        },
        'lastUpdated': datetime.now().isoformat()
    }

    with open(TRADES_DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"💾 Saved {len(all_trades)} Form 4 trades to {TRADES_DATA_FILE}")

def load_form8k_data():
    try:
        with open(FORM8K_DATA_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {'filings': [], 'totalFilings': 0}

def save_form8k_data(all_filings):
    data = {
        'filings': all_filings,
        'totalFilings': len(all_filings),
        'lastUpdated': datetime.now().isoformat()
    }

    with open(FORM8K_DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"💾 Saved {len(all_filings)} Form 8-K filings to {FORM8K_DATA_FILE}")

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def check_all_stocks():
    WATCHLIST = [
        "MSFT", "NVDA", "TSLA", "GOOGL", "META", "AMZN", "NFLX", "AMD",
        "INTC", "COIN", "LYFT", "ORCL", "AVGO", "ADBE", "PYPL", "PLTR",
        "SMCI", "SOFI", "SMR", "GME", "HIMS", "CRWV", "XPEV", "HOOD",
        "OKLO", "ACHR", "IREN", "NBIS", "MU", "SNOW", "APP", "TSM",
        "ASTS", "MRVL", "BA", "PDD", "SOUN", "PANW", "TEM", "LLY",
        "ALGN", "SPOT", "CVNA", "SHOP", "DUOL", "NKE", "CSCO", "BULL",
        "JNJ", "LCID", "KO", "GE", "BE", "NEE", "PEP", "RR", "IONQ",
        "QCOM", "LNTH", "CFLT", "LMND", "JOBY", "CAT", "OPEN", "RIVN",
        "PFE", "CNC", "NVO", "NOW", "CVS", "ABT", "IBM", "JPM", "NVAX", "BRK-B", "UNH", "AAPL"
    ]

    print(f"{'='*60}")
    print(f"🔍 Executive Insider Filing Monitor - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    print(f"📋 Watchlist: {len(WATCHLIST)} stocks")
    print(f"📊 Checking Form 4 (top 7) & Form 8-K (top 7)")
    print(f"💰 Minimum alert threshold: ${MIN_AMOUNT:,}")
    print(f"{'='*60}\n")

    # Load or build the CIK cache
    cik_cache = load_cik_cache()
    if not cik_cache:
        cik_cache = build_cik_cache()

    seen_trades   = load_seen_trades()
    seen_8k       = load_seen_8k()
    trades_data   = load_trades_data()
    form8k_data   = load_form8k_data()
    all_trades    = trades_data.get('trades', [])
    all_8k_filings = form8k_data.get('filings', [])

    print(f"📋 Loaded {len(seen_trades)} seen Form 4 trades")
    print(f"📋 Loaded {len(seen_8k)} seen Form 8-K filings")
    print(f"📋 CIK cache has {len(cik_cache)} entries\n")

    form4_alerts   = 0
    form8k_alerts  = 0
    new_trades     = 0
    new_8k_filings = 0

    # Per-run cache so we only call the SEC submissions API once per ticker
    sec_url_cache = {}   # symbol → resolved URL (or None)

    for idx, symbol in enumerate(WATCHLIST, 1):
        print(f"📊 [{idx}/{len(WATCHLIST)}] Checking {symbol}...")

        # --------------------------------------------------------------
        # Form 4 – Insider Trades
        # --------------------------------------------------------------
        try:
            trades = fetch_insider_trades(symbol)

            if trades:
                print(f"   📄 Form 4: Found {len(trades)} filings (top 7)")
                for trade in trades:
                    change           = trade.get('change', 0)
                    shares           = abs(change)
                    transaction_price = trade.get('transactionPrice', 0)
                    has_price        = transaction_price and transaction_price > 0
                    value            = shares * transaction_price if has_price else 0
                    name             = trade.get('name', 'Unknown')
                    transaction_date = trade.get('transactionDate', 'Unknown')
                    filing_date      = trade.get('filingDate', 'Unknown')
                    trade_id         = f"{symbol}_{name}_{transaction_date}_{shares}_{change}"

                    if trade_id in seen_trades:
                        continue
                    if value > 0 and value < MIN_AMOUNT:
                        continue

                    new_trades += 1
                    action           = "BUY" if change > 0 else "SELL"
                    transaction_code = trade.get('transactionCode', '')

                    # --- Resolve direct Form 4 URL via data.sec.gov ---
                    # Cache per symbol so we only hit the SEC API once per ticker per run.
                    if symbol not in sec_url_cache:
                        sec_url_cache[symbol] = fetch_sec_form4_url(
                            symbol, filing_date, cik_cache
                        )
                        # SEC rate limit: 10 requests/second max
                        time.sleep(0.15)

                    sec_form4_link = sec_url_cache[symbol]

                    # Ultimate fallback: generic EDGAR company search
                    if not sec_form4_link:
                        cik = get_cik(symbol, cik_cache)
                        if cik:
                            sec_form4_link = (
                                f"https://www.sec.gov/cgi-bin/browse-edgar"
                                f"?action=getcompany&CIK={cik}&type=4"
                                f"&dateb=&owner=include&count=10"
                            )
                        else:
                            sec_form4_link = (
                                f"https://www.sec.gov/cgi-bin/browse-edgar"
                                f"?action=getcompany&company={symbol}&type=4"
                                f"&dateb=&owner=include&count=10"
                            )

                    dashboard_trade = {
                        'id': trade_id,
                        'symbol': symbol,
                        'name': name,
                        'type': action,
                        'shares': shares,
                        'price': transaction_price if has_price else 0,
                        'totalValue': value,
                        'sharesOwned': trade.get('share', 0),
                        'transactionDate': transaction_date,
                        'filingDate': filing_date,
                        'transactionCode': transaction_code,
                        'transactionDescription': get_transaction_code_description(transaction_code),
                        'position': 'Executive',
                        'secLink': sec_form4_link
                    }
                    all_trades.insert(0, dashboard_trade)

                    print(f"      🚨 NEW Form 4 {action}: {name} - {shares:,} shares - Code: {transaction_code}")
                    print(f"      🔗 {sec_form4_link}")

                    try:
                        alert_message = format_form4_alert(trade, symbol, sec_form4_link)
                        if send_telegram_alert(alert_message, BOT_TOKEN_FORM4, CHAT_ID):
                            form4_alerts += 1
                            seen_trades.add(trade_id)
                            time.sleep(1)
                    except Exception as e:
                        print(f"      ❌ Error sending Form 4 alert: {e}")
        except Exception as e:
            print(f"   ❌ Error checking Form 4: {e}")

        time.sleep(0.5)

        # --------------------------------------------------------------
        # Form 8-K
        # --------------------------------------------------------------
        try:
            filings = fetch_form8k_filings(symbol)
            if filings:
                print(f"   📋 Form 8-K: Found {len(filings)} filings (top 7)")
                for filing in filings:
                    filing_id = f"{symbol}_{filing.get('acceptedDate', '')}_{filing.get('accessNumber', '')}"

                    if filing_id in seen_8k:
                        continue

                    new_8k_filings += 1

                    report_url  = filing.get('reportUrl', '')
                    item_codes  = extract_8k_item_codes(report_url) if report_url else []
                    report_date = extract_8k_report_date(report_url) if report_url else None

                    if not report_date:
                        report_date = filing.get('reportDate', filing.get('filedDate', 'Unknown'))

                    dashboard_filing = {
                        'id': filing_id,
                        'symbol': symbol,
                        'form': filing.get('form', '8-K'),
                        'filedDate': filing.get('filedDate', 'Unknown'),
                        'acceptedDate': filing.get('acceptedDate', 'Unknown'),
                        'reportDate': report_date,
                        'reportUrl': report_url,
                        'accessNumber': filing.get('accessNumber', ''),
                        'items': item_codes
                    }
                    all_8k_filings.insert(0, dashboard_filing)

                    items_display = ', '.join(item_codes) if item_codes else 'No specific items found'
                    print(f"      🚨 NEW Form 8-K filed: {filing.get('filedDate', 'Unknown')} - Items: {items_display}")

                    try:
                        alert_message = format_form8k_alert(dashboard_filing)
                        if send_telegram_alert(alert_message, BOT_TOKEN_FORM8K, CHAT_ID):
                            form8k_alerts += 1
                            seen_8k.add(filing_id)
                            time.sleep(1)
                    except Exception as e:
                        print(f"      ❌ Error sending 8-K alert: {e}")
        except Exception as e:
            print(f"   ❌ Error checking Form 8-K: {e}")

        time.sleep(1)

    # Save everything
    save_seen_trades(seen_trades)
    save_seen_8k(seen_8k)
    save_trades_data(all_trades[:500])
    save_form8k_data(all_8k_filings[:500])

    print(f"\n{'='*60}")
    print(f"✅ CHECK COMPLETE")
    print(f"{'='*60}")
    print(f"📬 Form 4 alerts sent: {form4_alerts}")
    print(f"📬 Form 8-K alerts sent: {form8k_alerts}")
    print(f"📊 New Form 4 trades: {new_trades}")
    print(f"📊 New Form 8-K filings: {new_8k_filings}")
    print(f"💾 Dashboard has {len(all_trades[:500])} Form 4 trades")
    print(f"💾 Dashboard has {len(all_8k_filings[:500])} Form 8-K filings")
    print(f"🕐 Completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

if __name__ == "__main__":
    check_all_stocks()
