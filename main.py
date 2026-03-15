import os, json, requests
from datetime import datetime
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials

# --- CONFIG ---
GARMIN_EMAIL = os.getenv("GARMIN_EMAIL")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_SHEETS_CREDS")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

try:
    # 1. АВТОРИЗАЦИЯ
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
    today_str = datetime.now().strftime("%Y-%m-%d")
    # Используем текущее время запуска, чтобы оно было в таблице
    display_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # 2. СБОР ДАННЫХ (Morning)
    stats = gar.get_user_summary(today_str)
    
    # Вес
    weight = round(stats.get('weight', 0) / 1000, 1) if stats.get('weight') else ""
    # Пульс покоя
    rhr = stats.get('restingHeartRate', "")
    # Боди батарея
    bb = stats.get('bodyBatteryMostRecentValue', "")
    
    # Сон (выделяем в отдельный блок, чтобы не упало)
    slp_score, slp_h = "", ""
    try:
        sleep = gar.get_sleep_data(today_str)
        slp_score = sleep.get('dailySleepDTO', {}).get('score', "")
        slp_sec = sleep.get('dailySleepDTO', {}).get('sleepTimeSeconds', 0)
        if slp_sec: slp_h = round(slp_sec / 3600, 1)
    except: pass

    # HRV
    hrv = ""
    try:
        hrv_data = gar.get_hrv_data(today_str)
        hrv = hrv_data.get('hrvSummary', {}).get('lastNightAvg', "")
    except: pass

    # Жир и Мышцы (если весы S2 синхронились)
    fat, muscle = "", ""
    try:
        comp = gar.get_body_composition(today_str)
        fat = comp.get('totalDailyLeaf', {}).get('bodyFat', "")
        m_mass = comp.get('totalDailyLeaf', {}).get('muscleMass')
        if m_mass: muscle = round(m_mass / 1000, 1)
    except: pass

    morning_row = [display_time, weight, fat, muscle, rhr, hrv, bb, slp_score, slp_h, "40", "Logged"]

    # 3. СБОР ДАННЫХ (Activities)
    activities_to_log = []
    try:
        all_acts = gar.get_activities(0, 3) # Берем последние 3
        for a in all_acts:
            start = a.get('startTimeLocal', '')
            if not start.startswith(today_str): continue
            
            act_id = str(a.get('activityId'))
            row = [
                start, a.get('activityType', {}).get('typeKey'),
                round(a.get('duration', 0)/3600, 2), round(a.get('distance', 0)/1000, 2),
                a.get('averageHR', ""), a.get('maxHR', ""),
                a.get('intensityFactor', ""), a.get('trainingLoad', ""),
                a.get('trainingEffect', ""), a.get('calories', ""),
                a.get('averagePower', ""), a.get('averageCadence', ""),
                a.get('normPower', ""), a.get('trainingStressScore', ""),
                "", act_id
            ]
            activities_to_log.append({"id": act_id, "row": row})
    except: pass

    # 4. ЗАПИСЬ В ТАБЛИЦУ
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(creds).open("Garmin_Data")
    
    # Пишем в Morning
    ws_m = ss.worksheet("Morning")
    ws_m.append_row(morning_row)
    
    # Пишем в Activities
    if activities_to_log:
        ws_a = ss.worksheet("Activities")
        # Берем ID из колонки P (16-я)
        existing_ids = [r[15] for r in ws_a.get_all_values() if len(r) > 15]
        for act in activities_to_log:
            if act["id"] not in existing_ids:
                ws_a.append_row(act["row"])

    print("Все данные успешно записаны.")
    if TELEGRAM_BOT_TOKEN:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": f"✅ Данные Garmin за {today_str} обновлены в таблице."})

except Exception as e:
    print(f"Ошибка: {e}")
