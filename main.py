import os
import json
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials

# --- CONFIG ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
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
                if val not in (None, "", 0, "0"): 
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
today_str = now.strftime("%Y-%m-%d")
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# --- 1. MORNING BLOCK ---
try:
    sl = gar.get_sleep_data(today_str)
    s_dto = sl.get("dailySleepDTO") or {}
    
    # Пытаемся достать ЛОКАЛЬНОЕ время пробуждения
    raw_wake = s_dto.get("sleepEndTimeLocal")
    if raw_wake:
        # Превращаем "2026-02-18T07:15:00.0" в "2026-02-18 07:15"
        morning_ts = raw_wake.replace("T", " ")[:16]
    else:
        # Если данных о сне нет, ставим сегодняшнее число и 08:00
        morning_ts = f"{today_str} 08:00"

    slp_h = round(s_dto.get("sleepTimeSeconds", 0)/3600, 1) if s_dto.get("sleepTimeSeconds") else ""
    slp_sc = s_dto.get("sleepScore", "")
    
    # HRV за последнюю ночь
    hrv_data = gar.get_hrv_data(today_str)
    hrv = hrv_data[0].get("lastNightAvg", "") if hrv_data else ""
    
    # Вес (сегодня или вчера)
    w_comp = gar.get_body_composition(today_str)
    if not w_comp.get('uploads'): w_comp = gar.get_body_composition(yesterday_str)
    weight = round(w_comp['uploads'][-1].get('weight', 0) / 1000, 1) if w_comp.get('uploads') else ""
    
    summary = gar.get_user_summary(today_str) or {}
    bb_morning = summary.get("bodyBatteryHighestValue", "")
    r_hr = summary.get("restingHeartRate", "")

    morning_row = [morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h]
except:
    morning_row = [f"{today_str} 08:00", "", "", "", "", "", ""]

# --- 2. DAILY BLOCK ---
try:
    step_data = gar.get_daily_steps(today_str, today_str)
    steps = step_data[0].get('totalSteps', "") if step_data else ""
    dist = round(step_data[0].get('totalDistance', 0) / 1000, 2) if step_data else ""
    cals = (summary.get("activeCalories", 0) or 0) + (summary.get("bmrCalories", 0) or 0)
    
    daily_row = [today_str, steps, dist, cals, r_hr, summary.get("bodyBatteryMostRecentValue", "")]
except:
    daily_row = [today_str, "", "", "", "", ""]

# --- 3. ACTIVITIES ---
activities_to_log = []
try:
    acts = gar.get_activities_by_date(today_str, today_str)
    acts.sort(key=lambda x: x.get('startTimeLocal', ''))
    for a in acts:
        st_time = a.get('startTimeLocal', "")[11:16]
        # Расширенный поиск каденса (вело датчики)
        cad = a.get('averageBikingCadence') or a.get('averageCadence') or a.get('averageRunCadence') or ""
        avg_hr = a.get('averageHR', 0)
        
        intensity = "N/A"
        if avg_hr and r_hr:
            res = (float(avg_hr) - float(r_hr)) / (185 - float(r_hr))
            intensity = "Low" if res < 0.5 else ("Moderate" if res < 0.75 else "High")

        activities_to_log.append([
            today_str, st_time, a.get('activityType', {}).get('typeKey', ''),
            round(a.get('duration', 0) / 3600, 2), round(a.get('distance', 0) / 1000, 2),
            avg_hr, a.get('maxHR', ""), a.get('trainingLoad', ""),
            round(float(a.get('aerobicTrainingEffect', 0)), 1), a.get('calories', ""),
            a.get('avgPower', ""), cad, intensity
        ])
except: pass

# --- 4. SYNC ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)
    
    act_sheet = ss.worksheet("Activities")
    existing = [f"{r[0]}_{r[1]}_{r[2]}" for r in act_sheet.get_all_values() if len(r) > 2]
    for act in activities_to_log:
        if f"{act[0]}_{act[1]}_{act[2]}" not in existing:
            act_sheet.append_row(act)
    
    print(f"✔ Готово. Время Morning: {morning_ts}")
except Exception as e:
    print(f"❌ Ошибка: {e}")
