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
                if val not in (None, "", 0, "0", 0.0): 
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

# Инициализация переменных для ИИ
steps, dist, cals, r_hr, bb_morning, slp_h, weight, hrv = 0, 0, 0, 0, 0, 0, 0, 0

# --- 1. MORNING BLOCK ---
morning_ts = f"{today_str} 08:00"
try:
    sl = gar.get_sleep_data(today_str)
    s_dto = sl.get("dailySleepDTO") or {}
    wake_time = s_dto.get("sleepEndTimeLocal")
    if wake_time:
        morning_ts = wake_time.replace("T", " ")[:16]
    
    slp_h = round(s_dto.get("sleepTimeSeconds", 0)/3600, 1) if s_dto.get("sleepTimeSeconds") else 0
    slp_sc = s_dto.get("sleepScore", 0)

    h_data = gar.get_hrv_data(today_str)
    hrv = h_data[0].get("lastNightAvg", 0) if h_data and isinstance(h_data, list) else 0

    w_comp = gar.get_body_composition(three_days_ago, today_str)
    if w_comp.get('uploads'):
        weight = round(w_comp['uploads'][-1].get('weight', 0) / 1000, 1)

    summary = gar.get_user_summary(today_str) or {}
    bb_morning = summary.get("bodyBatteryHighestValue", 0)
    r_hr = summary.get("restingHeartRate", 0)

    morning_row = [morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h]
except:
    morning_row = [morning_ts, 0, 0, 0, 0, 0, 0]

# --- 2. DAILY BLOCK ---
try:
    step_data = gar.get_daily_steps(today_str, today_str)
    steps = step_data[0].get('totalSteps', 0) if step_data else 0
    dist = round(step_data[0].get('totalDistance', 0) / 1000, 2) if step_data else 0
    cals = (summary.get("activeCalories", 0) or 0) + (summary.get("bmrCalories", 0) or 0)
    
    daily_row = [today_str, steps, dist, cals, r_hr, summary.get("bodyBatteryMostRecentValue", 0)]
except:
    daily_row = [today_str, 0, 0, 0, 0, 0]

# --- 3. ACTIVITIES ---
activities_to_log = []
try:
    acts = gar.get_activities_by_date(today_str, today_str)
    acts.sort(key=lambda x: x.get('startTimeLocal', ''))
    for a in acts:
        st_time = a.get('startTimeLocal', "")[11:16]
        cad = a.get('averageBikingCadence') or a.get('averageCadence') or ""
        avg_hr = a.get('averageHR', 0)
        
        intensity = "N/A"
        if avg_hr and r_hr > 0:
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

    # --- AI SECTION ---
    advice = "AI Skip"
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            if models:
                model = genai.GenerativeModel(models[0])
                prompt = (f"Анализ данных за {today_str}: Вес {weight}кг, HRV {hrv}, Сон {slp_h}ч, "
                          f"Шаги {steps}, Ккал {cals}, Body Battery утром {bb_morning}. "
                          f"Тренировок сегодня: {len(activities_to_log)}. "
                          f"Дай краткий профессиональный совет по восстановлению или нагрузке на завтра.")
                response = model.generate_content(prompt)
                advice = response.text.strip()
        except Exception as e:
            if "429" in str(e): advice = "AI Limit Reached"
            else: advice = f"AI Error: {str(e)[:30]}"
    
    ss.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Success", advice])
    print(f"✔ Готово. AI: {advice[:50]}...")

except Exception as e:
    print(f"❌ Ошибка: {e}")
