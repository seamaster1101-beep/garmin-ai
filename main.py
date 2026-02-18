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

def find_hrv_for_days(client, dates):
    """Ищем HRV за несколько дней, возвращаем первый непустой"""
    for d in dates:
        try:
            data = client.get_hrv_data(d) or []
            if data and data[0].get("lastNightAvg"):
                return safe_value(data[0].get("lastNightAvg")), d
        except:
            pass
    return "", ""

def find_sleep_for_days(client, dates):
    """Ищем данные сна за несколько дней"""
    for d in dates:
        try:
            sleep = client.get_sleep_data(d) or {}
            dto = sleep.get("dailySleepDTO", {})
            score = dto.get("sleepScore")
            secs = dto.get("sleepTimeSeconds", 0)
            if score or secs > 0:
                hrs = round(secs / 3600, 1) if secs else ""
                return safe_value(score), safe_value(hrs), d
        except:
            pass
    return "", "", ""

def update_or_append(sheet, date_str, row_data):
    try:
        dates = sheet.col_values(1)
        if date_str in dates:
            idx = dates.index(date_str) + 1
            for i, val in enumerate(row_data[1:], start=2):
                if safe_value(val) != "":
                    sheet.update_cell(idx, i, val)
            return "Updated"
        else:
            sheet.append_row(row_data)
            return "Appended"
    except Exception as e:
        return f"Error: {e}"

# ---------- START ----------
try:
    garmin = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    garmin.login()
except Exception as e:
    print(f"🚨 Garmin login error: {e}")
    exit(1)

now = datetime.now()
today = now.strftime("%Y-%m-%d")
yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
day2 = (now - timedelta(days=2)).strftime("%Y-%m-%d")
debug = [f"Dates tried: {today}, {yesterday}, {day2}"]

# --- STATS ---
try:
    stats = garmin.get_stats(today) or {}
    resting_hr = safe_value(stats.get("restingHeartRate"))
    body_battery = safe_value(stats.get("bodyBatteryMostRecentValue"))
    debug.append(f"Stats: HR {resting_hr}, BB {body_battery}")
except Exception as e:
    resting_hr = ""
    body_battery = ""
    debug.append(f"StatsErr:{e}")

# --- WEIGHT ---
weight = ""
try:
    w_data = garmin.get_body_composition(yesterday, today) or {}
    if "uploads" in w_data and w_data["uploads"]:
        weight = safe_value(round(w_data["uploads"][-1].get("weight", 0) / 1000, 1))
        debug.append(f"WgtFromUploads:{weight}")
    else:
        summary = garmin.get_user_summary(today) or {}
        w2 = summary.get("weight", 0)
        weight = safe_value(round(w2 / 1000, 1)) if w2 else ""
        debug.append(f"WgtFromSummary:{weight}")
except Exception as e:
    debug.append(f"WeightErr:{e}")

# --- HRV (за 3 дня) ---
hrv, hrv_date = find_hrv_for_days(garmin, [today, yesterday, day2])
debug.append(f"HRV:{hrv} from {hrv_date if hrv_date else 'none'}")

# --- SLEEP (за 3 дня) ---
sleep_score, sleep_hours, sleep_date = find_sleep_for_days(garmin, [today, yesterday, day2])
debug.append(f"Sleep Score:{sleep_score} Hours:{sleep_hours} from {sleep_date if sleep_date else 'none'}")

# --- AI ADVICE ---
ai_advice = "No advice"
try:
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY.strip())
        models = [m.name for m in genai.list_models() if "generateContent" in m.supported_generation_methods]
        model_name = "models/gemini-1.5-pro" if "models/gemini-1.5-pro" in models else models[0]
        model = genai.GenerativeModel(model_name)

        prompt = (
            f"Данные: Сон {sleep_hours}ч (Score {sleep_score}), HRV {hrv},"
            f" Пульс покоя {resting_hr}, BodyBattery {body_battery}. "
            "Дай краткий совет на завтра (2 предложения)."
        )
        ai_advice = model.generate_content(prompt).text.strip()
        debug.append("AI:OK")
except Exception as e:
    debug.append(f"AIErr:{str(e)[:80]}")
    ai_advice = f"AI Error: {str(e)[:100]}"

# --- GOOGLE SHEETS SYNC ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    credentials = Credentials.from_service_account_info(
        creds,
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    gs = gspread.authorize(credentials)
    sheet = gs.open("Garmin_Data")

    morning = sheet.worksheet("Morning")
    row = [
        today,
        weight,
        resting_hr,
        hrv,
        body_battery,
        sleep_score,
        sleep_hours
    ]
    result = update_or_append(morning, today, row)

    ai_log = sheet.worksheet("AI_Log")
    ai_log.append_row([
        now.strftime("%Y-%m-%d %H:%M:%S"),
        result,
        " | ".join(debug),
        ai_advice
    ])

    print(f"✔ {result} | {' | '.join(debug)}")
except Exception as e:
    print(f"🚨 Sheets Error: {e}")
