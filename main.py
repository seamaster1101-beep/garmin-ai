import os
import json
import requests
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials

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
            if search_date in str(val):
                found_idx = i + 1
                break
        
        # Используем USER_ENTERED, чтобы Google Sheets сам распознал числа и даты
        if found_idx != -1:
            sheet.update(f"A{found_idx}", [row_data], value_input_option='USER_ENTERED')
            return "Updated"
        else:
            sheet.append_row(row_data, value_input_option='USER_ENTERED')
            return "Appended"
    except Exception as e: return f"Err: {e}"

# --- LOGIN & DATA ---
gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
gar.login()
now = datetime.now()
today_str = now.strftime("%Y-%m-%d")

# 1. Сбор базовых данных (Твой рабочий метод)
summary = gar.get_user_summary(today_str) or {}
hrv_res = gar.get_hrv_data(today_str) or {}
hrv = hrv_res.get("hrvSummary", {}).get("lastNightAvg") or 0
r_hr = summary.get("restingHeartRate") or 0

# 2. Сон и Точное время (Твой рабочий метод)
morning_ts = f"{today_str} 08:00"
slp_sc, slp_h = 0, 0
try:
    sleep_data = gar.get_sleep_data(today_str) or {}
    dto = sleep_data.get("dailySleepDTO") or {}
    if dto:
        slp_h = round(float(dto.get("sleepTimeSeconds", 0)) / 3600, 1)
        slp_sc = dto.get("sleepScore") or 0
        raw_ts = dto.get("sleepEndTimestampLocal")
        if raw_ts:
            morning_ts = datetime.fromtimestamp(raw_ts / 1000).strftime("%Y-%m-%d %H:%M")
except: pass

# 3. Вес
weight = 0
try:
    w_data = gar.get_body_composition(today_str, today_str) or {}
    weights = w_data.get('dateWeightList', [])
    if weights:
        weight = round(float(weights[-1].get('weight', 0)) / 1000, 1)
except: pass

# 4. Калории (Твой рабочий метод)
cals = int(summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0))

# 5. Fitness Age (Gemini - Строго только число)
fit_age = ""
if GEMINI_API_KEY and hrv > 0:
    try:
        prompt = f"Возраст 62, HRV {hrv}, RHR {r_hr}. Оцени фитнес-возраст. Выведи ТОЛЬКО число."
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10).json()
        fit_age = res["candidates"][0]["content"]["parts"][0]["text"].strip()
    except: fit_age = "Err"

# --- ФОРМИРОВАНИЕ СТРОК (Чистые данные без str()) ---

# Morning (A:Date, B:Weight, C:Fat, D:Muscle, E:RHR, F:HRV, G:BB, H:Score, I:Hours, J:Age, K:FitAge)
morning_row = [
    morning_ts, 
    weight if weight > 0 else "", 
    "", "", # Fat и Muscle (пусто)
    int(r_hr),
    int(hrv),
    int(summary.get("bodyBatteryHighestValue", 0)),
    int(slp_sc),
    float(slp_h),
    62, 
    fit_age
]

# Daily (A:Date, B:Steps, C:Dist, D:Cals, E:RHR, F:BB)
steps = int(summary.get('totalSteps', 0))
dist = round(float(steps * 0.000762), 2)
daily_row = [
    today_str, 
    steps, 
    dist, 
    cals, 
    int(r_hr), 
    int(summary.get("bodyBatteryMostRecentValue", 0))
]

# --- ЗАПИСЬ ---
creds_dict = json.loads(GOOGLE_CREDS_JSON)
creds = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
ss = gspread.authorize(creds).open("Garmin_Data")

update_or_append(ss.worksheet("Morning"), today_str, morning_row)
update_or_append(ss.worksheet("Daily"), today_str, daily_row)

print(f"✅ Успех: Калории {cals}, Время {morning_ts}")
