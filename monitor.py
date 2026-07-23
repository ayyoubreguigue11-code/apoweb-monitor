import os
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

LOGIN = os.environ["APO_LOGIN"]
PASSWORD = os.environ["APO_PASS"]

session = requests.Session()

url = "https://apoweb-ta.uae.ac.ma/dossier_etudiant_fsjes_tanger/"

data = {
    "Login": LOGIN,
    "pass": PASSWORD,
    "submit": "Login"
}

r = session.post(url, data=data)

html = r.text

if "note" in html.lower() or "moyenne" in html.lower():
    message = "🎉 تم العثور على صفحة النقط أو ظهور تغيير جديد في APoweb !"
else:
    message = "ℹ️ تم فحص APoweb، لا يوجد تغيير جديد."

telegram_url = (
    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
)

requests.post(
    telegram_url,
    data={
        "chat_id": CHAT_ID,
        "text": message
    }
)
