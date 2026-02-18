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
except: print("Garmin Login Fail"); exit(1)

now = datetime.now()
today_date = now.strftime("%Y-%m-%d")
current_ts = now.strftime("%Y-%m-%d %H:%M")
yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# --- 1. DAILY (Суммирование калорий) ---
try:
    sm = gar.get_user_summary(today_date) or {}
    steps = sm.get("totalSteps") or sm.get("steps") or ""
    raw_dist = sm.get("totalDistanceMeters") or sm.get("distance", 0)
    dist = round(float(raw_dist) / 1000, 2) if raw_dist else ""
    
    # Калории
    total_cals = sm.get("totalCalories") or sm.get("calories")
    if not total_cals:
        total_cals = (sm.get("activeCalories", 0) or 0) + (sm.get("bmrCalories", 0) or 0)
    cals = total_cals if total_cals > 0 else ""
    
    r_hr = sm.get("restingHeartRate") or ""
    bb = sm.get("bodyBatteryMostRecentValue") or ""
    daily_row = [today_date, steps, dist, cals, r_hr, bb]
except: daily_row = [today_date, "", "", "", "", ""]

# --- 2. MORNING (HRV, Сон, Вес, Макс Батарейка) ---
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
    m_bb = max([i['value'] for i in bb_full if int(i['timeOffsetInSeconds']) < 36000]) if bb_full else bb
    morning_row = [current_ts, weight, r_hr, hrv, m_bb, slp_sc, slp_h]
except: morning_row = [current_ts, "", "", "", "", "", ""]

# --- 3. ACTIVITIES ---
activities_to_log = []
try:
    acts = gar.get_activities_by_date(today_date, today_date)
    acts.sort(key=lambda x: x.get('startTimeLocal', ''))
    for a in acts:
        raw_start = a.get('startTimeLocal', "")
        st_time = raw_start.split('T')[1][:5] if 'T' in raw_start else (raw_start.split(' ')[1][:5] if ' ' in raw_start else "00:00")
        avg_hr = a.get('averageHR', '')
        activities_to_log.append([
            today_date, st_time, a.get('activityType', {}).get('typeKey', ''),
            round(a.get('duration', 0) / 3600, 2), round(a.get('distance', 0) / 1000, 2),
            avg_hr, a.get('maxHR', ''), a.get('trainingLoad', ''),
            round(float(a.get('aerobicTrainingEffect', 0)), 1), a.get('calories', ''),
            a.get('avgPower', ''), a.get('averageCadence', ''), calculate_intensity(avg_hr, r_hr)
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
    all_acts = act_sheet.get_all_values()
    existing_keys = [f"{r[0]}_{r[1]}_{r[2]}" for r in all_acts if len(r) > 2]
    for act in activities_to_log:
        if f"{act[0]}_{act[1]}_{act[2]}" not in existing_keys: act_sheet.append_row(act)

    # --- AI ---
    advice = "AI analysis skipped"
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            m_list = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            model = genai.GenerativeModel(m_list[0])
            prompt = f"Данные: Шаги {steps}, Сон {slp_h}ч, Калории {cals}. Дай совет."
            advice = model.generate_content(prompt).text.strip()
        except: pass
    
    ss.worksheet("AI_Log").append_row([current_ts, "Success", advice])
    print(f"✔ Завершено. Калории: {cals}")
except Exception as e: print(f"❌ Ошибка: {e}")
