import os
import json
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

# ---------- SETTINGS ----------
HR_MAX = 165
gemini_key = os.environ.get("GEMINI_API_KEY")

# ---------- FUNCTIONS ----------
def update_or_append(sheet, date_str, row_data):
    try:
        dates = sheet.col_values(1)
        if date_str in dates:
            row_num = dates.index(date_str) + 1
            for i, new_value in enumerate(row_data[1:], start=2):
                if new_value != "" and new_value is not None:
                    sheet.update_cell(row_num, i, new_value)
            print(f"✅ {sheet.title}: Данные за {date_str} обновлены.")
        else:
            sheet.append_row(row_data)
            print(f"✅ {sheet.title}: Создана строка за {date_str}.")
    except Exception as e:
        print(f"❌ Sheets Error: {e}")

# ---------- GARMIN LOGIN ----------
client = Garmin(os.environ["GARMIN_EMAIL"], os.environ["GARMIN_PASSWORD"])
client.login()

now = datetime.now()
today_date = now.strftime("%Y-%m-%d")
# Для интервала
start_date = (now - timedelta(days=2)).strftime("%Y-%m-%d")

# ---------- DATA COLLECTION ----------
# 1. Общие статы
stats = client.get_stats(today_date)
resting_hr = stats.get("restingHeartRate") or ""
body_battery = stats.get("bodyBatteryMostRecentValue") or ""

# 2. ВЕС (Тянем из истории за последние 3 дня)
try:
    weight_history = client.get_body_composition(start_date, today_date)
    # Если есть список взвешиваний (uploads), берем последнее
    if weight_history and 'uploads' in weight_history and weight_history['uploads']:
        last_weight_raw = weight_history['uploads'][-1]['weight']
        weight = round(last_weight_raw / 1000, 1)
    else:
        # Запасной вариант
        weight = round(weight_history.get('totalWeight', 0) / 1000, 1) if weight_history.get('totalWeight') else ""
except: weight = ""

# 3. HRV (Смотрим сегодня, если пусто — вчера)
try:
    hrv_data = client.get_hrv_data(today_date)
    if not hrv_data or not hrv_data[0].get('lastNightAvg'):
        hrv_data = client.get_hrv_data((now - timedelta(days=1)).strftime("%Y-%m-%d"))
    hrv = hrv_data[0].get('lastNightAvg', "") if hrv_data else ""
except: hrv = ""

# 4. СОН (Глубокий поиск Score)
try:
    sleep = client.get_sleep_data(today_date)
    sleep_score = sleep.get('dailySleepDTO', {}).get('sleepScore') or ""
    s_sec = sleep.get('dailySleepDTO', {}).get('sleepTimeSeconds') or 0
    sleep_hours = round(s_sec / 3600, 1) if s_sec > 0 else ""
except: sleep_score, sleep_hours = "", ""

# ---------- AI ANALYSIS (Тот, который заработал) ----------
ai_advice = "Анализ не выполнен"
if gemini_key:
    try:
        genai.configure(api_key=gemini_key.strip())
        models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        target = next((m for m in models if 'flash' in m), models[0]) if models else None
        if target:
            model = genai.GenerativeModel(target)
            prompt = f"Данные: Сон {sleep_hours}ч (Score {sleep_score}), HRV {hrv}, Пульс {resting_hr}. Дай совет на завтра (2 фразы)."
            response = model.generate_content(prompt)
            ai_advice = response.text
    except: ai_advice = "AI Error"

# ---------- SYNC ----------
creds = json.loads(os.environ["GOOGLE_CREDS"])
credentials = Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
gc = gspread.authorize(credentials)
spreadsheet = gc.open("Garmin_Data")

# Morning
update_or_append(spreadsheet.worksheet("Morning"), today_date, [today_date, weight, resting_hr, hrv, body_battery, sleep_score, sleep_hours])

# Log
spreadsheet.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Final Fix Sync", ai_advice])

print(f"🚀 Done! Weight: {weight}, HRV: {hrv}, Sleep Score: {sleep_score}")
