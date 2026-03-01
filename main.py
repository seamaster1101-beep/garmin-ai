import os
import json
import time
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import requests

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
    except: return "Error"

# --- LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
except Exception as e:
    print(f"Login Fail: {e}"); exit(1)

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# --- 1. MORNING BLOCK ---
morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h = f"{today_str} 08:00", "", "", "", "", "", ""

try:
    # HRV Search
    try:
        hrv_res = gar.get_hrv_data(today_str)
        if hrv_res and "hrvSummary" in hrv_res:
            hrv = hrv_res.get("hrvSummary", {}).get("lastNightAvg") or ""
    except: pass
    if not hrv:
        try:
            stats = gar.get_stats(today_str) or {}
            hrv = stats.get("allDayAvgHrv") or stats.get("lastNightAvgHrv") or ""
        except: pass

    # Sleep & Sleep Score (6.9 fix)
    for d in [today_str, yesterday_str]:
        try:
            sleep_data = gar.get_sleep_data(d)
            dto = sleep_data.get("dailySleepDTO") or {}
            if dto and dto.get("sleepTimeSeconds", 0) > 0:
                slp_sc = dto.get("sleepScore") or sleep_data.get("sleepScore") or ""
                slp_h = round(float(dto.get("sleepTimeSeconds")) / 3600, 1)
                morning_ts = dto.get("sleepEndTimeLocal", "").replace("T", " ")[:16]
                break
        except: continue

    # Weight
    for i in range(5):
        d_check = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            w_data = gar.get_body_composition(d_check)
            if w_data and w_data.get('uploads'):
                weight = round(w_data['uploads'][-1].get('weight', 0) / 1000, 1)
                break
        except: continue

    summary = gar.get_user_summary(today_str) or {}
    r_hr = summary.get("restingHeartRate") or summary.get("heartRateRestingValue") or ""
    bb_morning = summary.get("bodyBatteryHighestValue") or ""
    
    morning_row = [morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h]
except:
    morning_row = [morning_ts, "", "", "", "", "", ""]

# --- 2. DAILY BLOCK ---
try:
    summary = gar.get_user_summary(today_str) or {}
    steps_data = gar.get_daily_steps(today_str, today_str)
    steps = steps_data[0].get('totalSteps', 0) if steps_data else 0
    # Исправленная строка расчета калорий:
    cals = (summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0)) or 0
    daily_row = [today_str, steps, round(steps * 0.000762, 2), cals, r_hr, summary.get("bodyBatteryMostRecentValue", "")]
except:
    daily_row = [today_str, "", "", "", "", ""]

# --- 3. ACTIVITIES ---
HISTORY_FILE = "history.json"
if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "r") as f: history = json.load(f)
else: history = {"processed_activity_ids": []}
processed_ids = set(history.get("processed_activity_ids", []))
activities_to_log = []

try:
    latest = gar.get_activities(0, 10)
    for a in latest:
        activity_id = str(a.get("activityId"))
        if activity_id in processed_ids: continue
        if not a.get("startTimeLocal", "").startswith(today_str): continue
        
        act_row = [
            a.get("startTimeLocal", "").replace("T", " ")[:16],
            a.get('activityType', {}).get('typeKey', ''),
            round(a.get('duration', 0) / 3600, 2),
            round(a.get('distance', 0) / 1000, 2),
            a.get('averageHR', ""), a.get('maxHR', ""), "", 
            round(float(a.get('trainingLoad', 0)), 1),
            round(float(a.get('aerobicTrainingEffect', 0)), 1),
            a.get('calories', ""), "", "", activity_id
        ]
        activities_to_log.append(act_row)
        processed_ids.add(activity_id)
    
    history["processed_activity_ids"] = list(processed_ids)
    with open(HISTORY_FILE, "w") as f: json.dump(history, f, indent=2)
except: pass

# --- WRITE TO SHEETS ---
try:
    creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), 
            scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(creds).open("Garmin_Data")
    
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)
    
    act_sheet = ss.worksheet("Activities")
    for act in activities_to_log:
        act_sheet.append_row(act)
except Exception as e: print(f"Sheets Error: {e}")

# ---------- AI BLOCK ----------
advice = "Нет данных для анализа"
if GEMINI_API_KEY:
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-lite:generateContent?key={GEMINI_API_KEY.strip()}"
        prompt = (f"Ты - ироничный тренер. Данные сегодня: HRV {hrv}, Пульс {r_hr}, Сон {slp_h}ч, Вес {weight}.\n"
                  f"Дай 1 короткий колкий совет на русском.")
        
        time.sleep(4)
        res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=20)
        if res.status_code == 200:
            advice = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except: advice = "ИИ сегодня отдыхает"

# LOG TO AI_LOG
try:
    status = "Success" if "отдыхает" not in advice else "Fail"
    ss.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), status, advice.replace('*', '')])
except: pass
