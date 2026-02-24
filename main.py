import os
import json
from datetime import datetime
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import requests

# --- 1. CONFIG ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def clean(val):
    if val is None or val == "" or val == 0: return ""
    return str(val).replace('.', ',')

# --- 2. GOOGLE SHEETS AUTH ---
try:
    if not GOOGLE_CREDS_JSON:
        raise ValueError("Секрет GOOGLE_CREDS пуст!")
    
    # Пытаемся распарсить JSON. Если тут ошибка - значит в секретах мусор.
    try:
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
    except json.JSONDecodeError as e:
        print(f"❌ Ошибка: В секрете GOOGLE_CREDS невалидный JSON! Проверь копирование. Детали: {e}")
        exit(1)

    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
    gc = gspread.authorize(creds)
    ss = gc.open("Garmin_Data") 
    print("✅ Успех: Google Sheets подключен")
except Exception as e:
    print(f"❌ Критическая ошибка Google Auth: {str(e)}")
    exit(1)

# --- 3. GARMIN LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
    print("✅ Успех: Garmin подключен")
except Exception as e:
    print(f"❌ Ошибка Garmin: {e}")
    exit(1)

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")

# --- 4. DATA COLLECTION ---
r_hr, hrv, bb_m, slp_h, steps, weight = "", "", "", "", "", ""
try:
    summary = gar.get_user_summary(today_str) or {}
    stats = gar.get_stats(today_str) or {}
    r_hr = summary.get("restingHeartRate") or stats.get("restingHeartRate") or ""
    hrv = stats.get("lastNightAvgHrv") or stats.get("allDayAvgHrv") or ""
    bb_m = summary.get("bodyBatteryHighestValue") or ""
    steps = stats.get("totalSteps") or ""
    
    s_data = gar.get_sleep_data(today_str) or {}
    dto = s_data.get("dailySleepDTO") or {}
    slp_h = round(dto.get("sleepTimeSeconds", 0) / 3600, 1) if dto.get("sleepTimeSeconds") else ""

    w_data = gar.get_body_composition(today_str)
    if w_data and w_data.get('uploads'):
        weight = round(w_data['uploads'][-1].get('weight', 0) / 1000, 1)
except Exception as e:
    print(f"⚠️ Ошибка сбора данных: {e}")

# --- 5. ACTIVITIES ---
try:
    acts = gar.get_activities_by_date(today_str, today_str)
    act_sheet = ss.worksheet("Activities")
    existing_rows = act_sheet.get_all_values()

    for a in acts:
        act_date = a.get("startTimeLocal", "")[:10]
        act_time = a.get("startTimeLocal", "")[11:16]
        sport = a.get('activityType', {}).get('typeKey', '').capitalize()

        if any(r[0] == act_date and r[1] == act_time and r[2] == sport for r in existing_rows):
            continue

        avg_hr = a.get('averageHR') or a.get('averageHeartRate') or 0
        intensity = "N/A"
        if avg_hr and r_hr and str(r_hr).isdigit():
            res = (float(avg_hr) - float(r_hr)) / (185 - float(r_hr))
            intensity = "Low" if res < 0.5 else ("Moderate" if res < 0.75 else "High")

        row = [
            act_date, act_time, sport, 
            clean(round(a.get('duration', 0) / 3600, 2)), 
            clean(round(a.get('distance', 0) / 1000, 2)),
            avg_hr, a.get('maxHR') or "", intensity,
            a.get('trainingLoad') or "", 
            clean(round(float(a.get('aerobicTrainingEffect', 0)), 1)),
            a.get('calories', ""), a.get('averagePower', ""), 
            (a.get('averageBikingCadence') or a.get('averageRunCadence') or "")
        ]
        act_sheet.append_row(row)
    print("✅ Активности обновлены")
except Exception as e:
    print(f"⚠️ Ошибка в Activities: {e}")

# --- 6. FINAL SYNC ---
try:
    ss.worksheet("Daily").append_row([today_str, steps, "", "", r_hr, ""])
    ss.worksheet("Morning").append_row([today_str, clean(weight), r_hr, hrv, bb_m, "", clean(slp_h)])
    print("✅ Биометрия записана")
except Exception as e:
    print(f"⚠️ Ошибка записи в таблицу: {e}")

# --- 7. TELEGRAM ---
if TELEGRAM_BOT_TOKEN:
    try:
        msg = f"📊 *Sync {today_str}*\n👣 Шаги: {steps}\n💓 HR: {r_hr}\n🌙 Сон: {slp_h}ч"
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
        print("✅ Telegram отправлен")
    except:
        print("⚠️ Ошибка Telegram")
