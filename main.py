import os, requests, json, sys, gspread
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials

# --- CONFIG ---
BIO_AGE = 63
FTP_GARMIN = 213 
ATHLETE_WEIGHT = 88.0
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

def ask_expert(prompt):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
        ans = res.json()['candidates'][0]['content']['parts'][0]['text']
        return ans.strip().replace("_", " ").replace("*", " ")
    except Exception as e:
        return f"Ошибка связи с базой: {e}"

def main():
    now = datetime.utcnow() + timedelta(hours=1)
    today = now.strftime("%Y-%m-%d")
    
    # 1. Сбор данных Strava
    activities = []
    try:
        r = requests.post("https://www.strava.com/oauth/token", data={
            'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
            'refresh_token': REFRESH_TOKEN, 'grant_type': 'refresh_token'
        }, timeout=15)
        token = r.json().get('access_token')
        res = requests.get("https://www.strava.com/api/v3/athlete/activities",
                         headers={"Authorization": f"Bearer {token}"}, params={"per_page": 50}, timeout=15)
        activities = res.json()
    except: pass

    # 2. Google Sheets
    creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), 
                                                  scopes=["https://www.googleapis.com/auth/spreadsheets"])
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
    records = sheet.get_all_records()
    
    morning = next((row for row in reversed(records) if today in str(row.get('Date', ''))), records[-1])
    row_idx = next((i+2 for i, r in enumerate(records) if today in str(r.get('Date', ''))), None)

    # Метрики
    rhr = safe_float(morning.get("Resting_HR"), 60)
    hrv = safe_float(morning.get("HRV"), 45)
    fat = safe_float(morning.get("Body_Fat"), 18.3)
    s_raw = safe_float(morning.get("Sleep_Hours"), 7.0)
    sleep_h = s_raw / 10 if s_raw > 24 else s_raw
    sleep_score = safe_float(morning.get("Sleep_Score"), 70)
    recovery_h = safe_float(morning.get("Recovery_Time"), 0)

    # 3. Расчет CTL/ATL/TSB и VO2
    ctl, atl, vo2_list = 0.0, 0.0, []
    hr_max = 208 - (0.7 * BIO_AGE)
    
    for a in sorted(activities, key=lambda x: x.get("start_date_local", "")):
        if a.get("type") in ["Ride", "VirtualRide"]:
            w = safe_float(a.get("average_watts"))
            tss = (a.get("moving_time", 0)/3600)*(w/FTP_GARMIN)**2*100 if w > 0 else 0
            ctl += (tss - ctl) / 42
            atl += (tss - atl) / 7
            hr = safe_float(a.get("average_heartrate"))
            if w > 0 and hr > 105:
                # Формула VO2max (проверенная)
                v = (w * 10.8 / ATHLETE_WEIGHT) + 7
                if 25 < v < 65: vo2_list.append(v)

    tsb = round(ctl - atl, 1)
    vo2_max = round(sum(vo2_list[-7:]) / len(vo2_list[-7:]), 1) if vo2_list else 35.0
    eftp = int(vo2_max * ATHLETE_WEIGHT * 0.07) # Реалистичный eFTP

    # 4. Fitness Age (Жесткий контроль)
    # HRV 100 и Пульс 44 ДОЛЖНЫ давать омоложение
    hrv_bonus = (hrv - 45) * 0.15
    rhr_bonus = (55 - rhr) * 0.3
    f_age = round(BIO_AGE - hrv_bonus - rhr_bonus + (fat-20)*0.4, 1)
    f_age = max(48.0, min(BIO_AGE + 2, f_age))

    # Запись в таблицу
    if row_idx:
        try:
            headers = sheet.row_values(1)
            if "Fitness_Age" in headers:
                sheet.update_cell(row_idx, headers.index("Fitness_Age")+1, f_age)
        except: print("⚠️ Ошибка записи Fitness_Age")

    # 5. Готовность (Ready Score)
    score = 3.0
    if hrv > 80: score += 1.0
    if rhr < 50: score += 0.5
    if tsb < -20: score -= 1.5
    if recovery_h > 24: score -= 1.0
    score = max(1.0, min(5.0, round(score, 1)))

    # 6. Формирование отчета
    icon = "🛑" if score < 2.5 else "🟡" if score < 3.8 else "🟢"
    circles = "🟢🟢🟢" if score >= 4 else "🟢🟢" if score >= 2.8 else "🟡"
    
    prompt = (
        f"Ты — Арнольд, коуч атлета 63 лет. ДАННЫЕ: HRV {hrv}, Пульс {rhr}, Сон {round(sleep_h,1)}ч, "
        f"Recovery {recovery_h}ч, TSB {tsb}. Готовность {score}/5. "
        f"ИНСТРУКЦИИ: 1. Оцени HRV {hrv} (100 - это мощь!). 2. Если Recovery > 24ч — запрети рекорды. "
        f"3. Дай план: Z1/Z2 или отдых. 4. Прокомментируй Fit Age {f_age} (если ниже 63 - похвали). "
        f"Будь краток, стиль Терминатора. Фирменная фраза в конце."
    )
    
    ai_msg = ask_expert(prompt)
    
    report = (f"🌅 *УТРЕННИЙ СТАТУС* {icon}\n"
              f"{circles} *FTP: {FTP_GARMIN} | eFTP: {eftp} ({eftp-FTP_GARMIN:+})*\n\n"
              f"❤️ Пульс: {int(rhr)} | 🌀 HRV: {int(hrv)}\n"
              f"📊 TSB: {tsb} | 🔋 *Готовность: {score}/5*\n"
              f"🧬 Fit Age: {f_age} | VO2max: {vo2_max}\n\n"
              f"🤖 *АРНИ:* \n_{ai_msg}_")

    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                  json={"chat_id": TELEGRAM_CHAT_ID, "text": report, "parse_mode": "Markdown"})

if __name__ == "__main__":
    main()
