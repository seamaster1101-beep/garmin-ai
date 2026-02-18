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

def update_or_append(sheet, date_str, row_data, check_morning=False):
    """Универсальная функция обновления: для Daily по дате, для Morning с сохранением времени."""
    try:
        col_values = sheet.col_values(1)
        search_date = date_str.split(' ')[0]
        found_idx = -1
        for i, val in enumerate(col_values):
            if search_date in val:
                found_idx = i + 1
                break
        
        if found_idx != -1:
            # Обновляем ячейки со 2-й колонки (B, C...)
            start_col = 2
            for i, val in enumerate(row_data[1:], start=start_col):
                if val != "": sheet.update_cell(found_idx, i, val)
            return "Updated"
        else:
            sheet.append_row(row_data)
            return "Appended"
    except Exception as e: return f"Err: {str(e)[:15]}"

# --- LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
except: print("Garmin Login Fail"); exit(1)

now = datetime.now()
today_date = now.strftime("%Y-%m-%d")
current_ts = now.strftime("%Y-%m-%d %H:%M")
yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# --- 1. DAILY (Сбор данных из двух источников) ---
try:
    s = gar.get_stats(today_date) or {}
    sm = gar.get_user_summary(today_date) or {}
    r_hr = s.get("restingHeartRate") or sm.get("restingHeartRate") or ""
    
    daily_row = [
        today_date, 
        s.get("steps") or sm.get("steps") or "", 
        round((s.get("distance", 0) or sm.get("distance", 0)) / 1000, 2),
        s.get("calories") or sm.get("calories") or "", 
        r_hr, 
        s.get("bodyBatteryMostRecentValue") or ""
    ]
except: daily_row = [today_date, "", "", "", "", ""]

# --- 2. MORNING (HRV, Сон, Вес, Пиковая Батарейка) ---
try:
    w_comp = gar.get_body_composition(yesterday, today_date)
    weight = round(w_comp['uploads'][-1].get('weight', 0) / 1000, 1) if w_comp and w_comp.get('uploads') else ""
    
    h_data = gar.get_hrv_data(today_date) or gar.get_hrv_data(yesterday)
    hrv = h_data[0].get("lastNightAvg", "") if isinstance(h_data, list) and h_data else ""
    
    sl = gar.get_sleep_data(today_date)
    d = sl.get("dailySleepDTO") or {}
    slp_sc = d.get("sleepScore", "")
    slp_h = round(d.get("sleepTimeSeconds", 0)/3600, 1) if d.get("sleepTimeSeconds") else ""
    
    bb_full = gar.get_body_battery(today_date)
    m_bb = max([i['value'] for i in bb_full if int(i['timeOffsetInSeconds']) < 36000]) if bb_full else daily_row[5]
    
    morning_row = [current_ts, weight, r_hr, hrv, m_bb, slp_sc, slp_h]
except: morning_row = [current_ts, "", "", "", "", "", ""]

# --- 3. ACTIVITIES (Хронология, Время, Округление) ---
activities_to_log = []
try:
    acts = gar.get_activities_by_date(today_date, today_date)
    # Сортировка: сначала ранние тренировки
    acts.sort(key=lambda x: x.get('startTimeLocal', ''))
    
    for a in acts:
        raw_start = a.get('startTimeLocal', "")
        st_time = raw_start.split('T')[1][:5] if 'T' in raw_start else "00:00"
        avg_hr = a.get('averageHR', '')
        
        activities_to_log.append([
            today_date,                                   # A
            st_time,                                      # B
            a.get('activityType', {}).get('typeKey', ''),  # C
            round(a.get('duration', 0) / 3600, 2),        # D
            round(a.get('distance', 0) / 1000, 2),        # E
            avg_hr,                                       # F
            a.get('maxHR', ''),                           # G
            a.get('trainingLoad', ''),                    # H
            round(float(a.get('aerobicTrainingEffect', 0)), 1), # I
            a.get('calories', ''),                        # J
            a.get('avgPower', ''),                        # K
            a.get('averageCadence', ''),                  # L
            calculate_intensity(avg_hr, r_hr)             # M
        ])
except: pass

# ---
