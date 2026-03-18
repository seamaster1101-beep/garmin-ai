import os
import json
import requests
import garth
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials

# --- CONFIG ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_SHEETS_CREDS")

# СРАЗУ ОПРЕДЕЛЯЕМ ВРЕМЯ (до логина!)
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
    except Exception as e: print(f"Err: {e}")

# --- УЛЬТРА-СТАБИЛЬНЫЙ ЛОГИН (Hybrid Edition) ---
import garth
import time

session_dir = "./.garth"
gar = None

# 1. Пытаемся поднять сессию (самый быстрый и безопасный путь)
if os.path.exists(session_dir) and os.listdir(session_dir):
    try:
        garth.resume(session_dir)
        gar = Garmin()
        gar.login() # Если сессия жива, это пройдет мгновенно
        print("✅ Сессия восстановлена из кэша")
    except Exception as e:
        print(f"⚠️ Сессия не подошла: {e}")
        gar = None

# 2. Если кэша нет или он протух — заходим по паролю
if gar is None:
    for attempt in range(2): # 2 попытки вполне хватит
        try:
            print(f"🔑 Попытка входа {attempt+1}...")
            gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
            gar.login()
            garth.save(session_dir)
            print("💾 Вход успешен, сессия сохранена!")
            break
        except Exception as e:
            if "429" in str(e) and attempt == 0:
                print("⏳ Поймали 429. Ждем 60 сек и пробуем последний раз...")
                time.sleep(60)
            else:
                print(f"❌ Не удалось войти: {e}")
                raise

# --- 1. ПЕРВИЧНЫЕ ДАННЫЕ ---
summary = gar.get_user_summary(today_str) or {}
hrv_res = gar.get_hrv_data(today_str) or {}
hrv = hrv_res.get("hrvSummary", {}).get("lastNightAvg") or ""
r_hr = summary.get("restingHeartRate") or ""

# --- 2. ВЕС, ЖИР, МЫШЦЫ ---
weight, fat, muscle = "", "", ""
try:
    w_data = gar.get_body_composition((now - timedelta(days=3)).strftime("%Y-%m-%d"), today_str) or {}
    weights = w_data.get('dateWeightList', [])
    if weights:
        actual_entry = max(weights, key=lambda x: x.get('sampleTime', x.get('date', 0)))
        weight = round(float(actual_entry.get('weight', 0)) / 1000, 1)
        fat = actual_entry.get('bodyFat', "")
        raw_m = actual_entry.get('muscleMass')
        if raw_m:
            muscle = round(float(raw_m) / 1000, 1)
except Exception as e:
    print(f"Ошибка парсинга весов: {e}")

# --- 3. СОН И ВРЕМЯ ---
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
            if raw_ts:
                if isinstance(raw_ts, (int, float)):
                    morning_ts = datetime.fromtimestamp(raw_ts / 1000).strftime("%Y-%m-%d %H:%M")
                else:
                    morning_ts = str(raw_ts).replace("T", " ")[:16]
            break
    except:
        continue

# --- 4. FITNESS AGE ---
fit_age = ""
try:
    actual_age = 62
    rhr_val = int(r_hr) if r_hr else 60
    rhr_impact = (rhr_val - 55) * 0.4
    fat_val = float(fat) if fat else 25
    fat_impact = (fat_val - 22) * 0.5
    hrv_val = int(hrv) if hrv else 40
    hrv_impact = (hrv_val - 45) * 0.1
    calculated = actual_age + rhr_impact + fat_impact - hrv_impact
    fit_age = round(max(45, min(actual_age + 5, calculated)), 1)
except:
    fit_age = "62"
        
# --- 5. ФОРМИРОВАНИЕ СТРОК ---
morning_bb_max = summary.get("bodyBatteryHighestValue") or summary.get("bodyBatteryMostRecentValue", "")
real_age = 62 

morning_row = [f"'{morning_ts}", weight, fat, muscle, r_hr, hrv, morning_bb_max, slp_sc, slp_h, real_age, fit_age]

steps = summary.get('totalSteps', 0)
daily_dist = round(steps * 0.000762, 2)
cals = int(summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0))
current_bb = summary.get("bodyBatteryMostRecentValue", "")

daily_row = [f"'{today_str}", steps, daily_dist, cals, r_hr, current_bb]

# --- 6. ACTIVITIES ---
activities_to_log = []
try:
    latest_activities = gar.get_activities(0, 5) or []
    for a in latest_activities:
        start_local = a.get("startTimeLocal", "")
        if not start_local.startswith(today_str): continue
        
        act_id = str(a.get("activityId"))
        np_val = a.get('normPower') or a.get('weightedAveragePower', "")
        if_val = a.get('intensityFactor')
        tss_val = a.get('trainingStressScore')
        avg_pwr = a.get('avgPower', "")
        
        vi_val = ""
        if np_val and avg_pwr and float(avg_pwr) > 0:
            vi_val = round(float(np_val) / float(avg_pwr), 2)

        row_data = [
            start_local.replace("T", " ")[:16], a.get('activityType', {}).get('typeKey', ''), 
            round(a.get('duration', 0) / 3600, 2), round(a.get('distance', 0) / 1000, 2),
            a.get('averageHR', ""), a.get('maxHR', ""), 
            round(float(if_val), 3) if if_val else "", round(float(a.get('activityTrainingLoad', 0)), 1),
            round(float(a.get('aerobicTrainingEffect', 0)), 1), a.get('calories', ""),
            avg_pwr, a.get('averageBikingCadence') or "",
            round(float(np_val), 1) if np_val else "", round(float(tss_val), 1) if tss_val else "", 
            vi_val, f"'{act_id}"
        ]
        activities_to_log.append({"id": act_id, "row": row_data})
except Exception as e:
    print(f"Activity Error: {e}")

# --- 7. AI BLOCK ---
ai_advice = ""
report_type = ""

creds_dict = json.loads(GOOGLE_CREDS_JSON)
credentials = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
ss = gspread.authorize(credentials).open("Garmin_Data")
log_sheet = ss.worksheet("AI_Log")
last_logs = log_sheet.get_all_values()

morning_done_today = any(today_str in row[0] and "Morning" in row[1] for row in last_logs)

if activities_to_log:
    report_type = "Activity"
    act = activities_to_log[0]['row']
    prompt = (f"Ты — опытный спортивный коуч. Разбери сессию: {act[1]}, {act[3]}км, NP {act[12]}Вт, TSS {act[13]}, IF {act[6]}. Твой стиль: профессиональный, мотивирующий.")
elif not morning_done_today:
    report_type = "Morning"
    prompt = (f"Ты — личный спортивный врач. HRV {morning_row[5]}, Пульс {morning_row[4]}, Сон {morning_row[8]}ч, BB {morning_row[6]}, Fit Age {morning_row[10]}. Дай краткую оценку состояния.")
else:
    ai_advice = "SKIP"

if GEMINI_API_KEY and ai_advice != "SKIP":
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        res_ai = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
        data = res_ai.json()
        if "candidates" in data:
            ai_advice = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        ai_advice = f"Ошибка ИИ: {e}"

# --- 8. ЗАПИСЬ И TELEGRAM ---
try:
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    
    act_sheet = ss.worksheet("Activities")
    existing_ids = {r[15] for r in act_sheet.get_all_values() if len(r) > 15}
    for act in activities_to_log:
        if act["id"] not in existing_ids:
            act_sheet.append_row(act["row"], value_input_option='USER_ENTERED')
    
    if ai_advice and ai_advice != "SKIP":
        clean_ai = ai_advice.replace('*', '')
        log_sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), report_type, clean_ai])
        
        if report_type == "Activity":
            header = "**НОВАЯ ТРЕНИРОВКА 🚴‍♂️🏋️🚶**"
            act = activities_to_log[0]['row']
            stats = f"📊 `{act[3]}км | NP {act[12]}W | TSS {act[13]}`"
        else:
            header = "**ДОБРОЕ УТРО КАПИТАН! 🌞☕⛵⚓**"
            stats = f"`📈 HRV: {morning_row[5]} | 💓 RHR: {morning_row[4]} | 🔋 BB: {morning_row[6]}`"

        msg = f"{header}\n{stats}\n\n{clean_ai}"
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=15)

    print("🚀 Всё четко!")
except Exception as e:
    print(f"🚨 Ошибка: {e}")
