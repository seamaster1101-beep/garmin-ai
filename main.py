import os
import requests
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import google.generativeai as genai
from datetime import datetime, timedelta
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)

# --- КОНФИГУРАЦИЯ ---
try:
    STRAVA_CLIENT_ID = os.environ['STRAVA_CLIENT_ID']
    STRAVA_CLIENT_SECRET = os.environ['STRAVA_CLIENT_SECRET']
    STRAVA_REFRESH_TOKEN = os.environ['STRAVA_REFRESH_TOKEN']
    TELEGRAM_BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
    TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
    GEMINI_API_KEY = os.environ['GEMINI_API_KEY']
    GOOGLE_CREDS_JSON = os.environ['GOOGLE_CREDS']
except KeyError as e:
    print(f"❌ Ошибка: Отсутствует секрет {e}")
    exit(1)

# --- ФУНКЦИИ ---

def get_strava_token():
    print("Step 1: Авторизация в Strava...")
    url = "https://www.strava.com/oauth/token"
    payload = {
        'client_id': STRAVA_CLIENT_ID,
        'client_secret': STRAVA_CLIENT_SECRET,
        'refresh_token': STRAVA_REFRESH_TOKEN,
        'grant_type': 'refresh_token'
    }
    res = requests.post(url, data=payload)
    data = res.json()
    if 'access_token' not in data:
        raise Exception(f"Strava Auth Failed: {data}")
    print("✅ Токен Strava получен.")
    return data['access_token']

def get_strava_activities(access_token):
    print("Step 2: Загрузка тренировок...")
    after = int((datetime.now() - timedelta(days=2)).timestamp())
    url = f"https://www.strava.com/api/v3/athlete/activities?after={after}"
    headers = {'Authorization': f'Bearer {access_token}'}
    res = requests.get(url, headers=headers)
    activities = res.json()
    
    detailed = []
    for act in activities:
        d_url = f"https://www.strava.com/api/v3/activities/{act['id']}"
        d_res = requests.get(d_url, headers=headers).json()
        detailed.append({
            'name': d_res.get('name'),
            'type': d_res.get('type'),
            'watts': d_res.get('average_watts', 0),
            'cadence': d_res.get('average_cadence', 0),
            'hr': d_res.get('average_heartrate', 0)
        })
    print(f"✅ Найдено тренировок: {len(detailed)}")
    return detailed

def get_morning_data():
    print("Step 3: Чтение Google Sheets...")
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    # ВНИМАНИЕ: Проверь, что имя таблицы в Кавычках совпадает с реальностью!
    try:
        sheet = client.open("ArniData").worksheet("Morning") 
        data = sheet.get_all_records()
        print("✅ Данные из таблицы получены.")
        return data[-1] if data else {}
    except Exception as e:
        print(f"❌ Ошибка Google Sheets: {e}")
        return {"error": str(e)}

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"})

# --- MAIN ---

try:
    s_token = get_strava_token()
    s_activities = get_strava_activities(s_token)
    m_data = get_morning_data()

    print("Step 4: Запрос к Gemini...")
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    prompt = f"Ты АРНИ, ИИ-тренер. Проанализируй:\nУтро: {m_data}\nТренировки: {s_activities}\nДай короткий совет."
    
    response = model.generate_content(prompt)
    print("✅ Ответ от Gemini получен.")
    
    send_telegram(response.text)
    print("🚀 Отчет отправлен в Telegram!")

except Exception as e:
    error_text = f"❌ Сбой системы АРНИ:\n{str(e)}"
    print(error_text)
    send_telegram(error_text)
