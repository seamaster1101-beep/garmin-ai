#--- Garmin AI Analysis с полной диагностикой

import os
import json
import time
import traceback
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import requests

MAX_RETRIES = 3
INITIAL_DELAY = 3
BACKOFF_MULTIPLIER = 2
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def log_debug(message):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {message}")

def call_gemini_api(prompt, max_retries=MAX_RETRIES):
    if not GEMINI_API_KEY:
        return None, "Нет API ключа"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-lite:generateContent?key={GEMINI_API_KEY.strip()}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    delay = INITIAL_DELAY
    for attempt in range(max_retries):
        try:
            log_debug(f"  📡 API запрос (попытка {attempt + 1}/{max_retries})...")
            res = requests.post(url, json=payload, timeout=30)
            if res.status_code == 200:
                result = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                log_debug(f"  ✅ Ответ получен ({len(result)} символов)")
                return result, "success"
            elif res.status_code == 429:
                log_debug(f"  ⏳ Rate limit, жду {delay}с...")
                time.sleep(delay)
                delay *= BACKOFF_MULTIPLIER
                continue
            elif res.status_code == 503:
                log_debug(f"  ⏳ Сервис недоступен, жду {delay}с...")
                time.sleep(delay)
                delay *= BACKOFF_MULTIPLIER
                continue
            else:
                log_debug(f"  ❌ Ошибка {res.status_code}")
                return None, f"API error {res.status_code}"
        except requests.Timeout:
            log_debug(f"  ⏳ Timeout, жду {delay}с...")
            time.sleep(delay)
            delay *= BACKOFF_MULTIPLIER
            continue
        except Exception as e:
            log_debug(f"  ❌ Exception: {str(e)[:50]}")
            return None, f"Error: {str(e)[:50]}"
    return None, f"Не удалось после {max_retries} попыток"

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        res = requests.post(url, json=data, timeout=10)
        return res.status_code == 200
    except Exception as e:
        log_debug(f"❌ Telegram error: {e}")
        return False

def analyze_last_workout(activity_data, rhr):
    if not activity_data:
        return "Нет данных о тренировке"
    try:
        act_type = activity_data.get('type', 'Unknown')
        duration_h = activity_data.get('duration_h', 0)
        distance_km = activity_data.get('distance_km', 0)
        avg_hr = activity_data.get('avg_hr', 0)
        max_hr = activity_data.get('max_hr', 0)
        training_load = activity_data.get('training_load', 0)
        calories = activity_data.get('calories', 0)
        intensity = ""
        if avg_hr and rhr and float(rhr) > 0:
            try:
                intensity = round(((float(avg_hr) - float(rhr)) / (185 - float(rhr))) * 100, 1)
            except:
                pass
        prompt = (f"Ты — опытный тренер с чувством юмора. Дай короткий, остроумный анализ тренировки:\n\n"
                  f"📊 ТИП: {act_type}\n"
                  f"⏱️ ВРЕМЯ: {duration_h}ч\n"
                  f"📍 ДИСТАНЦИЯ: {distance_km}км\n"
                  f"❤️ ПУЛЬС: {avg_hr}(макс {max_hr})\n"
                  f"💪 ИНТЕНСИВНОСТЬ: {intensity}%\n"
                  f"⚡ НАГРУЗКА: {training_load}\n"
                  f"🔥 КАЛОРИИ: {calories}\n\n"
                  f"ЗАДАЧА: Дай оценку эффективности (2-3 предложения), остроумно и колко!")
        result, status = call_gemini_api(prompt)
        return result if result else f"Анализ не удался: {status}"
    except Exception as e:
        log_debug(f"❌ Workout analysis error: {e}")
        return f"Ошибка анализа: {str(e)[:30]}"

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
                except:
                    pass
        prompt = (f"Ты — утренний мотивационный тренер с ироничным тоном. Анализируй состояние:\n\n"
                  f"🌅 СЕГОДНЯ:\n"
                  f"⚖️ Вес: {weight}кг\n"
                  f"❤️ Пульс в покое: {rhr} уд/мин\n"
                  f"📊 HRV: {hrv}\n"
                  f"🔋 Body Battery: {bb}%\n"
                  f"😴 Сон: {sleep_hours}ч (Оценка: {sleep_score})\n\n"
                  f"📈 КОНТЕКСТ:\n{history_text}\n"
                  f"ЗАДАЧА: Оцени восстановление, отметь тренды и дай ироничный совет.\n"
                  f"Ответ: 3-4 предложения максимум!")
        result, status = call_gemini_api(prompt)
        return result if result else f"Отчёт не удался: {status}"
    except Exception as e:
        log_debug(f"❌ Morning report error: {e}")
        return f"Ошибка отчёта: {str(e)[:30]}"

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
            log_debug(f"  ✏️ Обновлена строка {found_idx}")
            return "Updated"
        else:
            sheet.append_row(row_data)
            log_debug(f"  ➕ Добавлена новая строка")
            return "Appended"
    except Exception as e:
        log_debug(f"  ❌ update_or_append error: {e}")
        return f"Err: {str(e)[:15]}"

def safe_log_to_sheets(ss, log_type, message):
    try:
        log_sheet = ss.worksheet("AI_Log")
        log_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        clean_msg = str(message).replace('*', '').replace('_', '').strip()
        log_sheet.append_row([log_time, log_type, clean_msg[:500]])
        log_debug(f"  ✅ Залогировано в AI_Log: {log_type}")
        return True
    except Exception as e:
        log_debug(f"  ❌ AI_Log error: {e}")
        return False

log_debug("🚀 Начало выполнения скрипта")
try:
    log_debug("📱 Подключение к Garmin...")
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
    log_debug("✅ Успешно авторизован в Garmin")
except Exception as e:
    log_debug(f"❌ Garmin login failed: {e}")
    exit(1)

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
log_debug(f"📅 Сегодня: {today_str}, Вчера: {yesterday_str}")

log_debug("\n📋 === БЛОК: УТРО ===")
morning_ts = f"{today_str} 08:00"
weight = rhr = hrv = bb_morning = slp_sc = slp_h = ""

try:
    log_debug("  🔍 Ищу HRV...")
    try:
        hrv_res = gar.get_hrv_data(today_str)
        if hrv_res and "hrvSummary" in hrv_res:
            hrv = hrv_res.get("hrvSummary", {}).get("lastNightAvg") or ""
            log_debug(f"    ✅ HRV найден через get_hrv_data: {hrv}")
    except:
        pass
    if not hrv:
        try:
            stats = gar.get_stats(today_str) or {}
            hrv = (stats.get("allDayAvgHrv") or stats.get("lastNightAvgHrv") or stats.get("lastNightHrv") or "")
            if hrv:
                log_debug(f"    ✅ HRV найден через get_stats: {hrv}")
        except:
            pass
    if not hrv:
        log_debug(f"    ⚠️ HRV не найден")

    log_debug("  🔍 Ищу Sleep Score...")
    for d in [yesterday_str, today_str]:
        try:
            sleep_data = gar.get_sleep_data(d)
            if not sleep_data:
                continue
            dto = sleep_data.get("dailySleepDTO") or {}
            if dto and dto.get("sleepTimeSeconds", 0) > 0:
                slp_sc = (dto.get("sleepScore") or sleep_data.get("sleepScore") or sleep_data.get("overallScore", {}).get("value") or "")
                slp_h = round(dto.get("sleepTimeSeconds", 0) / 3600, 1)
                morning_ts = dto.get("sleepEndTimeLocal", "").replace("T", " ")[:16] or morning_ts
                log_debug(f"    ✅ Сон найден за {d}: Score={slp_sc}, Hours={slp_h}")
                break
        except Exception as e:
            log_debug(f"    ⚠️ Ошибка для дня {d}: {str(e)[:30]}")
            continue
    if not slp_sc or not slp_h:
        log_debug(f"    ⚠️ Sleep данные неполные: Score={slp_sc}, Hours={slp_h}")

    log_debug("  🔍 Ищу Weight...")
    for i in range(5):
        d_check = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            w_data = gar.get_body_composition(d_check, today_str)
            if w_data and isinstance(w_data, dict) and w_data.get('uploads'):
                w = round(w_data['uploads'][-1].get('weight', 0) / 1000, 1)
                if w > 0:
                    weight = w
                    log_debug(f"    ✅ Weight найден за {d_check}: {weight}кг")
                    break
        except Exception as e:
            log_debug(f"    ⚠️ Ошибка для дня {d_check}: {str(e)[:30]}")
            continue
    if not weight:
        log_debug(f"    ⚠️ Weight не найден")

    log_debug("  🔍 Ищу RHR и Body Battery...")
    summary = gar.get_user_summary(today_str) or {}
    rhr = summary.get("restingHeartRate") or summary.get("heartRateRestingValue") or ""
    bb_morning = summary.get("bodyBatteryHighestValue") or ""
    if rhr:
        log_debug(f"    ✅ RHR: {rhr}")
    if bb_morning:
        log_debug(f"    ✅ Body Battery: {bb_morning}")
except Exception as e:
    log_debug(f"❌ Morning Block Error: {e}\n{traceback.format_exc()}")

morning_row = [morning_ts, weight, rhr, hrv, bb_morning, slp_sc, slp_h]
log_debug(f"📦 Morning Row: {morning_row}")

log_debug("\n📋 === БЛОК: ДЕНЬ ===")
steps = steps_distance_km = cals = daily_bb = ""
try:
    summary = gar.get_user_summary(today_str) or {}
    stats = gar.get_stats(today_str) or {}
    steps_data = gar.get_daily_steps(today_str, today_str)
    steps = steps_data[0].get('totalSteps', 0) if steps_data else 0
    cals = (summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0)) or 0
    steps_distance_km = round(steps * 0.000762, 2) if steps else 0
    daily_bb = summary.get("bodyBatteryMostRecentValue", "")
    log_debug(f"  ✅ Steps: {steps}, Distance: {steps_distance_km}км, Calories: {cals}")
except Exception as e:
    log_debug(f"❌ Daily Error: {e}")

daily_row = [today_str, steps, steps_distance_km, cals, rhr, daily_bb]
log_debug(f"📦 Daily Row: {daily_row}")

log_debug("\n📋 === БЛОК: АКТИВНОСТИ ===")
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
    latest_activities = gar.get_activities(0, 20)
    log_debug(f"  📍 Найдено активностей: {len(latest_activities)}")
    activities_today = []
    for a in latest_activities:
        activity_id = str(a.get("activityId"))
        start_local = a.get("startTimeLocal", "")
        if not start_local.startswith(today_str):
            continue
        if activity_id in processed_ids:
            log_debug(f"  ⏭️ ID {activity_id[:8]}... уже обработан")
            continue
        act_date_time = start_local.replace("T", " ")[:16]
        act_type = a.get('activityType', {}).get('typeKey', 'unknown')
        duration_h = round(a.get('duration', 0) / 3600, 2)
        distance_km = round(a.get('distance', 0) / 1000, 2)
        avg_hr = a.get('averageHR', "")
        max_hr = a.get('maxHR', "")
        cad = (a.get('averageBikingCadenceInRevPerMinute') or a.get('averageBikingCadence') or a.get('averageRunCadence') or a.get('averageCadence') or "")
        raw_load = a.get('activityTrainingLoad') or a.get('trainingLoad') or 0
        t_load = round(float(raw_load), 1) if raw_load else ""
        intensity_val = ""
        try:
            if avg_hr and rhr and float(rhr) > 0:
                intensity_val = round(((float(avg_hr) - float(rhr)) / (185 - float(rhr))) * 100, 1)
        except:
            pass
        activity_calories = a.get('kilocalories', 0)
        activities_today.append({'row': [act_date_time, act_type, duration_h, distance_km, avg_hr, max_hr, intensity_val, t_load, cad, activity_calories], 'start_time': start_local, 'data': {'type': act_type, 'duration_h': duration_h, 'distance_km': distance_km, 'avg_hr': avg_hr, 'max_hr': max_hr, 'training_load': t_load, 'calories': activity_calories, 'intensity': intensity_val}})
        log_debug(f"  ✅ Активность: {act_type} ({duration_h}ч), {distance_km}км, HR={avg_hr}")
        processed_ids.add(activity_id)
    activities_today.sort(key=lambda x: x['start_time'])
    for i, act_dict in enumerate(activities_today):
        activities_to_log.append(act_dict['row'])
        if i == 0:
            last_activity = act_dict['data']
    log_debug(f"  📊 Сортировано активностей: {len(activities_today)} (ранние выше)")
    history["processed_activity_ids"] = list(processed_ids)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)
except Exception as e:
    log_debug(f"❌ Activities error: {e}\n{traceback.format_exc()}")

log_debug("\n📋 === БЛОК: GOOGLE SHEETS ===")
ss = None
try:
    log_debug("  🔗 Подключение к Google Sheets...")
    creds = json.loads(GOOGLE_CREDS_JSON)
    credentials = Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(credentials).open("Garmin_Data")
    log_debug("  ✅ Подключено к Google Sheets")
    if activities_to_log:
        log_debug(f"  📝 Пишу {len(activities_to_log)} активность(-ей)...")
        act_sheet = ss.worksheet("Activities")
        for act in activities_to_log:
            act_sheet.append_row(act)
        log_debug(f"  ✅ Активности добавлены")
    log_debug("  📝 Обновляю Morning...")
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)
    log_debug("  📝 Обновляю Daily...")
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    log_debug("✅ Google Sheets успешно обновлены")
except Exception as e:
    log_debug(f"❌ Sheets sync error: {e}\n{traceback.format_exc()}")
    ss = None

log_debug("\n📋 === БЛОК: AI АНАЛИЗ ===")
morning_analysis = ""
workout_analysis = ""
history_morning = []
if ss:
    try:
        history_morning = ss.worksheet("Morning").get_all_values()[1:]
    except Exception as e:
        log_debug(f"  ⚠️ Не могу получить историю Morning: {e}")

log_debug("\n🌅 === УТРЕННИЙ ОТЧЁТ ===")
morning_data_for_ai = {'weight': weight, 'rhr': rhr, 'hrv': hrv, 'bb': bb_morning, 'sleep_score': slp_sc, 'sleep_hours': slp_h}
log_debug(f"  📊 Данные: {morning_data_for_ai}")
morning_analysis = morning_report(morning_data_for_ai, history_morning)
log_debug(f"\n{morning_analysis}\n")
if ss:
    safe_log_to_sheets(ss, "Morning", morning_analysis)

if last_activity and last_activity.get('type'):
    log_debug("\n💪 === АНАЛИЗ ТРЕНИРОВКИ ===")
    log_debug(f"  📊 Данные: {last_activity}")
    workout_analysis = analyze_last_workout(last_activity, rhr)
    log_debug(f"\n{workout_analysis}\n")
    if ss:
        safe_log_to_sheets(ss, "Workout", workout_analysis)
else:
    log_debug("\n💪 === АНАЛИЗ ТРЕНИРОВКИ ===")
    log_debug("  ⚠️ Нет тренировок сегодня")

