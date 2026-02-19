import os
import json
import requests
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

# --- Инициализация переменных (чтобы скрипт не падал, если данных нет) ---
hrv, slp_sc, slp_h, weight, r_hr, bb_morning, advice = "N/A", "N/A", "N/A", "N/A", "N/A", "N/A", "Нет анализа"

try:
    # 1. ЗАГРУЗКА СЕКРЕТОВ ИЗ GITHUB
    GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
    GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")
    TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    TG_ID = os.environ.get("TELEGRAM_CHAT_ID")

    # 2. ПОДКЛЮЧЕНИЕ GOOGLE SHEETS
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    log_sheet = ss.worksheet("AI_Log")

    # 3. ПОДКЛЮЧЕНИЕ GARMIN И СБОР ДАННЫХ
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Пытаемся взять статистику
    try:
        stats = gar.get_stats(today) or {}
        hrv = stats.get("allDayAvgHrv") or stats.get("lastNightAvgHrv") or "N/A"
        summary = gar.get_user_summary(today) or {}
        r_hr = summary.get("restingHeartRate") or "N/A"
        bb_morning = summary.get("bodyBatteryHighestValue") or "N/A"
        
        slp = gar.get_sleep_data(today)
        if slp and slp.get("dailySleepDTO"):
            slp_sc = slp["dailySleepDTO"].get("sleepScore") or "N/A"
            slp_h = round(slp["dailySleepDTO"].get("sleepTimeSeconds", 0) / 3600, 1)
    except:
        print("Частичные данные Garmin недоступны")

    # 4. ИИ АНАЛИЗ (GEMINI)
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            model = genai.GenerativeModel('gemini-1.5-flash')
            prompt = f"Биометрия: HRV {hrv}, Пульс {r_hr}, Сон {slp_h}ч. Дай один очень короткий ироничный совет."
            res = model.generate_content(prompt)
            advice = res.text.strip()
        except:
            advice = "ИИ вредничает и молчит"

    # 5. ЗАПИСЬ В ТАБЛИЦУ (AI_LOG)
    log_sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Success", advice])

    # 6. ОТПРАВКА В TELEGRAM
    if TG_TOKEN and TG_ID:
        # Убираем символы, которые могут «сломать» текст
        clean_advice = str(advice).replace("*", "").replace("_", "")
        msg = (
            f"🚀 ОТЧЕТ ГАРМИН\n"
            f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            f"📊 HRV: {hrv}\n"
            f"😴 Сон: {slp_h}ч (Оценка: {slp_sc})\n"
            f"❤️ Пульс: {r_hr}\n"
            f"⚡ Батарейка: {bb_morning}\n"
            f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            f"🤖 {clean_advice}"
        )
        
        t_url = f"https://api.telegram.org/bot{TG_TOKEN.strip()}/sendMessage"
        payload = {"chat_id": str(TG_ID).strip(), "text": msg}
        
        # Сама отправка
        r = requests.post(t_url, json=payload, timeout=15)
        
        if r.status_code != 200:
            log_sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "TG Error", r.text])

except Exception as e:
    print(f"Критическая ошибка: {e}")
