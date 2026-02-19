import os
import json
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai
import requests
import time

# --- CONFIG ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# --- LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
    print("✅ Успешный вход в Garmin")
except Exception as e:
    print(f"❌ Ошибка входа: {e}")
    exit(1)

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

print(f"\n📅 Дата: {today_str}")

# --- MORNING DATA ---
morning_data = {
    'time': f"{today_str} 08:00",
    'weight': '',
    'resting_hr': '',
    'hrv': '',
    'body_battery': '',
    'sleep_score': '',
    'sleep_hours': ''
}

try:
    # HRV
    stats = gar.get_stats(today_str) or {}
    morning_data['hrv'] = stats.get("allDayAvgHrv") or stats.get("lastNightAvgHrv") or ''
    
    # Sleep
    for d in [today_str, yesterday_str]:
        try:
            sleep = gar.get_sleep_data(d)
            dto = sleep.get("dailySleepDTO") or {}
            if dto and dto.get("sleepTimeSeconds", 0) > 0:
                morning_data['sleep_score'] = dto.get("sleepScore") or ''
                morning_data['sleep_hours'] = round(dto.get("sleepTimeSeconds", 0) / 3600, 1)
                morning_data['time'] = dto.get("sleepEndTimeLocal", "").replace("T", " ")[:16] or morning_data['time']
                break
        except:
            continue
    
    # Weight
    for i in range(3):
        d_check = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            weight_data = gar.get_body_composition(d_check, today_str)
            if weight_data and weight_data.get('uploads'):
                morning_data['weight'] = round(weight_data['uploads'][-1].get('weight', 0) / 1000, 1)
                break
        except:
            continue
    
    # Resting HR & Body Battery
    summary = gar.get_user_summary(today_str) or {}
    morning_data['resting_hr'] = summary.get("restingHeartRate") or summary.get("heartRateRestingValue") or ''
    morning_data['body_battery'] = summary.get("bodyBatteryHighestValue") or ''
    
    print("✅ Morning data получены")
    
except Exception as e:
    print(f"⚠️ Ошибка Morning: {e}")

# --- DAILY DATA ---
daily_data = {
    'date': today_str,
    'steps': 0,
    'steps_distance': 0,
    'calories': 0,
    'resting_hr': morning_data['resting_hr'],
    'body_battery': ''
}

try:
    summary = gar.get_user_summary(today_str) or {}
    stats = gar.get_stats(today_str) or {}
    
    # Steps
    steps_data = gar.get_daily_steps(today_str, today_str)
    daily_data['steps'] = steps_data[0].get('totalSteps', 0) if steps_data else 0
    
    # Calories
    daily_data['calories'] = (
        summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0)
    ) or stats.get("calories") or 0
    
    # Steps distance
    daily_data['steps_distance'] = round(daily_data['steps'] * 0.000762, 2)
    
    # Body Battery
    daily_data['body_battery'] = summary.get("bodyBatteryMostRecentValue", "")
    
    print("✅ Daily data получены")
    
except Exception as e:
    print(f"⚠️ Ошибка Daily: {e}")

# --- ACTIVITIES ---
activities = []

try:
    # Получаем активности и сразу сортируем по времени
    raw_activities = gar.get_activities_by_date(today_str, today_str) or []
    
    # Сортируем по времени (от ранних к поздним)
    def get_time(act):
        start = act.get('startTimeLocal', '')
        if 'T' in start:
            return start.split('T')[1]
        elif ' ' in start:
            return start.split(' ')[1]
        return start
    
    activities = sorted(raw_activities, key=get_time)
    
    print(f"✅ Найдено активностей: {len(activities)}")
    for i, act in enumerate(activities, 1):
        start = act.get('startTimeLocal', '')
        sport = act.get('activityType', {}).get('typeKey', 'unknown')
        print(f"  {i}. {start} - {sport}")
    
except Exception as e:
    print(f"⚠️ Ошибка получения активностей: {e}")

# --- GOOGLE SHEETS ---
try:
    # Подключение к Google Sheets
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds = Credentials.from_service_account_info(
        creds_dict, 
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    client = gspread.authorize(creds)
    ss = client.open("Garmin_Data")
    print("✅ Подключение к Google Sheets")
    
    # --- MORNING SHEET ---
    try:
        morning_sheet = ss.worksheet("Morning")
        
        # Ищем строку с сегодняшней датой
        all_morning = morning_sheet.get_all_values()
        morning_row_idx = None
        
        for i, row in enumerate(all_morning, 1):
            if row and today_str in str(row[0]):
                morning_row_idx = i
                break
        
        morning_row = [
            morning_data['time'],
            morning_data['weight'],
            morning_data['resting_hr'],
            morning_data['hrv'],
            morning_data['body_battery'],
            morning_data['sleep_score'],
            str(morning_data['sleep_hours']).replace('.', ',') if morning_data['sleep_hours'] else ''
        ]
        
        if morning_row_idx:
            # Обновляем существующую строку
            for col, val in enumerate(morning_row, 1):
                if val:
                    morning_sheet.update_cell(morning_row_idx, col, val)
            print("✅ Morning sheet обновлен")
        else:
            # Добавляем новую строку
            morning_sheet.append_row(morning_row)
            print("✅ Morning sheet дополнен")
            
    except Exception as e:
        print(f"⚠️ Ошибка Morning sheet: {e}")
    
    # --- DAILY SHEET ---
    try:
        daily_sheet = ss.worksheet("Daily")
        
        # Ищем строку с сегодняшней датой
        all_daily = daily_sheet.get_all_values()
        daily_row_idx = None
        
        for i, row in enumerate(all_daily, 1):
            if row and row[0] == today_str:
                daily_row_idx = i
                break
        
        daily_row = [
            daily_data['date'],
            str(daily_data['steps']),
            str(daily_data['steps_distance']).replace('.', ','),
            str(daily_data['calories']),
            str(daily_data['resting_hr']),
            str(daily_data['body_battery'])
        ]
        
        if daily_row_idx:
            for col, val in enumerate(daily_row, 1):
                if val:
                    daily_sheet.update_cell(daily_row_idx, col, val)
            print("✅ Daily sheet обновлен")
        else:
            daily_sheet.append_row(daily_row)
            print("✅ Daily sheet дополнен")
            
    except Exception as e:
        print(f"⚠️ Ошибка Daily sheet: {e}")
    
    # --- ACTIVITIES SHEET ---
    try:
        activities_sheet = ss.worksheet("Activities")
        
        # Получаем все существующие строки для проверки дубликатов
        all_activities = activities_sheet.get_all_values()
        existing = set()
        
        for row in all_activities[1:]:  # пропускаем заголовок
            if len(row) >= 3:
                key = f"{row[0]}_{row[1]}_{row[2]}"
                existing.add(key)
        
        # Добавляем новые активности
        added = 0
        for activity in activities:
            # Парсим дату и время
            start = activity.get('startTimeLocal', '')
            
            if 'T' in start:
                date_part = start.split('T')[0]
                time_part = start.split('T')[1][:5]
            elif ' ' in start:
                date_part = start.split(' ')[0]
                time_part = start.split(' ')[1][:5]
            else:
                date_part = today_str
                time_part = ''
            
            sport = activity.get('activityType', {}).get('typeKey', 'unknown')
            
            # Проверяем, есть ли уже
            key = f"{date_part}_{time_part}_{sport}"
            
            if key not in existing:
                # Получаем данные
                duration = activity.get('duration', 0)
                duration_hr = round(duration / 3600, 2) if duration else ''
                
                distance = activity.get('distance', 0)
                distance_km = round(distance / 1000, 2) if distance else 0
                
                # ВАЖНО: правильное распределение!
                training_load = activity.get('trainingLoad', '')
                training_effect = activity.get('trainingEffect', '')
                calories = activity.get('calories', '')
                avg_power = activity.get('averagePower', '')
                cadence = activity.get('averageCadence', '')
                
                # Создаем строку
                new_row = [
                    date_part,                          # 1. Date
                    time_part,                          # 2. Start_Time
                    sport,                              # 3. Sport
                    str(duration_hr).replace('.', ',') if duration_hr else '',  # 4. Duration_hr
                    str(distance_km).replace('.', ',') if distance_km else '0', # 5. Distance_km
                    str(activity.get('averageHeartRate', '')),  # 6. Avg_HR
                    str(activity.get('maxHeartRate', '')),      # 7. Max_HR
                    str(training_load).replace('.', ',') if training_load else '',  # 8. Training_Load
                    str(training_effect).replace('.', ',') if training_effect else '',  # 9. Training_Effect
                    str(int(calories)) if calories else '',     # 10. Calories
                    str(avg_power) if avg_power else '',        # 11. Avg_Power
                    str(cadence) if cadence else '',            # 12. Cadence
                    ''                                           # 13. HR_Intensity
                ]
                
                activities_sheet.append_row(new_row)
                added += 1
                existing.add(key)  # добавляем в множество, чтобы избежать дублей в этой сессии
                print(f"  ✅ Добавлена: {time_part} {sport}")
        
        print(f"✅ Activities sheet: добавлено {added} новых")
        
    except Exception as e:
        print(f"⚠️ Ошибка Activities sheet: {e}")
    
    # --- AI ADVICE (с обработкой ошибки квоты) ---
    advice = "🤖 Совет: Слушай свое тело, оно умнее любых алгоритмов!"
    
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            model = genai.GenerativeModel('gemini-pro')
            
            # Формируем промпт
            acts = []
            for a in activities:
                sport = a.get('activityType', {}).get('typeKey', 'unknown')
                duration = round(a.get('duration', 0) / 60, 0)
                acts.append(f"{sport} {duration}мин")
            
            acts_text = ', '.join(acts) if acts else 'нет тренировок'
            
            prompt = (f"Утро: HRV={morning_data['hrv']}, пульс={morning_data['resting_hr']}, "
                      f"сон={morning_data['sleep_hours']}ч. Тренировки: {acts_text}. "
                      f"Дай короткий ироничный совет на русском, 1 предложение.")
            
            response = model.generate_content(prompt)
            if response and response.text:
                advice = f"🤖 {response.text.strip()}"
                print("✅ AI совет получен")
            else:
                print("⚠️ AI вернул пустой ответ")
                
        except Exception as ai_e:
            error_msg = str(ai_e)
            if "429" in error_msg:
                advice = "🤖 Квота AI исчерпана на сегодня, но ты и так молодец!"
            else:
                advice = "🤖 Совет: Главное - регулярность, а не цифры!"
            print(f"⚠️ AI Error: {error_msg[:50]}")
    
    # --- LOG AI ADVICE ---
    try:
        ai_log = ss.worksheet("AI_Log")
        ai_log.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            "Success" if "квота" not in advice and "ошибка" not in advice.lower() else "Failed",
            advice
        ])
    except:
        print("⚠️ AI_Log sheet not found")
    
    # --- TELEGRAM ---
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        # Формируем сообщение
        acts_list = []
        for a in activities:
            sport = a.get('activityType', {}).get('typeKey', 'unknown')
            duration = round(a.get('duration', 0) / 60, 0)
            acts_list.append(f"• {sport}: {duration}мин")
        
        acts_text = '\n'.join(acts_list) if acts_list else 'нет тренировок'
        
        msg = (
            f"📊 **Отчет {today_str}**\n\n"
            f"😴 Сон: {morning_data['sleep_hours']}ч | HRV: {morning_data['hrv']}\n"
            f"❤️ Пульс: {morning_data['resting_hr']} | ⚖️ Вес: {morning_data['weight']}кг\n"
            f"👣 Шаги: {daily_data['steps']}\n\n"
            f"🏋️ **Тренировки:**\n{acts_text}\n\n"
            f"{advice}"
        )
        
        try:
            tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN.strip()}/sendMessage"
            response = requests.post(
                tg_url, 
                json={"chat_id": TELEGRAM_CHAT_ID.strip(), "text": msg, "parse_mode": "Markdown"},
                timeout=15
            )
            print(f"✅ Telegram отправлен, статус: {response.status_code}")
        except Exception as tg_e:
            print(f"⚠️ Ошибка Telegram: {tg_e}")
    
    print("\n🎉 Все операции завершены!")

except Exception as e:
    print(f"❌ Критическая ошибка: {e}")
