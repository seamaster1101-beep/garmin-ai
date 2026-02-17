import os
import json
from datetime import date
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

today = date.today().isoformat()
print("Fetching stats for:", today)

# 1. Основные активности
stats = client.get_stats(today)
steps = stats.get("totalSteps", 0)
calories = stats.get("totalKilocalories", 0)
distance_km = stats.get("totalDistanceMeters", 0) / 1000

# 2. Данные для листа Morning (Вес, Сон, HRV)
print("Fetching health stats...")
try:
    # Пытаемся получить вес и HRV
    body_data = client.get_body_composition(today)
    weight = body_data.get('totalWeight', 0) / 1000 if body_data else 0
    
    hrv_data = client.get_hrv_data(today)
    hrv = hrv_data[0].get('lastNightAvg', 0) if hrv_data else 0
    
    sleep_data = client.get_sleep_data(today)
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

# 1. Запись в Activities
print("Updating Activities...")
spreadsheet.worksheet("Activities").append_row([today, steps, calories, round(distance_km, 2)])

# 2. Запись в Morning (image_3fd624.png)
print("Updating Morning...")
spreadsheet.worksheet("Morning").append_row([
    today, 
    round(weight, 1) if weight else "", 
    "", # Body_Fat
    stats.get("restDetectorRestingHeartRate", 0),
    hrv if hrv else "",
    stats.get("bodyBatteryMostRecentValue", 0),
    sleep_score if sleep_score else "",
    round(sleep_min, 0) if sleep_min else ""
])

# 3. Запись в AI_Log (Для будущего анализа)
# Пока просто фиксируем факт синхронизации
print("Updating AI_Log...")
spreadsheet.worksheet("AI_Log").append_row([today, "Auto-Sync successful", "All data processed"])

print("✅ Sync completed successfully")
