import os
import json
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai
import requests

# --- CONFIG ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
HR_MAX = 165

def format_num(val):
    """Исправляет точки на запятые и обрабатывает пустые значения"""
    if val is None or val == "" or val == 0 or val == "0": return ""
    # Если это число (float или int), конвертируем и меняем точку на запятую
    try:
        if isinstance(val, (float, int)):
            return str(val).replace('.', ',')
        return str(val).replace('.', ',')
    except:
        return str(val)

def update_or_append(sheet, date_str, row_data):
    try:
        col_values = sheet.col_values(1)
        search_date = date_str.split(' ')[0]
        found_idx = -1
        for i, val in enumerate(col_values):
            if search_date in str(val):
                found_idx = i + 1
                break
        
        formatted_row = [format_num(val) if i > 0 else val for i, val in enumerate(row_data)]
        if found_idx != -1:
            for i, val in enumerate(formatted_row[1:], start=2):
                if val != "":
                    sheet.update_cell(found_idx, i, val)
            return "Updated"
        else:
            sheet.append_row(formatted_row)
            return "Appended"
    except Exception as e:
        print(f"Sheet Error: {e}")
        return "Error"

# --- LOGIN ---
gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
gar.login()

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# --- ДАННЫЕ ЗДОРОВЬЯ (HRV, Пульс, BB) ---
weight, r_hr, hrv, bb_morning, slp_sc, slp_h = "", "", "", "", "", ""

try:
    # Самый надежный способ для RHR и HRV
    health_data = gar.get_rhr_and_hrv(today_str) or {}
    hrv = health_data.get("hrvSummary", {}).get("lastNightAvg", "")
    r_hr = health_data.get("restingHeartRate", "")
    
    # Если HRV всё еще нет, пробуем через stats
    if not hrv:
        stats = gar.get_stats(today_str) or {}
        hrv = stats.get("lastNightAvgHrv") or stats.get("allDayAvgHrv") or ""
    
    summary = gar.get_user_summary(today_str) or {}
    bb_morning = summary.get("bodyBatteryHighestValue") or ""

    # Сон (проверяем сегодня и вчера)
    for d in [today_str, yesterday_str]:
        s_data = gar.get_sleep_data(d)
        dto = s_data.get("dailySleepDTO") or {}
        if dto and dto.get("sleepTimeSeconds", 0) > 0:
            slp_sc = dto.get("sleepScore") or ""
            slp_h = round(dto.get("sleepTimeSeconds") / 3600, 1)
            break

    # Вес
    for i in range(3):
        d_check = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        w_data = gar.get_body_composition(d_check)
        if w_data and w_data.get('uploads'):
            weight = round(w_data['uploads'][-1].get('weight', 0) / 1000, 1)
            break
except Exception as e:
    print(f"Bio Data Error: {e}")

# --- DAILY ACTIVITY ---
try:
    daily_stats = gar.get_stats(today_str) or {}
    steps = daily_stats.get("totalSteps") or 0
    dist = round((daily_stats.get("totalDistanceMeters") or 0) / 1000, 2)
    cals = (summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0)) or daily_stats.get("calories") or 0
    bb_now = summary.get("bodyBatteryMostRecentValue") or ""
except:
    steps, dist, cals, bb_now = 0, 0, 0, ""

# --- GOOGLE SHEETS SYNC ---
try:
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds_dict, scopes=["
