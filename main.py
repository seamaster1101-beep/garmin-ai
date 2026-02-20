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

def clean(val):
    """Очистка числовых значений для Google Sheets (замена . на ,)"""
    if isinstance(val, (int, float)) and val != "":
        return f"{val:.2f}".replace(".", ",")
    return val

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
                    sheet.update_cell(found_idx, i, clean(val))
            return "Updated"
        else:
            sheet.append_row([clean(v) if isinstance(v, (int, float)) else v for v in row_data])
            return "Appended"
    except Exception as e: 
        return f"Err: {str(e)[:15]}"

# --- LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
    print("✅ Garmin login OK")
except Exception as e:
    print(f"❌ Login Fail: {e}"); exit(1)

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# --- 1. MORNING BLOCK (ИСПРАВЛЕНО) ---
print("🔍 Получаем утренние данные...")
morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h = f"{today_str} 08:00", "", "", "", "", "", ""

try:
    # HRV - расширенный поиск
    stats = gar.get_stats(today_str) or {}
    print(f"Stats keys: {list(stats.keys())}")
    
    hrv = (
        stats.get("allDayAvgHrv") or 
        stats.get("lastNightAvgHrv") or 
        stats.get("lastNightHrv") or
        stats.get("hrvStatus") or ""  # статус может содержать значение
    )
    
    # Сон - проверяем несколько дней назад
    for days_back in range(4):  # до 4 дней назад
        d_check = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
        try:
            print(f"Проверяем сон за {d_check}")
            sleep_data = gar.get_sleep_data(d_check)
            print(f"Sleep data keys: {list(sleep_data.keys())}")
            
            # Пробуем разные пути к данным
            daily_sleep = sleep_data.get("dailySleepDTO") or sleep_data
            if daily_sleep and daily_sleep.get("sleepTimeSeconds", 0) > 0:
                slp_sc = daily_sleep.get("sleepScore") or sleep_data.get("sleepScore") or ""
                slp_h = round(daily_sleep.get("sleepTimeSeconds", 0) / 3600, 1)
                morning_ts = daily_sleep.get("sleepEndTimeLocal", "").replace("T", " ")[:16] or morning_ts
                print(f"✅ Сон найден: {slp_sc}/{slp_h}ч")
                break
        except Exception as se:
            print(f"Сон за {d_check}: {se}")
            continue

    # Вес - расширенный поиск
    for days_back in range(7):  # неделю назад
        d_check = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
        try:
            print(f"Проверяем вес за {d_check}")
            w_data = gar.get_body_composition(d_check, today_str)
            if w_data and w_data.get('uploads'):
                latest = w_data['uploads'][-1]
                weight = round(latest.get('weight', 0) / 1000, 1)  # граммы -> кг
                print(f"✅ Вес найден: {weight}кг")
                break
        except Exception as we:
            print(f"Вес за {d_check}: {we}")
            continue

    # Остальное
    summary = gar.get_user_summary(today_str) or {}
    r_hr = summary.get("restingHeartRate") or summary.get("heartRateRestingValue") or ""
    bb_morning = summary.get("bodyBatteryHighestValue") or summary.get("bodyBattery") or ""

    morning_row = [morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h]
    print(f"Утренние данные: Weight={weight}, HRV={hrv}, Sleep={slp_sc}/{slp_h}")
    
except Exception as e:
    print(f"❌ Morning Error: {e}")
    morning_row = [morning_ts, "", "", "", "", "", ""]

# --- 2. DAILY BLOCK ---
print("🔍 Получаем дневные данные...")
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

    daily_row = [
        today_str,
        steps,
        steps_distance_km,
        cals,
        r_hr,
        summary.get("bodyBatteryMostRecentValue", "")
    ]

except Exception as e:
    print(f"❌ Daily Error: {e}")
    daily_row = [today_str, "", "", "", "", ""]

# --- 3. ACTIVITIES ---
print("🔍 Получаем активности...")
activities_to_log = []
try:
    raw_acts = gar.get_activities_by_date(today_str, today_str)
    print(f"Найдено активностей: {len(raw_acts)}")
    
    # Google Sheets подключение для проверки дублей
    creds = json.loads(GOOGLE_CREDS_JSON)
    credentials = Credentials.from_service_account_info(
        creds,
        scopes=["https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"]
    )
    ss = gspread.authorize(credentials).open("Garmin_Data")
    act_sheet = ss.worksheet("Activities")
    existing_rows = act_sheet.get_all_values()[1:]  # пропускаем заголовок

    for a in raw_acts:
        act_date = a.get("startTimeLocal", "")[:10]
        act_time = a.get("startTimeLocal", "")[11:16]
        sport = a.get('activityType', {}).get('typeKey', '').capitalize()

        if any(r[0] == act_date and r[1] == act_time and r[2] == sport for r in existing_rows):
            continue

        cad = (
            a.get('averageBikingCadenceInRevPerMinute') or
            a.get('averageBikingCadence') or
            a.get('averageRunCadence') or
            a.get('averageCadence') or ""
        )

        raw_load = a.get('activityTrainingLoad') or a.get('trainingLoad') or 0
        t_load = round(float(raw_load), 1)

        avg_hr = a.get('averageHR') or a.get('averageHeartRate') or ""
        max_hr = a.get('maxHR') or a.get('maxHeartRate', "")

        intensity_text = "N/A"
        try:
            if avg_hr and r_hr and float(r_hr) > 0:
                res = (float(avg_hr) - float(r_hr)) / (185 - float(r_hr))
                if res < 0.5: intensity_text = "Low"
                elif res < 0.75: intensity_text = "Moderate"
                else: intensity_text = "High"
        except: pass

        new_row = [
            act_date, act_time, sport,
            round(a.get('duration', 0) / 3600, 2),
            round(a.get('distance', 0) / 1000, 2),
            avg_hr, max_hr, intensity_text, t_load,
            round(float(a.get('aerobicTrainingEffect', 0)), 1),
            a.get('calories', ""),
            a.get('avgPower', ""),
            cad
        ]
        
        formatted_row = [clean(val) if i > 2 else val for i, val in enumerate(new_row)]
        activities_to_log.append(formatted_row)

except Exception as e:
    print(f"❌ Activities error: {e}")

# --- Write to Google Sheets ---
print("💾 Записываем в Google Sheets...")
try:
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds_dict, 
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    
    # Activities
    if activities_to_log:
        for row in activities_to_log:
            act_sheet.append_row(row)
        print(f"✅ Добавлено активностей: {len(activities_to_log)}")
    
    # Daily & Morning
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)

    # AI & Telegram
    advice = "Нет данных для анализа"
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            model = genai.GenerativeModel('gemini-1.5-flash')
            prompt = (f"Биометрия: HRV {hrv}, Пульс {r_hr}, Батарейка {bb_morning}, "
                      f"Сон {slp_h}ч (Score: {slp_sc}). Напиши один ироничный и мудрый совет на день.")
            res = model.generate_content(prompt)
            advice = res.text.strip()
        except Exception as ai_e:
            advice = f"AI Error: {str(ai_e)[:30]}"
    
    ss.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Success", advice])
    print(f"✔ ФИНИШ! Weight: {weight}кг | HRV: {hrv} | Sleep: {slp_sc}/{slp_h}ч")
    print(f"🤖 Совет: {advice[:60]}...")

    # Telegram
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        msg = f"🚀 Garmin Sync:\n📊 Weight: {weight}кг\n💓 HRV: {hrv}\n😴 Сон: {slp_sc}/{slp_h}ч\n💓 Пульс: {r_hr}\n🔋 BB: {bb_morning}\n\n🤖 {advice}"
        tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN.strip()}/sendMessage"
        resp = requests.post(tg_url, json={"chat_id": TELEGRAM_CHAT_ID.strip(), "text": msg}, timeout=15)
        print(f"📱 Telegram: {resp.status_code}")

except Exception as e:
    print(f"❌ Final Error: {e}")
