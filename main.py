import os
import requests
import json
from datetime import datetime, timedelta
import sys

# --- CONFIG ---
def get_env(name):
    val = os.environ.get(name)
    if not val:
        print(f"❌ Нет переменной: {name}")
        sys.exit(1)
    return val

# Твой ID таблицы
SPREADSHEET_ID = "1rxg5oqDXWXwHSHMmR-RbJuad8rXe2OdmCEMUMY2SBT4" 

CLIENT_ID = get_env('STRAVA_CLIENT_ID')
CLIENT_SECRET = get_env('STRAVA_CLIENT_SECRET')
REFRESH_TOKEN = get_env('STRAVA_REFRESH_TOKEN')
TELEGRAM_BOT_TOKEN = get_env('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = get_env('TELEGRAM_CHAT_ID')
GEMINI_API_KEY = get_env('GEMINI_API_KEY')
GOOGLE_CREDS_JSON = get_env('GOOGLE_CREDS')

def send_tg(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                     json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=15)
    except: pass

def get_power_label(watts):
    if not watts: return "N/A"
    if watts < 150: return "Z1-Z2 (Easy)"
    if watts < 220: return "Z3 (Tempo)"
    return "Z4+ (Hard)"

# --- ОСНОВНОЙ ЦИКЛ ---
def main():
    print("🚀 ARNI v3.1: SYSTEM ONLINE")
    
    # 1. Strava Auth & Data
    try:
        print("🔐 Strava Auth...")
        res = requests.post("https://www.strava.com/oauth/token", data={
            'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
            'refresh_token': REFRESH_TOKEN, 'grant_type': 'refresh_token'
        })
        s_data = res.json()
        token = s_data.get('access_token')
        
        print("🏃 Fetching Activities...")
        # Берем данные за последние 7 дней для расчета нагрузки
        after = int((datetime.now() - timedelta(days=7)).timestamp())
        r = requests.get("https://www.strava.com/api/v3/athlete/activities", 
                        headers={"Authorization": f"Bearer {token}"}, params={"after": after})
        all_activities = r.json() if isinstance(r.json(), list) else []
        
        today_str = datetime.now().strftime("%Y-%m-%d")
        today_acts = [a for a in all_activities if today_str in a.get('start_date_local', '')]
        print(f"✅ Найдено сегодня: {len(today_acts)}")

    except Exception as e:
        print(f"❌ Strava Error: {e}")
        return

    # 2. Google Sheets Data
    print("📊 Reading Morning Metrics...")
    morning = {}
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_info(
            json.loads(GOOGLE_CREDS_JSON), 
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly", "https://www.googleapis.com/auth/drive.readonly"]
        )
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
        records = sheet.get_all_records()
        
        # Ищем сегодняшнюю строку
        for row in reversed(records):
            if today_str in str(row.get('Date', '')):
                morning = row
                print(f"✅ Morning data found: HR {row.get('Resting_HR')}, HRV {row.get('HRV')}")
                break
    except Exception as e:
        print(f"❌ Sheets Error: {e}")

    # 3. Analytics & Fitness Score
    # Базовый расчет нагрузки на основе Strava
    loads = [ (a.get('average_heartrate', 120) * (a.get('moving_time', 0)/60) / 100) for a in all_activities ]
    atl = round(sum(loads) / 7, 1) if loads else 0
    ctl = 12.7 # Временная константа, пока нет истории за 42 дня
    tsb = round(ctl - atl, 1)
    
    # Считаем Fitness Score на основе пульса и HRV из таблицы
    score = 50
    if morning.get('Resting_HR'):
        # Идеальный пульс ~45-50. Если выше — штраф.
        score -= (morning['Resting_HR'] - 45) * 2
    if morning.get('HRV'):
        # Идеальный HRV > 90.
        score += (morning['HRV'] - 70) / 2
        
    fitness_score = max(5, min(100, int(score)))
    status = "Восстановление" if tsb > 5 else "Прогресс" if tsb > -10 else "ПЕРЕГРУЗ ⚠️"
    
    act_summary = ""
    for a in today_acts:
        dist = round(a.get('distance', 0)/1000, 2)
        pwr = get_power_label(a.get('average_watts'))
        act_summary += f"• {a.get('name')}: {dist}км | {pwr}\n"

    # 4. AI Analysis (Стабильная версия)
    print("🧠 Gemini AI Analysis...")
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash")
        
        prompt = f"""Ты - АРНИ, суровый тренер из Терминатора. Проанализируй данные:
        Утро: Пульс {morning.get('Resting_HR')}, HRV {morning.get('HRV')}.
        Тренировки сегодня: {act_summary if act_summary else 'НЕТ ТРЕНИРОВОК! ЛЕНТЯЙ!'}
        Метрики: Форма={ctl}, Усталость={atl}, Баланс={tsb}.
        Fitness Score: {fitness_score}/100. Статус: {status}.
        
        Дай разбор: коротко, едко, в стиле Шварценеггера. Похвали за пульс 45 или HRV 94, но напомни, что расслабляться нельзя. До 400 знаков."""

        response = model.generate_content(prompt)
        ai_text = response.text
    except Exception as e:
        ai_text = f"Ошибка ИИ: {e}"

    # 5. Final Report
    full_report = (
        f"🏋️ *ARNI INTELLIGENCE REPORT*\n\n"
        f"🔥 *Fitness Score:* {fitness_score}/100\n"
        f"📈 CTL: {ctl} | ATL: {atl} | TSB: {tsb}\n"
        f"🚦 Статус: *{status}*\n\n"
        f"🏃 *Сегодня:*\n{act_summary if act_summary else 'Тренировок не найдено'}\n\n"
        f"🤖 *АРНИ:* \n_{ai_text}_"
    )
    
    send_tg(full_report)
    print("✅ DONE. Report sent to Telegram.")

if __name__ == "__main__":
    main()
