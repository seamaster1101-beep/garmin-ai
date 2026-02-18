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
    return v if v not in (None, "", 0, "0", "None") else ""

def update_or_append(sheet, date_str, row_data):
    try:
        col_values = sheet.col_values(1)
        if date_str in col_values:
            row_idx = col_values.index(date_str) + 1
            for i, val in enumerate(row_data[1:], start=2):
                if val != "": sheet.update_cell(row_idx, i, val)
            return "Updated"
        else:
            sheet.append_row(row_data)
            return "Appended"
    except Exception as e: return f"Err: {str(e)[:20]}"

# --- LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
except: print("Garmin Login Fail"); exit(1)

now = datetime.now()
today = now.strftime("%Y-%m-%d")
yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# --- DATA COLLECTION ---

# 1. Активности (Тренировки)
workout_info = ""
try:
    activities = gar.get_activities_by_date(today, today)
    if activities:
        summary = []
        for a in activities:
            name = a.get('activityName', 'Training')
            dur = round(a.get('duration', 0) / 60)
            cal = round(a.get('calories', 0))
            summary.append(f"{name} ({dur}м, {cal}ккал)")
        workout_info = " | ".join(summary)
except: workout_info = "No workouts found"

# 2. Вес (Пробуем достать любым способом)
weight = ""
try:
    w_data = gar.get_body_composition(today, today)
    if w_data and w_data.get('uploads'):
        weight = round(w_data['uploads'][-1].get('weight', 0) / 1000, 1)
    if not weight:
        summ = gar.get_user_summary(today)
        weight = round(summ.get('weight', 0) / 1000, 1) if summ.get('weight') else ""
except: weight = ""

# 3. HRV и Пульс
hrv = ""; hr = ""; bb = ""
try:
    stats = gar.get_stats(today) or {}
    hr = safe_val(stats.get("restingHeartRate"))
    bb = safe_val(stats.get("bodyBatteryMostRecentValue"))
    
    h_data = gar.get_hrv_data(today) or gar.get_hrv_data(yesterday)
    if h_data:
        if isinstance(h_data, list): hrv = safe_val(h_data[0].get("lastNightAvg"))
        else: hrv = safe_val(h_data.get("lastNightAvg"))
except: pass

# 4. Сон
slp_sc = ""; slp_h = ""
try:
    sl = gar.get_sleep_data(today)
    d = sl.get("dailySleepDTO") or sl.get("daily_sleep_dto") or {}
    slp_sc = safe_val(d.get("sleepScore"))
    sec = d.get("sleepTimeSeconds", 0)
    if sec > 0: slp_h = round(sec/3600, 1)
except: pass

# --- AI ADVICE ---
advice = "No advice"
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY.strip())
        models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        m_name = "models/gemini-1.5-flash" if "models/gemini-1.5-flash" in models else models[0]
        model = genai.GenerativeModel(m_name)
        prompt = (f"Данные: Сон {slp_h}ч (Score {slp_sc}), HRV {hrv}, HR {hr}, BB {bb}, Вес {weight}. "
                  f"Тренировки сегодня: {workout_info}. Дай анализ и краткий совет (2 фразы).")
        advice = model.generate_content(prompt).text.strip()
    except Exception as e: advice = f"AI Status: {str(e)[:40]}"

# --- SYNC ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    
    # Morning Sheet (Добавь в таблицу колонку H для Training)
    m_sheet = ss.worksheet("Morning")
    m_row = [today, weight, hr, hrv, bb, slp_sc, slp_h, workout_info]
    m_status = update_or_append(m_sheet, today, m_row)
    
    # Log Sheet
    ss.worksheet("AI_Log").append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        f"Status: {m_status} | W:{weight} | HRV:{hrv} | Workouts: Found",
        advice
    ])
    print(f"Done! Workouts: {workout_info}")
except Exception as e: print(f"Sheets Error: {e}")
