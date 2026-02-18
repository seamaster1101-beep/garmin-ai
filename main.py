import os
import json
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

# --- CONFIG ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")
# --- END CONFIG ---

def safe_val(v):
    return v if v not in (None, "", 0) else ""

def get_past(n=7):
    base = datetime.now()
    return [(base - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]

dates = get_past(7)
debug = [f"Dates: {dates}"]

def log_json(obj):
    try:
        return json.dumps(obj, ensure_ascii=False)
    except:
        return str(obj)

# --- LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
except Exception as e:
    print("Garmin login fail:", e)
    exit(1)

# --- STATS (today) ---
try:
    stats = gar.get_stats(dates[0]) or {}
    resting_hr = safe_val(stats.get("restingHeartRate"))
    body_battery = safe_val(stats.get("bodyBatteryMostRecentValue"))
    debug.append(f"Stats: HR {resting_hr}, BB {body_battery}")
except Exception as e:
    resting_hr = ""
    body_battery = ""
    debug.append(f"StatsErr: {e}")

# --- WEIGHT (7 days) ---
weight = ""
weight_raw_all = {}
for d in dates:
    try:
        wr = gar.get_body_composition(d, d) or {}
        weight_raw_all[d] = wr
        if wr.get("uploads"):
            w_val = wr["uploads"][-1].get("weight")
            if w_val:
                weight = safe_val(round(w_val/1000,1))
                break
    except Exception as e:
        weight_raw_all[d] = {"error": str(e)}

debug.append(f"Weight: {weight}")

# --- HRV (7 days) ---
hrv = ""
hrv_raw_all = {}
for d in dates:
    try:
        hr = gar.get_hrv_data(d) or {}
        hrv_raw_all[d] = hr
        if isinstance(hr, list) and hr and hr[0].get("lastNightAvg"):
            hrv = safe_val(hr[0].get("lastNightAvg"))
            break
    except Exception as e:
        hrv_raw_all[d] = {"error": str(e)}

debug.append(f"HRV: {hrv}")

# --- SLEEP (7 days) ---
sleep_score = ""
sleep_hours = ""
sleep_raw_all = {}
for d in dates:
    try:
        sr = gar.get_sleep_data(d) or {}
        sleep_raw_all[d] = sr
        dto = sr.get("dailySleepDTO", {})
        sc = dto.get("sleepScore")
        secs = dto.get("sleepTimeSeconds", 0)
        if sc or secs > 0:
            sleep_score = safe_val(sc)
            sleep_hours = safe_val(round(secs/3600, 1)) if secs else ""
            break
    except Exception as e:
        sleep_raw_all[d] = {"error": str(e)}

debug.append(f"Sleep Score: {sleep_score}")
debug.append(f"Sleep Hours: {sleep_hours}")

# --- AI Advice ---
ai_advice = "No advice"
try:
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY.strip())
        models = [m.name for m in genai.list_models() if "generateContent" in m.supported_generation_methods]
        model_name = "models/gemini-1.5-pro" if "models/gemini-1.5-pro" in models else models[0]
        gen_model = genai.GenerativeModel(model_name)

        prompt = (
            f"Сон {sleep_hours}ч (Score {sleep_score}), HRV {hrv}, "
            f"RestHR {resting_hr}, BodyBattery {body_battery}, Вес {weight}."
            "Совет на завтра (2 предложения)."
        )
        ai_advice = gen_model.generate_content(prompt).text.strip()
        debug.append("AI:OK")
except Exception as e:
    debug.append(f"AIErr:{str(e)[:80]}")
    ai_advice = f"AI Error: {str(e)[:100]}"

# --- WRITE TO SHEETS ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    cred_obj = Credentials.from_service_account_info(
        creds,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
    )
    gs = gspread.authorize(cred_obj)
    ss = gs.open("Garmin_Data")

    # Morning sheet
    morning = ss.worksheet("Morning")
    morning.append_row([
        dates[0],
        weight,
        resting_hr,
        hrv,
        body_battery,
        sleep_score,
        sleep_hours
    ])

    # Log detailed
    ai_log = ss.worksheet("AI_Log")
    ai_log.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ai_advice,
        log_json(weight_raw_all),
        log_json(hrv_raw_all),
        log_json(sleep_raw_all)
    ])

    print("✔ Done!")
except Exception as e:
    print("Sheets Err:", e)
