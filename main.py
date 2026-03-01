#--- Garmin AI Analysis с детальным анализом тренировок и утренним отчётом

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
    """Обновляет или добавляет строку в лист"""
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
    except Exception as e: 
        return f"Err: {str(e)[:15]}"

def send_telegram_message(message):
    """Отправляет сообщение в Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        requests.post(url, json=data, timeout=10)
        return True
    except Exception as e:
        print(f"Telegram error: {e}")
        return False

def analyze_last_workout(activity_data, user_stats, rhr):
    """Анализирует последнюю тренировку с помощью ИИ"""
    if not GEMINI_API_KEY or not activity_data:
        return "Нет данных о тренировке"
    
    try:
        # Подготавливаем данные для анализа
        act_type = activity_data.get('type', 'Unknown')
        duration_h = activity_data.get('duration_h', 0)
        distance_km = activity_data.get('distance_km', 0)
        avg_hr = activity_data.get('avg_hr', 0)
        max_hr = activity_data.get('max_hr', 0)
        training_load = activity_data.get('training_load', 0)
        calories = activity_data.get('calories', 0)
        
        # Вычисляем интенсивность
        intensity = ""
        if avg_hr and rhr and float(rhr) > 0:
            intensity = round(((float(avg_hr) - float(rhr)) / (185 - float(rhr))) * 100, 1)
        
        prompt = (
            f"Ты — опытный тренер с чувством юмора. Дай короткий, остроумный анализ тренировки:\n\n"
            f"📊 ТИП: {act_type}\n"
            f"⏱️ ВРЕМЯ: {duration_h}ч\n"
            f"📍 ДИСТАНЦИЯ: {distance_km}км\n"
            f"❤️ ПУЛЬС: {avg_hr}(макс {max_hr})\n"
            f"💪 ИНТЕНСИВНОСТЬ: {intensity}%\n"
            f"⚡ НАГРУЗКА: {training_load}\n"
            f"🔥 КАЛОРИИ: {calories}\n\n"
            f"ЗАДАЧА: Дай оценку эффективности, отметь плюсы и минусы, дай ироничный совет."
            f"Ответ - максимум 3-4 предложения, очень колкие!"
        )
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-lite:generateContent?key={GEMINI_API_KEY.strip()}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        
        time.sleep(2)
        res = requests.post(url, json=payload, timeout=20)
        
        if res.status_code == 200:
            return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        elif res.status_code == 429:
            return "ИИ взял тайм-аут. Повтори позже. 🤖"
        else:
            return f"Ошибка ИИ: {res.status_code}"
    except Exception as e:
        print(f"Workout analysis error: {e}")
        return "Анализ не удался"

def morning_report(morning_data, daily_data, history_daily, history_morning):
    """Генерирует утренний отчёт на основе данных"""
    if not GEMINI_API_KEY:
        return "Нет API ключа для ИИ"
    
    try:
        # Подготавливаем текущие данные
        weight = morning_data.get('weight', 'N/A')
        rhr = morning_data.get('rhr', 'N/A')
        hrv = morning_data.get('hrv', 'N/A')
        bb = morning_data.get('bb', 'N/A')
        sleep_score = morning_data.get('sleep_score', 'N/A')
        sleep_hours = morning_data.get('sleep_hours', 'N/A')
        
        # Анализируем тренды (сравниваем с предыдущими 3 днями)
        prev_weights = []
        prev_sleep_scores = []
        
        for row in history_morning[-3:]:
            try:
                if len(row) > 1 and row[1]:
                    prev_weights.append(float(row[1]))
                if len(row) > 5 and row[5]:
                    prev_sleep_scores.append(float(row[5]))
            except:
                pass
        
        # Формируем подробный контекст
        history_text = "Последние данные:\n"
        for i, row in enumerate(history_morning[-4:]):
            if len(row) > 5:
                history_text += f"День {i}: Вес={row[1]}, RHR={row[2]}, HRV={row[3]}, Sleep Score={row[5]}\n"
        
        prompt = (
            f"Ты — утренний мотивационный тренер с ироничным тоном. Анализируй состояние пользователя:\n\n"
            f"🌅 СЕГОДНЯ:\n"
            f"⚖️ Вес: {weight}кг\n"
            f"❤️ Пульс в покое: {rhr} уд/мин\n"
            f"📊 HRV: {hrv}\n"
            f"🔋 Body Battery: {bb}%\n"
            f"😴 Сон: {sleep_hours}ч (Score: {sleep_score})\n\n"
            f"📈 КОНТЕКСТ ПРОШЛЫХ ДНЕЙ:\n{history_text}\n"
            f"ЗАДАЧА: \n"
            f"1. Оцени качество сна и восстановления\n"
            f"2. Отметь тренды (вес, пульс, HRV)\n"
            f"3. Дай ироничный совет на день (готовность к тренировкам, отдых и т.д.)\n"
            f"Ответ: 4-5 предложений максимум, очень остроумно и полезно!"
        )
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-lite:generateContent?key={GEMINI_API_KEY.strip()}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        
        time.sleep(2)
        res = requests.post(url, json=payload, timeout=20)
        
        if res.status_code == 200:
            return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        elif res.status_code == 429:
            return "ИИ отдыхает. Вернись через минуту 😴"
        else:
            return f"Ошибка: {res.status_code}"
    except Exception as e:
        print(f"Morning report error: {e}")
        return "Отчёт не удался"

# === MAIN EXECUTION ===

# Подключение к Garmin
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
except Exception as e:
    print(f"❌ Login Fail: {e}")
    exit(1)

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# --- 1. MORNING BLOCK ---
morning_ts = f"{today_str} 08:00"
weight = rhr = hrv = bb_morning = slp_sc = slp_h = ""

try:
    # HRV
    try:
        stats = gar.get_stats(today_str) or {}
        hrv = stats.get("allDayAvgHrv") or stats.get("lastNightAvgHrv") or stats.get("lastNightHrv") or ""
    except:
        pass
    
    # Сон
    for d in [today_str, yesterday_str]:
        try:
            sleep_data = gar.get_sleep_data(d)
            dto = sleep_data.get("dailySleepDTO") or {}
            if dto and dto.get("sleepTimeSeconds", 0) > 0:
                slp_sc = dto.get("sleepScore") or sleep_data.get("sleepScore") or ""
                slp_h = round(dto.get("sleepTimeSeconds", 0) / 3600, 1)
                morning_ts = dto.get("sleepEndTimeLocal", "").replace("T", " ")[:16] or morning_ts
                break
        except:
            continue
    
    # Вес (последние 3 дня)
    for i in range(3):
        d_check = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            w_data = gar.get_body_composition(d_check, today_str)
            if w_data and w_data.get('uploads'):
                weight = round(w_data['uploads'][-1].get('weight', 0) / 1000, 1)
                break
        except:
            continue
    
    # Пульс и Body Battery
    summary = gar.get_user_summary(today_str) or {}
    rhr = summary.get("restingHeartRate") or summary.get("heartRateRestingValue") or ""
    bb_morning = summary.get("bodyBatteryHighestValue") or ""
    
except Exception as e:
    print(f"⚠️ Morning Block Error: {e}")

morning_row = [morning_ts, weight, rhr, hrv, bb_morning, slp_sc, slp_h]

# --- 2. DAILY BLOCK ---
steps = steps_distance_km = cals = daily_bb = ""

try:
    summary = gar.get_user_summary(today_str) or {}
    stats = gar.get_stats(today_str) or {}
    steps_data = gar.get_daily_steps(today_str, today_str)
    steps = steps_data[0].get('totalSteps', 0) if steps_data else 0
    cals = (summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0)) or stats.get("calories") or 0
    steps_distance_km = round(steps * 0.000762, 2)
    daily_bb = summary.get("bodyBatteryMostRecentValue", "")
except Exception as e:
    print(f"⚠️ Daily Error: {e}")

daily_row = [today_str, steps, steps_distance_km, cals, rhr, daily_bb]

# --- 3. ACTIVITIES BLOCK ---
HISTORY_FILE = "history.json"
if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "r") as f:
        history = json.load(f)
else:
    history = {"processed_activity_ids": []}

processed_ids = set(history.get("processed_activity_ids", []))
activities_to_log = []
last_activity = None

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
        act_type = a.get('activityType', {}).get('typeKey', '')
        duration_h = round(a.get('duration', 0) / 3600, 2)
        distance_km = round(a.get('distance', 0) / 1000, 2)
        avg_hr = a.get('averageHR', "")
        max_hr = a.get('maxHR', "")
        
        cad = (a.get('averageBikingCadenceInRevPerMinute') or 
               a.get('averageBikingCadence') or 
               a.get('averageRunCadence') or 
               a.get('averageCadence') or "")
        
        raw_load = a.get('activityTrainingLoad') or a.get('trainingLoad') or 0
        t_load = round(float(raw_load), 1) if raw_load else ""
        
        intensity_val = ""
        try:
            if avg_hr and rhr and float(rhr) > 0:
                intensity_val = round(((float(avg_hr) - float(rhr)) / (185 - float(rhr))) * 100, 1)
        except:
            pass
        
        activity_calories = a.get('kilocalories', 0)
        
        activities_to_log.append([
            act_date_time, act_type, duration_h, distance_km, avg_hr, max_hr, 
            intensity_val, t_load, cad, activity_calories
        ])
        
        # Сохраняем последнюю активность для анализа
        if last_activity is None:
            last_activity = {
                'type': act_type,
                'duration_h': duration_h,
                'distance_km': distance_km,
                'avg_hr': avg_hr,
                'max_hr': max_hr,
                'training_load': t_load,
                'calories': activity_calories,
                'intensity': intensity_val
            }
        
        processed_ids.add(activity_id)
    
    history["processed_activity_ids"] = list(processed_ids)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)

except Exception as e:
    print(f"⚠️ Activities error: {e}")

# --- 4. WRITE TO GOOGLE SHEETS ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    credentials = Credentials.from_service_account_info(
        creds,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
    )
    ss = gspread.authorize(credentials).open("Garmin_Data")
    
    # Пишем активности
    if activities_to_log:
        act_sheet = ss.worksheet("Activities")
        for act in activities_to_log:
            act_sheet.append_row(act)
    
    # Пишем Daily и Morning
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)
    
    print("✅ Google Sheets обновлены")
    
except Exception as e:
    print(f"⚠️ Sheets error: {e}")
    ss = None

# --- 5. AI ANALYSIS BLOCK ---

morning_analysis = ""
workout_analysis = ""

# Получаем историю для анализа
if ss:
    try:
        history_morning = ss.worksheet("Morning").get_all_values()
        history_daily = ss.worksheet("Daily").get_all_values()
    except:
        history_morning = []
        history_daily = []
else:
    history_morning = []
    history_daily = []

# 🌅 УТРЕННИЙ ОТЧЁТ
print("\n" + "="*50)
print("🌅 УТРЕННИЙ ОТЧЁТ")
print("="*50)

morning_data_for_ai = {
    'weight': weight,
    'rhr': rhr,
    'hrv': hrv,
    'bb': bb_morning,
    'sleep_score': slp_sc,
    'sleep_hours': slp_h
}

morning_analysis = morning_report(morning_data_for_ai, daily_row, history_daily, history_morning)
print(f"\n{morning_analysis}\n")

# Логируем утренний отчёт
if ss:
    try:
        log_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        ss.worksheet("AI_Log").append_row([log_time, "Morning", morning_analysis.replace('*', '')])
    except:
        pass

# 💪 АНАЛИЗ ПОСЛЕДНЕЙ ТРЕНИРОВКИ
if last_activity:
    print("\n" + "="*50)
    print("💪 АНАЛИЗ ПОСЛЕДНЕЙ ТРЕНИРОВКИ")
    print("="*50)
    
    user_stats = {'rhr': rhr, 'hrv': hrv}
    workout_analysis = analyze_last_workout(last_activity, user_stats, rhr)
    print(f"\n{workout_analysis}\n")
    
    # Логируем анализ тренировки
    if ss:
        try:
            log_time = datetime.now().strftime("%Y-%m-%d %H:%M")
            ss.worksheet("AI_Log").append_row([log_time, "Workout", workout_analysis.replace('*', '')])
        except:
            pass

# --- 6. TELEGRAM NOTIFICATIONS ---
if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
    print("\n" + "="*50)
    print("📨 Отправка в Telegram...")
    print("="*50)
    
    telegram_msg = f"🌅 *Утренний отчёт*\n{morning_analysis}\n\n"
    
    if last_activity:
        telegram_msg += f"💪 *Анализ тренировки*\n{workout_analysis}"
    
    if send_telegram_message(telegram_msg):
        print("✅ Сообщение отправлено в Telegram")
    else:
        print("⚠️ Не удалось отправить в Telegram")

print("\n" + "="*50)
print("✅ СКРИПТ ЗАВЕРШЁН")
print("="*50)
