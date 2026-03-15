import os
import json
from datetime import datetime
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import requests

# --- CONFIG ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_SHEETS_CREDS") or os.environ.get("GOOGLE_CREDS")

def update_or_append(sheet, date_str, row_data):
    try:
        col_values = sheet.col_values(1)
        search_date = date_str.split(' ')[0]
        found_idx = -1
        for i, val in enumerate(col_values):
            if search_date in str(val):
                found_idx = i + 1
                break
        if found_idx != -1:
            # Обновляем ячейки. ВАЖНО: value_input_option='USER_ENTERED' заставит Google понять числа
            sheet.update(f"A{found_idx}", [row_data], value_input_option='USER_ENTERED')
            return "Updated"
        else:
            sheet.append_row(row_data, value_input_option='USER_ENTERED')
            return "Appended"
    except Exception as e: print(f"Sheet error: {e}")

# --- DATA COLLECTION ---
gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
gar.login()
today = datetime.now().strftime("%Y-%m-%d")

stats = gar.get_user_summary(today)
sleep = gar.get_sleep_data(today)
hrv_data = gar.get_hrv_data(today) or {}

# 1. Время пробуждения (из данных сна)
dto = sleep.get('dailySleepDTO', {})
wake_time_raw = dto.get('sleepEndTimeLocal')
wake_time = wake_time_raw.replace('T', ' ')[:16] if wake_time_raw else f"{today} 08:00"

# 2. Калории (пробуем все возможные ключи)
cals = stats.get('totalCalories') or stats.get('caloriesOutAllDay') or stats.get('totalCaloriesOut') or 0

# 3. Сон и HRV
slp_hours = round(dto.get('sleepTimeSeconds', 0) / 3600, 1) if dto.get('sleepTimeSeconds') else 0
slp_score = dto.get('sleepScore', 0)
hrv_avg = hrv_data.get('hrvSummary', {}).get('lastNightAvg', 0)

# 4. Вес
weight = 0
try:
    w_body = gar.get_body_composition(today, today)
    w_list = w_body.get('dateWeightList', [])
    if w_list: weight = round(w_list[-1]['weight'] / 1000, 1)
except: pass

# 5. Fitness Age (Gemini)
fitness_age_val = ""
if GEMINI_API_KEY:
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        prompt = f"Возраст 62, HRV {hrv_avg}, RHR {stats.get('restingHeartRate')}. Оцени мой Fitness Age одним числом."
        res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10).json()
        fitness_age_val = res["candidates"][0]["content"]["parts"][0]["text"].strip()[:10]
    except: fitness_age_val = "Error"

# --- ФОРМИРОВАНИЕ СТРОК (БЕЗ str(), ЧТОБЫ ПЕРЕДАТЬ ЧИСЛА) ---

# Morning: A:Date, B:Weight, C:Fat, D:Muscle, E:RHR, F:HRV, G:BB, H:Score, I:Hours, J:Age, K:FitAge
morning_row = [
    wake_time, weight, "", "", 
    stats.get('restingHeartRate', 0), 
    hrv_avg, 
    stats.get('bodyBatteryHighestValue', 0), 
    slp_score, slp_hours, 62, fitness_age_val
]

# Daily: A:Date, B:Steps, C:Dist, D:Cals, E:RHR, F:BB
dist_km = round(stats.get('totalDistanceMeters', 0) / 1000, 2)
daily_row = [
    today, stats.get('totalSteps', 0), dist_km, cals, 
    stats.get('restingHeartRate', 0), 
    stats.get('bodyBatteryMostRecentValue', 0)
]

# --- ЗАПИСЬ ---
creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
ss = gspread.authorize(creds).open("Garmin_Data")

update_or_append(ss.worksheet("Morning"), today, morning_row)
update_or_append(ss.worksheet("Daily"), today, daily_row)

print(f"Done. Wake: {wake_time}, Cals: {cals}")
