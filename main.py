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
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

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
            for i, val in enumerate(row_data[1:], start=2):
                if val not in (None, "", 0, "0", 0.0, "N/A"): 
                    sheet.update_cell(found_idx, i, val)
            return "Updated"
        else:
            sheet.append_row(row_data)
            return "Appended"
    except Exception as e: return f"Err: {str(e)[:15]}"

# --- LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
except Exception as e:
    print(f"Login Fail: {e}"); exit(1)

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")

# --- СБОР ДАННЫХ ---
try:
    stats = gar.get_user_summary(today_str)
    sleep = gar.get_sleep_data(today_str)
    hrv_res = gar.get_hrv_data(today_str) or {}
    
    # 1. Возраст из профиля
    profile = gar.get_user_settings()
    birth_date = profile.get('birthDate', '1964-01-01')
    real_age = datetime.now().year - int(birth_date[:4])

    # 2. Время (07:22)
    dto = sleep.get('dailySleepDTO', {})
    raw_ts = dto.get('sleepEndTimeLocal')
    morning_ts = raw_ts.replace('T', ' ')[:16] if raw_ts else datetime.now().strftime("%Y-%m-%d %H:%M")

    # 3. Вес и Сводка
    w_data = gar.get_body_composition((now - timedelta(days=3)).strftime("%Y-%m-%d"), today_str) or {}
    weights = w_data.get('dateWeightList', [])
    weight = round(float(max(weights, key=lambda x: x.get('sampleTime', 0)).get('weight', 0)) / 1000, 1) if weights else ""
    
    r_hr = stats.get("restingHeartRate") or ""
    hrv = hrv_res.get("hrvSummary", {}).get("lastNightAvg") or ""
    slp_sc = dto.get("sleepScore") or ""
    slp_h = round(float(dto.get("sleepTimeSeconds", 0)) / 3600, 1) if dto else ""
    bb_morning = stats.get("bodyBatteryHighestValue") or stats.get("bodyBatteryMostRecentValue") or ""

    # 4. Daily (Калории)
    cals = stats.get("totalCalories", "")
    daily_row = [today_str, stats.get('totalSteps', 0), round(stats.get('totalDistanceMeters', 0)/1000, 2), cals, r_hr, stats.get("bodyBatteryMostRecentValue", "")]

    # --- AI ANALYSIS (Fitness Age) ---
    fitness_age_result = "Calculating..."
    ai_advice = ""
    if GEMINI_API_KEY:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
            prompt = (f"Атлет: {real_age} лет. Данные: вес {weight}, HRV {hrv}, покой HR {r_hr}, сон {slp_h}ч. "
                      f"Определи его Fitness Age на основе этих данных и дай короткий совет.")
            res_ai = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15).json()
            full_text = res_ai["candidates"][0]["content"]["parts"][0]["text"].strip()
            # Пытаемся выцепить цифру из ответа для колонки Fitness Age
            fitness_age_result = full_text.split('.')[0] # Первое предложение обычно содержит оценку
            ai_advice = full_text
        except: fitness_age_result = "AI Error"

    morning_row = [morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h, real_age, fitness_age_result]

    # --- ЗАПИСЬ ---
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    credentials = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(credentials).open("Garmin_Data")
    
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    
    # Activities (Вело)
    latest_acts = gar.get_activities(0, 3)
    ws_a = ss.worksheet("Activities")
    existing_ids = {r[12] for r in ws_a.get_all_values() if len(r) > 12}
    for a in latest_acts:
        if a.get('startTimeLocal', '').startswith(today_str):
            a_id = str(a.get('activityId'))
            if a_id not in existing_ids:
                row = [a.get('startTimeLocal').replace('T',' ')[:16], a.get('activityType', {}).get('typeKey'), round(a.get('duration',0)/3600,2), round(a.get('distance',0)/1000,2), a.get('averageHR'), a.get('maxHR'), "", round(float(a.get('activityTrainingLoad',0)),1), round(float(a.get('aerobicTrainingEffect',0)),1), a.get('calories'), a.get('avgPower'), "", a_id]
                ws_a.append_row(row)

    # Telegram
    if TELEGRAM_BOT_TOKEN:
        msg = f"📊 *Athlete Sync ({real_age} years)*\n\n🕒 Пробуждение: {morning_ts}\n🔥 Калории: {cals}\n🧬 Fitness Age: {fitness_age_result}\n\n🤖 {ai_advice}"
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})

    print(f"✅ Успех! Возраст: {real_age}, Fitness Age: {fitness_age_result}")

except Exception as e:
    print(f"Error: {e}")
