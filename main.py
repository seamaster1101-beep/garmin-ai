import os
import json
import time
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import requests
import traceback

# --- CONFIG ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def update_or_append(sheet, date_str, row_data):
    try:
        col_values = sheet.col_values(1)
        search_date = date_str.split(' ')[0]
        found_idx = -1
        for i, val in enumerate(col_values):
            if search_date in str(val):
                found_idx = i + 1
                break
        if found_idx != -1:
            for i, val in enumerate(row_data[1:], start=2):
                if val not in (None, "", 0, "0", 0.0, "N/A"): 
                    sheet.update_cell(found_idx, i, val)
            return "Updated"
        else:
            sheet.append_row(row_data)
            return "Appended"
    except Exception as e: return f"Err: {str(e)[:15]}"

# --- LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
except Exception as e:
    print(f"Login Fail: {e}"); exit(1)

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# --- 1. MORNING BLOCK (С ИСПРАВЛЕННЫМ СНОМ И HRV) ---
morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h = f"{today_str} 08:00", "", "", "", "", "", ""

try:
    # 1. Поиск HRV
    try:
        hrv_res = gar.get_hrv_data(today_str)
        if hrv_res and "hrvSummary" in hrv_res:
            hrv = hrv_res.get("hrvSummary", {}).get("lastNightAvg") or ""
    except: pass
    
    if not hrv:
        try:
            stats = gar.get_stats(today_str) or {}
            hrv = stats.get("allDayAvgHrv") or stats.get("lastNightAvgHrv") or stats.get("lastNightHrv") or ""
        except: pass

    # 2. Поиск Сна (ИСПРАВЛЕНО: возвращен точный расчет из первого main.py)
    for d in [today_str, yesterday_str]:
        try:
            sleep_data = gar.get_sleep_data(d)
            dto = sleep_data.get("dailySleepDTO") or {}
            if dto and dto.get("sleepTimeSeconds", 0) > 0:
                slp_sc = dto.get("sleepScore") or sleep_data.get("sleepScore") or ""
                # Точный расчет без лишних оберток, как в оригинале
                slp_h = round(dto.get("sleepTimeSeconds") / 3600, 1)
                morning_ts = dto.get("sleepEndTimeLocal", "").replace("T", " ")[:16] or morning_ts
                break
        except: continue

    # 3. Поиск Веса (за 5 дней)
    for i in range(5):
        d_check = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            w_data = gar.get_body_composition(d_check, today_str)
            if w_data and w_data.get('uploads'):
                weight = round(w_data['uploads'][-1].get('weight', 0) / 1000, 1)
                break
        except: continue

    # 4. Пульс и Body Battery
    summary = gar.get_user_summary(today_str) or {}
    r_hr = summary.get("restingHeartRate") or summary.get("heartRateRestingValue") or ""
    bb_morning = summary.get("bodyBatteryHighestValue") or ""
    
    morning_row = [morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h]
except Exception as e:
    print(f"Morning Block Error: {e}")
    morning_row = [morning_ts, "", "", "", "", "", ""]

# --- 2. DAILY BLOCK ---
try:
    summary = gar.get_user_summary(today_str) or {}
    stats = gar.get_stats(today_str) or {}
    steps_data = gar.get_daily_steps(today_str, today_str)
    steps = steps_data[0].get('totalSteps', 0) if steps_data else 0
    cals = (summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0)) or stats.get("calories") or 0
    steps_distance_km = round(steps * 0.000762, 2)
    daily_row = [today_str, steps, steps_distance_km, cals, r_hr, summary.get("bodyBatteryMostRecentValue", "")]
except Exception as e:
    print(f"Daily Error: {e}")
    daily_row = [today_str, "", "", "", "", ""]

# --- 3. ACTIVITIES ---
HISTORY_FILE = "history.json"
if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "r") as f: history = json.load(f)
else: history = {"processed_activity_ids": []}
processed_ids = set(history.get("processed_activity_ids", []))
activities_to_log = []

try:
    latest_activities = gar.get_activities(0, 10)
    for a in latest_activities:
        activity_id = str(a.get("activityId"))
        if activity_id in processed_ids: continue
        start_local = a.get("startTimeLocal", "")
        if not start_local.startswith(today_str): continue
        
        act_date_time = start_local.replace("T", " ")[:16]
        raw_load = (a.get('activityTrainingLoad') or a.get('trainingLoad') or 0)
        t_load = round(float(raw_load), 1)
        avg_hr = a.get('averageHR', "")
        
        activities_to_log.append([
            act_date_time, a.get('activityType', {}).get('typeKey', ''), 
            round(a.get('duration', 0) / 3600, 2), round(a.get('distance', 0) / 1000, 2), 
            avg_hr, a.get('maxHR', ""), "", t_load, 
            round(float(a.get('aerobicTrainingEffect', 0)), 1), a.get('calories', ""), 
            "", "", activity_id
        ])
        processed_ids.add(activity_id)
    
    history["processed_activity_ids"] = list(processed_ids)
    with open(HISTORY_FILE, "w") as f: json.dump(history, f, indent=2)
except Exception as e: print("Activities error:", e)

# --- Write to Sheets ---
try:
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    credentials = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(credentials).open("Garmin_Data")
    
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)
    
    act_sheet = ss.worksheet("Activities")
    existing_keys = {f"{r[12]}" for r in act_sheet.get_all_values() if len(r) > 12}
    for act in activities_to_log:
        if act[12] not in existing_keys: act_sheet.append_row(act)
    print("✅ Данные синхронизированы")
except Exception as e: print("Sheets write error:", e)

# ---------- AI BLOCK: АНАЛИЗ ТРЕНДОВ ----------
advice = "Нет данных для анализа"
if GEMINI_API_KEY:
    try:
        daily_hist = ss.worksheet("Daily").get_all_values()[-5:]
        morning_hist = ss.worksheet("Morning").get_all_values()[-5:]
        history_context = f"История за 5 дней (Daily): {daily_hist}\nИстория за 5 дней (Morning): {morning_hist}"
        
        url = f"https://generativ
