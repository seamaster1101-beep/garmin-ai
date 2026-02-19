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
    print("✅ Garmin login OK")
except Exception as e:
    print(f"Login Fail: {e}")
    exit(1)

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

print(f"📅 Today: {today_str}")

# --- 1. MORNING BLOCK ---
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
    print("✅ Morning data OK")
except Exception as e:
    print(f"Morning Error: {e}")
    morning_row = [morning_ts, "", "", "", "", "", ""]

# --- 2. DAILY BLOCK ---
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
    print("✅ Daily data OK")
except Exception as e:
    print(f"Daily Error: {e}")
    daily_row = [today_str, "", "", "", "", ""]

# --- 3. ACTIVITIES BLOCK (ИСПРАВЛЕНО) ---
activities_today = []

try:
    activities_today = gar.get_activities_by_date(today_str, today_str) or []
    print(f"✅ Найдено активностей: {len(activities_today)}")
    
    # Сортируем по времени
    def get_time(a):
        t = a.get('startTimeLocal', '')
        if 'T' in t:
            return t.split('T')[1]
        elif ' ' in t:
            return t.split(' ')[1]
        return t
    
    activities_today.sort(key=get_time)
    
    for act in activities_today:
        t = act.get('startTimeLocal', '')
        s = act.get('activityType', {}).get('typeKey', 'unknown')
        print(f"  {t} - {s}")
        
except Exception as e:
    print(f"Activities fetch error: {e}")

# --- 4. SYNC TO GOOGLE SHEETS ---
try:
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    print("✅ Google Sheets connected")
    
    # Обновляем основные листы
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)
    
    # --- ACTIVITIES SHEET (ПРАВИЛЬНАЯ ЗАПИСЬ) ---
    try:
        activities_sheet = ss.worksheet("Activities")
        all_rows = activities_sheet.get_all_values()
        
        # Удаляем все строки за сегодня
        rows_to_delete = []
        for i, row in enumerate(all_rows[1:], start=2):
            if len(row) > 0 and row[0] == today_str:
                rows_to_delete.append(i)
        
        for row_num in reversed(rows_to_delete):
            activities_sheet.delete_rows(row_num)
            print(f"  Удалена строка {row_num}")
        
        # Добавляем сегодняшние активности
        for activity in activities_today:
            # Парсим время
            start_time = activity.get('startTimeLocal', '')
            if 'T' in start_time:
                date_part = start_time.split('T')[0]
                time_part = start_time.split('T')[1][:5]
            elif ' ' in start_time:
                date_part = start_time.split(' ')[0]
                time_part = start_time.split(' ')[1][:5]
            else:
                date_part = today_str
                time_part = ""
            
            sport = activity.get('activityType', {}).get('typeKey', 'unknown')
            
            # Конвертируем данные
            duration_sec = activity.get('duration', 0)
            duration_hr = round(duration_sec / 3600, 2) if duration_sec else ""
            
            distance_m = activity.get('distance', 0)
            distance_km = round(distance_m / 1000, 2) if distance_m else 0
            
            avg_hr = activity.get('averageHeartRate', '')
            max_hr = activity.get('maxHeartRate', '')
            training_load = activity.get('trainingLoad', '')
            training_effect = activity.get('trainingEffect', '')
            calories = activity.get('calories', '')
            avg_power = activity.get('averagePower', '')
            cadence = activity.get('averageCadence', '')
            
            # Форматируем
            duration_str = str(duration_hr).replace('.', ',') if duration_hr else ""
            distance_str = str(distance_km).replace('.', ',') if distance_km else "0"
            load_str = str(training_load).replace('.', ',') if training_load else ""
            effect_str = str(training_effect).replace('.', ',') if training_effect else ""
            calories_str = str(int(calories)) if calories else ""
            
            # Создаем строку (13 колонок)
            new_row = [
                date_part,           # 1. Date
                time_part,           # 2. Start_Time
                sport,               # 3. Sport
                duration_str,        # 4. Duration_hr
                distance_str,        # 5. Distance_km
                str(avg_hr),         # 6. Avg_HR
                str(max_hr),         # 7. Max_HR
                load_str,            # 8. Training_Load
                effect_str,          # 9. Training_Effect
                calories_str,        # 10. Calories
                str(avg_power),      # 11. Avg_Power
                str(cadence),        # 12. Cadence
                ""                   # 13. HR_Intensity
            ]
            
            activities_sheet.append_row(new_row)
            print(f"  ✅ Добавлена: {time_part} {sport}")
        
        print(f"✅ Activities: удалено {len(rows_to_delete)}, добавлено {len(activities_today)}")
        
    except Exception as e:
        print(f"Activities sheet error: {e}")

    # --- 5. AI ADVICE ---
    advice = "🤖 Хорошего дня!"
    
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            model = genai.GenerativeModel('gemini-1.5-pro')
            
            acts = []
            for a in activities_today:
                sport = a.get('activityType', {}).get('typeKey', 'unknown')
                duration = round(a.get('duration', 0) / 60, 0)
                acts.append(f"{sport} {duration}мин")
            
            acts_text = ', '.join(acts) if acts else 'нет тренировок'
            
            prompt = (f"Утро: HRV={hrv}, пульс={r_hr}, сон={slp_h}ч. "
                      f"Тренировки: {acts_text}. Дай короткий совет на русском.")
            
            response = model.generate_content(prompt)
            if response and response.text:
                advice = f"🤖 {response.text.strip()}"
                print("✅ AI совет получен")
        except Exception as ai_e:
            print(f"AI Error: {ai_e}")
    
    # --- 6. TELEGRAM ---
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            acts_list = []
            for a in activities_today:
                sport = a.get('activityType', {}).get('typeKey', 'unknown')
                duration = round(a.get('duration', 0) / 60, 0)
                acts_list.append(f"• {sport}: {duration}мин")
            
            acts_text = '\n'.join(acts_list) if acts_list else 'нет тренировок'
            
            msg = (f"📊 Отчет {today_str}\n\n"
                   f"😴 Сон: {slp_h}ч | HRV: {hrv}\n"
                   f"❤️ Пульс: {r_hr} | ⚖️ Вес: {weight}кг\n"
                   f"👣 Шаги: {steps}\n\n"
                   f"🏋️ Тренировки:\n{acts_text}\n\n"
                   f"{advice}")
            
            tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN.strip()}/sendMessage"
            response = requests.post(tg_url, json={"chat_id": TELEGRAM_CHAT_ID.strip(), "text": msg}, timeout=15)
            print(f"✅ Telegram отправлен, статус: {response.status_code}")
        except Exception as tg_e:
            print(f"Telegram error: {tg_e}")

    print("\n🎉 Готово!")

except Exception as e:
    print(f"Final Error: {e}")
