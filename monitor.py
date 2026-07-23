import json
import os
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup


APOWEB_URL = (
    "https://apoweb-ta.uae.ac.ma/"
    "dossier_etudiant_fsjes_tanger/"
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
APO_LOGIN = os.environ["APO_LOGIN"]
APO_PASS = os.environ["APO_PASS"]

STATE_FILE = Path("state.json")


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def send_telegram(message: str) -> None:
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


def fetch_grades() -> dict:
    session = requests.Session()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/126 Safari/537.36"
        )
    }

    session.get(
        APOWEB_URL,
        headers=headers,
        timeout=30,
    )

    response = session.post(
        APOWEB_URL,
        headers=headers,
        data={
            "Login": APO_LOGIN,
            "pass": APO_PASS,
            "submit": "Login",
        },
        timeout=30,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # التأكد من أن تسجيل الدخول نجح
    if (
        soup.select_one('input[name="Login"]')
        and soup.select_one('input[name="pass"]')
    ):
        raise RuntimeError(
            "فشل تسجيل الدخول إلى APoweb. "
            "تحقق من APO_LOGIN و APO_PASS."
        )

    grades = {}

    for row in soup.select("table tr"):
        cells = [
            clean_text(cell.get_text(" ", strip=True))
            for cell in row.select("th, td")
        ]

        if len(cells) < 2:
            continue

        subject = cells[0]
        note = cells[1] if len(cells) > 1 else ""
        result = cells[3] if len(cells) > 3 else ""

        if not subject:
            continue

        if subject.upper().startswith("SEMESTRE"):
            continue

        # تجاهل عناوين الجدول
        if subject.upper() in {
            "ELEMENT",
            "ÉLÉMENT",
            "NOTE",
        }:
            continue

        grades[subject] = {
            "note": note,
            "result": result,
        }

    if not grades:
        raise RuntimeError(
            "تم الدخول، لكن لم يتم العثور على جدول النقط."
        )

    return grades


def load_previous_state() -> dict:
    if not STATE_FILE.exists():
        return {}

    with STATE_FILE.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_state(grades: dict) -> None:
    with STATE_FILE.open("w", encoding="utf-8") as file:
        json.dump(
            grades,
            file,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )


def find_changes(old: dict, new: dict) -> list[str]:
    changes = []

    for subject, current in new.items():
        previous = old.get(
            subject,
            {"note": "", "result": ""},
        )

        old_note = previous.get("note", "")
        new_note = current.get("note", "")

        old_result = previous.get("result", "")
        new_result = current.get("result", "")

        if old_note != new_note or old_result != new_result:
            details = [f"📚 {subject}"]

            if old_note != new_note:
                details.append(
                    f"النقطة: {old_note or 'غير معلنة'}"
                    f" ⟶ {new_note or 'غير معلنة'}"
                )

            if old_result != new_result:
                details.append(
                    f"النتيجة: {old_result or 'غير معلنة'}"
                    f" ⟶ {new_result or 'غير معلنة'}"
                )

            changes.append("\n".join(details))

    return changes


def main() -> None:
    current_grades = fetch_grades()
    previous_grades = load_previous_state()

    # أول تشغيل: حفظ الوضع الحالي دون اعتباره تغييراً
    if not previous_grades:
        save_state(current_grades)
        send_telegram(
            "✅ تم ربط مراقب APoweb بنجاح.\n"
            "تم حفظ النقط الحالية كحالة أولية، "
            "وسيتم إشعارك عند حدوث تغيير."
        )
        return

    changes = find_changes(
        previous_grades,
        current_grades,
    )

    if changes:
        message = (
            "🔔 تم اكتشاف تغيير جديد في نقط APoweb:\n\n"
            + "\n\n".join(changes)
        )
        send_telegram(message)
        save_state(current_grades)
    else:
        print("لا يوجد أي تغيير في النقط.")


if __name__ == "__main__":
    main()
