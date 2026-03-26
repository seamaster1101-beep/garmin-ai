import os
import requests
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import google.generativeai as genai
from datetime import datetime, timedelta

# --- КОНФИГУРАЦИЯ ---
STRAVA_CLIENT_ID = os.environ['STRAVA_CLIENT_ID']
STRAVA_CLIENT_SECRET = os.environ['STRAVA_CLIENT_SECRET']
STRAVA_REFRESH_TOKEN = os.environ['STRAVA_REFRESH_TOKEN']
TELEGRAM_BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
GEMINI_API_KEY = os.environ['GEMINI_API_KEY']
GOOGLE_CREDS_JSON = os.environ['GOOGLE_CREDS'] # Твои креды Google Sheets

# --- ФУНКЦИИ ---

def get_strava_token():
    url = "https://www.strava.com/oauth/token"
    payload = {
        'client_id': STRAVA_CLIENT_ID,
        'client_secret': STRAVA_CLIENT_SECRET,
        'refresh_token': STRAVA_REFRESH_TOKEN,
        'grant_type': 'refresh_token'
    }
    res = requests.post(url, data=payload)
    return res.json()['access_token']

def get_strava_activities(access_token):
    after = int((datetime.now() - timedelta(days=1)).timestamp())
    url = f"https://www.strava.com/api/v3/athlete/activities?after={after}"
    headers = {'Authorization': f'Bearer {access_token}'}
    res = requests.get(url, headers=headers)
    activities = res.json()
    
    detailed_data = []
    for act in activities:
        # Берем детальные данные (там есть ватты и каденс)
        detail_url = f"https://www.strava.com/api/v3/activities/{act['id']}"
        detail_res = requests.get(detail_url, headers=headers).json()
        detailed_data.append({
            'name': detail_res.get('name'),
            'type': detail_res.get('type'),
            'distance': detail_res.get('distance'),
            'moving_time': detail_res.get('moving_time'),
            'avg_watts': detail_res.get('average_watts', 0),
            'max_watts': detail_res.get('max_watts', 0),
            'avg_cadence': detail_res.get('average_cadence', 0),
            'avg_hr': detail_res.get('average_heartrate', 0)
        })
    return detailed_data

def get_morning_data():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    sheet = client.open("ArniData").worksheet("Morning") # Проверь название таблицы!
    data = sheet.get_all_records()
    return data[-1] if data else {}

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

# --- ОСНОВНОЙ ЛОГИК ---

try:
    # 1. Сбор данных
    token = get_strava_token()
    activities = get_strava_activities(token)
    morning = get_morning_data()

    # 2. Подготовка промпта для АРНИ
    prompt = f"""
    Ты - АРНИ, суровый и профессиональный ИИ-тренер. 
    Проанализируй данные атлета и дай короткий, мощный отчет.
    
    УТРЕННИЕ ДАННЫЕ (из Google Sheets):
    {json.dumps(morning, indent=2, ensure_ascii=False)}
    
    ТРЕНИРОВКИ ЗА 24 ЧАСА (из Strava):
    {json.dumps(activities, indent=2, ensure_ascii=False)}
    
    Инструкция:
    1. Если есть тренировка с ваттами - разбери её (эффективность, интенсивность).
    2. Сопоставь утреннее состояние (сон, HRV) с нагрузкой.
    3. Дай один четкий совет на сегодня. Тон: подбадривающий, но строгий.
    """

    # 3. Запрос к Gemini
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
    response = model.generate_content(prompt)
    
    # 4. Отправка
    send_telegram(response.text)
    print("Отчет успешно отправлен!")

except Exception as e:
    send_telegram(f"❌ Ошибка АРНИ: {str(e)}")
    print(f"Ошибка: {e}")
