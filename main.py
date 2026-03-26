import os
import requests
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import google.generativeai as genai
from datetime import datetime, timedelta
import warnings

# Убираем предупреждение о версии библиотеки
warnings.filterwarnings("ignore", category=FutureWarning)

# --- КОНФИГУРАЦИЯ ---
STRAVA_CLIENT_ID = os.environ['STRAVA_CLIENT_ID']
STRAVA_CLIENT_SECRET = os.environ['STRAVA_CLIENT_SECRET']
STRAVA_REFRESH_TOKEN = os.environ['STRAVA_REFRESH_TOKEN']
TELEGRAM_BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
GEMINI_API_KEY = os.environ['GEMINI_API_KEY']
GOOGLE_CREDS_JSON = os.environ['GOOGLE_CREDS']

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
    data = res.json()
    
    if 'access_token' not in data:
        error_msg = f"❌ Ошибка авторизации Strava: {data.get('message', 'Unknown Error')}\nПодробности: {data.get('errors', '')}"
        raise Exception(error_msg)
        
    return data['access_token']

def get_strava_activities(access_token):
    # Берем данные за последние 48 часов, чтобы точно что-то зацепить
    after = int((datetime.now() - timedelta(days=2)).timestamp())
    url = f"https://www.strava.com/api/v3/athlete/activities?after={after}"
    headers = {'Authorization': f'Bearer {access_token}'}
    res = requests.get(url, headers=headers)
    
    if res.status_code != 200:
        return []
        
    activities = res.json()
    detailed_data = []
    
    for act in activities:
        detail_url = f"https://www.strava.com/api/v3/activities/{act['id']}"
        detail_res = requests.get(detail_url, headers=headers).json()
        detailed_data.append({
            'name': detail_res.get('name'),
            'type': detail_res.get('type'),
            'start_date': detail_res.get('start_date_local'),
            'distance': round(detail_res.get('distance', 0) / 1000, 2), # в км
            'moving_time': round(detail_res.get('moving_time', 0) / 60, 1), # в мин
            'avg_watts': detail_res.get('average_watts', 0),
            'max_watts': detail_res.get('max_watts', 0),
            'avg_cadence': detail_res.get('average_cadence', 0),
            'avg_hr': detail_res.get('average_heartrate', 0)
        })
    return detailed_data

# ... (остальные функции get_morning_data и send_telegram остаются прежними)
