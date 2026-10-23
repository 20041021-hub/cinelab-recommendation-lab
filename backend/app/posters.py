"""本地生成海报（展示层增强）。

课程数据包不含授权海报文件：`movies.poster_path` 全部为空，外部图床在本机网络也不可达。
这里按 movie_id 与真实元数据（片名 / 年份 / 类型）确定性地渲染一张 SVG 海报：

* 同一部电影任何时候都得到同一张图（哈希决定配色与构图），便于缓存与复现；
* 只读数据库，不写任何表，不动冻结数据，也不进入推荐代码路径；
* 图上保留 `CINELAB · LOCAL POSTER` 标记，避免被误当成官方海报。
"""

from __future__ import annotations

import hashlib
import re
from xml.sax.saxutils import escape

from fastapi import APIRouter, Request, Response
from sqlalchemy import text

from .errors import APIError
from .schemas import IdPath, identifier

router = APIRouter(tags=["本地生成海报"])

WIDTH, HEIGHT = 360, 540
FONT = "'PingFang SC','Microsoft YaHei','Noto Sans SC','Source Han Sans SC',system-ui,sans-serif"

# 与页面新配色同一族的十组海报色板：(底色, 深色, 强调色, 文字色)
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


@router.get("/movies/{movie_id}/poster.svg")
def movie_poster(movie_id: IdPath, request: Request):
    mid = identifier(movie_id)
    with request.app.state.db.connection() as conn:
        row = conn.execute(
            text("SELECT movie_id,movielens_title,title_zh,genres,release_year FROM movies WHERE movie_id=:id"),
            {"id": mid},
        ).mappings().first()
    if row is None:
        raise APIError(404, "MOVIE_NOT_FOUND", "电影不存在")
    svg = render_poster(row)
    etag = '"' + hashlib.sha256(svg.encode("utf-8")).hexdigest()[:32] + '"'
    headers = {
        "Cache-Control": "public, max-age=604800, immutable",
        "ETag": etag,
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=svg, media_type="image/svg+xml", headers=headers)