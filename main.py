import os, requests, json, sys, gspread
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials

# --- CONFIG ---
BIO_AGE = 63
FTP_GARMIN = 213 
SPREADSHEET_ID = "1rxg5oqDXWXwHSHMmR-RbJuad8rXe2OdmCEMUMY2SBT4"

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
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                     json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=15)
    except: pass

def ask_expert(prompt, fallback):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
        return res.json()['candidates'][0]['content']['parts'][0]['text'].strip().replace("_", " ").replace("*", " ")
    except: return fallback

def main():
    now = datetime.utcnow() + timedelta(hours=1)
    today = now.strftime("%Y-%m-%d")
    
    # 1. Strava
    activities = []
    try:
        r = requests.post("https://www.strava.com/oauth/token", data={
            'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
            'refresh_token': REFRESH_TOKEN, 'grant_type': 'refresh_token'
        }, timeout=15)
        token = r.json().get('access_token')
        res = requests.get("https://www.strava.com/api/v3/athlete/activities",
                         headers={"Authorization": f"Bearer {token}"}, params={"per_page": 100}, timeout=15)
        activities = res.json()
    except: pass

    # 2. Google Sheets
    creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), 
                                                  scopes=["https://www.googleapis.com/auth/spreadsheets"])
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
    records = sheet.get_all_records()
    
    morning = {}
    row_idx = None
    for i, row in enumerate(records):
        if today in str(row.get('Date', '')):
            morning = row
            row_idx = i + 2
            break
    
    if not morning:
        print("❌ Данные на сегодня не найдены"); return

    # Данные
    rhr, hrv = safe_float(morning.get("Resting_HR"), 60), safe_float(morning.get("HRV"), 45)
    weight, fat = safe_float(morning.get("Weight"), 88.0), safe_float(morning.get("Body_Fat"), 18.3)
    s_raw = safe_float(morning.get("Sleep_Hours"), 7.0)
    sleep_h = s_raw / 10 if s_raw > 24 else s_raw
    deep_sleep = morning.get("Deep_Sleep", "н/д")
    sleep_score = safe_float(morning.get("Sleep_Score"), 70)
    recovery_h = safe_float(morning.get("Recovery_Time"), 0)
    body_battery = morning.get("Body_Battery", "н/д")

    # 3. TSB & VO2
    ctl, atl, vo2_vals = 0.0, 0.0, []
    hr_max = 208 - (0.7 * BIO_AGE)
    for a in sorted(activities, key=lambda x: x.get("start_date_local", "")):
        if a.get("type") in ["Ride", "VirtualRide"]:
            w = safe_float(a.get("average_watts"))
            tss = (a.get("moving_time", 0)/3600)*(w/FTP_GARMIN)**2*100 if w > 0 else 0
            ctl += (tss - ctl) / 42
            atl += (tss - atl) / 7
            hr = safe_float(a.get("average_heartrate"))
            if w > 0 and hr > 105:
                v = (10.51 * (w * (hr_max / hr)) / weight) + 7
                if 20 < v < 70: vo2_vals.append(v)

    tsb = round(ctl - atl, 1)
    tsb_tom = round((ctl * (1 - 1/42)) - (atl * (1 - 1/7)), 1)
    vo2_avg = round(sum(vo2_vals[-7:]) / len(vo2_vals[-7:]), 1) if vo2_vals else 35.0

    # 4. ГОТОВНОСТЬ (Штрафная модель)
    score = 4.0 # База
    if hrv > 85: score += 0.5
    if hrv < 55: score -= 1.0
    if sleep_score < 65: score -= 1.0
    if recovery_h > 24: score -= 1.0
    if recovery_h > 40: score -= 1.0
    if tsb < -20: score -= 1.5
    score = max(1.0, min(5.0, round(score, 1)))

    # 5. FITNESS AGE & ЗАПИСЬ
    f_age = round(BIO_AGE + (rhr-55)*0.4 + (fat-22)*0.5 - (hrv-45)*0.1 - (vo2_avg-35)*1.5, 1)
    f_age = max(45.0, min(BIO_AGE + 2, f_age))
    
    headers = sheet.row_values(1)
    if "Fitness_Age" in headers:
        col = headers.index("Fitness_Age") + 1
        sheet.update_cell(row_idx, col, f_age)
        print(f"✅ FitAge {f_age} записан.")

    # 6. ОТЧЕТЫ (ТВОИ ПОЛНЫЕ ПРОМПТЫ)
    icon = "🛑" if score < 2.5 else "🟡" if score < 3.8 else "🟢"
    eftp = max(100, min(400, int(round(vo2_avg * weight * 0.071, 0))))
    header_ftp = f"🟢🟢 *FTP: {FTP_GARMIN} | eFTP: {eftp} ({eftp-FTP_GARMIN:+})*"
    
    today_acts = [a for a in activities if a.get("start_date_local", "")[:10] == today]

    if not today_acts:
        prompt = (
            f"Ты — легендарный Арнольд, элитный коуч атлета 63 лет. Дай профессиональный анализ состояния. "
            f"ДАННЫЕ: HRV {hrv} (у него 100 — это элитно!), Пульс {rhr}, Сон {round(sleep_h,1)}ч (Глубокий: {deep_sleep}), "
            f"Sleep Score: {sleep_score}/100, Recovery Time: {recovery_h}ч, Body Battery: {body_battery}, "
            f"Fit Age: {f_age}, VO2max: {vo2_avg}. "
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
                  f"❤️ Пульс: {int(rhr)} | 🌀 HRV: {int(hrv)}\n📊 TSB: {tsb} (завтра: {tsb_tom})\n"
                  f"🔋 *Готовность: {score}/5*\n🧬 Fit Age: {f_age} | VO2max: {vo2_avg}\n\n"
                  f"🤖 *ТРЕНЕР:* \n_{ai_msg}_")
        send_tg(report)

if __name__ == "__main__":
    main()
