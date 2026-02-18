import os
import json
from datetime import datetime
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

print("🚀 Starting Garmin Sync: Smart Model Selection + Debug Mode")

# ---------- SETTINGS ----------
HR_MAX = 165
gemini_key = os.environ.get("GEMINI_API_KEY")

# ---------- FUNCTIONS ----------
def update_or_append(sheet, date_str, row_data):
    try:
        cell = sheet.find(date_str)
        row_num = cell.row
        for i, new_value in enumerate(row_data[1:], start=2):
            if new_value != "" and new_value is not None:
                sheet.update_cell(row_num, i, new_value)
        print(f"✅ {sheet.title}: Данные за {date_str} обновлены.")
    except gspread.exceptions.CellNotFound:
        sheet.append_row(row_data)
        print(f"✅ {sheet.title}: Создана строка за {date_str}.")

# ---------- GARMIN LOGIN ----------
client = Garmin(os.environ["GARMIN_EMAIL"], os.environ["GARMIN_PASSWORD"])
client.login()

now = datetime.now()
today_date = now.strftime("%Y-%m-%d")

# ---------- DATA COLLECTION ----------
stats = client.get_stats(today_date)
resting_hr = stats.get("restingHeartRate") or ""
body_battery = stats.get("bodyBatteryMostRecentValue") or ""

# HRV Debug
try:
    hrv_raw = client.get_hrv_data(today_date)
    hrv = hrv_raw[0].get('lastNightAvg', "") if hrv_raw else ""
    print(f"DEBUG: Raw HRV data: {hrv_raw}")
except Exception as e:
    print(f"DEBUG: HRV Error: {e}")
    hrv = ""

# Weight
try:
    body = client.get_body_composition(today_date)
    weight = round(body.get('totalWeight', 0) / 1000, 1) if body and body.get('totalWeight') else ""
except: weight = ""

# Sleep
try:
    sleep = client.get_sleep_data(today_date)
    sleep_score = sleep.get('dailySleepDTO', {}).get('sleepScore', "")
    s_sec = sleep.get('dailySleepDTO', {}).get('sleepTimeSeconds') or 0
    sleep_hours = round(s_sec / 3600, 1) if s_sec > 0 else ""
except: sleep_score, sleep_hours = "", ""

# ---------- AI ANALYSIS (ULTIMATE FIX) ----------
ai_advice = "Анализ недоступен"
if gemini_key:
    try:
        genai.configure(api_key=gemini_key.strip())
        # Авто-поиск доступной модели
        models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        target = next((m for m in models if 'flash' in m), models[0]) if models else None
        
        if target:
            model = genai.GenerativeModel(target)
            prompt = f"Данные за {today_date}: Сон {sleep_hours}ч, HRV: {hrv}, Пульс: {resting_hr}. Дай совет на завтра (2 фразы)."
            response = model.generate_content(prompt)
            ai_advice = response.text
            print(f"✅ AI ({target}) ответил успешно.")
    except Exception as e:
        ai_advice = f"AI Error: {str(e)[:50]}"

# ---------- GOOGLE SHEETS SYNC ----------
creds = json.loads(os.environ["GOOGLE_CREDS"])
credentials = Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
gc = gspread.authorize(credentials)
spreadsheet = gc.open("Garmin_Data")

# Morning
update_or_append(spreadsheet.worksheet("Morning"), today_date, [today_date, weight, resting_hr, hrv, body_battery, sleep_score, sleep_hours])

# Log
spreadsheet.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Sync", ai_advice])

print("🚀 Синхронизация завершена.")
