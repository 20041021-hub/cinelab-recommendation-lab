"""电影海报（展示层增强，实验六扩展）。

课程数据包不含海报文件（`movies.poster_path` 全部为空），因此这里提供两级方案：

1. **真实海报缓存**：按 `tmdb_id` 到公开电影资料页取回真实海报图片（600×900 JPEG），
   写入 `CINELAB_POSTER_DIR`（默认 `/app/posters`）并直接对外提供。缓存命中即零外呼，
   同一部电影只抓一次；抓取失败会记进 `_misses.json`，短时间不重复尝试。
2. **本地生成海报**：没有 `tmdb_id`、资料页没有海报或外网不可用时，按 movie_id 与真实
   元数据确定性地渲染一张 SVG（同一部电影任何时候都是同一张图），保证页面不留空白。

两条路径都只读 `movies` 表、不写任何业务表、不进入推荐与评价代码路径。
抓取到的海报仅作本机课程演示缓存使用，仓库不提交图片文件（`posters/` 已加入 .gitignore）。
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from fastapi import APIRouter, Request, Response
from sqlalchemy import text

from .errors import APIError
from .schemas import IdPath, identifier

router = APIRouter(tags=["电影海报"])

WIDTH, HEIGHT = 360, 540
FONT = "'PingFang SC','Microsoft YaHei','Noto Sans SC','Source Han Sans SC',system-ui,sans-serif"

# --------------------------------------------------------------------------- 真实海报缓存

POSTER_SOURCE = "letterboxd"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
FILM_PAGE = "https://letterboxd.com/tmdb/{tmdb_id}/"
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MISS_RETRY_SECONDS = 7 * 24 * 3600
MISS_MAX_ATTEMPTS = 3

_PAGE_PATTERNS = (
    # 竖版海报优先：og:image 在 Letterboxd 上多是 16:9 剧照，塞进 2:3 的海报框会被裁掉大半，
    # 所以先取资料页里真正的 film-poster 资源，取不到再退回 og:image。
    re.compile(r"https://[a-z]\.ltrbxd\.com/resized/film-poster/[^\"'\s]+"),
    re.compile(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', re.IGNORECASE),
    re.compile(r'<meta[^>]+content="([^"]+)"[^>]+property="og:image"', re.IGNORECASE),
    re.compile(r"https://[a-z]\.ltrbxd\.com/resized/[^\"'\s]+"),
)

_write_lock = threading.Lock()


class PosterUnavailable(Exception):
    """取不到真实海报（缺 tmdb_id / 资料页无海报 / 外网不可用）。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def poster_dir() -> Path:
    return Path(os.environ.get("CINELAB_POSTER_DIR", "/app/posters"))


def fetch_enabled() -> bool:
    return os.environ.get("CINELAB_POSTER_FETCH", "1").strip().lower() not in {"0", "false", "no"}


def fetch_timeout() -> float:
    try:
        return max(2.0, float(os.environ.get("CINELAB_POSTER_TIMEOUT", "8")))
    except ValueError:
        return 8.0


def _misses_path() -> Path:
    return poster_dir() / "_misses.json"


def _read_misses() -> dict[str, dict]:
    path = _misses_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_misses(data: dict[str, dict]) -> None:
    path = _misses_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def record_miss(movie_id: int, reason: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _write_lock:
        data = _read_misses()
        entry = data.get(str(movie_id), {"attempts": 0})
        entry.update({"reason": reason[:200], "at": now, "attempts": int(entry.get("attempts", 0)) + 1})
        data[str(movie_id)] = entry
        _write_misses(data)


def recent_miss(movie_id: int) -> str | None:
    entry = _read_misses().get(str(movie_id))
    if not entry:
        return None
    if int(entry.get("attempts", 0)) >= MISS_MAX_ATTEMPTS:
        return entry.get("reason") or "多次抓取失败"
    try:
        moment = datetime.fromisoformat(entry["at"])
    except (KeyError, ValueError):
        return None
    if (datetime.now(timezone.utc) - moment).total_seconds() < MISS_RETRY_SECONDS:
        return entry.get("reason") or "最近抓取失败"
    return None


def cache_path(movie_id: int) -> Path:
    return poster_dir() / f"{movie_id}.jpg"


def normalize_tmdb_id(value) -> str | None:
    """tmdb_id 在库中是整数列，缺省为 NULL；统一成字符串供资料页 URL 使用。"""
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


def cached_poster(movie_id: int) -> bytes | None:
    path = cache_path(movie_id)
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data or None


def _open(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = (response.headers.get("Content-Type") or "").lower()
        data = response.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise PosterUnavailable("海报文件过大")
        if content_type and not content_type.startswith(("image/", "text/html")):
            raise PosterUnavailable(f"意外的响应类型：{content_type}")
        return data


def extract_poster_url(page: bytes) -> str | None:
    text_page = page.decode("utf-8", "ignore")
    for pattern in _PAGE_PATTERNS:
        match = pattern.search(text_page)
        if match:
            url = html.unescape(match.group(1) if match.groups() else match.group(0))
            if "ltrbxd.com" in url:
                return url
    return None


def download_poster(tmdb_id: str, timeout: float | None = None) -> tuple[bytes, str]:
    """按 tmdb_id 取回真实海报：先取资料页，再取图片本体。"""
    limit = timeout or fetch_timeout()
    try:
        page = _open(FILM_PAGE.format(tmdb_id=tmdb_id), limit)
    except urllib.error.HTTPError as error:
        raise PosterUnavailable(f"资料页返回 HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise PosterUnavailable(f"资料页不可达：{type(error).__name__}") from error
    url = extract_poster_url(page)
    if not url:
        raise PosterUnavailable("资料页没有海报图片")
    try:
        data = _open(url, limit)
    except urllib.error.HTTPError as error:
        raise PosterUnavailable(f"海报图片返回 HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise PosterUnavailable(f"海报图片不可达：{type(error).__name__}") from error
    if not data.startswith(b"\xff\xd8") and not data.startswith(b"\x89PNG"):
        raise PosterUnavailable("图片格式不是 JPEG/PNG")
    return data, url


def store_poster(movie_id: int, data: bytes) -> Path:
    path = cache_path(movie_id)
    with _write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{movie_id}.jpg.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    return path


def ensure_poster(movie_id: int, tmdb_id: str | None, timeout: float | None = None) -> tuple[bytes | None, str | None]:
    """缓存优先；未命中且允许抓取时抓一次。返回 (图片字节, 失败原因)。"""
    data = cached_poster(movie_id)
    if data:
        return data, None
    if not tmdb_id:
        return None, "没有 tmdb_id"
    if not fetch_enabled():
        return None, "抓取已关闭（CINELAB_POSTER_FETCH=0）"
    miss = recent_miss(movie_id)
    if miss:
        return None, miss
    try:
        data, _ = download_poster(tmdb_id, timeout)
    except PosterUnavailable as error:
        record_miss(movie_id, error.reason)
        return None, error.reason
    store_poster(movie_id, data)
    return data, None


def poster_summary() -> dict:
    directory = poster_dir()
    cached = 0
    total_bytes = 0
    try:
        for entry in directory.glob("*.jpg"):
            cached += 1
            try:
                total_bytes += entry.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    misses = _read_misses()
    return {
        "source": POSTER_SOURCE,
        "directory": str(directory),
        "cached": cached,
        "bytes": total_bytes,
        "misses": len(misses),
        "fetch_enabled": fetch_enabled(),
        "timeout_seconds": fetch_timeout(),
    }


# --------------------------------------------------------------------------- 本地生成海报

# 与页面配色同一族的十组海报色板：(底色, 深色, 强调色, 文字色)
PALETTES: tuple[tuple[str, str, str, str], ...] = (
    ("#0f1b33", "#070c1a", "#67e8c3", "#f2f6ff"),
    ("#1a1030", "#0b0618", "#c4a1ff", "#f6f2ff"),
    ("#0d2233", "#05121d", "#7cd0ff", "#eef8ff"),
    ("#2a1220", "#15060f", "#ff9ec4", "#fff2f7"),
    ("#12261d", "#07130e", "#8ce99a", "#f2fff5"),
    ("#251a0c", "#120c05", "#ffcf7a", "#fff8ec"),
    ("#161a2e", "#0a0c18", "#9fb4ff", "#f2f4ff"),
    ("#231026", "#100513", "#f0a6ff", "#fdf2ff"),
    ("#0b2430", "#04131a", "#5ee1d8", "#ecfeff"),
    ("#261414", "#120808", "#ffab91", "#fff4f0"),
)


def _seed(movie_id: int, salt: str) -> int:
    raw = hashlib.sha256(f"{salt}:{movie_id}".encode("utf-8")).digest()
    return int.from_bytes(raw[:8], "big")


def _text_units(value: str) -> float:
    return sum(1.0 if ord(char) > 0x2E80 else 0.56 for char in value)


def _strip_year(value: str) -> str:
    """去掉片名尾部括号里的年份：年份已经单独出现在元信息里。"""
    return re.sub(r"\s*\(\d{4}\)\s*$", "", value or "").strip()


def _wrap(value: str, limit: float, max_lines: int) -> list[str]:
    """按“中日韩字符算 1 个单位、西文字符算 0.56”粗略折行，超出加省略号。"""
    lines: list[str] = []
    current = ""
    truncated = False
    for char in value:
        if current and _text_units(current + char) > limit:
            lines.append(current)
            current = char
            if len(lines) == max_lines:
                current = ""
                truncated = True
                break
        else:
            current += char
    if current and len(lines) < max_lines:
        lines.append(current)
    if not lines:
        lines = [value]
    else:
        consumed = sum(len(line) for line in lines)
        if truncated or consumed < len(value):
            head = lines[-1]
            lines[-1] = (head[:-1] if len(head) > 1 else "") + "…"
    return lines


def _decor(variant: int, deep: str, accent: str) -> str:
    if variant == 0:
        return (
            f'<circle cx="296" cy="150" r="150" fill="{accent}" opacity="0.12"/>'
            f'<circle cx="296" cy="150" r="96" fill="none" stroke="{accent}" stroke-width="1.5" opacity="0.35"/>'
            f'<rect x="0" y="392" width="360" height="2" fill="{accent}" opacity="0.3"/>'
        )
    if variant == 1:
        return (
            f'<rect x="-60" y="330" width="520" height="150" fill="{deep}" opacity="0.85" transform="rotate(-9 180 400)"/>'
            f'<rect x="-60" y="330" width="520" height="150" fill="none" stroke="{accent}" stroke-width="1.2" opacity="0.4" transform="rotate(-9 180 400)"/>'
            f'<circle cx="56" cy="86" r="46" fill="none" stroke="{accent}" stroke-width="8" opacity="0.22"/>'
        )
    if variant == 2:
        return (
            f'<path d="M360 0 L360 250 A250 250 0 0 1 110 0 Z" fill="{accent}" opacity="0.16"/>'
            f'<path d="M360 0 L360 250 A250 250 0 0 1 110 0" fill="none" stroke="{accent}" stroke-width="1.4" opacity="0.4"/>'
            f'<rect x="34" y="404" width="292" height="86" fill="{deep}" opacity="0.75"/>'
        )
    stripes = "".join(
        f'<rect x="{18 + index * 34}" y="0" width="8" height="540" fill="{accent}" opacity="0.07"/>'
        for index in range(10)
    )
    return (
        stripes
        + f'<rect x="18" y="18" width="324" height="504" fill="none" stroke="{accent}" stroke-width="1.2" opacity="0.35"/>'
        + f'<circle cx="300" cy="466" r="70" fill="{deep}" opacity="0.8"/>'
    )


def _perforations(accent: str) -> str:
    holes = "".join(
        f'<rect x="14" y="{26 + index * 34}" width="9" height="16" rx="3" fill="{accent}" opacity="0.28"/>'
        for index in range(14)
    )
    return holes


def render_poster(row) -> str:
    movie_id = int(row["movie_id"])
    raw_title = (row["title_zh"] or row["movielens_title"] or f"MovieLens #{movie_id}").strip()
    title = _strip_year(raw_title) or raw_title
    original = (row["movielens_title"] or "").strip()
    raw_genres = row["genres"] or ""
    genres = [] if raw_genres == "(no genres listed)" else raw_genres.split("|")
    year = row["release_year"]

    base, deep, accent, ink = PALETTES[_seed(movie_id, "palette") % len(PALETTES)]
    variant = _seed(movie_id, "layout") % 4
    monogram = (genres[0][:1] if genres else "C").upper()

    title_lines = _wrap(title, 12.0, 3)
    title_svg = "".join(
        f'<text x="48" y="{330 + index * 36}" font-size="27" font-weight="650" fill="{ink}">{escape(line)}</text>'
        for index, line in enumerate(title_lines)
    )
    meta_bits = " · ".join([str(year)] if year else [])
    if genres:
        meta_bits = " · ".join(part for part in (str(year) if year else "", " / ".join(genres[:2])) if part)
    if original and _strip_year(original) != title:
        meta_bits = f"{meta_bits} · {original}" if meta_bits else original
    meta_lines = _wrap(meta_bits, 36, 2)
    meta_svg = "".join(
        f'<text x="48" y="{342 + len(title_lines) * 36 + index * 19}" font-size="13" fill="{ink}" opacity="0.66">{escape(line)}</text>'
        for index, line in enumerate(meta_lines)
    )
    genre_line = escape(" / ".join(genres[:3]) if genres else "未收录类型")

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" height="{HEIGHT}" role="img" aria-label="{escape(title)}">'
        f"<defs><linearGradient id=\"bg\" x1=\"0\" y1=\"0\" x2=\"0.4\" y2=\"1\">"
        f'<stop offset="0" stop-color="{base}"/><stop offset="1" stop-color="{deep}"/></linearGradient></defs>'
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="url(#bg)"/>'
        f"{_decor(variant, deep, accent)}"
        f"{_perforations(accent)}"
        f'<rect x="48" y="30" width="270" height="1.2" fill="{accent}" opacity="0.5"/>'
        f'<text x="48" y="60" font-size="11" letter-spacing="3.4" fill="{accent}">CINELAB · LOCAL POSTER</text>'
        f'<text x="48" y="88" font-size="10.5" letter-spacing="2.6" fill="{ink}" opacity="0.55">FILM #{movie_id}</text>'
        f'<text x="342" y="250" text-anchor="end" font-family="{FONT}" font-size="215" font-weight="700" fill="{accent}" opacity="0.15">{escape(monogram)}</text>'
        f'<g font-family="{FONT}">{title_svg}{meta_svg}</g>'
        f'<rect x="48" y="{472}" width="270" height="1.2" fill="{accent}" opacity="0.4"/>'
        f'<text x="48" y="498" font-family="{FONT}" font-size="11" letter-spacing="1.6" fill="{ink}" opacity="0.6">'
        f"{genre_line}</text>"
        f'<text x="342" y="498" text-anchor="end" font-family="{FONT}" font-size="10.5" letter-spacing="1.6" fill="{accent}" opacity="0.8">本地生成</text>'
        f"</svg>"
    )


# --------------------------------------------------------------------------- 路由

POSTER_COLUMNS = "movie_id,movielens_title,title_zh,genres,release_year,tmdb_id"


def _movie_row(request: Request, movie_id: int):
    with request.app.state.db.connection() as conn:
        return conn.execute(
            text(f"SELECT {POSTER_COLUMNS} FROM movies WHERE movie_id=:id"),
            {"id": movie_id},
        ).mappings().first()


def _image_response(request: Request, data: bytes, media_type: str, etag_seed: bytes) -> Response:
    etag = '"' + hashlib.sha256(etag_seed).hexdigest()[:32] + '"'
    headers = {"Cache-Control": "public, max-age=604800, immutable", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=data, media_type=media_type, headers=headers)


@router.get("/movies/{movie_id}/poster")
def movie_poster(movie_id: IdPath, request: Request):
    """真实海报缓存优先；取不到则给本地生成海报，页面永不空白。"""
    mid = identifier(movie_id)
    row = _movie_row(request, mid)
    if row is None:
        raise APIError(404, "MOVIE_NOT_FOUND", "电影不存在")
    tmdb_id = normalize_tmdb_id(row["tmdb_id"])
    data, _reason = ensure_poster(mid, tmdb_id)
    if data:
        return _image_response(request, data, "image/jpeg", data)
    svg = render_poster(row)
    return _image_response(request, svg.encode("utf-8"), "image/svg+xml", svg.encode("utf-8"))


@router.get("/movies/{movie_id}/poster.svg")
def movie_poster_svg(movie_id: IdPath, request: Request):
    """本地生成海报（缓存未命中且抓不到真实海报时的确定性替代品）。"""
    mid = identifier(movie_id)
    row = _movie_row(request, mid)
    if row is None:
        raise APIError(404, "MOVIE_NOT_FOUND", "电影不存在")
    svg = render_poster(row)
    return _image_response(request, svg.encode("utf-8"), "image/svg+xml", svg.encode("utf-8"))


@router.get("/movies/{movie_id}/poster/status")
def movie_poster_status(movie_id: IdPath, request: Request, fetch: int = 0):
    """海报缓存状态；`fetch=1` 时顺带抓一次（供 scripts/fetch_posters.py 预热）。"""
    mid = identifier(movie_id)
    row = _movie_row(request, mid)
    if row is None:
        raise APIError(404, "MOVIE_NOT_FOUND", "电影不存在")
    tmdb_id = normalize_tmdb_id(row["tmdb_id"])
    data = cached_poster(mid)
    reason = None
    if data is None and fetch:
        data, reason = ensure_poster(mid, tmdb_id, timeout=fetch_timeout())
    elif data is None:
        reason = recent_miss(mid) or ("没有 tmdb_id" if not tmdb_id else "尚未抓取")
    body = {
        "movie_id": mid,
        "tmdb_id": tmdb_id,
        "cached": bool(data),
        "source": POSTER_SOURCE if data else None,
        "bytes": len(data) if data else 0,
        "reason": None if data else reason,
    }
    return Response(
        content=json.dumps(body, ensure_ascii=False),
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/experiments/poster-cache")
def poster_cache_summary():
    """海报缓存概览（缓存数量、占用空间、失败记录数）。"""
    return poster_summary()