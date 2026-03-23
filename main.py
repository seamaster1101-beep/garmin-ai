import base64, tarfile, os, json, requests, garth, time, random, io
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials

# --- 1. CONFIG ---
GARMIN_SESSION_BASE64 = os.environ.get("GARMIN_SESSION_BASE64")
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")

# --- 2. ФУНКЦИИ ЗАЩИТЫ ---
def safe_call(func, *args, **kwargs):
    try:
        time.sleep(random.uniform(2, 5)) # Небольшая пауза между запросами
        return func(*args, **kwargs)
    except Exception as e:
        print(f"⚠️ Ошибка при запросе: {e}")
        return None

def get_session():
    session_dir = os.path.abspath("./.garth")
    if os.path.exists(session_dir):
        import shutil
        shutil.rmtree(session_dir)
    os.makedirs(session_dir, exist_ok=True)

    if not GARMIN_SESSION_BASE64 or len(GARMIN_SESSION_BASE64) < 50:
        print("❌ Ключ сессии отсутствует или слишком короткий! Скрипт остановлен.")
        return None

    try:
        with open("session.tar.gz", "wb") as f:
            f.write(base64.b64decode(GARMIN_SESSION_BASE64.strip()))
        with tarfile.open("session.tar.gz", "r:gz") as tar:
            tar.extractall(path=".")
        
        garth.client.load(session_dir)
        gar = Garmin()
        gar.garth = garth.client
        # Проверка связи
        gar.get_display_name()
        print("🚀 Успешный вход по СЕССИИ!")
        return gar
    except Exception as e:
        print(f"⚠️ Сессия невалидна: {e}. Нужно обновить GARMIN_SESSION_BASE64.")
        return None

# --- 3. ОСНОВНОЙ ЦИКЛ ---
gar = get_session()

if gar:
    # Сбор данных Morning
    summary = safe_call(gar.get_user_summary, today_str) or {}
    hrv_res = safe_call(gar.get_hrv_data, today_str) or {}
    hrv = hrv_res.get("hrvSummary", {}).get("lastNightAvg") or ""
    r_hr = summary.get("restingHeartRate") or ""
    bb_max = summary.get("bodyBatteryHighestValue", "")

    # Вес
    weight, fat, muscle = "", "", ""
    try:
        w_data = gar.get_body_composition((now - timedelta(days=3)).strftime("%Y-%m-%d"), today_str)
        weights = w_data.get('dateWeightList', [])
        if weights:
            last_w = max(weights, key=lambda x: x.get('sampleTime', 0))
            weight = round(float(last_w.get('weight', 0)) / 1000, 1)
            fat = last_w.get('bodyFat', "")
            muscle = round(float(last_w.get('muscleMass', 0)) / 1000, 1)
    except: pass

    # Сон
    slp_sc, slp_h = "", ""
    sd = safe_call(gar.get_sleep_data, today_str) or {}
    if not sd.get("dailySleepDTO"):
        sd = safe_call(gar.get_sleep_data, (now - timedelta(days=1)).strftime("%Y-%m-%d")) or {}
    
    dto = sd.get("dailySleepDTO") or {}
    if dto:
        slp_h = round(float(dto.get("sleepTimeSeconds", 0)) / 3600, 1)
        slp_sc = dto.get("sleepScore") or ""

    # Запись в Google Sheets
    try:
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
        gc = gspread.authorize(creds)
        ss = gc.open("Garmin_Data")
        
        # Запись Morning
        morning_row = [f"'{today_str}", weight, fat, muscle, r_hr, hrv, bb_max, slp_sc, slp_h, 63, 50.0]
        sheet = ss.worksheet("Morning")
        try:
            cell = sheet.find(today_str)
            if cell: sheet.update(range_name=f"A{cell.row}:K{cell.row}", values=[morning_row], value_input_option="USER_ENTERED")
            else: sheet.append_row(morning_row, value_input_option="USER_ENTERED")
        except:
            sheet.append_row(morning_row, value_input_option="USER_ENTERED")
        
        # Сбор активностей
        activities = gar.get_activities(0, 3)
        act_sheet = ss.worksheet("Activities")
        existing_ids = {r[15] for r in act_sheet.get_all_values() if len(r) > 15}
        
        for a in activities:
            aid = str(a.get("activityId"))
            if f"'{aid}" not in existing_ids and a.get("startTimeLocal", "").startswith(today_str):
                row = [
                    a.get("startTimeLocal", "").replace("T", " ")[:16],
                    a.get("activityType", {}).get("typeKey", ""),
                    round(a.get("duration", 0)/3600, 2),
                    round(a.get("distance", 0)/1000, 2),
                    a.get("averageHR", ""), a.get("maxHR", ""),
                    round(a.get("intensityFactor", 0), 3),
                    round(a.get("activityTrainingLoad", 0), 1),
                    round(a.get("aerobicTrainingEffect", 0), 1),
                    a.get("calories", ""),
                    a.get("avgPower", ""),
                    a.get("averageBikingCadence", ""),
                    a.get("weightedAveragePower", ""),
                    a.get("trainingStressScore", ""),
                    "", # VI
                    f"'{aid}"
                ]
                act_sheet.append_row(row, value_input_option="USER_ENTERED")

        # Telegram
        msg = f"✅ Синхронизация {today_str} завершена!\nHRV: {hrv}, Сон: {slp_h}ч, Вес: {weight}кг."
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
        print("✅ Все данные записаны, отчет отправлен.")

    except Exception as e:
        print(f"🚨 Ошибка Google/Telegram: {e}")

else:
    print("⏸ Скрипт ждет обновления токена в секретах.")
