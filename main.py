import os, requests, json, sys, gspread
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials

# --- CONFIG ---
BIRTH_DATE = datetime(1963, 5, 29)
FTP_GARMIN = 213 
SPREADSHEET_ID = "1rxg5oqDXWXwHSHMmR-RbJuad8rXe2OdmCEMUMY2SBT4"

def get_bio_age():
    return (datetime.utcnow() - BIRTH_DATE).days / 365.25

def get_env(name):
    val = os.environ.get(name)
    if not val:
        print(f"❌ Нет переменной: {name}"); sys.exit(1)
    return val

# Переменные окружения
CLIENT_ID = get_env('STRAVA_CLIENT_ID')
CLIENT_SECRET = get_env('STRAVA_CLIENT_SECRET')
REFRESH_TOKEN = get_env('STRAVA_REFRESH_TOKEN')
TELEGRAM_BOT_TOKEN = get_env('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = get_env('TELEGRAM_CHAT_ID')
GEMINI_API_KEY = get_env('GEMINI_API_KEY')
GOOGLE_CREDS_JSON = get_env('GOOGLE_CREDS')

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def safe_float(val, default=0.0):
    if val is None or str(val).strip() in ["", "Н/Д", "None"]: return default
    try:
        v = float(str(val).replace(',', '.').strip())
        return v if v > 0 else default
    except: return default

def send_tg(msg):
    if len(msg) > 4000: msg = msg[:3900]
    try:
        res = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                     json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=15)
        if res.status_code != 200: print(f"⚠️ TG Error: {res.text}")
    except Exception as e: print(f"❌ TG Exception: {e}")

def ask_arnie(prompt, fallback_text):
    try:
        # 1. Сначала узнаем, какие модели сейчас доступны
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
            
        # 2. Выбираем Flash (она быстрее и стабильнее для таких задач)
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
            # Убираем лишние символы форматирования, которые иногда мешают в Telegram
            return data["candidates"][0]["content"]["parts"][0]["text"].strip().replace("_", " ").replace("*", " ")
            
    except Exception as e:
        print(f"⚠️ AI Error: {e}")
        
    return fallback_text

# --- РАБОТА С ДАННЫМИ ---
def get_google_client():
    creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), 
                                                  scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return gspread.authorize(creds)

def update_fitness_age_in_sheet(target_date, f_age_val):
    try:
        client = get_google_client()
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
        dates = sheet.col_values(1)
        for i, val in enumerate(dates):
            if target_date in val:
                header = sheet.row_values(1)
                if "Fitness_Age" in header:
                    sheet.update_cell(i + 1, header.index("Fitness_Age") + 1, f_age_val)
                    print(f"✅ FitAge {f_age_val} записан.")
                    break
    except Exception as e: print(f"⚠️ Sheet update error: {e}")

def estimate_performance(activities, weight=88.0):
    vals_vo2 = []
    hr_max = 208 - (0.7 * get_bio_age())
    for a in sorted(activities, key=lambda x: x.get("start_date_local", "")):
        if a.get("type") not in ["Ride", "VirtualRide"]: continue
        w = safe_float(a.get("average_watts"), 0)
        hr = safe_float(a.get("average_heartrate"), 0)
        if w > 0 and hr > 105:
            v = (10.51 * (w * (hr_max / hr)) / weight) + 7
            if 20 < v < 65: vals_vo2.append(v)
    
    if not vals_vo2: return None, None
    avg_vo2 = round(sum(vals_vo2[-7:]) / len(vals_vo2[-7:]), 1)
    # Ограничение eFTP 100-400 ватт
    eftp = max(100, min(400, int(round(avg_vo2 * weight * 0.071, 0))))
    return avg_vo2, eftp

# --- MAIN ---
def main():
    now = datetime.utcnow() + timedelta(hours=1)
    today = now.strftime("%Y-%m-%d")
    
    # Strava Data
    try:
        res = requests.post("https://www.strava.com/oauth/token", data={
            'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
            'refresh_token': REFRESH_TOKEN, 'grant_type': 'refresh_token'
        }, timeout=15)
        token = res.json().get('access_token')
        r = requests.get("https://www.strava.com/api/v3/athlete/activities",
                         headers={"Authorization": f"Bearer {token}"}, params={"per_page": 100}, timeout=15)
        data = r.json()
        activities = data if isinstance(data, list) else []
    except Exception as e: 
        print(f"❌ Strava fail: {e}"); activities = []

    # Google Sheets Data
    client = get_google_client()
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
    records = sheet.get_all_records()
    morning = next((row for row in reversed(records) if today in str(row.get('Date', ''))), 
                   (records[-1] if records else {}))

    # Metrics
    rhr = safe_float(morning.get("Resting_HR"), 60)
    hrv = safe_float(morning.get("HRV"), 45)
    weight = safe_float(morning.get("Weight"), 88.0)
    if weight > 500: weight /= 10
    fat = safe_float(morning.get("Body_Fat"), 18.3)
    if fat > 100: fat /= 10
    
    # Расширенные метрики сна и восстановления
    sleep = safe_float(morning.get("Sleep_Hours"), 7.0)
    if sleep > 24: sleep /= 10
    deep_sleep = safe_float(morning.get("Deep_Sleep"), 0.0)
    sleep_score = int(safe_float(morning.get("Sleep_Score"), 0))
    recovery_h = int(safe_float(morning.get("Recovery_Time"), 0))

    # Calculations
    vo2_val, eftp_val = estimate_performance(activities, weight=weight)
    
    ctl, atl = 0, 0
    for a in sorted(activities, key=lambda x: x.get("start_date_local", "")):
        if a.get("type") in ["Ride", "VirtualRide"]:
            w = safe_float(a.get("average_watts"), 0)
            t = a.get("moving_time", 0)
            tss = (t/3600)*(w/FTP_GARMIN)**2*100 if w > 0 else 0
            ctl += (tss - ctl) / 42
            atl += (tss - atl) / 7
    tsb = round(ctl - atl, 1)

    # Readiness & FitAge
    score = 3.0  # Базовая точка
    if hrv > 75: score += 0.5
    elif hrv < 50: score -= 1.0
    if rhr < 50: score += 0.5
    elif rhr > 60: score -= 0.5
    
    # Сон и восстановление (из сломанного кода)
    if sleep < 6.0: score -= 1.0
    if deep_sleep < 0.7 and deep_sleep > 0: score -= 0.8
    if sleep_score < 65 and sleep_score > 0: score -= 1.0
    if recovery_h > 24: score -= 1.0
    
    # Форма
    if tsb < -25: score -= 1.5
    elif -20 <= tsb <= -5: score += 0.5
    
    score = max(0, min(5, round(score, 1)))
    
    icon = "🔥🏆" if score >= 4 else "🟢🟢" if score >= 2.8 else "🟡"
    circles = "🟢🟢🟢" if score >= 4 else "🟢🟢" if score >= 2.8 else "🟡"

    vo2_calc = vo2_val if vo2_val is not None else 35
    f_age = round(get_bio_age() + (rhr-51)*0.2 + (fat-22)*0.5 - (hrv-85)*0.05 - (vo2_calc-35)*1.2, 1)
    f_age = max(45.0, min(get_bio_age() + 2, f_age))

    update_fitness_age_in_sheet(today, f_age)

    # Report
    eftp_str = f" | eFTP: {eftp_val} ({eftp_val - FTP_GARMIN:+})" if eftp_val else ""
    header_ftp = f"{circles} *FTP: {FTP_GARMIN}{eftp_str}*"
    
    today_acts = [a for a in activities if a.get("start_date_local", "")[:10] == today]
    
    if not today_acts:
        prompt = (
            f"Ты — опытный коуч и спортивный директор. Твой атлет — мужчина 63 лет. "
            f"Дай глубокий, человечный анализ утреннего состояния. "
            f"ДАННЫЕ: HRV {int(hrv)}, Пульс {int(rhr)}, Сон {sleep}ч (Глубокий: {deep_sleep}ч), "
            f"Sleep Score: {sleep_score}/100, Recovery Time: {recovery_h}ч, TSB {tsb}, Готовность {score}/5. "
            f"Fit Age {f_age}. VO2max: {vo2_val if vo2_val else 'н/д'}. "
            f"\nИНСТРУКЦИИ: "
            f"1. ГОВОРИ КАК ЧЕЛОВЕК: Будь мудрым наставником. Сравни Fit Age {f_age} и форму с активными ровесниками (60-65 лет). "
            f"2. ЧЕСТНОСТЬ И ПОДДЕРЖКА: Если восстановление провалено ({score}/5), поддержи атлета, объясни, что отдых — это тоже тренировка. "
            f"3. БЕЗ ВОДЫ: Не перечисляй цифры (они есть в отчете), а делай выводы. "
            f"4. ПЛАН: Четко скажи, что делать сегодня (Z1, Z2 или полный отдых) и на сколько минут. "
            f"5. ФИНАЛ: Весь текст пиши нормальным языком, но в самом конце добавь ОДНУ легендарную фразу Арнольда для настроя. "
            f"Пиши строго на русском."
        )
        ai_msg = ask_arnie(prompt, "В строю. Жду работу.")
        
        report = (f"🌅 *УТРЕННИЙ СТАТУС* {icon}\n{header_ftp}\n\n"
                  f"❤️ Пульс: {int(rhr)} | 🌀 HRV: {int(hrv)}\n"
                  f"🔋 *Готовность: {score}/5* (Сон: {sleep_score})\n"
                  f"📊 Форма (TSB): {tsb} | VO2max: {vo2_val if vo2_val else 'н/д'}\n"
                  f"🧬 Fit Age: {f_age}\n\n"
                  f"🤖 *АРНИ:* \n_{ai_msg}_")
    else:
        # Анализ последней тренировки за сегодня
        last = sorted(today_acts, key=lambda x: x.get("start_date_local"))[-1]
        dist = round(last.get("distance", 0) / 1000, 2)
        name = last.get("name", "Тренировка")
        
        # Для расчета TSS нам нужен FTP_GARMIN
        w_avg = last.get("average_watts", 0)
        t_sec = last.get("moving_time", 0)
        tss_last = round((t_sec/3600)*(w_avg/FTP_GARMIN)**2*100, 1) if w_avg else 0

        prompt = (
            f"Ты — опытный тренер. Разбери тренировку атлета 63 лет: {name}. "
            f"Дистанция: {dist}км, TSS: {tss_last}. Утренняя готовность была {score}/5. "
            f"\nИНСТРУКЦИИ: "
            f"1. Оцени, не была ли нагрузка чрезмерной для текущего состояния и возраста. "
            f"2. Похвали за дисциплину, укажи на успехи или халтуру (если TSS слишком мал). "
            f"3. Дай совет на завтра. Пиши на русском. "
            f"В конце — одна фраза Арнольда."
        )
        ai_msg = ask_arnie(prompt, "Работа сделана. Отдыхай.")

        report = (f"🏃 *ТРЕНИРОВКА* {icon}\n{header_ftp}\n\n"
                  f"*{name}*\n"
                  f"📍 {dist} км | 📈 TSS: {tss_last}\n"
                  f"🧬 Fit Age: {f_age}\n\n"
                  f"🤖 *АРНИ:* \n_{ai_msg}_")

    send_tg(report)

if __name__ == "__main__":
    main()
