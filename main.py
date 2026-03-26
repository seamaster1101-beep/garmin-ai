import os
import requests
import json
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai  # Возвращаем старый добрый импорт
from datetime import datetime, timedelta
import sys

# ... (весь блок CONFIG и send_tg остается прежним) ...

# --- ВНУТРИ main() ИСПРАВЬ БЛОК GEMINI ---
    print("🧠 Запрос к Арнольду (Stable SDK)...")
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        prompt = f"""Ты — АРНИ, жесткий тренер. 
        Утренние замеры: {morning}
        Тренировки за день: {activities}
        Дай краткий разбор дня (до 450 симв). 
        Стиль: Арнольд Шварценеггер. Эмодзи обязательны."""

        response = model.generate_content(prompt)
        
        if response.text:
            msg = f"🏋️ *ОТЧЕТ АРНИ*\n\n{response.text}"
            send_tg(msg)
            print("🚀 ГОТОВО! Проверяй Telegram.")
    except Exception as e:
        print(f"❌ Ошибка Gemini: {e}")
