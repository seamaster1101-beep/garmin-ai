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
GOOGLE_CREDS = os.environ.get("GOOGLE_SHEETS_CREDS") or os.environ.get("GOOGLE_CREDS")

def write_to_sheet(sheet, date_str, row_data):
    try:
        col_values = sheet.col_values(1)
        found_idx = -1
        search_date = date_str.split(' ')[0]
        for i, val in enumerate(col_values):
            if search_date in str(val):
                found_idx = i + 1
                break
        
        if found_idx != -1:
            # USER_ENTERED заставляет Google считать строки числами
            sheet.update(f"A{found_idx}", [row_data], value_input_option='USER_ENTERED')
        else:
            sheet.append_row(row_data, value_input_option='USER_ENTERED')
    except Exception as e:
        print(f"Sheet Error: {e}")

# --- DATA ---
gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
gar.login()
today = datetime.now().strftime("%Y-%m-%d")

stats = gar.get_user_summary(today)
sleep = gar.get_sleep_data(today) or {}
hrv_data = gar.get_hrv_data(today) or {}

# 1. Калории (Суммируем, если total пустой)
total_cals = stats.get('totalCalories')
if not total_cals or total_cals == 0:
    total_cals = int(stats.get('activeCalories', 0) + stats.get('bmrCalories', 0))

# 2. Сон и Время (07:22)
dto = sleep.get('dailySleepDTO', {})
wake_raw = dto.get('sleepEndTimeLocal')
wake_time = wake_raw.replace('T', ' ')[:16] if wake_raw else datetime.now().strftime("%Y-%m-%d %H:%M")

slp_score = int(dto.get('sleepScore', 0))
slp_hours = round(float(dto.get('sleepTimeSeconds', 0) / 3600), 1)
hrv_val = int(hrv_data.get('hrvSummary', {}).get('lastNightAvg', 0))

# 3. Вес
weight = 0.0
try:
    w_hist = gar.get_body_composition(today, today)
    w_list = w_hist.get('dateWeightList', [])
    if w_list: weight = round(float(w_list[-1]['weight'] / 1000), 1)
except: pass

# 4. Fitness Age (Числовой ответ)
fit_age = 0
if GEMINI_API_KEY and hrv_val > 0:
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        p = f"Im 62y, RHR {stats.get('restingHeartRate')}, HRV {hrv_val}. Calculate my fitness age. Return ONLY the number."
        resp = requests.post(url, json={"contents": [{"parts": [{"text": p}]}]}, timeout=10).json()
        fit_age = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
    except: fit_age = "Err"

# --- СТРОКИ (Данные передаются как числа, а не строки) ---

# Morning (A-K): Date, Weight, Fat, Muscle, RHR, HRV, BB, Sleep_Sc, Sleep_H, Age, Fit_Age
morning_row = [
    wake_time, 
    weight if weight > 0 else "", 
    "", "", # Fat, Muscle
    int(stats.get('restingHeartRate', 0)),
    hrv_val,
    int(stats.get('bodyBatteryHighestValue', 0)),
    slp_score,
    slp_hours,
    62, 
    fit_age
]

# Daily (A-F): Date, Steps, Dist, Cals, RHR, BB
dist_km = round(float(stats.get('totalDistanceMeters', 0) / 1000), 2)
daily_row = [
    today,
    int(stats.get('totalSteps', 0)),
    dist_km,
    total_cals,
    int(stats.get('restingHeartRate', 0)),
    int(stats.get('bodyBatteryMostRecentValue', 0))
]

# --- WRITE ---
creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS), scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
ss = gspread.authorize(creds).open("Garmin_Data")

write_to_sheet(ss.worksheet("Morning"), today, morning_row)
write_to_sheet(ss.worksheet("Daily"), today, daily_row)

print(f"Done. Cals: {total_cals}")
