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
    except Exception as e: return f"Err: {str(e)[:15]}"

# --- LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
except: print("Fail login"); exit(1)

now = datetime.now()
today_date = now.strftime("%Y-%m-%d")
yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# --- 1. SLEEP & MORNING DATA (Смещаем фокус на утро) ---
morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h = "", 0, 0, 0, 0, 0, 0
try:
    sl = gar.get_sleep_data(today_date)
    d = sl.get("dailySleepDTO") or {}
    
    # Время пробуждения
    if d.get("sleepEndTimeGMT"):
        # Конвертируем из GMT в локальное (примерно)
        morning_ts = today_date + " 08:00" # Заглушка, если не выйдет вытянуть точно
        slp_end = d.get("sleepEndTimeLocal") or d.get("sleepEndTimeGMT")
        morning_ts = slp_end.replace("T", " ")[:16]
    
    slp_h = round(d.get("sleepTimeSeconds", 0)/3600, 1) if d.get("sleepTimeSeconds") else 0
    slp_sc = d.get("sleepScore", 0)
    
    # Body Battery на утро (берем значение из сна)
    bb_morning = d.get("awakeCount", 0) # Это не совсем то, ищем в summary
    summary = gar.get_user_summary(today_date) or {}
    bb_morning = summary.get("bodyBatteryHighestValue", 0) # Пиковое значение обычно утром
    r_hr = summary.get("restingHeartRate", 0)

    # Вес (проверяем сегодня и вчера)
    w_comp = gar.get_body_composition(today_date)
    if not w_comp.get('uploads'): w_comp = gar.get_body_composition(yesterday)
    if w_comp.get('uploads'):
        weight = round(w_comp['uploads'][-1].get('weight', 0) / 1000, 1)
        
    # HRV (ночное среднее)
    h_data = gar.get_hrv_data(today_date)
    if not h_data: h_data = gar.get_hrv_data(yesterday)
    hrv = h_data[0].get("lastNightAvg", 0) if isinstance(h_data, list) and h_data else 0
    
    morning_row = [morning_ts or (today_date + " 08:00"), weight, r_hr, hrv, bb_morning, slp_sc, slp_h]
except:
    morning_row = [today_date + " 08:00", 0, 0, 0, 0, 0, 0]

# --- 2. DAILY ---
try:
    step_data = gar.get_daily_steps(today_date, today_date)
    steps = step_data[0].get('totalSteps', 0) if step_data else 0
    dist = round(step_data[0].get('totalDistance', 0) / 1000, 2) if step_data else 0
    
    st = gar.get_stats(today_date) or {}
    cals = st.get("calories", (summary.get("activeCalories", 0) + summary.get("bmrCalories", 0)))
    
    daily_row = [today_date, steps, dist, cals, r_hr, summary.get("bodyBatteryMostRecentValue", 0)]
except:
    daily_row = [today_date, 0, 0, 0, 0, 0]

# --- 3. ACTIVITIES (Тройной поиск Cadence) ---
activities_to_log = []
try:
    acts = gar.get_activities_by_date(today_date, today_date)
    acts.sort(key=lambda x: x.get('startTimeLocal', ''))
    for a in acts:
        # Ищем каденс везде: в базе, в вело-поле, в беговом
        cad = (a.get('averageBikingCadence') or a.get('averageCadence') or 
               a.get('averageRunCadence') or a.get('metadata', {}).get('avgCadence', ""))
        
        avg_hr = a.get('averageHR', 0)
        intensity = "N/A"
        if avg_hr and r_hr > 0:
            res = (float(avg_hr) - float(r_hr)) / (185 - float(r_hr))
            intensity = "Low" if res < 0.5 else ("Moderate" if res < 0.75 else "High")

        activities_to_log.append([
            today_date, a.get('startTimeLocal', "")[11:16], a.get('activityType', {}).get('typeKey', ''),
            round(a.get('duration', 0) / 3600, 2), round(a.get('distance', 0) / 1000, 2),
            avg_hr, a.get('maxHR', 0), a.get('trainingLoad', 0),
            round(float(a.get('aerobicTrainingEffect', 0)), 1), a.get('calories', 0),
            a.get('avgPower', ""), cad, intensity
        ])
except: pass

# --- 4. SYNC ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    
    update_or_append(ss.worksheet("Daily"), today_date, daily_row)
    update_or_append(ss.worksheet("Morning"), today_date, morning_row)
    
    act_sheet = ss.worksheet("Activities")
    existing = [f"{r[0]}_{r[1]}_{r[2]}" for r in act_sheet.get_all_values() if len(r) > 2]
    for act in activities_to_log:
        if f"{act[0]}_{act[1]}_{act[2]}" not in existing: act_sheet.append_row(act)

    print(f"✔ Лист Morning: {morning_ts}, HRV: {hrv}, Ккал: {cals}")
except Exception as e: print(f"❌ Error: {e}")
