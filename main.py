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
        if res.status_code != 200: print(f"⚠️ TG Error: {res.text}")
    except Exception as e: print(f"❌ TG Exception: {e}")

def ask_expert(prompt, fallback):
    try:
        print(f"--- [AI DEBUG] PROMPT SENT ---\n{prompt}\n------------------------------")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
        if res.status_code != 200: return fallback
        data = res.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        if not parts: return fallback
        ans = parts[0]["text"].strip().replace("_", " ").replace("*", " ")
        print(f"✅ [AI] Ответ получен ({len(ans)} симв.)")
        return ans
    except Exception as e:
        print(f"⚠️ AI Exception: {e}"); return fallback

def get_google_client():
    creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), 
                                                  scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return gspread.authorize(creds)

def update_fitness_age_in_sheet(target_date, f_age_val):
    try:
        client = get_google_client()
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
        dates = sheet.col_values(1)
        for i, val in enumerate(dates):
            if target_date in val:
                header = sheet.row_values(1)
                if "Fitness_Age" in header:
                    sheet.update_cell(i + 1, header.index("Fitness_Age") + 1, f_age_val)
                    print(f"✅ FitAge {f_age_val} записан.")
                    break
    except: pass

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
        if token:
            r = requests.get("https://www.strava.com/api/v3/athlete/activities",
                             headers={"Authorization": f"Bearer {token}"}, params={"per_page": 100}, timeout=15)
            data = r.json()
            activities = data if isinstance(data, list) else []
    except: pass

    client = get_google_client()
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
    records = sheet.get_all_records()
    morning = next((row for row in reversed(records) if today in str(row.get('Date', ''))), 
                   (records[-1] if records else {}))

    rhr, hrv = safe_float(morning.get("Resting_HR"), 60), safe_float(morning.get("HRV"), 45)
    weight, fat = safe_float(morning.get("Weight"), 88.0), safe_float(morning.get("Body_Fat"), 18.3)
    if weight > 500: weight /= 10
    if fat > 100: fat /= 10
    sleep = safe_float(morning.get("Sleep_Hours"), 7.0)
    if sleep > 24: sleep /= 10

    vo2_val, eftp_val = estimate_performance(activities, weight=weight)
    
    ctl, atl = 0.0, 0.0
    for a in sorted(activities, key=lambda x: x.get("start_date_local", "")):
        if a.get("type") in ["Ride", "VirtualRide"]:
            w = safe_float(a.get("average_watts"), 0)
            tss = (a.get("moving_time", 0)/3600)*(w/FTP_GARMIN)**2*100 if w > 0 and FTP_GARMIN > 0 else 0
            ctl += (tss - ctl) / 42
            atl += (tss - atl) / 7
            
    tsb = round(ctl - atl, 1)
    tsb_tomorrow = round((ctl * (1 - 1/42)) - (atl * (1 - 1/7)), 1)

    score = 2.5
    if hrv > 75: score += 1.0
    elif hrv < 45: score -= 1.0
    if rhr < 50: score += 0.5
    elif rhr > 60: score -= 0.5
    if sleep >= 7.5: score += 1.0
    elif sleep < 6.0: score -= 1.0
    if tsb < -25: score -= 2.0
    elif -25 <= tsb <= -10: score += 0.5
    score = max(0, min(5, round(score, 1)))
    
    icon = "🔥🏆" if score >= 4 else "🟢🟢" if score >= 2.8 else "🟡"
    circles = "🟢🟢🟢" if score >= 4 else "🟢🟢" if score >= 2.8 else "🟡"

    vo2_calc = vo2_val if vo2_val is not None else 35
    f_age = round(get_bio_age() + (rhr-55)*0.4 + (fat-22)*0.5 - (hrv-45)*0.1 - (vo2_calc-35)*1.5, 1)
    f_age = max(45.0, min(get_bio_age() + 2, f_age))
    update_fitness_age_in_sheet(today, f_age)

    eftp_diff = eftp_val - FTP_GARMIN if eftp_val else 0
    eftp_str = f" | eFTP: {eftp_val} ({eftp_diff:+})" if eftp_val else ""
    header_ftp = f"{circles} *FTP: {FTP_GARMIN}{eftp_str}*"
    
    today_acts = [a for a in activities if a.get("start_date_local", "")[:10] == today]
    
    if not today_acts:
        prompt = (f"Ты — опытный спортивный наставник. Атлет (63 года): HRV {int(hrv)} (норма 30, у него 100), Пульс {int(rhr)}, TSB {tsb}. "
                  f"Завтра TSB {tsb_tomorrow}. Сравни с ровесниками. Краткий совет + цитата Арнольда в конце. На русском.")
        ai_msg = ask_expert(prompt, "Тренер на связи. Показатели элитные.")
        report = (f"🌅 *УТРЕННИЙ СТАТУС* {icon}\n{header_ftp}\n\n"
                  f"❤️ Пульс: {int(rhr)} | 🌀 HRV: {int(hrv)}\n📊 TSB: {tsb} (завтра: {tsb_tomorrow})\n"
                  f"🔋 *Готовность: {score}/5*\n🧬 Fit Age: {f_age} | VO2max: {vo2_val if vo2_val else 'н/д'}\n\n"
                  f"🤖 *ТРЕНЕР:* \n_{ai_msg}_")
    else:
        last = today_acts[-1]
        dist = round(last.get("distance", 0) / 1000, 2)
        prompt = (f"Ты — спортивный тренер. Разбери велотренировку атлета 63 лет: {dist}км, TSB {tsb}, eFTP {eftp_val}. "
                  f"Сравни с ровесниками. Хвали за успехи. Цитата Арнольда в конце. На русском.")
        ai_msg = ask_expert(prompt, "Тренировка зафиксирована. Продолжаем!")
        report = (f"🏃 *ТРЕНИРОВКА* {icon}\n{header_ftp}\n\n"
                  f"*{last.get('name')}*\n📍 {dist} км | 🧬 Fit Age: {f_age}\n"
                  f"📊 TSB: {tsb}\n\n🤖 *ТРЕНЕР:* \n_{ai_msg}_")

    send_tg(report)

if __name__ == "__main__":
    main()
