import os
import requests
import json
from datetime import datetime, timedelta
import sys

# --- CONFIG ---
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

def send_tg(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                     json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=15)
    except: pass

# --- DATA PROVIDERS ---
def get_strava_data():
    # Auth
    res = requests.post("https://www.strava.com/oauth/token", data={
        'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
        'refresh_token': REFRESH_TOKEN, 'grant_type': 'refresh_token'
    }, timeout=15)
    token = res.json().get('access_token')
    
    # Fetch
    r = requests.get("https://www.strava.com/api/v3/athlete/activities",
                    headers={"Authorization": f"Bearer {token}"}, params={"per_page": 50}, timeout=15)
    return r.json() if r.status_code == 200 else []

def get_morning_metrics(target_date):
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON),
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
        records = sheet.get_all_records()
        for row in reversed(records):
            if target_date in str(row.get('Date', '')): return row
    except: pass
    return {}

def ask_arnie(prompt):
    # Стабильный URL v1 (Строка 58)
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=30)
        if res.status_code == 429:
            return "Арнольд отдыхает. Лимит запросов (429)."
        data = res.json()
        if 'candidates' in data and data['candidates']:
            return data['candidates'][0]['content']['parts'][0]['text']
        return f"АРНИ молчит (Код: {res.status_code})"
    except Exception as e:
        return f"Ошибка связи: {str(e)}"

# --- MAIN ---
def main():
    # 1. Время (Мальта UTC+1)
    malta_now = datetime.utcnow() + timedelta(hours=1)
    today_str = malta_now.strftime("%Y-%m-%d")
    print(f"🚀 ARNI v4.0. Сегодня: {today_str}")

    # 2. Сбор данных
    activities = get_strava_data()
    morning = get_morning_metrics(today_str)

    # 3. Фильтрация строго за текущую дату
    today_acts = []
    for a in activities:
        # Берем дату начала (ГГГГ-ММ-ДД) и сравниваем напрямую
        if a.get('start_date_local', '')[:10] == today_str:
            today_acts.append(a)

    # 4. Аналитика
    act_text = ""
    for a in today_acts:
        dist = round(a.get('distance', 0)/1000, 2)
        act_text += f"• {a.get('name')}: {dist} км\n"
    
    if not act_text: act_text = "Тренировок сегодня еще не было."

    # Упрощенный расчет Fitness Score (на базе твоих последних данных)
    rhr = morning.get('Resting_HR', 50)
    hrv = morning.get('HRV', 70)
    fitness_score = max(10, min(100, int(70 - (rhr - 45)*2 + (hrv - 70)*0.5)))

    # 5. ИИ Анализ
    prompt = f"Ты АРНИ, суровый тренер. Проанализируй: Пульс {rhr}, HRV {hrv}. Тренировки: {act_text}. Дай жесткий разбор до 350 знаков."
    arnie_speech = ask_arnie(prompt)

    # 6. Финальный отчет
    report = (
        f"🏋️ *ARNI INTELLIGENCE REPORT*\n\n"
        f"🔥 *Fitness Score:* {fitness_score}/100\n"
        f"📊 *Утро:* ❤️ {rhr} | 🌀 {hrv}\n\n"
        f"🏃 *Активность:* \n{act_text}\n"
        f"🤖 *АРНИ:* \n_{arnie_speech}_"
    )
    
    send_tg(report)
    print("✅ Отчет отправлен.")

if __name__ == "__main__":
    main()
