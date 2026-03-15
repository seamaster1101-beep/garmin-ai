import os, json, requests
from datetime import datetime
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

# --- CONFIG ---
GARMIN_EMAIL = os.getenv("GARMIN_EMAIL")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_SHEETS_CREDS")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def update_or_append(ws, date_key, row_data):
    try:
        cells = ws.col_values(1)
        # Ищем совпадение только по дате (первые 10 символов YYYY-MM-DD)
        date_only = date_key[:10]
        found_idx = -1
        for i, val in enumerate(cells):
            if val.startswith(date_only):
                found_idx = i + 1
                break
        
        if found_idx > 0:
            for i, val in enumerate(row_data):
                if val is not None and val != "":
                    ws.update_cell(found_idx, i + 1, val)
        else:
            ws.append_row(row_data)
    except Exception as e: print(f"Sheets error: {e}")

try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    # 1. Сбор данных
    stats = gar.get_user_summary(today_str)
    try: comp = gar.get_body_composition(today_str)
    except: comp = {}
    try: hrv_data = gar.get_hrv_data(today_str)
    except: hrv_data = {}
    try: sleep = gar.get_sleep_data(today_str)
    except: sleep = {}

    # Определяем время (Весы -> Сон -> Текущее)
    last_weight_time = comp.get('totalDailyLeaf', {}).get('calendarDate') # Это обычно полночь, не катит
    # Лучше вытащим время из последней записи веса
    weight_val = comp.get('totalDailyLeaf', {}).get('weight', 0)
    
    # По умолчанию время - сейчас, но попробуем найти время пробуждения
    wake_time = sleep.get('dailySleepDTO', {}).get('sleepEndTimeLocal')
    if wake_time:
        display_time = datetime.fromisoformat(wake_time).strftime("%Y-%m-%d %H:%M")
    else:
        display_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    weight = round(stats.get('weight', 0) / 1000, 1) if stats.get('weight') else ""
    fat = comp.get('totalDailyLeaf', {}).get('bodyFat', "")
    muscle = comp.get('totalDailyLeaf', {}).get('muscleMass')
    if muscle: muscle = round(muscle / 1000, 1)
    
    rhr = stats.get('restingHeartRate', "")
    hrv = hrv_data.get('hrvSummary', {}).get('lastNightAvg', "")
    bb = stats.get('bodyBatteryMostRecentValue', "")
    slp_score = sleep.get('dailySleepDTO', {}).get('score', "")
    slp_h = round(sleep.get('dailySleepDTO', {}).get('sleepTimeSeconds', 0) / 3600, 1) if sleep.get('dailySleepDTO') else ""
    
    # Возраст (из профиля)
    user_settings = gar.get_user_settings()
    birth_date = user_settings.get('birthDate', '1984-01-01')
    age = datetime.now().year - int(birth_date[:4])

    # Строка Morning (A-K)
    morning_row = [display_time, weight, fat, muscle, rhr, hrv, bb, slp_score, slp_h, age, "AI Calculation"]

    # 2. Активности
    all_acts = gar.get_activities(0, 5)
    activities_to_log = []
    for a in all_acts:
        start
