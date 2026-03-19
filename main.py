import os
import json
import requests
import garth
import time
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
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")

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
    except Exception as e: print(f"Err gspread: {e}")

# --- LOGIN (Hybrid) ---
session_dir = "./.garth"
gar = None
if os.path.exists(session_dir) and os.listdir(session_dir):
    try:
        garth.resume(session_dir)
        gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
        gar.garth = garth.client
        # Исправляем получение display_name:
        gar.display_name = garth.client.username or gar.get_full_name()
        print(f"✅ Сессия восстановлена для: {gar.display_name}")
    except Exception as e:
        print(f"⚠️ Сессия не подошла: {e}")
        gar = None

if gar is None:
    try:
        gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
        gar.login(session_dir)
        gar.display_name = garth.client.username or gar.get_full_name()
        print(f"🔑 Вход по паролю для: {gar.display_name}")
    except Exception as e:
        if "429" in str(e):
            print("🚨 Бан 429. Ждем.")
            exit(1)
        raise e

# --- 1. СБОР ДАННЫХ ---
summary = gar.get_user_summary(today_str) or {}
hrv_res = gar.get_hrv_data(today_str) or {}
hrv = hrv_res.get("hrvSummary", {}).get("lastNightAvg") or ""
r_hr = summary.get("restingHeartRate") or ""

# Вес S2 (за последние 3 дня)
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

# Сон
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
morning_ts, slp_sc, slp_h = f"{today_str} 08:00", "", ""
for d in [today_str, yesterday_str]:
    try:
        sleep_data = gar.get_sleep_data(d) or {}
        dto = sleep_data.get("dailySleepDTO") or {}
        if dto and dto.get("sleepTimeSeconds", 0) > 0:
            slp_h = round(float(dto.get("sleepTimeSeconds")) / 3600, 1)
            scores = dto.get("sleepScores") or {}
            slp_sc = scores.get("overall", {}).get("value") or dto.get("sleepScore") or ""
            raw_ts = dto.get("sleepEndTimestampLocal")
            morning_ts = datetime.fromtimestamp(raw_ts/1000).strftime("%Y-%m-%d %H:%M") if isinstance(raw_ts, (int, float)) else str(raw_ts).replace("T", " ")[:16]
            break
    except: continue

# Fitness Age (62 года)
real_age = 62
try:
    calc = real_age + (int(r_hr)-55)*0.4 + (float(fat or 22)-22)*0.5 - (int(hrv or 45)-45)*0.1
    fit_age = round(max(45, min(real_age + 5, calc)), 1)
except: fit_age = "62"

morning_bb_max = summary.get("bodyBatteryHighestValue") or summary.get("bodyBatteryMostRecentValue", "")
morning_row = [f"'{morning_ts}", weight, fat, muscle, r_hr, hrv, morning_bb_max, slp_sc, slp_h, real_age, fit_age]
steps = summary.get('totalSteps', 0)
daily_row = [f"'{today_str}", steps, round(steps * 0.000762, 2), int(summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0)), r_hr, summary.get("bodyBatteryMostRecentValue", "")]

# Тренировки
activities_to_log = []
try:
    latest = gar.get_activities(0, 5) or []
    for a in latest:
        if not a.get("startTimeLocal", "").startswith(today_str): continue
        act_id = str(a.get("activityId"))
        np = a.get('normPower') or a.get('weightedAveragePower', "")
        avg_p = a.get('avgPower', "")
        vi = round(float(np)/float(avg_p), 2) if np and avg_p and float(avg_p)>0 else ""
        row = [
            a.get("startTimeLocal").replace("T", " ")[:16], a.get('activityType', {}).get('typeKey', ''),
            round(a.get('duration', 0)/3600, 2), round(a.get('distance', 0)/1000, 2), a.get('averageHR', ""), a.get('maxHR', ""),
            round(float(a.get('intensityFactor', 0)), 3), round(float(a.get('activityTrainingLoad', 0)), 1),
            round(float(a.get('aerobicTrainingEffect', 0)), 1), a.get('calories', ""), avg_p,
            a.get('averageBikingCadence') or "", round(float(np), 1) if np else "", 
            round(float(a.get('trainingStressScore', 0)), 1), vi, f"'{act_id}"
        ]
        activities_to_log.append({"id": act_id, "row": row})
except: pass

# --- 2. GOOGLE & АНАЛИТИКА ---
creds_dict = json.loads(GOOGLE_CREDS_JSON)
credentials = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
ss = gspread.authorize(credentials).open("Garmin_Data")
act_sheet = ss.worksheet("Activities")
tss_values = [float(str(r[13]).replace(',', '.')) for r in act_sheet.get_all_values()[1:] if len(r) > 13 and r[13]]

ctl = atl = tsb = 0
if tss_values:
    def ewma(data, d):
        alpha = 2/(d+1); v = data[0]
        for x in data[1:]: v = alpha * x + (1 - alpha) * v
        return v
    ctl, atl = round(ewma(tss_values, 42), 1), round(ewma(tss_values, 7), 1)
    tsb = round(ctl - atl, 1)

# --- 3. ИИ АНАЛИТИКА ---
ai_advice, report_type = "SKIP", ""
log_sheet = ss.worksheet("AI_Log")
morning_done = any(today_str in r[0] and "Morning" in r[1] for r in log_sheet.get_all_values()[-15:])

if activities_to_log:
    report_type = "Activity"
    act = activities_to_log[0]['row']
    prompt = (f"Ты — опытный коуч. Разбери: {act[1]}, {act[3]}км, NP {act[12]}Вт, TSS {act[13]}, IF {act[6]}. "
              f"Будь честен, дай совет на завтра.")
elif not morning_done:
    report_type = "Morning"
    prompt = (f"Ты — спортивный врач. Состояние: HRV {hrv}, Пульс {r_hr}, Сон {slp_h}ч, BB {morning_bb_max}, "
              f"Fit Age {fit_age} (Реальный {real_age}). Оцени готовность к нагрузке.")
else: prompt = None

if GEMINI_API_KEY and prompt:
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
        ai_advice = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except: ai_advice = "Совет недоступен"

# --- 4. ЗАПИСЬ И ТЕЛЕГРАМ ---
try:
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    exist = {r[15] for r in act_sheet.get_all_values() if len(r) > 15}
    for a in activities_to_log:
        if a["id"] not in exist: act_sheet.append_row(a["row"], value_input_option='USER_ENTERED')
    
    if ai_advice != "SKIP":
        log_sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), report_type, ai_advice])
        header = "**НОВАЯ ТРЕНИРОВКА** 🚴‍♂️" if report_type == "Activity" else "**ДОБРОЕ УТРО** 🌞"
        stats = f"📊 `{act[3]}км | TSS {act[13]}`" if report_type == "Activity" else f"`📈 HRV: {hrv} | 🔋 BB: {morning_bb_max}`"
        msg = f"{header}\n{stats}\n📈 `CTL: {ctl} | TSB: {tsb}`\n\n{ai_advice.replace('*', '')}"
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
    print("🚀 Миссия выполнена!")
except Exception as e: print(f"🚨 Финальная ошибка: {e}")
