import streamlit as st
import os
import re
import csv
import time
import shutil
import base64
import mimetypes
from pathlib import Path
from datetime import datetime, timedelta
import requests
import uuid

# =====================================================
# STREAMLIT UI CONFIGURATION
# =====================================================
st.set_page_config(page_title="WP Chapter Publisher", page_icon="🚀", layout="centered")

st.title("🚀 WordPress Bulk Chapter Publisher")
st.write("Загрузите файлы глав (`.txt`) и опционально иллюстрации. Скрипт отформатирует текст со сносками, добавит картинку в начало текста, оглавление и создаст публикации на WordPress.")

# =====================================================
# SIDEBAR / CONFIGURATION INPUTS
# =====================================================
st.sidebar.header("Настройки WordPress")
wp_url = st.sidebar.text_input("WP URL (например, https://mysite.com)", value="")
username = st.sidebar.text_input("Username", value="")
app_password = st.sidebar.text_input("Application Password", type="password", value="")

st.sidebar.header("Параметры публикации")
post_status = st.sidebar.selectbox(
    "Статус поста",
    options=["future", "draft"],
    index=0,
    format_func=lambda x: "Запланировано (future)" if x == "future" else "Черновик (draft)"
)
category_id = st.sidebar.number_input("Category ID", value=1072)
author_id = st.sidebar.number_input("Author ID", value=975)
toc_url = st.sidebar.text_input("TOC URL", value="ссылка на страницу глав")
title_prefix = st.sidebar.text_input("Префикс заголовка", value="Летающий скальпель — ")
img_caption_prefix = st.sidebar.text_input("Текст/подпись для картинки в начале", value="Иллюстрация к главе")

st.sidebar.header("Расписание (актуально для статуса 'Future')")
start_date = st.sidebar.date_input("Дата первой публикации", value=datetime(2026, 10, 9).date())
start_time = st.sidebar.time_input("Время первой публикации", value=datetime.strptime("05:21:00", "%H:%M:%S").time())
interval_minutes = st.sidebar.number_input("Интервал между главами (минуты)", min_value=1, value=1)

# =====================================================
# FILE UPLOADERS
# =====================================================
st.subheader("1. Загрузка текста глав (.txt)")
uploaded_txt_files = st.file_uploader("Загрузите файлы глав (например, 'Глава 1.txt')", type=["txt"], accept_multiple_files=True)

st.subheader("2. Загрузка иллюстраций (опционально)")
st.write("Имена файлов должны строго соответствовать формату: `ashes_of_love_chapter_X.jpg` (или .png), где X — номер главы.")
uploaded_img_files = st.file_uploader("Загрузите картинки для глав", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True)

# =====================================================
# HELPER FUNCTIONS
# =====================================================
def upload_media(wp_url, auth_header, image_path: Path, alt_text: str = "") -> tuple[int, str]:
    url = f"{wp_url.rstrip('/')}/wp-json/wp/v2/media"
    filename = image_path.name
    mime_type, _ = mimetypes.guess_type(filename)
    if not mime_type:
        mime_type = "application/octet-stream"

    with open(image_path, "rb") as f:
        files = {"file": (filename, f, mime_type)}
        headers = {"Authorization": auth_header}
        resp = requests.post(url, headers=headers, files=files, timeout=60)

    if resp.status_code != 201:
        raise RuntimeError(f"Media upload failed: {resp.status_code} {resp.text}")

    resp_data = resp.json()
    media_id = resp_data["id"]
    source_url = resp_data.get("source_url", "")

    if alt_text:
        meta_url = f"{wp_url.rstrip('/')}/wp-json/wp/v2/media/{media_id}"
        meta_resp = requests.post(
            meta_url,
            headers={
                "Authorization": auth_header,
                "Content-Type": "application/json; charset=utf-8",
            },
            json={"alt_text": alt_text},
            timeout=30,
        )
        meta_resp.raise_for_status()

    return media_id, source_url

def create_post(wp_url, auth_header, title: str, content_html: str, status: str = "future", categories=None, featured_media_id=None, author_id=None, date_gmt_str: str = None) -> int:
    if categories is None: categories = []
    url = f"{wp_url.rstrip('/')}/wp-json/wp/v2/posts"
    payload = {"title": title, "content": content_html, "status": status, "categories": categories}
    if author_id is not None: payload["author"] = author_id
    if featured_media_id is not None: payload["featured_media"] = featured_media_id
    if date_gmt_str is not None and status == "future": payload["date_gmt"] = date_gmt_str

    headers = {"Authorization": auth_header, "Content-Type": "application/json; charset=utf-8"}
    resp = requests.post(url, headers=headers, json=payload, timeout=60)

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Post creation failed: {resp.status_code} {resp.text}")

    return resp.json()["id"]

def html_wrap(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n")]
    
    story_lines = []
    footnotes_dict = {}
    
    footnote_def_pattern = re.compile(r"^(?:\[\^?(\d+)\]|(\d+)\s*\.|\s*([¹²]))\s*(?:\:)?\s*(.*)")

    for line in lines:
        if not line:
            continue
        
        match = footnote_def_pattern.match(line)
        if match:
            num_str = match.group(1) or match.group(2) or match.group(3)
            if num_str == "¹":
                num = 1
            elif num_str == "²":
                num = 2
            else:
                num = int(num_str)
                
            content = match.group(4).strip()
            footnotes_dict[num] = content
            continue
        
        story_lines.append(line)

    footnote_uuids = {num: str(uuid.uuid4()) for num in footnotes_dict.keys()}
    processed_blocks = []

    for line in story_lines:
        for num, u_id in footnote_uuids.items():
            sign = "¹" if num == 1 else ("²" if num == 2 else None)
            wp_sup = f'<sup data-fn="{u_id}" class="fn"><a id="{u_id}-link" href="#{u_id}">{num}</a></sup>'
            
            line = re.sub(rf"\[\^{num}\]", wp_sup, line)
            line = re.sub(rf"\[{num}\]", wp_sup, line)
            if sign:
                line = re.sub(re.escape(sign), wp_sup, line)

        block = f"<!-- wp:paragraph -->\n<p>{line}</p>\n<!-- /wp:paragraph -->\n"
        processed_blocks.append(block)

    if footnote_uuids:
        processed_blocks.append("<!-- wp:separator -->\n<hr class=\"wp-block-separator has-alpha-channel-opacity\"/>\n<!-- /wp:separator -->\n")
        processed_blocks.append("<!-- wp:footnotes {\"className\":\"wp-block-footnotes\"} /-->\n")
        
        list_block = "<!-- wp:list {\"ordered\":true,\"className\":\"wp-block-footnotes\"} -->\n<ol class=\"wp-block-footnotes\">"
        for num in sorted(footnote_uuids.keys()):
            u_id = footnote_uuids[num]
            explanation = footnotes_dict[num]
            list_block += f'<li id="{u_id}">{explanation} <a href="#{u_id}-link" aria-label="Jump to footnote {num}">↩︎</a></li>'
        list_block += "</ol>\n<!-- /wp:list -->\n"
        processed_blocks.append(list_block)

    return "\n".join(processed_blocks)

def extract_chapter_number(filename: str) -> int:
    m = re.match(r"Глава\s*(\d+)", filename)
    if not m:
        raise ValueError(f"Не удалось извлечь номер главы из файла: {filename}")
    return int(m.group(1))

# =====================================================
# MAIN EXECUTION BUTTON
# =====================================================
if st.button("Запустить публикацию в WordPress"):
    if not uploaded_txt_files:
        st.error("Пожалуйста, загрузите хотя бы один файл главы (.txt).")
    elif not wp_url or not username or not app_password:
        st.error("Пожалуйста, заполните все настройки WordPress (URL, Username, Application Password).")
    else:
        input_dir = Path("temp_wp_input")
        if input_dir.exists():
            shutil.rmtree(input_dir)
        input_dir.mkdir(parents=True, exist_ok=True)

        # Save txt files
        for f in uploaded_txt_files:
            with open(input_dir / f.name, "wb") as file_out:
                file_out.write(f.getbuffer())

        # Save image files if any
        if uploaded_img_files:
            for f in uploaded_img_files:
                with open(input_dir / f.name, "wb") as file_out:
                    file_out.write(f.getbuffer())

        # Prepare Auth
        token = base64.b64encode(f"{username}:{app_password}".encode("utf-8")).decode("ascii")
        auth_header = f"Basic {token}"

        txt_paths = [input_dir / f.name for f in uploaded_txt_files if f.name.lower().endswith(".txt")]
        txt_paths = sorted(txt_paths, key=lambda p: extract_chapter_number(p.name))

        current_post_time = datetime.combine(start_date, start_time)
        
        status_container = st.empty()
        log_area = st.container()
        progress_bar = st.progress(0)

        success_count = 0
        total_files = len(txt_paths)

        with log_area:
            for idx, text_path in enumerate(txt_paths, 1):
                try:
                    chapter_num = extract_chapter_number(text_path.name)
                    clean_file_name = text_path.stem
                    title = f"{title_prefix}{clean_file_name}"
                    date_iso = current_post_time.strftime("%Y-%m-%dT%H:%M:%S")

                    st.write(f"📌 Обработка главы {chapter_num}: **{title}** (Статус: {post_status}, Дата: {date_iso if post_status == 'future' else 'Н/Д'})")

                    # Check image with new prefix
                    base_name = f"ashes_of_love_chapter_{chapter_num}"
                    image_path = None
                    for ext in [".jpg", ".png", ".jpeg", ".webp"]:
                        candidate = input_dir / f"{base_name}{ext}"
                        if candidate.exists():
                            image_path = candidate
                            break

                    media_id = None
                    source_url = None
                    if image_path:
                        try:
                            st.write(f"   -> Загрузка изображения: {image_path.name}")
                            alt_text_val = f"Chapter {chapter_num} Illustration"
                            media_id, source_url = upload_media(wp_url, auth_header, image_path, alt_text=alt_text_val)
                        except Exception as e:
                            st.warning(f"   -> Ошибка загрузки картинки: {e}. Публикация без изображения.")

                    raw_text = text_path.read_text(encoding="utf-8")
                    content = html_wrap(raw_text)

                    # Insert image at the beginning of content if uploaded
                    if media_id and source_url:
                        caption_text = f"{img_caption_prefix} {chapter_num}"
                        image_block = (
                            f'<!-- wp:image {{"id":{media_id},"sizeSlug":"large","linkDestination":"none"}} -->\n'
                            f'<figure class="wp-block-image size-large"><img src="{source_url}" alt="Chapter {chapter_num}" class="wp-image-{media_id}"/><figcaption class="wp-element-caption">{caption_text}</figcaption></figure>\n'
                            f'<!-- /wp:image -->\n\n'
                        )
                        content = image_block + content

                    # Add TOC embed
                    content += (
                        f"\n<!-- wp:embed {{\"url\":\"{toc_url}\",\"type\":\"wp-embed\",\"providerNameSlug\":\"asian-webnovels\"}} -->\n"
                        f"<figure class=\"wp-block-embed is-type-wp-embed is-provider-asian-webnovels wp-block-embed-asian-webnovels\"><div class=\"wp-block-embed__wrapper\">\n"
                        f"{toc_url}\n"
                        f"</div></figure>\n"
                        f"<!-- /wp:embed -->\n"
                    )

                    post_kwargs = {
                        "wp_url": wp_url,
                        "auth_header": auth_header,
                        "title": title,
                        "content_html": content,
                        "status": post_status,
                        "categories": [int(category_id)],
                        "author_id": int(author_id),
                        "date_gmt_str": date_iso,
                    }

                    if media_id is not None:
                        post_kwargs["featured_media_id"] = media_id

                    post_id = create_post(**post_kwargs)
                    st.success(f"   -> Успешно создано! ID поста: {post_id}")
                    success_count += 1

                except Exception as e:
                    st.error(f"   -> Ошибка с файлом {text_path.name}: {e}")

                current_post_time += timedelta(minutes=int(interval_minutes))
                progress_bar.progress(idx / total_files)

        st.success(f"Готово! Успешно обработано глав: {success_count} из {total_files}.")
