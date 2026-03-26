import os
import requests
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import google.generativeai as genai
from datetime import datetime, timedelta
import sys

def get_env(name):
    val = os.environ.get(name)
    if not val:
        print(f"❌ Ошибка: {name} не найден")
        sys.exit(1)
    return val

# Настройки
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
                     json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def main():
    print("🚀 СТАРТ ОТЛАДКИ АРНИ")
    
    # 1. Strava
    print("🔐 Авторизация Strava...")
    res = requests.post("https://www.strava.com/oauth/token", data={
        'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
        'refresh_token': REFRESH_TOKEN, 'grant_type': 'refresh_token'
    })
    token = res.json().get('access_token')
    
    activities = []
    if token:
        print("🏃‍♂️ Поиск тренировок...")
        after = int((datetime.now() - timedelta(days=1)).timestamp())
        r = requests.get("https://www.strava.com/api/v3/athlete/activities", 
                        headers={"Authorization": f"Bearer {token}"}, params={"after": after})
        activities = r.json()
        print(f"Найдено тренировок: {len(activities)}")
    
    # 2. Google Sheets
    print("📊 Чтение Google Sheets...")
    morning = {}
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(GOOGLE_CREDS_JSON), scope)
        client = gspread.authorize(creds)
        sheet = client.open("ArniData").worksheet("Morning")
        all_data = sheet.get_all_records()
        
        today_str = datetime.now().strftime("%Y-%m-%d")
        print(f"Ищем дату: {today_str}")
        
        for row in reversed(all_data):
            # Проверяем, начинается ли колонка Date с нужной даты
            if str(row.get('Date', '')).startswith(today_str):
                morning = row
                print(f"✅ Данные утра найдены: {morning}")
                break
    except Exception as e:
        print(f"❌ Ошибка Sheets: {e}")

    if not activities and not morning:
        print("😴 Данных за сегодня пока нет ни в Strava, ни в Таблице.")
        return

    # 3. AI
    print("🧠 Запрос к Gemini...")
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    prompt = f"Ты АРНИ. Разбери день. Утро: {morning}, Тренировки: {activities}. Будь краток."
    
    try:
        response = model.generate_content(prompt)
        send_tg(f"🏋️ *ОТЧЕТ АРНИ*\n\n{response.text}")
        print("✅ Готово! Сообщение отправлено.")
    except Exception as e:
        print(f"❌ Ошибка ИИ: {e}")

if __name__ == "__main__":
    main()
