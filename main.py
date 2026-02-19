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
    """Упрощенная функция для обновления или добавления строки"""
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
                if val not in (None, "", 0, "0", "0,0", "0.0"):
                    sheet.update_cell(found_idx, i, str(val).replace('.', ','))
            return "Updated"
        else:
            formatted_row = [str(val).replace('.', ',') if isinstance(val, float) else val for val in row_data]
            sheet.append_row(formatted_row)
            return "Appended"
    except Exception as e:
        return f"Err: {str(e)[:15]}"

# --- LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
except Exception as e:
    print(f"Login Fail: {e}")
    exit(1)

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# --- MORNING BLOCK ---
morning_ts = f"{today_str} 08:00"
weight = ""
r_hr = ""
hrv = ""
bb_morning = ""
slp_sc = ""
slp_h = ""

try:
    stats = gar.get_stats(today_str) or {}
    hrv = stats.get("allDayAvgHrv") or stats.get("lastNightAvgHrv") or ""
    
    for d in [today_str, yesterday_str]:
        try:
            sleep_data = gar.get_sleep_data(d)
            dto = sleep_data.get("dailySleepDTO") or {}
            if dto and dto.get("sleepTimeSeconds", 0) > 0:
                slp_sc = dto.get("sleepScore") or ""
                slp_h = round(dto.get("sleepTimeSeconds", 0) / 3600, 1)
                morning_ts = dto.get("sleepEndTimeLocal", "").replace("T", " ")[:16] or morning_ts
                break
        except:
            continue

    for i in range(3):
        d_check = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            w_data = gar.get_body_composition(d_check, today_str)
            if w_data and w_data.get('uploads'):
                weight = round(w_data['uploads'][-1].get('weight', 0) / 1000, 1)
                break
        except:
            continue

    summary = gar.get_user_summary(today_str) or {}
    r_hr = summary.get("restingHeartRate") or summary.get("heartRateRestingValue") or ""
    bb_morning = summary.get("bodyBatteryHighestValue") or ""

    morning_row = [morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h]
    
except Exception as e:
    print(f"Morning Error: {e}")
    morning_row = [morning_ts, "", "", "", "", "", ""]

# --- DAILY BLOCK ---
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
    print(f"Daily Error: {e}")
    daily_row = [today_str, "", "", "", "", ""]

# --- ACTIVITIES BLOCK (ПРОСТАЯ ВЕРСИЯ) ---
try:
    # Получаем активности за сегодня
    activities_today = gar.get_activities_by_date(today_str, today_str) or []
    print(f"Найдено активностей: {len(activities_today)}")
    
    # Выводим сырые данные для отладки
    for act in activities_today:
        print(f"\nСырые данные:")
        print(f"  startTimeLocal: {act.get('startTimeLocal')}")
        print(f"  activityType: {act.get('activityType', {}).get('typeKey')}")
        print(f"  duration: {act.get('duration')}")
        print(f"  distance: {act.get('distance')}")
        print(f"  trainingLoad: {act.get('trainingLoad')}")
        print(f"  trainingEffect: {act.get('trainingEffect')}")
        print(f"  calories: {act.get('calories')}")
        print(f"  averagePower: {act.get('averagePower')}")
        
except Exception as e:
    print(f"Activities fetch error: {e}")
    activities_today = []

# --- SYNC TO GOOGLE SHEETS ---
try:
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    
    # Обновляем основные листы
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)
    
    # --- ПРОСТАЯ ОБРАБОТКА ACTIVITIES ---
    try:
        activities_sheet = ss.worksheet("Activities")
        
        # Получаем все строки
        all_rows = activities_sheet.get_all_values()
        
        # Для каждой новой активности
        for activity in activities_today:
            # Получаем время старта
            start_time_full = activity.get('startTimeLocal', '')
            if 'T' in start_time_full:
                date_part, time_part = start_time_full.split('T')
                time_part = time_part[:5]
            else:
                date_part = today_str
                time_part = ""
            
            sport = activity.get('activityType', {}).get('typeKey', 'unknown')
            
            # Проверяем, есть ли уже такая активность
            exists = False
            for row in all_rows[1:]:  # пропускаем заголовок
                if len(row) >= 3 and row[0] == date_part and row[1] == time_part and row[2] == sport:
                    exists = True
                    break
            
            if not exists:
                # Создаем строку с данными
                duration_sec = activity.get('duration', 0)
                duration_hr = round(duration_sec / 3600, 2) if duration_sec else ""
                
                distance_m = activity.get('distance', 0)
                distance_km = round(distance_m / 1000, 2) if distance_m else 0
                
                training_load = activity.get('trainingLoad', '')
                training_effect = activity.get('trainingEffect', '')
                calories = activity.get('calories', '')
                avg_power = activity.get('averagePower', '')
                
                # Формируем строку
                new_row = [
                    date_part,
                    time_part,
                    sport,
                    str(duration_hr).replace('.', ',') if duration_hr else "",
                    str(distance_km).replace('.', ',') if distance_km else "0",
                    str(activity.get('averageHeartRate', '')),
                    str(activity.get('maxHeartRate', '')),
                    str(training_load).replace('.', ',') if training_load else "",
                    str(training_effect).replace('.', ',') if training_effect else "",
                    str(calories),
                    str(avg_power),
                    str(activity.get('averageCadence', '')),
                    ""  # HR_Intensity - пока пусто
                ]
                
                activities_sheet.append_row(new_row)
                print(f"Добавлена активность: {sport} в {time_part}")
        
    except Exception as e:
        print(f"Activities sheet error: {e}")

    # --- AI ADVICE ---
    advice = "Нет данных для анализа"
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            model = genai.GenerativeModel('gemini-pro')
            prompt = (f"Биометрия: HRV {hrv}, Пульс {r_hr}, Батарейка {bb_morning}, "
                      f"Сон {slp_h}ч. Напиши один ироничный совет на день.")
            res = model.generate_content(prompt)
            advice = res.text.strip()
        except Exception as ai_e:
            advice = f"AI Error: {str(ai_e)[:30]}"
    
    # --- TELEGRAM ---
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        activities_text = ""
        if activities_today:
            for act in activities_today:
                sport = act.get('activityType', {}).get('typeKey', 'unknown')
                duration = round(act.get('duration', 0) / 60, 0)
                activities_text += f"\n• {sport}: {duration}мин"
        
        msg = (f"🚀 Отчет за {today_str}:\n"
               f"HRV: {hrv}\n"
               f"Сон: {slp_h}ч\n"
               f"Пульс: {r_hr}\n"
               f"Вес: {weight}кг\n"
               f"Шаги: {steps}\n"
               f"Активности: {len(activities_today)}{activities_text}\n\n"
               f"🤖 {advice.replace('*', '')}")
        
        tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN.strip()}/sendMessage"
        requests.post(tg_url, json={"chat_id": TELEGRAM_CHAT_ID.strip(), "text": msg}, timeout=15)

except Exception as e:
    print(f"Final Error: {e}")
