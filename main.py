import os
import json
import time
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import requests

# --- CONFIG ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# Используем именно тот ключ, который прописан в твоем окружении (судя по логу)
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
        
        # ВАЖНО: value_input_option='USER_ENTERED' исправляет форматирование (цифры вправо)
        if found_idx != -1:
            sheet.update(f"A{found_idx}", [row_data], value_input_option='USER_ENTERED')
            return "Updated"
        else:
            sheet.append_row(row_data, value_input_option='USER_ENTERED')
            return "Appended"
    except Exception as e: return f"Err: {e}"

# --- LOGIN ---
gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
gar.login()

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# --- DATA COLLECTION (Твой рабочий код) ---
morning_ts = f"{today_str} 08:00"
weight, r_hr, hrv, bb_morning, slp_sc, slp_h = "", "", "", "", "", ""

# HRV
try:
    hrv_res = gar.get_hrv_data(today_str) or {}
    hrv = hrv_res.get("hrvSummary", {}).get("lastNightAvg") or ""
except: pass

# Сон и Время (Твой рабочий алгоритм)
for d in [today_str, yesterday_str]:
    try:
        sleep_data = gar.get_sleep_data(d) or {}
        dto = sleep_data.get("dailySleepDTO") or {}
        if dto and dto.get("sleepTimeSeconds", 0) > 0:
            slp_h = round(float(dto.get("sleepTimeSeconds")) / 3600, 1)
            scores = dto.get("sleepScores") or {}
            slp_sc = scores.get("overall", {}).get("value") or dto.get("sleepScore") or ""
            raw_ts = dto.get("sleepEndTimestampLocal")
            if raw_ts:
                if isinstance(raw_ts, (int, float)):
                    morning_ts = datetime.fromtimestamp(raw_ts / 1000).strftime("%Y-%m-%d %H:%M")
                else:
                    morning_ts = str(raw_ts).replace("T", " ")[:16]
            break
    except: continue

# Вес
try:
    w_data = gar.get_body_composition((now - timedelta(days=3)).strftime("%Y-%m-%d"), today_str) or {}
    weights = w_data.get('dateWeightList', [])
    if weights:
        actual_entry = max(weights, key=lambda x: x.get('sampleTime', 0))
        weight = round(float(actual_entry.get('weight', 0)) / 1000, 1)
except: pass

# Сводка и Калории (Твой рабочий алгоритм)
summary = gar.get_user_summary(today_str) or {}
r_hr = summary.get("restingHeartRate") or summary.get("heartRateRestingValue") or ""
bb_now = summary.get("bodyBatteryMostRecentValue") or ""
bb_morning = summary.get("bodyBatteryHighestValue") or bb_now
cals = int(summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0))

# Fitness Age (Gemini)
fit_age = ""
if GEMINI_API_KEY and hrv:
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        p = f"Атлет: 62 года, HRV {hrv}, RHR {r_hr}. Оцени фитнес-возраст. Выведи только цифру."
        res = requests.post(url, json={"contents": [{"parts": [{"text": p}]}]}, timeout=10).json()
        fit_age = res["candidates"][0]["content"]["parts"][0]["text"].strip()
    except: fit_age = "Err"

# --- СТРОКИ ДЛЯ ЗАПИСИ ---
# Morning (A-K): Date, Weight, Fat, Muscle, RHR, HRV, BB, Score, Hours, Age, FitAge
morning_row = [morning_ts, weight, "", "", r_hr, hrv, bb_morning, slp_sc, slp_h, 62, fit_age]

# Daily (A-F): Date, Steps, Dist, Cals, RHR, BB
steps = summary.get('totalSteps', 0)
dist = round(float(steps * 0.000762), 2)
daily_row = [today_str, steps, dist, cals, r_hr, bb_now]

# --- WRITE ---
if not GOOGLE_CREDS_JSON:
    print("Ошибка: Секрет GOOGLE_CREDS не найден!"); exit(1)

creds_dict = json.loads(GOOGLE_CREDS_JSON)
credentials = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
ss = gspread.authorize(credentials).open("Garmin_Data")

update_or_append(ss.worksheet("Morning"), today_str, morning_row)
update_or_append(ss.worksheet("Daily"), today_str, daily_row)

print(f"✅ Готово! Время: {morning_ts}, Калории: {cals}")
