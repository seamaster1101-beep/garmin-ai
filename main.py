import os
import json
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

# ---------- CONFIG ----------
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")

def safe_value(val):
    """Проверка на пустые значения"""
    return val if val not in (None, "", 0) else ""

def update_or_append(sheet, date_str, row_data):
    try:
        dates = sheet.col_values(1)
        if date_str in dates:
            idx = dates.index(date_str) + 1
            for i, value in enumerate(row_data[1:], start=2):
                if safe_value(value) != "":
                    sheet.update_cell(idx, i, value)
            return "Updated"
        else:
            sheet.append_row(row_data)
            return "Appended"
    except Exception as ex:
        return f"Error: {ex}"

# ---------- START ----------
try:
    client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    client.login()
except Exception as e:
    print(f"🚨 Garmin login error: {e}")
    exit(1)

now = datetime.now()
today = now.strftime("%Y-%m-%d")
yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
debug = []

# --- 1. STATS ---
try:
    stats = client.get_stats(today) or {}
    resting_hr = safe_value(stats.get("restingHeartRate"))
    body_battery = safe_value(stats.get("bodyBatteryMostRecentValue"))
    debug.append(f"Stats: HR {resting_hr}, BB {body_battery}")
except Exception as e:
    resting_hr = ""
    body_battery = ""
    debug.append(f"StatsErr:{str(e)}")

# --- 2. WEIGHT ---
weight = ""
try:
    w_data = client.get_body_composition(yesterday, today) or {}
    if "uploads" in w_data and w_data["uploads"]:
        weight = safe_value(round(w_data["uploads"][-1].get("weight", 0)/1000, 1))
        debug.append(f"WgtFromUploads:{weight}")
    else:
        summary = client.get_user_summary(today) or {}
        w2 = summary.get("weight", 0)
        weight = safe_value(round(w2/1000, 1)) if w2 else ""
        debug.append(f"WgtFromSummary:{weight}")
except Exception as e:
    debug.append(f"WgtErr:{str(e)}")

# --- 3. HRV ---
hrv = ""
try:
    hrv_data = client.get_hrv_data(today) or []
    if not hrv_data:
        hrv_data = client.get_hrv_data(yesterday) or []
    if hrv_data:
        hrv = safe_value(hrv_data[0].get("lastNightAvg"))
    debug.append(f"HRV:{hrv}")
except Exception as e:
    debug.append(f"HRVErr:{str(e)}")

# --- 4. SLEEP ---
sleep_score = ""
sleep_hours = ""
try:
    sleep = client.get_sleep_data(today) or {}
    dto = sleep.get("dailySleepDTO", {})
    sleep_score = safe_value(dto.get("sleepScore"))
    secs = dto.get("sleepTimeSeconds", 0)
    if secs:
        sleep_hours = safe_value(round(secs/3600, 1))
    debug.append(f"Sleep: Score {sleep_score}, Hours {sleep_hours}")
except Exception as e:
    debug.append(f"SleepErr:{str(e)}")

# --- 5. AI ADVICE ---
ai_advice = "No advice"
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY.strip())
        models = [m.name for m in genai.list_models() if "generateContent" in m.supported_generation_methods]
        model_name = "models/gemini-1.5-pro" if "models/gemini-1.5-pro" in models else models[0]
        model = genai.GenerativeModel(model_name)
        prompt = (
            f"Данные за {today}: Сон {sleep_hours}ч (Score {sleep_score}), "
            f"HRV {hrv}, Пульс покоя {resting_hr}, BodyBattery {body_battery}. "
            "Дай краткий совет на завтра (2 предложения)."
        )
        ai_advice = model.generate_content(prompt).text.strip()
        debug.append("AI:OK")
    except Exception as e:
        ai_advice = f"AIError:{str(e)[:100]}"
        debug.append(ai_advice)

# --- 6. GOOGLE SHEETS SYNC ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    credentials = Credentials.from_service_account_info(
        creds,
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    gs = gspread.authorize(credentials)
    ss = gs.open("Garmin_Data")

    morning = ss.worksheet("Morning")
    row = [
        today,
        weight,
        resting_hr,
        hrv,
        body_battery,
        sleep_score,
        sleep_hours
    ]
    res = update_or_append(morning, today, row)

    ai_log = ss.worksheet("AI_Log")
    ai_log.append_row([
        now.strftime("%Y-%m-%d %H:%M:%S"),
        res,
        " | ".join(debug),
        ai_advice
    ])

    print(f"✔ Done: {res} | {' | '.join(debug)}")
except Exception as e:
    print(f"🚨 SheetsErr: {e}")
