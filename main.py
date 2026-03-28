import os, requests, json, sys, gspread
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials

# --- CONFIG ---
BIRTH_DATE = datetime(1963, 5, 29)
FTP = 213  # Твой актуальный FTP из Garmin

def get_bio_age():
    return (datetime.utcnow() - BIRTH_DATE).days / 365.25

def get_env(name):
    val = os.environ.get(name)
    if not val:
        print(f"❌ Нет переменной: {name}"); sys.exit(1)
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
    except: pass

def ask_arnie(prompt, fallback):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
        print(f"DEBUG AI: Status Code = {res.status_code}")
        if res.status_code != 200:
            print(f"⚠️ AI Error: {res.text}")
            return fallback
        return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"❌ AI Exception: {e}")
        return fallback

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
        for row in reversed(records):
            if target_date in str(row.get('Date', '')): return row, records[-2].get('FTP', FTP)
        return records[-1], records[-2].get('FTP', FTP)
    except: return {}, FTP

# --- РАСЧЕТЫ ---
def estimate_vo2max(activities, weight=88.0):
    vals = []
    for a in sorted(activities, key=lambda x: x.get("start_date_local", "")):
        w, hr = a.get("average_watts"), a.get("average_heartrate")
        if w and hr and float(hr) > 110:
            v = (10.51 * (float(w) * (175 / float(hr))) / weight) + 7
            if 20 < v < 65: vals.append(v)
    return round(sum(vals[-7:]) / len(vals[-7:]), 1) if vals else None

def fitness_age(rhr, hrv, vo2, fat):
    bio_age = get_bio_age()
    try:
        rhr_i = (float(rhr) - 55) * 0.4
        fat_i = (float(fat) - 22) * 0.5
        hrv_i = (float(hrv) - 45) * 0.1
        vo2_i = (float(vo2) - 40) * 0.5 if vo2 else 0
        res = bio_age + rhr_i + fat_i - hrv_i - vo2_i
        return round(max(45, min(bio_age + 2, res)), 1)
    except: return round(bio_age, 1)

def get_readiness(morning, tsb=0):
    score = 2.5
    hrv = morning.get("HRV", 45)
    if hrv != "Н/Д":
        v = int(hrv)
        if v > 75: score += 1.0
        elif v < 45: score -= 1.0
    rhr = morning.get("Resting_HR", 60)
    if rhr != "Н/Д":
        v = int(rhr)
        if v < 50: score += 0.5
        elif v > 60: score -= 0.5
    s_raw = morning.get("Sleep_Hours", 0)
    sleep = float(str(s_raw).replace(',', '.'))
    if sleep > 24: sleep /= 10
    if sleep >= 7.5: score += 1.0
    elif sleep < 6.0: score -= 1.0
    
    if tsb < -25: score -= 2.0
    elif -25 <= tsb <= -10: score += 0.5
    
    score = max(0, min(5, round(score, 1)))
    icon = "🔥🏆" if score >= 4 else "🟢🟢" if score >= 2.8 else "🟡"
    text = "Отличная форма" if score >= 4 else "В строю" if score >= 2.8 else "Нужен отдых"
    return score, text, icon

# --- MAIN ---
def main():
    now = datetime.utcnow() + timedelta(hours=1)
    today = now.strftime("%Y-%m-%d")
    activities = get_strava_data()
    morning, prev_ftp = get_morning_metrics(today)

    weight = float(str(morning.get("Weight", 88.0)).replace(',', '.')); 
    if weight > 500: weight /= 10
    fat = float(str(morning.get("Body_Fat", 18.3)).replace(',', '.')); 
    if fat > 100: fat /= 10

    vo2_val = estimate_vo2max(activities, weight=weight)
    
    ctl, atl = 0, 0
    for a in sorted(activities, key=lambda x: x.get("start_date_local", "")):
        if a.get("type") in ["Ride", "VirtualRide"]:
            w = a.get("average_watts", 0)
            t = a.get("moving_time", 0)
            tss = (t/3600)*(w/FTP)**2*100 if w else 0
            ctl += (tss - ctl) / 42
            atl += (tss - atl) / 7
    tsb = round(ctl - atl, 1)

    r_val, r_text, r_icon = get_readiness(morning, tsb=tsb)
    f_age = fitness_age(morning.get("Resting_HR", 60), morning.get("HRV", 45), vo2_val, fat)
    
    ftp_diff = int(FTP - prev_ftp)
    ftp_trend = f"📈 FTP: {FTP} (+{ftp_diff})" if ftp_diff > 0 else f"📉 FTP: {FTP} ({ftp_diff})" if ftp_diff < 0 else f"↔️ FTP: {FTP}"

    today_acts = [a for a in activities if a.get("start_date_local", "")[:10] == today]
    
    if not today_acts:
        # ВОТ ТОТ САМЫЙ КРУТОЙ ПРОМПТ ИЗ 28.03
        prompt = (f"Ты — легендарный Арнольд. Дай анализ состояния. ПИШИ НА РУССКОМ. "
                  f"Данные: HRV {morning.get('HRV')}, Пульс {morning.get('Resting_HR')}, Fit Age {f_age}, TSB {tsb}. "
                  f"Готовность: {r_val}/5. Интерпретируй цифры, дай совет по Z-зонам на сегодня. Будь краток.")
        ai_msg = ask_arnie(prompt, r_text)
        report = (f"🌅 *УТРЕННИЙ СТАТУС* {r_icon}\n🏆 *{ftp_trend}*\n\n❤️ Пульс: {morning.get('Resting_HR')} | 🌀 HRV: {morning.get('HRV')}\n"
                  f"🔋 *Готовность: {r_val}/5*\n📊 Форма (TSB): {tsb} | VO2max: {vo2_val if vo2_val else 'н/д'}\n"
                  f"🧬 Fit Age: {f_age}\n\n🤖 *АРНИ:* \n_{ai_msg}_")
    else:
        last = today_acts[-1]
        dist = round(last.get("distance", 0) / 1000, 2)
        prompt = f"Арнольд, разбери тренировку {dist}км, TSS {round((last.get('moving_time',0)/3600)*(last.get('average_watts',0)/FTP)**2*100,1)}. ПИШИ НА РУССКОМ."
        ai_msg = ask_arnie(prompt, "Работа сделана!")
        report = f"🏃 *ТРЕНИРОВКА*\n🏆 *{ftp_trend}*\n\n*{last.get('name')}*\n📍 {dist} км | 🧬 Fit Age: {f_age}\n📊 TSB: {tsb}\n\n🤖 *АРНИ:* \n_{ai_msg}_"

    send_tg(report)

if __name__ == "__main__":
    main()
