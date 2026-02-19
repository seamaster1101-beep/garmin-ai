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

# --- DATA COLLECTION ---
# 1. Основная сводка
stats = gar.get_stats(today_str) or {}
summary = gar.get_user_summary(today_str) or {}

# 2. Метрики здоровья
hrv = stats.get("allDayAvgHrv") or stats.get("lastNightAvgHrv") or "N/A"
r_hr = summary.get("restingHeartRate") or summary.get("heartRateRestingValue") or "N/A"
bb_now = summary.get("bodyBatteryMostRecentValue") or "N/A"
bb_max = summary.get("bodyBatteryHighestValue") or "N/A"

# 3. Сон
slp_sc, slp_h = "N/A", "N/A"
try:
    sleep_data = gar.get_sleep_data(today_str)
    slp_sc = sleep_data.get("dailySleepDTO", {}).get("sleepScore") or "N/A"
    sec = sleep_data.get("dailySleepDTO", {}).get("sleepTimeSeconds", 0)
    if sec > 0: slp_h = round(sec / 3600, 1)
except: pass

# 4. Активность за день
steps = summary.get("totalSteps", 0)
daily_dist = round((summary.get("totalDistanceMeters", 0) / 1000), 2)
cals = (summary.get("activeCalories", 0) + summary.get("bmrCalories", 0)) or stats.get("calories", "N/A")

# 5. Детализация тренировок (Activities)
activity_info = ""
try:
    activities = gar.get_activities(0, 5) # Проверяем последние 5
    for act in activities:
        act_date = act.get('startTimeLocal', '')[:10]
        if act_date == today_str:
            name = act.get('activityName', 'Тренировка')
            a_dist = act.get('distance', 0)
            if a_dist > 0:
                activity_info += f"🏃 {name}: {round(a_dist/1000, 2)} км\n"
            else:
                dur = round(act.get('duration', 0) / 60)
                activity_info += f"💪 {name}: {dur} мин\n"
except Exception as e:
    print(f"Activities Error: {e}")

# --- SYNC TO GOOGLE ---
morning_row = [f"{today_str} 08:00", "", r_hr, hrv, bb_max, slp_sc, slp_h]
daily_row = [today_str, steps, daily_dist, cals, r_hr, bb_now]

advice = "Нет данных для ИИ"
try:
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)

    # --- AI ADVICE ---
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY.strip())
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = (f"Биометрия: HRV {hrv}, Пульс {r_hr}, Батарейка {bb_now}, Калории {cals}, "
                  f"Шаги {steps}, Дистанция {daily_dist}км, Сон {slp_h}ч (Score: {slp_sc}). "
                  f"Напиши один ироничный и мудрый совет на день.")
        res = model.generate_content(prompt)
        advice = res.text.strip()
except Exception as e:
    print(f"Sync/AI Error: {e}")

# --- TELEGRAM ---
if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
    train_section = f"\n🏋️ *ТРЕНИРОВКИ:*\n{activity_info}" if activity_info else ""
    msg = (
        f"🚀 *ОТЧЕТ ГАРМИН*\n"
        f"📊 HRV: {hrv}\n"
        f"😴 Сон: {slp_h}ч (Score: {slp_sc})\n"
        f"🔥 Калории: {cals}\n"
        f"👟 Шаги: {steps} ({daily_dist} км)\n"
        f"❤️ Пульс: {r_hr}\n"
        f"⚡ BB: {bb_now}\n"
        f"{train_section}\n"
        f"🤖 {advice.replace('*', '')}"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN.strip()}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID.strip(), "text": msg, "parse_mode": "Markdown"})
    print("Done! Message sent to Telegram.")
