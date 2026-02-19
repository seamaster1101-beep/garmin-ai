import os
import json
import requests
from datetime import datetime
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials

# 1. СБОР СЕКРЕТОВ (сверх-проверка)
raw_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_TOKEN = raw_token.strip()
TG_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

print(f"DEBUG: Token Length: {len(TG_TOKEN)}") # Должно быть 46 символов
print(f"DEBUG: ID: {TG_ID}")

try:
    # 2. GARMIN
    gar = Garmin(os.environ.get("GARMIN_EMAIL"), os.environ.get("GARMIN_PASSWORD"))
    gar.login()
    today = datetime.now().strftime("%Y-%m-%d")
    stats = gar.get_stats(today) or {}
    hrv = stats.get("lastNightAvgHrv") or "N/A"
    
    # 3. GEMINI (Прямой запрос без библиотек)
    advice = "ИИ вредничает"
    if GEMINI_KEY:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
            payload = {"contents": [{"parts":[{"text": f"HRV {hrv}. Дай совет из 3 слов."}]}]}
            res = requests.post(url, json=payload, timeout=10).json()
            advice = res['candidates'][0]['content']['parts'][0]['text'].strip()
        except: pass

    # 4. GOOGLE SHEETS
    creds_json = os.environ.get("GOOGLE_CREDS")
    if creds_json:
        creds_dict = json.loads(creds_json)
        c_obj = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
        ss = gspread.authorize(c_obj).open("Garmin_Data")
        ss.worksheet("AI_Log").append_row([datetime.now().strftime("%H:%M"), "Success", advice])

    # 5. ТЕЛЕГРАМ
    if len(TG_TOKEN) > 10 and TG_ID:
        msg = f"🚀 ОТЧЕТ\nHRV: {hrv}\n🤖 {advice}"
        t_url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        r = requests.post(t_url, json={"chat_id": TG_ID, "text": msg}, timeout=15)
        print(f"DEBUG: TG Status: {r.status_code}, Response: {r.text}")
    else:
        print("CRITICAL: TG_TOKEN is EMPTY or TOO SHORT!")

except Exception as e:
    print(f"ERROR: {e}")
