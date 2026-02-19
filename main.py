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
HR_MAX = 165  # Твой макс. пульс для расчета интенсивности

def format_num(val):
    """Превращает точку в запятую для Google Sheets и защищает от None"""
    if val is None or val == "": return ""
    return str(val).replace('.', ',')

def update_or_append(sheet, date_str, row_data):
    try:
        col_values = sheet.col_values(1)
        search_date = date_str.split(' ')[0]
        found_idx = -1
        for i, val in enumerate(col_values):
            if search_date in str(val):
                found_idx = i + 1
                break
        
        formatted_row = [format_num(val) for val in row_data]
        if found_idx != -1:
            # Обновляем существующую строку
            for i, val in enumerate(formatted_row[1:], start=2):
                if val not in (None, "", 0, "0", "0,0"):
                    sheet.update_cell(found_idx, i, val)
            return "Updated"
        else:
            sheet.append_row(formatted_row)
            return "Appended"
    except Exception as e:
        return f"Err: {str(e)[:15]}"

# --- LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
except Exception as e:
    print(f"Login Fail: {e}")
    exit(1)

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# --- MORNING BLOCK ---
weight, r_hr, hrv, bb_morning, slp_sc, slp_h = "", "", "", "", "", ""
morning_ts = f"{today_str} 08:00"

try:
    # Пытаемся достать HRV
    stats = gar.get_stats(today_str) or {}
    hrv = stats.get("allDayAvgHrv") or stats.get("lastNightAvgHrv") or ""
    
    # Сон (проверка за сегодня и вчера)
    for d in [today_str, yesterday_str]:
        sleep_data = gar.get_sleep_data(d)
        dto = sleep_data.get("dailySleepDTO") or {}
        if dto and dto.get("sleepTimeSeconds"):
            slp_sc = dto.get("sleepScore") or ""
            slp_h = round(dto.get("sleepTimeSeconds") / 3600, 1)
            morning_ts = dto.get("sleepEndTimeLocal", "").replace("T", " ")[:16]
            break

    # Вес
    for i in range(3):
        d_check = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        w_data = gar.get_body_composition(d_check)
        if w_data and w_data.get('uploads'):
            weight = round(w_data['uploads'][-1].get('weight', 0) / 1000, 1)
            break

    summary = gar.get_user_summary(today_str) or {}
    r_hr = summary.get("restingHeartRate") or summary.get("heartRateRestingValue") or ""
    bb_morning = summary.get("bodyBatteryHighestValue") or ""
except Exception as e:
    print(f"Morning Error: {e}")

morning_row = [morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h]

# --- DAILY BLOCK ---
try:
    summary = gar.get_user_summary(today_str) or {}
    stats = gar.get_stats(today_str) or {}
    steps = stats.get("totalSteps") or 0
    
    # Фикс ошибки NoneType / 1000
    raw_dist = stats.get("totalDistanceMeters") or 0
    dist_km = round(raw_dist / 1000, 2)
    
    cals = (summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0)) or stats.get("calories") or 0
    bb_now = summary.get("bodyBatteryMostRecentValue") or ""
    
    daily_row = [today_str, steps, dist_km, cals, r_hr, bb_now]
except Exception as e:
    print(f"Daily Error: {e}")
    daily_row = [today_str, 0, 0, 0, "", ""]

# --- ACTIVITIES ---
activities_today = []
try:
    activities_today = gar.get_activities_by_date(today_str, today_str) or []
except Exception as e:
    print(f"Activities Error: {e}")

# --- SYNC ---
try:
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)
    
    act_sheet = ss.worksheet("Activities")
    all_acts = act_sheet.get_all_values()
    
    for act in activities_today:
        start_full = act.get('startTimeLocal', '')
        t_part = start_full.split('T')[1][:5] if 'T' in start_full else ""
        sport = act.get('activityType', {}).get('typeKey', 'unknown')
        
        # Проверка дубликатов
        if any(r[0] == today_str and r[1] == t_part and r[2] == sport for r in all_acts):
            continue
            
        dur = round((act.get('duration') or 0) / 3600, 2)
        dist = round((act.get('distance') or 0) / 1000, 2)
        
        # Умный поиск пульса (разные ключи в Garmin API)
        avg_hr = act.get('averageHeartRate') or act.get('averageHR') or ""
        max_hr = act.get('maxHeartRate') or act.get('maxHR') or ""
        
        # Расчет интенсивности
        intensity = ""
        if avg_hr:
            perc = (avg_hr / HR_MAX) * 100
            if perc < 60: intensity = "Low"
            elif perc < 80: intensity = "Moderate"
            else: intensity = "High"

        new_act_row = [
            today_str, t_part, sport, dur, dist, avg_hr, max_hr,
            act.get('trainingLoad', ''), act.get('trainingEffect', ''),
            act.get('calories', ''), act.get('averagePower', ''),
            act.get('averageCadence', ''), intensity
        ]
        act_sheet.append_row([format_num(v) for v in new_act_row])

    # AI
    advice = "Limit reached"
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            model = genai.GenerativeModel('gemini-pro')
            res = model.generate_content(f"HRV {hrv}, BB {bb_morning}, Sleep {slp_h}. Дай ироничный совет.")
            advice = res.text.strip()
        except: pass

    # TG
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        msg = f"✅ Синхронизация {today_str}\nШаги: {steps}\nИИ: {advice}"
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})

except Exception as e:
    print(f"Final Sync Error: {e}")
