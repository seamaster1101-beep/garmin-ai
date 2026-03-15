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

def update_row(sheet, search_date, row_data):
    try:
        col_values = sheet.col_values(1)
        found_idx = -1
        for i, val in enumerate(col_values):
            if search_date in str(val):
                found_idx = i + 1
                break
        
        if found_idx != -1:
            # Дату пишем как текст (чтобы была слева), остальное как числа (чтобы были справа)
            # Сначала обновляем дату отдельно
            sheet.update_acell(f"A{found_idx}", row_data[0])
            # Затем все остальные цифры в ряду
            range_label = f"B{found_idx}:{chr(65+len(row_data)-1)}{found_idx}"
            sheet.update(range_label, [row_data[1:]], value_input_option='USER_ENTERED')
        else:
            sheet.append_row(row_data, value_input_option='USER_ENTERED')
    except Exception as e:
        print(f"Ошибка записи: {e}")

# --- DATA ---
gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
gar.login()
now = datetime.now()
today_str = now.strftime("%Y-%m-%d")

# Твой проверенный блок сбора (Morning)
summary = gar.get_user_summary(today_str) or {}
hrv_data = gar.get_hrv_data(today_str) or {}
hrv = hrv_data.get("hrvSummary", {}).get("lastNightAvg") or 0
r_hr = summary.get("restingHeartRate") or 0

# Точное время пробуждения (с фиксом 07:22)
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
            dt_obj = datetime.fromtimestamp(raw_ts / 1000)
            # Принудительный формат HH:MM с нулями
            morning_ts = dt_obj.strftime("%Y-%m-%d %H:%M") 
except: pass

# Fitness Age (Минималистичный запрос)
fit_age = ""
if GEMINI_API_KEY and hrv > 0:
    try:
        p = f"User 62y, HRV {hrv}, RHR {r_hr}. Output only one number as fitness age."
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        res = requests.post(url, json={"contents": [{"parts": [{"text": p}]}]}, timeout=10).json()
        fit_age = res["candidates"][0]["content"]["parts"][0]["text"].strip()
        # Если ИИ выдал не число, обрезаем лишнее
        fit_age = ''.join(filter(str.isdigit, fit_age))[:2]
    except: fit_age = "Err"

# --- ФОРМИРУЕМ РЯДЫ ---
# Morning: Date(A), Weight(B)... FitnessAge(K)
morning_row = [morning_ts, 88.1, "", "", int(r_hr), int(hrv), 
               int(summary.get("bodyBatteryHighestValue", 0)), 
               int(slp_sc), float(slp_h), 62, fit_age]

# Daily: Date(A), Steps(B), Dist(C), Cals(D), RHR(E), BB(F)
steps = int(summary.get('totalSteps', 0))
cals = int(summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0))
daily_row = [today_str, steps, round(steps * 0.000762, 2), cals, int(r_hr), 
             int(summary.get("bodyBatteryMostRecentValue", 0))]

# --- СОХРАНЕНИЕ ---
creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), 
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
ss = gspread.authorize(creds).open("Garmin_Data")

update_row(ss.worksheet("Morning"), today_str, morning_row)
update_row(ss.worksheet("Daily"), today_str, daily_row)

print(f"✅ Исправлено: {morning_ts}, Fitness Age: {fit_age}")
