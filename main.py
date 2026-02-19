import os
import json
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

# --- CONFIG ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")

# --- Helpers ---
def safe(val):
    return val if val not in (None, "", 0, "0") else ""

def get_last_days(n=7):
    base = datetime.now()
    return [(base - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]

def update_or_append(sheet, key, row):
    dates = sheet.col_values(1)
    for i, d in enumerate(dates):
        if key in d:
            row_idx = i + 1
            for col, v in enumerate(row[1:], start=2):
                if safe(v) != "":
                    sheet.update_cell(row_idx, col, v)
            return "Updated"
    sheet.append_row(row)
    return "Appended"

# --- START ---
dates = get_last_days(7)
today = dates[0]
debug = [f"Dates scanned: {dates}"]

# LOGIN
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
except Exception as e:
    print("🚨 Garmin login failed:", e)
    exit(1)

# ------------------- MORNING (Wellness + Sleep + Weight) -------------------

# Stats
try:
    stats = gar.get_stats(today) or {}
    resting_hr = safe(stats.get("restingHeartRate"))
    body_battery = safe(stats.get("bodyBatteryMostRecentValue"))
except:
    resting_hr = ""
    body_battery = ""

# HRV (try multiple days / multiple keys)
hrv = ""
try:
    for d in dates:
        raw_hrv = gar.get_hrv_data(d) or {}
        if isinstance(raw_hrv, list) and raw_hrv and raw_hrv[0].get("lastNightAvg"):
            hrv = raw_hrv[0]["lastNightAvg"]
            break
except: pass

# Sleep Score & Hours (try multiple days)
sleep_score = ""
sleep_hrs = ""
try:
    for d in dates:
        sdata = gar.get_sleep_data(d) or {}
        dto = sdata.get("dailySleepDTO", {})
        if dto.get("sleepTimeSeconds") is not None:
            sleep_score = safe(dto.get("sleepScore"))
            sleep_hrs = round(dto.get("sleepTimeSeconds")/3600,1)
            break
except: pass

# Weight (try multiple days)
weight = ""
try:
    for d in dates:
        wc = gar.get_body_composition(d, d) or {}
        if wc.get("uploads"):
            w = wc["uploads"][-1].get("weight")
            if w:
                weight = round(w/1000,1)
                break
except: pass

morning_row = [
    f"{today} 08:00",
    weight,
    resting_hr,
    hrv,
    body_battery,
    sleep_score,
    sleep_hrs
]

debug.append(f"Morning -> Weight:{weight} HRV:{hrv} SleepScore:{sleep_score} SleepH:{sleep_hrs}")

# ------------------- DAILY (Steps / Distance / Calories) -------------------

steps = 0
distance_km = 0
daily_cals = ""
try:
    # 1) steps + distance
    st = gar.get_daily_steps(today, today)
    if st:
        steps = st[0].get("totalSteps", 0)
        distance_km = round(st[0].get("totalDistance", 0)/1000,2)

    # 2) calories from summary (best available)
    summ = gar.get_user_summary(today) or {}
    cals = summ.get("calories", 0)
    if not cals:
        cals = (summ.get("activeCalories") or 0) + (summ.get("bmrCalories") or 0)
    daily_cals = safe(cals)
except: pass

daily_row = [
    today,
    steps,
    distance_km,
    daily_cals,
    resting_hr,
    body_battery
]

debug.append(f"Daily -> Steps:{steps} Dist:{distance_km} Calories:{daily_cals}")

# ------------------- ACTIVITIES (Load + Cadence) -------------------

activities_log = []
try:
    act_list = gar.get_activities_by_date(today, today) or []
    for a in act_list:
        # Cadence
        cad_keys = [
            "averageBikingCadence", "averageCadence",
            "averageRunCadence", "averageFractionalCadence"
        ]
        cadence = ""
        for k in cad_keys:
            if a.get(k):
                cadence = a[k]
                break

        # Training Load
        load_keys = [
            "trainingLoad",
            "metabolicCartTrainingLoad",
            "trainingLoadVO2Max",
            "trainingLoadPeakImpact"
        ]
        t_load = ""
        for lk in load_keys:
            if a.get(lk):
                t_load = a[lk]
                break

        activities_log.append([
            today,
            a.get("startTimeLocal", "")[11:16],
            a.get("activityType", {}).get("typeKey", ""),
            round(a.get("duration",0)/3600,2),
            round(a.get("distance",0)/1000,2),
            a.get("averageHR",""),
            a.get("maxHR",""),
            t_load,
            safe(a.get("aerobicTrainingEffect")),
            a.get("calories",""),
            a.get("avgPower",""),
            cadence
        ])
except:
    pass

debug.append(f"Activities count: {len(activities_log)}")

# ------------------- SYNC TO SHEETS -------------------

try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    creds_obj = Credentials.from_service_account_info(
        creds,
        scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
    )
    gs = gspread.authorize(creds_obj)
    ss = gs.open("Garmin_Data")

    # Morning sheet
    update_or_append(ss.worksheet("Morning"), today, morning_row)

    # Daily sheet
    update_or_append(ss.worksheet("Daily"), today, daily_row)

    # Activities sheet
    act_sheet = ss.worksheet("Activities")
    existing_keys = {f"{r[0]}_{r[1]}_{r[2]}" for r in act_sheet.get_all_values() if len(r)>2}
    for al in activities_log:
        key = f"{al[0]}_{al[1]}_{al[2]}"
        if key not in existing_keys:
            act_sheet.append_row(al)

    # Debug Log
    log_sheet = ss.worksheet("AI_Log")
    log_sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Sync OK", json.dumps(debug)])

    print("✔ Sync Completed")
except Exception as e:
    print("❌ Sync Error:", e)
