import os
import requests


BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

message = "✅ تم تشغيل نظام مراقبة نقط APoweb بنجاح."

url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

response = requests.post(
    url,
    data={
        "chat_id": CHAT_ID,
        "text": message,
    },
    timeout=30,
)

response.raise_for_status()
print("Telegram notification sent successfully.")
