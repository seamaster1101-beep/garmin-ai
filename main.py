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

    # Формируем строку для Morning листа в правильном порядке:
    # Date | Weight | Resting_HR | HRV | Body_Battery | Sleep_Score | Sleep_Hours
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

    # Дистанция ТОЛЬКО от шагов (в км, 0.762м/шаг - стандарт)
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

# --- 3. ACTIVITIES BLOCK (ИСПРАВЛЕНО) ---
activities_today = []
activities_yesterday = []

try:
    # Получаем активности за сегодня
    activities_today = gar.get_activities_by_date(today_str, today_str) or []
    
    # Получаем активности за вчера
    activities_yesterday = gar.get_activities_by_date(yesterday_str, yesterday_str) or []
    
    print(f"Найдено активностей: сегодня {len(activities_today)}, вчера {len(activities_yesterday)}")
    
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
    
    # --- ИСПРАВЛЕННАЯ обработка Activities ---
    try:
        activities_sheet = ss.worksheet("Activities")
        
        # Если есть активности за сегодня, добавляем их
        for activity in activities_today:
            # Извлекаем данные активности
            start_time = activity.get('startTimeLocal', '')
            
            # Разделяем дату и время
            if 'T' in start_time:
                date_part, time_part = start_time.split('T')
                time_part = time_part[:5]  # Берем только HH:MM
            else:
                date_part = today_str
                time_part = "00:00"
            
            sport = activity.get('activityType', {}).get('typeKey', 'unknown')
            
            # Длительность в часах (конвертируем из секунд)
            duration_sec = activity.get('duration', 0)
            duration_hr = round(duration_sec / 3600, 2) if duration_sec else ""
            
            # Дистанция в км (конвертируем из метров)
            distance_m = activity.get('distance', 0)
            distance_km = round(distance_m / 1000, 2) if distance_m else 0.0
            
            # Пульс
            avg_hr = activity.get('averageHeartRate', '')
            max_hr = activity.get('maxHeartRate', '')
            
            # Training Load и Effect - ВАЖНО: правильное распределение!
            training_load = activity.get('trainingLoad', '')  # Это должно идти в колонку Training_Load
            training_effect = activity.get('trainingEffect', '')  # Это в Training_Effec
            
            # Калории
            calories = activity.get('calories', '')
            
            # Доп. метрики
            avg_power = activity.get('averagePower', '')
            cadence = activity.get('averageCadence', '')
            
            # Определяем интенсивность по HR
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
            
            # Session (оставляем пустым для ручного заполнения)
            session = ""
            
            # Формируем строку строго по порядку колонок из таблицы:
            # Date | Start_Time | Sport | Duration_Hr | Distance_km | Avg_HR | Max_HR | 
            # Training_Load | Training_Effec | Calories | Avg_Power | Cadence | HR_Intensity | Session
            activity_row = [
                date_part,        # Date
                time_part,        # Start_Time
                sport,            # Sport
                duration_hr,      # Duration_Hr
                distance_km,      # Distance_km
                avg_hr,           # Avg_HR
                max_hr,           # Max_HR
                training_load,    # Training_Load (исправлено!)
                training_effect,  # Training_Effec (исправлено!)
                calories,         # Calories (исправлено!)
                avg_power,        # Avg_Power
                cadence,          # Cadence
                hr_intensity,     # HR_Intensity
                session           # Session (пусто)
            ]
            
            # Отладка - посмотрим, что записываем
            print(f"Активность: {sport} в {time_part}")
            print(f"  Training_Load: {training_load}, Training_Effec: {training_effect}, Calories: {calories}")
            
            # Обновляем или добавляем активность
            try:
                # Ищем строку с такой же датой и временем старта
                all_rows = activities_sheet.get_all_values()
                found = False
                
                # Пропускаем заголовок (первая строка)
                for i, row in enumerate(all_rows[1:], start=2):
                    if len(row) >= 2:
                        # Сравниваем дату и время
                        if len(row) >= 2 and row[0] == date_part and row[1] == time_part:
                            # Обновляем существующую
                            for j, val in enumerate(activity_row[2:], start=3):  # start=3 потому что первые 2 колонки - дата и время
                                if val not in (None, "", 0, "0", 0.0):
                                    activities_sheet.update_cell(i, j, str(val).replace('.', ','))
                            found = True
                            print(f"  Обновлена строка {i}")
                            break
                
                if not found:
                    # Добавляем новую
                    formatted_row = [str(v).replace('.', ',') if isinstance(v, float) else v for v in activity_row]
                    activities_sheet.append_row(formatted_row)
                    print(f"  Добавлена новая строка")
                    
            except Exception as e:
                print(f"Error updating activity: {e}")
        
        print(f"Обработано активностей: {len(activities_today)}")
        
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
                prompt = (f"Биометрия: HRV {hrv}, Пульс {r_hr}, Батарейка {bb_morning}, "
                          f"Сон {slp_h}ч (Score: {slp_sc}). Сегодня активностей: {len(activities_today)}. "
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

    print(f"✔ Финиш! Шаги: {steps}, Дист(шаги): {steps_distance_km}км, Активностей: {len(activities_today)}")
    print(f"AI: {advice[:40]}...")

    # --- ОТПРАВКА В ТЕЛЕГРАМ ---
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        # Формируем сообщение с активностями
        activities_text = ""
        if activities_today:
            for act in activities_today[:3]:  # покажем первые 3 активности
                sport = act.get('activityType', {}).get('typeKey', 'unknown')
                duration = round(act.get('duration', 0) / 60, 0)  # в минутах
                activities_text += f"\n• {sport}: {duration}мин"
        
        msg = (f"🚀 Отчет за {today_str}:\n"
               f"HRV: {hrv}\n"
               f"Сон: {slp_h}ч (Score: {slp_sc})\n"
               f"Пульс: {r_hr}\n"
               f"Вес: {weight}кг\n"
               f"Шаги: {steps}\n"
               f"Активности: {len(activities_today)}{activities_text}\n\n"
               f"🤖 {advice.replace('*', '')}")
        
        tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN.strip()}/sendMessage"
        resp = requests.post(tg_url, json={"chat_id": TELEGRAM_CHAT_ID.strip(), "text": msg}, timeout=15)
        print(f"Telegram Response: {resp.status_code}")
    else:
        print("Telegram Token or ID is missing in Secrets!")

except Exception as e:
    print(f"Final Error: {e}")
