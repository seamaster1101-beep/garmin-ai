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
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# --- 1. MORNING BLOCK (ГЛУБОКИЙ ПОИСК) ---
morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h = f"{today_str} 08:00", "", "", "", "", "", ""

try:
    # Ищем HRV (Пробуем 3 разных метода)
    wellness = gar.get_stats(today_str) or {}
    hrv = wellness.get("allDayAvgHrv") or wellness.get("lastNightAvgHrv")
    if not hrv:
        hrv_data = gar.get_hrv_data(today_str)
        if hrv_data: hrv = hrv_data[0].get("lastNightAvg")
    
    # Ищем СОН (Смотрим и вчера, и сегодня)
    for target_date in [today_str, yesterday_str]:
        sleep_raw = gar.get_sleep_data(target_date)
        s_dto = sleep_raw.get("dailySleepDTO") or {}
        # Если нашли оценку сна и она относится к сегодняшнему утру
        if s_dto.get("sleepScore") and (s_dto.get("sleepEndTimeLocal")[:10] == today_str):
            slp_sc = s_dto.get("sleepScore")
            slp_h = round(s_dto.get("sleepTimeSeconds", 0)/3600, 1)
            morning_ts = s_dto.get("sleepEndTimeLocal").replace("T", " ")[:16]
            break

    # Вес
    w_comp = gar.get_body_composition(yesterday_str, today_str)
    if w_comp.get('uploads'): weight = round(w_comp['uploads'][-1].get('weight', 0) / 1000, 1)

    summary = gar.get_user_summary(today_str) or {}
    r_hr = summary.get("restingHeartRate", "")
    bb_morning = summary.get("bodyBatteryHighestValue", "")

    morning_row = [morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h]
except Exception as e:
    print(f"Morning Error: {e}")
    morning_row = [morning_ts, "", "", "", "", "", ""]

# --- 2. DAILY BLOCK ---
try:
    step_data = gar.get_daily_steps(today_str, today_str)
    steps = step_data[0].get('totalSteps', 0) if step_data else 0
    dist = round(step_data[0].get('totalDistance', 0) / 1000, 2) if step_data else 0
    cals = wellness.get("calories") or (summary.get("activeCalories", 0) + summary.get("bmrCalories", 0))
    daily_row = [today_str, steps, dist, cals, r_hr, summary.get("bodyBatteryMostRecentValue", "")]
except: daily_row = [today_str, "", "", "", "", ""]

# --- 3. SYNC & AI ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)

    # AI Section (Снимаем ограничение slp_sc, чтобы он анализировал то, что есть)
    advice = "AI Limit"
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            model = genai.GenerativeModel('gemini-1.5-flash')
            prompt = f"Данные {today_str}: HRV {hrv}, Сон {slp_h}ч (Score: {slp_sc}), Вес {weight}, Пульс {r_hr}, Body Battery {bb_morning}. Проанализируй и дай совет."
            advice = model.generate_content(prompt).text.strip()
        except Exception as e: advice = f"AI Error: {str(e)[:20]}"
    
    ss.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Success", advice])
    print(f"✔ Готово. HRV: {hrv}, Score: {slp_sc}, AI: {advice[:30]}...")

except Exception as e: print(f"❌ Error: {e}")
