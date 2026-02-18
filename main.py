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

def calculate_intensity(avg_hr, resting_hr):
    try:
        if not avg_hr or not resting_hr: return "N/A"
        avg_hr, resting_hr = float(avg_hr), float(resting_hr)
        max_hr = 185 
        reserve = (avg_hr - resting_hr) / (max_hr - resting_hr)
        if reserve < 0.5: return "Low"
        if reserve < 0.75: return "Moderate"
        return "High"
    except: return "N/A"

def update_or_append(sheet, date_str, row_data):
    try:
        col_values = sheet.col_values(1)
        search_date = date_str.split(' ')[0]
        found_idx = -1
        for i, val in enumerate(col_values):
            if search_date in val:
                found_idx = i + 1
                break
        if found_idx != -1:
            for i, val in enumerate(row_data[1:], start=2):
                if val != "" and val is not None: 
                    sheet.update_cell(found_idx, i, val)
            return "Updated"
        else:
            sheet.append_row(row_data)
            return "Appended"
    except Exception as e: return f"Err: {str(e)[:15]}"

# --- LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
except: print("Fail login"); exit(1)

now = datetime.now()
today_date = now.strftime("%Y-%m-%d")
current_ts = now.strftime("%Y-%m-%d %H:%M")
yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# Инициализация для стабильности AI
steps, dist, cals, r_hr, bb, slp_h = "", "", 0, "", "", ""

# --- 1. DAILY & CALORIES ---
try:
    step_data = gar.get_daily_steps(today_date, today_date)
    if step_data:
        steps = step_data[0].get('totalSteps', "")
        dist = round(step_data[0].get('totalDistance', 0) / 1000, 2)

    summary = gar.get_user_summary(today_date) or {}
    active = summary.get("activeCalories", 0) or 0
    bmr = summary.get("bmrCalories", 0) or 0
    cals = active + bmr if (active + bmr) > 0 else (summary.get("totalCalories") or "")
    
    r_hr = summary.get("restingHeartRate") or ""
    bb = summary.get("bodyBatteryMostRecentValue") or ""
    daily_row = [today_date, steps, dist, cals, r_hr, bb]
except: daily_row = [today_date, "", "", "", "", ""]

# --- 2. MORNING & SLEEP ---
try:
    sl = gar.get_sleep_data(today_date)
    d = sl.get("dailySleepDTO") or {}
    slp_h = round(d.get("sleepTimeSeconds", 0)/3600, 1) if d.get("sleepTimeSeconds") else ""
    slp_sc = d.get("sleepScore", "")
    
    w_comp = gar.get_body_composition(yesterday, today_date)
    weight = round(w_comp['uploads'][-1].get('weight', 0) / 1000, 1) if w_comp and w_comp.get('uploads') else ""
        
    h_data = gar.get_hrv_data(today_date) or gar.get_hrv_data(yesterday)
    hrv = h_data[0].get("lastNightAvg", "") if isinstance(h_data, list) and h_data else ""
    
    morning_row = [current_ts, weight, r_hr, hrv, bb, slp_sc, slp_h]
except: morning_row = [current_ts, "", "", "", "", "", ""]

# --- 3. ACTIVITIES (Сортировка, Каденс и Интенсивность) ---
activities_to_log = []
try:
    acts = gar.get_activities_by_date(today_date, today_date)
    # Сортировка по времени старта
    acts.sort(key=lambda x: x.get('startTimeLocal', ''))
    
    for a in acts:
        st_time = a.get('startTimeLocal', "")[11:16]
        sport = a.get('activityType', {}).get('typeKey', '')
        avg_hr = a.get('averageHR', '')
        
        activities_to_log.append([
            today_date,           # A: Date
            st_time,              # B: Start_Time
            sport,                # C: Sport
            round(a.get('duration', 0) / 3600, 2), # D: Duration
            round(a.get('distance', 0) / 1000, 2), # E: Distance
            avg_hr,               # F: Avg_HR
            a.get('maxHR', ''),   # G: Max_HR
            a.get('trainingLoad', ''), # H: Load
            round(float(a.get('aerobicTrainingEffect', 0)), 1), # I: Effect
            a.get('calories', ''), # J: Calories
            a.get('avgPower', ''), # K: Power
            a.get('averageCadence', ''), # L: Cadence
            calculate_intensity(avg_hr, r_hr) # M: HR_Intensity
        ])
except: pass

# --- 4. GOOGLE SHEETS SYNC ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    
    # 1. Daily & Morning
    update_or_append(ss.worksheet("Daily"), today_date, daily_row)
    update_or_append(ss.worksheet("Morning"), today_date, morning_row)
    
    # 2. Activities (Защита от дублей)
    act_sheet = ss.worksheet("Activities")
    existing_rows = act_sheet.get_all_values()
    existing_keys = [f"{r[0]}_{r[1]}_{r[2]}" for r in existing_rows if len(r) > 2]
    
    for act in activities_to_log:
        key = f"{act[0]}_{act[1]}_{act[2]}"
        if key not in existing_keys:
            act_sheet.append_row(act)

    # 3. AI (Авто-выбор модели)
    advice = "AI Skip"
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            if models:
                model = genai.GenerativeModel(models[0])
                prompt = f"Данные {today_date}: Шаги {steps}, Ккал {cals}, Тренировок {len(activities_to_log)}. Дай 1 совет."
                advice = model.generate_content(prompt).text.strip()
        except: advice = "AI Analysis Error"
    
    ss.worksheet("AI_Log").append_row([current_ts, "Sync Success", advice])
    print(f"✔ Готово! Калории: {cals}, Тренировок записано: {len(activities_to_log)}")

except Exception as e: print(f"❌ Ошибка записи: {e}")
