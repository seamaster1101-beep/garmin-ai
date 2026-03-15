import os, json
from datetime import datetime
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials

# --- CONFIG ---
GARMIN_EMAIL = os.getenv("GARMIN_EMAIL")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_SHEETS_CREDS")

try:
    # 1. LOGIN
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    # 2. DATA COLLECTION
    stats = gar.get_user_summary(today_str)
    sleep = gar.get_sleep_data(today_str)
    
    # Пытаемся взять время пробуждения из сна
    try:
        wake_time = sleep.get('dailySleepDTO', {}).get('sleepEndTimeLocal')
        display_time = wake_time.replace('T', ' ')[:16] if wake_time else datetime.now().strftime("%Y-%m-%d %H:%M")
    except:
        display_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    # --- MORNING SHEET ---
    weight = round(stats.get('weight', 0) / 1000, 1) if stats.get('weight') else ""
    rhr = stats.get('restingHeartRate', "")
    bb = stats.get('bodyBatteryMostRecentValue', "")
    slp_score = sleep.get('dailySleepDTO', {}).get('score', "") if sleep else ""
    slp_sec = sleep.get('dailySleepDTO', {}).get('sleepTimeSeconds', 0) if sleep else 0
    slp_h = round(slp_sec / 3600, 1) if slp_sec > 0 else ""
    
    # Берем HRV (отдельно, чтобы не уронить скрипт)
    hrv = ""
    try:
        hrv_data = gar.get_hrv_data(today_str)
        hrv = hrv_data.get('hrvSummary', {}).get('lastNightAvg', "")
    except: pass

    morning_row = [display_time, weight, "", "", rhr, hrv, bb, slp_score, slp_h, "41", "Fixed"]

    # --- DAILY SHEET ---
    steps = stats.get('totalSteps', "")
    dist_m = stats.get('totalDistanceMeters', 0)
    dist_km = round(dist_m / 1000, 2) if dist_m else ""
    cals = stats.get('totalCalories', "")
    daily_row = [today_str, steps, dist_km, cals, rhr, bb]

    # --- ACTIVITIES SHEET ---
    activities_to_log = []
    try:
        all_acts = gar.get_activities(0, 5)
        for a in all_acts:
            start = a.get('startTimeLocal', '')
            if start.startswith(today_str):
                act_id = str(a.get('activityId'))
                row = [
                    start.replace('T', ' ')[:16], a.get('activityType', {}).get('typeKey'),
                    round(a.get('duration', 0)/3600, 2), round(a.get('distance', 0)/1000, 2),
                    a.get('averageHR', ""), a.get('maxHR', ""),
                    a.get('intensityFactor', ""), a.get('trainingLoad', ""),
                    a.get('trainingEffect', ""), a.get('calories', ""),
                    a.get('averagePower', ""), a.get('averageCadence', ""),
                    a.get('normPower', ""), a.get('trainingStressScore', ""),
                    "", act_id
                ]
                activities_to_log.append({"id": act_id, "row": row})
    except: pass

    # 3. WRITE TO GOOGLE SHEETS
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(creds).open("Garmin_Data")
    
    ss.worksheet("Morning").append_row(morning_row)
    ss.worksheet("Daily").append_row(daily_row)
    
    if activities_to_log:
        ws_a = ss.worksheet("Activities")
        exist_ids = [r[15] for r in ws_a.get_all_values() if len(r) > 15]
        for act in activities_to_log:
            if act["id"] not in exist_ids:
                ws_a.append_row(act["row"])

    print(f"Success. Data for {today_str} uploaded.")

except Exception as e:
    print(f"Error: {e}")
