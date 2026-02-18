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
                if val not in (None, "", 0, "0", 0.0, "N/A"): 
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
three_days_ago = (now - timedelta(days=3)).strftime("%Y-%m-%d")

# --- 1. MORNING BLOCK (Weight, HRV, Sleep Score) ---
morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h = f"{today_str} 08:00", "", "", "", "", "", ""

try:
    # Вес (пробуем достать через body_composition)
    w_data = gar.get_body_composition(three_days_ago, today_str)
    if w_data.get('uploads'):
        weight = round(w_data['uploads'][-1].get('weight', 0) / 1000, 1)

    # Сон и Sleep Score
    sl = gar.get_sleep_data(today_str)
    s_dto = sl.get("dailySleepDTO") or {}
    slp_sc = s_dto.get("sleepScore", "") # Тот самый Score
    slp_h = round(s_dto.get("sleepTimeSeconds", 0)/3600, 1) if s_dto.get("sleepTimeSeconds") else ""
    if s_dto.get("sleepEndTimeLocal"):
        morning_ts = s_dto.get("sleepEndTimeLocal").replace("T", " ")[:16]

    # HRV
    hrv_res = gar.get_hrv_data(today_str)
    if hrv_res:
        # HRV может быть списком или словарем
        hrv = hrv_res[0].get("lastNightAvg", "") if isinstance(hrv_res, list) else hrv_res.get("lastNightAvg", "")

    summary = gar.get_user_summary(today_str) or {}
    bb_morning = summary.get("bodyBatteryHighestValue", "")
    r_hr = summary.get("restingHeartRate", "")

    morning_row = [morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h]
except: morning_row = [morning_ts, "", "", "", "", "", ""]

# --- 2. DAILY BLOCK (Calories) ---
try:
    step_data = gar.get_daily_steps(today_str, today_str)
    steps = step_data[0].get('totalSteps', 0) if step_data else 0
    dist = round(step_data[0].get('totalDistance', 0) / 1000, 2) if step_data else 0
    
    # КАЛОРИИ: Берем total, если 0 - считаем сумму
    total_c = summary.get("totalCalories", 0)
    if total_c == 0:
        total_c = (summary.get("activeCalories", 0) or 0) + (summary.get("bmrCalories", 0) or 0)
    cals = total_c if total_c > 0 else ""
    
    daily_row = [today_str, steps, dist, cals, r_hr, summary.get("bodyBatteryMostRecentValue", "")]
except: daily_row = [today_str, "", "", "", "", ""]

# --- 3. ACTIVITIES (Load & Cadence) ---
activities_to_log = []
try:
    acts = gar.get_activities_by_date(today_str, today_str)
    acts.sort(key=lambda x: x.get('startTimeLocal', ''))
    for a in acts:
        st_time = a.get('startTimeLocal', "")[11:16]
        
        # КАДЕНС: проверяем все возможные поля для ANT+ датчиков
        cad = (a.get('averageBikingCadence') or 
               a.get('averageCadence') or 
               a.get('averageRunCadence') or 
               a.get('maxCadence', ""))
        
        # НАГРУЗКА (Training Load)
        t_load = a.get('trainingLoad', "")
        
        avg_hr = a.get('averageHR', 0)
        intensity = "N/A"
        if avg_hr and r_hr and r_hr > 0:
            res = (float(avg_hr) - float(r_hr)) / (185 - float(r_hr))
            intensity = "Low" if res < 0.5 else ("Moderate" if res < 0.75 else "High")

        activities_to_log.append([
            today_str, st_time, a.get('activityType', {}).get('typeKey', ''),
            round(a.get('duration', 0) / 3600, 2), round(a.get('distance', 0) / 1000, 2),
            avg_hr, a.get('maxHR', ""), t_load,
            round(float(a.get('aerobicTrainingEffect', 0)), 1), a.get('calories', ""),
            a.get('avgPower', ""), cad, intensity
        ])
except: pass

# --- 4. SYNC & AI ---
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

    # AI Section (с защитой от Quota)
    advice = "AI Limit"
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            model = genai.GenerativeModel('gemini-1.5-flash')
            prompt = f"Данные: HRV {hrv}, Сон {slp_h}ч (Score: {slp_sc}), Ккал {cals}. Дай совет."
            advice = model.generate_content(prompt).text.strip()
        except: pass
    
    ss.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Success", advice])
    print(f"✔ Обновлено. Load: {t_load if activities_to_log else 'N/A'}, Score: {slp_sc}")

except Exception as e: print(f"❌ Error: {e}")
