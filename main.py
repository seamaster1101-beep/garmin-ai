import os
import json
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

# ---------- CONFIG ----------
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")

def update_or_append(sheet, date_str, row_data):
    try:
        dates = sheet.col_values(1)
        if date_str in dates:
            row_num = dates.index(date_str) + 1
            for i, new_value in enumerate(row_data[1:], start=2):
                if new_value != "" and new_value is not None:
                    sheet.update_cell(row_num, i, new_value)
            return "Updated"
        else:
            sheet.append_row(row_data)
            return "Appended"
    except Exception as e:
        return f"Error: {e}"

# ---------- START ----------
client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
client.login()

now = datetime.now()
today_date = now.strftime("%Y-%m-%d")
yesterday_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
debug_log = []

# 1. Основные статы
stats = client.get_stats(today_date)
resting_hr = stats.get("restingHeartRate") or ""
body_battery = stats.get("bodyBatteryMostRecentValue") or ""

# 2. Вес (План А + План Б)
weight = ""
try:
    # План А: Состав тела
    w_data = client.get_body_composition(yesterday_date, today_date)
    if w_data and 'uploads' in w_data and w_data['uploads']:
        weight = round(w_data['uploads'][-1]['weight'] / 1000, 1)
        debug_log.append(f"W:Up({weight})")
    else:
        # План Б: Сводка пользователя (последний известный вес)
        summary = client.get_user_summary(today_date)
        weight = round(summary.get('weight', 0) / 1000, 1) if summary.get('weight') else ""
        if weight: debug_log.append(f"W:Sum({weight})")
except: debug_log.append("W:None")

# 3. HRV
hrv = ""
try:
    hrv_data = client.get_hrv_data(today_date) or client.get_hrv_data(yesterday_date)
    hrv = hrv_data[0].get('lastNightAvg', "") if hrv_data else ""
    debug_log.append(f"HRV:{hrv}" if hrv else "HRV:None")
except: debug_log.append("HRV:Err")

# 4. Сон
sleep_score, sleep_hours = "", ""
try:
    sleep = client.get_sleep_data(today_date)
    sleep_score = sleep.get('dailySleepDTO', {}).get('sleepScore', "")
    s_sec = sleep.get('dailySleepDTO', {}).get('sleepTimeSeconds', 0)
    if s_sec > 0: sleep_hours = round(s_sec / 3600, 1)
    debug_log.append(f"Slp:{sleep_hours}")
except: debug_log.append("Slp:Err")

# ---------- AI (Умный подбор модели) ----------
ai_advice = "No Advice"
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY.strip())
        # Пробуем найти любую доступную модель, если flash 1.5 недоступна
        models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        model_name = 'models/gemini-1.5-pro' if 'models/gemini-1.5-pro' in models else models[0]
        
        model = genai.GenerativeModel(model_name)
        prompt = f"Данные: Сон {sleep_hours}ч (Score {sleep_score}), HRV {hrv}, Пульс {resting_hr}. Дай совет на завтра (2 фразы)."
        response = model.generate_content(prompt)
        ai_advice = response.text.strip()
    except Exception as e:
        ai_advice = f"AI Error: {str(e)[:50]}"

# ---------- GOOGLE SYNC ----------
try:
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    credentials = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    gc = gspread.authorize(credentials)
    spreadsheet = gc.open("Garmin_Data")
    
    # Лист Morning
    row = [today_date, weight, resting_hr, hrv, body_battery, sleep_score, sleep_hours]
    res = update_or_append(spreadsheet.worksheet("Morning"), today_date, row)
    
    # Лист AI_Log
    debug_str = " | ".join(debug_log)
    spreadsheet.worksheet("AI_Log").append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M"), 
        f"Status: {debug_str}", 
        ai_advice
    ])
    print(f"🚀 Done: {debug_str}")
except Exception as e:
    print(f"🚨 Sheets Error: {e}")
