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

def update_or_append_morning(sheet, date_str, row_data):
    """Специальная функция для Morning: сохраняет время ПЕРВОЙ синхронизации"""
    try:
        col_values = sheet.col_values(1)
        today_only = date_str.split(' ')[0]
        found_idx = -1
        for i, val in enumerate(col_values):
            if today_only in val:
                found_idx = i + 1
                break
        
        if found_idx != -1:
            # Если нашли, обновляем всё КРОМЕ даты/времени (ячейка 1)
            for i, val in enumerate(row_data[1:], start=2):
                if val != "": sheet.update_cell(found_idx, i, val)
            return "Updated"
        else:
            sheet.append_row(row_data)
            return "Appended"
    except Exception as e: return f"Err: {str(e)[:15]}"

def update_or_append_daily(sheet, date_str, row_data):
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
    except Exception as e: return f"Err: {str(e)[:15]}"

# --- LOGIN ---
gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
gar.login()

now = datetime.now()
today_date = now.strftime("%Y-%m-%d")
morning_ts = now.strftime("%Y-%m-%d %H:%M")
yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# --- 1. DAILY ---
try:
    s = gar.get_stats(today_date) or {}
    r_hr = s.get("restingHeartRate", "")
    daily_row = [
        today_date, 
        s.get("steps", ""), 
        round(s.get("distance", 0)/1000, 2) if s.get("distance") else "",
        s.get("calories", ""), 
        r_hr, 
        s.get("bodyBatteryMostRecentValue", "")
    ]
except: daily_row = [today_date, "", "", "", "", ""]

# --- 2. MORNING ---
try:
    # Батарейка (макс до 10 утра)
    bb_data = gar.get_body_battery(today_date)
    morning_bb = ""
    if bb_data:
        morning_values = [i['value'] for i in bb_data if int(i['timeOffsetInSeconds']) < 36000]
        morning_bb = max(morning_values) if morning_values else daily_row[5]

    # Вес
    w_comp = gar.get_body_composition(today_date, today_date) or gar.get_body_composition(yesterday, today_date)
    weight = ""
    if w_comp and w_comp.get('uploads'):
        weight = round(w_comp['uploads'][-1].get('weight', 0) / 1000, 1)

    # HRV
    h_data = gar.get_hrv_data(today_date) or gar.get_hrv_data(yesterday)
    hrv = h_data[0].get("lastNightAvg", "") if isinstance(h_data, list) and h_data else ""
    
    # Сон
    sl = gar.get_sleep_data(today_date)
    d = sl.get("dailySleepDTO") or {}
    slp_sc = d.get("sleepScore", "")
    slp_h = round(d.get("sleepTimeSeconds", 0)/3600, 1) if d.get("sleepTimeSeconds") else ""
    
    morning_row = [morning_ts, weight, r_hr, hrv, morning_bb, slp_sc, slp_h]
except: morning_row = [morning_ts, "", "", "", "", "", ""]

# --- 3. ACTIVITIES (Исправление Start_Time) ---
activities_to_log = []
try:
    acts = gar.get_activities_by_date(today_date, today_date)
    acts.sort(key=lambda x: x.get('startTimeLocal', ''))
    
    for a in acts:
        # Пытаемся достать время старта из разных полей
        raw_start = a.get('startTimeLocal') or a.get('startTimeGMT') or ""
        st_time = "00:00"
        if "T" in raw_start:
            st_time = raw_start.split("T")[1][:5]
        elif " " in raw_start:
            st_time = raw_start.split(" ")[1][:5]
            
        avg_hr = a.get('averageHR', '')
        activities_to_log.append([
            today_date, st_time, a.get('activityType', {}).get('typeKey', ''),
            round(a.get('duration', 0) / 3600, 2),
            round(a.get('distance', 0) / 1000, 2),
            avg_hr, a.get('maxHR', ''), a.get('trainingLoad', ''),
            round(float(a.get('aerobicTrainingEffect', 0)), 1),
            a.get('calories', ''), a.get('avgPower', ''), a.get('averageCadence', ''),
            calculate_intensity(avg_hr, r_hr)
        ])
except: pass

# --- 4. SYNC ---
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

    # Daily & Morning (с защитой времени)
    update_or_append_daily(ss.worksheet("Daily"), today_date, daily_row)
    update_or_append_morning(ss.worksheet("Morning"), morning_ts, morning_row)
    
    print(f"✔ Готово. Утренняя батарейка: {morning_bb}")
except Exception as e: print(f"Error: {e}")
