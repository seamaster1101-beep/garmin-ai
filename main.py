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

# --- MORNING DATA ---
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

# --- DAILY DATA ---
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

# --- ACTIVITIES ---
activities_today = []

try:
    activities_today = gar.get_activities_by_date(today_str, today_str) or []
    print(f"✅ Найдено активностей: {len(activities_today)}")
    
    # Сортируем по времени
    activities_today.sort(key=lambda x: x.get('startTimeLocal', ''))
    
    for act in activities_today:
        t = act.get('startTimeLocal', '')
        s = act.get('activityType', {}).get('typeKey', 'unknown')
        print(f"  {t} - {s}")
        
except Exception as e:
    print(f"Activities fetch error: {e}")

# --- GOOGLE SHEETS ---
try:
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    print("✅ Google Sheets connected")
    
    # Daily
    daily_sheet = ss.worksheet("Daily")
    all_daily = daily_sheet.get_all_values()
    daily_row_num = None
    for i, row in enumerate(all_daily, 1):
        if row and row[0] == today_str:
            daily_row_num = i
            break
    
    if daily_row_num:
        for col, val in enumerate(daily_row, 1):
            if val:
                daily_sheet.update_cell(daily_row_num, col, str(val).replace('.', ','))
        print("✅ Daily updated")
    else:
        daily_sheet.append_row([str(v).replace('.', ',') if isinstance(v, float) else v for v in daily_row])
        print("✅ Daily appended")
    
    # Morning
    morning_sheet = ss.worksheet("Morning")
    all_morning = morning_sheet.get_all_values()
    morning_row_num = None
    for i, row in enumerate(all_morning, 1):
        if row and today_str in str(row[0]):
            morning_row_num = i
            break
    
    if morning_row_num:
        for col, val in enumerate(morning_row, 1):
            if val:
                morning_sheet.update_cell(morning_row_num, col, str(val).replace('.', ','))
        print("✅ Morning updated")
    else:
        morning_sheet.append_row([str(v).replace('.', ',') if isinstance(v, float) else v for v in morning_row])
        print("✅ Morning appended")
    
    # Activities - удаляем все за сегодня и добавляем заново
    activities_sheet = ss.worksheet("Activities")
    all_activities = activities_sheet.get_all_values()
    
    # Находим строки для удаления
    rows_to_delete = []
    for i, row in enumerate(all_activities[1:], start=2):
        if len(row) > 0 and row[0] == today_str:
            rows_to_delete.append(i)
    
    # Удаляем
    for row_num in reversed(rows_to_delete):
        activities_sheet.delete_rows(row_num)
        print(f"  Удалена строка {row_num}")
    
    # Добавляем новые
    for act in activities_today:
        start = act.get('startTimeLocal', '')
        if 'T' in start:
            date_part = start.split('T')[0]
            time_part = start.split('T')[1][:5]
        elif ' ' in start:
            date_part = start.split(' ')[0]
            time_part = start.split(' ')[1][:5]
        else:
            date_part = today_str
            time_part = ""
        
        sport = act.get('activityType', {}).get('typeKey', 'unknown')
        
        duration = act.get('duration', 0)
        duration_hr = round(duration / 3600, 2) if duration else ""
        
        distance = act.get('distance', 0)
        distance_km = round(distance / 1000, 2) if distance else 0
        
        new_row = [
            date_part,
            time_part,
            sport,
            str(duration_hr).replace('.', ',') if duration_hr else "",
            str(distance_km).replace('.', ',') if distance_km else "0",
            str(act.get('averageHeartRate', '')),
            str(act.get('maxHeartRate', '')),
            str(act.get('trainingLoad', '')).replace('.', ',') if act.get('trainingLoad') else "",
            str(act.get('trainingEffect', '')).replace('.', ',') if act.get('trainingEffect') else "",
            str(int(act.get('calories', 0))) if act.get('calories') else "",
            str(act.get('averagePower', '')),
            str(act.get('averageCadence', '')),
            ""
        ]
        
        activities_sheet.append_row(new_row)
        print(f"  ✅ Добавлена: {time_part} {sport}")
    
    print(f"✅ Activities: удалено {len(rows_to_delete)}, добавлено {len(activities_today)}")
    
    # --- AI ADVICE (ПРОСТОЙ ВАРИАНТ) ---
    advice = "Хорошего дня!"
    
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            model = genai.GenerativeModel('gemini-1.5-pro')
            
            acts_text = ""
            for a in activities_today:
                sport = a.get('activityType', {}).get('typeKey', 'unknown')
                duration = round(a.get('duration', 0) / 60, 0)
                acts_text += f"{sport} {duration}мин, "
            
            prompt = f"Дай короткий совет на день. Данные: HRV={hrv}, пульс={r_hr}, сон={slp_h}ч, тренировки: {acts_text}"
            response = model.generate_content(prompt)
            
            if response and response.text:
                advice = response.text.strip()
                print("✅ AI совет получен")
        except Exception as ai_e:
            print(f"AI Error: {ai_e}")
            advice = "Хорошего дня!"
    
    # --- TELEGRAM ---
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        acts_list = []
        for a in activities_today:
            sport = a.get('activityType', {}).get('typeKey', 'unknown')
            duration = round(a.get('duration', 0) / 60, 0)
            acts_list.append(f"• {sport}: {duration}мин")
        
        acts_text = '\n'.join(acts_list) if acts_list else 'нет тренировок'
        
        msg = (f"📊 Отчет {today_str}\n\n"
               f"😴 Сон: {slp_h}ч\n"
               f"❤️ Пульс: {r_hr}\n"
               f"⚖️ Вес: {weight}кг\n"
               f"👣 Шаги: {steps}\n\n"
               f"🏋️ Тренировки:\n{acts_text}\n\n"
               f"🤖 {advice}")
        
        tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN.strip()}/sendMessage"
        requests.post(tg_url, json={"chat_id": TELEGRAM_CHAT_ID.strip(), "text": msg}, timeout=15)
        print("✅ Telegram отправлен")

    print("\n🎉 Готово!")

except Exception as e:
    print(f"Final Error: {e}")
