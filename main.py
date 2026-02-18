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

def get_past_dates(n=7):
    """Возвращает список дат (строк) за последние n дней"""
    base = datetime.now()
    return [(base - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]

def find_hrv(client, dates):
    """Ищем HRV за несколько дней"""
    for d in dates:
        try:
            raw = client.get_hrv_data(d) or []
            if raw and raw[0].get("lastNightAvg"):
                return safe_value(raw[0].get("lastNightAvg")), d, raw
        except Exception as e:
            return "", d, {"error": str(e)}
    return "", "", []

def find_sleep(client, dates):
    """Ищем сон за несколько дней"""
    for d in dates:
        try:
            raw = client.get_sleep_data(d) or {}
            dto = raw.get("dailySleepDTO", {})
            score = dto.get("sleepScore")
            secs = dto.get("sleepTimeSeconds", 0)
            if score or secs > 0:
                hrs = round(secs / 3600, 1) if secs else ""
                return safe_value(score), safe_value(hrs), d, raw
        except Exception as e:
            return "", "", d, {"error": str(e)}
    return "", "", "", {}

def find_weight(client, dates):
    """Пытаемся взять вес за несколько дней"""
    for d in dates:
        try:
            raw = client.get_body_composition(d, d) or {}
            if "uploads" in raw and raw["uploads"]:
                w = raw["uploads"][-1].get("weight")
                if w:
                    return safe_value(round(w / 1000, 1)), d, raw
        except Exception as e:
            return "", d, {"error": str(e)}
    # Попробуем summary за последнюю дату
    try:
        raw = client.get_user_summary(dates[0]) or {}
        w2 = raw.get("weight")
        if w2:
            return safe_value(round(w2 / 1000, 1)), dates[0], raw
    except Exception as e:
        return "", dates[0], {"error": str(e)}
    return "", "", {}

def update_or_append(sheet, date_str, row_data):
    try:
        dates = sheet.col_values(1)
        if date_str in dates:
            idx = dates.index(date_str) + 1
            for col, val in enumerate(row_data[1:], start=2):
                if safe_value(val) != "":
                    sheet.update_cell(idx, col, val)
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

dates = get_past_dates(7)
debug = [f"Dates tried: {', '.join(dates)}"]

# --- STATS (TODAY) ---
try:
    stats = garmin.get_stats(dates[0]) or {}
    resting_hr = safe_value(stats.get("restingHeartRate"))
    body_battery = safe_value(stats.get("bodyBatteryMostRecentValue"))
    debug.append(f"Stats (today): HR {resting_hr}, BB {body_battery}")
except Exception as e:
    resting_hr = ""
    body_battery = ""
    debug.append(f"StatsErr:{e}")

# --- WEIGHT (7 days) ---
weight, w_date, w_raw = find_weight(garmin, dates)
debug.append(f"Weight:{weight}kg from {w_date}")
debug.append(f"Weight_raw:{json.dumps(w_raw)[:200]}")

# --- HRV (7 days) ---
hrv, hrv_date, hrv_raw = find_hrv(garmin, dates)
debug.append(f"HRV:{hrv} from {hrv_date}")
debug.append(f"HRV_raw:{json.dumps(hrv_raw)[:200]}")

# --- SLEEP (7 days) ---
sleep_score, sleep_hours, sleep_date, sleep_raw = find_sleep(garmin, dates)
debug.append(f"Sleep Score:{sleep_score} Hours:{sleep_hours} from {sleep_date}")
debug.append(f"Sleep_raw:{json.dumps(sleep_raw)[:200]}")

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
            f" Пульс покоя {resting_hr}, BodyBattery {body_battery}, Вес {weight}. "
            "Краткий совет на завтра (2 предложения)."
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
        dates[0],
        weight,
        resting_hr,
        hrv,
        body_battery,
        sleep_score,
        sleep_hours
    ]
    result = update_or_append(morning, dates[0], row)

    ai_log = sheet.worksheet("AI_Log")
    ai_log.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        result,
        " | ".join(debug),
        ai_advice
    ])

    print(f"✔ {result} | {' | '.join(debug)}")
except Exception as e:
    print(f"🚨 Sheets Error: {e}")
