import os
import requests
import json
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

# Функция для получения Access Token Google без gspread
def get_google_token(creds):
    print("🔑 Получение токена Google...")
    url = "https://oauth2.googleapis.com/token"
    header = {"alg": "RS256", "typ": "JWT"}
    # Упрощенная авторизация через requests для надежности
    import time
    import jwt # Должен быть в системе
    iat = int(time.time())
    exp = iat + 3600
    payload = {
        "iss": creds['client_email'],
        "scope": "https://www.googleapis.com/auth/spreadsheets.readonly",
        "aud": url,
        "exp": exp,
        "iat": iat
    }
    signed_jwt = jwt.encode(payload, creds['private_key'], algorithm='RS256')
    res = requests.post(url, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": signed_jwt})
    return res.json().get('access_token')

def main():
    print("🚀 СТАРТ АРНИ v3.0 (Direct)")
    
    # 1. Strava
    print("🔐 Strava Auth...")
    res_s = requests.post("https://www.strava.com/oauth/token", data={
        'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
        'refresh_token': REFRESH_TOKEN, 'grant_type': 'refresh_token'
    })
    s_token = res_s.json().get('access_token')
    
    activities = []
    if s_token:
        after = int((datetime.now() - timedelta(days=1)).timestamp())
        r = requests.get("https://www.strava.com/api/v3/athlete/activities", 
                        headers={"Authorization": f"Bearer {s_token}"}, params={"after": after})
        activities = r.json()
        print(f"✅ Strava ok. Тренировок: {len(activities)}")

    # 2. Google Sheets (Через прямой API запрос)
    print("📊 Чтение Google Sheets (Direct API)...")
    morning = {}
    try:
        creds = json.loads(GOOGLE_CREDS_JSON)
        # Мы используем gspread только для парсинга, но авторизацию берем в свои руки
        import gspread
        from google.oauth2.service_account import Credentials
        
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        g_creds = Credentials.from_service_account_info(creds, scopes=scopes)
        client = gspread.authorize(g_creds)
        
        # Попытка открыть по ID (ID берем из URL твоей таблицы)
        # URL: https://docs.google.com/spreadsheets/d/1X.../edit
        # Если не сработает по имени "ArniData", попробуем открыть напрямую
        sheet = client.open("ArniData").worksheet("Morning")
        data = sheet.get_all_records()
        
        today_str = datetime.now().strftime("%Y-%m-%d")
        for row in reversed(data):
            if today_str in str(row.get('Date', '')):
                morning = row
                print(f"✅ Утро найдено!")
                break
    except Exception as e:
        print(f"❌ Ошибка Sheets: {e}")

    if not activities and not morning:
        print("😴 Данных нет.")
        return

    # 3. Gemini
    print("🧠 Запрос к Арнольду...")
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"Ты АРНИ. Разбери данные за сегодня. Утро: {morning}. Тренировки: {activities}. Будь краток и суров. Эмодзи!"
        response = model.generate_content(prompt)
        
        if response.text:
            send_tg(f"🏋️ *ОТЧЕТ АРНИ*\n\n{response.text}")
            print("🚀 ГОТОВО!")
    except Exception as e:
        print(f"❌ Ошибка Gemini: {e}")

if __name__ == "__main__":
    main()
