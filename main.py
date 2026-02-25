#--- Активность only new

import os
import json
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
from google.genai import Client
import requests

# --- CONFIG ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
test_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY.strip()}"
test_res = requests.get(test_url)
print(test_res.json())
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

    steps_data = gar.get_daily_steps(today_str, today_str)
    steps = steps_data[0].get('totalSteps', 0) if steps_data else 0

    cals = (
        summary.get("activeKilocalories", 0)
        + summary.get("bmrKilocalories", 0)
    ) or stats.get("calories") or 0

    steps_distance_km = round(steps * 0.000762, 2)

    activities = gar.get_activities_by_date(today_str, today_str) or []
    activity_count = len(activities)

    daily_row = [
        today_str,
        steps,
        steps_distance_km,
        cals,
        r_hr,
        summary.get("bodyBatteryMostRecentValue", "")
    ]

except Exception as e:
    print(f"Daily Error: {e}")
    daily_row = [today_str, "", "", "", "", ""]

# --- 3. ACTIVITIES (только сегодняшние, без дубликатов) ---
HISTORY_FILE = "history.json"
if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "r") as f:
        history = json.load(f)
else:
    history = {"processed_activity_ids": []}

processed_ids = set(history.get("processed_activity_ids", []))
activities_to_log = []

try:
    latest_activities = gar.get_activities(0, 10)
    for a in latest_activities:
        activity_id = str(a.get("activityId"))
        if activity_id in processed_ids:
            continue

        start_local = a.get("startTimeLocal", "")
        if not start_local.startswith(today_str):
            continue

        act_date_time = start_local.replace("T", " ")[:16]

        cad = (
            a.get('averageBikingCadenceInRevPerMinute') or
            a.get('averageBikingCadence') or
            a.get('averageRunCadence') or
            a.get('averageCadence') or
            a.get('averageFractionalCadence') or
            ""
        )

        raw_load = (
            a.get('activityTrainingLoad') or
            a.get('trainingLoad') or
            a.get('metabolicCartTrainingLoad') or
            0
        )
        t_load = round(float(raw_load), 1)

        avg_hr = a.get('averageHR', "")
        max_hr = a.get('maxHR', "")

        intensity_val = ""
        try:
            if avg_hr and r_hr and float(r_hr) > 0:
                intensity_val = round(
                    ((float(avg_hr) - float(r_hr)) / (185 - float(r_hr))) * 100, 1
                )
        except:
            intensity_val = ""

        activities_to_log.append([
            act_date_time,
            a.get('activityType', {}).get('typeKey', ''),
            round(a.get('duration', 0) / 3600, 2),
            round(a.get('distance', 0) / 1000, 2),
            avg_hr,
            max_hr,
            intensity_val,
            t_load,
            round(float(a.get('aerobicTrainingEffect', 0)), 1),
            a.get('calories', ""),
            a.get('avgPower', ""),
            cad,
            activity_id
        ])
        processed_ids.add(activity_id)

    history["processed_activity_ids"] = list(processed_ids)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)

    print("NEW ACTIVITIES ADDED:", len(activities_to_log))

except Exception as e:
    print("Activities error:", e)

# --- Write to Google Sheets ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    credentials = Credentials.from_service_account_info(
        creds,
        scopes=["https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"]
    )
    ss = gspread.authorize(credentials).open("Garmin_Data")
    act_sheet = ss.worksheet("Activities")

    existing_keys = {f"{r[0]}_{r[1]}_{r[2]}" for r in act_sheet.get_all_values() if len(r) > 2}
    activities_to_log.sort(key=lambda x: x[0])  # сортировка по дате+времени

    for act in activities_to_log:
        key = f"{act[0]}_{act[1]}_{act[12]}"
        if key not in existing_keys:
            act_sheet.append_row(act)
            print("Appended activity:", key)
        else:
            print("Already exists:", key)

except Exception as e:
    print("Sheets Activities write error:", e)

# --- 4. SYNC (Таблицы) ---
try:
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    ss = gspread.authorize(c_obj).open("Garmin_Data")

    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)
    print("✅ Таблицы Daily/Morning обновлены")
except Exception as e:
    print(f"⚠️ Ошибка в блоке Sync: {e}")

# ---------- AI BLOCK (Максимально живучий) ----------
advice = "Нет данных для анализа"
if GEMINI_API_KEY:
    urls = [
        f"https://generativelanguage.googleapis.com/v1/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY.strip()}",
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY.strip()}"
    ]
    
    for url in urls:
        try:
            headers = {'Content-Type': 'application/json'}
            payload = {
                "contents": [{
                    "parts": [{
                        "text": f"Биометрия: HRV {hrv}, Сон {slp_h}ч. Дай короткий ироничный совет на русском."
                    }]
                }]
            }
            res = requests.post(url, headers=headers, json=payload, timeout=20)
            
            if res.status_code == 200:
                data = res.json()
                advice = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                print(f"✅ Успех AI через: {url.split('/')[3]}")
                break 
            else:
                print(f"⚠️ Попытка {url.split('/')[3]} не удалась ({res.status_code})")
                advice = f"AI Error {res.status_code}"
        except Exception as e:
            print(f"❌ Ошибка запроса AI: {e}")
            continue

# ---------- TELEGRAM (Вынесен из цикла AI) ----------
try:
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        message = (
            f"🚀 *Отчет Garmin*\n"
            f"💓 HRV: {hrv}\n"
            f"🌙 Сон: {slp_h}ч\n"
            f"🩺 Пульс: {r_hr}\n\n"
            f"🤖 {advice.replace('*', '')}"
        )

        tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN.strip()}/sendMessage"
        tg_response = requests.post(
            tg_url,
            json={
                "chat_id": TELEGRAM_CHAT_ID.strip(),
                "text": message,
                "parse_mode": "Markdown"
            },
            timeout=15
        )
        print(f"✅ Telegram Response: {tg_response.status_code}")
    else:
        print("⚠️ Данные Telegram (Token/ID) не найдены")
except Exception as e:
    print(f"❌ Ошибка отправки Telegram: {e}")
