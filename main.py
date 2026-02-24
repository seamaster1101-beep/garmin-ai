import os
import json
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
from google import genai  # Исправленный импорт
import requests

# --- CONFIG ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def clean(val):
    if val is None or val == "" or val == 0: return ""
    return str(val).replace('.', ',')

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
                    sheet.update_cell(found_idx, i, clean(val))
            return "Updated"
        else:
            sheet.append_row([clean(x) for x in row_data])
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

# --- 1. MORNING BLOCK ---
morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h = f"{today_str} 08:00", "", "", "", "", "", ""

try:
    stats = gar.get_stats(today_str) or {}
    hrv = stats.get("allDayAvgHrv") or stats.get("lastNightAvgHrv") or stats.get("lastNightHrv") or ""
    
    for d in [today_str, yesterday_str]:
        try:
            sleep_data = gar.get_sleep_data(d)
            dto = sleep_data.get("dailySleepDTO") or {}
            if dto and dto.get("sleepTimeSeconds", 0) > 0:
                slp_sc = dto.get("sleepScore") or sleep_data.get("sleepScore") or ""
                slp_h = round(dto.get("sleepTimeSeconds", 0) / 3600, 1)
                morning_ts = dto.get("sleepEndTimeLocal", "").replace("T", " ")[:16] or morning_ts
                break
        except: continue

    summary = gar.get_user_summary(today_str) or {}
    r_hr = summary.get("restingHeartRate") or summary.get("heartRateRestingValue") or ""
    bb_morning = summary.get("bodyBatteryHighestValue") or ""

    # Вес
    try:
        w_data = gar.get_body_composition(today_str)
        if w_data and w_data.get('uploads'):
            weight = round(w_data['uploads'][-1].get('weight', 0) / 1000, 1)
    except: pass

    morning_row = [morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h]
except Exception as e:
    print(f"Morning Error: {e}")
    morning_row = [morning_ts, "", "", "", "", "", ""]

# --- 2. DAILY BLOCK ---
try:
    summary = gar.get_user_summary(today_str) or {}
    steps_data = gar.get_daily_steps(today_str, today_str)
    steps = steps_data[0].get('totalSteps', 0) if steps_data else 0
    cals = (summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0)) or 0
    dist = round(steps * 0.000762, 2)

    daily_row = [today_str, steps, dist, cals, r_hr, summary.get("bodyBatteryMostRecentValue", "")]
except Exception as e:
    print(f"Daily Error: {e}")
    daily_row = [today_str, "", "", "", "", ""]

# --- 3. ACTIVITIES ---
activities_to_log = []
try:
    latest_activities = gar.get_activities(0, 5)
    for a in latest_activities:
        start_local = a.get("startTimeLocal", "")
        if not start_local.startswith(today_str): continue

        act_id = str(a.get("activityId"))
        avg_hr = a.get('averageHR', "")
        
        intensity_val = ""
        try:
            if avg_hr and r_hr and float(r_hr) > 0:
                intensity_val = round(((float(avg_hr) - float(r_hr)) / (185 - float(r_hr))) * 100, 1)
        except: pass

        activities_to_log.append([
            start_local.replace("T", " ")[:16],
            a.get('activityType', {}).get('typeKey', ''),
            round(a.get('duration', 0) / 3600, 2),
            round(a.get('distance', 0) / 1000, 2),
            avg_hr, a.get('maxHR', ""),
            intensity_val,
            round(float(a.get('trainingLoad' or 0)), 1),
            round(float(a.get('aerobicTrainingEffect', 0)), 1),
            a.get('calories', ""), a.get('avgPower', ""),
            (a.get('averageRunCadence') or a.get('averageBikingCadence') or ""),
            act_id
        ])
except Exception as e: print(f"Act Error: {e}")

# --- WRITE TO SHEETS ---
try:
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    
    # Обновление Daily/Morning
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)

    # Активности (только новые)
    if activities_to_log:
        act_sheet = ss.worksheet("Activities")
        existing_ids = act_sheet.col_values(13) # ID активности в 13 колонке
        for act in activities_to_log:
            if str(act[12]) not in existing_ids:
                act_sheet.append_row([clean(x) for x in act])
                print(f"Added Act: {act[12]}")

    # --- AI ADVICE ---
    advice = "AI не доступен"
    if GEMINI_API_KEY:
        try:
            client = genai.Client(api_key=GEMINI_API_KEY.strip())
            prompt = f"Биометрия: HRV {hrv}, Пульс {r_hr}, Батарейка {bb_morning}, Сон {slp_h}ч (Баллы: {slp_sc}). Напиши один ироничный и мудрый совет на день на русском."
            response = client.models.generate_content(model="gemini-1.5-flash", contents=prompt)
            advice = response.text.strip()
        except Exception as ai_e: advice = f"AI Error: {str(ai_e)[:30]}"

    ss.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Success", advice])
    
    # --- TELEGRAM ---
    if TELEGRAM_BOT_TOKEN:
        msg = f"🚀 *Отчет {today_str}*\n\n💓 HRV: {hrv}\n🌙 Сон: {slp_h}ч ({slp_sc}/100)\n👣 Шаги: {steps}\n\n🤖 *Совет:* {advice}"
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})

except Exception as e: print(f"Final Error: {e}")
