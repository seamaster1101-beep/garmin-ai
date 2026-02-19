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

# --- LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
except Exception as e:
    print(f"Login Fail: {e}"); exit(1)

today_str = datetime.now().strftime("%Y-%m-%d")

# --- 1. СБОР ДАННЫХ (БЕЗОПАСНЫЙ) ---
try:
    stats = gar.get_stats(today_str) or {}
    summary = gar.get_user_summary(today_str) or {}
    
    hrv = stats.get("allDayAvgHrv") or stats.get("lastNightAvgHrv") or "-"
    r_hr = summary.get("restingHeartRate") or "-"
    bb_now = summary.get("bodyBatteryMostRecentValue") or "-"
    steps = summary.get("totalSteps", 0)
    
    # Калории (берем только активные + БМР)
    cals = (summary.get("activeCalories", 0) + summary.get("bmrCalories", 0))
    
    # ТРЕНИРОВКИ: Ищем только сегодняшние
    activity_info = ""
    total_act_dist = 0
    activities = gar.get_activities(0, 5)
    for act in activities:
        if act.get('startTimeLocal', '')[:10] == today_str:
            name = act.get('activityName', 'Тренировка')
            d = act.get('distance', 0)
            if d > 0:
                dist_km = round(d/1000, 2)
                total_act_dist += dist_km
                activity_info += f"🏃 {name}: {dist_km} км\n"
            else:
                dur = round(act.get('duration', 0) / 60)
                activity_info += f"💪 {name}: {dur} мин\n"
except Exception as e:
    print(f"Data Error: {e}"); activity_info = ""; total_act_dist = 0

# --- 2. AI ADVICE ---
advice = "Держи темп!"
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY.strip())
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"У меня сегодня: Шаги: {steps}, Пульс: {r_hr}, Тренировки: {activity_info}. Дай ироничный совет."
        res = model.generate_content(prompt)
        advice = res.text.strip()
    except: advice = "ИИ взял выходной."

# --- 3. TELEGRAM ---
if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
    msg = (
        f"🚀 *ОТЧЕТ ГАРМИН*\n"
        f"📊 HRV: {hrv} | ❤️ HR: {r_hr}\n"
        f"👟 Шаги: {steps} ({round(total_act_dist, 2)} км тренировок)\n"
        f"⚡ Батарейка: {bb_now}%\n"
        f"🔥 Калории: {cals}\n"
        f"\n{activity_info if activity_info else 'Тренировок пока нет'}\n"
        f"🤖 {advice.replace('*', '')}"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN.strip()}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID.strip(), "text": msg, "parse_mode": "Markdown"})

# --- 4. TABLE SYNC (УПРОЩЕННО) ---
try:
    creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(creds).open("Garmin_Data")
    # Просто записываем в конец Daily для теста
    ss.worksheet("Daily").append_row([today_str, steps, total_act_dist, cals, r_hr, bb_now])
except Exception as e: print(f"Table Error: {e}")
