#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import base64
import mimetypes
import re
from datetime import datetime, timedelta
from pathlib import Path
import uuid
import requests
import streamlit as st

st.set_page_config(page_title="WordPress Novel Auto-Publisher", page_icon="📚", layout="wide")

# ==============================
# SIDEBAR CONFIGURATION
# ==============================
st.sidebar.header("WordPress Settings")

wp_url = st.sidebar.text_input("WordPress URL", value="https://www.asianwebnovels.com")
username = st.sidebar.text_input("Username / Email", value="linhlepersonal@gmail.com")
app_password = st.sidebar.text_input("Application Password", value="", type="password")

category_id = st.sidebar.number_input("Category ID", value=0, step=1, help="Set to 0 to omit category assignment.")
author_id = st.sidebar.number_input("Author ID", value=1395, step=1)
toc_url = st.sidebar.text_input("TOC URL", value="https://asianwebnovels.com/falling-into-you-list-of-chapters/")
title_prefix = st.sidebar.text_input("Title Prefix", value="Пылающая дорожка — ")
post_status = st.sidebar.selectbox("Post Status", options=["draft", "publish", "future", "private"], index=0)

start_date = st.sidebar.date_input("Start Date", value=datetime(2026, 10, 12))
start_time = st.sidebar.time_input("Start Time", value=datetime.strptime("05:11", "%H:%M").time())
interval_minutes = st.sidebar.number_input("Interval between chapters (minutes)", value=1, min_value=0)

# Build Headers
token = base64.b64encode(f"{username}:{app_password}".encode("utf-8")).decode("ascii")
auth_header = f"Basic {token}"


# ==============================
# WORDPRESS API FUNCTIONS
# ==============================

def upload_media_bytes(file_bytes: bytes, filename: str, alt_text: str = "") -> int:
    url = f"{wp_url.rstrip('/')}/wp-json/wp/v2/media"
    mime_type, _ = mimetypes.guess_type(filename)
    if not mime_type:
        mime_type = "application/octet-stream"

    files = {"file": (filename, file_bytes, mime_type)}
    headers = {"Authorization": auth_header}
    resp = requests.post(url, headers=headers, files=files, timeout=60)

    if resp.status_code != 201:
        raise RuntimeError(f"Media upload failed ({resp.status_code}): {resp.text}")

    media_id = resp.json()["id"]

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

    return media_id


def create_post(
    title: str,
    content_html: str,
    status: str = "draft",
    categories=None,
    featured_media_id=None,
    author_id_val=None,
    date_gmt_str: str = None,
) -> int:
    url = f"{wp_url.rstrip('/')}/wp-json/wp/v2/posts"
    payload = {
        "title": title,
        "content": content_html,
        "status": status,
    }
    if categories:
        payload["categories"] = categories
    if author_id_val is not None:
        payload["author"] = author_id_val
    if featured_media_id is not None:
        payload["featured_media"] = featured_media_id
    if date_gmt_str is not None:
        payload["date_gmt"] = date_gmt_str

    headers = {
        "Authorization": auth_header,
        "Content-Type": "application/json; charset=utf-8",
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=60)

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Post creation failed ({resp.status_code}): {resp.text}")

    return resp.json()["id"]


# ==============================
# HELPER FUNCTIONS
# ==============================

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
        processed_blocks.append(
            "<!-- wp:separator -->\n<hr class=\"wp-block-separator has-alpha-channel-opacity\"/>\n<!-- /wp:separator -->\n"
        )
        processed_blocks.append("<!-- wp:footnotes {\"className\":\"wp-block-footnotes\"} /-->\n")

        list_block = "<!-- wp:list {\"ordered\":true,\"className\":\"wp-block-footnotes\"} -->\n<ol class=\"wp-block-footnotes\">"
        for num in sorted(footnote_uuids.keys()):
            u_id = footnote_uuids[num]
            explanation = footnotes_dict[num]
            list_block += f'<li id="{u_id}">{explanation} <a href="#{u_id}-link" aria-label="Jump to footnote {num}">↩︎</a></li>'
        list_block += "</ol>\n<!-- /wp:list -->\n"
        processed_blocks.append(list_block)

    return "\n".join(processed_blocks)


def extract_chapter_number(file_name: str) -> int:
    m = re.search(r"Глава\s*(\d+)", file_name)
    if not m:
        raise ValueError(f"Could not extract chapter number from: {file_name}")
    return int(m.group(1))


def parse_image_chapter_range(file_name: str) -> tuple[int, int] | None:
    """
    Extracts (start_chapter, end_chapter) from image filenames like:
      - 'tiger_1-3.jpg'  -> (1, 3)
      - 'tiger_1-5.png'  -> (1, 5)
      - 'tiger_4.jpg'    -> (4, 4)
      - '4.jpg'          -> (4, 4)
    """
    stem = Path(file_name).stem

    range_match = re.search(r"(?:^|[\s_-])(\d+)\s*-\s*(\d+)$", stem)
    if range_match:
        start_ch = int(range_match.group(1))
        end_ch = int(range_match.group(2))
        if start_ch > end_ch:
            start_ch, end_ch = end_ch, start_ch
        return start_ch, end_ch

    single_end_match = re.search(r"(?:^|[\s_-])(\d+)$", stem)
    if single_end_match:
        ch = int(single_end_match.group(1))
        return ch, ch

    any_num_match = re.search(r"(\d+)", stem)
    if any_num_match:
        ch = int(any_num_match.group(1))
        return ch, ch

    return None


def build_chapter_image_map(image_files) -> tuple[dict[int, object], list[str]]:
    """
    Maps each chapter number to its corresponding UploadedFile.
    Returns (chapter_to_image_dict, warnings_list).
    """
    chapter_map: dict[int, object] = {}
    warnings: list[str] = []

    if not image_files:
        return chapter_map, warnings

    for img in image_files:
        parsed = parse_image_chapter_range(img.name)
        if not parsed:
            warnings.append(f"Could not detect chapter number or range from image `{img.name}`; skipping.")
            continue

        start_ch, end_ch = parsed
        for ch in range(start_ch, end_ch + 1):
            if ch in chapter_map:
                warnings.append(
                    f"Chapter {ch} already matched `{chapter_map[ch].name}`; overwritten by `{img.name}`."
                )
            chapter_map[ch] = img

    return chapter_map, warnings


# ==============================
# MAIN STREAMLIT APP INTERFACE
# ==============================

st.title("📚 WordPress Novel Chapter Uploader")
st.markdown("Upload your chapter text files (`Глава *.txt`) and optional chapter or range images to schedule WordPress posts automatically.")

col1, col2 = st.columns(2)

with col1:
    txt_files = st.file_uploader(
        "Upload Chapter Text Files (.txt)",
        type=["txt"],
        accept_multiple_files=True,
        help="Filenames should match the format 'Глава 1.txt', 'Глава 2.txt', etc.",
    )

with col2:
    image_files = st.file_uploader(
        "Upload Chapter Images (Optional: .jpg, .png)",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=True,
        help="Supports single chapters (e.g. 'tiger_1.jpg') or ranges (e.g. 'tiger_1-3.jpg', 'tiger_1-5.png').",
    )

if st.button("🚀 Publish / Schedule Chapters", type="primary"):
    if not txt_files:
        st.error("Please upload at least one `.txt` chapter file.")
    else:
        image_map, img_warnings = build_chapter_image_map(image_files)
        for warn_msg in img_warnings:
            st.warning(warn_msg)

        uploaded_media_cache: dict[str, int] = {}
        failed_media_filenames: set[str] = set()

        try:
            sorted_txt_files = sorted(txt_files, key=lambda f: extract_chapter_number(f.name))
        except ValueError as e:
            st.error(f"Error parsing chapter numbers: {e}")
            st.stop()

        current_post_time = datetime.combine(start_date, start_time)
        progress_bar = st.progress(0)
        status_box = st.empty()

        total_files = len(sorted_txt_files)

        for idx, text_file in enumerate(sorted_txt_files):
            try:
                chapter_num = extract_chapter_number(text_file.name)
                clean_file_name = Path(text_file.name).stem
                title = f"{title_prefix}{clean_file_name}"
                date_iso = current_post_time.strftime("%Y-%m-%dT%H:%M:%S")

                status_box.info(f"Processing **Chapter {chapter_num}** ({idx + 1}/{total_files})...")

                media_id = None
                if chapter_num in image_map:
                    img_file = image_map[chapter_num]
                    img_key = img_file.name

                    if img_key in uploaded_media_cache:
                        media_id = uploaded_media_cache[img_key]
                    elif img_key not in failed_media_filenames:
                        try:
                            status_box.text(f"Uploading image: {img_file.name}...")
                            parsed_range = parse_image_chapter_range(img_file.name)
                            if parsed_range and parsed_range[0] != parsed_range[1]:
                                alt = f"Chapters {parsed_range[0]}-{parsed_range[1]} Illustration"
                            else:
                                alt = f"Chapter {chapter_num} Illustration"

                            media_id = upload_media_bytes(
                                img_file.getvalue(),
                                img_file.name,
                                alt_text=alt,
                            )
                            uploaded_media_cache[img_key] = media_id
                        except Exception as e:
                            failed_media_filenames.add(img_key)
                            st.warning(f"Failed to upload image `{img_file.name}` for chapter {chapter_num}: {e}")

                raw_text = text_file.getvalue().decode("utf-8")
                content = html_wrap(raw_text)

                if toc_url:
                    content += (
                        f"\n<!-- wp:embed {{\"url\":\"{toc_url}\",\"type\":\"wp-embed\",\"providerNameSlug\":\"asian-webnovels\"}} -->\n"
                        f"<figure class=\"wp-block-embed is-type-wp-embed is-provider-asian-webnovels wp-block-embed-asian-webnovels\"><div class=\"wp-block-embed__wrapper\">\n"
                        f"{toc_url}\n"
                        f"</div></figure>\n"
                        f"<!-- /wp:embed -->\n"
                    )

                post_kwargs = {
                    "title": title,
                    "content_html": content,
                    "status": post_status,
                    "author_id_val": int(author_id),
                    "date_gmt_str": date_iso,
                }

                if int(category_id) > 0:
                    post_kwargs["categories"] = [int(category_id)]

                if media_id is not None:
                    post_kwargs["featured_media_id"] = media_id

                post_id = create_post(**post_kwargs)
                st.success(f"✅ Chapter {chapter_num} scheduled successfully (Post ID: {post_id}) at {date_iso}")

                current_post_time += timedelta(minutes=interval_minutes)

            except Exception as err:
                st.error(f"❌ Error processing chapter file `{text_file.name}`: {err}")

            progress_bar.progress((idx + 1) / total_files)

        status_box.success("🎉 All chapters processed successfully!")
