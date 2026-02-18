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

def update_or_append_morning(sheet, date_str, row_data):
    try:
        col_values = sheet.col_values(1)
        today_date = date_str.split(' ')[0]
        found_idx = -1
        for i, val in enumerate(col_values):
            if today_date in val:
                found_idx = i + 1
                break
        
        if found_idx != -1:
            # Обновляем всё, КРОМЕ колонки A (сохраняем утреннее время)
            for i, val in enumerate(row_data[1:], start=2):
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

# --- 1. DAILY (Усиленный поиск: Stats + Summary) ---
try:
    s = gar.get_stats(today_date) or {}
    sm = gar.get_user_summary(today_date) or {} # Альтернативный источник
    
    steps = s.get("steps") or sm.get("steps") or ""
    dist = round((s.get("distance") or sm.get("distance", 0)) / 1000, 2)
    cals = s.get("calories") or sm.get("calories") or ""
    r_hr = s.get("restingHeartRate") or sm.get("restingHeartRate") or ""
    bb = s.get("bodyBatteryMostRecentValue") or ""
    
    daily_row = [today_date, steps, dist, cals, r_hr, bb]
except: daily_row = [today_date, "", "", "", "", ""]

# --- 2. MORNING (Сон, Вес, HRV) ---
try:
    # Вес (проверяем за 2 дня на случай UTC сдвига)
    w_comp = gar.get_body_composition(yesterday, today_date)
    weight = round(w_comp['uploads'][-1].get('weight', 0) / 1000, 1) if w_comp and w_comp.get('uploads') else ""
    
    # Сон и HRV
    sl = gar.get_sleep_data(today_date)
    d = sl.get("dailySleepDTO") or {}
    slp_sc = d.get("sleepScore", "")
    slp_h = round(d.get("sleepTimeSeconds", 0)/3600, 1) if d.get("sleepTimeSeconds") else ""
    
    h_data = gar.get_hrv_data(today_date) or gar.get_hrv_data(yesterday)
    hrv = h_data[0].get("lastNightAvg", "") if isinstance(h_data, list) and h_data else ""
    
    # Макс батарейка утра
    bb_full = gar.get_body_battery(today_date)
    morning_bb = max([i['value'] for i in bb_full if int(i['timeOffsetInSeconds']) < 36000]) if bb_full else bb

    morning_row = [current_ts, weight, r_hr, hrv, morning_bb, slp_sc, slp_h]
except: morning_row = [current_ts, "", "", "", "", "", ""]

# --- 1. ТРЕНИРОВКИ (Лист Activities) ---
activities_to_log = []
try:
    # Получаем данные о пульсе покоя для расчета интенсивности
    stats = gar.get_stats(today_date) or {}
    r_hr = stats.get("restingHeartRate", "")

    acts = gar.get_activities_by_date(today_date, today_date)
    # Сортируем строго по времени: сначала силовая, потом вело
    acts.sort(key=lambda x: x.get('startTimeLocal', ''))
    
    for a in acts:
        # 1. Время старта (извлекаем строго HH:MM из '2026-02-18T16:30:00.0')
        raw_start = a.get('startTimeLocal', "")
        st_time = raw_start.split('T')[1][:5] if 'T' in raw_start else "00:00"
        
        # 2. Интенсивность
        avg_hr = a.get('averageHR', '')
        intensity_label = calculate_intensity(avg_hr, r_hr) # Используем твою формулу
        
        activities_to_log.append([
            today_date,                                   # A: Date
            st_time,                                      # B: Start_Time
            a.get('activityType', {}).get('typeKey', ''),  # C: Sport
            round(a.get('duration', 0) / 3600, 2),        # D: Duration_hr
            round(a.get('distance', 0) / 1000, 2),        # E: Distance_km
            avg_hr,                                       # F: Avg_HR
            a.get('maxHR', ''),                           # G: Max_HR
            a.get('trainingLoad', ''),                    # H: Training_Load
            round(float(a.get('aerobicTrainingEffect', 0)), 1), # I: Training_Effect
            a.get('calories', ''),                        # J: Calories
            a.get('avgPower', ''),                        # K: Avg_Power
            a.get('averageCadence', ''),                  # L: Cadence
            intensity_label                               # M: HR_Intensity
        ])
except Exception as e:
    print(f"Ошибка сбора тренировок: {e}")

# --- 2. ЗАПИСЬ (С защитой от дублей) ---
try:
    act_sheet = ss.worksheet("Activities")
    # Читаем всю таблицу, чтобы проверить на наличие такой тренировки
    all_rows = act_sheet.get_all_values()
    
    for act in activities_to_log:
        # Уникальный ключ: "Дата_Время_Спорт" (например "2026-02-18_16:30_cycling")
        activity_key = f"{act[0]}_{act[1]}_{act[2]}"
        
        is_duplicate = False
        for row in all_rows:
            if len(row) >= 3:
                existing_key = f"{row[0]}_{row[1]}_{row[2]}"
                if activity_key == existing_key:
                    is_duplicate = True
                    break
        
        if not is_duplicate:
            act_sheet.append_row(act)
            print(f"Записана новая тренировка: {act[2]} в {act[1]}")
        else:
            print(f"Тренировка {act[2]} в {act[1]} уже есть, пропускаем.")
except Exception as e:
    print(f"Ошибка записи в Sheets: {e}")

# --- 4. AI & SYNC ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    
    # Записываем Morning (сохранит время первой синхронизации)
    update_or_append_morning(ss.worksheet("Morning"), current_ts, morning_row)
    
    # Записываем Daily
    update_or_append_morning(ss.worksheet("Daily"), today_date, daily_row)
    
    # Записываем Activities
    act_sheet = ss.worksheet("Activities")
    exist = [f"{r[0]}_{r[1]}" for r in act_sheet.get_all_values()]
    for act in activities:
        if f"{act[0]}_{act[1]}" not in exist: act_sheet.append_row(act)

    # AI Блок вынесен отдельно, чтобы не вешать скрипт
    advice = "AI could not generate advice"
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            # Подбор любой живой модели
            m_list = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            model = genai.GenerativeModel(m_list[0])
            prompt = f"Данные {today_date}: Шаги {steps}, Сон {slp_h}ч, Тренировки: {len(activities)}. Дай совет."
            advice = model.generate_content(prompt).text.strip()
        except: pass

    ss.worksheet("AI_Log").append_row([current_ts, "Sync Complete", advice])
    print(f"✔ Готово! Шаги: {steps}, Батарейка: {morning_bb}")

except Exception as e: print(f"Sheets Error: {e}")
