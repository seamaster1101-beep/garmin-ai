import os, json, requests
from datetime import datetime
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials

# --- 1. CONFIG ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# --- 2. LOGIN & DATA ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
    today = datetime.now().strftime("%Y-%m-%d")
    
    summary = gar.get_user_summary(today) or {}
    stats = gar.get_stats(today) or {}

    steps = summary.get("totalSteps", 0)
    heart = summary.get("restingHeartRate", "-")
    bb = summary.get("bodyBatteryMostRecentValue", "-")
    cals = summary.get("activeCalories", 0) + summary.get("bmrCalories", 0)

    # --- 3. TELEGRAM (БЕЗ ИИ) ---
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        msg = (f"🚀 *БАЗОВЫЙ ОТЧЕТ*\n"
               f"👟 Шаги: {steps}\n"
               f"❤️ Пульс: {heart}\n"
               f"⚡ BB: {bb}%\n"
               f"🔥 Калории: {cals}")
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN.strip()}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID.strip(), "text": msg, "parse_mode": "Markdown"})

    # --- 4. GOOGLE SHEETS (ПРОСТО ЗАПИСЬ) ---
    creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), 
            scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(creds).open("Garmin_Data")
    ss.worksheet("Daily").append_row([today, steps, "", cals, heart, bb])
    print("Всё прошло успешно в упрощенном режиме.")

except Exception as e:
    print(f"Ошибка: {e}")
