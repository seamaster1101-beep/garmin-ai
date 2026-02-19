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
    """
    Универсальная функция для обновления или добавления строки в лист.
    Ищет строку по дате и обновляет только те ячейки, где есть данные.
    """
    try:
        # Получаем все значения из первой колонки (даты)
        col_values = sheet.col_values(1)
        search_date = date_str.split(' ')[0]  # Берем только дату без времени
        
        found_idx = -1
        for i, val in enumerate(col_values):
            if search_date in str(val):
                found_idx = i + 1  # +1 потому что индексация в gspread с 1
                break
        
        if found_idx != -1:
            # Обновляем существующую строку (только непустые значения)
            for i, val in enumerate(row_data[1:], start=2):  # start=2 потому что первая колонка - дата
                if val not in (None, "", 0, "0", 0.0, "N/A"):
                    sheet.update_cell(found_idx, i, str(val).replace('.', ','))  # Заменяем . на ,
            return "Updated"
        else:
            # Добавляем новую строку
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
    hrv = stats.get("allDayAvgHrv") or stats.get("lastNightAvgHrv") or stats.get("lastNightHrv")
    
    # Получаем данные сна
    for d in [today_str, yesterday_str]:
        try:
            sleep_data = gar.get_sleep_data(d)
            dto = sleep_data.get("dailySleepDTO") or {}
            if dto and dto.get("sleepTimeSeconds", 0) > 0:
                # Sleep Score
                slp_sc = dto.get("sleepScore") or sleep_data.get("sleepScore") or ""
                
                # Sleep Hours
                slp_h = round(dto.get("sleepTimeSeconds", 0) / 3600, 1)
                
                # Время пробуждения
                morning_ts = dto.get("sleepEndTimeLocal", "").replace("T", " ")[:16] or morning_ts
                break
        except:
            continue

    # Получаем вес
    for i in range(3):
        d_check = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            w_data = gar.get_body_composition(d_check, today_str)
            if w_data and w_data.get('uploads'):
                weight = round(w_data['uploads'][-1].get('weight', 0) / 1000, 1)
                break
        except:
            continue

    # Получаем Resting HR и Body Battery
    summary = gar.get_user_summary(today_str) or {}
    r_hr = summary.get("restingHeartRate") or summary.get("heartRateRestingValue") or ""
    bb_morning = summary.get("bodyBatteryHighestValue") or ""

    # Формируем строку для Morning листа
    morning_row = [morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h]
    
    print(f"Morning data: Вес={weight}, HRV={hrv}, Сон={slp_h}ч, Score={slp_sc}")
    
except Exception as e:
    print(f"Morning Error: {e}")
    morning_row = [morning_ts, "", "", "", "", "", ""]

# --- 2. DAILY BLOCK ---
try:
    summary = gar.get_user_summary(today_str) or {}
    stats = gar.get_stats(today_str) or {}

    # Шаги
    steps_data = gar.get_daily_steps(today_str, today_str)
    steps = steps_data[0].get('totalSteps', 0) if steps_data else 0

    # Калории
    cals = (
        summary.get("activeKilocalories", 0)
        + summary.get("bmrKilocalories", 0)
    ) or stats.get("calories") or 0

    # Дистанция ТОЛЬКО от шагов
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

# --- 3. ACTIVITIES BLOCK (ФИНАЛЬНАЯ ВЕРСИЯ) ---
activities_today = []

try:
    # Получаем активности за сегодня
    activities_today = gar.get_activities_by_date(today_str, today_str) or []
    print(f"Найдено активностей за сегодня: {len(activities_today)}")
    
except Exception as e:
    print(f"Activities fetch error: {e}")

# --- 4. SYNC, AI & TELEGRAM ---
try:
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    
    # Обновляем основные листы
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)
    
    # --- ТОЧНОЕ ВЫРАВНИВАНИЕ ACTIVITIES ---
    try:
        activities_sheet = ss.worksheet("Activities")
        
        # Получаем все существующие строки для проверки
        all_rows = activities_sheet.get_all_values()
        existing_by_key = {}  # для поиска дубликатов
        
        # Заголовки для отладки
        if len(all_rows) > 0:
            print("Структура таблицы:")
            for i, header in enumerate(all_rows[0], 1):
                print(f"  Колонка {i}: {header}")
        
        # Индексируем существующие строки (кроме заголовка)
        for i, row in enumerate(all_rows[1:], start=2):
            if len(row) >= 3:
                # Ключ: дата + время + спорт
                key = f"{row[0]}_{row[1]}_{row[2]}"
                existing_by_key[key] = i
        
        # Обрабатываем каждую активность
        for activity in activities_today:
            # --- ИЗВЛЕКАЕМ ДАННЫЕ С ТОЧНЫМ ФОРМАТИРОВАНИЕМ ---
            
            # 1. DATE и START_TIME
            start_time_full = activity.get('startTimeLocal', '')
            if 'T' in start_time_full:
                date_part, time_part = start_time_full.split('T')
                time_part = time_part[:5]  # HH:MM
            else:
                date_part = today_str
                time_part = "00:00"
            
            # 2. SPORT
            sport = activity.get('activityType', {}).get('typeKey', 'unknown')
            
            # 3. DURATION_HR (часы, с одним знаком после запятой)
            duration_sec = activity.get('duration', 0)
            if duration_sec:
                duration_hr = round(duration_sec / 3600, 2)
                # Форматируем как в примере: 0,76 или 0,56
                duration_str = f"{duration_hr:.2f}".replace('.', ',')
            else:
                duration_str = ""
            
            # 4. DISTANCE_KM
            distance_m = activity.get('distance', 0)
            if distance_m:
                distance_km = round(distance_m / 1000, 2)
                distance_str = f"{distance_km:.2f}".replace('.', ',')
            else:
                distance_str = "0"
            
            # 5. AVG_HR
            avg_hr = activity.get('averageHeartRate', '')
            avg_hr_str = str(avg_hr) if avg_hr else ""
            
            # 6. MAX_HR
            max_hr = activity.get('maxHeartRate', '')
            max_hr_str = str(max_hr) if max_hr else ""
            
            # 7. TRAINING_LOAD
            training_load = activity.get('trainingLoad', '')
            if training_load and training_load != 0:
                # Форматируем как в примере: 2 или 2,9
                if isinstance(training_load, float):
                    if training_load.is_integer():
                        training_load_str = str(int(training_load))
                    else:
                        training_load_str = f"{training_load:.1f}".replace('.', ',')
                else:
                    training_load_str = str(training_load)
            else:
                training_load_str = ""
            
            # 8. TRAINING_EFFEC
            training_effect = activity.get('trainingEffect', '')
            if training_effect and training_effect != 0:
                if isinstance(training_effect, float):
                    if training_effect.is_integer():
                        training_effect_str = str(int(training_effect))
                    else:
                        training_effect_str = f"{training_effect:.1f}".replace('.', ',')
                else:
                    training_effect_str = str(training_effect)
            else:
                training_effect_str = ""
            
            # 9. CALORIES
            calories = activity.get('calories', '')
            calories_str = str(calories) if calories else ""
            
            # 10. AVG_POWER
            avg_power = activity.get('averagePower', '')
            avg_power_str = str(avg_power) if avg_power else ""
            
            # 11. CADENCE
            cadence = activity.get('averageCadence', '')
            cadence_str = str(cadence) if cadence else ""
            
            # 12. HR_INTENSITY
            hr_intensity = ""
            if avg_hr and r_hr and r_hr != "":
                try:
                    hr_reserve = float(avg_hr) - float(r_hr)
                    if hr_reserve < 30:
                        hr_intensity = "Low"
                    elif hr_reserve < 60:
                        hr_intensity = "Moderate"
                    else:
                        hr_intensity = "High"
                except:
                    hr_intensity = ""
            
            # 13. SESSION (оставляем пустым)
            session = ""
            
            # --- ФОРМИРУЕМ СТРОКУ ТОЧНО ПО КОЛОНКАМ ---
            activity_row = [
                date_part,           # 1. Date
                time_part,           # 2. Start_Time
                sport,               # 3. Sport
                duration_str,        # 4. Duration_hr
                distance_str,        # 5. Distance_km
                avg_hr_str,          # 6. Avg_HR
                max_hr_str,          # 7. Max_HR
                training_load_str,   # 8. Training_Load
                training_effect_str, # 9. Training_Effec
                calories_str,        # 10. Calories
                avg_power_str,       # 11. Avg_Power
                cadence_str,         # 12. Cadence
                hr_intensity,        # 13. HR_Intensity
                session              # 14. Session
            ]
            
            # Отладка - что записываем
            print(f"\n--- Активность: {sport} в {time_part} ---")
            print(f"  Duration_hr: {duration_str}")
            print(f"  Distance_km: {distance_str}")
            print(f"  Training_Load: {training_load_str}")
            print(f"  Training_Effec: {training_effect_str}")
            print(f"  Calories: {calories_str}")
            print(f"  Avg_Power: {avg_power_str}")
            print(f"  HR_Intensity: {hr_intensity}")
            
            # Проверяем существование
            key = f"{date_part}_{time_part}_{sport}"
            
            if key in existing_by_key:
                # ОБНОВЛЯЕМ существующую строку
                row_num = existing_by_key[key]
                print(f"  Обновление строки {row_num}")
                
                # Обновляем только заполненные колонки (начиная с 4)
                updates = [
                    (4, duration_str),
                    (5, distance_str),
                    (6, avg_hr_str),
                    (7, max_hr_str),
                    (8, training_load_str),
                    (9, training_effect_str),
                    (10, calories_str),
                    (11, avg_power_str),
                    (12, cadence_str),
                    (13, hr_intensity),
                    (14, session)
                ]
                
                for col_num, value in updates:
                    if value not in (None, "", "0", "0,00", "0.0"):
                        activities_sheet.update_cell(row_num, col_num, value)
            else:
                # ДОБАВЛЯЕМ новую строку
                print(f"  Добавление новой строки")
                activities_sheet.append_row(activity_row)
        
        print(f"\n✅ Обработано активностей: {len(activities_today)}")
        
    except gspread.WorksheetNotFound:
        print("Лист 'Activities' не найден. Пропускаем...")
    except Exception as e:
        print(f"Activities sheet error: {e}")

    # --- AI ADVICE ---
    advice = "Нет данных для анализа"
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            if available_models:
                model_name = available_models[0]
                model = genai.GenerativeModel(model_name)
                
                # Формируем промпт с данными о сегодняшних активностях
                activities_summary = ""
                if activities_today:
                    for act in activities_today:
                        sport = act.get('activityType', {}).get('typeKey', 'unknown')
                        duration = round(act.get('duration', 0) / 60, 0)
                        activities_summary += f"{sport} ({duration}мин), "
                
                prompt = (f"Биометрия: HRV {hrv}, Пульс {r_hr}, Батарейка {bb_morning}, "
                          f"Сон {slp_h}ч (Score: {slp_sc}). "
                          f"Сегодняшние активности: {activities_summary}. "
                          f"Напиши один ироничный и мудрый совет на день.")
                res = model.generate_content(prompt)
                advice = res.text.strip()
            else:
                advice = "API Key жив, но доступных моделей нет."
        except Exception as ai_e:
            advice = f"AI Error: {str(ai_e)[:30]}"
    
    # --- LOG AI ADVICE ---
    try:
        ai_log = ss.worksheet("AI_Log")
        ai_log.append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Success", advice])
    except:
        print("AI_Log sheet not found")

    print(f"\n✔ ФИНИШ! Шаги: {steps}, Активностей: {len(activities_today)}")
    print(f"AI: {advice[:60]}...")

    # --- ОТПРАВКА В ТЕЛЕГРАМ ---
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        # Формируем детальное сообщение
        activities_text = ""
        if activities_today:
            for act in activities_today:
                sport = act.get('activityType', {}).get('typeKey', 'unknown')
                duration = round(act.get('duration', 0) / 60, 0)
                distance = round(act.get('distance', 0) / 1000, 1)
                if distance > 0:
                    activities_text += f"\n• {sport}: {duration}мин, {distance}км"
                else:
                    activities_text += f"\n• {sport}: {duration}мин"
        
        msg = (f"🚀 Отчет за {today_str}:\n"
               f"❤️ HRV: {hrv} | Пульс: {r_hr}\n"
               f"😴 Сон: {slp_h}ч (Score: {slp_sc})\n"
               f"⚖️ Вес: {weight}кг\n"
               f"👣 Шаги: {steps}\n"
               f"🏋️ Активности: {len(activities_today)}{activities_text}\n\n"
               f"🤖 {advice.replace('*', '')}")
        
        tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN.strip()}/sendMessage"
        resp = requests.post(tg_url, json={"chat_id": TELEGRAM_CHAT_ID.strip(), "text": msg}, timeout=15)
        print(f"Telegram Response: {resp.status_code}")
    else:
        print("Telegram Token or ID is missing in Secrets!")

except Exception as e:
    print(f"❌ Final Error: {e}")
