#--- Активность only new

import os
import json
import time # Добавил для возможной паузы
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
# from google.genai import Client # Эту библиотеку можно убрать, если не используешь
import requests

# --- CONFIG ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# УДАЛИЛИ диагностический запрос test_url, чтобы не вызывать ошибку 429

GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# ... (Блоки update_or_append, LOGIN, MORNING, DAILY, ACTIVITIES остаются БЕЗ ИЗМЕНЕНИЙ) ...

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
    # 4. Остальное (пульс и Body Battery)
    summary = gar.get_user_summary(today_str) or {}
    r_hr = summary.get("restingHeartRate") or summary.get("heartRateRestingValue") or ""
    bb_morning = summary.get("bodyBatteryHighestValue") or ""
    
except Exception as e:
    print(f"Morning Block Error: {e}")

# 1. Пробуем достать HRV (самый капризный пункт)
    try:
        hrv_res = gar.get_hrv_data(today_str)
        hrv = hrv_res.get("hrvSummary", {}).get("lastNightAvg") or ""
    except:
        stats = gar.get_stats(today_str) or {}
        hrv = stats.get("lastNightAvgHrv") or stats.get("allDayAvgHrv") or ""

    # 2. Сон и Sleep Score (ищем во всех закоулках JSON)
    for d in [today_str, yesterday_str]:
        try:
            s_data = gar.get_sleep_data(d) or {}
            dto = s_data.get("dailySleepDTO") or {}
            if dto.get("sleepTimeSeconds", 0) > 0:
                # Ищем Score: сначала в dto, потом в корне, потом в специальных полях
                slp_sc = dto.get("sleepScore") or s_data.get("sleepScore") or s_data.get("overallScore", {}).get("value") or ""
                slp_h = round(dto.get("sleepTimeSeconds", 0) / 3600, 1)
                morning_ts = dto.get("sleepEndTimeLocal", "").replace("T", " ")[:16]

    def morning_report(morning_data, history_morning):
    try:
        weight = morning_data.get('weight', 'N/A')
        rhr = morning_data.get('rhr', 'N/A')
        hrv = morning_data.get('hrv', 'N/A')
        bb = morning_data.get('bb', 'N/A')
        sleep_score = morning_data.get('sleep_score', 'N/A')
        sleep_hours = morning_data.get('sleep_hours', 'N/A')
        history_text = "Последние 3 дня:\n"
        if history_morning:
            for i, row in enumerate(history_morning[-3:]):
                try:
                    if len(row) >= 7:
                        history_text += (f"День {i}: Вес={row[1] or 'N/A'}, "
                                         f"RHR={row[2] or 'N/A'}, HRV={row[3] or 'N/A'}, "
                                         f"Sleep={row[6] or 'N/A'}ч\n")            
                break
        except: continue

    # 3. Вес (последний доступный)
    try:
        w_res = gar.get_body_composition(today_str)
        if w_res and w_res.get('uploads'):
            weight = round(w_res['uploads'][-1].get('weight', 0) / 1000, 1)
    except: pass

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
        cad = (a.get('averageBikingCadenceInRevPerMinute') or a.get('averageBikingCadence') or a.get('averageRunCadence') or a.get('averageCadence') or "")
        raw_load = (a.get('activityTrainingLoad') or a.get('trainingLoad') or 0)
        t_load = round(float(raw_load), 1)
        avg_hr = a.get('averageHR', "")
        max_hr = a.get('maxHR', "")
        intensity_val = ""
        try:
            if avg_hr and r_hr and float(r_hr) > 0:
                intensity_val = round(((float(avg_hr) - float(r_hr)) / (185 - float(r_hr))) * 100, 1)
        except: intensity_val = ""
        activities_to_log.append([act_date_time, a.get('activityType', {}).get('typeKey', ''), round(a.get('duration', 0) / 3600, 2), round(a.get('distance', 0) / 1000, 2), avg_hr, max_hr, intensity_val, t_load, round(float(a.get('aerobicTrainingEffect', 0)), 1), a.get('calories', ""), a.get('avgPower', ""), cad, activity_id])
        processed_ids.add(activity_id)
    history["processed_activity_ids"] = list(processed_ids)
    with open(HISTORY_FILE, "w") as f: json.dump(history, f, indent=2)
except Exception as e: print("Activities error:", e)

# --- Write to Sheets ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    credentials = Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(credentials).open("Garmin_Data")
    act_sheet = ss.worksheet("Activities")
    existing_keys = {f"{r[0]}_{r[1]}_{r[12]}" for r in act_sheet.get_all_values() if len(r) > 12}
    for act in activities_to_log:
        key = f"{act[0]}_{act[1]}_{act[12]}"
        if key not in existing_keys: act_sheet.append_row(act)
except Exception as e: print("Sheets Activities write error:", e)

# --- 4. SYNC ---
try:
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)
    print("✅ Таблицы Daily/Morning обновлены")
except Exception as e: print(f"⚠️ Ошибка в блоке Sync: {e}")

# ---------- AI BLOCK: УМНЫЙ АНАЛИЗ ТРЕНДОВ ----------
advice = "Нет данных для анализа"
if GEMINI_API_KEY:
    try:
        # 1. Забираем историю из таблиц (последние 5 дней)
        daily_hist = ss.worksheet("Daily").get_all_values()[-5:]
        morning_hist = ss.worksheet("Morning").get_all_values()[-5:]
        
        # 2. Формируем контекст для ИИ
        history_context = f"История за 5 дней (Daily): {daily_hist}\nИстория за 5 дней (Morning): {morning_hist}"
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-lite:generateContent?key={GEMINI_API_KEY.strip()}"
        
        payload = {
            "contents": [{
                "parts": [{
                    "text": (
                        f"Ты — ироничный цифровой тренер. Проанализируй данные пользователя Garmin.\n"
                        f"ТЕКУЩИЕ ДАННЫЕ: HRV {hrv}, Пульс {r_hr}, Сон {slp_h}ч, Вес {weight}.\n"
                        f"КОНТЕКСТ ПРОШЛЫХ ДНЕЙ:\n{history_context}\n\n"
                        f"ЗАДАЧА: Сделай короткий вывод о состоянии (есть ли прогресс или переутомление) "
                        f"и дай один мудрый, но очень колкий и ироничный совет. Не используй много текста."
                    )
                }]
            }]
        }
        
        time.sleep(2) # Защита от 429
        res = requests.post(url, json=payload, timeout=20)
        if res.status_code == 200:
            advice = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        elif res.status_code == 429:
            advice = "ИИ взял тайм-аут из-за лимитов. Подожди немного."
    except Exception as e:
        print(f"AI Deep Analysis Error: {e}")
        advice = "ИИ сегодня не в духе."

# Запись в AI_Log (как мы обсуждали — в 3 колонки)
try:
    log_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    status = "Success" if "Error" not in advice and "тайм-аут" not in advice else "Fail"
    ss.worksheet("AI_Log").append_row([log_time, status, advice.replace('*', '')])
except: pass
