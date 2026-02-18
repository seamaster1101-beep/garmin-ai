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
current_ts = now.strftime("%Y-%m-%d %H:%M")
yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# Предварительная инициализация
steps, dist, cals, r_hr, bb, slp_h, weight, hrv = "", "", 0, "", "", "", "", ""

# --- 1. DAILY (С исправлением калорий) ---
try:
    # Шаги и дистанция через самый надежный метод
    step_data = gar.get_daily_steps(today_date, today_date)
    if step_data:
        steps = step_data[0].get('totalSteps', "")
        dist = round(step_data[0].get('totalDistance', 0) / 1000, 2)

    # Калории: пробуем вытащить хоть откуда-то
    summary = gar.get_user_summary(today_date) or {}
    total_cals = summary.get("totalCalories", 0)
    
    if not total_cals or total_cals == 0:
        # Если в сумме ноль, берем активные + BMR
        active = summary.get("activeCalories", 0) or 0
        bmr = summary.get("bmrCalories", 0) or 0
        total_cals = active + bmr
    
    # Если все еще ноль, пробуем метод get_stats
    if not total_cals or total_cals == 0:
        stats = gar.get_stats(today_date) or {}
        total_cals = stats.get("calories", 0)

    cals = total_cals if total_cals > 0 else ""
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
    slp_sc = d.get("sleepScore", "")
    
    w_comp = gar.get_body_composition(yesterday, today_date)
    if w_comp and w_comp.get('uploads'):
        weight = round(w_comp['uploads'][-1].get('weight', 0) / 1000, 1)
        
    h_data = gar.get_hrv_data(today_date) or gar.get_hrv_data(yesterday)
    hrv = h_data[0].get("lastNightAvg", "") if isinstance(h_data, list) and h_data else ""
    
    morning_row = [current_ts, weight, r_hr, hrv, bb, slp_sc, slp_h]
except:
    morning_row = [current_ts, "", "", "", "", "", ""]

# --- 3. ACTIVITIES (С защитой от дублей) ---
activities_to_log = []
try:
    acts = gar.get_activities_by_date(today_date, today_date)
    for a in acts:
        # Формируем данные
        st_time = a.get('startTimeLocal', "")[11:16] # HH:MM
        sport = a.get('activityType', {}).get('typeKey', '')
        
        activities_to_log.append([
            today_date, st_time, sport,
            round(a.get('duration', 0) / 3600, 2), round(a.get('distance', 0) / 1000, 2),
            a.get('averageHR', ''), a.get('maxHR', ''), a.get('trainingLoad', ''),
            round(float(a.get('aerobicTrainingEffect', 0)), 1), a.get('calories', ''),
            a.get('avgPower', ''), a.get('averageCadence', '')
        ])
except: pass

# --- 4. SYNC ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    
    # Запись Daily и Morning
    update_or_append(ss.worksheet("Daily"), today_date, daily_row)
    update_or_append(ss.worksheet("Morning"), today_date, morning_row)
    
    # Запись Активностей с проверкой на дубли
    act_sheet = ss.worksheet("Activities")
    existing_rows = act_sheet.get_all_values()
    # Создаем "ключ" для каждой существующей строки: Дата + Время + Спорт
    existing_keys = [f"{r[0]}_{r[1]}_{r[2]}" for r in existing_rows]

    for act in activities_to_log:
        key = f"{act[0]}_{act[1]}_{act[2]}"
        if key not in existing_keys:
            act_sheet.append_row(act)
            print(f"Добавлена тренировка: {key}")
        else:
            print(f"Пропуск дубликата: {key}")

    # AI Совет
    advice = "AI Skip"
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            model = genai.GenerativeModel('gemini-1.5-flash')
            prompt = f"Данные: Шаги {steps}, Ккал {cals}, Сон {slp_h}ч. Дай 1 совет."
            advice = model.generate_content(prompt).text.strip()
        except Exception as e: advice = f"AI Err: {e}"
    
    ss.worksheet("AI_Log").append_row([current_ts, "Success", advice])
    print(f"✔ Синхронизация завершена. Ккал: {cals}")

except Exception as e: print(f"❌ Ошибка записи: {e}")
