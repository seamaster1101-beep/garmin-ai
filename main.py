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
    except Exception as e: return f"Err: {e}"

# --- LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
except: print("Fail login"); exit(1)

now = datetime.now()
today_date = now.strftime("%Y-%m-%d")
current_ts = now.strftime("%Y-%m-%d %H:%M")
yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# Инициализируем переменные заранее, чтобы AI не выдавал ошибку NameError
steps, dist, cals, r_hr, bb, slp_h = "", "", "", "", "", ""

# --- 1. DAILY & CALORIES (Комбинированный метод) ---
try:
    # 1. Тянем шаги и дистанцию (самый точный метод)
    step_data = gar.get_daily_steps(today_date, today_date)
    if step_data:
        steps = step_data[0].get('totalSteps', "")
        # Дистанция в метрах, переводим в км
        dist = round(step_data[0].get('totalDistance', 0) / 1000, 2)

    # 2. Тянем калории через User Summary
    summary = gar.get_user_summary(today_date) or {}
    active = summary.get("activeCalories", 0) or 0
    bmr = summary.get("bmrCalories", 0) or 0
    cals = active + bmr if (active + bmr) > 0 else summary.get("totalCalories", "")
    
    r_hr = summary.get("restingHeartRate") or ""
    bb = summary.get("bodyBatteryMostRecentValue") or ""
    
    daily_row = [today_date, steps, dist, cals, r_hr, bb]
except Exception as e:
    print(f"Daily Error: {e}")
    daily_row = [today_date, "", "", "", "", ""]

# --- 2. MORNING & SLEEP ---
try:
    sl = gar.get_sleep_data(today_date)
    d = sl.get("dailySleepDTO") or {}
    slp_h = round(d.get("sleepTimeSeconds", 0)/3600, 1) if d.get("sleepTimeSeconds") else ""
    
    # Вес и HRV
    w_comp = gar.get_body_composition(yesterday, today_date)
    weight = round(w_comp['uploads'][-1].get('weight', 0) / 1000, 1) if w_comp and w_comp.get('uploads') else ""
    h_data = gar.get_hrv_data(today_date) or gar.get_hrv_data(yesterday)
    hrv = h_data[0].get("lastNightAvg", "") if isinstance(h_data, list) and h_data else ""
    
    morning_row = [current_ts, weight, r_hr, hrv, bb, d.get("sleepScore", ""), slp_h]
except: morning_row = [current_ts, "", "", "", "", "", ""]

# --- 3. ACTIVITIES ---
try:
    acts = gar.get_activities_by_date(today_date, today_date)
    activities_to_log = []
    for a in acts:
        activities_to_log.append([
            today_date, a.get('startTimeLocal', "")[11:16], a.get('activityType', {}).get('typeKey', ''),
            round(a.get('duration', 0) / 3600, 2), round(a.get('distance', 0) / 1000, 2),
            a.get('averageHR', ''), a.get('maxHR', ''), a.get('trainingLoad', ''),
            round(float(a.get('aerobicTrainingEffect', 0)), 1), a.get('calories', '')
        ])
except: activities_to_log = []

# --- 4. SYNC & AI ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    
    update_or_append(ss.worksheet("Daily"), today_date, daily_row)
    update_or_append(ss.worksheet("Morning"), today_date, morning_row)
    
    # Запись активностей
    act_sheet = ss.worksheet("Activities")
    for act in activities_to_log:
        act_sheet.append_row(act)

    # AI Совет (теперь переменные точно определены)
    advice = "AI Skip"
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            model = genai.GenerativeModel('gemini-1.5-flash')
            prompt = f"Данные: Шаги {steps}, Ккал {cals}, Дистанция {dist}км. Дай 1 короткий совет."
            advice = model.generate_content(prompt).text.strip()
        except Exception as e: advice = f"AI Error: {e}"
    
    ss.worksheet("AI_Log").append_row([current_ts, "Success", advice])
    print(f"✔ Готово! Калории: {cals}, Дистанция: {dist}")
except Exception as e: print(f"❌ Ошибка синхронизации: {e}")
