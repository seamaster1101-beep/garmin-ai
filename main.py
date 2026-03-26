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

# ВСТАВЬ СВОЙ ID ТАБЛИЦЫ ЗДЕСЬ:
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

# --- МОДУЛЬ АНАЛИТИКИ ---
def calc_training_metrics(activities):
    # Упрощенный расчет нагрузки (TSS) на базе HR и времени
    # Для CTL/ATL нужно больше данных, делаем скользящее среднее за доступные дни
    loads = [ (a.get('average_heartrate', 120) * (a.get('moving_time', 0)/60) / 100) for a in activities ]
    if not loads: return 0, 0, 0
    
    atl = sum(loads[:7]) / 7  # Усталость (неделя)
    ctl = sum(loads) / 42     # Форма (6 недель - аппроксимация)
    tsb = ctl - atl           # Баланс (Ready to race?)
    return round(ctl, 1), round(atl, 1), round(tsb, 1)

def get_power_label(watts):
    if not watts: return "N/A"
    if watts < 150: return "Z1-Z2 (Easy)"
    if watts < 220: return "Z3 (Tempo)"
    return "Z4+ (Hard)"

# --- ОСНОВНОЙ ЦИКЛ ---
def main():
    print("🚀 ARNI v3.0: SYSTEM ONLINE")
    
    # 1. Strava Auth & Data
    try:
        print("🔐 Strava Auth...")
        res = requests.post("https://www.strava.com/oauth/token", data={
            'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
            'refresh_token': REFRESH_TOKEN, 'grant_type': 'refresh_token'
        })
        s_data = res.json()
        token = s_data.get('access_token')
        
        # Лог нового рефреша на случай обновления
        if s_data.get('refresh_token') and s_data.get('refresh_token') != REFRESH_TOKEN:
            print(f"⚠️ NEW REFRESH TOKEN: {s_data.get('refresh_token')}")

        print("🏃 Fetching Activities...")
        after = int((datetime.now() - timedelta(days=7)).timestamp()) # Берем неделю для метрик
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
        
        for row in reversed(records):
            if today_str in str(row.get('Date', '')):
                morning = row
                print("✅ Morning data found.")
                break
    except Exception as e:
        print(f"❌ Sheets Error: {e}")

    # 3. Analytics
    ctl, atl, tsb = calc_training_metrics(all_activities)
    fitness_score = max(0, min(100, int(50 + tsb * 2)))
    
    status = "Восстановление" if tsb > 5 else "Прогресс" if tsb > -10 else "ПЕРЕГРУЗ ⚠️"
    
    act_summary = ""
    for a in today_acts:
        dist = round(a.get('distance', 0)/1000, 2)
        pwr = get_power_label(a.get('average_watts'))
        act_summary += f"• {a.get('name')}: {dist}км | {pwr}\n"

    # 4. AI Analysis
    print("🧠 Gemini AI Analysis...")
    try:
        from google import genai
        client_ai = genai.Client(api_key=GEMINI_API_KEY)
        
        prompt = f"""Ты - АРНИ, суровый тренер. Проанализируй данные:
        Утро: {morning}
        Тренировки сегодня: {act_summary}
        Метрики: CTL(Форма)={ctl}, ATL(Усталость)={atl}, TSB(Баланс)={tsb}.
        Fitness Score: {fitness_score}/100. Статус: {status}.
        
        Дай разбор: коротко, едко, в стиле Шварценеггера. Похвали за низкий пульс или отругай за лень. До 450 знаков."""

        response = client_ai.models.generate_content(model="gemini-1.5-flash", contents=prompt)
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
