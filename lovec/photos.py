"""Фото оголошення: знайти URL на сторінці, скачати повнорозмірні, закешувати, віддати zip.

Порядок пошуку URL (зберігаємо порядок як на сайті):
  1) структуровані дані: JSON-LD image, __NEXT_DATA__ / інші JSON у <script> — масиви фото оголошення;
  2) og:image;
  3) галерея з <img>/<a>/<source srcset>.
Якщо (1) дав фото — (3) не беремо: там часто «схожі оголошення» і реклама.

Кеш: <data>/photos/<listing_id>/01.jpg … + manifest.json. Скачане вдруге не качаємо.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from .http import UA

log = logging.getLogger(__name__)

MIN_BYTES = 12 * 1024
MAX_PHOTOS = 40
WORKERS = 6

IMG_EXT = re.compile(r"\.(jpe?g|png|webp)(?:$|[?#])", re.I)
JUNK = re.compile(r"(logo|icon|sprite|avatar|placeholder|blank|pixel|spacer|banner|badge|flag|favicon|"
                  r"emoji|loader|loading|no[-_]?image|noimage|watermark|/ads?/|adserver|doubleclick|"
                  r"googlesyndication|facebook\.com/tr|qr[-_]?code|maps\.google|staticmap|/agents?/|"
                  r"/makler|/broker|/user[-_]?photo|profile)", re.I)
JUNK_HOSTS = ("google", "doubleclick", "facebook", "fbcdn", "gstatic", "gemius", "hotjar", "criteo")
THUMB_HINT = re.compile(r"(thumb|_s\.|_xs\.|_sm\.|small|mini|/\d{2,3}x\d{2,3}/|[?&](w|width|h|height)=\d{2,3}(&|$))", re.I)
SKIP_KEYS = re.compile(r"(similar|related|recommend|other|promo|advert[iy]ser|agent|broker|seller|user|logo|"
                       r"banner|ads?$|partner|developer|project|author|company)", re.I)
PHOTO_KEYS = ("photos", "images", "gallery", "media", "pictures", "photoList", "imageUrls", "image")


class PhotoError(Exception):
    """kind: not_found | no_photos | site_down"""

    def __init__(self, kind: str, msg: str = ""):
        super().__init__(msg or kind)
        self.kind = kind


MESSAGES = {
    "not_found": "оголошення не знайдено",
    "no_photos": "фото не знайдено",
    "site_down": "сайт не відповідає, спробуй пізніше",
}


# ---------------------------------------------------------------- витяг URL

def _full_size(url: str, source: str = "") -> str:
    """Мініатюра -> повнорозмірне, де формат відомий."""
    # Bazoš: /img/1t/480/196719480.jpg -> /img/1/480/196719480.jpg (1, 2, 3… — номер фото)
    url = re.sub(r"(bazos\.\w+/img/\d+)t/", r"\1/", url)
    # srcset/CDN-ресайзи: …/fit-in/300x200/… або _300x200. — прибираємо розмір, якщо він у шляху
    url = re.sub(r"/(?:fit-in/)?\d{2,3}x\d{2,3}/(?=[^/]+\.(?:jpe?g|png|webp))", "/", url, flags=re.I)
    return url


def _is_photo_url(u: str) -> bool:
    if not u or u.startswith("data:"):
        return False
    p = urlsplit(u)
    if p.scheme not in ("http", "https"):
        return False
    if any(h in p.netloc for h in JUNK_HOSTS):
        return False
    if JUNK.search(u):
        return False
    if re.search(r"\.(svg|gif|ico)(?:$|[?#])", u, re.I):
        return False
    # без розширення теж буває (CDN), але тоді має бути «img/photo/image» у шляху
    return bool(IMG_EXT.search(u) or re.search(r"/(img|image|images|photo|photos|foto|media)/", p.path, re.I))


def _largest_from_srcset(srcset: str) -> str:
    best, best_w = "", -1
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        w = 0
        if len(bits) > 1:
            m = re.match(r"(\d+)(w|x)", bits[1])
            if m:
                w = int(m.group(1)) * (1000 if m.group(2) == "x" else 1)
        if w > best_w:
            best, best_w = bits[0], w
    return best


def _walk_json_photos(obj, out: list[str], depth: int = 0, in_photo_key: bool = False) -> None:
    """Збирає рядки-URL фото з JSON. Пріоритет — значення під ключами photos/images/gallery."""
    if depth > 14 or len(out) > 200:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if SKIP_KEYS.search(str(k)):
                continue  # «схожі оголошення», реклама, агент — не фото цього оголошення
            hot = in_photo_key or k in PHOTO_KEYS
            # у фото-об'єктах беремо найбільший варіант: original/large/full/url
            if hot and isinstance(v, dict):
                for kk in ("original", "originalUrl", "full", "fullUrl", "large", "big", "xl", "url", "src", "href"):
                    if isinstance(v.get(kk), str):
                        out.append(v[kk])
                        break
            _walk_json_photos(v, out, depth + 1, hot)
    elif isinstance(obj, list):
        for v in obj:
            if in_photo_key and isinstance(v, dict):
                for kk in ("original", "originalUrl", "full", "fullUrl", "large", "big", "xl", "url", "src", "href"):
                    if isinstance(v.get(kk), str):
                        out.append(v[kk])
                        break
                else:
                    _walk_json_photos(v, out, depth + 1, in_photo_key)
            else:
                _walk_json_photos(v, out, depth + 1, in_photo_key)
    elif isinstance(obj, str) and in_photo_key and obj.startswith(("http", "//", "/")):
        out.append(obj)


def extract_image_urls(html: str, page_url: str, source: str = "", ext_id: str = "") -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    structured: list[str] = []

    # 1a) JSON-LD
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        _walk_json_photos(data, structured)
    # 1b) Next.js / інші JSON-скрипти
    for s in soup.find_all("script"):
        txt = s.string or ""
        if s.get("id") == "__NEXT_DATA__" or (s.get("type") == "application/json" and len(txt) > 50):
            try:
                _walk_json_photos(json.loads(txt), structured)
            except (json.JSONDecodeError, TypeError):
                continue

    og = [m.get("content", "") for m in soup.find_all("meta", attrs={"property": re.compile(r"^og:image(:url)?$")})]

    dom: list[str] = []
    if not structured:
        for el in soup.find_all(["img", "source", "a"]):
            if el.find_parent(["header", "footer", "nav"]):
                continue
            if el.name == "a":
                href = el.get("href", "")
                if IMG_EXT.search(href):
                    dom.append(href)
                continue
            for attr in ("data-full", "data-original", "data-large", "data-zoom", "data-src", "src"):
                if el.get(attr):
                    dom.append(el[attr])
                    break
            for attr in ("data-srcset", "srcset"):
                if el.get(attr):
                    dom.append(_largest_from_srcset(el[attr]))

    seen, out = set(), []
    for raw in og + structured + dom:
        if not raw:
            continue
        u = _full_size(urljoin(page_url, raw.strip()), source)
        if not _is_photo_url(u):
            continue
        # Bazoš: на сторінці є мініатюри чужих оголошень — беремо лише фото з цим id
        if source == "bazos" and ext_id and f"/{ext_id}." not in u:
            continue
        key = re.sub(r"[?#].*$", "", u)
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
    # мініатюри в кінець: якщо є повнорозмірні — вони головні
    out.sort(key=lambda x: bool(THUMB_HINT.search(x)))
    return out[:MAX_PHOTOS]


# ---------------------------------------------------------------- скачування / кеш

def photos_dir(db_path: str) -> Path:
    return Path(db_path).resolve().parent / "photos"


def _download_one(client: httpx.Client, url: str) -> tuple[str, bytes | None, str]:
    try:
        r = client.get(url)
        if r.status_code != 200:
            return url, None, ""
        ctype = r.headers.get("content-type", "")
        if ctype and not ctype.startswith("image/"):
            return url, None, ""
        return url, r.content, ctype
    except httpx.HTTPError as e:
        log.info("photo %s: %s", url, e)
        return url, None, ""


def _ext(ctype: str, url: str) -> str:
    for kind in ("png", "webp"):
        if kind in ctype or url.lower().split("?")[0].endswith("." + kind):
            return "." + kind
    return ".jpg"


def download(urls: list[str], dest: Path, referer: str) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": UA, "Referer": referer, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}
    with httpx.Client(headers=headers, timeout=30, follow_redirects=True) as client, \
            ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda u: _download_one(client, u), urls))  # map зберігає порядок
    files, hashes = [], set()
    for url, data, ctype in results:
        if not data or len(data) < MIN_BYTES:
            continue  # іконки/мініатюри/порожні
        h = hashlib.sha1(data).hexdigest()
        if h in hashes:
            continue  # той самий файл під різними URL
        hashes.add(h)
        p = dest / f"{len(files) + 1:02d}{_ext(ctype, url)}"
        p.write_bytes(data)
        files.append(p)
    (dest / "manifest.json").write_text(json.dumps({"files": [f.name for f in files], "source_urls": urls},
                                                   ensure_ascii=False), encoding="utf-8")
    return files


def cached(dest: Path) -> list[Path] | None:
    m = dest / "manifest.json"
    if not m.exists():
        return None
    try:
        names = json.loads(m.read_text(encoding="utf-8"))["files"]
    except (ValueError, KeyError):
        return None
    files = [dest / n for n in names]
    return files if files and all(f.exists() for f in files) else None


def get_photos(db, listing_id: int, fetch_page=None) -> list[Path]:
    """-> список файлів у порядку сайту. PhotoError(kind) — якщо не вийшло.
    fetch_page(url) -> html|None: для тестів/повторного використання Fetcher."""
    row = db.get(listing_id)
    if not row:
        raise PhotoError("not_found")
    dest = photos_dir(db.path) / str(listing_id)
    hit = cached(dest)
    if hit:
        return hit

    urls = json.loads(row.get("images") or "[]")
    if not urls:  # при парсингу не зберегли (старі ліди) — йдемо на сайт
        if fetch_page is None:
            from .http import Fetcher
            f = Fetcher((0.0, 0.0))
            try:
                html = f.get(row["url"], retries=1)
            finally:
                f.close()
        else:
            html = fetch_page(row["url"])
        if not html:
            raise PhotoError("site_down")
        urls = extract_image_urls(html, row["url"], row["source"], row["ext_id"])
        if urls:
            db.set_images(listing_id, urls)
    if not urls:
        raise PhotoError("no_photos")

    files = download(urls, dest, referer=row["url"])
    if not files:
        raise PhotoError("no_photos", "фото є на сторінці, але не скачались (сайт блокує або всі дрібні)")
    return files


def make_zip(db, listing_id: int, files: list[Path]) -> Path:
    from .models import lead_code
    z = files[0].parent / f"{lead_code(listing_id)}.zip"
    if z.exists() and z.stat().st_mtime >= max(f.stat().st_mtime for f in files):
        return z
    with zipfile.ZipFile(z, "w", zipfile.ZIP_STORED) as zf:  # jpg вже стиснуті
        for f in files:
            zf.write(f, f"{lead_code(listing_id)}_{f.name}")
    return z
