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
def safe(val):
    return val if val is not None else ""
def fmt_val(v):
    """Format utility for Google Sheets (handles None, floats/commas)."""
    if v in (None, "", "None"):
        return ""
    if isinstance(v, float):
        if v.is_integer():
            return int(v)
        return round(v, 2)
    if isinstance(v, str):
        v_stripped = v.strip()
        if v_stripped.isdigit():
            return int(v_stripped)
        try:
            return float(v_stripped)
        except ValueError:
            pass
    return v
# --- LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
except Exception as e:
    print(f"Login Fail: {e}"); exit(1)
now = datetime.now()
today = now.strftime("%Y-%m-%d")
today_str = today
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
debug = []
# --- 1. MORNING BLOCK ---
morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h = f"{today_str} 08:00", "", "", "", "", "", ""
try:
    stats = gar.get_stats(today_str) or {}
    hrv = stats.get("allDayAvgHrv") or stats.get("lastNightAvgHrv") or stats.get("lastNightHrv")
    
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
    for i in range(3):
        d_check = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            w_data = gar.get_body_composition(d_check, today_str)
            if w_data and w_data.get('uploads'):
                weight = round(w_data['uploads'][-1].get('weight', 0) / 1000, 1)
                break
        except: continue
    summary = gar.get_user_summary(today_str) or {}
    r_hr = summary.get("restingHeartRate") or summary.get("heartRateRestingValue") or ""
    bb_morning = summary.get("bodyBatteryHighestValue") or ""
    morning_row = [morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h]
except Exception as e:
    print(f"Morning Error: {e}")
    morning_row = [morning_ts, "", "", "", "", "", ""]
# --- 2. DAILY BLOCK ---
try:
    summary = gar.get_user_summary(today_str) or {}
    stats = gar.get_stats(today_str) or {}
    # Шаги
    steps_data = gar.get_daily_steps(today_str, today_str)
    steps = steps_data[0].get('totalSteps', 0) if steps_data else 0
    # Калории
    cals = (
        summary.get("activeKilocalories", 0)
        + summary.get("bmrKilocalories", 0)
    ) or stats.get("calories") or 0
    # Дистанция ТОЛЬКО от шагов (в км, 0.762м/шаг - стандарт)
    steps_distance_km = round(steps * 0.000762, 2)
    # Активности за сегодня (завершённые)
    activities = gar.get_activities_by_date(today_str, today_str) or []
    activity_count = len(activities)
    daily_row = [
        today_str,
        steps,
        steps_distance_km,  # Только шаги!
        cals,
        r_hr,
        summary.get("bodyBatteryMostRecentValue", "")
        # activity_count убран отсюда, чтобы не было лишней колонки
    ]
except Exception as e:
    print(f"Daily Error: {e}")
    daily_row = [today_str, "", "", "", "", ""]
# ------------------3. ACTIVITIES (Load + Cadence) -------------------
activities_log = []
try:
    act_list = gar.get_activities_by_date(today, today) or []
    for a in act_list:
        # Cadence
        cad_keys = [
            "averageBikingCadence", "averageCadence",
            "averageRunCadence", "averageFractionalCadence",
            "averageRunningCadenceInStepsPerMinute",
            "averageBikingCadenceInRevPerMinute"
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
            "trainingLoadPeakImpact",
            "activityTrainingLoad"
        ]
        t_load = ""
        for lk in load_keys:
            if a.get(lk):
                t_load = a[lk]
                break
                
        # Handle Garmin API keys for others
        avg_hr = a.get("averageHR") or a.get("averageHeartRate") or ""
        max_hr = a.get("maxHR") or a.get("maxHeartRate") or ""
        te = a.get("aerobicTrainingEffect") or a.get("trainingEffect") or ""
        calories = a.get("calories", "")
        if calories:
            try: calories = int(float(calories)) 
            except: pass
        avg_power = a.get("avgPower") or a.get("averagePower") or ""
        # Use our formatter function for alignment
        activities_log.append([
            today,
            a.get("startTimeLocal", "")[11:16],
            a.get("activityType", {}).get("typeKey", ""),
            fmt_val(a.get("duration",0)/3600 if a.get("duration") else 0),
            fmt_val(a.get("distance",0)/1000 if a.get("distance") else 0),
            fmt_val(avg_hr),
            fmt_val(max_hr),
            fmt_val(t_load),
            fmt_val(te),
            fmt_val(calories),
            fmt_val(avg_power),
            fmt_val(cadence),
            "" # 13th empty column for HR intensity
        ])
except Exception as e:
    print(f"Activities Error: {e}")
debug.append(f"Activities count: {len(activities_log)}")
# -----------------4. SYNC TO SHEETS -------------------
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
            act_sheet.append_row(al, value_input_option='USER_ENTERED') # Important for correct data-types/alignments!
# --- 5. SYNC, AI & TELEGRAM ---
# try block here previously contained duplicate code. Keeping only one cred init block.
    
    advice = "Нет данных для анализа"
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            if available_models:
                # Ensure we use pro for high quality advice
                model_name = "models/gemini-1.5-pro" if "models/gemini-1.5-pro" in available_models else available_models[0]
                model = genai.GenerativeModel(model_name)
                prompt = (f"Биометрия: HRV {hrv}, Пульс {r_hr}, Батарейка {bb_morning}, "
                          f"Сон {slp_h}ч (Score: {slp_sc}). Напиши один ироничный и мудрый совет на день.")
                res = model.generate_content(prompt)
                advice = res.text.strip()
            else:
                advice = "API Key жив, но доступных моделей нет."
        except Exception as ai_e:
            advice = f"AI Error: {str(ai_e)[:30]}"
    
    try:
        ss.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Success", advice])
    except: pass
    
    print(f"✔ Финиш! HRV: {hrv}, AI: {advice[:40]}")
    # --- ОТПРАВКА В ТЕЛЕГРАМ ---
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        msg = f"🚀 Отчет:\nHRV: {hrv}\nСон: {slp_h}ч\nПульс: {r_hr}\n\n🤖 {advice.replace('*', '')}"
        tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN.strip()}/sendMessage"
        resp = requests.post(tg_url, json={"chat_id": TELEGRAM_CHAT_ID.strip(), "text": msg}, timeout=15)
        print(f"Telegram Response: {resp.status_code} {resp.text}")
    else:
        print("Telegram Token or ID is missing in Secrets!")
except Exception as e:
    print(f"Final Error: {e}")
