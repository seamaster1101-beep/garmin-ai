import os
import json
from datetime import datetime
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import requests

print("🚀 Starting Garmin → Google Sheets PRO + AI analysis")

# ---------- SETTINGS ----------
HR_MAX = 165
gemini_key = os.environ.get("GEMINI_API_KEY")

# ---------- GARMIN LOGIN ----------
email = os.environ["GARMIN_EMAIL"]
password = os.environ["GARMIN_PASSWORD"]

client = Garmin(email, password)
client.login()

now = datetime.now()
today_date = now.strftime("%Y-%m-%d")

print(f"Fetching data for: {today_date}")

# ---------- DAILY STATS (С защитой от пустых данных) ----------
stats = client.get_stats(today_date)
steps = stats.get("totalSteps") or 0
daily_calories = stats.get("totalKilocalories") or 0
# Защита от None: если дистанции нет, ставим 0
raw_dist = stats.get("totalDistanceMeters") or 0
daily_distance_km = round(raw_dist / 1000, 2)
resting_hr = stats.get("restingHeartRate") or 0
body_battery = stats.get("bodyBatteryMostRecentValue") or 0

# ---------- LAST ACTIVITY ----------
try:
    activities = client.get_activities(0, 1)
    if activities and activities[0]['startTimeLocal'].startswith(today_date):
        last_act = activities[0]
    else:
        last_act = None
except Exception as e:
    print(f"Activity fetch error: {e}")
    last_act = None

# ---------- HEALTH ----------
try:
    body_data = client.get_body_composition(today_date)
    weight = round(body_data.get('totalWeight', 0) / 1000, 1) if body_data and body_data.get('totalWeight') else ""
    hrv_data = client.get_hrv_data(today_date)
    hrv = hrv_data[0].get('lastNightAvg', "") if hrv_data else ""
    sleep_data = client.get_sleep_data(today_date)
    sleep_score = sleep_data.get('dailySleepDTO', {}).get('sleepScore', "")
    s_sec = sleep_data.get('dailySleepDTO', {}).get('sleepTimeSeconds') or 0
    sleep_min = round(s_sec / 60, 0) if s_sec > 0 else ""
except:
    weight, hrv, sleep_score, sleep_min = "", "", "", ""

# ---------- AI ANALYSIS BLOCK (Исправленный URL v1) ----------
ai_advice = "Анализ не выполнен"
if gemini_key:
    try:
        api_key_clean = str(gemini_key).strip()
        workout_info = f"Тренировка: {last_act['activityType']['typeKey']}, TE: {last_act.get('trainingEffect')}" if last_act else "Тренировок не было"
        
        user_prompt = (f"Проанализируй показатели за сегодня ({today_date}): "
                       f"Сон: {sleep_score}/100, HRV: {hrv}, Пульс покоя: {resting_hr}, "
                       f"Body Battery: {body_battery}, Шаги: {steps}. {workout_info}. "
                       f"Дай краткую оценку восстановления и совет на завтра (2 предложения).")

        # Используем стабильный URL v1
        url = f"https://generativelanguage.googleapis.com/v1/models/gemini-1.5-flash:generateContent?key={api_key_clean}"
        headers = {'Content-Type': 'application/json'}
        payload = {
            "contents": [{"parts": [{"text": user_prompt}]}],
            "generationConfig": {"maxOutputTokens": 300, "temperature": 0.7}
        }

        response = requests.post(url, headers=headers, json=payload, timeout=15)
        
        if response.status_code == 200:
            result = response.json()
            ai_advice = result['candidates'][0]['content']['parts'][0]['text']
        else:
            ai_advice = f"API Error {response.status_code}: {response.reason}"
            
    except Exception as e:
        ai_advice = f"Local Error: {str(e)[:50]}"

# ---------- GOOGLE SHEETS ----------
creds_dict = json.loads(os.environ["GOOGLE_CREDS"])
credentials = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
gc = gspread.authorize(credentials)
spreadsheet = gc.open("Garmin_Data")

# 1. Лист DAILY
daily_sheet = spreadsheet.worksheet("Daily")
daily_sheet.append_row([today_date, steps, daily_distance_km, daily_calories, resting_hr, body_battery])

# 2. Лист ACTIVITIES
if last_act:
    act_sheet = spreadsheet.worksheet("Activities")
    avg_hr = last_act.get('averageHR', 0)
    hr_intensity = round(avg_hr / HR_MAX, 2) if avg_hr else ""
    te = last_act.get('trainingEffect', 0)
    
    if te:
        if te < 2.0: session_type = "Recovery"
        elif te < 3.0: session_type = "Base"
        elif te < 4.0: session_type = "Tempo"
        else: session_type = "HIIT"
    else: session_type = ""

    act_sheet.append_row([
        today_date, last_act['startTimeLocal'][11:16], last_act['activityType']['typeKey'].capitalize(),
        round(last_act['duration'] / 3600, 2), round(last_act.get('distance', 0) / 1000, 2),
        avg_hr, last_act.get('maxHR', ""), last_act.get('trainingLoad', ""),
        te, last_act.get('calories', ""), last_act.get('avgPower', ""),
        last_act.get('averageRunningCadence', ""), hr_intensity, session_type
    ])

# 3. Лист MORNING
morning_sheet = spreadsheet.worksheet("Morning")
morning_sheet.append_row([today_date, weight, resting_hr, hrv, body_battery, sleep_score, sleep_min])

# 4. Лист AI_LOG
spreadsheet.worksheet("AI_Log").append_row([now.strftime("%Y-%m-%d %H:%M"), "Sync Complete", ai_advice])

print(f"✅ Sync Successful! AI Advice: {ai_advice[:50]}...")
