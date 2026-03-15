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

def final_safe_write(sheet, search_date, row_data):
    try:
        col_values = sheet.col_values(1)
        found_idx = -1
        for i, val in enumerate(col_values):
            if search_date[:10] in str(val):
                found_idx = i + 1
                break
        
        # Если нашли строку, сначала полностью её чистим
        if found_idx != -1:
            sheet.batch_clear([f"A{found_idx}:K{found_idx}"])
        else:
            # Если не нашли, будем добавлять в конец
            found_idx = len(col_values) + 1

        # ЗАПИСЬ ПО ЯЧЕЙКАМ (Самый надежный метод против сдвигов)
        # 1. Дату пишем с апострофом в начале, чтобы Google Sheets считал её ТЕКСТОМ (будет слева)
        sheet.update_acell(f"A{found_idx}", f"'{row_data[0]}")
        
        # 2. Остальные данные пишем как числа (будут справа)
        remaining_data = [row_data[1:]]
        col_end = chr(65 + len(row_data) - 1)
        sheet.update(f"B{found_idx}:{col_end}{found_idx}", remaining_data, value_input_option='USER_ENTERED')
        
    except Exception as e:
        print(f"Err in {sheet.title}: {e}")

# --- DATA ---
gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
gar.login()
today_str = datetime.now().strftime("%Y-%m-%d")

# Собираем Garmin данные
summary = gar.get_user_summary(today_str) or {}
hrv = (gar.get_hrv_data(today_str) or {}).get("hrvSummary", {}).get("lastNightAvg") or ""
r_hr = summary.get("restingHeartRate") or ""
# КАЛОРИИ (Складываем честно)
cals = int(summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0))

# Вес (Динамический)
weight = ""
try:
    w_data = gar.get_body_composition((datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d"), today_str)
    weights = w_data.get('dateWeightList', [])
    if weights:
        weight = round(float(max(weights, key=lambda x: x.get('sampleTime'))['weight']) / 1000, 1)
except: pass

# Время и Сон
morning_ts = f"{today_str} 08:00"
slp_sc, slp_h = "", ""
try:
    dto = (gar.get_sleep_data(today_str) or {}).get("dailySleepDTO") or {}
    if dto:
        slp_h = round(float(dto.get("sleepTimeSeconds", 0)) / 3600, 1)
        slp_sc = dto.get("sleepScore") or ""
        raw_ts = dto.get("sleepEndTimestampLocal")
        if raw_ts:
            morning_ts = datetime.fromtimestamp(raw_ts / 1000).strftime("%Y-%m-%d %H:%M")
except: pass

# Fitness Age (Gemini) - только цифра
fit_age = ""
if GEMINI_API_KEY and hrv:
    try:
        p = f"User 62y, HRV {hrv}, RHR {r_hr}. Output ONLY fitness age number."
        res = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}", 
                            json={"contents": [{"parts": [{"text": p}]}]}, timeout=10).json()
        fit_age = ''.join(filter(str.isdigit, res["candidates"][0]["content"]["parts"][0]["text"]))
    except: pass

# --- ФОРМИРУЕМ СТРОКИ ---
# Morning: A:Date, B:Weight, C:Fat, D:Muscle, E:RHR, F:HRV, G:BB, H:Score, I:Hours, J:Age, K:FitAge
morning_row = [morning_ts, weight, "", "", r_hr, hrv, summary.get("bodyBatteryHighestValue", ""), slp_sc, slp_h, 62, fit_age]

# Daily: A:Date, B:Steps, C:Dist, D:Cals, E:RHR, F:BB
daily_row = [today_str, summary.get('totalSteps', 0), round(summary.get('totalSteps', 0) * 0.000762, 2), cals, r_hr, summary.get("bodyBatteryMostRecentValue", "")]

# --- ЗАПИСЬ ---
ss = gspread.authorize(Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), 
     scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])).open("Garmin_Data")

final_safe_write(ss.worksheet("Morning"), today_str, morning_row)
final_safe_write(ss.worksheet("Daily"), today_str, daily_row)

print(f"✅ Фикс применен. Вес: {weight}, Калории в D: {cals}, Время: {morning_ts}")
