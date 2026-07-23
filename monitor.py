import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


# =========================================================
# الروابط والإعدادات
# =========================================================

APOWEB_URL = (
    "https://apoweb-ta.uae.ac.ma/"
    "dossier_etudiant_fsjes_tanger/"
)

MEN_ANNOUNCEMENTS_URL = (
    "https://www.men.gov.ma/"
    "%D8%A5%D8%B9%D9%84%D8%A7%D9%86%D8%A7%D8%AA"
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
APO_LOGIN = os.environ["APO_LOGIN"]
APO_PASS = os.environ["APO_PASS"]

STATE_FILE = Path("state.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    )
}

# الكلمات التي تدل على إعلان يخص الامتحان المهني
MEN_KEYWORDS = (
    "الامتحان المهني",
    "الامتحانات المهنية",
    "الكفاءة المهنية",
    "الكفاءة المهني",
    "نتائج الامتحان المهني",
    "نتائج الامتحانات المهنية",
    "الترقية بالشهادة المهنية",
    "examen professionnel",
    "examens professionnels",
    "aptitude professionnelle",
)


# =========================================================
# أدوات عامة
# =========================================================

def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_arabic(text: str) -> str:
    """
    توحيد بعض الحروف العربية لتحسين مطابقة العناوين.
    """
    text = clean_text(text).lower()

    replacements = {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ى": "ي",
        "ة": "ه",
        "ؤ": "و",
        "ئ": "ي",
        "ـ": "",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    # حذف التشكيل
    text = re.sub(
        r"[\u0617-\u061A\u064B-\u0652]",
        "",
        text,
    )

    return text


def current_time() -> str:
    now = datetime.now(ZoneInfo("Africa/Casablanca"))
    return now.strftime("%d/%m/%Y - %H:%M")


def create_http_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


# =========================================================
# Telegram
# =========================================================

def send_telegram(message: str) -> None:
    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message,
            "disable_web_page_preview": False,
        },
        timeout=(30, 60),
    )

    response.raise_for_status()


def send_telegram_document(
    file_path: Path,
    caption: str = "",
) -> None:
    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendDocument"
    )

    with file_path.open("rb") as document:
        response = requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "caption": caption[:1024],
            },
            files={
                "document": (
                    file_path.name,
                    document,
                    "application/pdf",
                )
            },
            timeout=(30, 180),
        )

    response.raise_for_status()


# =========================================================
# إدارة الحالة
# =========================================================

def default_state() -> dict:
    return {
        "apoweb": {
            "initialized": False,
            "grades": {},
        },
        "men": {
            "initialized": False,
            "seen_announcements": [],
            "seen_pdfs": [],
        },
    }


def load_state() -> dict:
    state = default_state()

    if not STATE_FILE.exists():
        return state

    try:
        with STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:
            loaded = json.load(file)

    except (json.JSONDecodeError, OSError):
        return state

    if not isinstance(loaded, dict):
        return state

    # ترحيل state.json القديم الذي كان يحتوي النقط مباشرة
    if "apoweb" not in loaded and "men" not in loaded:
        state["apoweb"]["initialized"] = True
        state["apoweb"]["grades"] = loaded
        return state

    apoweb_state = loaded.get("apoweb", {})
    men_state = loaded.get("men", {})

    if isinstance(apoweb_state, dict):
        state["apoweb"]["initialized"] = bool(
            apoweb_state.get("initialized", False)
        )

        grades = apoweb_state.get("grades", {})
        if isinstance(grades, dict):
            state["apoweb"]["grades"] = grades

    if isinstance(men_state, dict):
        state["men"]["initialized"] = bool(
            men_state.get("initialized", False)
        )

        announcements = men_state.get(
            "seen_announcements",
            [],
        )

        pdfs = men_state.get("seen_pdfs", [])

        if isinstance(announcements, list):
            state["men"]["seen_announcements"] = announcements

        if isinstance(pdfs, list):
            state["men"]["seen_pdfs"] = pdfs

    return state


def save_state(state: dict) -> None:
    with STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            state,
            file,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )


# =========================================================
# مراقبة APOWEB
# =========================================================

def fetch_grades() -> dict:
    session = create_http_session()

    session.get(
        APOWEB_URL,
        timeout=(30, 120),
    )

    response = session.post(
        APOWEB_URL,
        data={
            "Login": APO_LOGIN,
            "pass": APO_PASS,
            "submit": "Login",
        },
        timeout=(30, 120),
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

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
            clean_text(
                cell.get_text(" ", strip=True)
            )
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
            "تم تسجيل الدخول إلى APoweb، "
            "لكن لم يتم العثور على جدول النقط."
        )

    return grades


def find_grade_changes(
    old: dict,
    new: dict,
) -> list[str]:
    changes = []

    for subject, current in new.items():
        new_note = current.get("note", "")
        new_result = current.get("result", "")

        if subject not in old:
            if new_note:
                title = "🔔 تم نشر نقطة جديدة"
            else:
                title = "➕ تمت إضافة مادة جديدة"

            changes.append(
                "\n".join(
                    [
                        title,
                        f"📚 المادة: {subject}",
                        (
                            f"📝 النقطة: "
                            f"{new_note or 'غير معلنة'}"
                        ),
                        (
                            f"✅ النتيجة: "
                            f"{new_result or 'غير معلنة'}"
                        ),
                    ]
                )
            )

            continue

        previous = old[subject]

        old_note = previous.get("note", "")
        old_result = previous.get("result", "")

        if (
            old_note == new_note
            and old_result == new_result
        ):
            continue

        details = []

        if not old_note and new_note:
            details.append("🔔 تم نشر نقطة جديدة")

        elif old_note != new_note:
            details.append("🔄 تم تعديل نقطة")

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
                f"📝 النقطة: "
                f"{new_note or 'غير معلنة'}"
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
                f"✅ النتيجة: "
                f"{new_result or 'غير معلنة'}"
            )

        changes.append("\n".join(details))

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


def monitor_apoweb(state: dict) -> bool:
    """
    تعيد True إذا تغيرت الحالة ويجب حفظ state.json.
    """
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
        return False

    except requests.exceptions.RequestException as error:
        print("حدث خطأ أثناء الاتصال بـ APoweb.")
        print(error)
        return False

    except RuntimeError as error:
        print(error)
        return False

    apoweb_state = state["apoweb"]

    if not apoweb_state["initialized"]:
        apoweb_state["initialized"] = True
        apoweb_state["grades"] = current_grades

        send_telegram(
            "✅ تم تشغيل مراقب APoweb بنجاح.\n\n"
            "📊 تم حفظ الوضع الحالي للنقط.\n"
            "🔔 سيتم إشعارك عند نشر نقطة جديدة "
            "أو حدوث أي تغيير.\n\n"
            f"🕒 {current_time()}\n\n"
            "🤖 APoWeb & MEN Monitor"
        )

        print("تمت تهيئة مراقب APoweb.")
        return True

    previous_grades = apoweb_state["grades"]

    changes = find_grade_changes(
        previous_grades,
        current_grades,
    )

    if not changes:
        print("لا يوجد أي تغيير في نقط APoweb.")
        return False

    message = (
        "🎓 FSJES Tanger\n\n"
        + "\n\n━━━━━━━━━━━━━━\n\n".join(changes)
        + f"\n\n🕒 وقت الاكتشاف: {current_time()}"
        + "\n\n🤖 APoWeb & MEN Monitor"
    )

    send_telegram(message)

    apoweb_state["grades"] = current_grades

    print(
        "تم اكتشاف تغيير في APoweb "
        "وإرسال إشعار Telegram."
    )

    return True


# =========================================================
# مراقبة موقع الوزارة
# =========================================================

def is_professional_exam_title(title: str) -> bool:
    normalized_title = normalize_arabic(title)

    return any(
        normalize_arabic(keyword) in normalized_title
        for keyword in MEN_KEYWORDS
    )


def extract_announcement_title(
    link,
) -> str:
    """
    يحاول استخراج عنوان الإعلان من السطر أو البطاقة
    التي يوجد فيها رابط عرض التفاصيل.
    """
    row = link.find_parent("tr")

    if row:
        cells = [
            clean_text(
                cell.get_text(" ", strip=True)
            )
            for cell in row.select("th, td")
        ]

        cells = [
            cell
            for cell in cells
            if cell
            and "عرض التفاصيل" not in cell
        ]

        # عادة يكون العنوان أطول عنصر نصي في السطر
        if cells:
            return max(cells, key=len)

    # محاولة ثانية مع العناصر الأب
    parent = link.parent

    for _ in range(6):
        if parent is None:
            break

        text = clean_text(
            parent.get_text(" ", strip=True)
        )

        text = text.replace(
            clean_text(
                link.get_text(" ", strip=True)
            ),
            "",
        ).strip()

        if len(text) >= 15:
            # حذف الكلمات والعناوين العامة
            text = re.sub(
                r"^(التاريخ|العنوان|الوثيقة)\s*",
                "",
                text,
            )

            if text:
                return text

        parent = parent.parent

    return ""


def fetch_ministry_announcements() -> list[dict]:
    session = create_http_session()

    response = session.get(
        MEN_ANNOUNCEMENTS_URL,
        timeout=(30, 120),
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    announcements = []
    seen_urls = set()

    for link in soup.find_all("a", href=True):
        link_text = clean_text(
            link.get_text(" ", strip=True)
        )

        href = link.get("href", "").strip()

        if not href:
            continue

        absolute_url = urljoin(
            MEN_ANNOUNCEMENTS_URL,
            href,
        )

        decoded_url = unquote(absolute_url)

        # نريد روابط صفحات تفاصيل الإعلانات فقط
        if "/إعلانات/" not in decoded_url:
            continue

        if absolute_url.rstrip("/") == (
            MEN_ANNOUNCEMENTS_URL.rstrip("/")
        ):
            continue

        if (
            "عرض التفاصيل" not in link_text
            and "details" not in link_text.lower()
        ):
            continue

        if absolute_url in seen_urls:
            continue

        title = extract_announcement_title(link)

        if not title:
            continue

        seen_urls.add(absolute_url)

        announcements.append(
            {
                "title": title,
                "url": absolute_url,
            }
        )

    return announcements


def extract_pdf_links(
    announcement_url: str,
) -> list[dict]:
    session = create_http_session()

    response = session.get(
        announcement_url,
        timeout=(30, 120),
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    pdf_links = []
    seen_urls = set()

    for link in soup.find_all("a", href=True):
        href = link.get("href", "").strip()

        if not href:
            continue

        text = clean_text(
            link.get_text(" ", strip=True)
        )

        absolute_url = urljoin(
            announcement_url,
            href,
        )

        parsed_path = urlparse(
            absolute_url
        ).path.lower()

        appears_to_be_pdf = (
            parsed_path.endswith(".pdf")
            or ".pdf?" in absolute_url.lower()
            or "تنزيل" in text
            or "download" in text.lower()
        )

        if not appears_to_be_pdf:
            continue

        if absolute_url in seen_urls:
            continue

        seen_urls.add(absolute_url)

        pdf_links.append(
            {
                "url": absolute_url,
                "label": text or "وثيقة النتائج",
            }
        )

    return pdf_links


def filename_from_response(
    response: requests.Response,
    fallback_index: int,
) -> str:
    content_disposition = response.headers.get(
        "Content-Disposition",
        "",
    )

    filename_match = re.search(
        r"""filename\*?=(?:UTF-8''|")?([^";]+)""",
        content_disposition,
        flags=re.IGNORECASE,
    )

    if filename_match:
        filename = unquote(
            filename_match.group(1)
        ).strip().strip('"')
    else:
        filename = unquote(
            Path(
                urlparse(response.url).path
            ).name
        )

    if not filename:
        filename = (
            f"resultats_examen_professionnel_"
            f"{fallback_index}.pdf"
        )

    filename = re.sub(
        r'[<>:"/\\|?*\x00-\x1F]',
        "_",
        filename,
    )

    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"

    return filename


def download_pdf(
    pdf_url: str,
    destination_directory: Path,
    index: int,
) -> Path:
    session = create_http_session()

    response = session.get(
        pdf_url,
        timeout=(30, 180),
        stream=True,
        allow_redirects=True,
    )

    response.raise_for_status()

    content_type = response.headers.get(
        "Content-Type",
        "",
    ).lower()

    first_chunk = b""

    chunks = response.iter_content(
        chunk_size=1024 * 256,
    )

    try:
        first_chunk = next(chunks)
    except StopIteration:
        raise RuntimeError(
            "الوثيقة التي أرسلتها الوزارة فارغة."
        )

    # التأكد أن الرابط يقود فعلًا إلى PDF
    is_pdf = (
        "application/pdf" in content_type
        or first_chunk.startswith(b"%PDF")
    )

    if not is_pdf:
        raise RuntimeError(
            "رابط التنزيل لا يقود إلى ملف PDF صالح."
        )

    filename = filename_from_response(
        response,
        index,
    )

    file_path = (
        destination_directory
        / filename
    )

    with file_path.open("wb") as file:
        file.write(first_chunk)

        for chunk in chunks:
            if chunk:
                file.write(chunk)

    return file_path


def monitor_ministry(state: dict) -> bool:
    """
    تراقب إعلانات الوزارة.
    تعيد True عندما تتغير الحالة.
    """
    try:
        announcements = fetch_ministry_announcements()

    except (
        requests.exceptions.ConnectTimeout,
        requests.exceptions.ReadTimeout,
        requests.exceptions.ConnectionError,
    ) as error:
        print(
            "تعذر الاتصال بموقع الوزارة حالياً. "
            "سيتم تكرار الفحص في التشغيل القادم."
        )
        print(error)
        return False

    except requests.exceptions.RequestException as error:
        print(
            "حدث خطأ أثناء قراءة صفحة إعلانات الوزارة."
        )
        print(error)
        return False

    relevant_announcements = [
        announcement
        for announcement in announcements
        if is_professional_exam_title(
            announcement["title"]
        )
    ]

    men_state = state["men"]

    seen_announcements = set(
        men_state["seen_announcements"]
    )

    seen_pdfs = set(
        men_state["seen_pdfs"]
    )

    # أول تشغيل: حفظ الوضع الحالي دون إرسال القديم
    if not men_state["initialized"]:
        for announcement in relevant_announcements:
            seen_announcements.add(
                announcement["url"]
            )

            try:
                pdf_links = extract_pdf_links(
                    announcement["url"]
                )

                for pdf in pdf_links:
                    seen_pdfs.add(pdf["url"])

            except requests.exceptions.RequestException:
                # لا نوقف التهيئة إذا تعذر فتح إعلان قديم
                pass

        men_state["initialized"] = True
        men_state["seen_announcements"] = sorted(
            seen_announcements
        )
        men_state["seen_pdfs"] = sorted(
            seen_pdfs
        )

        send_telegram(
            "✅ تم تشغيل مراقب وزارة التربية الوطنية.\n\n"
            "📢 تم حفظ الإعلانات الحالية المتعلقة "
            "بالامتحان المهني.\n"
            "📄 سيتم إرسال أي ملف PDF جديد عند نشره.\n\n"
            f"🕒 {current_time()}\n\n"
            "🤖 APoWeb & MEN Monitor"
        )

        print("تمت تهيئة مراقب موقع الوزارة.")
        return True

    state_changed = False

    # ترتيب معكوس حتى يتم إرسال الأقدم أولًا عند ظهور أكثر من إعلان
    for announcement in reversed(
        relevant_announcements
    ):
        announcement_url = announcement["url"]
        title = announcement["title"]

        is_new_announcement = (
            announcement_url
            not in seen_announcements
        )

        try:
            pdf_links = extract_pdf_links(
                announcement_url
            )

        except requests.exceptions.RequestException as error:
            print(
                "تعذر فتح إعلان الوزارة:"
            )
            print(announcement_url)
            print(error)
            continue

        new_pdfs = [
            pdf
            for pdf in pdf_links
            if pdf["url"] not in seen_pdfs
        ]

        if not is_new_announcement and not new_pdfs:
            continue

        # إرسال إشعار الإعلان
        if is_new_announcement:
            send_telegram(
                "🚨 إعلان جديد من وزارة التربية الوطنية\n\n"
                f"📢 {title}\n\n"
                f"🔗 {announcement_url}\n\n"
                f"🕒 {current_time()}\n\n"
                "🤖 APoWeb & MEN Monitor"
            )

            seen_announcements.add(
                announcement_url
            )

            state_changed = True

        if not new_pdfs:
            print(
                "تم اكتشاف الإعلان، "
                "لكن لا يوجد PDF مرفق حتى الآن."
            )
            continue

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            for index, pdf in enumerate(
                new_pdfs,
                start=1,
            ):
                try:
                    file_path = download_pdf(
                        pdf["url"],
                        temp_path,
                        index,
                    )

                    caption = (
                        "📄 نتائج الامتحان المهني\n\n"
                        f"📢 {title}\n\n"
                        f"🕒 {current_time()}"
                    )

                    send_telegram_document(
                        file_path,
                        caption,
                    )

                    seen_pdfs.add(pdf["url"])
                    state_changed = True

                    print(
                        "تم تحميل ملف الوزارة "
                        "وإرساله إلى Telegram:"
                    )
                    print(file_path.name)

                except (
                    requests.exceptions.RequestException,
                    RuntimeError,
                    OSError,
                ) as error:
                    print(
                        "تعذر تحميل أو إرسال "
                        "ملف PDF:"
                    )
                    print(pdf["url"])
                    print(error)

    men_state["seen_announcements"] = sorted(
        seen_announcements
    )

    men_state["seen_pdfs"] = sorted(
        seen_pdfs
    )

    if not state_changed:
        print(
            "لا يوجد إعلان مهني أو PDF جديد "
            "في موقع الوزارة."
        )

    return state_changed


# =========================================================
# التشغيل الرئيسي
# =========================================================

def main() -> None:
    state = load_state()

    state_changed = False

    # فشل أحد الموقعين لا يمنع مراقبة الموقع الآخر
    try:
        if monitor_apoweb(state):
            state_changed = True

    except requests.exceptions.RequestException as error:
        print(
            "خطأ غير متوقع أثناء مراقبة APoweb:"
        )
        print(error)

    try:
        if monitor_ministry(state):
            state_changed = True

    except requests.exceptions.RequestException as error:
        print(
            "خطأ غير متوقع أثناء مراقبة الوزارة:"
        )
        print(error)

    if state_changed:
        save_state(state)
        print("تم تحديث state.json.")

    else:
        print("لا توجد تغييرات جديدة لحفظها.")


if __name__ == "__main__":
    main()
