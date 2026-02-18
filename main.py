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

# Начальные значения
steps, dist, cals, r_hr, bb, slp_h, weight, hrv = 0, 0, 0, 0, 0, 0, 0, 0

# --- 1. DAILY (Возвращаем рабочую дистанцию и ищем калории) ---
try:
    # Дистанция и шаги (самый точный метод для твоего аккаунта)
    step_data = gar.get_daily_steps(today_date, today_date)
    if step_data:
        steps = step_data[0].get('totalSteps', 0)
        dist = round(step_data[0].get('totalDistance', 0) / 1000, 2)

    # Калории и пульс
    summary = gar.get_user_summary(today_date) or {}
    r_hr = summary.get("restingHeartRate") or 0
    bb = summary.get("bodyBatteryMostRecentValue") or 0
    
    # Пытаемся найти калории (Active + BMR)
    cals = (summary.get("activeCalories", 0) or 0) + (summary.get("bmrCalories", 0) or 0)
    
    # Если все еще 0, пробуем get_stats
    if cals < 100:
        st = gar.get_stats(today_date) or {}
        cals = st.get("calories", cals)

    daily_row = [today_date, steps, dist, cals, r_hr, bb]
except Exception as e:
    print(f"Daily Err: {e}")
    daily_row = [today_date, 0, 0, 0, 0, 0]

# --- 2. MORNING & SLEEP ---
try:
    sl = gar.get_sleep_data(today_date)
    d = sl.get("dailySleepDTO") or {}
    slp_h = round(d.get("sleepTimeSeconds", 0)/3600, 1) if d.get("sleepTimeSeconds") else 0
    slp_sc = d.get("sleepScore", 0)
    
    w_comp = gar.get_body_composition(yesterday, today_date)
    weight = round(w_comp['uploads'][-1].get('weight', 0) / 1000, 1) if w_comp and w_comp.get('uploads') else 0
        
    h_data = gar.get_hrv_data(today_date) or gar.get_hrv_data(yesterday)
    hrv = h_data[0].get("lastNightAvg", 0) if isinstance(h_data, list) and h_data else 0
    morning_row = [current_ts, weight, r_hr, hrv, bb, slp_sc, slp_h]
except:
    morning_row = [current_ts, 0, 0, 0, 0, 0, 0]

# --- 3. ACTIVITIES (Каденс и Интенсивность) ---
activities_to_log = []
try:
    acts = gar.get_activities_by_date(today_date, today_date)
    acts.sort(key=lambda x: x.get('startTimeLocal', ''))
    for a in acts:
        st_time = a.get('startTimeLocal', "")[11:16]
        sport = a.get('activityType', {}).get('typeKey', '')
        avg_hr = a.get('averageHR', 0)
        
        # Проверка всех полей каденса
        cad = a.get('averageBikingCadence') or a.get('averageCadence') or a.get('averageRunCadence') or ""
        
        # Расчет интенсивности
        intensity = "N/A"
        if avg_hr and r_hr:
            res = (float(avg_hr) - float(r_hr)) / (185 - float(r_hr))
            intensity = "Low" if res < 0.5 else ("Moderate" if res < 0.75 else "High")

        activities_to_log.append([
            today_date, st_time, sport,
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
        if f"{act[0]}_{act[1]}_{act[2]}" not in existing:
            act_sheet.append_row(act)

    # Безопасный вызов AI
    advice = "AI Quota Exceeded"
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            m_list = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            if m_list:
                model = genai.GenerativeModel(m_list[0])
                advice = model.generate_content(f"Шаги {steps}, Ккал {cals}. Совет в 1 предложении.").text.strip()
        except: pass
    
    ss.worksheet("AI_Log").append_row([current_ts, "Success", advice])
    print(f"✔ Синхронизация: Дистанция {dist} км, Калории {cals}")
except Exception as e: print(f"❌ Error: {e}")
