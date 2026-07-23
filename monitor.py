import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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


def current_time() -> str:
    now = datetime.now(ZoneInfo("Africa/Casablanca"))
    return now.strftime("%d/%m/%Y - %H:%M")


def send_telegram(message: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message,
        },
        timeout=(30, 60),
    )

    response.raise_for_status()


def fetch_grades() -> dict:
    session = requests.Session()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/126.0 Safari/537.36"
        )
    }

    session.get(
        APOWEB_URL,
        headers=headers,
        timeout=(30, 120),
    )

    response = session.post(
        APOWEB_URL,
        headers=headers,
        data={
            "Login": APO_LOGIN,
            "pass": APO_PASS,
            "submit": "Login",
        },
        timeout=(30, 120),
    )

    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    if (
        soup.select_one('input[name="Login"]')
        and soup.select_one('input[name="pass"]')
    ):
        raise RuntimeError(
            "فشل تسجيل الدخول إلى APoweb. "
            "تحقق من APO_LOGIN وAPO_PASS."
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
            "تم تسجيل الدخول، لكن لم يتم العثور على جدول النقط."
        )

    return grades


def load_previous_state() -> dict:
    if not STATE_FILE.exists():
        return {}

    try:
        with STATE_FILE.open("r", encoding="utf-8") as file:
            return json.load(file)

    except (json.JSONDecodeError, OSError):
        return {}


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

    # المواد الجديدة أو النقط التي تم نشرها أو تعديلها
    for subject, current in new.items():
        new_note = current.get("note", "")
        new_result = current.get("result", "")

        if subject not in old:
            if new_note:
                title = "🔔 تم نشر نقطة جديدة"
            else:
                title = "➕ تمت إضافة مادة جديدة"

            details = [
                title,
                f"📚 المادة: {subject}",
                f"📝 النقطة: {new_note or 'غير معلنة'}",
                f"✅ النتيجة: {new_result or 'غير معلنة'}",
            ]

            changes.append("\n".join(details))
            continue

        previous = old[subject]

        old_note = previous.get("note", "")
        old_result = previous.get("result", "")

        if old_note == new_note and old_result == new_result:
            continue

        details = []

        # نشر نقطة كانت غير معلنة
        if not old_note and new_note:
            details.append("🔔 تم نشر نقطة جديدة")

        # تعديل نقطة موجودة
        elif old_note != new_note:
            details.append("🔄 تم تعديل نقطة")

        # تغيير النتيجة فقط
        elif old_result != new_result:
            details.append("⚠️ تم تغيير النتيجة")

        details.append(f"📚 المادة: {subject}")

        if old_note != new_note:
            details.append(
                f"📝 النقطة: "
                f"{old_note or 'غير معلنة'}"
                f" ⟶ "
                f"{new_note or 'غير معلنة'}"
            )
        else:
            details.append(
                f"📝 النقطة: {new_note or 'غير معلنة'}"
            )

        if old_result != new_result:
            details.append(
                f"✅ النتيجة: "
                f"{old_result or 'غير معلنة'}"
                f" ⟶ "
                f"{new_result or 'غير معلنة'}"
            )
        else:
            details.append(
                f"✅ النتيجة: {new_result or 'غير معلنة'}"
            )

        changes.append("\n".join(details))

    # المواد التي اختفت أو حُذفت من الجدول
    for subject, previous in old.items():
        if subject in new:
            continue

        changes.append(
            "\n".join(
                [
                    "🗑️ تم حذف مادة أو اختفاؤها من الجدول",
                    f"📚 المادة: {subject}",
                    (
                        f"📝 النقطة السابقة: "
                        f"{previous.get('note') or 'غير معلنة'}"
                    ),
                    (
                        f"✅ النتيجة السابقة: "
                        f"{previous.get('result') or 'غير معلنة'}"
                    ),
                ]
            )
        )

    return changes


def main() -> None:
    try:
        current_grades = fetch_grades()

    except (
        requests.exceptions.ConnectTimeout,
        requests.exceptions.ReadTimeout,
        requests.exceptions.ConnectionError,
    ) as error:
        print(
            "تعذر الاتصال بـ APoweb حالياً. "
            "سيتم تكرار الفحص في التشغيل القادم."
        )
        print(error)
        return

    except requests.exceptions.RequestException as error:
        print("حدث خطأ أثناء الاتصال بـ APoweb.")
        print(error)
        return

    except RuntimeError as error:
        print(error)
        return

    previous_grades = load_previous_state()

    if not previous_grades:
        save_state(current_grades)

        send_telegram(
            "✅ تم تشغيل مراقب APoweb بنجاح.\n\n"
            "📊 تم حفظ الوضع الحالي للنقط.\n"
            "🔔 سيتم إشعارك عند نشر نقطة جديدة "
            "أو حدوث أي تغيير.\n\n"
            f"🕒 {current_time()}\n\n"
            "🤖 APoWeb Monitor"
        )
        return

    changes = find_changes(
        previous_grades,
        current_grades,
    )

    if changes:
        message = (
            "🎓 FSJES Tanger\n\n"
            + "\n\n━━━━━━━━━━━━━━\n\n".join(changes)
            + f"\n\n🕒 وقت الاكتشاف: {current_time()}"
            + "\n\n🤖 APoWeb Monitor"
        )

        send_telegram(message)
        save_state(current_grades)

        print("تم اكتشاف التغيير وإرسال إشعار Telegram.")

    else:
        print("لا يوجد أي تغيير في النقط.")


if __name__ == "__main__":
    main()
