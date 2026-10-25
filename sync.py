#!/usr/bin/env python3
"""Archive the current items from a YUTorah teacher's RSS feed to rclone."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

import feedparser
import requests
from torah_dl import extract as torah_dl_extract

from categories import Routing, load_routing


RSS_URL = "https://www.yutorah.org/rss/RssAudioOnly/teacher/{teacher_id}"
REQUEST_TIMEOUT = (15, 120)
CHUNK_SIZE = 1024 * 1024
USER_AGENT = "yutorah-drive-archiver/1.0"
UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')
CONTROL_FILENAME_CHARS = re.compile(r"[\x00-\x1f\x7f]")
WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Shiur:
    shiur_id: str
    title: str
    page_url: str
    mp3_url: str | None


@dataclass(frozen=True)
class SyncResult:
    found: int
    skipped: int
    uploaded: int
    failed: int


def extract_shiur_id(*urls: str | None) -> str | None:
    """Extract a shiur ID from common YUTorah page and media URL formats."""
    for url in urls:
        if not url:
            continue

        parsed = urlparse(str(url))
        query = {key.lower(): value for key, value in parse_qs(parsed.query).items()}
        for key in ("shiurid", "shiur_id", "lectureid", "lecture_id"):
            values = query.get(key)
            if values and values[0].isdigit():
                return values[0]

        path = parsed.path
        patterns = (
            r"(?i)/lectures/(?:lecture\.cfm/|details/)?(\d+)(?:/|$)",
            r"(?i)/(\d+)\.mp3$",
            r"(?i)/(\d+)/[^/]*\.mp3$",
        )
        for pattern in patterns:
            match = re.search(pattern, path)
            if match:
                return match.group(1)

        # Some older links embed the query-like value outside a conventional query.
        match = re.search(r"(?i)(?:shiur|lecture)_?id[=/](\d+)", str(url))
        if match:
            return match.group(1)

    return None


def _entry_enclosure_url(entry: Any) -> str | None:
    candidates: Iterable[Any] = entry.get("enclosures", []) or entry.get("links", [])
    for enclosure in candidates:
        relation = str(enclosure.get("rel", "enclosure")).lower()
        media_type = str(enclosure.get("type", "")).lower()
        url = enclosure.get("href") or enclosure.get("url")
        if url and relation == "enclosure" and (
            media_type.startswith("audio/") or urlparse(str(url)).path.lower().endswith(".mp3")
        ):
            return str(url)
    return None


def parse_feed(content: bytes | str) -> list[Shiur]:
    """Parse every usable RSS item into a small, normalized record."""
    feed = feedparser.parse(content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"Unable to parse YUTorah RSS feed: {feed.bozo_exception}")

    shiurim: list[Shiur] = []
    for entry in feed.entries:
        page_url = str(entry.get("link") or "")
        guid = str(entry.get("id") or entry.get("guid") or "")
        mp3_url = _entry_enclosure_url(entry)
        shiur_id = extract_shiur_id(page_url, guid, mp3_url)
        if not shiur_id:
            logging.warning("Skipping RSS item with no recognizable shiur ID: %s", entry.get("title", "untitled"))
            continue

        # feedparser may expose a GUID media URL as ``link`` when <link> is absent.
        if not page_url or urlparse(page_url).path.lower().endswith(".mp3"):
            page_url = f"https://www.yutorah.org/lectures/details?shiurid={shiur_id}"
        title = str(entry.get("title") or f"Shiur {shiur_id}").strip()
        shiurim.append(Shiur(shiur_id, title, page_url, mp3_url))

    return shiurim


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def sanitize_title(title: str, max_bytes: int = 200) -> str:
    """Make a cross-platform-safe title without discarding Hebrew or Unicode."""
    value = unicodedata.normalize("NFC", title)
    value = CONTROL_FILENAME_CHARS.sub(" ", value)
    value = UNSAFE_FILENAME_CHARS.sub("_", value)
    value = WHITESPACE.sub(" ", value).strip(" .")
    value = _truncate_utf8(value, max_bytes).rstrip(" .")
    return value or "Untitled shiur"


def archive_filename(shiur: Shiur) -> str:
    prefix = f"{shiur.shiur_id} - "
    max_title_bytes = 240 - len(prefix.encode("utf-8")) - len(".mp3")
    return f"{prefix}{sanitize_title(shiur.title, max_title_bytes)}.mp3"


def list_remote_filenames(destination: str) -> list[str]:
    completed = subprocess.run(
        ["rclone", "lsf", destination, "--files-only", "--recursive"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.rstrip("\r") for line in completed.stdout.splitlines() if line]


def is_archived(shiur_id: str, remote_filenames: Iterable[str]) -> bool:
    prefix = f"{shiur_id} -"
    return any(Path(name).name.startswith(prefix) for name in remote_filenames)


def _remote_file(destination: str, filename: str) -> str:
    separator = "" if destination.endswith((":", "/")) else "/"
    return f"{destination}{separator}{filename}"


def upload_file(local_file: Path, destination: str) -> None:
    subprocess.run(
        ["rclone", "copyto", str(local_file), _remote_file(destination, local_file.name)],
        check=True,
    )


def stream_download(session: requests.Session, url: str, destination: Path) -> None:
    partial = destination.with_suffix(destination.suffix + ".part")
    try:
        with session.get(
            url,
            stream=True,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        ) as response:
            response.raise_for_status()
            with partial.open("wb") as output:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        output.write(chunk)
        partial.replace(destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def fallback_download_url(page_url: str) -> str:
    extraction = torah_dl_extract(page_url)
    url = getattr(extraction, "download_url", None)
    if not url:
        raise ValueError("torah-dl did not return a download URL")
    return str(url)


def download_shiur(session: requests.Session, shiur: Shiur, destination: Path) -> None:
    """Prefer the feed enclosure, falling back to torah-dl extraction."""
    enclosure_error: Exception | None = None
    if shiur.mp3_url:
        try:
            stream_download(session, shiur.mp3_url, destination)
            return
        except Exception as exc:  # Continue into the fallback for this item.
            enclosure_error = exc
            logging.warning("RSS enclosure failed for shiur %s; trying torah-dl: %s", shiur.shiur_id, exc)

    fallback_url = fallback_download_url(shiur.page_url)
    if fallback_url == shiur.mp3_url and enclosure_error:
        raise enclosure_error
    stream_download(session, fallback_url, destination)


def fetch_feed(session: requests.Session, teacher_id: str) -> list[Shiur]:
    url = RSS_URL.format(teacher_id=teacher_id)
    response = session.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return parse_feed(response.content)


def sync_teacher(teacher_id: str, teacher_name: str, destination: str, routing: Routing | None = None) -> SyncResult:
    if routing is None:
        routing = load_routing(teacher_id)
    session = requests.Session()
    shiurim = fetch_feed(session, teacher_id)
    remote_filenames = list_remote_filenames(destination)
    skipped = uploaded = failed = 0

    logging.info("Found %d current RSS items for %s", len(shiurim), teacher_name)
    with tempfile.TemporaryDirectory(prefix="yutorah-") as temporary_directory:
        download_directory = Path(temporary_directory)
        for shiur in shiurim:
            if is_archived(shiur.shiur_id, remote_filenames):
                skipped += 1
                logging.info("Already archived: %s - %s", shiur.shiur_id, shiur.title)
                continue

            filename = archive_filename(shiur)
            category = routing.category(shiur.shiur_id, shiur.title)
            local_file = download_directory / filename
            try:
                logging.info("Downloading: %s", filename)
                download_shiur(session, shiur, local_file)
                logging.info("Uploading to %s: %s", category, filename)
                upload_file(local_file, _remote_file(destination, category))
                remote_filenames.append(f"{category}/{filename}")
                uploaded += 1
            except Exception:
                failed += 1
                logging.exception("Failed to archive shiur %s; continuing", shiur.shiur_id)
            finally:
                local_file.unlink(missing_ok=True)

    return SyncResult(len(shiurim), skipped, uploaded, failed)


def required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Required environment variable {name} is not set")
    return value


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        teacher_id = required_environment("YU_TORAH_TEACHER_ID")
        teacher_name = required_environment("YU_TORAH_TEACHER_NAME")
        destination = required_environment("GDRIVE_DESTINATION")
        result = sync_teacher(teacher_id, teacher_name, destination)
    except Exception:
        logging.exception("Sync could not run")
        return 1

    logging.info(
        "Finished: %d found, %d already archived, %d uploaded, %d failed",
        result.found,
        result.skipped,
        result.uploaded,
        result.failed,
    )
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
