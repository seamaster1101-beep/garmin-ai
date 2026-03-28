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

# Переменные окружения
CLIENT_ID = get_env('STRAVA_CLIENT_ID')
CLIENT_SECRET = get_env('STRAVA_CLIENT_SECRET')
REFRESH_TOKEN = get_env('STRAVA_REFRESH_TOKEN')
TELEGRAM_BOT_TOKEN = get_env('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = get_env('TELEGRAM_CHAT_ID')
GEMINI_API_KEY = get_env('GEMINI_API_KEY')
GOOGLE_CREDS_JSON = get_env('GOOGLE_CREDS')

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
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
        
        if res.status_code == 429:
            print("⚠️ Gemini rate limit (429)"); return fallback
        if res.status_code != 200:
            print(f"⚠️ Gemini error {res.status_code}: {res.text}"); return fallback

        data = res.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        if not parts: 
            print("⚠️ AI returned empty candidates"); return fallback

        ans = parts[0]["text"].strip().replace("_", " ").replace("*", " ")
        print(f"✅ [AI] Ответ получен ({len(ans)} симв.)")
        return ans
    except Exception as e:
        print(f"⚠️ AI Exception: {e}"); return fallback

# --- РАБОТА С ДАННЫМИ ---
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
                    print(f"✅ FitAge {f_age_val} записан в Sheets.")
                    break
    except Exception as e: print(f"⚠️ Sheet update error: {e}")

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

# --- MAIN ---
def main():
    now = datetime.utcnow() + timedelta(hours=1)
    today = now.strftime("%Y-%m-%d")
    
    # 1. Strava Safe
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

    # 2. Google Sheets Safe
    client = get_google_client()
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
    records = sheet.get_all_records()
    morning = next((row for row in reversed(records) if today in str(row.get('Date', ''))), 
                   (records[-1] if records else {}))

    rhr = safe_float(morning.get("Resting_HR"), 60)
    hrv = safe_float(morning.get("HRV"), 45)
    weight = safe_float(morning.get("Weight"), 88.0)
    if weight > 500: weight /= 10
    fat = safe_float(morning.get("Body_Fat"), 18.3)
    if fat > 100: fat /= 10
    sleep = safe_float(morning.get("Sleep_Hours"), 7.0)
    if sleep > 24: sleep /= 10

    # 3. Calculations & Forecast
    vo2_val, eftp_val = estimate_performance(activities, weight=weight)
    
    ctl, atl = 0, 0
    for a in sorted(activities, key=lambda x: x.get("start_date_local", "")):
        if a.get("type") in ["Ride", "VirtualRide"]:
            w = safe_float(a.get("average_watts"), 0)
            t = a.get("moving_time", 0)
            tss = (t/3600)*(w/FTP_GARMIN)**2*100 if w > 0 and FTP_GARMIN > 0 else 0
            ctl += (tss - ctl) / 42
            atl += (tss - atl) / 7
    tsb = round(ctl - atl, 1)
    tsb_tomorrow =
