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

# --- LOGIN GARMIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
    print("✅ Garmin login OK")
except Exception as e:
    print(f"❌ Garmin login error: {e}")
    exit(1)

now = datetime.now()
today = now.strftime("%Y-%m-%d")
yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

print(f"📅 Today: {today}")

# --- MORNING DATA ---
morning = {
    'time': f"{today} 08:00",
    'weight': '',
    'resting_hr': '',
    'hrv': '',
    'body_battery': '',
    'sleep_score': '',
    'sleep_hours': ''
}

try:
    # HRV
    stats = gar.get_stats(today)
    if stats:
        morning['hrv'] = stats.get('allDayAvgHrv') or stats.get('lastNightAvgHrv') or ''
    
    # Sleep
    for d in [today, yesterday]:
        try:
            sleep = gar.get_sleep_data(d)
            dto = sleep.get('dailySleepDTO', {})
            if dto and dto.get('sleepTimeSeconds', 0) > 0:
                morning['sleep_score'] = dto.get('sleepScore', '')
                morning['sleep_hours'] = round(dto.get('sleepTimeSeconds', 0) / 3600, 1)
                end_time = dto.get('sleepEndTimeLocal', '')
                if end_time:
                    morning['time'] = end_time.replace('T', ' ')[:16]
                break
        except:
            continue
    
    # Weight
    for i in range(3):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            body = gar.get_body_composition(d, today)
            if body and body.get('uploads'):
                morning['weight'] = round(body['uploads'][-1].get('weight', 0) / 1000, 1)
                break
        except:
            continue
    
    # Resting HR & Body Battery
    summary = gar.get_user_summary(today) or {}
    morning['resting_hr'] = summary.get('restingHeartRate') or summary.get('heartRateRestingValue') or ''
    morning['body_battery'] = summary.get('bodyBatteryHighestValue') or ''
    
    print("✅ Morning data OK")
except Exception as e:
    print(f"⚠️ Morning error: {e}")

# --- DAILY DATA ---
daily = {
    'date': today,
    'steps': 0,
    'steps_distance': 0,
    'calories': 0,
    'resting_hr': morning['resting_hr'],
    'body_battery': ''
}

try:
    summary = gar.get_user_summary(today) or {}
    stats = gar.get_stats(today) or {}
    
    # Steps
    steps_data = gar.get_daily_steps(today, today)
    daily['steps'] = steps_data[0].get('totalSteps', 0) if steps_data else 0
    
    # Calories
    daily['calories'] = summary.get('activeKilocalories', 0) + summary.get('bmrKilocalories', 0) or stats.get('calories', 0)
    
    # Distance from steps
    daily['steps_distance'] = round(daily['steps'] * 0.000762, 2)
    
    # Body Battery
    daily['body_battery'] = summary.get('bodyBatteryMostRecentValue', '')
    
    print("✅ Daily data OK")
except Exception as e:
    print(f"⚠️ Daily error: {e}")

# --- ACTIVITIES ---
activities = []

try:
    raw = gar.get_activities_by_date(today, today) or []
    
    # Sort by time
    def get_time(a):
        t = a.get('startTimeLocal', '')
        if 'T' in t:
            return t.split('T')[1]
        elif ' ' in t:
            return t.split(' ')[1]
        return t
    
    activities = sorted(raw, key=get_time)
    print(f"✅ Activities found: {len(activities)}")
    for a in activities:
        t = a.get('startTimeLocal', '')
        s = a.get('activityType', {}).get('typeKey', 'unknown')
        print(f"  {t} - {s}")
except Exception as e:
    print(f"⚠️ Activities error: {e}")

# --- GOOGLE SHEETS ---
try:
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    client = gspread.authorize(creds)
    ss = client.open("Garmin_Data")
    print("✅ Google Sheets connected")
    
    # --- MORNING SHEET ---
    try:
        sheet = ss.worksheet("Morning")
        all_rows = sheet.get_all_values()
        
        # Find today's row
        row_num = None
        for i, row in enumerate(all_rows, 1):
            if row and today in str(row[0]):
                row_num = i
                break
        
        morning_row = [
            morning['time'],
            str(morning['weight']) if morning['weight'] else '',
            str(morning['resting_hr']) if morning['resting_hr'] else '',
            str(morning['hrv']) if morning['hrv'] else '',
            str(morning['body_battery']) if morning['body_battery'] else '',
            str(morning['sleep_score']) if morning['sleep_score'] else '',
            str(morning['sleep_hours']).replace('.', ',') if morning['sleep_hours'] else ''
        ]
        
        if row_num:
            for col, val in enumerate(morning_row, 1):
                if val:
                    sheet.update_cell(row_num, col, val)
            print("✅ Morning updated")
        else:
            sheet.append_row(morning_row)
            print("✅ Morning appended")
    except Exception as e:
        print(f"⚠️ Morning sheet error: {e}")
    
    # --- DAILY SHEET ---
    try:
        sheet = ss.worksheet("Daily")
        all_rows = sheet.get_all_values()
        
        row_num = None
        for i, row in enumerate(all_rows, 1):
            if row and row[0] == today:
                row_num = i
                break
        
        daily_row = [
            daily['date'],
            str(daily['steps']),
            str(daily['steps_distance']).replace('.', ','),
            str(daily['calories']),
            str(daily['resting_hr']),
            str(daily['body_battery'])
        ]
        
        if row_num:
            for col, val in enumerate(daily_row, 1):
                if val:
                    sheet.update_cell(row_num, col, val)
            print("✅ Daily updated")
        else:
            sheet.append_row(daily_row)
            print("✅ Daily appended")
    except Exception as e:
        print(f"⚠️ Daily sheet error: {e}")
    
    # --- ACTIVITIES SHEET - ПРОСТОЕ РЕШЕНИЕ ---
    try:
        sheet = ss.worksheet("Activities")
        all_rows = sheet.get_all_values()
        
        # 1. Находим все строки за сегодня
        rows_to_delete = []
        for i, row in enumerate(all_rows[1:], start=2):  # start=2 потому что первая строка - заголовок
            if len(row) > 0 and row[0] == today:
                rows_to_delete.append(i)
        
        # 2. Удаляем их (в обратном порядке)
        for row_num in reversed(rows_to_delete):
            sheet.delete_rows(row_num)
            print(f"  Удалена строка {row_num}")
        
        # 3. Добавляем все активности за сегодня в правильном порядке
        for act in activities:
            # Парсим время
            start = act.get('startTimeLocal', '')
            if 'T' in start:
                date_part = start.split('T')[0]
                time_part = start.split('T')[1][:5]
            elif ' ' in start:
                date_part = start.split(' ')[0]
                time_part = start.split(' ')[1][:5]
            else:
                date_part = today
                time_part = ''
            
            sport = act.get('activityType', {}).get('typeKey', 'unknown')
            
            # Получаем данные
            duration = act.get('duration', 0)
            duration_hr = round(duration / 3600, 2) if duration else ''
            
            distance = act.get('distance', 0)
            distance_km = round(distance / 1000, 2) if distance else 0
            
            training_load = act.get('trainingLoad', '')
            training_effect = act.get('trainingEffect', '')
            calories = act.get('calories', '')
            avg_power = act.get('averagePower', '')
            cadence = act.get('averageCadence', '')
            
            # Создаем строку
            new_row = [
                date_part,                          # 1. Date
                time_part,                          # 2. Start_Time
                sport,                              # 3. Sport
                str(duration_hr).replace('.', ',') if duration_hr else '',  # 4. Duration_hr
                str(distance_km).replace('.', ',') if distance_km else '0', # 5. Distance_km
                str(act.get('averageHeartRate', '')),  # 6. Avg_HR
                str(act.get('maxHeartRate', '')),      # 7. Max_HR
                str(training_load).replace('.', ',') if training_load else '',  # 8. Training_Load
                str(training_effect).replace('.', ',') if training_effect else '',  # 9. Training_Effect
                str(int(calories)) if calories else '',     # 10. Calories
                str(avg_power) if avg_power else '',        # 11. Avg_Power
                str(cadence) if cadence else '',            # 12. Cadence
                ''                                           # 13. HR_Intensity
            ]
            
            sheet.append_row(new_row)
            print(f"  ✅ Добавлена: {time_part} {sport}")
        
        print(f"✅ Activities: удалено {len(rows_to_delete)}, добавлено {len(activities)}")
        
    except Exception as e:
        print(f"⚠️ Activities sheet error: {e}")

    # --- AI ADVICE ---
    advice = "🤖 Хорошего дня!"
    
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            model = genai.GenerativeModel('models/gemini-1.5-pro')
            
            # Формируем промпт
            acts = []
            for a in activities:
                sport = a.get('activityType', {}).get('typeKey', 'unknown')
                duration = round(a.get('duration', 0) / 60, 0)
                acts.append(f"{sport} {duration}мин")
            
            acts_text = ', '.join(acts) if acts else 'нет тренировок'
            
            prompt = (f"Утро: HRV={morning['hrv']}, пульс={morning['resting_hr']}, "
                      f"сон={morning['sleep_hours']}ч. Тренировки: {acts_text}. "
                      f"Дай короткий совет на русском, 1 предложение.")
            
            response = model.generate_content(prompt)
            if response and response.text:
                advice = f"🤖 {response.text.strip()}"
                print("✅ AI совет получен")
        except Exception as ai_e:
            print(f"⚠️ AI Error: {ai_e}")
            advice = "🤖 Хорошего дня!"
    
    # --- TELEGRAM (ОБЯЗАТЕЛЬНО ДОЛЖЕН БЫТЬ!) ---
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            # Формируем сообщение
            acts_list = []
            for a in activities:
                sport = a.get('activityType', {}).get('typeKey', 'unknown')
                duration = round(a.get('duration', 0) / 60, 0)
                acts_list.append(f"• {sport}: {duration}мин")
            
            acts_text = '\n'.join(acts_list) if acts_list else 'нет тренировок'
            
            msg = (
                f"📊 **Отчет {today}**\n\n"
                f"😴 Сон: {morning['sleep_hours']}ч | HRV: {morning['hrv']}\n"
                f"❤️ Пульс: {morning['resting_hr']} | ⚖️ Вес: {morning['weight']}кг\n"
                f"👣 Шаги: {daily['steps']}\n\n"
                f"🏋️ **Тренировки:**\n{acts_text}\n\n"
                f"{advice}"
            )
            
            tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN.strip()}/sendMessage"
            response = requests.post(
                tg_url, 
                json={
                    "chat_id": TELEGRAM_CHAT_ID.strip(), 
                    "text": msg, 
                    "parse_mode": "Markdown"
                },
                timeout=15
            )
            print(f"✅ Telegram отправлен, статус: {response.status_code}")
        except Exception as tg_e:
            print(f"⚠️ Ошибка Telegram: {tg_e}")

    print("\n🎉 Все операции завершены!")

except Exception as e:
    print(f"❌ Критическая ошибка: {e}")
