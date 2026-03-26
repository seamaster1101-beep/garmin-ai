import os
import requests
import json
from datetime import datetime, timedelta
import sys

# --- CONFIG ---
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

FTP = 250  # ⚠️ поставь свой реальный FTP

# --- TELEGRAM ---
def send_tg(msg):
    if len(msg) > 4000:
        msg = msg[:3900] + "\n\n...(обрезано)"
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

        data = res.json()
        token = data.get('access_token')

        if not token:
            print("❌ Нет access_token")
            return []

        r = requests.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 100},
            timeout=15
        )

        if r.status_code != 200:
            print("Strava error:", r.text)
            return []

        data = r.json()

        if not isinstance(data, list):
            print("❌ Strava вернул не список:", data)
            return []

        return data

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

        # сначала сегодня
        for row in reversed(records):
            if target_date in str(row.get('Date', '')):
                return row

        # fallback на вчера
        yesterday = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        for row in reversed(records):
            if yesterday in str(row.get('Date', '')):
                print("⚠️ Использую вчерашние данные")
                return row

    except Exception as e:
        print("Sheets error:", e)

    return {}

# --- TRAINING LOAD (TSS) ---
def calc_tss(activity):
    watts = activity.get("average_watts")
    duration = activity.get("moving_time", 0)

    if not watts or watts == 0:
        return 0

    intensity = watts / FTP
    hours = duration / 3600

    return round(hours * (intensity ** 2) * 100, 1)

# --- CTL / ATL / TSB ---
def calc_training_metrics(activities):
    daily = {}

    for a in activities:
        d = a.get("start_date_local", "")[:10]
        tss = calc_tss(a)
        daily[d] = daily.get(d, 0) + tss

    ctl = 0
    atl = 0

    for d in sorted(daily.keys()):
        load = daily[d]
        ctl += (load - ctl) * (1/42)
        atl += (load - atl) * (1/7)

    tsb = ctl - atl

    return round(ctl,1), round(atl,1), round(tsb,1)

# --- VO2max ---
def estimate_vo2max(activities):
    vo2_list = []

    for a in activities:
        speed = a.get("average_speed")  # м/с
        hr = a.get("average_heartrate")

        if speed and hr and hr > 0:
            vo2 = (speed * 3.6) * 0.2 + 3.5
            vo2_list.append(vo2)

    if vo2_list:
        return round(sum(vo2_list)/len(vo2_list),1)

    return "Н/Д"

# --- POWER ZONES ---
def power_zone(w):
    if not w:
        return "N/A"
    z = w / FTP
    if z < 0.55: return "Z1"
    if z < 0.75: return "Z2"
    if z < 0.90: return "Z3"
    if z < 1.05: return "Z4"
    if z < 1.20: return "Z5"
    return "Z6+"

# --- FITNESS ---
def calc_fitness(rhr, hrv):
    try:
        rhr = int(rhr)
        hrv = int(hrv)
        score = 70 - (rhr - 45)*2 + (hrv - 70)*0.5
        return max(10, min(100, int(score)))
    except:
        return "Н/Д"

def detect_status(score):
    if isinstance(score, str):
        return "Н/Д"
    if score > 80: return "Готов 🚀"
    if score > 60: return "Норма 👍"
    if score > 40: return "Устал 😐"
    return "Перегруз ⚠️"

# --- LOCAL FALLBACK AI ---
def local_ai(tsb, load):
    if tsb < -10:
        return "Ты перегружен. Завтра только восстановление."
    if tsb < 0:
        return "Работаешь в нагрузке. Это нормально."
    if tsb > 5:
        return "Свежий. Можно давить."
    return "Баланс норм. Работай."

# --- GEMINI ---
def ask_arnie(prompt, tsb, load):
    urls = [
        f"https://generativelanguage.googleapis.com/v1/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}",
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent?key={GEMINI_API_KEY}"
    ]

    payload = {"contents": [{"parts": [{"text": prompt[:1000]}]}]}

    for url in urls:
        try:
            res = requests.post(url, json=payload, timeout=20)

            if res.status_code != 200:
                print("Gemini fail:", res.status_code)
                continue

            data = res.json()
            text = data.get("candidates",[{}])[0].get("content",{}).get("parts",[{}])[0].get("text")

            if text:
                return text[:500]

        except Exception as e:
            print("Gemini error:", e)

    return local_ai(tsb, load)

# --- MAIN ---
def main():
    # ✅ Утренний лаг (ключевой фикс)
    now = datetime.utcnow() + timedelta(hours=1) - timedelta(hours=3)
    today = now.strftime("%Y-%m-%d")

    activities = get_strava_data()
    morning = get_morning_metrics(today)

    # только сегодня
    today_acts = [a for a in activities if a.get("start_date_local","")[:10] == today]
    today_acts = today_acts[::-1]

    # --- MORNING ---
    rhr = morning.get("Resting_HR", "Н/Д")
    hrv = morning.get("HRV", "Н/Д")

    fitness = calc_fitness(rhr, hrv)
    status = detect_status(fitness)

    # --- LOAD ---
    ctl, atl, tsb = calc_training_metrics(activities)

    # --- VO2 ---
    vo2 = estimate_vo2max(activities)

    act_text = ""
    total_tss = 0

    for a in today_acts:
        name = a.get("name", "Тренировка")
        dist = round(a.get("distance",0)/1000,2)
        watts = a.get("average_watts")
        zone = power_zone(watts)
        tss = calc_tss(a)

        total_tss += tss
        act_text += f"• {name}: {dist} км | {zone} | TSS {tss}\n"

    if not act_text:
        act_text = "Сегодня отдыхаешь."

    # --- AI ---
    prompt = f"""
    Пульс {rhr}, HRV {hrv}
    CTL {ctl}, ATL {atl}, TSB {tsb}
    Нагрузка {total_tss}
    """

    ai = ask_arnie(prompt, tsb, total_tss)

    # --- REPORT ---
    report = (
        f"🏋️ *ARNI REPORT*\n\n"
        f"🔥 Fitness: {fitness}/100\n"
        f"🚦 Статус: {status}\n\n"
        f"📊 CTL: {ctl} | ATL: {atl} | TSB: {tsb}\n"
        f"⚡ Load today: {total_tss}\n"
        f"🫁 VO2max: {vo2}\n\n"
        f"📊 Утро: ❤️ {rhr} | 🌀 {hrv}\n\n"
        f"🏃 Активность:\n{act_text}\n"
        f"🤖 АРНИ:\n_{ai}_"
    )

    send_tg(report)
    print("✅ DONE")

if __name__ == "__main__":
    main()
