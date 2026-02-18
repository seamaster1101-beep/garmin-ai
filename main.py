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
        avg_hr, resting_hr = float(avg_hr), float(resting_hr)
        max_hr = 185 
        reserve = (avg_hr - resting_hr) / (max_hr - resting_hr)
        if reserve < 0.5: return "Low"
        if reserve < 0.75: return "Moderate"
        return "High"
    except: return "N/A"

def update_or_append(sheet, date_str, row_data, search_by_date_only=True):
    try:
        col_values = sheet.col_values(1)
        search_term = date_str.split(' ')[0] if search_by_date_only else date_str
        found_idx = -1
        for i, val in enumerate(col_values):
            if search_term in val:
                found_idx = i + 1
                break
        if found_idx != -1:
            for i, val in enumerate(row_data[1:], start=2):
                if val != "": sheet.update_cell(found_idx, i, val)
            return "Updated"
        else:
            sheet.append_row(row_data)
            return "Appended"
    except Exception as e: return f"Err: {str(e)[:15]}"

# --- LOGIN ---
gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
gar.login()

now = datetime.now()
today_date = now.strftime("%Y-%m-%d")
morning_ts = now.strftime("%Y-%m-%d %H:%M") # Дата + время для Morning
yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# --- 1. DAILY (С двойной проверкой данных) ---
try:
    s = gar.get_stats(today_date) or {}
    sumry = gar.get_user_summary(today_date) or {}
    steps = s.get("steps") or sumry.get("steps") or ""
    dist = round((s.get("distance") or sumry.get("distance", 0)) / 1000, 2)
    cals = s.get("calories") or sumry.get("calories") or ""
    r_hr = s.get("restingHeartRate") or sumry.get("restingHeartRate") or ""
    curr_bb = s.get("bodyBatteryMostRecentValue") or ""
    daily_row = [today_date, steps, dist, cals, r_hr, curr_bb]
except: daily_row = [today_date, "", "", "", "", ""]

# --- 2. MORNING (Пиковая батарейка и время) ---
try:
    bb_data = gar.get_body_battery(today_date)
    morning_bb = ""
    if bb_data:
        # Берем максимум батарейки в период с 00:00 до 09:00
        morning_values = [i['value'] for i in bb_data if int(i['timeOffsetInSeconds']) < 32400]
        morning_bb = max(morning_values) if morning_values else daily_row[5]

    w_comp = gar.get_body_composition(today_date, today_date)
    weight = round(w_comp['uploads'][-1].get('weight', 0) / 1000, 1) if w_comp.get('uploads') else ""
    
    h_data = gar.get_hrv_data(today_date) or gar.get_hrv_data(yesterday)
    hrv = h_data[0].get("lastNightAvg", "") if isinstance(h_data, list) and h_data else ""
    
    sl = gar.get_sleep_data(today_date)
    d = sl.get("dailySleepDTO") or {}
    slp_sc = d.get("sleepScore", "")
    slp_h = round(d.get("sleepTimeSeconds", 0)/3600, 1) if d.get("sleepTimeSeconds") else ""
    
    morning_row = [morning_ts, weight, r_hr, hrv, morning_bb, slp_sc, slp_h]
except: morning_row = [morning_ts, "", "", "", "", "", ""]

# --- 3. ACTIVITIES (Сортировка по времени и округление) ---
activities_to_log = []
try:
    acts = gar.get_activities_by_date(today_date, today_date)
    # Сортируем: силовая (раньше) -> вело (позже)
    acts.sort(key=lambda x: x.get('startTimeLocal', ''))
    
    for a in acts:
        raw_start = a.get('startTimeLocal', today_date)
        st_time = raw_start.split('T')[1][:5] if 'T' in raw_start else "00:00"
        avg_hr = a.get('averageHR', '')
        
        activities_to_log.append([
            today_date, st_time, a.get('activityType', {}).get('typeKey', ''),
            round(a.get('duration', 0) / 3600, 2),
            round(a.get('distance', 0) / 1000, 2),
            avg_hr, a.get('maxHR', ''), a.get('trainingLoad', ''),
            round(float(a.get('aerobicTrainingEffect', 0)), 1), # Округление до 0.1
            a.get('calories', ''), a.get('avgPower', ''), a.get('averageCadence', ''),
            calculate_intensity(avg_hr, r_hr)
        ])
except: pass

# --- 4. AI (Исправление ошибки 404 / Модель) ---
advice = "No advice"
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY.strip())
        # Авто-выбор первой доступной модели
        model_list = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        m_name = "models/gemini-1.5-flash" if "models/gemini-1.5-flash" in model_list else model_list[0]
        
        gen_model = genai.GenerativeModel(m_name)
        prompt = (f"Данные {today_date}: Сон {slp_h}ч, HRV {hrv}, Пик батарейки {morning_bb}, "
                  f"Пульс {r_hr}. Тренировок: {len(activities_to_log)}. Дай совет.")
        advice = gen_model.generate_content(prompt).text.strip()
    except Exception as e:
        advice = f"AI Status: Error choosing model ({str(e)[:30]})"

# --- 5. SYNC ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    
    # Activities
    act_sheet = ss.worksheet("Activities")
    existing = [f"{r[0]}_{r[1]}" for r in act_sheet.get_all_values()]
    for act in activities_to_log:
        if f"{act[0]}_{act[1]}" not in existing:
            act_sheet.append_row(act)

    update_or_append(ss.worksheet("Daily"), today_date, daily_row)
    update_or_append(ss.worksheet("Morning"), today_date, morning_row)
    
    # AI Log
    ss.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Final Sync", advice])
    print(f"✔ Готово. Утренняя батарейка: {morning_bb}, AI: {m_name}")
except Exception as e: print(f"Error: {e}")
