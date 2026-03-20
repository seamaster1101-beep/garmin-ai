import garth
import base64
import os
import shutil

# Твои данные (впиши их сюда один раз)
email = "seamaster@inbox.lv"
password = "Gozo1101"

def make_session():
    session_dir = "./.garth"
    
    # 1. Удаляем старую папку, если она есть, чтобы сессия была чистой
    if os.path.exists(session_dir):
        shutil.rmtree(session_dir)
    
    print("🚀 Логинимся в Garmin...")
    try:
        garth.login(email, password)
        garth.save(session_dir)
        print("✅ Сессия сохранена в папку .garth")
        
        # 2. Архивируем и кодируем в Base64
        # Мы имитируем то, что делает Linux: tar + base64
        import subprocess
        # Создаем архив (работает и на Win10+, и на Mac/Linux)
        subprocess.run(["tar", "-czf", "garth.tar.gz", ".garth"])
        
        with open("garth.tar.gz", "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
        
        # 3. Сохраняем готовую строку в файл
        with open("session_base64.txt", "w") as f:
            f.write(encoded)
            
        print("\n🔥 ГОТОВО! Строка для GitHub сохранена в файл: session_base64.txt")
        print("Скопируй всё содержимое этого файла.")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    make_session()
