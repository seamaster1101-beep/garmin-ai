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

# ГАРАНТИРОВАННАЯ ИНИЦИАЛИЗАЦИЯ (чтобы ИИ не выдавал ошибку)
steps, dist, cals, r_hr, bb, slp_h, weight, hrv = 0, 0, 0, 0, 0, 0, 0, 0

# --- 1. DAILY (Сбор калорий и шагов) ---
try:
    stats = gar.get_stats(today_date) or {}
    steps = stats.get("totalSteps", 0)
    dist = round(stats.get("totalDistance", 0) / 1000, 2)
    r_hr = stats.get("restingHeartRate", 0)
    bb = stats.get("bodyBatteryMostRecentValue", 0)
    
    # КАЛОРИИ (двойная проверка)
    summary = gar.get_user_summary(today_date) or {}
    total_cals = summary.get("totalCalories", 0)
    if total_cals == 0:
        total_cals = (summary.get("activeCalories", 0) or 0) + (summary.get("bmrCalories", 0) or 0)
    cals = total_cals
    
    daily_row = [today_date, steps, dist, cals, r_hr, bb]
except: daily_row = [today_date, 0, 0, 0, 0, 0]

# --- 2. MORNING & SLEEP ---
try:
    sl = gar.get_sleep_data(today_date)
    d = sl.get("dailySleepDTO") or {}
    slp_h = round(d.get("sleepTimeSeconds", 0)/3600, 1) if d.get("sleepTimeSeconds") else 0
    slp_sc = d.get("sleepScore", 0)
    
    w_comp = gar.get_body_composition(yesterday, today_date)
    if w_comp and w_comp.get('uploads'):
        weight = round(w_comp['uploads'][-1].get('weight', 0) / 1000, 1)
        
    h_data = gar.get_hrv_data(today_date) or gar.get_hrv_data(yesterday)
    hrv = h_data[0].get("lastNightAvg", 0) if isinstance(h_data, list) and h_data else 0
    morning_row = [current_ts, weight, r_hr, hrv, bb, slp_sc, slp_h]
except: morning_row = [current_ts, 0, 0, 0, 0, 0, 0]

# --- 3. ACTIVITIES (Глубокий поиск Cadence для вело) ---
activities_to_log = []
try:
    acts = gar.get_activities_by_date(today_date, today_date)
    acts.sort(key=lambda x: x.get('startTimeLocal', ''))
    
    for a in acts:
        st_time = a.get('startTimeLocal', "")[11:16]
        sport = a.get('activityType', {}).get('typeKey', '')
        avg_hr = a.get('averageHR', 0)
        
        # Проверка каденса во всех возможных полях (включая вело-специфичные)
        cad = a.get('averageBikingCadence') or a.get('averageCadence') or a.get('averageRunCadence') or ""
        
        activities_to_log.append([
            today_date, st_time, sport,
            round(a.get('duration', 0) / 3600, 2), round(a.get('distance', 0) / 1000, 2),
            avg_hr, a.get('maxHR', 0), a.get('trainingLoad', 0),
            round(float(a.get('aerobicTrainingEffect', 0)), 1), a.get('calories', 0),
            a.get('avgPower', ""), cad, calculate_intensity(avg_hr, r_hr)
        ])
except: pass

# --- 4. GOOGLE SYNC & AI ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    
    update_or_append(ss.worksheet("Daily"), today_date, daily_row)
    update_or_append(ss.worksheet("Morning"), today_date, morning_row)
    
    # Запись Activities без дублей
    act_sheet = ss.worksheet("Activities")
    existing = [f"{r[0]}_{r[1]}_{r[2]}" for r in act_sheet.get_all_values() if len(r) > 2]
    for act in activities_to_log:
        if f"{act[0]}_{act[1]}_{act[2]}" not in existing:
            act_sheet.append_row(act)

    # Исправленный ИИ (динамический выбор модели)
    advice = "AI Skip"
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            if models:
                model = genai.GenerativeModel(models[0]) # Берем актуальную модель
                prompt = f"Данные {today_date}: Шаги {steps}, Ккал {cals}, Сон {slp_h}ч. Дай 1 короткий совет."
                advice = model.generate_content(prompt).text.strip()
        except Exception as e: advice = f"AI Error: {str(e)[:40]}"
    
    ss.worksheet("AI_Log").append_row([current_ts, "Success", advice])
    print(f"✔ Финальная синхронизация: Калории {cals}, Каденс найден.")

except Exception as e: print(f"❌ Error: {e}")
