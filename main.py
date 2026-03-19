import os
import json
import requests
import garth
import time
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials

# --- CONFIG (Взято с твоего скриншота) ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")

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
            sheet.update(range_name=f"A{found_idx}", values=[row_data], value_input_option='USER_ENTERED')
        else:
            sheet.append_row(row_data, value_input_option='USER_ENTERED')
    except Exception as e: 
        print(f"Err gspread update: {e}")

# --- ЛОГИН GARMIN (Hybrid Edition) ---
session_dir = "./.garth"
gar = None

if os.path.exists(session_dir) and os.listdir(session_dir):
    try:
        print("✅ Найдена сохраненная сессия. Пробуем тихий вход...")
        garth.resume(session_dir)
        gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD) 
        gar.garth = garth.client
        gar.display_name = garth.client.username
        print(f"🚀 Успех! Сессия восстановлена для пользователя.")
    except Exception as e:
        print(f"⚠️ Сессия из кэша не подошла: {e}")
        gar = None

if gar is None:
    print("🔑 Пробуем вход по логину и паролю...")
    for attempt in range(2):
        try:
            gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
            gar.login(session_dir) 
            print("💾 Вход успешен, токены обновлены!")
            break
        except Exception as e:
            if "429" in str(e):
                print("⏳ 429... ждем минуту.")
                time.sleep(60)
            else: 
                raise e

if not gar:
    raise Exception("Критическая ошибка: не удалось подключиться к Garmin.")

# --- ИНИЦИАЛИЗАЦИЯ GOOGLE SHEETS (Критически важно: ДО аналитики) ---
try:
    if not GOOGLE_CREDS_JSON:
        raise ValueError("Секрет GOOGLE_CREDS пуст или не найден!")
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    credentials = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(credentials).open("Garmin_Data") # Проверь имя таблицы в Google!
    print("✅ Таблица Google Sheets подключена!")
except Exception as e:
    print(f"🚨 Ошибка Google Sheets: {e}")
    raise e

# --- 1. СБОР ПЕРВИЧНЫХ ДАННЫХ ---
try:
    summary = gar.get_user_summary(today_str) or {}
except Exception as e:
    print(f"⚠️ Сводка дня заблокирована (403), используем пустую: {e}")
    summary = {}

try:
    stats = gar.get_stats(today_str) or {}
except:
    stats = {}

hrv_res = gar.get_hrv_data(today_str) or {}
hrv = hrv_res.get("hrvSummary", {}).get("lastNightAvg") or ""
r_hr = summary.get("restingHeartRate") or ""

# --- 2. ВЕС ---
weight, fat, muscle = "", "", ""
try:
    w_data = gar.get_body_composition((now - timedelta(days=3)).strftime("%Y-%m-%d"), today_str) or {}
    weights = w_data.get('dateWeightList', [])
    if weights:
        actual_entry = max(weights, key=lambda x: x.get('sampleTime', x.get('date', 0)))
        weight = round(float(actual_entry.get('weight', 0)) / 1000, 1)
        fat = actual_entry.get('bodyFat', "")
        raw_m = actual_entry.get('muscleMass')
        if raw_m: muscle = round(float(raw_m) / 1000, 1)
except: pass

# --- 3. СОН ---
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
morning_ts = f"{today_str} 08:00"
slp_sc, slp_h = "", ""

for d in [today_str, yesterday_str]:
    try:
        sleep_data = gar.get_sleep_data(d) or {}
        dto = sleep_data.get("dailySleepDTO") or {}
        if dto and dto.get("sleepTimeSeconds", 0) > 0:
            slp_h = round(float(dto.get("sleepTimeSeconds")) / 3600, 1)
            scores = dto.get("sleepScores") or {}
            slp_sc = scores.get("overall", {}).get("value") or dto.get("sleepScore") or ""
            raw_ts = dto.get("sleepEndTimestampLocal")
            if raw_ts: morning_ts = str(raw_ts).replace("T", " ")[:16]
            break
    except: continue

# Fitness Age (упрощенно для стабильности)
fit_age = "62"
morning_bb_max = summary.get("bodyBatteryHighestValue") or summary.get("bodyBatteryMostRecentValue", "")
morning_row = [f"'{morning_ts}", weight, fat, muscle, r_hr, hrv, morning_bb_max, slp_sc, slp_h, 62, fit_age]

steps = summary.get('totalSteps', 0)
daily_dist = round(steps * 0.000762, 2)
cals = int(summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0))
daily_row = [f"'{today_str}", steps, daily_dist, cals, r_hr, summary.get("bodyBatteryMostRecentValue", "")]

# --- 4. ACTIVITIES ---
activities_to_log = []
try:
    latest_activities = gar.get_activities(0, 5) or []
    for a in latest_activities:
        act_id = str(a.get("activityId"))
        np_val = a.get('normPower') or a.get('weightedAveragePower', "")
        avg_pwr = a.get('avgPower', "")
        vi_val = round(float(np_val) / float(avg_pwr), 2) if np_val and avg_pwr and float(avg_pwr) > 0 else ""
        
        row_data = [
            a.get("startTimeLocal", "").replace("T", " ")[:16], a.get('activityType', {}).get('typeKey', ''), 
            round(a.get('duration', 0) / 3600, 2), round(a.get('distance', 0) / 1000, 2),
            a.get('averageHR', ""), a.get('maxHR', ""), 
            round(float(a.get('intensityFactor', 0)), 3) if a.get('intensityFactor') else "", 
            round(float(a.get('activityTrainingLoad', 0)), 1),
            round(float(a.get('aerobicTrainingEffect', 0)), 1), a.get('calories', ""),
            avg_pwr, a.get('averageBikingCadence') or "",
            round(float(np_val), 1) if np_val else "", round(float(a.get('trainingStressScore', 0)), 1) if a.get('trainingStressScore') else "", 
            vi_val, f"'{act_id}"
        ]
        activities_to_log.append({"id": act_id, "row": row_data})
except Exception as e: print(f"Act Err: {e}")

# --- 5. ANALYTICS (CTL/ATL/TSB) ---
ctl = atl = tsb = readiness_score = 0
readiness_text = "Данных для анализа пока недостаточно"
try:
    act_sheet = ss.worksheet("Activities")
    rows = act_sheet.get_all_values()[1:]
    tss_list = [float(r[13]) for r in rows[-60:] if len(r) > 13 and r[13]]
    
    if tss_list:
        def ewma(data, days):
            alpha = 2 / (days + 1)
            res = data[0]
            for x in data[1:]: res = alpha * x + (1 - alpha) res
            return res
        ctl = round(ewma(tss_list, 42), 1)
        atl = round(ewma(tss_list, 7), 1)
        tsb = round(ctl - atl, 1)
        if tsb > 5: readiness_text = "🔥 Отличная готовность!"
        elif tsb < -15: readiness_text = "⚠️ Нужен отдых"
        else: readiness_text = "👍 Можно тренироваться"
except Exception as e: print(f"Analytics Err: {e}")

# --- 6. AI BLOCK ---
ai_advice = "SKIP"
log_sheet = ss.worksheet("AI_Log")
morning_done = any(today_str in row[0] and "Morning" in row[1] for row in log_sheet.get_all_values()[-20:])

if activities_to_log:
    report_type = "Activity"
    act = activities_to_log[0]['row']
    prompt = f"Разбери заезд: {act[1]}, {act[3]}км, TSS {act[13]}, IF {act[6]}. Будь краток и профессионален."
elif not morning_done:
    report_type = "Morning"
    prompt = f"Утренний отчет: HRV {hrv}, RHR {r_hr}, Сон {slp_h}ч. Дай краткий совет."
else: prompt = None

if GEMINI_API_KEY and prompt:
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        res_ai = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=25)
        ai_advice = res_ai.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except: ai_advice = "Совет временно недоступен"

# --- 7. ЗАПИСЬ И TELEGRAM ---
try:
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    
    a_sheet = ss.worksheet("Activities")
    exist = {r[15] for r in a_sheet.get_all_values() if len(r) > 15}
    for a in activities_to_log:
        if a["id"] not in exist: a_sheet.append_row(a["row"], value_input_option='USER_ENTERED')

    if ai_advice != "SKIP":
        clean_ai = ai_advice.replace('*', '')
        log_sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), report_type, clean_ai])
        
        header = "НОВАЯ ТРЕНИРОВКА 🚴‍♂️" if report_type == "Activity" else "ДОБРОЕ УТРО 🌞"
        msg = f"**{header}**\nCTL: {ctl} | ATL: {atl} | TSB: {tsb}\n{readiness_text}\n\n{clean_ai}"
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
    print("🚀 Миссия выполнена!")
except Exception as e:
    print(f"🚨 Финальная ошибка: {e}")
