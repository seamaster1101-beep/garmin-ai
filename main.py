import os, json, requests
from datetime import datetime, timedelta
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
                if val not in (None, "", 0, "0", 0.0, "N/A"): 
                    sheet.update_cell(found_idx, i, val)
            return "Updated"
        else:
            sheet.append_row(row_data)
            return "Appended"
    except Exception as e: return f"Err: {str(e)[:15]}"

# --- LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
except Exception as e:
    print(f"Login Fail: {e}"); exit(1)

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")

# --- DATA EXTRACTION ---
stats = gar.get_stats(today_str) or {}
summary = gar.get_user_summary(today_str) or {}

# 1. HRV & Пульс
hrv = stats.get("allDayAvgHrv") or stats.get("lastNightAvgHrv") or "N/A"
r_hr = summary.get("restingHeartRate") or summary.get("heartRateRestingValue") or "N/A"
bb_now = summary.get("bodyBatteryMostRecentValue") or "N/A"
bb_max = summary.get("bodyBatteryHighestValue") or "N/A"

# 2. Калории (Активные + БМР)
active_cals = summary.get("activeCalories", 0)
bmr_cals = summary.get("bmrCalories", 0)
total_cals = active_cals + bmr_cals if (active_cals or bmr_cals) else stats.get("calories", "N/A")

# 3. Сон (Улучшенный поиск Score)
slp_sc, slp_h = "N/A", "N/A"
try:
    sleep_data = gar.get_sleep_data(today_str)
    # Ищем Score в разных полях, которые использует Garmin
    slp_sc = sleep_data.get("dailySleepDTO", {}).get("sleepScore") or sleep_data.get("sleepScore") or "N/A"
    sec = sleep_data.get("dailySleepDTO", {}).get("sleepTimeSeconds", 0)
    if sec > 0: slp_h = round(sec / 3600, 1)
except: pass

# --- SYNC & AI ---
morning_row = [f"{today_str} 08:00", "", r_hr, hrv, bb_max, slp_sc, slp_h]
daily_row = [today_str, summary.get("totalSteps", 0), "", total_cals, r_hr, bb_now]

advice = "Нет данных"
try:
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)

    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY.strip())
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = (f"Биометрия: HRV {hrv}, Пульс {r_hr}, Батарейка {bb_now}, Калории {total_cals}, "
                  f"Сон {slp_h}ч (Score: {slp_sc}). Напиши ироничный совет.")
        res = model.generate_content(prompt)
        advice = res.text.strip()
except Exception as e: print(f"Sync/AI Error: {e}")

# --- TELEGRAM ---
if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
    msg = (f"🚀 *ОТЧЕТ ГАРМИН*\n📊 HRV: {hrv}\n😴 Сон: {slp_h}ч (Score: {slp_sc})\n"
           f"🔥 Калории: {total_cals}\n❤️ Пульс: {r_hr}\n⚡ BB: {bb_now}\n\n🤖 {advice}")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN.strip()}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID.strip(), "text": msg, "parse_mode": "Markdown"})
