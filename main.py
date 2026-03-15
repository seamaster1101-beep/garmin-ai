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

# --- 2. ВЕС, ЖИР, МЫШЦЫ (На основе твоего лога S2) ---
weight, fat, muscle = "", "", ""
try:
    w_data = gar.get_body_composition((now - timedelta(days=3)).strftime("%Y-%m-%d"), today_str) or {}
    weights = w_data.get('dateWeightList', [])
    if weights:
        # Берем самый свежий замер (за 15.03)
        actual_entry = max(weights, key=lambda x: x.get('sampleTime', x.get('date', 0)))
        
        # Вес: 88080.0 -> 88.1
        weight = round(float(actual_entry.get('weight', 0)) / 1000, 1)
        
        # Жир: 18.3
        fat = actual_entry.get('bodyFat', "")
        
        # Мышцы: 32500 -> 32.5
        raw_m = actual_entry.get('muscleMass')
        if raw_m:
            muscle = round(float(raw_m) / 1000, 1)
except Exception as e:
    print(f"Ошибка парсинга весов: {e}")

# --- 3. СОН И ВРЕМЯ (Твой рабочий утренний алгоритм) ---
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
morning_ts = f"{today_str} 08:00"
slp_sc, slp_h = "", ""

for d in [today_str, yesterday_str]:
    try:
        sleep_data = gar.get_sleep_data(d) or {}
        dto = sleep_data.get("dailySleepDTO") or {}
        if dto and dto.get("sleepTimeSeconds", 0) > 0:
            slp_h = round(float(dto.get("sleepTimeSeconds")) / 3600, 1)
            # Тот самый поиск Score, который у тебя работал
            scores = dto.get("sleepScores") or {}
            slp_sc = scores.get("overall", {}).get("value") or dto.get("sleepScore") or ""
            
            raw_ts = dto.get("sleepEndTimestampLocal")
            if raw_ts:
                if isinstance(raw_ts, (int, float)):
                    morning_ts = datetime.fromtimestamp(raw_ts / 1000).strftime("%Y-%m-%d %H:%M")
                else:
                    morning_ts = str(raw_ts).replace("T", " ")[:16]
            break # Нашли данные — выходим из цикла
    except:
        continue

# --- 4. FITNESS AGE (Проверенный путь v1beta) ---
fit_age = ""
if GEMINI_API_KEY and hrv:
    try:
        # Корректный URL для 1.5-flash
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{
                "parts": [{"text": f"User 62y, HRV {hrv}, RHR {r_hr}. Оцени фитнес-возраст. Выдай ТОЛЬКО одно число."}]
            }]
        }
        res = requests.post(url, json=payload, timeout=15).json()
        
        # Проверка структуры ответа
        if 'candidates' in res and res['candidates']:
            raw_text = res["candidates"][0]["content"]["parts"][0]["text"]
            fit_age = ''.join(filter(str.isdigit, raw_text))
        else:
            print(f"Gemini API Response Error: {res}")
            fit_age = "Err_API"
    except Exception as e:
        print(f"General error in Gemini block: {e}")
        fit_age = "Err_Gen"
        
# --- 5. ФОРМИРОВАНИЕ СТРОК ---
# A:Date(1), B:Weight(2), C:Fat(3), D:Muscle(4), E:RHR(5), F:HRV(6), G:BB(7), H:Score(8), I:Hours(9), J:Age(10), K:FitAge(11)
morning_row = [
    f"'{morning_ts}", 
    weight, 
    fat,        # Теперь здесь данные из actual_entry.get('bodyFat')
    muscle,     # Теперь здесь данные из actual_entry.get('muscleMass')
    r_hr, 
    hrv, 
    summary.get("bodyBatteryHighestValue", ""), 
    slp_sc, 
    slp_h, 
    62, 
    fit_age
]

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
