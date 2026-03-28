import os, requests, json, sys, gspread
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials

# --- CONFIG ---
# Динамический расчет фактического возраста
BIRTH_DATE = datetime(1963, 5, 15) 

def get_bio_age():
    return (datetime.utcnow() - BIRTH_DATE).days / 365.25

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

FTP = 213 # Твой актуальный FTP

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def safe_float(val, default=0.0):
    if val is None or str(val).strip() in ["", "Н/Д", "None"]:
        return default
    try:
        v = float(str(val).replace(',', '.').strip())
        return v if v > 0 else default
    except:
        return default

def send_tg(msg):
    if len(msg) > 4000: msg = msg[:3900]
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                     json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=15)
    except Exception as e: print(f"❌ TG Error: {e}")

# --- AI ---
def ask_arnie(prompt, fallback):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
        data = res.json()
        if "candidates" in data and data["candidates"]:
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except: pass
    return fallback

# --- РАСЧЕТЫ ---
def estimate_vo2max(activities, weight=88.0):
    acts = sorted(activities, key=lambda x: x.get("start_date_local", ""))
    vals = []
    HR_MAX = 175
    for a in acts:
        if a.get("type") not in ["Ride", "VirtualRide"]: continue
        w = a.get("average_watts")
        hr = a.get("average_heartrate")
        if w and hr and float(hr) > 100:
            try:
                w_f, hr_f = float(w), float(hr)
                v = (10.51 * (w_f * (HR_MAX / hr_f)) / weight) + 7
                if 20 < v < 65: vals.append(v)
            except: continue
    if vals:
        recent = vals[-min(len(vals), 7):]
        return round(sum(recent) / len(recent), 1)
    return None

def fitness_age(rhr, hrv, vo2=None, fat=18.3):
    bio_age = get_bio_age()
    try:
        rhr_diff = (rhr - 55) * 0.4
        fat_diff = (fat - 22) * 0.5
        hrv_bonus = (hrv - 45) * 0.1
        vo2_bonus = (vo2 - 35) * 1.5 if vo2 is not None else 0
        res = bio_age + rhr_diff + fat_diff - hrv_bonus - vo2_bonus
        return round(max(40, min(bio_age + 5, res)), 1)
    except: return round(bio_age, 1)

# --- GOOGLE & STRAVA ---
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
        for row in reversed(records):
            if target_date in str(row.get('Date', '')): return row
        return {}
    except: return {}

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

# --- MAIN ---
def main():
    now = datetime.utcnow() + timedelta(hours=1)
    today = now.strftime("%Y-%m-%d")
    activities = get_strava_data()
    morning = get_morning_metrics(today)

    rhr = safe_float(morning.get("Resting_HR"), 60)
    hrv = safe_float(morning.get("HRV"), 45)
    weight = safe_float(morning.get("Weight"), 88.0)
    if weight > 500: weight /= 10
    fat = safe_float(morning.get("Body_Fat"), 18.3)
    if fat > 100: fat /= 10
    
    sleep_raw = safe_float(morning.get("Sleep_Hours"), 0)
    sleep = sleep_raw / 10 if sleep_raw > 24 else sleep_raw

    vo2_val = estimate_vo2max(activities, weight=weight)
    f_age = fitness_age(rhr, hrv, vo2_val, fat=fat)
    
    # Форма (TSB)
    ctl, atl, safe_ftp = 0, 0, (FTP if FTP > 0 else 250)
    for a in sorted(activities, key=lambda x: x.get("start_date_local", "")):
        if a.get("type") in ["Ride", "VirtualRide"]:
            w = a.get("average_watts")
            t = a.get("moving_time", 0)
            tss = round((t/3600)*(float(w)/safe_ftp)**2*100, 1) if w else 0
            ctl += (tss - ctl) / 42
            atl += (tss - atl) / 7
    tsb = round(ctl - atl, 1)

    print(f"--- TOTAL DEBUG ---")
    print(f"Weight: {weight} | Fat: {fat} | Sleep: {sleep}")
    print(f"VO2: {vo2_val} | FitAge: {f_age} | RHR: {rhr} | HRV: {hrv}")
    print(f"-------------------")

    update_fitness_age(today, f_age)

    today_acts = [a for a in activities if a.get("start_date_local", "")[:10] == today]
    
    if not today_acts:
        # УТРЕННИЙ ОТЧЕТ
        prompt = (f"Ты Арнольд, элитный коуч. HRV {hrv}, Пульс {rhr}, Сон {sleep}ч, Fit Age {f_age}, TSB {tsb}. "
                  f"Дай краткий план на день. ПИШИ НА РУССКОМ.")
        ai_msg = ask_arnie(prompt, "Вставай, чемпион!").replace("_", " ").replace("*", " ")
        report = f"🌅 *УТРЕННИЙ СТАТУС*\n\n❤️ Пульс: {int(rhr)} | 🌀 HRV: {int(hrv)}\n📊 TSB: {tsb} | 🧬 Fit Age: {f_age}\n\n🤖 *АРНИ:* \n_{ai_msg}_"
        send_tg(report)
    else:
        # ОТЧЕТ ПО ТРЕНИРОВКЕ
        last = today_acts[-1]
        dist = round(last.get("distance", 0) / 1000, 2)
        prompt = f"Арнольд, оцени тренировку: {dist}км, TSB {tsb}. Будь краток, на русском."
        ai_msg = ask_arnie(prompt, "Хорошая работа.").replace("_", " ").replace("*", " ")
        report = f"🚲 *ТРЕНИРОВКА ЗАВЕРШЕНА*\n\n*{last.get('name')}*\n📍 {dist} км | 🧬 Fit Age: {f_age}\n\n🤖 *АРНИ:* \n_{ai_msg}_"
        send_tg(report)

if __name__ == "__main__":
    main()
