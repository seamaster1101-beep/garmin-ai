import os
import json
import requests
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

# --- CONFIG ---
def get_env(name):
    val = os.environ.get(name)
    if not val:
        print(f"CRITICAL: Secret {name} is missing!")
    return val

GARMIN_EMAIL = get_env("GARMIN_EMAIL")
GARMIN_PASSWORD = get_env("GARMIN_PASSWORD")
GEMINI_API_KEY = get_env("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = get_env("GOOGLE_CREDS")
TELEGRAM_BOT_TOKEN = get_env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = get_env("TELEGRAM_CHAT_ID")

# Инициализация переменных по умолчанию
hrv, slp_sc, slp_h, weight, r_hr, bb_morning, advice = "N/A", "N/A", "N/A", "N/A", "N/A", "N/A", "Ошибка анализа"

try:
    # 1. СВЯЗЬ С ТАБЛИЦЕЙ (Делаем в начале, чтобы было куда писать ошибки)
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    log_sheet = ss.worksheet("AI_Log")
    print("✔ Google Sheets подключен")

    # 2. LOGIN GARMIN
    try:
        gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
        gar.login()
        print("✔ Garmin Login Success")
    except Exception as e:
        log_sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Garmin Login Error", str(e)])
        print(f"❌ Garmin Login Fail: {e}")
        exit(1)

    # 3. СБОР ДАННЫХ
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    
    try:
        stats = gar.get_stats(today_str) or {}
        hrv = stats.get("allDayAvgHrv") or stats.get("lastNightAvgHrv") or "N/A"
        summary = gar.get_user_summary(today_str) or {}
        r_hr = summary.get("restingHeartRate") or "N/A"
        bb_morning = summary.get("bodyBatteryHighestValue") or "N/A"
        
        # Сон
        for d in [today_str, yesterday_str]:
            slp = gar.get_sleep_data(d)
            if slp and slp.get("dailySleepDTO"):
                slp_sc = slp["dailySleepDTO"].get("sleepScore") or "N/A"
                slp_h = round(slp["dailySleepDTO"].get("sleepTimeSeconds", 0) / 3600, 1)
                break
        print("✔ Данные Garmin собраны")
    except Exception as e:
        print(f"⚠ Ошибка сбора данных: {e}")

    # 4. ИИ АНАЛИЗ
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            model = genai.GenerativeModel(models[0] if models else 'gemini-1.5-flash')
            res = model.generate_content(f"HRV {hrv}, Сон {slp_h}, Батарейка {bb_morning}. Дай короткий ироничный совет.")
            advice = res.text.strip()
            print("✔ ИИ отчет готов")
        except Exception as e:
            advice = f"AI Error: {str(e)[:20]}"

    # 5. ЗАПИСЬ В ТАБЛИЦУ
    log_sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Success", advice])

# 6. ТЕЛЕГРАМ (ФИНАЛЬНЫЙ ТЕСТ)
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        # Убираем вообще всё форматирование, только голый текст
        clean_advice = str(advice).replace("*", "").replace("_", "")
        msg = f"HRV: {hrv}\nСон: {slp_h}\nBB: {bb_morning}\n\nСовет: {clean_advice}"
        
        try:
            # Превращаем ID в число на случай, если GitHub передал его как строку с пробелом
            chat_id_int = int(str(TELEGRAM_CHAT_ID).strip())
            t_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN.strip()}/sendMessage"
            
            payload = {"chat_id": chat_id_int, "text": msg}
            r = requests.post(t_url, json=payload, timeout=20) # Используем json= вместо data=
            
            if r.status_code != 200:
                log_sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "TG Error", r.text])
            else:
                log_sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "TG Success", "Sent!"])
        except Exception as t_e:
            log_sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "TG Exception", str(t_e)])
