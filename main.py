import base64, tarfile, os, garth, time, random, sys, requests, json
from datetime import datetime, timedelta
from garminconnect import Garmin

# --- 1. CONFIG ---
GARMIN_SESSION_BASE64 = os.environ.get("GARMIN_SESSION_BASE64")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

now = datetime.now()
target_days = [now.strftime("%Y-%m-%d"), (now - timedelta(days=1)).strftime("%Y-%m-%d")]

def send_tg(message):
    if len(message) > 3900: message = message[:3900] + "\n\n...(обрезано)"
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"})
    except: pass

def escape_md(text):
    for char in ['_', '*', '`', '[', '(', ')', '-']:
        text = str(text).replace(char, f"\\{char}")
    return text

def ask_gemini(data_text):
    if not GEMINI_API_KEY or not data_text: return ""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        prompt = f"Ты тренер АРНИ. Кратко проанализируй тренировки: {data_text[:500]}. Оцени нагрузку и дай 1 совет. Будь краток и суров. Макс 300 симв."
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        res = requests.post(url, json=payload, timeout=15)
        if res.status_code != 200: return ""
        data = res.json()
        if "candidates" in data and data["candidates"]:
            comment = data["candidates"][0]["content"]["parts"][0]["text"]
            return comment[:400]
        return ""
    except: return ""

def get_session():
    if not GARMIN_SESSION_BASE64: sys.exit(1)
    session_dir = os.path.abspath("./.garth")
    if os.path.exists(session_dir): import shutil; shutil.rmtree(session_dir)
    os.makedirs(session_dir, exist_ok=True)
    try:
        with open("session.tar.gz", "wb") as f:
            f.write(base64.b64decode(GARMIN_SESSION_BASE64.strip()))
        with tarfile.open("session.tar.gz", "r:gz") as tar:
            tar.extractall(path=".")
        garth.client.load(session_dir)
        gar = Garmin(); gar.garth = garth.client
        return gar
    except: return None

# --- 2. ЗАПУСК ---
print(f"--- Запуск финальной версии: {now.strftime('%H:%M')} ---")
gar = get_session()

if gar:
    try:
        time.sleep(random.uniform(10, 20)) # Удлиненная пауза для безопасности
        activities = gar.get_activities(0, 4) 
        
        report_parts = []
        raw_data_for_ai = ""
        
        for a in activities:
            start_time = a.get("startTimeLocal", "")
            if start_time[:10] in target_days:
                name = escape_md(a.get("activityName", "Тренировка"))
                dist = round(a.get("distance", 0)/1000, 2)
                dur = round(a.get("duration", 0)/60, 1)
                hr = a.get("averageHR", "--")
                load = round(a.get("activityTrainingLoad", 0), 1)
                date_label = "Сегодня" if start_time.startswith(target_days[0]) else "Вчера"

                info = f"🕒 *{date_label}* ({start_time[11:16]}) — {name}\n📏 {dist} км | ⏱ {dur} мин | 💓 HR: {hr}\n📊 Load: {load}"
                report_parts.append(info)
                if len(raw_data_for_ai) < 400: # Лимит для промпта
                    raw_data_for_ai += f"{date_label}: {name}, load {load}; "

        if report_parts:
            report_parts.reverse() 
            ai_comment = ask_gemini(raw_data_for_ai)
            full_report = f"🚀 *АРНИ: Сводка активностей*\n\n" + "\n\n".join(report_parts)
            if ai_comment:
                full_report += f"\n\n🤖 *АРНИ:* _{escape_md(ai_comment)}_"
            
            send_tg(full_report)
            print("✅ Успех!")
        else:
            print("ℹ️ Нет активностей за 48ч.")

    except Exception as e:
        if "429" in str(e):
            print("🚨 429! Спим и выходим."); time.sleep(300); sys.exit(1)
        print(f"⚠️ Ошибка: {e}")
