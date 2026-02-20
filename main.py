#--- Активость сегодня

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

import requests

# --- 3. ACTIVITIES (полный Internal API + авторизация) ---
activities_to_log = []

try:
    # ————————————————————————————————
    # 1) LOG IN WEB GARMIN CONNECT
    # ————————————————————————————————

    login_url = "https://sso.garmin.com/sso/login"
    auth_params = {
        "service": "https://connect.garmin.com/modern/",
        "webhost": "connect.garmin.com",
        "username": GARMIN_EMAIL,
        "password": GARMIN_PASSWORD,
        "rememberme": "true"
    }

    with requests.Session() as s:
        # Login page to get initial cookies
        s.get(login_url)
        # Post credentials
        r = s.post(login_url, params=auth_params)
        if "CASTGC" not in s.cookies:
            raise Exception("Login failed: no CASTGC cookie")

        # ————————————————————————————————
        # 2) EXCHANGE TICKET
        # ————————————————————————————————
        tgt = s.cookies.get("CASTGC")
        service = "https://connect.garmin.com/modern/"
        st_url = f"https://sso.garmin.com/sso/ticket?service={service}&CASTGC={tgt}"
        s.get(st_url)  # now we have session cookies set for connect

        # ————————————————————————————————
        # 3) REQUEST INTERNAL ACTIVITIES API
        # ————————————————————————————————
        act_url = (
            f"https://connect.garmin.com/modern/proxy/activity-service/activities"
            f"?startDate={yesterday_str}&endDate={today_str}"
        )
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*"
        }
        resp = s.get(act_url, headers=headers)
        raw_acts = resp.json() if resp.status_code == 200 else []
        print("RAW_ACTIVITIES:", raw_acts)

        # ————————————————————————————————
        # 4) PARSE ACTIVITIES INTO LIST
        # ————————————————————————————————
        for a in raw_acts:
            activity_id = str(a.get("activityId"))
            act_date = a.get("startTimeLocal", "")[:10]
            act_time = a.get("startTimeLocal", "")[11:16]
            sport = a.get("activityType", {}).get("typeKey", "")

            # Cadence
            cad = (
                a.get("averageBikingCadenceInRevPerMinute")
                or a.get("averageBikingCadence")
                or a.get("averageRunCadence")
                or a.get("averageCadence")
                or a.get("averageFractionalCadence")
                or ""
            )

            # Training Load .1
            raw_load = (
                a.get("activityTrainingLoad")
                or a.get("trainingLoad")
                or a.get("metabolicCartTrainingLoad")
                or 0
            )
            t_load = round(float(raw_load), 1)

            avg_hr = a.get("averageHR", "")
            max_hr = a.get("maxHR", "")

            # HR Intensity
            intensity_val = ""
            try:
                if avg_hr and r_hr and float(r_hr) > 0:
                    intensity_val = round(
                        ((float(avg_hr) - float(r_hr)) / (185 - float(r_hr))) * 100, 1
                    )
            except:
                intensity_val = ""

            activities_to_log.append([
                act_date,
                act_time,
                sport,
                round(a.get("duration", 0) / 3600, 2),
                round(a.get("distance", 0) / 1000, 2),
                avg_hr,
                max_hr,
                intensity_val,
                t_load,
                round(float(a.get("aerobicTrainingEffect", 0)), 1),
                a.get("calories", ""),
                a.get("avgPower", ""),
                cad,
                activity_id
            ])

    print("ACTIVITIES_TO_LOG COUNT:", len(activities_to_log))

except Exception as e:
    print("Activities error:", e)


# --- Write only NEW activities ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    credentials = Credentials.from_service_account_info(
        creds,
        scopes=["https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"]
    )
    ss = gspread.authorize(credentials).open("Garmin_Data")
    act_sheet = ss.worksheet("Activities")

    # Existing activityIds
    existing_ids = set()
    for r in act_sheet.get_all_values():
        if len(r) > 13 and r[13].strip():
            existing_ids.add(r[13].strip())

    # Also block old rows without ID by old date_time_sport
    for r in act_sheet.get_all_values():
        if len(r) > 12 and not r[13].strip():
            existing_ids.add(f"{r[0]}_{r[1]}_{r[2]}")

    # Sort
    activities_to_log.sort(key=lambda x: (x[0], x[1]))

    for act in activities_to_log:
        act_id = act[-1]
        if act_id not in existing_ids:
            act_sheet.append_row(act)
            print("Appended activity:", act_id)
        else:
            print("Already exists activity:", act_id)

except Exception as e:
    print("Sheets Activities write error:", e)


# --- 4. SYNC, AI & TELEGRAM ---
try:
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)

    advice = "Нет данных для анализа"
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            if available_models:
                model_name = available_models[0]
                model = genai.GenerativeModel(model_name)
                prompt = (f"Биометрия: HRV {hrv}, Пульс {r_hr}, Батарейка {bb_morning}, "
                          f"Сон {slp_h}ч (Score: {slp_sc}). Напиши один ироничный и мудрый совет на день.")
                res = model.generate_content(prompt)
                advice = res.text.strip()
            else:
                advice = "API Key жив, но доступных моделей нет."
        except Exception as ai_e:
            advice = f"AI Error: {str(ai_e)[:30]}"
    
    ss.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Success", advice])
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
