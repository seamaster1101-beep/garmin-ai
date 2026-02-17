import os
import json
from datetime import datetime # Изменили импорт для времени
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials

print("Starting Garmin → Google Sheets sync")

# ---------- GARMIN ----------
email = os.environ["GARMIN_EMAIL"]
password = os.environ["GARMIN_PASSWORD"]

print("Logging into Garmin...")
client = Garmin(email, password)
client.login()

# Теперь здесь будет и дата, и время (например, 2026-02-17 21:45)
today = datetime.now().strftime("%Y-%m-%d %H:%M")
print("Fetching stats for:", today)

# 1. Основная статистика дня
stats = client.get_stats(datetime.now().date().isoformat())
steps = stats.get("totalSteps", 0)
daily_calories = stats.get("totalKilocalories", 0)
daily_distance_km = stats.get("totalDistanceMeters", 0) / 1000

# 2. Проверка последней тренировки за сегодня
try:
    activities = client.get_activities(0, 1)
    # Сравниваем только дату для поиска тренировки
    if activities and activities[0]['startTimeLocal'].startswith(datetime.now().date().isoformat()):
        last_act = activities[0]
        print("Workout found!")
    else:
        last_act = None
except Exception as e:
    print(f"Activity fetch failed: {e}")
    last_act = None

# 3. Данные здоровья (Morning)
print("Fetching health stats...")
current_date_str = datetime.now().date().isoformat()
try:
    body_data = client.get_body_composition(current_date_str)
    weight = body_data.get('totalWeight', 0) / 1000 if body_data else 0
    hrv_data = client.get_hrv_data(current_date_str)
    hrv = hrv_data[0].get('lastNightAvg', 0) if hrv_data else 0
    sleep_data = client.get_sleep_data(current_date_str)
    sleep_score = sleep_data.get('dailySleepDTO', {}).get('sleepScore', 0)
    sleep_min = sleep_data.get('dailySleepDTO', {}).get('sleepTimeSeconds', 0) / 60
except Exception as e:
    print(f"Health data fetch failed: {e}")
    weight, hrv, sleep_score, sleep_min = 0, 0, 0, 0

# ---------- GOOGLE SHEETS ----------
print("Connecting to Google Sheets...")
creds_dict = json.loads(os.environ["GOOGLE_CREDS"])
scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(credentials)
spreadsheet = gc.open("Garmin_Data")

# 1. Запись в Activities (A-L)
act_sheet = spreadsheet.worksheet("Activities")
act_sheet.append_row([
    today, "Daily_Steps", "", round(daily_distance_km, 2), "", "", "", "", daily_calories, "", "", steps
])

if last_act:
    act_sheet.append_row([
        today, last_act['activityType']['typeKey'].capitalize(), 
        round(last_act['duration']/60, 1), round(last_act['distance']/1000, 2),
        round(last_act.get('averageHR', 0)), round(last_act.get('maxHR', 0)),
        last_act.get('trainingEffect', ""), last_act.get('trainingLoad', ""),
        last_act.get('calories', 0), last_act.get('avgPower', ""),
        last_act.get('averageRunningCadence', ""), ""
    ])

# 2. Запись в Morning
spreadsheet.worksheet("Morning").append_row([
    today, round(weight, 1) if weight else "", "", 
    stats.get("restDetectorRestingHeartRate", 0), hrv if hrv else "",
    stats.get("bodyBatteryMostRecentValue", 0), sleep_score if sleep_score else "",
    round(sleep_min, 0) if sleep_min else ""
])

# 3. Запись в AI_Log
spreadsheet.worksheet("AI_Log").append_row([today, "Sync successful", "Full data packet updated"])

print("✅ Sync completed successfully with timestamp!")
