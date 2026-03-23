import base64, tarfile, os, json, requests, garth, time, random, io
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials

# --- 1. ЗАЩИТА И ПАУЗЫ ---
def human_delay():
    time.sleep(random.uniform(5, 10))

def safe_call(func, *args, **kwargs):
    try: return func(*args, **kwargs)
    except Exception as e:
        if "429" in str(e):
            print("⚠️ Garmin 429. Ждем...")
            time.sleep(60)
        else: raise e
    return None

def update_or_append(sheet, date_str, row_data):
    try:
        cell = sheet.find(date_str)
        if cell:
            sheet.update(range_name=f"A{cell.row}:Z{cell.row}", values=[row_data], value_input_option="USER_ENTERED")
            return
    except: pass
    sheet.append_row(row_data, value_input_option="USER_ENTERED")

# --- 2. CONFIG ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")
GARMIN_SESSION_BASE64 = os.environ.get("GARMIN_SESSION_BASE64")

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")

# --- 3. ЛОГИН (УЛЬТРА-ЗАЩИТА) ---
session_dir = os.path.abspath("./.garth")
if os.path.exists(session_dir):
    import shutil
    shutil.rmtree(session_dir)
os.makedirs(session_dir, exist_ok=True)

gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
login_success = False

# Пытаемся поднять сессию из секрета
if GARMIN_SESSION_BASE64 and len(GARMIN_SESSION_BASE64) > 20:
    try:
        print("📦 Проверка секрета сессии...")
        with open("session.tar.gz", "wb") as f:
            f.write(base64.b64decode(GARMIN_SESSION_BASE64.strip()))
        with tarfile.open("session.tar.gz", "r:gz") as tar:
            tar.extractall(path=".")
        
        if os.path.exists(os.path.join(session_dir, "oauth1_token.json")):
            garth.client.load(session_dir)
            gar.garth = garth.client
            # Проверка связи
            gar.get_display_name()
            print("🚀 Вход по СЕССИИ выполнен!")
            login_success = True
    except Exception as e:
        print(f"⚠️ Сессия не подошла ({e}). Пробуем обычный логин...")

# Если сессия не сработала — логинимся по старинке
if not login_success:
    human_delay()
    gar.login()
    garth.save(session_dir)
    print("🚀 Вход по ПАРОЛЮ выполнен!")
    
    # Сразу печатаем НОВЫЙ код для секрета
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        tar.add(session_dir, arcname=".garth")
    new_b64 = base64.b64encode(out.getvalue()).decode()
    print(f"\n🔑 ОБНОВИ ЭТОТ КОД В SECRETS:\n{new_b64}\n")

# --- 4. ОСТАЛЬНОЙ КОД (БЕЗ DAILY) ---
summary = safe_call(gar.get_user_summary, today_str) or {}
hrv_res = safe_call(gar.get_hrv_data, today_str) or {}
hrv = hrv_res.get("hrvSummary", {}).get("lastNightAvg") or ""
r_hr = summary.get("restingHeartRate") or ""

# Вес (S2)
weight, fat, muscle = "", "", ""
try:
    w_data = gar.get_body_composition((now - timedelta(days=3)).strftime("%Y-%m-%d"), today_str)
    weights = w_data.get('dateWeightList', [])
    if weights:
        last_w = max(weights, key=lambda x: x.get('sampleTime', 0))
        weight = round(float(last_w.get('weight', 0)) / 1000, 1)
        fat = last_w.get('bodyFat', "")
        muscle = round(float(last_w.get('muscleMass', 0)) / 1000, 1)
except: pass

# Сон
slp_sc, slp_h, morning_ts = "", "", f"{today_str} 08:00"
for d in [today_str, (now - timedelta(days=1)).strftime("%Y-%m-%d")]:
    sd = safe_call(gar.get_sleep_data, d) or {}
    dto = sd.get("dailySleepDTO") or {}
    if dto and dto.get("sleepTimeSeconds", 0) > 0:
        slp_h = round(float(dto.get("sleepTimeSeconds")) / 3600, 1)
        slp_sc = dto.get("sleepScore") or ""
        break

# Google Sheets & AI
creds_dict = json.loads(GOOGLE_CREDS_JSON)
creds = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
client = gspread.authorize(creds)
ss = client.open("Garmin_Data")

morning_row = [f"'{today_str}", weight, fat, muscle, r_hr, hrv, summary.get("bodyBatteryHighestValue", ""), slp_sc, slp_h, 63, 50.0]
update_or_append(ss.worksheet("Morning"), today_str, morning_row)

# Telegram (простой отчет)
if hrv:
    msg = f"📊 Утро: HRV {hrv}, RHR {r_hr}, Сон {slp_h}ч. Готовность записана!"
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})

print("✅ Готово!")
