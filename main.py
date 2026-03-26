import os
import requests
import json
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai
from datetime import datetime, timedelta
import sys

# --- CONFIG ---
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
    print("🚀 СТАРТ АРНИ v2.9")
    
    # 1. Strava Auth
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
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client_gs = gspread.authorize(creds)
        
        sheet = client_gs.open("ArniData").worksheet("Morning")
        all_rows = sheet.get_all_records()
        
        today_str = datetime.now().strftime("%Y-%m-%d")
        print(f"🔍 Ищем дату: {today_str}")
        
        for row in reversed(all_rows):
            if today_str in str(row.get('Date', '')):
                morning = row
                print(f"✅ Утро найдено!")
                break
    except Exception as e:
        print(f"❌ Ошибка Sheets: {e}")

    if not activities and not morning:
        print("😴 Данных нет. Сообщение не отправляем.")
        return

    # 3. Gemini (Исправленный вызов)
    print("🧠 Запрос к Арнольду...")
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        # Явное указание версии модели для обхода 404
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        prompt = f"Ты АРНИ. Разбери данные. Утро: {morning}. Тренировки: {activities}. Будь краток и суров как Терминатор. Эмодзи!"
        
        # Пробуем получить ответ
        response = model.generate_content(prompt)
        
        if response and response.text:
            msg = f"🏋️ *ОТЧЕТ АРНИ*\n\n{response.text}"
            send_tg(msg)
            print("🚀 ПОБЕДА! Сообщение ушло.")
        else:
            print("⚠️ ИИ вернул пустой результат.")
            
    except Exception as e:
        print(f"❌ Ошибка Gemini: {e}")

if __name__ == "__main__":
    main()
