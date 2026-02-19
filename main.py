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
except Exception as e:
    print(f"Login Fail: {e}"); exit(1)

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# --- 1. MORNING BLOCK ---
morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h = f"{today_str} 08:00", "", "", "", "", "", ""

try:
    stats = gar.get_stats(today_str) or {}
    # HRV
    hrv = stats.get("allDayAvgHrv") or stats.get("lastNightAvgHrv") or stats.get("lastNightHrv")
    
    # Сон (проверка за 2 дня)
    for d in [today_str, yesterday_str]:
        sleep_data = gar.get_sleep_data(d)
        dto = sleep_data.get("dailySleepDTO", {})
        if dto and dto.get("sleepTimeSeconds", 0) > 0:
            slp_sc = dto.get("sleepScore") or sleep_data.get("sleepScore", "")
            slp_h = round(dto.get("sleepTimeSeconds", 0) / 3600, 1)
            morning_ts = dto.get("sleepEndTimeLocal", "").replace("T", " ")[:16] or morning_ts
            break

    # Вес (проверка за 3 дня)
    for i in range(3):
        d_check = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            w_data = gar.get_body_composition(d_check, today_str)
            if w_data.get('uploads'):
                weight = round(w_data['uploads'][-1].get('weight', 0) / 1000, 1)
                break
        except: continue

    summary = gar.get_user_summary(today_str) or {}
    r_hr = summary.get("restingHeartRate") or summary.get("heartRateRestingValue", "")
    bb_morning = summary.get("bodyBatteryHighestValue", "")

    morning_row = [morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h]
except Exception as e:
    print(f"Morning Error: {e}")
    morning_row = [morning_ts, "", "", "", "", "", ""]

# --- 2. DAILY BLOCK ---
try:
    steps_data = gar.get_daily_steps(today_str, today_str)
    steps = steps_data[0].get('totalSteps', 0) if steps_data else 0
    cals = stats.get("calories") or (summary.get("activeCalories", 0) + summary.get("bmrCalories", 0))
    daily_row = [today_str, steps, "", cals, r_hr, summary.get("bodyBatteryMostRecentValue", "")]
except:
    daily_row = [today_str, "", "", "", "", ""]

# --- 3. SYNC & AI ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)

    advice = "Нет данных для ИИ"
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            # Стабильное название модели без лишних версий
            model = genai.GenerativeModel('gemini-1.5-flash')
            prompt = (f"Данные: HRV {hrv}, Пульс {r_hr}, Body Battery {bb_morning}, "
                      f"Сон {slp_h}ч (Score: {slp_sc}). Напиши один короткий, ироничный и полезный совет.")
            res = model.generate_content(prompt)
            advice = res.text.strip()
        except Exception as ai_err:
            advice = f"AI Error: {str(ai_err)[:30]}"
    
    ss.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Success", advice])
    print(f"✔ Завершено. HRV: {hrv}, AI: {advice[:40]}...")
except Exception as e:
    print(f"Final Error: {e}")
