import os
import json
import requests
from datetime import datetime
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

# ПРОВЕРКА НАЛИЧИЯ СЕКРЕТОВ (выведется в логи GitHub)
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

print(f"STATUS: TG_TOKEN exists: {bool(TG_TOKEN)}, TG_ID: {TG_ID}")

try:
    # 1. GARMIN
    gar = Garmin(os.environ.get("GARMIN_EMAIL"), os.environ.get("GARMIN_PASSWORD"))
    gar.login()
    today = datetime.now().strftime("%Y-%m-%d")
    stats = gar.get_stats(today) or {}
    hrv = stats.get("allDayAvgHrv") or stats.get("lastNightAvgHrv") or "N/A"
    print(f"STATUS: Garmin Data - HRV: {hrv}")

    # 2. GEMINI AI
    advice = "ИИ вредничает"
    if GEMINI_KEY:
        try:
            genai.configure(api_key=GEMINI_KEY)
            model = genai.GenerativeModel('gemini-1.5-flash')
            res = model.generate_content(f"У меня HRV {hrv}. Дай совет из 3 слов.")
            advice = res.text.strip()
            print(f"STATUS: AI Advice: {advice}")
        except Exception as ai_e:
            print(f"STATUS: AI Error: {ai_e}")

    # 3. GOOGLE SHEETS
    creds = json.loads(os.environ.get("GOOGLE_CREDS"))
    c_obj = Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    ss.worksheet("AI_Log").append_row([datetime.now().strftime("%H:%M"), "OK", advice])
    print("STATUS: Google Sheets Updated")

    # 4. TELEGRAM (самый важный блок)
    if TG_TOKEN and TG_ID:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        payload = {"chat_id": TG_ID, "text": f"📊 HRV: {hrv}\n🤖 {advice}"}
        r = requests.post(url, json=payload, timeout=15)
        
        print(f"STATUS: Telegram Response: {r.status_code}, Body: {r.text}")
        
        if r.status_code != 200:
            ss.worksheet("AI_Log").append_row([datetime.now().strftime("%H:%M"), "TG_FAIL", r.text])

except Exception as e:
    print(f"CRITICAL ERROR: {e}")
