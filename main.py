import os
import json
from datetime import datetime
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials

print("Starting Garmin → Google Sheets sync")

# ---------- GARMIN ----------
email = os.environ["GARMIN_EMAIL"]
password = os.environ["GARMIN_PASSWORD"]

client = Garmin(email, password)
client.login()

now = datetime.now()
today_date = now.strftime("%Y-%m-%d")
start_time = now.strftime("%H:%M")

print("Fetching data for:", today_date)

# ---------- DAILY STATS ----------
stats = client.get_stats(today_date)

steps = stats.get("totalSteps", 0)
daily_calories = stats.get("totalKilocalories", 0)
daily_distance_km = stats.get("totalDistanceMeters", 0) / 1000
resting_hr = stats.get("restingHeartRate", 0)
body_battery = stats.get("bodyBatteryMostRecentValue", 0)

# ---------- LAST ACTIVITY ----------
try:
    activities = client.get_activities(0, 1)
    if activities and activities[0]['startTimeLocal'].startswith(today_date):
        last_act = activities[0]
    else:
        last_act = None
except:
    last_act = None

# ---------- HEALTH / MORNING ----------
try:
    body_data = client.get_body_composition(today_date)
    weight = body_data.get('totalWeight', 0) / 1000 if body_data else ""

    hrv_data = client.get_hrv_data(today_date)
    hrv = hrv_data[0].get('lastNightAvg', "") if hrv_data else ""

    sleep_data = client.get_sleep_data(today_date)
    sleep_score = sleep_data.get('dailySleepDTO', {}).get('sleepScore', "")
    sleep_min = sleep_data.get('dailySleepDTO', {}).get('sleepTimeSeconds', 0) / 60
except:
    weight, hrv, sleep_score, sleep_min = "", "", "", ""

# ---------- GOOGLE SHEETS ----------
creds_dict = json.loads(os.environ["GOOGLE_CREDS"])
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(credentials)

spreadsheet = gc.open("Garmin_Data")

# ---------- WRITE DAILY ----------
daily_sheet = spreadsheet.worksheet("Daily")
daily_sheet.append_row([
    today_date,
    steps,
    round(daily_distance_km, 2),
    daily_calories,
    resting_hr,
    body_battery
])

# ---------- WRITE ACTIVITY ----------
if last_act:
    activities_sheet = spreadsheet.worksheet("Activities")

    duration_hr = round(last_act['duration'] / 3600, 2)

    activities_sheet.append_row([
        today_date,                                    # Date
        last_act['startTimeLocal'][11:16],              # Start_Time
        last_act['activityType']['typeKey'].capitalize(),  # Sport
        duration_hr,                                    # Duration_hr
        round(last_act.get('distance', 0) / 1000, 2),   # Distance_km
        last_act.get('averageHR', ""),                  # Avg_HR
        last_act.get('maxHR', ""),                      # Max_HR
        last_act.get('trainingLoad', ""),               # Training_Load
        last_act.get('trainingEffect', ""),             # Training_Effect
        last_act.get('calories', ""),                   # Calories
        last_act.get('avgPower', ""),                   # Avg_Power
        last_act.get('averageRunningCadence', ""),      # Cadence
        "",                                             # HR_Intensity (formula)
        ""                                              # Session_Type (manual)
    ])

# ---------- WRITE MORNING ----------
morning_sheet = spreadsheet.worksheet("Morning")
morning_sheet.append_row([
    today_date,
    round(weight, 1) if weight else "",
    resting_hr,
    hrv,
    body_battery,
    sleep_score,
    round(sleep_min, 0) if sleep_min else ""
])

# ---------- LOG ----------
spreadsheet.worksheet("AI_Log").append_row([
    now.strftime("%Y-%m-%d %H:%M"),
    "Sync successful",
    "Activities, Daily, Morning updated"
])

print("✅ Sync completed successfully!")
