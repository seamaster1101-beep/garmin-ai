import os
import requests
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import google.generativeai as genai
from datetime import datetime, timedelta
import sys

# --- CONFIG ---
def get_env(name):
    val = os.environ.get(name)
    if not val:
        print(f"❌ Нет переменной {name}")
        sys.exit(1)
    return val

CLIENT_ID = get_env('STRAVA_CLIENT_ID')
CLIENT_SECRET = get_env('STRAVA_CLIENT_SECRET')
REFRESH_TOKEN = get_env('STRAVA_REFRESH_TOKEN')
TELEGRAM_BOT_TOKEN = get_env('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = get_env('TELEGRAM_CHAT_ID')
GEMINI_API_KEY = get_env('GEMINI_API_KEY')
GOOGLE_CREDS_JSON = get_env('GOOGLE_CREDS')

# --- TELEGRAM ---
def send_tg(msg):
    if len(msg) > 4000: msg = msg[:4000] + "..."
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception as e: print(f"TG Error: {e}")

# --- STRAVA AUTH (БРОНЕБОЙНЫЙ) ---
def get_strava_access():
    print("🔄 Авторизация в Strava...")
    try:
        res = requests.post("https://www.strava.com/oauth/token", data={
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'refresh_token': REFRESH_TOKEN,
            'grant_type': 'refresh_token'
        }, timeout=15)
        data = res.json()
        
        if res.status_code != 200:
            print(f"❌ Ошибка Strava: {data}")
            sys.exit(1)

        # Проверка обновления рефреш-токена
        new_refresh = data.get('refresh_token')
        if new_refresh and new_refresh != REFRESH_TOKEN:
            print(f"\n⚠️ СРОЧНО! НОВЫЙ REFRESH_TOKEN: {new_refresh}\n")
            
        return data["access_token"]
    except Exception as e:
        print(f"Critical Auth Error: {e}")
        sys.exit(1)

# --- АНАЛИТИКА МОЩНОСТИ ---
def get_power_zones(token, activity_id, ftp=250):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        res = requests.get(f"https://www.strava.com/api/v3/activities/{activity_id}/streams",
                          headers=headers, params={"keys": "watts", "key_by_type": "true"}, timeout=10)
        stream = res.json().get("watts", {}).get("data", [])
        if not stream: return None
        
        zones = [0,0,0,0,0] # Z1-Z5
        for w in stream:
            if w < 0.55*ftp: zones[0]+=1
            elif w < 0.75*ftp: zones[1]+=1
            elif w < 0.90*ftp: zones[2]+=1
            elif w < 1.05*ftp: zones[3]+=1
            else: zones[4]+=1
        total = sum(zones) or 1
        return [round(z/total*100) for z in zones]
    except: return None

# --- СБОР ДАННЫХ ---
def collect_strava_data(token):
    headers = {"Authorization": f"Bearer {token}"}
    res = requests.get("https://www.strava.com/api/v3/athlete/activities", 
                      headers=headers, params={"per_page": 5}, timeout=15)
    activities = res.json() if res.status_code == 200 else []
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    today_data, yesterday_data = [], []

    for a in activities:
        a_id = a['id']
        # Доп. детали (watts, cadence)
        det = requests.get(f"https://www.strava.com/api/v3/activities/{a_id}", headers=headers).json()
        
        info = {
            "name": a.get("name"),
            "dist": round(a.get("distance",0)/1000, 2),
            "time": round(a.get("moving_time",0)/60, 1),
            "hr": a.get("average_heartrate"),
            "pwr": det.get("average_watts"),
            "load": det.get("suffer_score", 0),
            "zones": get_power_zones(token, a_id)
        }
        
        date = a.get("start_date_local", "")
        if today_str in date: today_data.append(info)
        elif yesterday_str in date: yesterday_data.append(info)
            
    return today_data, yesterday_data

# --- GOOGLE SHEETS ---
def get_morning_stats():
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(GOOGLE_CREDS_JSON), scope)
        client = gspread.authorize(creds)
        sheet = client.open("ArniData").worksheet("Morning")
        return sheet.get_all_records()[-1]
    except Exception as e:
        print(f"Sheets error: {e}")
        return {}

# --- MAIN ---
def main():
    print("🚀 Запуск АРНИ v2.5 Гибрид")
    
    token = get_strava_access()
    today, yesterday = collect_strava_data(token)
    morning = get_morning_stats()
    
    if not today and not morning:
        print("📭 Данных нет.")
        return

    # Расчет Score (интенсивность)
    load_today = sum(a['load'] for a in today)
    load_yesterday = sum(a['load'] for a in yesterday)
    score
