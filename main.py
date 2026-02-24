import os
import json
from datetime import datetime
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import requests

# --- 1. CONFIG (Берем данные из секретов GitHub) ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def clean(val):
    """Превращает точки в запятые для Google Sheets"""
    if val is None or val == "" or val == 0: return ""
    return str(val).replace('.', ',')

# --- 2. GOOGLE SHEETS AUTH ---
try:
    if not GOOGLE_CREDS_JSON:
        raise ValueError("Секрет GOOGLE_CREDS пуст или не найден!")
    
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
    
    gc = gspread.authorize(creds)
    # Открываем таблицу по имени. Убедись, что имя в кавычках совпадает с реальностью!
    ss = gc.open("Garmin_Data") 
    print("✅ Успех: Подключено к Google Sheets")
except Exception as e:
    print(f"❌ Ошибка Google Auth: {e}")
    exit(1)

# --- 3. GARMIN LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
    print("✅ Успех: Авторизация в Garmin прошла")
except Exception as e:
    print(f"❌ Ошибка Garmin: Проверь логин/пароль! Детали: {e}")
    exit(1)

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")

# --- 4. DATA COLLECTION (Биометрия) ---
r_hr, hrv, bb_m, slp_h, steps, weight = "", "", "", "", "", ""
try:
    summary = gar.get_user_summary(today_str) or {}
    stats = gar.get_stats(today_str) or {}
    
    r_hr = summary.get("restingHeartRate") or stats.get("restingHeartRate") or ""
    hrv = stats.get("lastNightAvgHrv") or stats.get("allDayAvgHrv") or ""
    bb_m = summary.get("bodyBatteryHighestValue") or ""
    steps = stats.get("totalSteps") or ""
    
    # Сон
    s_data = gar.get_sleep_data(today_str) or {}
    dto = s_data.get("dailySleepDTO") or {}
    slp_h = round(dto.get("sleepTimeSeconds", 0) / 3600, 1) if dto.get("sleepTimeSeconds") else ""

    # Вес
    w_data = gar.get_body_composition(today_str)
    if w_data and w_data.get('uploads'):
        weight = round(w_data['uploads'][-1].get('weight', 0) / 1000, 1)
    
    print(f"✅ Биометрия собрана: HR={r_hr}, Steps={steps}")
except Exception as e:
    print(f"⚠️ Ошибка сбора биометрии: {e}")

# --- 5. ACTIVITIES (Тренировки) ---
try:
    acts = gar.get_activities_by_date(today_str, today_str)
    act_sheet = ss.worksheet("Activities")
    existing_rows = act_sheet.get_all_values()

    activities_to_log = []
    for a in acts:
        act_date = a.get("startTimeLocal", "")[:10]
        act_time = a.get("startTimeLocal", "")[11:16]
        sport = a.get('activityType', {}).get('typeKey', '').capitalize()

        # Проверка на дубликаты
        if any(r[0] == act_date and r[1] == act_time and r[2] == sport for r in existing_rows):
            continue

        avg_hr = a.get('averageHR') or a.get('averageHeartRate') or 0
        
        # Интенсивность (Low / Moderate / High)
        intensity = "N/A"
        try:
            if avg_hr and r_hr and float(r_hr) > 0:
                res = (float(avg_hr) - float(r_hr)) / (185 - float(r_hr))
                if res < 0.5: intensity = "Low"
                elif res < 0.75: intensity = "Moderate"
                else: intensity = "High"
        except: pass

        cad = (a.get('averageBikingCadence') or a.get('averageRunCadence') or a.get('averageCadence') or "")

        row
