import base64, tarfile, os, json, requests, garth, time, random, io, sys
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials

# --- 1. CONFIG ---
GARMIN_SESSION_BASE64 = os.environ.get("GARMIN_SESSION_BASE64")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")

def send_tg(message):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": message})
    except: pass

def safe_call(func, *args, **kwargs):
    """Предохранитель: если ловим 429, немедленно выходим из программы."""
    try:
        time.sleep(random.uniform(5, 10)) # Увеличили паузу для безопасности
        return func(*args, **kwargs)
    except Exception as e:
        err_msg = str(e)
        if "429" in err_msg:
            print(f"🚨 ОБНАРУЖЕН RATE LIMIT (429). Экстренная остановка, чтобы избежать бана!")
            sys.exit(1)
        print(f"⚠️ Ошибка запроса: {err_msg}")
        return None

def get_session():
    session_dir = os.path.abspath("./.garth")
    if os.path.exists(session_dir):
        import shutil
        shutil.rmtree(session_dir)
    os.makedirs(session_dir, exist_ok=True)

    if not GARMIN_SESSION_BASE64 or len(GARMIN_SESSION_BASE64) < 50:
        print("❌ Ключ сессии отсутствует или поврежден!")
        return None

    try:
        with open("session.tar.gz", "wb") as f:
            f.write(base64.b64decode(GARMIN_SESSION_BASE64.strip()))
        with tarfile.open("session.tar.gz", "r:gz") as tar:
            tar.extractall(path=".")
        
        garth.client.load(session_dir)
        gar = Garmin()
        gar.garth = garth.client
        # Проверка связи через профиль
        safe_call(gar.get_full_name)
        print("🚀 Успешный вход по СЕССИИ!")
        return gar
    except Exception as e:
        if "429" in str(e):
            print("🚨 Бан на уровне входа. Остановка.")
            sys.exit(1)
        print(f"⚠️ Сессия невалидна: {e}")
        return None

# --- 2. ОСНОВНОЙ ЦИКЛ ---
print(f"--- Запуск синхронизации: {today_str} ---")
gar = get_session()

if gar:
    results = []
    
    # Сбор Morning Summary
    summary = safe_call(gar.get_user_summary, today_str)
    r_hr = summary.get("restingHeartRate", "--") if summary else "--"
    bb = summary.get("bodyBatteryHighestValue", "--") if summary else "--"
    
    if r_hr != "--":
        results.append(f"💓 Пульс покой: {r_hr}")
    if bb != "--":
        results.append(f"🔋 Max Body Battery: {bb}")

    # Сбор Активностей (последние 2)
    activities = safe_call(gar.get_activities, 0, 2)
    found_activities = []
    if activities:
        for a in activities:
            if a.get("startTimeLocal", "").startswith(today_str):
                name = a.get("activityName", "Тренировка")
                dist = round(a.get("distance", 0)/1000, 2)
                dur = round(a.get("duration", 0)/3600, 2)
                aid = str(a.get("activityId"))
                found_activities.append([
                    a.get("startTimeLocal", "").replace("T", " ")[:16],
                    a.get("activityType", {}).get("typeKey", ""),
                    dur, dist, a.get("averageHR", ""), a.get("maxHR", ""),
                    round(a.get("intensityFactor", 0), 3),
                    round(a.get("activityTrainingLoad", 0), 1),
                    round(a.get("aerobicTrainingEffect", 0), 1),
                    a.get("calories", ""), "", "", "", "", "", f"'{aid}"
                ])
                results.append(f"🏃 {name}: {dist} км ({dur} ч)")

    # 3. ЗАПИСЬ В GOOGLE SHEETS (только если есть данные)
    if r_hr != "--" or found_activities:
        try:
            creds_dict = json.loads(GOOGLE_CREDS_JSON)
            creds = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
            gc = gspread.authorize(creds)
            ss = gc.open("Garmin_Data")
            
            # Обновляем Morning
