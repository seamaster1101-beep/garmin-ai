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

# --- 1. ТРЕНИРОВКИ (Лист Activities) ---
workout_summary = []
activities_to_log = []
try:
    activities = gar.get_activities_by_date(today, today)
    for a in activities:
        name = a.get('activityName', 'Training')
        dur = round(a.get('duration', 0) / 60)
        cal = round(a.get('calories', 0))
        start_time = a.get('startTimeLocal', today)
        workout_summary.append(f"{name} ({dur}м)")
        activities_to_log.append([start_time, name, dur, cal])
except: pass

# --- 2. ЕЖЕДНЕВНЫЕ ДАННЫЕ (Лист Daily) ---
try:
    s = gar.get_stats(today) or {}
    steps = s.get("steps", 0)
    dist = round(s.get("distance", 0) / 1000, 2)
    cals = s.get("calories", 0)
    hr = safe_val(s.get("restingHeartRate"))
    bb = safe_val(s.get("bodyBatteryMostRecentValue"))
except: steps = dist = cals = hr = bb = ""

# --- 3. УТРЕННИЕ ДАННЫЕ (Лист Morning) ---
weight = ""; hrv = ""; slp_sc = ""; slp_h = ""
try:
    # Вес
    w_comp = gar.get_body_composition(today, today)
    if w_comp and w_comp.get('uploads'):
        weight = round(w_comp['uploads'][-1].get('weight', 0) / 1000, 1)
    
    # HRV
    h = gar.get_hrv_data(today) or gar.get_hrv_data(yesterday)
    if h: hrv = safe_val(h[0].get("lastNightAvg") if isinstance(h, list) else h.get("lastNightAvg"))
    
    # Сон
    sl = gar.get_sleep_data(today)
    d = sl.get("dailySleepDTO") or sl.get("daily_sleep_dto") or {}
    slp_sc = safe_val(d.get("sleepScore"))
    sec = d.get("sleepTimeSeconds", 0)
    if sec > 0: slp_h = round(sec/3600, 1)
except: pass

# --- 4. AI АНАЛИЗ ---
advice = "No advice"
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY.strip())
        models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        m_name = "models/gemini-1.5-flash" if "models/gemini-1.5-flash" in models else models[0]
        model = genai.GenerativeModel(m_name)
        prompt = (f"Анализ {today}: Сон {slp_h}ч (Score {slp_sc}), HRV {hrv}, HR {hr}, BB {bb}. "
                  f"Тренировки: {', '.join(workout_summary)}. Дай короткий совет (2 фразы).")
        advice = model.generate_content(prompt).text.strip()
    except: advice = "AI busy"

# --- 5. ЗАПИСЬ В ТАБЛИЦУ ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    
    # Daily (Обновляем текущий день)
    update_or_append(ss.worksheet("Daily"), today, [today, steps, dist, cals, hr, bb])
    
    # Morning (Обновляем утренние замеры)
    update_or_append(ss.worksheet("Morning"), today, [today, weight, hr, hrv, bb, slp_sc, slp_h])
    
    # Activities (Добавляем новые, если их еще нет)
    act_sheet = ss.worksheet("Activities")
    existing_times = act_sheet.col_values(1)
    for act in activities_to_log:
        if act[0] not in existing_times:
            act_sheet.append_row(act)
            
    # AI Log
    ss.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Full Sync Success", advice])
    print("✔ Все листы обновлены!")
except Exception as e: print(f"Error: {e}")
