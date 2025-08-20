import os
import requests
import time
import subprocess
from datetime import datetime
from pathlib import Path
from pycoingecko import CoinGeckoAPI
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import traceback

# === CONFIGURABLE SETTINGS ===
BASE = "https://api.dexie.space/v1"
XCH_ID = "xch"
WUSDC_ID = "wusdc.b"
MOJO_PER_XCH = 1_000_000_000_000
OFFER_SAVE_DIR = "offers"
LOG_DIR = "logs"
#PATH TO YOUR chia exe
CHIA_CLI = "/home/me/chia-dir/chia"
FINGERPRINT = "YOURWALLET FINGERPRINT"
MAX_XCH_SELL = 8.0
MAX_WUSDC_BUY = 20.0
BUY_TRIGGER_PCT = -0.50  # Buy XCH if .5% below market
SELL_TRIGGER_PCT = 6.00  # Sell XCH if 6% above market
#dont want to spam dexie
SLEEP_INTERVAL = 15

ASSET_NAME_MAP = {
    "xch": "XCH",
    "wusdc.b": "wUSDC.b",
    "fa4a180ac326e67ea289b869e3448256f6af05721f7cf934cb9901baa6b7a99d": "wUSDC.b",
}

os.makedirs(OFFER_SAVE_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
cg = CoinGeckoAPI()

def chia_cli(*args):
    return subprocess.run([CHIA_CLI, *args], capture_output=True, text=True, check=True)

def log_taken_offer(info):
    with open(os.path.join(LOG_DIR, "executed_offers.log"), "a") as f:
        f.write(f"{datetime.now().isoformat()} - {info}\n")

def take_offer(filepath, offered_amt, offered_name, requested_amt, requested_name, price, pct, offer_id, direction):
    try:
        print(f"⚡ Taking offer: {filepath}")
        subprocess.run([CHIA_CLI, "wallet", "take_offer", filepath, "-f", FINGERPRINT], input="y\n", text=True, check=True)
        log_taken_offer(
            f"SUCCESS [{direction}] | {offered_amt:.4f} {offered_name} → {requested_amt:.4f} {requested_name} | ${price:.4f} ({pct:+.2f}%) | Offer ID: {offer_id} | File: {filepath}"
        )
        print("✅ Offer taken and logged.")
    except Exception as e:
        tb = traceback.format_exc()
        log_taken_offer(
            f"FAILURE [{direction}] | {offered_amt:.4f} {offered_name} → {requested_amt:.4f} {requested_name} | ${price:.4f} ({pct:+.2f}%) | Offer ID: {offer_id} | File: {filepath} | ERROR: {e}\n{tb}"
        )
        print(f"❌ Error taking offer: {e}")

def get_xch_balance():
    try:
        result = chia_cli("wallet", "show", "-w", "standard_wallet", "-f", FINGERPRINT)
        for line in result.stdout.splitlines():
            if "Total Balance" in line:
                amount = line.split(":")[-1].strip().split(" ")[0]
                return float(amount)
    except Exception as e:
        print(f"❌ Could not fetch XCH balance: {e}")
    return 0

def fetch_offer_ui(offer_id):
    url = f"https://dexie.space/offers/{offer_id}"
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    driver = webdriver.Chrome(options=options)
    print(f"🌐 Opening {url}")
    driver.get(url)
    time.sleep(4)
    try:
        links = driver.find_elements(By.TAG_NAME, "a")
        for link in links:
            href = link.get_attribute("href")
            if href and href.startswith("data:text/plain,offer"):
                offer_string = href.split("data:text/plain,", 1)[-1]
                path = os.path.join(OFFER_SAVE_DIR, f"{offer_id}.offer")
                with open(path, "w") as f:
                    f.write(offer_string)
                print(f"✅ Saved offer to: {path}")
                return path
        print("❌ No downloadable offer found.")
    finally:
        driver.quit()

def fetch_filtered_offers():
    offers = []
    try:
        resp1 = requests.get(f"{BASE}/offers", params={"offered": XCH_ID, "requested": WUSDC_ID})
        if resp1.status_code == 200:
            offers.extend(resp1.json().get("offers", []))
        resp2 = requests.get(f"{BASE}/offers", params={"offered": WUSDC_ID, "requested": XCH_ID})
        if resp2.status_code == 200:
            offers.extend(resp2.json().get("offers", []))
    except Exception as e:
        print(f"❌ Error fetching offers: {e}")
    return offers

def price_delta_vs_market(price, market_price):
    return ((price - market_price) / market_price) * 100

def normalize_amount(asset, amount):
    amt = float(amount)
    if asset == XCH_ID and amt > 1000:
        return amt / MOJO_PER_XCH
    return amt

def resolve_asset_name(asset_id):
    return ASSET_NAME_MAP.get(asset_id.lower(), asset_id)

# === MAIN LOOP ===
while True:
    os.system("clear")
    now = datetime.now()
    print(f"⏰ {now.strftime('%Y-%m-%d %H:%M:%S')}")
    try:
        xch_price_usd = cg.get_price(ids='chia', vs_currencies='usd')['chia']['usd']
    except:
        print("❌ Failed to fetch XCH price from CoinGecko. Exiting.")
        break

    xch_balance = get_xch_balance()
    if xch_balance <= 0:
        print("🛑 XCH balance is zero. Stopping script.")
        log_taken_offer("Stopped script due to zero XCH balance.")
        break

    print(f"""
🛠️ Current Trading Settings:
• Sell XCH if ≥ {SELL_TRIGGER_PCT:.1f}% above market
• Buy XCH if ≤ {BUY_TRIGGER_PCT:.1f}% below market
• XCH Balance: {xch_balance:.4f} XCH
""")
    print(f"🔵 Market price (CoinGecko): ${xch_price_usd:.4f}\n")

    offers = fetch_filtered_offers()
    buy_offers = []
    sell_offers = []

    for offer in offers:
        if not offer.get("offered") or not offer.get("requested"):
            continue
        offered = offer["offered"][0]
        requested = offer["requested"][0]
        offered_name = resolve_asset_name(offered['id'])
        requested_name = resolve_asset_name(requested['id'])

        if offered_name == "XCH" and requested_name == "wUSDC.b":
            price = float(requested['amount']) / float(offered['amount'])
            pct = price_delta_vs_market(price, xch_price_usd)
            buy_offers.append((pct, offer, price))
        elif offered_name == "wUSDC.b" and requested_name == "XCH":
            price = float(offered['amount']) / float(requested['amount'])
            pct = price_delta_vs_market(price, xch_price_usd)
            sell_offers.append((pct, offer, price))

    buy_offers.sort(key=lambda x: x[0])
    sell_offers.sort(key=lambda x: x[0], reverse=True)

    print("🟢 Offers (you sell XCH):")
    for i, (pct, offer, price) in enumerate(sell_offers[:5]):
        offered = offer["offered"][0]
        requested = offer["requested"][0]
        offered_name = resolve_asset_name(offered['id'])
        requested_name = resolve_asset_name(requested['id'])
        offered_amt = normalize_amount(offered['id'], offered['amount'])
        requested_amt = normalize_amount(requested['id'], requested['amount'])
        alert = ""
        if pct >= 3:
            alert = "🟢🚨 MEGA DEAL (3%+)"
        elif pct >= 2:
            alert = "💥 SUPER DEAL (2%+)"
        elif pct >= 1:
            alert = "✅ Good deal (1%+)"
        elif pct <= -1:
            alert = "💩 SHIT DEAL (1%+ under market)"
        print(f"{alert} • {offered_amt:.4f} {offered_name} → {requested_amt:.4f} {requested_name} @ ${price:.4f} ({pct:+.2f}%)")
        print(f"🔗 https://dexie.space/offers/{offer['id']}")
        if pct >= SELL_TRIGGER_PCT:
            path = fetch_offer_ui(offer["id"])
            if path:
                take_offer(path, offered_amt, offered_name, requested_amt, requested_name, price, pct, offer['id'], "SELL")

    print("\n🔵 Offers (you buy XCH):")
    for i, (pct, offer, price) in enumerate(buy_offers[:5]):
        offered = offer["offered"][0]
        requested = offer["requested"][0]
        offered_name = resolve_asset_name(offered['id'])
        requested_name = resolve_asset_name(requested['id'])
        offered_amt = normalize_amount(offered['id'], offered['amount'])
        requested_amt = normalize_amount(requested['id'], requested['amount'])
        alert = ""
        if pct <= -3:
            alert = "🟢🚨 MEGA DEAL (3%+)"
        elif pct <= -2:
            alert = "💥 SUPER DEAL (2%+)"
        elif pct <= -1:
            alert = "✅ Good deal (1%+)"
        elif pct >= 1:
            alert = "💩 SHIT DEAL (1%+ above market)"
        print(f"{alert} • {requested_amt:.4f} {requested_name} → {offered_amt:.4f} {offered_name} @ ${price:.4f} ({pct:+.2f}%)")
        print(f"🔗 https://dexie.space/offers/{offer['id']}")
        if pct <= BUY_TRIGGER_PCT:
            path = fetch_offer_ui(offer["id"])
            if path:
                take_offer(path, offered_amt, offered_name, requested_amt, requested_name, price, pct, offer['id'], "BUY")

    print(f"\n⏳ Next check in {SLEEP_INTERVAL} seconds...")
    time.sleep(SLEEP_INTERVAL)
