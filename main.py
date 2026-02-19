import os
import json
import requests
from datetime import datetime
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials

def run_main():
    # 1. ЗАГРУЗКА СЕКРЕТОВ (Имена в точности как в GitHub Secrets)
    GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
    GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
    GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    # Технический лог в консоль GitHub (чтобы мы видели, что секреты подтянулись)
    print(f"--- DEBUG INFO ---")
    print(f"TG Token Length: {len(TELEGRAM_BOT_TOKEN)}")
    print(f"TG Chat ID: {TELEGRAM_CHAT_ID}")
    print(f"Gemini Key Length: {len(GEMINI_API_KEY)}")
    print(f"------------------")

    hrv, slp_h, bb_morning, advice = "N/A", "N/A", "N/A", "ИИ не ответил"

    try:
        # 2. GARMIN: Сбор данных
        gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
        gar.login()
        today = datetime.now().strftime("%Y-%m-%d")
        
        # Получаем HRV и Body Battery
        stats = gar.get_stats(today) or {}
        hrv = stats.get("lastNightAvgHrv") or stats.get("allDayAvgHrv") or "N/A"
        
        summary = gar.get_user_summary(today) or {}
        bb_morning = summary.get("bodyBatteryHighestValue") or "N/A"

        # Получаем сон
        slp = gar.get_sleep_data(today)
        if slp and slp.get("dailySleepDTO"):
            slp_h = round(slp["dailySleepDTO"].get("sleepTimeSeconds", 0) / 3600, 1)
        
        print(f"Garmin Data: HRV={hrv}, Sleep={slp_h}, BB={bb_morning}")

        # 3. GEMINI: Анализ (Прямой API запрос для надежности)
        if GEMINI_API_KEY:
            try:
                ai_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
                ai_payload = {
                    "contents": [{"parts": [{"text": f"Биометрия: HRV {hrv}, Сон {slp_h}ч, Body Battery {bb_morning}. Дай один очень короткий ироничный совет."}]}]
                }
                res = requests.post(ai_url, json=ai_payload, timeout=15).json()
                advice = res['candidates'][0]['content']['parts'][0]['text'].strip()
            except Exception as e:
                advice = f"Ошибка ИИ: {str(e)[:30]}"
                print(f"AI Error: {e}")

        # 4. GOOGLE SHEETS: Логирование
        if GOOGLE_CREDS_JSON:
            creds_dict = json.loads(GOOGLE_CREDS_JSON)
            c_obj = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
            ss = gspread.authorize(c_obj).open("Garmin_Data")
            log_sheet = ss.worksheet("AI_Log")
            log_sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Success", advice])
            print("Google Sheets updated.")

        # 5. TELEGRAM: Отправка отчета
        if len(TELEGRAM_BOT_TOKEN) > 10 and TELEGRAM_CHAT_ID:
            # Убираем символы, которые могут сломать сообщение
            safe_advice = str(advice).replace("*", "").replace("_", "")
            msg = (
                f"🚀 GARMIN DAILY\n"
                f"━━━━━━━━━━━━━━\n"
                f"📊 HRV: {hrv}\n"
                f"😴 Сон: {slp_h}ч\n"
                f"⚡ BB: {bb_morning}\n"
                f"━━━━━━━━━━━━━━\n"
                f"🤖 {safe_advice}"
            )
            
            tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            tg_res = requests.post(tg_url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=15)
            
            print(f"Telegram Status: {tg_res.status_code}")
            if tg_res.status_code != 200:
                print(f"Telegram Error Body: {tg_res.text}")
        else:
            print("Telegram credentials missing or invalid.")

    except Exception as global_e:
        print(f"CRITICAL ERROR: {global_e}")

if __name__ == "__main__":
    run_main()
