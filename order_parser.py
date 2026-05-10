"""
SBMS Order Parser
=================
Извлекает структурированные данные из коммерческого приказа SBMS (PDF / DOCX).

Ожидаемые секции (могут быть перенумерованы):
  1. Состав семейства продуктов          → product_family
  2. Общее описание продукта              → description
  3.2 Доступность продукта на тарифных планах → rate_plans (postpaid + prepaid)
  3.3.1 Взаимоисключающие Пакеты          → excl_packs
  3.3.2 Взаимоисключающие Услуги          → excl_services
  6.1 PCRF + Приложение №4                 → pcrf

Точка входа: parse_order(filename, content_bytes) -> dict.
Возвращает meta + 6 секций. Если какая-то не найдена — возвращает пустой список,
ошибку не бросает.
"""
from __future__ import annotations

import io
import re
from typing import Any

import pdfplumber


# ============================================================================
#                              ОБЩИЕ ХЕЛПЕРЫ
# ============================================================================

EXCLUSION_TYPES = {
    "1": "Не подключать пакет, если подключен пакет из таблицы",
    "2": "Заменить пакет, если обнаружен пакет из таблицы без подтверждения",
    "3": "Заменить пакет, если обнаружен пакет из таблицы с подтверждением",
}


def _clean(s: Any) -> str:
    if s is None:
        return ""
    s = str(s).replace("\xa0", " ").replace(" ", " ").replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip()


def _norm_lines(s: str) -> list[str]:
    return [_clean(ln) for ln in (s or "").split("\n") if _clean(ln)]


# ============================================================================
#                         СЕКЦИЯ 1. СОСТАВ СЕМЕЙСТВА
# ============================================================================
def _parse_family(tables_by_page: list[list[list[list[str]]]]) -> list[dict]:
    """Таблица: №, Коммерческое наименование, Техническое наименование, Product_id."""
    out: list[dict] = []
    for tables in tables_by_page:
        for tbl in tables or []:
            if not tbl or not tbl[0]:
                continue
            header = [_clean(c).lower() for c in tbl[0] if c]
            joined = " | ".join(header)
            if "product_id" not in joined:
                continue
            if "коммерч" not in joined and "наимен" not in joined:
                continue
            pid_idx = next((i for i, h in enumerate(header) if "product_id" in h), None)
            tech_idx = next((i for i, h in enumerate(header) if "техн" in h), None)
            comm_idx = next(
                (i for i, h in enumerate(header) if "коммерч" in h or "наимен" in h),
                None,
            )
            if pid_idx is None:
                continue
            for row in tbl[1:]:
                if not row or pid_idx >= len(row):
                    continue
                pid_raw = _clean(row[pid_idx] or "")
                m = re.search(r"(\d{3,8})", pid_raw)
                if not m:
                    continue
                comm_cell = (
                    row[comm_idx]
                    if comm_idx is not None and comm_idx < len(row) and row[comm_idx]
                    else ""
                )
                names: dict[str, str] = {}
                for ln in _norm_lines(comm_cell):
                    m2 = re.match(r"(рус|eng|uzb|ru|en|uz)\s*[:.]?\s*(.+)$", ln, flags=re.I)
                    if m2:
                        key = {
                            "рус": "ru", "ru": "ru",
                            "eng": "en", "en": "en",
                            "uzb": "uz", "uz": "uz",
                        }[m2.group(1).lower()]
                        names[key] = _clean(m2.group(2))
                tech = ""
                if tech_idx is not None and tech_idx < len(row):
                    tech = _clean(row[tech_idx] or "")
                out.append({
                    "product_id": int(m.group(1)),
                    "name_ru": names.get("ru", ""),
                    "name_en": names.get("en", ""),
                    "name_uz": names.get("uz", ""),
                    "technical_name": tech,
                })
            if out:
                return out
    return out


# ============================================================================
#                         СЕКЦИЯ 2. ОПИСАНИЕ ПРОДУКТА
# ============================================================================
def _parse_description(text_by_page, tables_by_page) -> list[dict]:
    """Таблица [Название, Лимит, Стоимость] из раздела '2 Общее описание'."""
    target_pages = [
        i for i, t in enumerate(text_by_page)
        if re.search(r"(?:^|\n)\s*2\s+Общее описание продукта", t or "")
    ]
    items: list[dict] = []
    for pi in target_pages:
        for tbl in tables_by_page[pi] or []:
            if not tbl or not tbl[0]:
                continue
            hdr = [_clean(c).lower() for c in tbl[0]]
            if not any("лимит" in c for c in hdr):
                continue
            if not any("стоимост" in c or "цена" in c for c in hdr):
                continue
            lim_idx = next(i for i, h in enumerate(hdr) if "лимит" in h)
            price_idx = next(
                i for i, h in enumerate(hdr) if "стоимост" in h or "цена" in h
            )
            name_idx = next(
                (i for i, h in enumerate(hdr) if "назван" in h or "наимен" in h),
                0,
            )
            for row in tbl[1:]:
                name = _clean(row[name_idx] or "") if name_idx < len(row) else ""
                lim = _clean(row[lim_idx] or "") if lim_idx < len(row) else ""
                pr = _clean(row[price_idx] or "") if price_idx < len(row) else ""
                if name or lim or pr:
                    items.append({"name": name, "limit": lim, "price": pr})
            if items:
                return items
    return items


# ============================================================================
#                  СЕКЦИЯ 3.2 ДОСТУПНОСТЬ НА ТАРИФНЫХ ПЛАНАХ
# ============================================================================
def _parse_rate_plans(pdf: pdfplumber.PDF, text_by_page) -> dict:
    """3-колоночная таблица RATE_PLAN_ID/RATE_PLAN_NAME × 3.

    pdfplumber из-за отсутствия линий между ячейками часто возвращает
    «слипшийся» текст. Парсим через extract_words() и x-координаты заголовков.
    """
    start = next(
        (i for i, t in enumerate(text_by_page)
         if re.search(r"(?:^|\n)\s*3\.2\s+Доступность продукта на тариф", t or "")),
        None,
    )
    if start is None:
        return {"postpaid": [], "prepaid": []}
    end = next(
        (i for i in range(start + 1, len(text_by_page))
         if re.search(
             r"(?:^|\n)\s*(?:3\.2\s+Зависимые|3\.3\s+Взаимо|4\s+Требования)",
             text_by_page[i],
         )),
        len(text_by_page),
    )

    postpaid: list[dict] = []
    prepaid: list[dict] = []
    in_prepaid = False

    for pi in range(start, end):
        page = pdf.pages[pi]
        words = page.extract_words(keep_blank_chars=False, use_text_flow=True)
        if not words:
            continue

        # Группируем слова в строки по y
        rows_by_y: dict[float, list[dict]] = {}
        for w in words:
            ykey = round(w["top"], 0)
            rows_by_y.setdefault(ykey, []).append(w)
        rows = [
            sorted(rows_by_y[y], key=lambda w: w["x0"])
            for y in sorted(rows_by_y.keys())
        ]

        # Найти строки-заголовки (RATE_PLAN_ID / RATE_PLAN_NAME)
        header_rows: list[tuple[float, list[tuple[float, str]]]] = []
        for row in rows:
            merged: list[dict] = []
            i = 0
            while i < len(row):
                t = row[i]["text"]
                if (
                    t == "RATE_"
                    and i + 1 < len(row)
                    and row[i + 1]["text"].startswith("PLAN_")
                ):
                    merged.append({
                        "text": "RATE_" + row[i + 1]["text"],
                        "x0": row[i]["x0"],
                        "top": row[i]["top"],
                    })
                    i += 2
                else:
                    merged.append(row[i])
                    i += 1
            cols_id = [w["x0"] for w in merged if w["text"] == "RATE_PLAN_ID"]
            cols_name = [w["x0"] for w in merged if w["text"] == "RATE_PLAN_NAME"]
            if cols_id and cols_name:
                positions = sorted(
                    [(x, "id") for x in cols_id]
                    + [(x, "name") for x in cols_name]
                )
                header_rows.append((row[0]["top"], positions))

        # На какой y начинается секция Prepaid (если на этой странице)
        prepaid_y: float | None = None
        for row in rows:
            txt = " ".join(w["text"] for w in row)
            if "Тарифные планы Prepaid" in txt:
                prepaid_y = row[0]["top"]
                break

        # Парсим строки между заголовком таблицы и следующим заголовком
        for hi, (h_y, positions) in enumerate(header_rows):
            next_y = header_rows[hi + 1][0] if hi + 1 < len(header_rows) else 9e9
            id_xs = [p[0] for p in positions if p[1] == "id"]
            name_xs = [p[0] for p in positions if p[1] == "name"]
            n_pairs = min(len(id_xs), len(name_xs))
            pair_bounds: list[tuple[float, float, float]] = []
            for k in range(n_pairs):
                left = id_xs[k]
                right = id_xs[k + 1] if k + 1 < len(id_xs) else 1e9
                mid = name_xs[k]
                pair_bounds.append((left, mid, right))

            for row in rows:
                ry = row[0]["top"]
                if ry <= h_y + 2 or ry >= next_y - 2:
                    continue
                for left, mid, right in pair_bounds:
                    in_pair = [w for w in row if left - 2 <= w["x0"] < right - 2]
                    if not in_pair:
                        continue
                    id_words = [w["text"] for w in in_pair if w["x0"] < mid - 2]
                    name_words = [w["text"] for w in in_pair if w["x0"] >= mid - 2]
                    if not id_words and not name_words:
                        continue
                    if not name_words and id_words:
                        if not re.match(r"^\d{1,5}$", id_words[0]):
                            continue
                        iid = int(id_words[0])
                        name = " ".join(id_words[1:])
                    else:
                        if not id_words:
                            continue
                        if not re.match(r"^\d{1,5}$", id_words[0]):
                            continue
                        iid = int(id_words[0])
                        name = " ".join(id_words[1:] + name_words).strip()

                    item = {"id": iid, "name": _clean(name)}
                    target = postpaid
                    if in_prepaid or (prepaid_y is not None and ry > prepaid_y):
                        target = prepaid
                    target.append(item)

        if prepaid_y is not None:
            in_prepaid = True

    def _dedup(lst: list[dict]) -> list[dict]:
        seen: set[int] = set()
        out: list[dict] = []
        for it in lst:
            if it["id"] in seen:
                continue
            seen.add(it["id"])
            out.append(it)
        return out

    return {"postpaid": _dedup(postpaid), "prepaid": _dedup(prepaid)}


# ============================================================================
#               СЕКЦИИ 3.3.1 / 3.3.2 ВЗАИМОИСКЛЮЧАЮЩИЕ ПАКЕТЫ/УСЛУГИ
# ============================================================================
def _find_section_bbox(pdf: pdfplumber.PDF, text_by_page, header_re, end_res):
    """Найти (page_index, top_y) начала и конца секции по тексту заголовка."""
    pages_in_section: list[tuple[int, float, float]] = []  # (pi, top, bottom)
    start_pi: int | None = None
    start_y: float | None = None

    for pi, txt in enumerate(text_by_page):
        if re.search(header_re, txt or ""):
            start_pi = pi
            page = pdf.pages[pi]
            words = page.extract_words(keep_blank_chars=False, use_text_flow=True)
            for w in words:
                if re.search(header_re.replace(r"(?:^|\n)\s*", ""), w["text"]):
                    start_y = w["top"]
                    break
            if start_y is None:
                # fallback: ищем заголовок по строке
                rows_by_y: dict[float, list[dict]] = {}
                for w in words:
                    rows_by_y.setdefault(round(w["top"], 0), []).append(w)
                for y in sorted(rows_by_y.keys()):
                    line = " ".join(w["text"] for w in rows_by_y[y])
                    if re.search(header_re.replace(r"(?:^|\n)\s*", ""), line):
                        start_y = y
                        break
            break
    if start_pi is None:
        return []

    for pi in range(start_pi, len(text_by_page)):
        page = pdf.pages[pi]
        page_h = page.height
        top = start_y if pi == start_pi else 0.0
        bottom = page_h
        # Конец секции: ищем заголовок следующего раздела
        words = page.extract_words(keep_blank_chars=False, use_text_flow=True)
        rows_by_y: dict[float, list[dict]] = {}
        for w in words:
            rows_by_y.setdefault(round(w["top"], 0), []).append(w)
        end_y: float | None = None
        for y in sorted(rows_by_y.keys()):
            if pi == start_pi and y <= (start_y or 0) + 2:
                continue
            line = " ".join(w["text"] for w in rows_by_y[y])
            for end_re in end_res:
                if re.search(end_re, line):
                    end_y = y
                    break
            if end_y is not None:
                break
        if end_y is not None:
            bottom = end_y
            pages_in_section.append((pi, top, bottom))
            break
        pages_in_section.append((pi, top, bottom))
    return pages_in_section


def _parse_exclusions(pdf: pdfplumber.PDF, text_by_page, kind: str) -> list[dict]:
    """kind = 'pack' (3.3.1) или 'service' (3.3.2)."""
    if kind == "pack":
        header_re = r"(?:^|\n)\s*3\.3\.1\s+Взаимоисключающие\s+Пакеты"
        end_res = [
            r"^\s*3\.3\.2\s+Взаимоисключающие",
            r"^\s*3\.4\s+",
            r"^\s*4\s+Требования",
        ]
    else:
        header_re = r"(?:^|\n)\s*3\.3\.2\s+Взаимоисключающие\s+Услуги"
        end_res = [
            r"^\s*3\.4\s+",
            r"^\s*4\s+Требования",
        ]

    pages_in_section = _find_section_bbox(pdf, text_by_page, header_re, end_res)
    if not pages_in_section:
        return []

    items: list[dict] = []

    for pi, top, bottom in pages_in_section:
        page = pdf.pages[pi]
        # Берём только таблицы, которые ВНУТРИ диапазона [top, bottom]
        try:
            tables = page.find_tables()
        except Exception:
            tables = []
        for tobj in tables:
            t_top = tobj.bbox[1]
            t_bottom = tobj.bbox[3]
            if t_bottom <= top or t_top >= bottom:
                continue
            tbl = tobj.extract()
            if not tbl or not tbl[0]:
                continue
            hdr = [_clean(c).lower() for c in tbl[0]]
            if not any("тип взаимоискл" in (h or "") for h in hdr):
                continue
            n_idx = next(
                (i for i, h in enumerate(hdr) if h == "№" or "номер" in h), 0
            )
            name_idx = next(
                (i for i, h in enumerate(hdr) if "название" in h or "наимен" in h),
                1,
            )
            id_idx = next(
                (i for i, h in enumerate(hdr) if h == "id"),
                len(hdr) - 2,
            )
            type_idx = next(
                (i for i, h in enumerate(hdr) if "тип" in h),
                len(hdr) - 1,
            )
            for row in tbl[1:]:
                def get(k):
                    if k is None or k >= len(row) or not row[k]:
                        return ""
                    return _clean(row[k] or "")

                n_val = get(n_idx)
                name_val = get(name_idx)
                id_val = get(id_idx)
                type_val = get(type_idx)
                if not (n_val or name_val or id_val or type_val):
                    continue
                ids = [int(x) for x in re.findall(r"\d{2,8}", id_val)]
                tcode = ""
                m = re.search(r"(\d)\s*$", type_val)
                if m:
                    tcode = m.group(1)
                items.append({
                    "n": n_val,
                    "name": name_val,
                    "ids": ids,
                    "exclusion_type_raw": type_val,
                    "exclusion_code": tcode,
                    "exclusion_text": EXCLUSION_TYPES.get(tcode, ""),
                })

    # Постпроцесс: объединение «продолжающихся» строк, где имя ушло на отдельный
    # подряд из-за переноса в PDF
    merged: list[dict] = []
    for it in items:
        if (
            merged
            and not merged[-1]["name"]
            and it["name"]
            and not it["n"]
            and not it["ids"]
            and not it["exclusion_type_raw"]
        ):
            merged[-1]["name"] = it["name"]
            continue
        # Случай "n='17',name='',ids=[1121],type='...3'" + "n='',name='Data_pack...',ids=[],type=''"
        if (
            merged
            and not it["n"]
            and it["name"]
            and not it["ids"]
            and not it["exclusion_type_raw"]
            and not merged[-1]["name"]
        ):
            merged[-1]["name"] = it["name"]
            continue
        merged.append(it)

    return merged


# ============================================================================
#                          СЕКЦИЯ 6.1 PCRF + ПРИЛОЖЕНИЕ №4
# ============================================================================
def _parse_pcrf(text_by_page, tables_by_page) -> list[dict]:
    """Объединяет данные из 6.1 PCRF (COS / тип активации / примечание) и
    Приложения №4 (Quota Value / Priority / RG/SI)."""
    cos_items: dict[str, dict] = {}

    # 6.1: COS, Тип активации, Примечание
    for pi, _txt in enumerate(text_by_page):
        for tbl in tables_by_page[pi] or []:
            if not tbl or not tbl[0]:
                continue
            hdr = [_clean(c).lower() for c in tbl[0]]
            joined = " | ".join(hdr)
            if "cos" not in joined:
                continue
            if "тип активации" not in joined and "примечан" not in joined:
                continue
            cos_idx = next(
                (i for i, h in enumerate(hdr)
                 if "cos" in h and "name" not in h),
                None,
            )
            act_idx = next(
                (i for i, h in enumerate(hdr) if "тип" in h and "актив" in h),
                None,
            )
            note_idx = next(
                (i for i, h in enumerate(hdr) if "примечан" in h),
                None,
            )
            for row in tbl[1:]:
                # COS — ищем ячейку с подстрокой буквенного COS (S_..., RG_..., и т.п.)
                cos = ""
                if cos_idx is not None and cos_idx < len(row) and row[cos_idx]:
                    cos = _clean(row[cos_idx]).replace(" ", "")
                if not cos:
                    for c in row:
                        cc = _clean(c or "")
                        # имя COS: содержит буквы, подчёркивания, цифры, нет кириллицы
                        if cc and re.search(r"[A-Za-z]", cc) and re.match(
                            r"^[A-Za-z0-9_\s]+$", cc
                        ):
                            cos = re.sub(r"\s+", "", cc)
                            break
                if not cos:
                    continue
                cos_items.setdefault(cos, {"cos": cos})
                if act_idx is not None and act_idx < len(row):
                    cos_items[cos]["activation"] = _clean(row[act_idx] or "")
                if note_idx is not None and note_idx < len(row):
                    cos_items[cos]["note"] = _clean(row[note_idx] or "")

    # Приложение №4: COS Name / Quota / Priority / RG/SI
    for pi, _txt in enumerate(text_by_page):
        for tbl in tables_by_page[pi] or []:
            if not tbl or not tbl[0]:
                continue
            hdr = [_clean(c).lower() for c in tbl[0]]
            joined = " | ".join(hdr)
            if "cos name" not in joined:
                continue
            if "quota" not in joined and "rg/si" not in joined:
                continue
            quota_idx = next(
                (i for i, h in enumerate(hdr) if "quota" in h),
                None,
            )
            prio_idx = next(
                (i for i, h in enumerate(hdr) if "priority" in h),
                None,
            )
            rg_idx = next(
                (i for i, h in enumerate(hdr) if "rg" in h),
                None,
            )
            for row in tbl[1:]:
                # COS Name может быть «split» на 2 ячейки — собираем буквенные части
                cos_parts: list[str] = []
                seen_cols: set[int] = set()
                for k, c in enumerate(row):
                    cc = _clean(c or "")
                    if k in {quota_idx, prio_idx, rg_idx}:
                        continue
                    if not cc:
                        continue
                    # принимаем только токены с буквами (не «60000»)
                    if re.search(r"[A-Za-z]", cc) and re.match(
                        r"^[A-Za-z0-9_\s]+$", cc
                    ):
                        cos_parts.append(re.sub(r"\s+", "", cc))
                        seen_cols.add(k)
                if not cos_parts:
                    continue
                cos = "".join(cos_parts)
                cos_items.setdefault(cos, {"cos": cos})
                cos_items[cos]["cos"] = cos
                if quota_idx is not None and quota_idx < len(row):
                    q = _clean(row[quota_idx] or "")
                    if q:
                        cos_items[cos]["quota"] = q
                if prio_idx is not None and prio_idx < len(row):
                    p = _clean(row[prio_idx] or "")
                    if p:
                        cos_items[cos]["priority"] = p
                if rg_idx is not None and rg_idx < len(row):
                    rs = _clean(row[rg_idx] or "")
                    if rs:
                        cos_items[cos]["rg_si"] = rs

    return list(cos_items.values())


# ============================================================================
#                                   META
# ============================================================================
def _parse_meta(text_by_page) -> dict:
    full = "\n".join(text_by_page)
    m_no = re.search(r"Рег\.?\s*№\s*(\d+[\"«»“”]?[А-ЯA-Z]?[\"»“”«]?)", full)
    m_dt = re.search(r"Дата:\s*(\d{2}\.\d{2}\.\d{4})", full)
    title = ""
    m_title = re.search(r"ПРИКАЗ\s*\n([\s\S]+?)(?=\n\s*В\s+целях|приказываю)", full)
    if m_title:
        title = _clean(re.sub(r"\s+", " ", m_title.group(1)))
    return {
        "order_no": _clean(m_no.group(1)) if m_no else "",
        "order_date": m_dt.group(1) if m_dt else "",
        "title": title,
    }


# ============================================================================
#                              ВНЕШНЯЯ ТОЧКА ВХОДА
# ============================================================================
def parse_order(filename: str, content: bytes) -> dict:
    """Главная функция. На вход байты PDF/DOCX, на выходе структурированный JSON."""
    name_lower = (filename or "").lower()
    if name_lower.endswith(".pdf"):
        return _parse_pdf(content)
    if name_lower.endswith(".docx"):
        return _parse_docx(content)
    # Если расширение неизвестно — пробуем по сигнатуре
    if content[:4] == b"%PDF":
        return _parse_pdf(content)
    if content[:2] == b"PK":
        return _parse_docx(content)
    raise ValueError("Поддерживаются только PDF и DOCX")


def _parse_pdf(content: bytes) -> dict:
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        text_by_page = [(p.extract_text() or "") for p in pdf.pages]
        tables_by_page = [p.extract_tables() for p in pdf.pages]

        meta = _parse_meta(text_by_page)
        family = _parse_family(tables_by_page)
        description = _parse_description(text_by_page, tables_by_page)
        rate_plans = _parse_rate_plans(pdf, text_by_page)
        excl_packs = _parse_exclusions(pdf, text_by_page, "pack")
        excl_services = _parse_exclusions(pdf, text_by_page, "service")
        pcrf = _parse_pcrf(text_by_page, tables_by_page)

    # Постпроцесс: для строк взаимоисключений с пустым ID — попробовать
    # подставить product_id из секции 1 по совпадению technical_name
    tech_to_id = {it["technical_name"]: it["product_id"] for it in family if it.get("technical_name")}
    for it in excl_packs:
        if not it["ids"] and it["name"] in tech_to_id:
            it["ids"] = [tech_to_id[it["name"]]]

    return {
        "meta": meta,
        "product_family": family,
        "description": description,
        "rate_plans": rate_plans,
        "excl_packs": excl_packs,
        "excl_services": excl_services,
        "pcrf": pcrf,
    }


def _parse_docx(content: bytes) -> dict:
    """Минимальная поддержка DOCX. Конвертирует в PDF не пытаемся —
    парсим напрямую через python-docx, если он доступен.

    Полная поддержка DOCX потребует отдельной реализации; пока
    возвращаем meta + текст всех параграфов."""
    try:
        import docx  # python-docx
    except ImportError as e:
        raise RuntimeError(
            "Для парсинга DOCX установите python-docx (pip install python-docx)"
        ) from e

    doc = docx.Document(io.BytesIO(content))
    full_text = "\n".join(p.text for p in doc.paragraphs)
    return {
        "meta": _parse_meta([full_text]),
        "product_family": [],
        "description": [],
        "rate_plans": {"postpaid": [], "prepaid": []},
        "excl_packs": [],
        "excl_services": [],
        "pcrf": [],
        "raw_text": full_text,
        "_warning": "DOCX парсится частично — для точного разбора сконвертируйте в PDF.",
    }
