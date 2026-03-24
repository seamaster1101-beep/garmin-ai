import base64, tarfile, os, json, requests, garth, time, random, io, sys
from datetime import datetime
from garminconnect import Garmin

# --- CONFIG ---
GARMIN_SESSION_BASE64 = os.environ.get("GARMIN_SESSION_BASE64")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

today_str = datetime.now().strftime("%Y-%m-%d")

def send_tg(message):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": message})
    except: pass

def safe_call(func, *args, **kwargs):
    """Предохранитель: если ловим 429, немедленно выходим из программы."""
    try:
        time.sleep(random.uniform(3, 7)) # Имитируем раздумья человека
        return func(*args, **kwargs)
    except Exception as e:
        err_msg = str(e)
        if "429" in err_msg:
            print(f"🚨 ОБНАРУЖЕН RATE LIMIT (429). Экстренная остановка!")
            # Не шлем уведомление в ТГ здесь, чтобы не плодить запросы
            sys.exit(1) # Убиваем скрипт полностью
        print(f"⚠️ Ошибка запроса: {err_msg}")
        return None

def get_session():
    session_dir = os.path.abspath("./.garth")
    if os.path.exists(session_dir):
        import shutil
        shutil.rmtree(session_dir)
    os.makedirs(session_dir, exist_ok=True)

    if not GARMIN_SESSION_BASE64:
        print("❌ Нет ключа сессии в секретах!")
        return None

    try:
        # Распаковка "паспорта"
        with open("session.tar.gz", "wb") as f:
            f.write(base64.b64decode(GARMIN_SESSION_BASE64.strip()))
        with tarfile.open("session.tar.gz", "r:gz") as tar:
            tar.extractall(path=".")
        
        garth.client.load(session_dir)
        gar = Garmin()
        gar.garth = garth.client
        # Легкая проверка связи
        gar.get_full_name()
        print("🚀 Успешный вход по СЕССИИ!")
        return gar
    except Exception as e:
        if "429" in str(e):
            print("🚨 Бан на уровне входа. Нужно ждать.")
            sys.exit(1)
        print(f"⚠️ Сессия невалидна: {e}")
        return None

# --- ЗАПУСК ---
print(f"--- Запуск синхронизации за {today_str} ---")
gar = get_session()

if gar:
    results = []
    
    # 1. Базовые показатели (Один запрос вместо трех)
    summary = safe_call(gar.get_user_summary, today_str)
    if summary:
        r_hr = summary.get("restingHeartRate", "--")
        bb = summary.get("bodyBatteryHighestValue", "--")
        results.append(f"💓 Пульс покой: {r_hr}\n🔋 Body Battery: {bb}")

    # 2. Активности (Только последние)
    activities = safe_call(gar.get_activities, 0, 1)
    if activities:
        for a in activities:
            if a.get("startTimeLocal", "").startswith(today_str):
                name = a.get("activityName", "Тренировка")
                dist = round(a.get("distance", 0)/1000, 2)
                results.append(f"🏃 {name}: {dist} км")

    # Если что-то собрали — шлем в ТГ
    if results:
        report = f"✅ Данные Garmin ({today_str}):\n\n" + "\n".join(results)
        send_tg(report)
        print("✅ Отчет отправлен в Telegram.")
    else:
        print("ℹ️ Новых данных за сегодня пока нет.")

else:
    print("⏸ Ожидание обновления токена.")
