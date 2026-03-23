import base64, tarfile, os, json, requests, garth, time, random
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials

# --- 1. ЗАЩИТА И ПАУЗЫ ---
def human_delay():
    wait = random.uniform(6.5, 10.5)
    print(f"⏳ Пауза {round(wait, 1)} сек...")
    time.sleep(wait)

def safe_call(func, *args, **kwargs):
    for attempt in range(2):
        try: return func(*args, **kwargs)
        except Exception as e:
            if "429" in str(e):
                wait = 60 * (attempt + 1)
                print(f"⚠️ Garmin 429. Ждем {wait}с...")
                time.sleep(wait)
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
display_date = now.strftime("%d.%m.%Y")

# --- 3. ЛОГИН (ФИКС) ---
session_dir = os.path.abspath("./.garth")
os.makedirs(session_dir, exist_ok=True)
if GARMIN_SESSION_BASE64:
    try:
        with open("session.tar.gz", "wb") as f: f.write(base64.b64decode(GARMIN_SESSION_BASE64))
        with tarfile.open("session.tar.gz", "r:gz") as tar: tar.extractall(path=".")
    except: pass

gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
try:
    if os.path.exists(os.path.join(session_dir, "oauth1_token.json")):
        garth.client.load(session_dir)
        gar.garth = garth.client
    else:
        human_delay()
        safe_call(gar.login)
        garth.save(session_dir)
except Exception as e: print(f"🚨 Login Error: {e}"); raise e

# --- 4. СБОР ДАННЫХ (ТОЛЬКО ПРОВЕРЕННЫЕ МЕТОДЫ) ---
human_delay()
summary = safe_call(gar.get_user_summary, today_str) or {}
human_delay()
hrv_res = safe_call(gar.get_hrv_data, today_str) or {}

hrv = hrv_res.get("hrvSummary", {}).get("lastNightAvg") or ""
r_hr = summary.get("restingHeartRate") or ""

# Вес S2
weight, fat, muscle = "", "", ""
try:
    w_data = safe_call(gar.get_body_composition, (now - timedelta(days=3)).strftime("%Y-%m-%d"), today_str) or {}
    weights = w_data.get('dateWeightList', [])
    if weights:
        last_w = max(weights, key=lambda x: x.get('sampleTime', 0))
        weight = round(float(last_w.get('weight', 0)) / 1000, 1)
        fat = last_w.get('bodyFat', "")
        muscle = round(float(last_w.get('muscleMass', 0)) / 1000, 1) if last_w.get('muscleMass') else ""
except: pass

# Сон
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
slp_sc, slp_h, morning_ts = "", "", f"{today_str} 08:00"
for d in [today_str, yesterday_str]:
    human_delay()
    sd = safe_call(gar.get_sleep_data, d) or {}
    dto = sd.get("dailySleepDTO") or {}
    if dto and dto.get("sleepTimeSeconds", 0) > 0:
        slp_h = round(float(dto.get("sleepTimeSeconds")) / 3600, 1)
        slp_sc = dto.get("sleepScore") or dto.get("sleepScores", {}).get("overall", {}).get("value") or ""
        raw_ts = dto.get("sleepEndTimestampLocal")
        morning_ts = datetime.fromtimestamp(raw_ts/1000).strftime("%Y-%m-%d %H:%M") if isinstance(raw_ts, (int, float)) else str(raw_ts).replace("T", " ")[:16]
        break

# --- 5. АНАЛИТИКА И GOOGLE ---
creds_dict = json.loads(GOOGLE_CREDS_JSON)
creds = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
ss = gspread.authorize(creds).open("Garmin_Data")
act_sheet = ss.worksheet("Activities")
all_acts = act_sheet.get_all_values()
existing_ids = {r[15] for r in all_acts if len(r) > 15}

# CTL/ATL/TSB
tss_history = [float(str(r[13]).replace(',', '.')) for r in all_acts[1:][-60:] if len(r) > 13 and r[13]]
def get_ewma(data, days):
    if not data: return 0
    a = 2/(days+1)
    res = data[0]
    for x in data[1:]: res = a * x + (1-a) * res
    return round(res, 1)
ctl, atl = get_ewma(tss_history, 42), get_ewma(tss_history, 7)
tsb = round(ctl - atl, 1)

# --- 6. ТРЕНИРОВКИ ---
human_delay()
latest_acts = safe_call(gar.get_activities, 0, 3) or []
new_acts_to_log = []
for a in latest_acts:
    sl = a.get("startTimeLocal", "")
    aid = str(a.get("activityId"))
    if sl.startswith(today_str) and f"'{aid}" not in existing_ids:
        np = a.get('normPower') or a.get('weightedAveragePower', 0)
        avg_p = a.get('avgPower', 0)
        vi = round(float(np)/float(avg_p), 2) if avg_p and np else ""
        row = [
            sl.replace("T", " ")[:16], a.get('activityType', {}).get('typeKey', ''),
            round(a.get('duration', 0)/3600, 2), round(a.get('distance', 0)/1000, 2),
            a.get('averageHR', ""), a.get('maxHR', ""), round(float(a.get('intensityFactor', 0)), 3),
            round(float(a.get('activityTrainingLoad', 0)), 1), round(float(a.get('aerobicTrainingEffect', 0)), 1),
            a.get('calories', ""), avg_p, a.get('averageBikingCadence') or "",
            np, a.get('trainingStressScore', ""), vi, f"'{aid}"
        ]
        new_acts_to_log.append(row)

# --- 7. РЕЗУЛЬТАТ ---
morning_row = [f"'{morning_ts}", weight, fat, muscle, r_hr, hrv, summary.get("bodyBatteryHighestValue", ""), slp_sc, slp_h, 63, 52] # 52 - fit_age заглушка
steps = summary.get('totalSteps', 0)
daily_row = [f"'{today_str}", steps, round(steps * 0.000762, 2), int(summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0)), r_hr, summary.get("bodyBatteryMostRecentValue", "")]

log_sheet = ss.worksheet("AI_Log")
morning_done = any(today_str in str(r[0]) and "Morning" in str(r[1]) for r in log_sheet.get_all_values()[-10:])

report_type, prompt = "", ""
if new_acts_to_log:
    report_type, act = "Activity", new_acts_to_log[0]
    prompt = f"Ты АРНИ. Разбери тренировку: {act[1]}, {act[3]}км, NP {act[12]}W, TSS {act[13]}. TSB {tsb}. Будь краток."
elif not morning_done:
    report_type = "Morning"
    prompt = f"Ты АРНИ. Утро: HRV {hrv}, RHR {r_hr}, Сон {slp_h}ч, BB {morning_row[6]}. CTL {ctl}, TSB {tsb}. Оцени день."

if prompt:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]})
    ai_text = res.json()["candidates"][0]["content"]["parts"][0]["text"].replace('*', '')
    
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": f"<b>{report_type}</b>\n\n{ai_text}\n\n📊 TSB: {tsb}", "parse_mode": "HTML"})
    log_sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), report_type, ai_text])
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    for r in reversed(new_acts_to_log): act_sheet.append_row(r, value_input_option="USER_ENTERED")

print("✅ Done.")
