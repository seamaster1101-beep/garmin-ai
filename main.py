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

def safe_val(v):
    return v if v not in (None, "", 0) else ""

# Функция обновления или добавления строки
def update_or_append(sheet, date_str, row_data):
    try:
        col_values = sheet.col_values(1)
        if date_str in col_values:
            row_idx = col_values.index(date_str) + 1
            # Обновляем со 2-го столбца
            for i, val in enumerate(row_data[1:], start=2):
                if val != "":
                    sheet.update_cell(row_idx, i, val)
            return "Updated"
        else:
            sheet.append_row(row_data)
            return "Appended"
    except Exception as e:
        return f"Error: {e}"

# --- LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
except Exception as e:
    print("Garmin login fail:", e)
    exit(1)

now = datetime.now()
today = now.strftime("%Y-%m-%d")
yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# --- DATA COLLECTION ---

# 1. Основные статы (Пульс, Батарейка)
try:
    stats = gar.get_stats(today) or {}
    resting_hr = safe_val(stats.get("restingHeartRate"))
    body_battery = safe_val(stats.get("bodyBatteryMostRecentValue"))
except: resting_hr = body_battery = ""

# 2. Вес (Берем из саммари - самый надежный способ)
weight = ""
try:
    summary = gar.get_user_summary(today)
    w_raw = summary.get("weight")
    if w_raw:
        weight = round(w_raw / 1000, 1)
except: weight = ""

# 3. HRV (Проверяем сегодня и вчера)
hrv = ""
try:
    hr_data = gar.get_hrv_data(today) or gar.get_hrv_data(yesterday)
    if hr_data and isinstance(hr_data, list):
        hrv = safe_val(hr_data[0].get("lastNightAvg"))
except: hrv = ""

# 4. Сон
sleep_score = ""
sleep_hours = ""
try:
    sr = gar.get_sleep_data(today)
    dto = sr.get("dailySleepDTO", {})
    sc = dto.get("sleepScore")
    secs = dto.get("sleepTimeSeconds", 0)
    if sc or secs > 0:
        sleep_score = safe_val(sc)
        sleep_hours = safe_val(round(secs/3600, 1))
except: sleep_score = sleep_hours = ""

# --- AI ADVICE ---
ai_advice = "No advice"
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY.strip())
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = (
            f"Анализ на {today}: Сон {sleep_hours}ч (Score {sleep_score}), HRV {hrv}, "
            f"Пульс {resting_hr}, Батарейка {body_battery}, Вес {weight}. "
            f"Дай краткий совет на завтра (2 предложения)."
        )
        ai_advice = model.generate_content(prompt).text.strip()
    except Exception as e:
        ai_advice = f"AI Error: {e}"

# --- WRITE TO SHEETS ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    cred_obj = Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    gs = gspread.authorize(cred_obj)
    ss = gs.open("Garmin_Data")

    # Обновляем Morning (без дублей)
    morning_sheet = ss.worksheet("Morning")
    morning_row = [today, weight, resting_hr, hrv, body_battery, sleep_score, sleep_hours]
    res_m = update_or_append(morning_sheet, today, morning_row)

    # Обновляем AI_Log (чистый вид)
    log_sheet = ss.worksheet("AI_Log")
    log_sheet.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        f"Status: {res_m}",
        ai_advice
    ])

    print(f"✔ Done! Morning: {res_m}, Weight: {weight}, HRV: {hrv}")
except Exception as e:
    print("Sheets Err:", e)
