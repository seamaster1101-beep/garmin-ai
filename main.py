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
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_SHEETS_CREDS")

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
            sheet.update(f"A{found_idx}", [row_data], value_input_option='USER_ENTERED')
        else:
            sheet.append_row(row_data, value_input_option='USER_ENTERED')
    except Exception as e: print(f"Err: {e}")

# --- LOGIN ---
gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
gar.login()
now = datetime.now()
today_str = now.strftime("%Y-%m-%d")

# --- 1. ПЕРВИЧНЫЕ ДАННЫЕ (Объявляем всё здесь) ---
summary = gar.get_user_summary(today_str) or {}
hrv_res = gar.get_hrv_data(today_str) or {}
hrv = hrv_res.get("hrvSummary", {}).get("lastNightAvg") or ""
r_hr = summary.get("restingHeartRate") or ""

# --- 2. ВЕС (Твой рабочий блок) ---
weight = ""
try:
    w_data = gar.get_body_composition((now - timedelta(days=3)).strftime("%Y-%m-%d"), today_str) or {}
    weights = w_data.get('dateWeightList', [])
    if weights:
        actual_entry = max(weights, key=lambda x: x.get('sampleTime', 0))
        weight = round(float(actual_entry.get('weight', 0)) / 1000, 1)
except: pass

# --- 3. СОН И ВРЕМЯ (Фикс 07:22 и Score) ---
morning_ts = f"{today_str} 08:00"
slp_sc, slp_h = "", ""
try:
    sleep_data = gar.get_sleep_data(today_str) or {}
    dto = sleep_data.get("dailySleepDTO") or {}
    if dto:
        slp_h = round(float(dto.get("sleepTimeSeconds", 0)) / 3600, 1)
        slp_sc = dto.get("sleepScore") or sleep_data.get("sleepSummary", {}).get("score") or ""
        raw_ts = dto.get("sleepEndTimestampLocal")
        if raw_ts:
            morning_ts = datetime.fromtimestamp(raw_ts / 1000).strftime("%Y-%m-%d %H:%M")
except: pass

# --- 4. FITNESS AGE (Теперь HRV точно определен выше) ---
fit_age = ""
if GEMINI_API_KEY and hrv:
    try:
        p = f"User 62y, HRV {hrv}, RHR {r_hr}. Оцени фитнес-возраст. Выдай только число."
        res = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}", 
                            json={"contents": [{"parts": [{"text": p}]}]}, timeout=15).json()
        fit_age = ''.join(filter(str.isdigit, res["candidates"][0]["content"]["parts"][0]["text"]))
    except: pass

# --- 5. ФОРМИРОВАНИЕ СТРОК ---
morning_row = [f"'{morning_ts}", weight, "", "", r_hr, hrv, summary.get("bodyBatteryHighestValue", ""), slp_sc, slp_h, 62, fit_age]

steps = summary.get('totalSteps', 0)
cals = int(summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0))
daily_row = [f"'{today_str}", steps, round(steps * 0.000762, 2), cals, r_hr, summary.get("bodyBatteryMostRecentValue", "")]

# --- 6. ЗАПИСЬ ---
creds_dict = json.loads(GOOGLE_CREDS_JSON)
ss = gspread.authorize(Credentials.from_service_account_info(creds_dict, 
     scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])).open("Garmin_Data")

update_or_append(ss.worksheet("Morning"), today_str, morning_row)
update_or_append(ss.worksheet("Daily"), today_str, daily_row)

print(f"✅ Финиш: Время={morning_ts}, Вес={weight}, Score={slp_sc}, Calories={cals}")
