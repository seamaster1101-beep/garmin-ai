import os
import requests
import json
from datetime import datetime, timedelta
import sys

# --- CONFIG ---
BIO_AGE = 63  # твой возраст

def get_env(name):
    val = os.environ.get(name)
    if not val:
        print(f"❌ Нет переменной: {name}")
        sys.exit(1)
    return val

SPREADSHEET_ID = "1rxg5oqDXWXwHSHMmR-RbJuad8rXe2OdmCEMUMY2SBT4"

CLIENT_ID = get_env('STRAVA_CLIENT_ID')
CLIENT_SECRET = get_env('STRAVA_CLIENT_SECRET')
REFRESH_TOKEN = get_env('STRAVA_REFRESH_TOKEN')
TELEGRAM_BOT_TOKEN = get_env('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = get_env('TELEGRAM_CHAT_ID')
GEMINI_API_KEY = get_env('GEMINI_API_KEY')
GOOGLE_CREDS_JSON = get_env('GOOGLE_CREDS')

FTP = 250

# --- TELEGRAM ---
def send_tg(msg):
    if len(msg) > 4000:
        msg = msg[:3900]
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=15
        )
    except:
        pass

# --- STRAVA ---
def get_strava_data():
    try:
        res = requests.post("https://www.strava.com/oauth/token", data={
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'refresh_token': REFRESH_TOKEN,
            'grant_type': 'refresh_token'
        }, timeout=15)

        token = res.json().get('access_token')
        if not token:
            return []

        r = requests.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 100},
            timeout=15
        )

        data = r.json()
        return data if r.status_code == 200 and isinstance(data, list) else []

    except Exception as e:
        print("Strava error:", e)
        return []

# --- GOOGLE SHEETS ---
def get_morning_metrics(target_date):
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_info(
            json.loads(GOOGLE_CREDS_JSON),
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
        )

        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
        records = sheet.get_all_records()

        for row in reversed(records):
            if target_date in str(row.get('Date', '')):
                return row

        yesterday = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        for row in reversed(records):
            if yesterday in str(row.get('Date', '')):
                print("⚠️ Использую вчерашние данные")
                return row

    except Exception as e:
        print("Sheets error:", e)

    return {}

# --- TSS ---
def calc_tss(a):
    w = a.get("average_watts")
    t = a.get("moving_time", 0)
    if not w:
        return 0
    return round((t/3600)*(w/FTP)**2*100,1)

# --- VO2 ---
def estimate_vo2max(activities):
    vals = []
    for a in activities:
        s = a.get("average_speed")
        hr = a.get("average_heartrate")
        if s and hr and 2 < s < 8 and 80 < hr < 180:
            vals.append((s*3.6)*0.2 + 3.5)
    if len(vals) >= 3:
        v = round(sum(vals)/len(vals),1)
        return v if v >= 20 else None
    return None

# --- FITNESS AGE ---
def fitness_age(rhr, hrv, vo2):
    try:
        rhr = int(rhr)
        hrv = int(hrv)
        # Базовая точка — твой реальный возраст
        age_diff = 0
        
        # RHR: норма для 60 лет ~65-70. Твои 44-45 — это уровень элиты.
        age_diff -= (65 - rhr) * 0.5  # за каждый удар ниже нормы убираем полгода
        
        # HRV: для 60 лет норма ~25-35. Твои 89 — это космос.
        age_diff -= (hrv - 35) * 0.4  # за каждый пункт выше нормы
        
        # VO2max: если есть данные
        if vo2:
            age_diff -= (vo2 - 30) * 1.0
            
        return round(max(30, BIO_AGE + age_diff)) # не даем стать моложе 30
    except:
        return BIO_AGE

# --- AI ---
def ask_arnie(prompt, fallback_text):
    urls = [
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    ]

    payload = {
        "contents": [{"parts": [{"text": prompt[:1200]}]}]
    }

    for url in urls:
        try:
            r = requests.post(url, json=payload, timeout=20)

            if r.status_code != 200:
                print("❌ Gemini status:", r.status_code, r.text[:200])
                continue

            data = r.json()

            text = data.get("candidates", [{}])[0] \
                       .get("content", {}) \
                       .get("parts", [{}])[0] \
                       .get("text")

            if text and len(text.strip()) > 10:
                return text.strip()[:600]

            print("⚠️ Пустой ответ Gemini:", data)

        except Exception as e:
            print("❌ Gemini error:", e)

    return fallback_text

# --- MAIN ---
def main():
    now = datetime.utcnow() + timedelta(hours=1) - timedelta(hours=3)
    today = now.strftime("%Y-%m-%d")
    yesterday = (datetime.strptime(today,"%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")

    activities = get_strava_data()
    morning = get_morning_metrics(today)

    rhr = morning.get("Resting_HR","Н/Д")
    hrv = morning.get("HRV","Н/Д")

    # сегодняшние активности
    today_acts = [a for a in activities if a.get("start_date_local","")[:10]==today]

    # вчера
    y_tss = sum(calc_tss(a) for a in activities if a.get("start_date_local","")[:10]==yesterday)

    vo2 = estimate_vo2max(activities)
    f_age = fitness_age(rhr, hrv, vo2)

    # =========================
    # УТРЕННИЙ РЕЖИМ
    # =========================
    if not today_acts:

        prompt = f"""
Ты — Арнольд, легендарный тренер. Проанализируй состояние атлета (63 года).
Данные: Пульс {rhr}, HRV {hrv}, вчерашний TSS {y_tss}.
Дай развернутый, ироничный и мотивирующий разбор. Оцени восстановление и дай совет по интенсивности на сегодня.
"""

Данные:
Пульс {rhr}
HRV {hrv}
Вчера нагрузка {y_tss}

Ответ 3 строки:

Восстановление:
Нервная система:
Готовность:
"""

        fallback = "Восстановление хорошее. Нервная система стабильна. Готов к умеренной нагрузке."
        ai = ask_arnie(prompt, fallback)

        report = (
            f"🌅 *УТРЕННИЙ СТАТУС*\n\n"
            f"❤️ Пульс: {rhr}\n"
            f"🌀 HRV: {hrv}\n"
            f"📊 Вчера TSS: {y_tss}\n"
            f"🧬 Fitness Age: {f_age}\n\n"
            f"🤖 АРНИ:\n_{ai}_"
        )

        send_tg(report)
        print("✅ MORNING")
        return

    # =========================
    # АНАЛИЗ ПОСЛЕДНЕЙ ТРЕНИРОВКИ
    # =========================
    last = sorted(today_acts, key=lambda x: x.get("start_date_local"))[-1]

    tss = calc_tss(last)
    dist = round(last.get("distance",0)/1000,2)
    name = last.get("name","Тренировка")

    prompt = f"""
Ты тренер.

Проанализируй тренировку:

{name}
Дистанция {dist} км
TSS {tss}
HRV {hrv}
Пульс {rhr}

Ответ:

Качество:
Нагрузка:
Что дальше:
"""

    fallback = "Нагрузка умеренная. Тренировка выполнена нормально. Продолжай по плану."
    ai = ask_arnie(prompt, fallback)

    report = (
        f"🏃 *ТРЕНИРОВКА*\n\n"
        f"{name}\n"
        f"{dist} км | TSS {tss}\n"
        f"🧬 Fitness Age: {f_age}\n\n"
        f"🤖 АРНИ:\n_{ai}_"
    )

    send_tg(report)
    print("✅ TRAINING")

if __name__ == "__main__":
    main()
