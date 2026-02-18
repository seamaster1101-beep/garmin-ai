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
    return v if v not in (None, "", 0, "0") else ""

def update_or_append(sheet, date_str, row_data):
    """Ищет дату в 1-м столбце. Если нашел — обновляет непустые ячейки."""
    try:
        col_values = sheet.col_values(1)
        if date_str in col_values:
            row_idx = col_values.index(date_str) + 1
            # Обновляем ячейки со 2-й колонки (B, C, D...)
            for i, val in enumerate(row_data[1:], start=2):
                if val != "":
                    sheet.update_cell(row_idx, i, val)
            return "Updated"
        else:
            sheet.append_row(row_data)
            return "Appended"
    except Exception as e:
        return f"Sheets Err: {e}"

# --- LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
except Exception as e:
    print(f"Garmin login fail: {e}")
    exit(1)

now = datetime.now()
today = now.strftime("%Y-%m-%d")
yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# --- DATA COLLECTION ---

# 1. Базовые статы
try:
    stats = gar.get_stats(today) or {}
    resting_hr = safe_val(stats.get("restingHeartRate"))
    body_battery = safe_val(stats.get("bodyBatteryMostRecentValue"))
except: resting_hr = body_battery = ""

# 2. Вес (Пробуем 2 метода)
weight = ""
try:
    # Метод 1: Summary
    summary = gar.get_user_summary(today)
    if summary.get("weight"):
        weight = round(summary["weight"] / 1000, 1)
    else:
        # Метод 2: Composition за последние 2 дня
        w_comp = gar.get_body_composition(yesterday, today)
        if w_comp.get("uploads"):
            weight = round(w_comp["uploads"][-1]["weight"] / 1000, 1)
except: weight = ""

# 3. HRV (Сегодня или вчера)
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
    dto = sr.get("daily_sleep_dto", sr.get("dailySleepDTO", {}))
    sleep_score = safe_val(dto.get("sleepScore"))
    secs = dto.get("sleepTimeSeconds", 0)
    if secs > 0:
        sleep_hours = round(secs / 3600, 1)
except: sleep_score = sleep_hours = ""

# --- AI ADVICE (С защитой от 404) ---
ai_advice = "No advice available"
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY.strip())
        # Авто-подбор доступной модели
        models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        target_model = "models/gemini-1.5-flash" if "models/gemini-1.5-flash" in models else models[0]
        
        gen_model = genai.GenerativeModel(target_model)
        prompt = (f"Анализ {today}: Сон {sleep_hours}ч, HRV {hrv}, HR {resting_hr}, "
                  f"Батарейка {body_battery}, Вес {weight}. Дай короткий совет (2 фразы).")
        ai_advice = gen_model.generate_content(prompt).text.strip()
    except Exception as e:
        ai_advice = f"AI Status: Модель недоступна ({str(e)[:50]})"

# --- WRITE TO SHEETS ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    cred_obj = Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    gs = gspread.authorize(cred_obj)
    ss = gs.open("Garmin_Data")

    # 1. Morning Sheet (Update existing or Add new)
    m_sheet = ss.worksheet("Morning")
    m_row = [today, weight, resting_hr, hrv, body_battery, sleep_score, sleep_hours]
    m_status = update_or_append(m_sheet, today, m_row)

    # 2. AI_Log (Clean Log)
    l_sheet = ss.worksheet("AI_Log")
    l_sheet.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        f"Sync: {m_status} (W:{weight}, HRV:{hrv})",
        ai_advice
    ])

    print(f"✔ Финиш! Статус: {m_status}, Вес: {weight}, HRV: {hrv}")
except Exception as e:
    print(f"Sheets Error: {e}")
