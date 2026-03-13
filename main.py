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

# --- 1. MORNING BLOCK (ИСПРАВЛЕННЫЙ) ---
# Инициализируем дату сразу, чтобы она не пропала при ошибках
morning_ts = f"{today_str} 08:00"
weight, r_hr, hrv, bb_morning, slp_sc, slp_h = "", "", "", "", "", ""

try:
    # 1. HRV
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

# 2. Сон и Sleep Score (Твой рабочий метод)
    for d in [today_str, yesterday_str]:
        sleep_data = gar.get_sleep_data(d) or {}
        dto = sleep_data.get("dailySleepDTO") or {}
        if dto and dto.get("sleepTimeSeconds", 0) > 0:
            slp_h = round(float(dto.get("sleepTimeSeconds")) / 3600, 1)
            # Тот самый путь, который сработал:
            scores = dto.get("sleepScores") or {}
            slp_sc = scores.get("overall", {}).get("value") or dto.get("sleepScore") or ""
            if dto.get("sleepEndTimestampLocal"):
                morning_ts = dto.get("sleepEndTimestampLocal", "").replace("T", " ")[:16]
            break

    # 3. Вес (Исправлена опечатка w_res)
    for i in range(5):
        d_check = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            w_data = gar.get_body_composition(d_check)
            if w_data and w_data.get('uploads'):
                val = w_data['uploads'][-1].get('weight', 0)
                if val > 0:
                    weight = round(val / 1000, 1)
                    break
        except: continue

    # 4. Пульс и BB
    summary = gar.get_user_summary(today_str) or {}
    r_hr = summary.get("restingHeartRate") or summary.get("heartRateRestingValue") or ""
    bb_morning = summary.get("bodyBatteryHighestValue") or ""

except Exception as e:
    print(f"Morning Block Minor Error: {e}")

# Финальная сборка строки (morning_ts теперь точно не пустой)
morning_row = [morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h]

# --- ОБНОВЛЕННЫЙ DEBUG DATA ---
print("\n--- FINAL RADAR REPORT ---")
print(f"Weight: {weight}")
print(f"Sleep Score: {slp_sc}")
print(f"Sleep Hours: {slp_h}")

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

# --- 3. ACTIVITIES (С сохранением в историю) ---
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
        cad = (a.get('averageBikingCadenceInRevPerMinute') or a.get('averageBikingCadence') or 
               a.get('averageRunCadence') or a.get('averageCadence') or "")
        raw_load = (a.get('activityTrainingLoad') or a.get('trainingLoad') or 0)
        t_load = round(float(raw_load), 1)
        avg_hr = a.get('averageHR', "")
        max_hr = a.get('maxHR', "")
        
        intensity_val = ""
        try:
            if avg_hr and r_hr and float(r_hr) > 0:
                intensity_val = round(((float(avg_hr) - float(r_hr)) / (185 - float(r_hr))) * 100, 1)
        except: pass
        
        activities_to_log.append([
            act_date_time, a.get('activityType', {}).get('typeKey', ''), 
            round(a.get('duration', 0) / 3600, 2), round(a.get('distance', 0) / 1000, 2), 
            avg_hr, max_hr, intensity_val, t_load, 
            round(float(a.get('aerobicTrainingEffect', 0)), 1), a.get('calories', ""), 
            a.get('avgPower', ""), cad, activity_id
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
    
    # Синхронизация Daily/Morning
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)
    
    # Запись активностей
    act_sheet = ss.worksheet("Activities")
    existing_keys = {f"{r[0]}_{r[1]}_{r[12]}" for r in act_sheet.get_all_values() if len(r) > 12}
    for act in activities_to_log:
        key = f"{act[0]}_{act[1]}_{act[12]}"
        if key not in existing_keys: act_sheet.append_row(act)
    print("✅ Данные Garmin синхронизированы с Google Sheets")
except Exception as e: print("Sheets write error:", e)

# ---------- БЛОК ИИ С АВТОПОДБОРОМ МОДЕЛИ ----------
ai_advice = "Нет данных"
if not GEMINI_API_KEY:
    ai_advice = "Ошибка: API Ключ не найден"
else:
    try:
        print("🔍 Шаг 1: Опрашиваю список доступных моделей...")
        list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY.strip()}"
        list_res = requests.get(list_url, timeout=20)
        
        target_model = "models/gemini-1.5-flash" # Резерв
        if list_res.status_code == 200:
            models_data = list_res.json().get("models", [])
            # Ищем любую модель, поддерживающую генерацию контента
            available = [m["name"] for m in models_data if "generateContent" in m.get("supportedGenerationMethods", [])]
            if available:
                # Приоритет: flash -> pro -> любая первая
                target_model = next((m for m in available if "flash" in m), 
                                    next((m for m in available if "pro" in m), available[0]))
                print(f"✅ Найдена рабочая модель: {target_model}")
            else:
                print("⚠️ Список моделей пуст, пробую стандарт.")
        else:
            print(f"⚠️ Не удалось получить список моделей ({list_res.status_code}), пробую стандарт.")

        print(f"⏳ Шаг 2: Запрос к {target_model}...")
        time.sleep(5)
        
        prompt = (f"Ты ироничный тренер. Данные: HRV {hrv}, Пульс {r_hr}, Сон {slp_h}ч, BB {bb_morning}.\n"
                  f"Дай 1 колкий совет на русском (до 2 предложений).")

        # Формируем URL динамически на основе найденной модели
        url = f"https://generativelanguage.googleapis.com/v1beta/{target_model}:generateContent?key={GEMINI_API_KEY.strip()}"
        res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
        
        if res.status_code == 200:
            ai_advice = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            print("✅ ИИ ответил!")
        else:
            ai_advice = f"Ошибка API {res.status_code}"
            print(f"❌ Финальный отказ: {res.text}")

    except Exception as e:
        ai_advice = f"Ошибка выполнения: {str(e)[:50]}"

# --- РЕНТГЕН ДАННЫХ (Для Weight и Sleep Score) ---
print("\n--- DEBUG DATA ---")
print(f"Weight Raw: {weight}")
print(f"Sleep Score Raw: {slp_sc}")
print(f"Sleep Hours: {slp_h}")
# --------------------------------------------------

# Запись в AI_Log
try:
    ss.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Info", ai_advice.replace('*', '')])
except: pass
    
# ---------- TELEGRAM (ОТКЛЮЧЕНО ПО ПРОСЬБЕ) ----------
# try:
#     if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
#         msg = f"🚀 *Garmin Sync*\n💓 HRV: {hrv or 'N/A'}\n🌙 Сон: {slp_h or 'N/A'}ч\n\n🤖 {advice.replace('*', '')}"
#         requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN.strip()}/sendMessage", 
#                       json={"chat_id": TELEGRAM_CHAT_ID.strip(), "text": msg, "parse_mode": "Markdown"})
# except: pass
