import os
import json
import requests
from datetime import datetime
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials

# --- CONFIG ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_SHEETS_CREDS") or os.environ.get("GOOGLE_CREDS")

def clean_and_write(sheet, search_date, row_data, end_col):
    try:
        col_values = sheet.col_values(1)
        found_idx = -1
        for i, val in enumerate(col_values):
            if search_date[:10] in str(val):
                found_idx = i + 1
                break
        
        if found_idx != -1:
            range_to_clean = f"A{found_idx}:{end_col}{found_idx}"
            sheet.batch_clear([range_to_clean])
            sheet.update(f"A{found_idx}", [row_data], value_input_option='USER_ENTERED')
        else:
            sheet.append_row(row_data, value_input_option='USER_ENTERED')
    except Exception as e:
        print(f"Sheet error: {e}")

# --- DATA ---
gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
gar.login()
today_str = datetime.now().strftime("%Y-%m-%d")

summary = gar.get_user_summary(today_str) or {}
hrv_res = gar.get_hrv_data(today_str) or {}
hrv = hrv_res.get("hrvSummary", {}).get("lastNightAvg") or ""
r_hr = summary.get("restingHeartRate") or ""

# Сон и Время (07:22)
morning_ts = f"{today_str} 08:00"
slp_sc, slp_h = "", ""
try:
    sleep_data = gar.get_sleep_data(today_str) or {}
    dto = sleep_data.get("dailySleepDTO") or {}
    if dto:
        slp_h = round(float(dto.get("sleepTimeSeconds", 0)) / 3600, 1)
        # ВОЗВРАЩАЕМ SCORE
        slp_sc = dto.get("sleepScore") or ""
        raw_ts = dto.get("sleepEndTimestampLocal")
        if raw_ts:
            morning_ts = datetime.fromtimestamp(raw_ts / 1000).strftime("%Y-%m-%d %H:%M")
except: pass

# Fitness Age
fit_age = ""
if GEMINI_API_KEY and hrv:
    try:
        p = f"User 62y, HRV {hrv}, RHR {r_hr}. Output only fitness age number."
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        res = requests.post(url, json={"contents": [{"parts": [{"text": p}]}]}, timeout=10).json()
        raw_text = res["candidates"][0]["content"]["parts"][0]["text"].strip()
        fit_age = ''.join(filter(str.isdigit, raw_text))
    except: pass

# --- MORNING ROW (A-K) ---
# A:Date, B:Weight, C:Fat, D:Muscle, E:RHR, F:HRV, G:BB, H:Score, I:Hours, J:Age, K:FitAge
morning_row = [
    morning_ts,      # A (Слева)
    88.1,            # B (Справа)
    "", "",          # C, D
    r_hr,            # E
    hrv,             # F
    summary.get("bodyBatteryHighestValue", ""), # G
    slp_sc,          # H - ВОТ ОН!
    slp_h,           # I
    62,              # J
    fit_age          # K
]

# --- DAILY ROW (A-F) ---
steps = summary.get('totalSteps', 0)
cals = int(summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0))
daily_row = [
    today_str,       # A (Слева)
    steps,           # B
    round(steps * 0.000762, 2), # C
    cals,            # D
    r_hr,            # E
    summary.get("bodyBatteryMostRecentValue", "") # F
]

# --- WRITE ---
creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), 
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
ss = gspread.authorize(creds).open("Garmin_Data")

clean_and_write(ss.worksheet("Morning"), today_str, morning_row, "K")
clean_and_write(ss.worksheet("Daily"), today_str, daily_row, "F")

print(f"✅ Готово. Сон: {slp_sc}, Калории: {cals}")
