import os
import requests
import json
from datetime import datetime, timedelta
import sys

# --- CONFIG ---
BIO_AGE = 63  # твой возраст

def get_env(name):
    val = os.environ.get(name)
    if not val:
        print(f"❌ Нет переменной: {name}")
        sys.exit(1)
    return val

SPREADSHEET_ID = "1rxg5oqDXWXwHSHMmR-RbJuad8rXe2OdmCEMUMY2SBT4"

CLIENT_ID = get_env('STRAVA_CLIENT_ID')
CLIENT_SECRET = get_env('STRAVA_CLIENT_SECRET')
REFRESH_TOKEN = get_env('STRAVA_REFRESH_TOKEN')
TELEGRAM_BOT_TOKEN = get_env('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = get_env('TELEGRAM_CHAT_ID')
GEMINI_API_KEY = get_env('GEMINI_API_KEY')
GOOGLE_CREDS_JSON = get_env('GOOGLE_CREDS')

FTP = 250

# --- TELEGRAM ---
def send_tg(msg):
    if len(msg) > 4000:
        msg = msg[:3900]
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=15
        )
    except:
        pass

# --- STRAVA ---
def get_strava_data():
    try:
        res = requests.post("https://www.strava.com/oauth/token", data={
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'refresh_token': REFRESH_TOKEN,
            'grant_type': 'refresh_token'
        }, timeout=15)

        token = res.json().get('access_token')
        if not token:
            return []

        r = requests.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 100},
            timeout=15
        )

        data = r.json()
        return data if r.status_code == 200 and isinstance(data, list) else []

    except Exception as e:
        print("Strava error:", e)
        return []

# --- GOOGLE SHEETS ---
def get_morning_metrics(target_date):
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_info(
            json.loads(GOOGLE_CREDS_JSON),
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
        )

        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
        records = sheet.get_all_records()

        for row in reversed(records):
            if target_date in str(row.get('Date', '')):
                return row

        yesterday = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        for row in reversed(records):
            if yesterday in str(row.get('Date', '')):
                print("⚠️ Использую вчерашние данные")
                return row

    except Exception as e:
        print("Sheets error:", e)

    return {}

# --- TSS ---
def calc_tss(a):
    w = a.get("average_watts")
    t = a.get("moving_time", 0)
    if not w:
        return 0
    return round((t/3600)*(w/FTP)**2*100,1)

# --- VO2 ---
def estimate_vo2max(activities):
    vals = []
    for a in activities:
        s = a.get("average_speed")
        hr = a.get("average_heartrate")
        if s and hr and 2 < s < 8 and 80 < hr < 180:
            vals.append((s*3.6)*0.2 + 3.5)
    if len(vals) >= 3:
        v = round(sum(vals)/len(vals),1)
        return v if v >= 20 else None
    return None

# --- FITNESS AGE ---
def fitness_age(rhr, hrv, fat=18.3): # Жир 18.3 из твоего лога S2
    try:
        actual_age = 63 
        # 1. Влияние пульса покоя (RHR)
        rhr_val = int(rhr) if rhr and rhr != "Н/Д" else 60
        rhr_impact = (rhr_val - 55) * 0.4  
        
        # 2. Влияние жира (Body Fat) - атлетический уровень
        fat_val = float(fat)
        fat_impact = (fat_val - 22) * 0.5  
        
        # 3. Влияние HRV
        hrv_val = int(hrv) if hrv and hrv != "Н/Д" else 45
        hrv_impact = (hrv_val - 45) * 0.1  
        
        # Итоговый расчет
        calculated = actual_age + rhr_impact + fat_impact - hrv_impact
        
        # Ограничиваем разумными пределами, как было в старом коде
        return round(max(48, min(actual_age + 2, calculated)), 1)
    except:
        return 63

# --- AI ---
def ask_arnie(prompt, fallback_text):
    try:
        # 1. Сначала узнаем, какие модели сейчас живы
        res_m = requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}", 
            timeout=10
        )
        models_data = res_m.json()
        available = [
            m["name"] for m in models_data.get("models", []) 
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
        
        if not available:
            return fallback_text
            
        # 2. Выбираем Flash (она быстрее и стабильнее)
        target_model = next((m for m in available if "flash" in m), available[0])
        
        # 3. Делаем сам запрос
        url = f"https://generativelanguage.googleapis.com/v1beta/{target_model}:generateContent?key={GEMINI_API_KEY}"
        res_ai = requests.post(
            url, 
            json={"contents": [{"parts": [{"text": prompt}]}]}, 
            timeout=30
        )
        
        data = res_ai.json()
        if "candidates" in data and data["candidates"]:
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
            
    except Exception as e:
        print(f"AI Error: {e}")
        
    return fallback_text

# --- READINESS CALCULATION ---
def get_readiness(morning):
    score = 0
    try:
        # 1. HRV (Колонка F)
        hrv = morning.get("HRV")
        if hrv and hrv != "Н/Д":
            val = int(hrv)
            if val > 80: score += 2  # У тебя отличные показатели HRV, поднял планку
            elif val < 50: score -= 2

        # 2. Пульс покоя (Колонка E)
        rhr = morning.get("Resting_HR")
        if rhr and rhr != "Н/Д":
            val = int(rhr)
            if val < 46: score += 1
            elif val > 55: score -= 1

        # 3. Сон (Колонка I)
        slp = morning.get("Sleep_Hours")
        if slp:
            # Заменяем запятую на точку, если она есть (для Google Sheets)
            val = float(str(slp).replace(',', '.'))
            if val >= 7: score += 1
            elif val < 6: score -= 1

        # 4. Body Battery (Колонка G)
        bb = morning.get("Body_Battery")
        if bb:
            val = int(bb)
            if val > 85: score += 1
            elif val < 60: score -= 1

        # Итоговый расчет (базово 2 балла + бонусы/штрафы)
        final_score = max(0, min(5, score + 2)) 
        
        if final_score >= 4:
            text = "🔥 Отличная готовность — можно делать тяжёлую тренировку"
        elif final_score >= 3:
            text = "👍 Нормальная готовность — допустима умеренная нагрузка"
        elif final_score >= 2:
            text = "⚠️ Сниженная готовность — лучше лёгкая тренировка"
        else:
            text = "❌ Низкая готовность — восстановление или отдых"
        
        return final_score, text
    except Exception as e:
        print(f"Readiness Error: {e}")
        return 2, "👍 Нормальная готовность (ошибка расчета)"

# --- MAIN ---
def main():
    now = datetime.utcnow() + timedelta(hours=1) # Твое локальное время
    today = now.strftime("%Y-%m-%d")
    yesterday = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")

    activities = get_strava_data()
    morning = get_morning_metrics(today)

    rhr = morning.get("Resting_HR", "Н/Д")
    hrv = morning.get("HRV", "Н/Д")
    vo2 = estimate_vo2max(activities)
    f_age = fitness_age(rhr, hrv) # Используем фитнес-возраст

    today_acts = [a for a in activities if a.get("start_date_local", "")[:10] == today]

    if not today_acts:
        # Считаем готовность
        r_val, r_text = get_readiness(morning)
        
        prompt = f"""
Ты — Арнольд, легендарный тренер. Проанализируй состояние атлета ({BIO_AGE} лет).
Данные: Пульс {rhr}, HRV {hrv}, Готовность {r_val}/5 ({r_text}).
Твой Fitness Age: {f_age}. Вчерашний TSS: {sum(calc_tss(a) for a in activities if a.get('start_date_local','')[:10]==yesterday)}.

Дай развернутый, ироничный разбор на РУССКОМ языке. Оцени цифры и дай приказ на сегодня.
"""
        ai_response = ask_arnie(prompt, "Восстановление в норме. Работай по самочувствию.")

        report = (
            f"🌅 *УТРЕННИЙ СТАТУС*\n\n"
            f"❤️ Пульс: {rhr} | 🌀 HRV: {hrv}\n"
            f"🔋 *Готовность: {r_val}/5*\n"
            f"📢 {r_text}\n"
            f"🧬 Fitness Age: {f_age}\n\n"
            f"🤖 АРНИ:\n_{ai_response}_"
        )
        send_tg(report)
        return

    # ==========================================
    # 2. АНАЛИЗ ТРЕНИРОВКИ (Если есть активность)
    # ==========================================
    last = sorted(today_acts, key=lambda x: x.get("start_date_local"))[-1]
    tss = calc_tss(last)
    dist = round(last.get("distance", 0) / 1000, 2)
    name = last.get("name", "Тренировка")

    # Формируем ОДИН четкий промпт
    prompt = f"""
Ты — Арнольд, жесткий тренер. Проанализируй тренировку: {name}.
Дистанция: {dist} км, TSS: {tss}. Пульс: {rhr}, HRV: {hrv}.
Дай оценку качества, скажи, не халявил ли я, и что делать дальше. 

ВАЖНО: Пиши в своем стиле строго на русском языке.
"""
    
    fallback = "Тренировка засчитана. Хорошая работа."
    ai_response = ask_arnie(prompt, fallback)

    report = (
        f"🏃 *ТРЕНИРОВКА*\n\n"
        f"*{name}*\n"
        f"📍 {dist} км | 📈 TSS {tss}\n"
        f"🧬 Fitness Age: {f_age}\n\n"
        f"🤖 АРНИ:\n_{ai_response}_"
    )

    send_tg(report)
    print("✅ TRAINING REPORT SENT")

if __name__ == "__main__":
    main()
