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

stats = client.get_stats(today)

steps = stats.get("totalSteps", 0)
calories = stats.get("totalKilocalories", 0)
distance_km = stats.get("totalDistanceMeters", 0) / 1000

print("Garmin data:")
print("Steps:", steps)
print("Calories:", calories)
print("Distance (km):", distance_km)

# ---------- GOOGLE SHEETS ----------
print("Connecting to Google Sheets...")
creds_dict = json.loads(os.environ["GOOGLE_CREDS"])
scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(credentials)
spreadsheet = gc.open("Garmin_Data")

# Запись в Activities (то, что уже работает)
spreadsheet.worksheet("Activities").append_row([today, steps, calories, round(distance_km, 2)])

# Запись в Morning (новое!)
spreadsheet.worksheet("Morning").append_row([
    today, 
    round(weight, 1), 
    "", # Body Fat (если нет весов Index, можно оставить пустым)
    stats.get("restDetectorRestingHeartRate", 0),
    hrv,
    stats.get("bodyBatteryMostRecentValue", 0),
    sleep_score,
    round(sleep_min, 0)
])

print("✅ Sync completed successfully")
