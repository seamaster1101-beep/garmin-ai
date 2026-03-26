import os
import requests
import json
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai
from datetime import datetime, timedelta
import sys

def get_env(name):
    val = os.environ.get(name)
    if not val:
        print(f"❌ Ошибка: {name} не найден")
        sys.exit(1)
    return val

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

def main():
    print("🚀 СТАРТ АРНИ v2.6")
    
    # 1. Strava
    print("🔐 Strava Auth...")
    res = requests.post("https://www.strava.com/oauth/token", data={
        'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
        'refresh_token': REFRESH_TOKEN, 'grant_type': 'refresh_token'
    })
    token = res.json().get('access_token')
    
    activities = []
    if token:
        after = int((datetime.now() - timedelta(days=1)).timestamp())
        r = requests.get("https://www.strava.com/api/v3/athlete/activities", 
                        headers={"Authorization": f"Bearer {token}"}, params={"after": after})
        activities = r.json() if r.status_code == 200 else []
        print(f"✅ Strava ok. Тренировок: {len(activities)}")

    # 2. Google Sheets
    print("📊 Чтение Google Sheets...")
    morning = {}
    try:
        # Новый метод авторизации
        info = json.loads(GOOGLE_CREDS_JSON)
        creds = Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        client = gspread.authorize(creds)
        
        sheet = client.open("ArniData").worksheet("Morning")
        all_data = sheet.get_all_records()
        
        today_str = datetime.now().strftime("%Y-%m-%d")
        print(f"Ищем записи за: {today_str}")
        
        for row in reversed(all_data):
            row_date = str(row.get('Date', ''))
            if today_str in row_date:
                morning = row
                print(f"✅ Нашел данные в таблице: {morning}")
                break
    except Exception as e:
        print(f"❌ Ошибка Sheets: {str(e)}")

    if not activities and not morning:
        print("😴 Глухо. Ни в Strava, ни в Таблице ничего нового.")
        return

    # 3. Gemini
    print("🧠 Запрос к Арнольду...")
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        prompt = f"Ты АРНИ. Прокомментируй эти данные. Утро: {morning}. Тренировки: {activities}. Будь краток и суров."
        response = model.generate_content(prompt)
        
        msg = f"🏋️ *ОТЧЕТ АРНИ*\n\n{response.text}"
        send_tg(msg)
        print("🚀 ГОТОВО! Проверяй Telegram.")
    except Exception as e:
        print(f"❌ Ошибка ИИ: {e}")

if __name__ == "__main__":
    main()
