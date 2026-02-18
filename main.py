import os
import json
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

print("🚀 Starting Garmin Sync: Ultimate Weight & HRV Fix")

# ---------- SETTINGS ----------
HR_MAX = 165
gemini_key = os.environ.get("GEMINI_API_KEY")

# ---------- FUNCTIONS ----------
def update_or_append(sheet, date_str, row_data):
    """Надежный поиск строки по первому столбцу"""
    try:
        dates = sheet.col_values(1)
        if date_str in dates:
            row_num = dates.index(date_str) + 1
            for i, new_value in enumerate(row_data[1:], start=2):
                if new_value != "" and new_value is not None:
                    sheet.update_cell(row_num, i, new_value)
            print(f"✅ {sheet.title}: Данные за {date_str} дополнены.")
        else:
            sheet.append_row(row_data)
            print(f"✅ {sheet.title}: Создана новая строка за {date_str}.")
    except Exception as e:
        print(f"❌ Ошибка при обновлении Sheets: {e}")

# ---------- GARMIN LOGIN ----------
client = Garmin(os.environ["GARMIN_EMAIL"], os.environ["GARMIN_PASSWORD"])
client.login()

now = datetime.now()
today_date = now.strftime("%Y-%m-%d")
yesterday_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# ---------- DATA COLLECTION ----------
# 1. Основные статы
stats = client.get_stats(today_date)
resting_hr = stats.get("restingHeartRate") or ""
body_battery = stats.get("bodyBatteryMostRecentValue") or ""

# 2. ВЕС (Исправлено: берем интервал, чтобы поймать утреннее взвешивание)
try:
    # Запрашиваем данные за 2 дня
    weight_data = client.get_body_composition(yesterday_date, today_date)
    # Ищем последнее взвешивание в списке
    if weight_data and 'uploads' in weight_data and len(weight_data['uploads']) > 0:
        # Берем самый свежий вес из списка
        last_weight_raw = weight_data['uploads'][-1]['weight']
        weight = round(last_weight_raw / 1000, 1)
    else:
        # Запасной вариант, если структура иная
        weight = round(weight_data.get('totalWeight') / 1000, 1) if weight_data.get('totalWeight') else ""
    print(f"DEBUG: Вес найден: {weight}")
except Exception as e:
    print(f"DEBUG Weight Error: {e}")
    weight = ""

# 3. HRV (Исправлено: если сегодня пусто, смотрим вчерашнюю ночь)
try:
    hrv_data = client.get_hrv_data(today_date)
    if not hrv_data or not hrv_data[0].get('lastNightAvg'):
        hrv_data = client.get_hrv_data(yesterday_date)
    
    hrv = hrv_data[0].get('lastNightAvg', "") if hrv_data else ""
    print(f"DEBUG: HRV найден: {hrv}")
except: hrv = ""

# 4. Сон
try:
    sleep = client.get_sleep_data(today_date)
    sleep_score = sleep.get('dailySleepDTO', {}).get('sleepScore') or ""
    s_sec = sleep.get('dailySleepDTO', {}).get('sleepTimeSeconds') or 0
    sleep_hours = round(s_sec / 3600, 1) if s_sec > 0 else ""
except: sleep_score, sleep_hours = "", ""

# ---------- AI ANALYSIS (Тот самый, что заработал) ----------
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

# ---------- GOOGLE SHEETS SYNC ----------
creds = json.loads(os.environ["GOOGLE_CREDS"])
credentials = Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
gc = gspread.authorize(credentials)
spreadsheet = gc.open("Garmin_Data")

# Morning Update
morning_data = [today_date, weight, resting_hr, hrv, body_battery, sleep_score, sleep_hours]
update_or_append(spreadsheet.worksheet("Morning"), today_date, morning_data)

# Log
spreadsheet.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Manual Sync", ai_advice])

print(f"🚀 Синхронизация завершена! Вес: {weight}, HRV: {hrv}, Сон: {sleep_hours}ч.")
