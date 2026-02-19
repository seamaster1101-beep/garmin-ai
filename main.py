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

def update_or_append(sheet, date_str, row_data):
    try:
        col_values = sheet.col_values(1)
        search_date = date_str.split(' ')[0]
        found_idx = -1
        for i, val in enumerate(col_values):
            if search_date in val:
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

# --- 1. MORNING BLOCK (HRV, Sleep Score, Weight) ---
morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h = f"{today_str} 08:00", "", "", "", "", "", ""

try:
    # 1.1 HRV (уже проверенный метод)
    stats = gar.get_stats(today_str) or {}
    hrv = stats.get("allDayAvgHrv") or stats.get("lastNightAvgHrv") or stats.get("lastNightHrv")
    if not hrv:
        try:
            hrv_data = gar.get_hrv_data(today_str)
            if hrv_data and 'hrvSummary' in hrv_data:
                hrv = hrv_data['hrvSummary'].get('lastNightAvg')
        except: pass

    # 1.2 SLEEP SCORE (усиленный поиск)
    for d in [today_str, yesterday_str]:
        sleep_raw = gar.get_sleep_data(d)
        dto = sleep_raw.get("dailySleepDTO", {})
        if dto:
            # Ищем score везде, где он может прятаться
            slp_sc = dto.get("sleepScore") or sleep_raw.get("sleepScore") or ""
            slp_h = round(dto.get("sleepTimeSeconds", 0) / 3600, 1)
            if slp_h > 0:
                morning_ts = dto.get("sleepEndTimeLocal", "").replace("T", " ")[:16]
                print(f"DEBUG: Нашел сон за {d}. Score: {slp_sc}, Hours: {slp_h}")
                break

    # 1.3 WEIGHT (ищем за последние 3 дня)
    for d_offset in range(3):
        check_d = (now - timedelta(days=d_offset)).strftime("%Y-%m-%d")
        w_comp = gar.get_body_composition(check_d, today_str)
        if w_comp.get('uploads'):
            weight = round(w_comp['uploads'][-1].get('weight', 0) / 1000, 1)
            break

    summary = gar.get_user_summary(today_str) or {}
    r_hr = summary.get("restingHeartRate", "")
    bb_morning = summary.get("bodyBatteryHighestValue", "")

    morning_row = [morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h]
except Exception as e:
    print(f"Morning Block Error: {e}")
    morning_row = [morning_ts, "", "", "", "", "", ""]

# --- 2. DAILY BLOCK ---
try:
    steps_info = gar.
