import os
import json
import requests
from datetime import datetime
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

# 1. Секреты (строго по вашим названиям в GitHub)
email = os.environ.get("GARMIN_EMAIL")
password = os.environ.get("GARMIN_PASSWORD")
gemini_key = os.environ.get("GEMINI_API_KEY")
creds_json = os.environ.get("GOOGLE_CREDS")
tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
tg_id = os.environ.get("TELEGRAM_CHAT_ID")

def run_main():
    hrv, slp_h, bb, advice = "N/A", "N/A", "N/A", "Нет анализа"

    try:
        # 2. Garmin: Сбор данных
        gar = Garmin(email, password)
        gar.login()
        today = datetime.now().strftime("%Y-%m-%d")
        
        stats = gar.get_stats(today) or {}
        hrv = stats.get("lastNightAvgHrv") or stats.get("allDayAvgHrv") or "N/A"
        summary = gar.get_user_summary(today) or {}
        bb = summary.get("bodyBatteryHighestValue") or "N/A"
        
        slp = gar.get_sleep_data(today)
        if slp and slp.get("dailySleepDTO"):
            slp_h = round(slp["dailySleepDTO"].get("sleepTimeSeconds", 0) / 3600, 1)

        # 3. AI: Анализ (тот метод, что работал в самом начале)
        if gemini_key:
            try:
                genai.configure(api_key=gemini_key.strip())
                model = genai.GenerativeModel('gemini-1.5-flash')
                response = model.generate_content(f"HRV {hrv}, сон {slp_h}ч, BB {bb}. Дай совет в 5 слов.")
                advice = response.text.strip()
            except Exception as ai_err:
                advice = f"Ошибка ИИ: {str(ai_err)[:20]}"

        # 4. Google Sheets: Запись
        if creds_json:
            c_dict = json.loads(creds_json)
            scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
            creds = Credentials.from_service_account_info(c_dict, scopes=scopes)
            sheet = gspread.authorize(creds).open("Garmin_Data").worksheet("AI_Log")
            sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "OK", advice])

        # 5. Telegram: Только отправка текста
        if tg_token and tg_id:
            msg = f"🚀 Отчет:\nHRV: {hrv}\nСон: {slp_h}ч\nBB: {bb}\n\n🤖 {advice}"
            # Убираем все лишнее, просто шлем текст по URL
            url = f"https://api.telegram.org/bot{tg_token.strip()}/sendMessage"
            requests.post(url, json={"chat_id": tg_id.strip(), "text": msg}, timeout=10)

    except Exception as e:
        print(f"Ошибка: {e}")

if __name__ == "__main__":
    run_main()
