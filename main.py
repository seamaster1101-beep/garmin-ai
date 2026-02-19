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

def safe(v): 
    return v if v not in (None, "", 0, "0") else ""

def get_dates(n=7):
    base = datetime.now()
    return [(base - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]

dates = get_dates(7)
today = dates[0]
debug = []

# --- LOGIN GARMIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
    debug.append("Garmin login OK")
except Exception as e:
    print("Login failed:", e)
    exit(1)

# --- MORNING DATA ---
rest_hr = ""
body_batt = ""
hrv = ""
sleep_score = ""
sleep_hrs = ""
weight = ""

try:
    # RESTING + BODY BATTERY
    stats = gar.get_stats(today) or {}
    rest_hr = safe(stats.get("restingHeartRate"))
    body_batt = safe(stats.get("bodyBatteryMostRecentValue"))
    debug.append(f"Stats fetched: HR {rest_hr}, BB {body_batt}")

    # SLEEP + HRV
    for d in dates:
        s = gar.get_sleep_data(d) or {}
        dto = s.get("dailySleepDTO", {})
        if dto:
            sleep_score = safe(dto.get("sleepScore"))
            sleep_hrs = round(dto.get("sleepTimeSeconds",0)/3600,1) if dto.get("sleepTimeSeconds") else ""
            # HRV из сна
            hrv = safe(dto.get("hrvAvg")) or safe(s.get("hrv")) or safe(s.get("averageHrv"))
            if sleep_hrs:
                debug.append(f"Sleep found {d} => Score {sleep_score}, hrs {sleep_hrs}, HRV {hrv}")
                break

    # WEIGHT from Index
    # Garmin specific endpoint
    try:
        w_data = gar.get_weight_data(today) or {}
        if w_data and w_data.get("weight"):
            weight = round(w_data["weight"]/1000,1)
            debug.append(f"Weight Index {weight}")
    except:
        debug.append("Weight Index not found")
except Exception as e:
    debug.append(f"Morning parse error: {e}")

# --- DAILY STATS ---
steps = 0
dist_km = 0
daily_cals = ""

try:
    st = gar.get_daily_steps(today, today) or []
    if st:
        steps = st[0].get("totalSteps",0)
        dist_km = round(st[0].get("totalDistance",0)/1000,2)

    # Try to get daily total calories
    try:
        cal_stats = gar.get_stats_calories_breakdown(today) or {}
        daily_cals = safe(cal_stats.get("totalCalories"))
        debug.append(f"Calories breakdown {daily_cals}")
    except:
        # fallback
        summary = gar.get_user_summary(today) or {}
        daily_cals = safe(summary.get("activeCalories") or summary.get("calories"))
        debug.append(f"Calories summary {daily_cals}")
except Exception as e:
    debug.append(f"Daily parse error: {e}")

# --- ACTIVITIES (with details) ---
activities = []
try:
    raw_acts = gar.get_activities_by_date(today, today) or []
    for a in raw_acts:
        act_id = a.get("activityId")
        detail = {}
        try:
            detail = gar.get_activity_details(act_id) or {}
        except:
            detail = {}

        # Cadence
        cadence = safe(
            detail.get("averageBikingCadence") or
            detail.get("averageRunCadence") or
            detail.get("averageCadence") or
            detail.get("averageFractionalCadence")
        )

        # Training Load
        t_load = safe(detail.get("trainingLoad") or detail.get("trainingLoadVO2Max") or detail.get("metabolicCartTrainingLoad"))

        activities.append([
            today,
            a.get("startTimeLocal","")[11:16],
            a.get("activityType",{}).get("typeKey",""),
            round(a.get("duration",0)/3600,2),
            round(a.get("distance",0)/1000,2),
            a.get("averageHR",""),
            a.get("maxHR",""),
            t_load,
            safe(detail.get("aerobicTrainingEffect")),
            a.get("calories",""),
            safe(detail.get("avgPower")),
            cadence
        ])
    debug.append(f"Activities fetched {len(activities)}")
except Exception as e:
    debug.append(f"Activities error: {e}")

# --- WRITE TO GOOGLE SHEETS ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    creds_obj = Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"])
    gs = gspread.authorize(creds_obj)
    ss = gs.open("Garmin_Data")

    # Morning
    msheet = ss.worksheet("Morning")
    msheet.append_row([today, weight, rest_hr, hrv, body_batt, sleep_score, sleep_hrs])

    # Daily
    ds = ss.worksheet("Daily")
    ds.append_row([today, steps, dist_km, daily_cals, rest_hr, body_batt])

    # Activities
    act_sheet = ss.worksheet("Activities")
    existing = {f"{r[0]}_{r[1]}_{r[2]}" for r in act_sheet.get_all_values() if len(r)>2}
    for act in activities:
        key = f"{act[0]}_{act[1]}_{act[2]}"
        if key not in existing:
            act_sheet.append_row(act)

    # Log
    log_sheet = ss.worksheet("AI_Log")
    log_sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "OK", json.dumps(debug)])

    print("✔ DONE")
except Exception as e:
    print("Sheets Error:", e)
