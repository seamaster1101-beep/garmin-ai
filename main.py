import os
import json
from datetime import datetime, timedelta
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
            # Обновляем только непустые значения
            for i, val in enumerate(row_data[1:], start=2):
                if val not in (None, "", 0, "0", 0.0, "N/A"):
                    sheet.update_cell(found_idx, i, val)
            return "Updated"
        else:
            sheet.append_row(row_data)
            return "Appended"
    except Exception as e: return f"Err: {str(e)[:15]}"

# --- LOGIN ---
gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
gar.login()

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")

# --- СБОР ДАННЫХ ---
stats = gar.get_user_summary(today_str)
sleep = gar.get_sleep_data(today_str)
hrv_data = gar.get_hrv_data(today_str) or {}

# Проверка калорий в логах
print(f"DEBUG Stats: {stats}")

# 1. Возраст
real_age = 62

# 2. Время и Сон
dto = sleep.get('dailySleepDTO', {})
raw_ts = dto.get('sleepEndTimeLocal')
morning_ts = raw_ts.replace('T', ' ')[:16] if raw_ts else f"{today_str} 08:00"

slp_h = round(float(dto.get('sleepTimeSeconds', 0)) / 3600, 1) if dto.get('sleepTimeSeconds') else ""
slp_sc = dto.get('sleepScore', "")

# 3. Вес и HRV
weight = ""
try:
    w_data = gar.get_body_composition((now - timedelta(days=3)).strftime("%Y-%m-%d"), today_str)
    weights = w_data.get('dateWeightList', [])
    if weights:
        weight = str(round(float(max(weights, key=lambda x: x['sampleTime'])['weight']) / 1000, 1)).replace('.', ',')
except: pass

r_hr = stats.get('restingHeartRate', "")
hrv_val = hrv_data.get('hrvSummary', {}).get('lastNightAvg', "")
bb_morning = stats.get('bodyBatteryHighestValue', "")

# --- ФОРМИРОВАНИЕ СТРОКИ MORNING (Строго по буквам колонок A-K) ---
# A:Date, B:Weight, C:Fat, D:Muscle, E:RHR, F:HRV, G:BB, H:Score, I:Hours, J:Age, K:FitAge
morning_row = [
    morning_ts,   # A
    weight,       # B
    "",           # C (Body Fat - пусто)
    "",           # D (Muscle Mass - пусто)
    r_hr,         # E
    hrv_val,      # F
    bb_morning,   # G
    slp_sc,       # H
    slp_h,        # I
    real_age,     # J
    "AI Calc"     # K
]

# --- DAILY (Калории) ---
cals = stats.get('totalCalories', stats.get('caloriesOutAllDay', ""))
steps = stats.get('totalSteps', 0)
dist = str(round(stats.get('totalDistanceMeters', 0) / 1000, 2)).replace('.', ',')
daily_row = [today_str, steps, dist, cals, r_hr, stats.get('bodyBatteryMostRecentValue', "")]

# --- ЗАПИСЬ ---
creds = json.loads(GOOGLE_CREDS_JSON)
gc = gspread.authorize(Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]))
ss = gc.open("Garmin_Data")

# Запись Morning
update_or_append(ss.worksheet("Morning"), today_str, morning_row)

# Запись Daily
update_or_append(ss.worksheet("Daily"), today_str, daily_row)

print(f"✅ Готово. Калории: {cals}, Время: {morning_ts}")
