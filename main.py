import os, requests, json, sys, gspread
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials

# --- CONFIG ---
BIRTH_DATE = datetime(1963, 5, 29)
FTP_GARMIN = 213 
SPREADSHEET_ID = "1rxg5oqDXWXwHSHMmR-RbJuad8rXe2OdmCEMUMY2SBT4"

def get_bio_age():
    return (datetime.utcnow() - BIRTH_DATE).days / 365.25

def get_env(name):
    val = os.environ.get(name)
    if not val:
        print(f"❌ Нет переменной: {name}"); sys.exit(1)
    return val

CLIENT_ID = get_env('STRAVA_CLIENT_ID')
CLIENT_SECRET = get_env('STRAVA_CLIENT_SECRET')
REFRESH_TOKEN = get_env('STRAVA_REFRESH_TOKEN')
TELEGRAM_BOT_TOKEN = get_env('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = get_env('TELEGRAM_CHAT_ID')
GEMINI_API_KEY = get_env('GEMINI_API_KEY')
GOOGLE_CREDS_JSON = get_env('GOOGLE_CREDS')

def safe_float(val, default=0.0):
    if val is None or str(val).strip() in ["", "Н/Д", "None"]: return default
    try:
        v = float(str(val).replace(',', '.').strip())
        return v if v > 0 else default
    except: return default

def send_tg(msg):
    if len(msg) > 4000: msg = msg[:3900]
    try:
        res = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                     json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=15)
        print(f"📡 TG Status: {res.status_code}")
    except Exception as e: print(f"❌ TG Error: {e}")

def ask_expert(prompt, fallback):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
        data = res.json()
        if 'candidates' in data:
            ans = data['candidates'][0]['content']['parts'][0]['text'].strip().replace("_", " ").replace("*", " ")
            return ans
    except: pass
    return fallback

def get_google_client():
    creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), 
                                                  scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return gspread.authorize(creds)

def estimate_performance(activities, weight=88.0):
    vals_vo2 = []
    hr_max = 208 - (0.7 * get_bio_age())
    for a in sorted(activities, key=lambda x: x.get("start_date_local", "")):
        if a.get("type") not in ["Ride", "VirtualRide"]: continue
        w, hr = safe_float(a.get("average_watts")), safe_float(a.get("average_heartrate"))
        if w > 0 and hr > 105:
            v = (10.51 * (w * (hr_max / hr)) / weight) + 7
            if 20 < v < 70: vals_vo2.append(v)
    if not vals_vo2: return None, None
    avg_vo2 = round(sum(vals_vo2[-7:]) / len(vals_vo2[-7:]), 1)
    eftp = max(100, min(400, int(round(avg_vo2 * weight * 0.071, 0))))
    return avg_vo2, eftp

def main():
    now = datetime.utcnow() + timedelta(hours=1)
    today = now.strftime("%Y-%m-%d")
    
    activities = []
    try:
        res = requests.post("https://www.strava.com/oauth/token", data={
            'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
            'refresh_token': REFRESH_TOKEN, 'grant_type': 'refresh_token'
        }, timeout=15)
        token = res.json().get('access_token')
        r = requests.get("https://www.strava.com/api/v3/athlete/activities",
                         headers={"Authorization": f"Bearer {token}"}, params={"per_page": 100}, timeout=15)
        activities = r.json()
    except: pass

    client = get_google_client()
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
    records = sheet.get_all_records()
    morning = next((row for row in reversed(records) if today in str(row.get('Date', ''))), records[-1])

    # Данные
    rhr, hrv = safe_float(morning.get("Resting_HR"), 60), safe_float(morning.get("HRV"), 45)
    weight, fat = safe_float(morning.get("Weight"), 88.0), safe_float(morning.get("Body_Fat"), 18.3)
    sleep_raw = safe_float(morning.get("Sleep_Hours"), 7.0)
    sleep_h = sleep_raw / 10 if sleep_raw > 24 else sleep_raw
    deep_sleep = morning.get("Deep_Sleep", "н/д")
    sleep_score = safe_float(morning.get("Sleep_Score"), 70)
    recovery_h = safe_float(morning.get("Recovery_Time"), 0)
    body_battery = morning.get("Body_Battery", "н/д")

    vo2_val, eftp_val = estimate_performance(activities, weight=weight)
    
    ctl, atl = 0.0, 0.0
    for a in sorted(activities, key=lambda x: x.get("start_date_local", "")):
        if a.get("type") in ["Ride", "VirtualRide"]:
            w = safe_float(a.get("average_watts"), 0)
            tss = (a.get("moving_time", 0)/3600)*(w/FTP_GARMIN)**2*100 if w > 0 else 0
            ctl += (tss - ctl) / 42
            atl += (tss - atl) / 7
            
    tsb = round(ctl - atl, 1)
    tsb_tomorrow = round((ctl * (1 - 1/42)) - (atl * (1 - 1/7)), 1)

    # Строгая готовность ( Ready Score )
    score = 3.5
    if hrv < 55: score -= 1.0
    if sleep_score < 65: score -= 1.0
    if recovery_h > 24: score -= 1.0
    if tsb < -20: score -= 1.0
    score = max(1.0, min(5, round(score, 1)))

    icon = "🛑" if score < 2.5 else "🟢" if score > 3.8 else "🟡"
    circles = "🟢🟢🟢" if score >= 4 else "🟢🟢" if score >= 2.8 else "🟡"
    
    vo2_calc = vo2_val if vo2_val else 35
    f_age = round(get_bio_age() + (rhr-55)*0.4 + (fat-22)*0.5 - (hrv-45)*0.1 - (vo2_calc-35)*1.5, 1)
    f_age = max(45.0, min(get_bio_age() + 5, f_age))

    eftp_diff = eftp_val - FTP_GARMIN if eftp_val else 0
    eftp_str = f" | eFTP: {eftp_val} ({eftp_diff:+})" if eftp_val else ""
    header_ftp = f"{circles} *FTP: {FTP_GARMIN}{eftp_str}*"
    
    today_acts = [a for a in activities if a.get("start_date_local", "")[:10] == today]
    
    if not today_acts:
        prompt = (
            f"Ты — легендарный Арнольд, элитный коуч атлета 63 лет. Дай профессиональный анализ состояния. "
            f"ДАННЫЕ: HRV {hrv} (у него 100 — это элитно!), Пульс {rhr}, Сон {round(sleep_h,1)}ч (Глубокий: {deep_sleep}), "
            f"Sleep Score: {sleep_score}/100, Recovery Time: {recovery_h}ч, Body Battery: {body_battery}, "
            f"Fit Age: {f_age}, VO2max: {vo2_val if vo2_val else 'н/д'}. "
            f"ФОРМА: CTL {round(ctl,1)} (Фитнес), TSB {tsb} (Баланс). ГОТОВНОСТЬ: {score}/5. "
            f"\nИНСТРУКЦИИ: "
            f"1. Не просто читай цифры, а интерпретируй их! Сравнивай с нормой для 60+. ПИШИ НА РУССКОМ. "
            f"2. Если Recovery Time > 24ч или Sleep Score < 65 — СТРОГО ЗАПРЕТИ рекорды. "
            f"3. Трактуй TSB {tsb}: >10 (застой), -10...-25 (зона чемпионов), < -25 (риск перетрена). "
            f"4. ДАЙ ПЛАН: укажи зону (Z1, Z2 или Отдых) и время в минутах. "
            f"5. Если Fit Age {f_age} ниже 63 — вставь мощный комментарий. "
            f"6. Будь лаконичен (2-3 абзаца), сохрани дух Терминатора. Фирменная фраза в конце."
        )
        ai_msg = ask_expert(prompt, "Показатели в норме, работаем.")
        report = (f"🌅 *УТРЕННИЙ СТАТУС* {icon}\n{header_ftp}\n\n"
                  f"❤️ Пульс: {int(rhr)} | 🌀 HRV: {int(hrv)}\n📊 TSB: {tsb} (завтра: {tsb_tomorrow})\n"
                  f"🔋 *Готовность: {score}/5*\n🧬 Fit Age: {f_age} | VO2max: {vo2_val if vo2_val else 'н/д'}\n\n"
                  f"🤖 *ТРЕНЕР:* \n_{ai_msg}_")
    else:
        last = sorted(today_acts, key=lambda x: x.get("start_date_local"))[-1]
        dist = round(last.get("distance", 0) / 1000, 2)
        prompt = (
            f"Ты — коуч Арнольд. Разбери велотренировку атлета 63 лет: {last.get('name')}. "
            f"Дистанция: {dist}км, eFTP {eftp_val}. Утренний HRV был {hrv}, TSB {tsb}. "
            f"\nИНСТРУКЦИИ: "
            f"1. Оцени качество работы. Сравни eFTP {eftp_val} с базовым {FTP_GARMIN}. "
            f"2. Дай краткий совет на завтра. ПИШИ НА РУССКОМ. "
            f"3. В конце — фирменная фраза."
        )
        ai_msg = ask_expert(prompt, "Тренировка принята. Хорошая работа!")
        report = (f"🏃 *ТРЕНИРОВКА* {icon}\n{header_ftp}\n\n"
                  f"*{last.get('name')}*\n📍 {dist} км | 🧬 Fit Age: {f_age}\n"
                  f"📊 TSB: {tsb}\n\n🤖 *ТРЕНЕР:* \n_{ai_msg}_")

    send_tg(report)
    print("✅ Скрипт завершен, отчет отправлен.")

if __name__ == "__main__":
    main()
