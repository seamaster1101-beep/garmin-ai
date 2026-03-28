import os, requests, json, sys, gspread
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials

# --- КОНСТАНТЫ ---
BIO_AGE = 63
FTP_GARMIN = 213 
ATHLETE_WEIGHT = 88.0
SPREADSHEET_ID = "1rxg5oqDXWXwHSHMmR-RbJuad8rXe2OdmCEMUMY2SBT4"

def get_env(name):
    val = os.environ.get(name)
    if not val:
        print(f"❌ Ошибка: {name} не найден!"); sys.exit(1)
    return val

def safe_float(val, default=0.0):
    try:
        res = float(str(val).replace(',', '.').strip())
        return res if res == res else default
    except: return default

def ask_expert(prompt):
    """Исправленная функция: теперь она точно скажет, если AI сбоит"""
    try:
        api_key = os.environ.get('GEMINI_API_KEY')
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
        response = requests.post(url, json={"contents": [{"parts": [{"text": str(prompt)}]}]}, timeout=30)
        res = response.json()
        
        # Если API вернул ошибку (например, 400 или 429)
        if response.status_code != 200:
            return f"Ошибка API ({response.status_code}): {res.get('error', {}).get('message', 'Неизвестно')}"

        # Проверка наличия контента в ответе
        if 'candidates' in res and len(res['candidates']) > 0:
            candidate = res['candidates'][0]
            if 'content' in candidate and 'parts' in candidate['content']:
                return candidate['content']['parts'][0]['text'].replace("_", " ").replace("*", " ").strip()
            else:
                return f"AI заблокировал ответ. Причина: {candidate.get('finishReason', 'Не указана')}"
        
        return "AI вернул пустой результат. Проверь квоту или данные."
    except Exception as e:
        return f"Критическая ошибка связи с AI: {str(e)}"

def main():
    now = datetime.utcnow() + timedelta(hours=1)
    today = now.strftime("%Y-%m-%d")
    
    # 1. СТРАВА
    try:
        r = requests.post("https://www.strava.com/oauth/token", data={
            'client_id': get_env('STRAVA_CLIENT_ID'),
            'client_secret': get_env('STRAVA_CLIENT_SECRET'),
            'refresh_token': get_env('STRAVA_REFRESH_TOKEN'),
            'grant_type': 'refresh_token'
        }).json()
        token = r.get('access_token')
        activities = requests.get("https://www.strava.com/api/v3/athlete/activities", 
                                  headers={"Authorization": f"Bearer {token}"}, params={"per_page": 50}).json()
    except Exception as e:
        print(f"Strava error: {e}"); activities = []

    # 2. ТАБЛИЦА
    try:
        creds = Credentials.from_service_account_info(json.loads(get_env('GOOGLE_CREDS')), 
                                                      scopes=["https://www.googleapis.com/auth/spreadsheets"])
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
        records = sheet.get_all_records()
        morning = next((row for row in reversed(records) if today in str(row.get('Date', ''))), records[-1])
        row_idx = next((i+2 for i, r in enumerate(records) if today in str(r.get('Date', ''))), None)
    except Exception as e:
        print(f"Sheets error: {e}"); return

    # 3. МЕТРИКИ
    hrv = int(safe_float(morning.get("HRV"), 100))
    rhr = int(safe_float(morning.get("Resting_HR"), 44))
    fat = safe_float(morning.get("Body_Fat"), 18.3)
    s_raw = safe_float(morning.get("Sleep_Hours"), 7.0)
    sleep_h = round(s_raw / 10 if s_raw > 24 else s_raw, 1)
    sleep_score = int(safe_float(morning.get("Sleep_Score"), 70))
    recovery_h = int(safe_float(morning.get("Recovery_Time"), 0))
    body_battery = morning.get("Body_Battery", "н/д")
    deep_sleep = morning.get("Deep_Sleep", "н/д")

    # 4. TSB & VO2
    ctl, atl, vo2_vals = 0.0, 0.0, []
    for a in sorted(activities, key=lambda x: x.get("start_date_local", "")):
        if a.get("type") in ["Ride", "VirtualRide"]:
            w = safe_float(a.get("average_watts"))
            tss = (a.get("moving_time", 0)/3600)*(w/FTP_GARMIN)**2*100 if w > 0 else 0
            ctl += (tss - ctl) / 42
            atl += (tss - atl) / 7
            hr = safe_float(a.get("average_heartrate"))
            if w > 110 and hr > 105:
                v = (w * 10.8 / ATHLETE_WEIGHT) + 7
                if 28 < v < 45: vo2_vals.append(v)

    tsb = round(ctl - atl, 1)
    vo2_avg = round(sum(vo2_vals[-7:]) / len(vo2_vals[-7:]), 1) if vo2_vals else 32.7
    eftp = int(vo2_avg * ATHLETE_WEIGHT * 0.071)

    # 5. FITNESS AGE (ФИКСАЦИЯ НА 54.6)
    f_age = round(BIO_AGE + (rhr-55)*0.4 + (fat-22)*0.5 - (hrv-45)*0.1 - (vo2_avg-35)*1.5, 1)
    f_age = max(45.0, min(BIO_AGE + 2, f_age))

    if row_idx:
        try:
            headers = sheet.row_values(1)
            if "Fitness_Age" in headers:
                sheet.update_cell(row_idx, headers.index("Fitness_Age")+1, f_age)
        except: pass

    # 6. ГОТОВНОСТЬ
    score = 3.5
    if hrv > 90: score += 1.0
    if sleep_score < 65: score -= 1.0
    if recovery_h > 24: score -= 1.0
    if tsb < -25: score -= 1.0
    score = max(1.0, min(5.0, round(score, 1)))

    # 7. ТВОЙ ПОЛНЫЙ ПРОМПТ
    FULL_PROMPT = (
        f"Ты — легендарный Арнольд, элитный коуч атлета 63 лет. Дай профессиональный анализ состояния. "
        f"ДАННЫЕ: HRV {hrv} (у него 100 — это элитно!), Пульс {rhr}, Сон {sleep_h}ч (Глубокий: {deep_sleep}), "
        f"Sleep Score: {sleep_score}/100, Recovery Time: {recovery_h}ч, Body Battery: {body_battery}, "
        f"Fit Age: {f_age}, VO2max: {vo2_avg}. ФОРМА: CTL {round(ctl,1)} (Фитнес), TSB {tsb} (Баланс). ГОТОВНОСТЬ: {score}/5. "
        f"\nИНСТРУКЦИИ: "
        f"1. Не просто читай цифры, а интерпретируй их! Сравнивай с нормой для 60+. ПИШИ НА РУССКОМ. "
        f"2. Если Recovery Time > 24ч или Sleep Score < 65 — СТРОГО ЗАПРЕТИ рекорды. "
        f"3. Трактуй TSB {tsb}: >10 (застой), -10...-25 (зона чемпионов), < -25 (риск перетрена). "
        f"4. ДАЙ ПЛАН: укажи зону (Z1, Z2 или Отдых) и время в минутах. "
        f"5. Если Fit Age {f_age} ниже 63 — вставь мощный комментарий. "
        f"6. Будь лаконичен (2-3 абзаца), сохрани дух Терминатора. Фирменная фраза в конце."
    )
    
    ai_msg = ask_expert(FULL_PROMPT)
    icon = "🟢" if score > 3.8 else "🟡" if score > 2.5 else "🛑"
    
    final_report = (f"🌅 *УТРЕННИЙ СТАТУС* {icon}\n🟢🟢 *FTP: {FTP_GARMIN} | eFTP: {eftp} ({eftp-FTP_GARMIN})*\n\n"
                    f"❤️ Пульс: {rhr} | 🌀 HRV: {hrv}\n📊 TSB: {tsb}\n🔋 *Готовность: {score}/5*\n"
                    f"🧬 Fit Age: {f_age} | VO2max: {vo2_avg}\n\n🤖 *АРНИ:* \n_{ai_msg}_")

    requests.post(f"https://api.telegram.org/bot{get_env('TELEGRAM_BOT_TOKEN')}/sendMessage",
                  json={"chat_id": get_env('TELEGRAM_CHAT_ID'), "text": final_report, "parse_mode": "Markdown"})

if __name__ == "__main__":
    main()
