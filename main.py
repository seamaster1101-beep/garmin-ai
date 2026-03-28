import os, requests, json, sys
from datetime import datetime, timedelta

# --- 1. CONFIG ---
BIRTH_DATE = datetime(1963, 5, 15) 
FTP = 213  # Твой актуальный FTP
SPREADSHEET_ID = "1rxg5oqDXWXwHSHMmR-RbJuad8rXe2OdmCEMUMY2SBT4"

def get_bio_age():
    """Динамический расчет возраста на момент запуска"""
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

# --- 2. СЕРВИСНЫЕ ФУНКЦИИ ---
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

# --- 3. ДАННЫЕ ---
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
        import gspread
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), 
                                                      scopes=["https://www.googleapis.com/auth/spreadsheets"])
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
        records = sheet.get_all_records()
        for row in reversed(records):
            if target_date in str(row.get('Date', '')): return row
    except: pass
    return {}

def update_fitness_age(target_date, f_age_val):
    try:
        import gspread
        from google.oauth2.service_account import Credentials
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

# --- 4. МАТЕМАТИКА ---
def estimate_vo2max(activities, weight=88.0):
    HR_MAX = 175
    vals = []
    # Сортировка для корректного среза последних активностей
    acts = sorted(activities, key=lambda x: x.get("start_date_local", ""))
    if weight < 50 or weight > 150: weight = 88.0

    for a in acts:
        if a.get("type") not in ["Ride", "VirtualRide"]: continue
        w = safe_float(a.get("average_watts"), 0)
        hr = safe_float(a.get("average_heartrate"), 0)
        t = safe_float(a.get("moving_time"), 0)
        
        # FIX: Фильтр по времени (минимум 20 минут) для точности VO2
        if w > 0 and hr > 100 and t > 1200:
            try:
                ratio = min(HR_MAX / hr, 1.35)
                v = (10.51 * (w * ratio) / weight) + 7
                if 20 < v < 75: vals.append(v)
            except: continue
    
    if vals:
        recent = vals[-min(len(vals), 7):]
        if len(recent) > 4: recent = sorted(recent)[1:-1]
        return round(sum(recent) / len(recent), 1)
    return None

def fitness_age(rhr, hrv, vo2, fat):
    bio_age = get_bio_age()
    vo2_calc = vo2 if vo2 is not None else 35.0
    
    rhr_diff = (rhr - 55) * 0.4
    fat_diff = (fat - 22) * 0.5
    hrv_bonus = (hrv - 45) * 0.1
    vo2_bonus = (vo2_calc - 35) * 1.5
    
    res = bio_age + rhr_diff + fat_diff - hrv_bonus - vo2_bonus
    # Ограничение: не моложе 40 и не старше BIO_AGE + 5
    return round(max(40, min(bio_age + 5, res)), 1)

# --- 5. MAIN ---
def main():
    now = datetime.utcnow() + timedelta(hours=1)
    today = now.strftime("%Y-%m-%d")
    
    activities = get_strava_data()
    morning = get_morning_metrics(today)

    # 1. Данные из таблицы
    rhr = safe_float(morning.get("Resting_HR"), 60)
    hrv = safe_float(morning.get("HRV"), 45)
    weight = safe_float(morning.get("Weight"), 88.0)
    if weight > 500: weight /= 10
    fat = safe_float(morning.get("Body_Fat"), 18.3)
    if fat > 100: fat /= 10

    # 2. Расчеты
    vo2_val = estimate_vo2max(activities, weight=weight)
    f_age = fitness_age(rhr, hrv, vo2_val, fat=fat)
    
    # 3. TSB и Интенсивность
    ctl, atl = 0, 0
    sorted_acts = sorted(activities, key=lambda x: x.get("start_date_local", ""))
    
    # FIX: Безопасный FTP для исключения деления на 0
    safe_ftp = FTP if FTP > 0 else 200

    for a in sorted_acts:
        if a.get("type") in ["Ride", "VirtualRide"]:
            w = safe_float(a.get("average_watts"), 0)
            t = safe_float(a.get("moving_time"), 0)
            tss = round((t/3600)*(w/safe_ftp)**2*100, 1) if w > 0 else 0
            ctl += (tss - ctl) / 42
            atl += (tss - atl) / 7

    tsb = round(ctl - atl, 1)
    
    # FIX: Интенсивность строго последней тренировки
    last_act = sorted_acts[-1] if sorted_acts else None
    last_intensity = 0
    if last_act:
        w_last = safe_float(last_act.get("average_watts"), 0)
        last_intensity = round((w_last / safe_ftp) * 100, 1) if w_last > 0 else 0

    # --- TOTAL DEBUG ---
    print(f"--- TOTAL DEBUG ---")
    print(f"BIO_AGE: {round(get_bio_age(), 2)} | FitAge: {f_age} | Weight: {weight}")
    print(f"VO2: {vo2_val if vo2_val else 'NO DATA'} | TSB: {tsb} | Last Intensity: {last_intensity}%")
    print(f"-------------------")

    update_fitness_age(today, f_age)

    # 4. ОТЧЕТЫ
    today_acts = [a for a in activities if a.get("start_date_local", "")[:10] == today]
    
    if not today_acts:
        # Утренний Арнольд
        prompt = f"Арнольд, утренний статус: HRV {hrv}, Пульс {rhr}, Fit Age {f_age}, TSB {tsb}. Будь краток. НА РУССКОМ."
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        try:
            res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
            ai_msg = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        except: ai_msg = "Вставай, чемпион!"
        
        report = f"🌅 *УТРЕННИЙ СТАТУС*\n\n❤️ Пульс: {int(rhr)} | 🌀 HRV: {int(hrv)}\n📊 TSB: {tsb} | 🧬 Fit Age: {f_age}\n\n🤖 *АРНИ:* \n_{ai_msg}_"
        send_tg(report)
    else:
        # Тренировочный отчет
        last = today_acts[-1]
        dist = round(safe_float(last.get("distance", 0)) / 1000, 2)
        w_avg = safe_float(last.get("average_watts"), 0)
        int_pct = round((w_avg / safe_ftp) * 100, 1) if w_avg > 0 else 0
        
        report = f"🚲 *ТРЕНИРОВКА ЗАВЕРШЕНА*\n\n*{last.get('name')}*\n📍 {dist} км | 🔥 Интенсивность: {int_pct}% от FTP\n🧬 Fit Age: {f_age}"
        send_tg(report)

if __name__ == "__main__":
    main()
