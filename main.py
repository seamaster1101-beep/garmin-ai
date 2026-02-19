import os, json, requests
from datetime import datetime
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

# --- CONFIG ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Функция обновления таблицы (старая надежная версия)
def update_or_append(sheet, date_str, row_data):
    try:
        col_values = sheet.col_values(1)
        search_date = date_str.split(' ')[0]
        found_idx = -1
        for i, val in enumerate(col_values):
            if search_date in val:
                found_idx = i + 1
                break
        if found_idx != -1:
            for i, val in enumerate(row_data[1:], start=2):
                if val not in (None, "", 0, "0", 0.0): 
                    sheet.update_cell(found_idx, i, val)
            return "Updated"
        else:
            sheet.append_row(row_data)
            return "Appended"
    except: return "Error"

# --- LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
except Exception as e:
    print(f"Login Fail: {e}"); exit(1)

today_str = datetime.now().strftime("%Y-%m-%d")

# --- СБОР ДАННЫХ ---
try:
    stats = gar.get_stats(today_str) or {}
    summary = gar.get_user_summary(today_str) or {}
    
    hrv = stats.get("allDayAvgHrv") or stats.get("lastNightAvgHrv") or "-"
    r_hr = summary.get("restingHeartRate") or "-"
    bb_now = summary.get("bodyBatteryMostRecentValue") or "-"
    steps = summary.get("totalSteps", 0)
    # Дистанция за весь день в км
    dist_total = round((summary.get("totalDistanceMeters", 0) / 1000), 2)
    # Калории за весь день
    cals = summary.get("activeCalories", 0) + summary.get("bmrCalories", 0)
except Exception as e:
    print(f"Data Error: {e}"); exit(1)

# --- AI ADVICE ---
advice = "Держи темп!"
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY.strip())
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"Биометрия: HRV {hrv}, Пульс {r_hr}, Шаги {steps}, Дистанция {dist_total}км. Дай короткий ироничный совет."
        res = model.generate_content(prompt)
        advice = res.text.strip()
    except: pass

# --- TELEGRAM ---
if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
    msg = (
        f"🚀 *ОТЧЕТ ГАРМИН*\n"
        f"📊 HRV: {hrv} | ❤️ HR: {r_hr}\n"
        f"👟 Шаги: {steps} ({dist_total} км)\n"
        f"⚡ Батарейка: {bb_now}%\n"
        f"🔥 Калории: {cals}\n\n"
        f"🤖 {advice.replace('*', '')}"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN.strip()}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID.strip(), "text": msg, "parse_mode": "Markdown"})

# --- TABLE SYNC ---
try:
    creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(creds).open("Garmin_Data")
    daily_row = [today_str, steps, dist_total, cals, r_hr, bb_now]
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
except Exception as e:
    print(f"Table Error: {e}")
