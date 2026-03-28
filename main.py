import os, requests, json, sys, gspread
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials

# --- CONFIG ---
BIRTH_DATE = datetime(1963, 5, 15) 
FTP = 213  # Твой актуальный FTP
SPREADSHEET_ID = "1rxg5oqDXWXwHSHMmR-RbJuad8rXe2OdmCEMUMY2SBT4"

def get_bio_age():
    return (datetime.utcnow() - BIRTH_DATE).days / 365.25

def get_env(name):
    val = os.environ.get(name)
    if not val: print(f"❌ Нет переменной: {name}"); sys.exit(1)
    return val

CLIENT_ID = get_env('STRAVA_CLIENT_ID')
CLIENT_SECRET = get_env('STRAVA_CLIENT_SECRET')
REFRESH_TOKEN = get_env('STRAVA_REFRESH_TOKEN')
TELEGRAM_BOT_TOKEN = get_env('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = get_env('TELEGRAM_CHAT_ID')
GEMINI_API_KEY = get_env('GEMINI_API_KEY')
GOOGLE_CREDS_JSON = get_env('GOOGLE_CREDS')

# --- СЕРВИСНЫЕ ФУНКЦИИ ---
def safe_float(val, default=0.0):
    if val is None or str(val).strip() in ["", "Н/Д", "None"]: return default
    try:
        v = float(str(val).replace(',', '.').strip())
        return v if v >= 0 else default
    except: return default

def send_tg(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                     json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=15)
    except: pass

# --- ДАННЫЕ ---
def get_strava_data():
    try:
        res = requests.post("https://www.strava.com/oauth/token", data={
            'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
            'refresh_token': REFRESH_TOKEN, 'grant_type': 'refresh_token'
        }, timeout=15)
        token = res.json().get('access_token')
        if not token: return []
        r = requests.get("https://www.strava.com/api/v3/athlete/activities",
                         headers={"Authorization": f"Bearer {token}"}, params={"per_page": 100}, timeout=15)
        return r.json() if r.status_code == 200 else []
    except: return []

def get_morning_metrics(target_date):
    try:
        creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), 
                                                      scopes=["https://www.googleapis.com/auth/spreadsheets"])
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
        records = sheet.get_all_records()
        
        # Ищем сегодня
        for row in reversed(records):
            if target_date in str(row.get('Date', '')): return row
        
        # Если нет сегодня — ищем вчера (как в твоем утреннем коде)
        yesterday = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        for row in reversed(records):
            if yesterday in str(row.get('Date', '')):
                print("⚠️ Использую вчерашние данные")
                return row
    except Exception as e: print(f"Sheets error: {e}")
    return {}

def update_fitness_age(target_date, f_age_val):
    try:
        creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), 
                                                      scopes=["https://www.googleapis.com/auth/spreadsheets"])
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
        dates = sheet.col_values(1)
        for i, val in enumerate(dates):
            if target_date in val:
                header = sheet.row_values(1)
                if "Fitness_Age" in header:
                    sheet.update_cell(i + 1, header.index("Fitness_Age") + 1, f_age_val)
                    break
    except: pass

# --- МАТЕМАТИКА ---
def estimate_vo2max(activities, weight=88.0):
    hr_rest, hr_max = 44, 175
    vals = []
    for a in activities:
        if a.get("type") not in ["Ride", "VirtualRide"]: continue
        w = safe_float(a.get("average_watts"))
        hr = safe_float(a.get("average_heartrate"))
        s = safe_float(a.get("average_speed"))
        t = safe_float(a.get("moving_time"))

        if t < 1200: continue # Минимум 20 минут

        if w > 0 and hr > 110:
            intensity = (hr - hr_rest) / (hr_max - hr_rest)
            if intensity > 0.6:
                v = ((12.0 * w / weight) + 7) / (intensity * 0.7 + 0.3)
                if 25 < v < 65: vals.append(v)
        elif s > 2 and hr > 110: # Запасной по скорости
            v = (s * 3.6 * 0.2 + 3.5) * 1.2
            if 25 < v < 60: vals.append(v)
            
    if vals:
        recent = sorted(vals[-7:])
        return round(sum(recent[1:-1]) / len(recent[1:-1]), 1) if len(recent) > 2 else round(sum(recent)/len(recent), 1)
    return None

def get_readiness(morning, tsb=0):
    score = 3.0
    try:
        hrv = safe_float(morning.get("HRV"), 45)
        if hrv > 75: score += 0.5
        elif hrv < 50: score -= 1.0

        rhr = safe_float(morning.get("Resting_HR"), 60)
        if rhr < 50: score += 0.5
        elif rhr > 65: score -= 0.5

        bb = safe_float(morning.get("Body_Battery"), 70)
        if bb > 80: score += 0.5
        elif bb < 40: score -= 1.0

        sleep_hrs = safe_float(morning.get("Sleep_Hours"), 7)
        sleep_score = safe_float(morning.get("Sleep_Score"), 70)
        if sleep_hrs < 5.5 or sleep_score < 65: score -= 1.5

        rec_time = safe_float(morning.get("Recovery_Time"), 0)
        if rec_time > 24: score -= 1.0
        if rec_time > 36: score -= 1.0

        if tsb < -25: score -= 1.5
    except: pass
    
    score = max(0, min(5, round(score, 1)))
    icons = "🔴🔴" if score < 1.5 else "🟡🟠" if score < 2.8 else "🟢🟢" if score < 4 else "🔥🏆"
    texts = ["Критическая усталость", "Средняя готовность", "Хорошая готовность", "Отличная готовность"]
    idx = 0 if score < 1.5 else 1 if score < 2.8 else 2 if score < 4 else 3
    return score, texts[idx], icons

# --- AI ---
def ask_arnie(prompt, fallback):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=25)
        return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except: return fallback

# --- MAIN ---
def main():
    now = datetime.utcnow() + timedelta(hours=1)
    today = now.strftime("%Y-%m-%d")
    activities = get_strava_data()
    morning = get_morning_metrics(today)

    w_raw = morning.get("Weight", 88.0)
    user_weight = safe_float(w_raw, 88.0)
    if user_weight > 500: user_weight /= 10
    user_fat = safe_float(morning.get("Body_Fat"), 18.3)
    if user_fat > 100: user_fat /= 10

    vo2_val = estimate_vo2max(activities, weight=user_weight)
    
    # Расчет TSB
    ctl, atl, safe_ftp = 0, 0, (FTP if FTP > 0 else 213)
    sorted_acts = sorted(activities, key=lambda x: x.get("start_date_local", ""))
    for a in sorted_acts:
        if a.get("type") in ["Ride", "VirtualRide"]:
            ts_val = round((safe_float(a.get("moving_time"))/3600)*(safe_float(a.get("average_watts"))/safe_ftp)**2*100, 1)
            ctl += (ts_val - ctl) / 42
            atl += (ts_val - atl) / 7
    tsb = round(ctl - atl, 1)

    rhr = safe_float(morning.get("Resting_HR"), 55)
    hrv = safe_float(morning.get("HRV"), 45)
    
    # Fitness Age
    bio_age = get_bio_age()
    f_age = round(bio_age + (rhr-55)*0.4 + (user_fat-22)*0.5 - (hrv-45)*0.1 - (vo2_val-35 if vo2_val else 0)*1.5, 1)
    f_age = max(40, min(bio_age + 5, f_age))

    r_val, r_text, r_icon = get_readiness(morning, tsb)
    update_fitness_age(today, f_age)

    today_acts = [a for a in activities if a.get("start_date_local", "")[:10] == today]

    if not today_acts:
        prompt = (f"Ты Арнольд, суровый коуч. Анализ: HRV {hrv}, Пульс {rhr}, Сон {morning.get('Sleep_Hours')}ч, "
                  f"Sleep Score: {morning.get('Sleep_Score')}, Recovery: {morning.get('Recovery_Time')}ч, TSB {tsb}, Готовность {r_val}/5. "
                  f"Если Recovery > 24ч или Sleep Score < 65 - ЗАПРЕТИ рекорды. Дай план Z1/Z2. ПИШИ НА РУССКОМ.")
        ai_msg = ask_arnie(prompt, r_text).replace("_", " ").replace("*", " ")
        report = (f"🌅 *УТРЕННИЙ СТАТУС* {r_icon}\n\n❤️ Пульс: {int(rhr)} | 🌀 HRV: {int(hrv)}\n"
                  f"🔋 *Готовность: {r_val}/5*\n📊 Форма (TSB): {tsb} | VO2max: {vo2_val if vo2_val else 'н/д'}\n"
                  f"📢 {r_text}\n🧬 Fit Age: {f_age}\n\n🤖 *АРНИ:* \n_{ai_msg}_")
        send_tg(report)
    else:
        last = today_acts[-1]
        dist = round(safe_float(last.get("distance"))/1000, 2)
        int_pct = round((safe_float(last.get("average_watts"))/safe_ftp)*100, 1)
        prompt = f"Арнольд, разбор тренировки: {last.get('name')}, {dist}км, интенсивность {int_pct}% от FTP. Коротко и по делу на русском."
        ai_msg = ask_arnie(prompt, "Good job.").replace("_", " ").replace("*", " ")
        report = (f"🚲 *ТРЕНИРОВКА ЗАВЕРШЕНА*\n\n*{last.get('name')}*\n📍 {dist} км | 🔥 Интенсивность: {int_pct}%\n🧬 Fit Age: {f_age}\n\n🤖 *АРНИ:* \n_{ai_msg}_")
        send_tg(report)

if __name__ == "__main__":
    main()
