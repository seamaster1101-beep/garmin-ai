import os
import json
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

# --- START CONFIG ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")
# --- END CONFIG ---

def safe_val(val):
    return val if val not in (None, "", 0) else ""

def get_last_days(n=7):
    base = datetime.now()
    return [(base - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]

def log_api(name, raw):
    try:
        return json.dumps(raw, ensure_ascii=False)
    except:
        return str(raw)

dates = get_last_days(7)
debug = [f"Dates: {dates}"]

try:
    garmin = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    garmin.login()
except Exception as e:
    print(f"Garmin login failed: {e}")
    exit(1)

# 1) STATS
try:
    stats = garmin.get_stats(dates[0]) or {}
    resting_hr = safe_val(stats.get("restingHeartRate"))
    body_battery = safe_val(stats.get("bodyBatteryMostRecentValue"))
    debug.append(f"Stats: HR {resting_hr}, BB {body_battery}")
except Exception as e:
    resting_hr = ""
    body_battery = ""
    debug.append(f"StatsErr: {e}")

# 2) WEIGHT RAW
weight = ""
weight_raw_all = {}
for d in dates:
    try:
        w_raw = garmin.get_body_composition(d, d) or {}
        weight_raw_all[d] = w_raw
        if w_raw.get("uploads"):
            weight = safe_val(round(w_raw["uploads"][-1].get("weight", 0)/1000, 1))
            break
    except Exception as e:
        weight_raw_all[d] = {"error": str(e)}

# 3) HRV RAW
hrv = ""
hrv_raw_all = {}
for d in dates:
    try:
        raw_hrv = garmin.get_hrv_data(d) or {}
        hrv_raw_all[d] = raw_hrv
        if isinstance(raw_hrv, list) and raw_hrv and raw_hrv[0].get("lastNightAvg"):
            hrv = safe_val(raw_hrv[0].get("lastNightAvg"))
            break
    except Exception as e:
        hrv_raw_all[d] = {"error": str(e)}

# 4) SLEEP RAW
sleep = ""
sleep_raw_all = {}
for d in dates:
    try:
        raw_slp = garmin.get_sleep_data(d) or {}
        sleep_raw_all[d] = raw_slp
        dto = raw_slp.get("dailySleepDTO", {})
        if dto.get("sleepTimeSeconds"):
            sleep = safe_val(round(dto["sleepTimeSeconds"]/3600,1))
            break
    except Exception as e:
        sleep_raw_all[d] = {"error": str(e)}

debug.append(f"Weight: {weight}")
debug.append(f"HRV: {hrv}")
debug.append(f"Sleep: {sleep}")

# AI ADVICE
ai_advice = ""
try:
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY.strip())
        models = [m.name for m in genai.list_models() if "generateContent" in m.supported_generation_methods]
        model_nm = "models/gemini-1.5-pro" if "models/gemini-1.5-pro" in models else models[0]
        model = genai.GenerativeModel(model_nm)

        prompt = (
            f"Sleep {sleep}h, HRV {hrv}, "
            f"Resting HR {resting_hr}, BodyBattery {body_battery}."
            "Совет на завтра."
        )
        ai_advice = model.generate_content(prompt).text.strip()
except Exception as e:
    ai_advice = f"AI error: {e}"

# Write to Sheets
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    creds_obj = Credentials.from_service_account_info(creds, scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ])
    client = gspread.authorize(creds_obj)
    ss = client.open("Garmin_Data")

    # Morning
    morning = ss.worksheet("Morning")
    morning.append_row([
        datetime.now().strftime("%Y-%m-%d"),
        weight,
        resting_hr,
        hrv,
        body_battery,
        sleep
    ])

    # Detailed API Log
    ai_log = ss.worksheet("AI_Log")
    ai_log.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ai_advice,
        log_api("Weight", weight_raw_all),
        log_api("HRV", hrv_raw_all),
        log_api("Sleep", sleep_raw_all)
    ])

    print("✔ Done.")
except Exception as e:
    print("Sheets error:", e)
