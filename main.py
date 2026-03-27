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
def get_readiness(morning, tsb=0):
    # Базовая готовность 2.5
    readiness_score = 2.5
    
    try:
        # 1. HRV (Индекс 5) - Вес: 1.0
        hrv_val = morning.get("HRV")
        if hrv_val and hrv_val != "Н/Д":
            val = int(hrv_val)
            if val > 75: readiness_score += 1.0
            elif val > 65: readiness_score += 0.5
            elif val < 45: readiness_score -= 1.0

        # 2. Пульс покоя (Индекс 4) - Вес: 0.5
        rhr_val = morning.get("Resting_HR")
        if rhr_val and rhr_val != "Н/Д":
            val = int(rhr_val)
            if 0 < val < 50: readiness_score += 0.5
            elif val > 60: readiness_score -= 0.5

        # 3. Сон (Индекс 8) - Вес: 1.5
        sleep_hrs = morning.get("Sleep_Hours")
        if sleep_hrs:
            val = float(str(sleep_hrs).replace(',', '.'))
            if val >= 7.5: readiness_score += 1.0
            elif val < 6.0: readiness_score -= 1.0

        # 4. Body Battery (Индекс 6) - Вес: 0.5
        bb_val = morning.get("Body_Battery")
        if bb_val:
            val = int(bb_val)
            if val > 80: readiness_score += 0.5
            elif val < 40: readiness_score -= 0.5

        # 5. Ограничитель по перегрузке (TSB)
        t_val = float(tsb) if tsb else 0
        if t_val < -25: readiness_score -= 1.5
        elif t_val < -15: readiness_score -= 0.5

    except Exception as e:
        print(f"Readiness Calculation Error: {e}")

    # Ограничиваем рамками 0 и 5
    readiness_score = max(0, min(5, round(readiness_score, 1)))

    # Интерпретация и иконки
    if readiness_score >= 4:
        readiness_text = "Отличная готовность — идеальный день для рекордов"
        rd_icon = "🔥🏆"
    elif readiness_score >= 3:
        readiness_text = "Хорошая готовность — можно тренироваться уверенно"
        rd_icon = "🟢🟢"
    elif readiness_score >= 2:
        readiness_text = "Средняя готовность — работаем, но без фанатизма"
        rd_icon = "🟢🟡"
    elif readiness_score >= 1:
        readiness_text = "Низкая готовность — лучше восстановиться"
        rd_icon = "🟠"
    else:
        readiness_text = "Критическая усталость — строгий отдых"
        rd_icon = "🔴"

    return readiness_score, readiness_text, rd_icon

# --- MAIN ---
def main():
    now = datetime.utcnow() + timedelta(hours=1)
    today = now.strftime("%Y-%m-%d")
    yesterday = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")

    activities = get_strava_data()
    morning = get_morning_metrics(today)

    # --- РАСЧЕТ TSB (ФОРМЫ) ---
    # ATL (усталость за 7 дней), CTL (фитнес за 42 дня)
    all_tss = []
    for a in activities:
        dt = datetime.strptime(a.get("start_date_local", "2000-01-01")[:10], "%Y-%m-%d")
        days_ago = (datetime.utcnow() - dt).days
        all_tss.append((days_ago, calc_tss(a)))

    atl = sum(tss for d, tss in all_tss if d <= 7) / 7
    ctl = sum(tss for d, tss in all_tss if d <= 42) / 42
    tsb = round(ctl - atl, 1)

    rhr = morning.get("Resting_HR", "Н/Д")
    hrv = morning.get("HRV", "Н/Д")
    f_age = fitness_age(rhr, hrv) 

    today_acts = [a for a in activities if a.get("start_date_local", "")[:10] == today]

    if not today_acts:
        # Передаем полученный TSB в расчет готовности
        r_val, r_text, r_icon = get_readiness(morning, tsb=tsb)
        
        # Профессиональный промпт (без воды)
        prompt = (
            f"Ты — элитный спортивный директор Арни. Тон: профессиональный, без воды. "
            f"Данные атлета: HRV {hrv}, Пульс {rhr}, Сон {morning.get('Sleep_Hours')}ч, "
            f"BB {morning.get('Body_Battery')}, Fit Age {f_age}. "
            f"Форма: CTL {round(ctl,1)}, ATL {round(atl,1)}, TSB {tsb}. "
            f"Готовность: {r_val}/5. "
            f"\nИНСТРУКЦИЯ: "
            f"1. Учитывай TSB: если он ниже -25, требуй отдыха. "
            f"2. Атлету 63 года. HRV > 70 — это элитный уровень, отметь это кратко. "
            f"3. Оцени: нужно ли сегодня восстанавливаться или можно грузиться. "
            f"4. ПИШИ СТРОГО НА РУССКОМ, КОРОТКО И ПО ДЕЛУ."
        )
        
        ai_response = ask_arnie(prompt, r_text)

        report = (
            f"🌅 *УТРЕННИЙ СТАТУС* {r_icon}\n\n"
            f"❤️ Пульс: {rhr} | 🌀 HRV: {hrv}\n"
            f"🔋 *Готовность: {r_val}/5*\n"
            f"📊 Форма (TSB): {tsb}\n"
            f"📢 {r_text}\n"
            f"🧬 Fitness Age: {f_age}\n\n"
            f"🤖 *АРНИ:* \n_{ai_response}_"
        )
        
        send_tg(report)
        print("✅ MORNING REPORT SENT")
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
