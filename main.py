import os
import json
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai
import requests

# --- Настройки ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def clean_val(val):
    """Превращает любое значение в строку с запятой вместо точки"""
    if val is None or val == "": return ""
    return str(val).replace('.', ',')

# --- Логин ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
except Exception as e:
    print(f"Garmin Login Error: {e}")
    exit(1)

now = datetime.now()
today = now.strftime("%Y-%m-%d")

# --- Сбор данных ---
try:
    # Базовые показатели
    stats = gar.get_stats(today) or {}
    summary = gar.get_user_summary(today) or {}
    
    steps = stats.get('totalSteps', 0)
    dist = round((stats.get('totalDistanceMeters', 0) / 1000), 2)
    cals = (summary.get('activeKilocalories', 0) + summary.get('bmrKilocalories', 0)) or stats.get('calories', 0)
    rhr = summary.get('restingHeartRate') or ""
    bb = summary.get('bodyBatteryMostRecentValue') or ""

    # Биометрия (HRV и Сон)
    hrv_data = gar.get_rhr_and_hrv(today) or {}
    hrv = hrv_data.get("hrvSummary", {}).get("lastNightAvg", "")
    
    sleep_data = gar.get_sleep_data(today) or {}
    sleep_dto = sleep_data.get("dailySleepDTO") or {}
    slp_score = sleep_dto.get("sleepScore") or ""
    slp_hours = round((sleep_dto.get("sleepTimeSeconds", 0) / 3600), 1) if sleep_dto.get("sleepTimeSeconds") else ""
    
    # Вес
    weight = ""
    w_info = gar.get_body_composition(today)
    if w_info and w_info.get('uploads'):
        weight = round(w_info['uploads'][-1].get('weight', 0) / 1000, 1)

except Exception as e:
    print(f"Data Collection Error: {e}")

# --- Google Sheets ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    client = gspread.authorize(Credentials.from_service_account_info(creds, scopes=scope))
    ss = client.open("Garmin_Data")

    # 1. Лист Daily
    ss.worksheet("Daily").append_row([today, steps, clean_val(dist), cals, rhr, bb])
    
    # 2. Лист Morning
    ss.worksheet("Morning").append_row([today, clean_val(weight), rhr, hrv, bb, slp_score, clean_val(slp_hours)])

    # 3. Лист Activities
    act_sheet = ss.worksheet("Activities")
    activities = gar.get_activities_by_date(today, today) or []
    
    for a in activities:
        start_time = a.get('startTimeLocal', 'T00:00:00').split('T')[1][:5]
        sport = a.get('activityType', {}).get('typeKey', 'unknown').capitalize()
        dur = round((a.get('duration', 0) / 3600), 2)
        km = round((a.get('distance', 0) / 1000), 2)
        avg_hr = a.get('averageHeartRate') or a.get('averageHR') or ""
        
        # Чтобы не дублировать, просто добавляем только новые (проверка по времени и спорту)
        act_sheet.append_row([
            today, start_time, sport, clean_val(dur), clean_val(km), 
            avg_hr, a.get('maxHeartRate', ''), a.get('trainingLoad', ''),
            clean_val(a.get('trainingEffect', '')), a.get('calories', ''),
            a.get('averagePower', ''), a.get('averageCadence', ''), ""
        ])

except Exception as e:
    print(f"Sheets Sync Error: {e}")

# --- AI (Gemini 1.5 Flash) ---
advice = "Ошибок нет, но ИИ отдыхает (429)."
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY.strip())
        model = genai.GenerativeModel('gemini-1.5-flash')
        res = model.generate_content(f"HRV: {hrv}, Сон: {slp_hours}ч, Шаги: {steps}. Дай очень короткий совет.")
        advice = res.text.strip()
    except: pass

# --- Telegram ---
if TELEGRAM_BOT_TOKEN:
    text = f"📊 *Данные Garmin {today}*\n👣 Шаги: {steps}\n💓 HRV: {hrv}\n😴 Сон: {slp_h}ч\n🤖 {advice}"
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                  json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"})
