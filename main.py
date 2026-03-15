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
        
        # Записываем дату с апострофом (чтобы была слева), остальное USER_ENTERED (чтобы числа были справа)
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

# 1. Сон и Score (с защитой от пустых значений)
slp_sc, slp_h = "", ""
try:
    sleep_data = gar.get_sleep_data(today_str) or {}
    dto = sleep_data.get("dailySleepDTO") or {}
    if dto:
        # Пробуем достать Score напрямую или из вложенного словаря
        slp_sc = dto.get("sleepScore") or sleep_data.get("sleepSummary", {}).get("score") or ""
        slp_h = round(float(dto.get("sleepTimeSeconds", 0)) / 3600, 1)
        
        raw_ts = dto.get("sleepEndTimestampLocal")
        if raw_ts:
            morning_ts = datetime.fromtimestamp(raw_ts / 1000).strftime("%Y-%m-%d %H:%M")
except Exception as e:
    print(f"Sleep data error: {e}")

# 2. Fitness Age (Gemini) — добавляем жесткую проверку
fit_age = ""
if GEMINI_API_KEY and hrv:
    try:
        prompt = f"User 62y, HRV {hrv}, RHR {r_hr}. Оцени фитнес-возраст. Выдай ТОЛЬКО одно число."
        res = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}", 
            json={"contents": [{"parts": [{"text": prompt}]}]}, 
            timeout=15 # Увеличил таймаут
        ).json()
        text_resp = res["candidates"][0]["content"]["parts"][0]["text"].strip()
        fit_age = ''.join(filter(str.isdigit, text_resp))
    except:
        fit_age = "Calc..." # Чтобы ты видел, что запрос был, но не прошел

# --- СТРОКА ДЛЯ ЗАПИСИ (Ничего не сдвигаем!) ---
# A:Date, B:Weight, C:Fat, D:Muscle, E:RHR, F:HRV, G:BB, H:Score, I:Hours, J:Age, K:FitAge
morning_row = [
    f"'{morning_ts}", # Дата слева
    weight,           # Вес в B
    "", "",           # Пустые C, D
    r_hr,             # E
    hrv,              # F
    summary.get("bodyBatteryHighestValue", ""), # G
    slp_sc,           # H (Sleep Score вернулся!)
    slp_h,           # I
    62,               # J
    fit_age           # K (Fitness Age)
]
# --- 2. DAILY BLOCK (Твоя логика без изменений) ---
steps = summary.get('totalSteps', 0)
cals = int(summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0))
daily_row = [f"'{today_str}", steps, round(steps * 0.000762, 2), cals, r_hr, summary.get("bodyBatteryMostRecentValue", "")]

# --- WRITE ---
ss = gspread.authorize(Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), 
     scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])).open("Garmin_Data")

update_or_append(ss.worksheet("Morning"), today_str, morning_row)
update_or_append(ss.worksheet("Daily"), today_str, daily_row)

print(f"✅ Morning Fixed: Time={morning_ts}, Weight={weight}, Score={slp_sc}")
