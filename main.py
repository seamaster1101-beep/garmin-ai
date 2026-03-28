import os, requests, json, sys, gspread
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials

# --- CONFIG ---
# Считаем возраст автоматически на основе даты рождения
BIRTH_DATE = datetime(1963, 5, 29)
def get_bio_age():
    return (datetime.utcnow() - BIRTH_DATE).days / 365.25

# Текущий FTP (меняй здесь, когда Garmin обновит)
FTP = 213 

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

# --- СЛУЖЕБНЫЕ ФУНКЦИИ ---
def send_tg(msg):
    if len(msg) > 4000: msg = msg[:3900]
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                     json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=15)
    except Exception as e: print(f"❌ TG Error: {e}")

def ask_arnie(prompt, fallback):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
        if res.status_code != 200:
            print(f"⚠️ AI Error {res.status_code}: {res.text}")
            return fallback
        return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except: return fallback

# --- ДАННЫЕ (STRAVA / SHEETS) ---
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
        # Ищем за сегодня, если нет — берем последнюю строку
        for row in reversed(records):
            if target_date in str(row.get('Date', '')): return row, records[-2].get('FTP', FTP)
        return records[-1], records[-2].get('FTP', FTP)
    except: return {}, FTP

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
                if "FTP" in header:
                    sheet.update_cell(i + 1, header.index("FTP") + 1, FTP)
                break
    except: pass

# --- РАСЧЕТЫ ---
def estimate_vo2max(activities, weight=88.0):
    vals = []
    HR_MAX = 175
    for a in sorted(activities, key=lambda x: x.get("start_date_local", "")):
        if a.get("type") not in ["Ride", "VirtualRide"]: continue
        w, hr = a.get("average_watts"), a.get("average_heartrate")
        if w and hr and float(hr) > 110:
            try:
                v = (10.51 * (float(w) * (HR_MAX / float(hr))) / weight) + 7
                if 20 < v < 65: vals.append(v)
            except: continue
    return round(sum(vals[-7:]) / len(vals[-7:]), 1) if vals else None

def calc_fitness_age(rhr, hrv, vo2, fat):
    bio_age = get_bio_age()
    try:
        rhr_diff = (float(rhr) - 55) * 0.4
        fat_diff = (float(fat) - 22) * 0.5
        hrv_bonus = (float(hrv) - 45) * 0.1
        vo2_bonus = (float(vo2) - 35) * 1.5 if vo2 else 0
        res = bio_age + rhr_diff + fat_diff - hrv_bonus - vo2_bonus
        return round(max(40, min(bio_age + 5, res)), 1)
    except: return round(bio_age, 1)

# --- MAIN ---
def main():
    now = datetime.utcnow() + timedelta(hours=1)
    today = now.strftime("%Y-%m-%d")
    
    activities = get_strava_data()
    morning, prev_ftp = get_morning_metrics(today)

    # Чистим данные веса и сна (твоя логика)
    w_raw = morning.get("Weight", 88.0)
    weight = float(str(w_raw).replace(',', '.'))
    if weight > 500: weight /= 10

    f_raw = morning.get("Body_Fat", 18.3)
    fat = float(str(f_raw).replace(',', '.'))
    if fat > 100: fat /= 10

    s_raw = morning.get("Sleep_Hours", 0)
    sleep = float(str(s_raw).replace(',', '.'))
    if sleep > 24: sleep /= 10

    rhr = morning.get("Resting_HR", 60)
    hrv = morning.get("HRV", 45)

    # Считаем метрики
    vo2_val = estimate_vo2max(activities, weight=weight)
    f_age = calc_fitness_age(rhr, hrv, vo2_val, fat)
    
    # TSB (Форма)
    ctl, atl = 0, 0
    for a in sorted(activities, key=lambda x: x.get("start_date_local", "")):
        if a.get("type") in ["Ride", "VirtualRide"]:
            w = a.get("average_watts", 0)
            t = a.get("moving_time", 0)
            tss = (t/3600)*(w/FTP)**2*100 if w else 0
            ctl += (tss - ctl) / 42
            atl += (tss - atl) / 7
    tsb = round(ctl - atl, 1)

    # Готовность (0-5)
    r_score = round((min(5, hrv/20) + (5 if tsb > 0 else max(0, 5 + (tsb/10)))) / 2, 1)
    r_icon = "🔥" if r_score >= 4 else "🟢" if r_score >= 2.8 else "🟡"
    r_text = "Отличная форма" if r_score >= 4 else "В строю" if r_score >= 2.8 else "Нужен отдых"

    # FTP Тренд
    ftp_diff = int(FTP - prev_ftp)
    ftp_trend = f"📈 FTP: {FTP} (+{ftp_diff})" if ftp_diff > 0 else f"📉 FTP: {FTP} ({ftp_diff})" if ftp_diff < 0 else f"↔️ FTP: {FTP}"

    update_fitness_age(today, f_age)

    # ОТЧЕТ
    today_acts = [a for a in activities if a.get("start_date_local", "")[:10] == today]
    if not today_acts:
        prompt = f"Арнольд, статус: HRV {hrv}, Пульс {rhr}, Сон {sleep}ч, Fit Age {f_age}, TSB {tsb}. Будь краток и суров."
        ai_msg = ask_arnie(prompt, r_text)
        report = (f"🌅 *УТРЕННИЙ СТАТУС* {r_icon}\n🏆 *{ftp_trend}*\n\n❤️ Пульс: {rhr} | 🌀 HRV: {hrv}\n"
                  f"🔋 *Готовность: {r_score}/5*\n📊 Форма (TSB): {tsb} | VO2max: {vo2_val}\n"
                  f"📢 {r_text}\n🧬 Fit Age: {f_age}\n\n🤖 *АРНИ:* \n_{ai_msg}_")
    else:
        last = today_acts[-1]
        dist = round(last.get("distance", 0) / 1000, 2)
        report = f"🏃 *ТРЕНИРОВКА*\n🏆 *{ftp_trend}*\n\n*{last.get('name')}*\n📍 {dist} км | 🧬 Fit Age: {f_age}\n📊 TSB: {tsb}"
        
    send_tg(report)

if __name__ == "__main__":
    main()
