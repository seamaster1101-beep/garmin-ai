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

    # 2. Сон и Время (Исправлено: 6.9 и сохранение morning_ts)
    for d in [today_str, yesterday_str]:
        try:
            sleep_data = gar.get_sleep_data(d)
            dto = sleep_data.get("dailySleepDTO") or {}
            if dto and dto.get("sleepTimeSeconds", 0) > 0:
                slp_sc = dto.get("sleepScore") or sleep_data.get("sleepScore") or ""
                # Та самая формула для 6.9
                slp_h = round(float(dto.get("sleepTimeSeconds")) / 3600, 1)
                
                # Обновляем время только если оно есть в данных сна
                if dto.get("sleepEndTimeLocal"):
                    morning_ts = dto.get("sleepEndTimeLocal", "").replace("T", " ")[:16]
                break
        except: continue

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

# --- Дополнительная попытка получить Sleep Score ---
if not slp_sc:
    for d in [yesterday_str, today_str]:
        try:
            sleep_data = gar.get_sleep_data(d)
            if not sleep_data:
                continue

            slp_sc = (
                sleep_data.get("sleepScore")
                or sleep_data.get("overallScore", {}).get("value")
                or sleep_data.get("sleepScores", {}).get("overall")
                or sleep_data.get("sleepScores", {}).get("overall", {}).get("value")
                or ""
            )

            if slp_sc:
                break
        except:
            continue

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

# ---------- AI BLOCK: АНАЛИЗ ТРЕНДОВ ----------
advice = "Нет данных для анализа"
if GEMINI_API_KEY:
    try:
        daily_hist = ss.worksheet("Daily").get_all_values()[-5:]
        morning_hist = ss.worksheet("Morning").get_all_values()[-5:]
        history_context = f"История за 5 дней (Daily): {daily_hist}\nИстория за 5 дней (Morning): {morning_hist}"
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-lite:generateContent?key={GEMINI_API_KEY.strip()}"
        payload = {
            "contents": [{
                "parts": [{
                    "text": (
                        f"Ты — ироничный цифровой тренер. Проанализируй данные пользователя Garmin.\n"
                        f"ТЕКУЩИЕ ДАННЫЕ: HRV {hrv}, Пульс {r_hr}, Сон {slp_h}ч, Вес {weight}.\n"
                        f"КОНТЕКСТ ПРОШЛЫХ ДНЕЙ:\n{history_context}\n\n"
                        f"ЗАДАЧА: Сделай короткий вывод о состоянии и дай один мудрый, но колкий совет. Без лишнего текста."
                    )
                }]
            }]
        }
        
        time.sleep(2) # Защита от 429
        res = requests.post(url, json=payload, timeout=20)
        if res.status_code == 200:
            advice = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        elif res.status_code == 429:
            advice = "ИИ взял тайм-аут из-за лимитов."
    except Exception as e:
        print(f"AI Error: {e}")
        advice = "ИИ сегодня не в духе."

# ---------- AI ANALYSIS BLOCK (ULTIMATE AUTO-FIND) ----------

# ---------- AI BLOCK (ИСПРАВЛЕННЫЙ: БЕЗ ВНЕШНИХ БИБЛИОТЕК) ----------
ai_advice = "Нет данных для анализа"

if GEMINI_API_KEY:
    try:
        # Подготовка данных для промпта
        workout_info = f"Тренировка: {activities_to_log[0][1]}, TE: {activities_to_log[0][8]}" if activities_to_log else "Тренировок не было"
        
        # Формируем промпт, используя те же переменные, что и выше в скрипте
        user_prompt = (f"Проанализируй показатели за сегодня ({today_str}): "
                       f"Сон: {slp_sc}/100 ({slp_h}ч), HRV: {hrv}, Пульс покоя: {r_hr}, "
                       f"Body Battery: {bb_morning}, Шаги: {steps}. {workout_info}. "
                       f"Дай краткую оценку восстановления и совет на завтра (2 предложения на русском).")

        # Прямой URL к API (используем flash-lite как самую быструю и стабильную)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-lite:generateContent?key={GEMINI_API_KEY.strip()}"
        
        payload = {
            "contents": [{
                "parts": [{
                    "text": user_prompt
                }]
            }]
        }

        time.sleep(2) # Защита от лимитов
        res = requests.post(url, json=payload, timeout=30)
        
        if res.status_code == 200:
            result = res.json()
            if "candidates" in result:
                ai_advice = result["candidates"][0]["content"]["parts"][0]["text"].strip()
                print("✅ ПОБЕДА! ИИ ответил.")
            else:
                ai_advice = "ИИ прислал пустой ответ."
        else:
            ai_advice = f"Ошибка API: {res.status_code}"
            print(f"❌ Ошибка API: {res.text}")

    except Exception as e:
        ai_advice = f"Ultimate Error: {str(e)[:100]}"
        print(f"❌ Ошибка ИИ: {e}")

print(f"Final AI Status: {ai_advice}")

# Записываем результат в таблицу AI_Log (если она есть)
try:
    ss.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Success", ai_advice.replace('*', '')])
except:
    pass

# ---------- TELEGRAM (ОТКЛЮЧЕНО ПО ПРОСЬБЕ) ----------
# try:
#     if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
#         msg = f"🚀 *Garmin Sync*\n💓 HRV: {hrv or 'N/A'}\n🌙 Сон: {slp_h or 'N/A'}ч\n\n🤖 {advice.replace('*', '')}"
#         requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN.strip()}/sendMessage", 
#                       json={"chat_id": TELEGRAM_CHAT_ID.strip(), "text": msg, "parse_mode": "Markdown"})
# except: pass
