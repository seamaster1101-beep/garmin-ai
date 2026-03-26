import os
import requests
import json
from datetime import datetime, timedelta
import sys

# --- CONFIG ---
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

# --- TELEGRAM ---
def send_tg(msg):
    if len(msg) > 3900:
        msg = msg[:3900] + "\n\n...(обрезано)"
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=15
        )
    except Exception as e:
        print(f"TG Error: {e}")

# --- STRAVA ---
def get_strava_token():
    res = requests.post("https://www.strava.com/oauth/token", data={
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'refresh_token': REFRESH_TOKEN,
        'grant_type': 'refresh_token'
    }, timeout=15)
    data = res.json()
    if res.status_code != 200:
        raise Exception(f"Strava Auth Error: {data}")
    return data['access_token']

def get_activities(token, days=7):
    after = int((datetime.now() - timedelta(days=days)).timestamp())
    res = requests.get(
        "https://www.strava.com/api/v3/athlete/activities",
        headers={"Authorization": f"Bearer {token}"},
        params={"after": after, "per_page": 50},
        timeout=15
    )
    return res.json() if res.status_code == 200 else []

# --- GOOGLE SHEETS ---
def get_morning_data():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_info(
            json.loads(GOOGLE_CREDS_JSON),
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly", "https://www.googleapis.com/auth/drive.readonly"]
        )
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
        records = sheet.get_all_records()
        today = datetime.now().strftime("%Y-%m-%d")
        for row in reversed(records):
            if today in str(row.get('Date', '')):
                return row
    except Exception as e:
        print(f"Sheets Error: {e}")
    return {}

# --- ANALYTICS ---
def calc_load(activity):
    hr = activity.get('average_heartrate', 120)
    duration_min = activity.get('moving_time', 0) / 60
    return (hr * duration_min) / 100

def calc_training_metrics(activities):
    loads = [calc_load(a) for a in activities]
    atl = sum(loads[-7:]) / 7 if loads else 0
    ctl = sum(loads) / 42 if len(loads) > 10 else sum(loads)/max(1, len(loads))
    tsb = ctl - atl
    return round(ctl,1), round(atl,1), round(tsb,1)

def get_power_zone(watts):
    if not watts: return "N/A"
    if watts < 150: return "Z1-Z2"
    if watts < 220: return "Z3"
    if watts < 300: return "Z4"
    return "Z5+"

def calc_fitness_score(morning, tsb):
    score = 60
    rhr = morning.get('Resting_HR')
    hrv = morning.get('HRV')
    if rhr: score -= max(0, (rhr - 45)) * 2
    if hrv: score += (hrv - 70) * 0.5
    score += tsb * 0.5
    return max(5, min(100, int(score)))

# --- AI (BULLETPROOF VERSION) ---
def ask_ai(prompt):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        res = requests.post(url, json=payload, timeout=20)
        
        if res.status_code != 200:
            return f"Ошибка API ({res.status_code}). Контроль TSB."
        
        data = res.json()
        text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        
        if not text: return "ИИ временно безмолвен."
        return text[:800] + "..." if len(text) > 800 else text
    except Exception as e:
        return f"Сбой связи с ИИ: {e}"

# --- MAIN ---
def main():
    print("🚀 ARNI SYSTEM v3.5 ONLINE")
    
    # Data Gathering
    try:
        token = get_strava_token()
        activities = get_activities(token, 42) # Берем больше для CTL
    except Exception as e:
        print(f"Strava error: {e}"); activities = []

    today_str = datetime.now().strftime("%Y-%m-%d")
    today_acts = [a for a in activities if today_str in a.get('start_date_local', '')]
    morning = get_morning_data()

    # Calculations
    ctl, atl, tsb = calc_training_metrics(activities)
    fitness_score = calc_fitness_score(morning, tsb)
    status = "Восстановление" if tsb > 10 else "Прогресс" if tsb > -10 else "ПЕРЕГРУЗ ⚠️"

    act_text = ""
    for a in today_acts:
        dist = round(a.get('distance', 0)/1000, 2)
        zone = get_power_zone(a.get('average_watts'))
        act_text += f"• {a.get('name')}: {dist} км | {zone}\n"
    if not act_text: act_text = "Тренировок не зафиксировано."

    # AI Prompt
    prompt = f"Ты АРНИ, суровый тренер. Утро: HR={morning.get('Resting_HR')}, HRV={morning.get('HRV')}. " \
             f"Метрики: CTL={ctl}, ATL={atl}, TSB={tsb}. Fitness={fitness_score}. Сегодня: {act_text}. " \
             f"Дай краткий едкий разбор в стиле Шварценеггера и 1 совет. До 400 знаков."

    ai_text = ask_ai(prompt)

    # Report Construction
    report = (
        f"🏋️ *ARNI INTELLIGENCE REPORT*\n\n"
        f"🔥 *Fitness Score:* {fitness_score}/100\n"
        f"📈 CTL: {ctl} | ATL: {atl} | TSB: {tsb}\n"
        f"🚦 Статус: *{status}*\n\n"
        f"🏃 *Сегодня:*\n{act_text}\n"
        f"🤖 *АРНИ:* \n_{ai_text}_"
    )

    send_tg(report)
    print("✅ REPORT SENT")

if __name__ == "__main__":
    main()
