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

def safe_val(v, default=""):
    return v if v not in (None, "", 0, "0", "None") else default

def calculate_intensity(avg_hr, resting_hr):
    try:
        if not avg_hr or not resting_hr: return "N/A"
        avg_hr = float(avg_hr)
        resting_hr = float(resting_hr)
        max_hr = 185 # Можно заменить на свою формулу (220 - возраст)
        # Процент от резерва пульса (Карвонен)
        intensity = (avg_hr - resting_hr) / (max_hr - resting_hr)
        if intensity < 0.5: return "Low"
        if intensity < 0.75: return "Moderate"
        return "High"
    except: return "N/A"

def update_or_append(sheet, date_str, row_data):
    try:
        col_values = sheet.col_values(1)
        if date_str in col_values:
            row_idx = col_values.index(date_str) + 1
            for i, val in enumerate(row_data[1:], start=2):
                if val != "":
                    sheet.update_cell(row_idx, i, val)
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

# --- 1. DAILY & RESTING HR (Нужен для интенсивности) ---
try:
    s = gar.get_stats(today) or {}
    resting_hr = s.get("restingHeartRate", "")
    daily_row = [
        today, 
        s.get("steps", ""), 
        round(s.get("distance", 0) / 1000, 2) if s.get("distance") else "",
        s.get("calories", ""), 
        resting_hr, 
        s.get("bodyBatteryMostRecentValue", "")
    ]
except: 
    daily_row = [today, "", "", "", "", ""]
    resting_hr = ""

# --- 2. ACTIVITIES (Лист Activities) ---
activities_to_log = []
try:
    acts = gar.get_activities_by_date(today, today)
    for a in acts:
        raw_start = a.get('startTimeLocal', today)
        start_time = raw_start.split('T')[1][:5] if 'T' in raw_start else raw_start
        avg_hr = a.get('averageHR', '')
        
        # Расчет интенсивности
        intensity_label = calculate_intensity(avg_hr, resting_hr)
        
        activities_to_log.append([
            today,                                      # A: Date
            start_time,                                 # B: Start_Time
            a.get('activityType', {}).get('typeKey', ''), # C: Sport
            round(a.get('duration', 0) / 3600, 2),      # D: Duration_hr
            round(a.get('distance', 0) / 1000, 2),      # E: Distance_km
            avg_hr,                                     # F: Avg_HR
            a.get('maxHR', ''),                        # G: Max_HR
            a.get('trainingLoad', ''),                 # H: Training_Load
            a.get('aerobicTrainingEffect', ''),        # I: Training_Effect
            a.get('calories', ''),                     # J: Calories
            a.get('avgPower', ''),                     # K: Avg_Power
            a.get('averageCadence', ''),               # L: Cadence
            intensity_label                             # M: HR_Intensity
        ])
except: pass

# --- 3. MORNING DATA ---
try:
    w_comp = gar.get_body_composition(today, today)
    weight = round(w_comp['uploads'][-1].get('weight', 0) / 1000, 1) if w_comp.get('uploads') else ""
    h_data = gar.get_hrv_data(today) or gar.get_hrv_data(yesterday)
    hrv = h_data[0].get("lastNightAvg", "") if isinstance(h_data, list) and h_data else ""
    sl = gar.get_sleep_data(today)
    d = sl.get("dailySleepDTO") or {}
    slp_sc = d.get("sleepScore", "")
    slp_h = round(d.get("sleepTimeSeconds", 0)/3600, 1) if d.get("sleepTimeSeconds") else ""
except: weight = hrv = slp_sc = slp_h = ""

# --- 4. AI ---
advice = "No advice"
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY.strip())
        model = genai.GenerativeModel("models/gemini-1.5-flash")
        prompt = (f"Анализ: Сон {slp_h}ч, HRV {hrv}, BB {daily_row[5]}. "
                  f"Тренировок сегодня: {len(activities_to_log)}. "
                  f"Интенсивность последней: {activities_to_log[-1][-1] if activities_to_log else 'Нет'}. "
                  f"Дай совет на завтра.")
        advice = model.generate_content(prompt).text.strip()
    except: advice = "AI Error"

# --- 5. SYNC ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    
    # Activities
    act_sheet = ss.worksheet("Activities")
    existing_entries = [f"{r[0]}_{r[1]}" for r in act_sheet.get_all_values()]
    for act in activities_to_log:
        if f"{act[0]}_{act[1]}" not in existing_entries:
            act_sheet.append_row(act)

    # Daily & Morning
    update_or_append(ss.worksheet("Daily"), today, daily_row)
    update_or_append(ss.worksheet("Morning"), today, [today, weight, daily_row[4], hrv, daily_row[5], slp_sc, slp_h])
    
    # AI Log
    ss.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Activity Sync", advice])
    print(f"✔ Готово! Последняя тренировка: {activities_to_log[-1][2] if activities_to_log else 'Не найдена'}")
except Exception as e: print(f"Error: {e}")
