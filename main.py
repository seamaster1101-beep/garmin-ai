import os
import json
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

# ---------- CONFIG ----------
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")

def safe_value(val):
    """Проверка на пустые значения"""
    return val if val not in (None, "", 0) else ""

def find_hrv_for_days(client, dates):
    """Ищем HRV за несколько дней, возвращаем первый непустой"""
    for d in dates:
        try:
            data = client.get_hrv_data(d) or []
            if data and data[0].get("lastNightAvg"):
                return safe_value(data[0].get("lastNightAvg")), d
        except:
            pass
    return "", ""

def find_sleep_for_days(client, dates):
    """Ищем данные сна за несколько дней"""
    for d in dates:
        try:
            sleep = client.get_sleep_data(d) or {}
            dto = sleep.get("dailySleepDTO", {})
            score = dto.get("sleepScore")
            secs = dto.get("sleepTimeSeconds", 0)
            if score or secs > 0:
                hrs = round(secs / 3600, 1) if secs else ""
                return safe_value(score), safe_value(hrs), d
        except:
            pass
    return "", "", ""

def update_or_append(sheet, date_str, row_data):
    try:
        dates = sheet.col_values(1)
        if date_str in dates:
            idx = dates.index(date_str) + 1
            for i, val in enumerate(row_data[1:], start=2):
                if safe_value(val) != "":
                    sheet.update_cell(idx, i, val)
            return "Updated"
        else:
            sheet.append_row(row_data)
            return "Appended"
    except Exception as e:
        return f"Error: {e}"

# ---------- START ----------
try:
    garmin = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    garmin.login()
except Exception as e:
    print(f"🚨 Garmin login error: {e}")
    exit(1)

now = datetime.now()
today = now.strftime("%Y-%m-%d")
yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
day2 = (now - timedelta(days=2)).strftime("%Y-%m-%d")
debug = [f"Dates tried: {today}, {yesterday}, {day2}"]

# --- STATS ---
try:
    stats = garmin.get_stats(today) or {}
    resting_hr = safe_value(stats.get("restingHeartRate"))
    body_battery = safe_value(stats.get("bodyBatteryMostRecentValue"))
    debug.append(f"Stats: HR {resting_hr}, BB {body_battery}")
except Exception as e:
    resting_hr = ""
    body_battery = ""
    debug.append(f"StatsErr:{e}")

# --- WEIGHT ---
weight = ""
try:
    w_data = garmin.get_body_composition(yesterday, today) or {}
    if "uploads" in w_data and w_data["uploads"]:
        weight = safe_value(round(w_data["uploads"][-1].get("weight", 0)/1000, 1))
        debug.append(f"WgtFromUploads:{weight}")
    else:
        summary = garmin.get_user_summary(today) or {}
        w2 = summary.get("weight", 0)
        weight = safe_value(round(w2/1000, 1)) if w2 else ""_
