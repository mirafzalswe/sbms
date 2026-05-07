/* ============================================================
   SBMS OrderParse — reusable парсер списков {id, name} из приказов.

   Поддерживаемые источники:
     1. Excel/Word табличный paste (TSV)         → клиент
     2. Markdown / ASCII-таблицы (| col | col |) → клиент
     3. CSV (запятая/точка-запятая/таб)          → клиент
     4. JSON массив                              → клиент
     5. Free-form (одна запись на строку)        → клиент
     6. PDF / DOCX / XLSX / TXT файл             → backend /api/availability/parse

   API
   ---
     SbmsOrderParse.parseText(text)              → {items: [{id,name,line}], errors: []}
     SbmsOrderParse.parseFile(file)              → Promise<{items, source, filename}>
     SbmsOrderParse.normalize(items)             → дедуп + нормализация типов
     SbmsOrderParse.toCsv(items, headers?)       → text/csv

   Эвристики:
   - "(ID 12345)", "id: 12345", "[12345]", "12345 — Name" — извлекают ID
   - Если первая колонка цифры → ID, остаток → имя
   - Если ровно 2 колонки → (id, name)
   - Если 3+ → ищем колонку с числами как ID и текстовую как имя
   ============================================================ */
(function (global) {
    'use strict';

    if (global.SbmsOrderParse) return;

    /* Допустимые названия столбцов */
    const ID_HEADER_RE   = /^(id|код|kod|ident|идент|number|номер|product[_ ]?id|pack[_ ]?id|service[_ ]?id|rate[_ ]?plan[_ ]?id)$/i;
    const NAME_HEADER_RE = /^(name|название|наим|nomi|nom|nazvanie|product|продукт|пакет|услуга|тариф|description|описание)$/i;

    /* Регэкспы */
    const ID_PAREN  = /\(\s*(?:ID\s*[:=]?\s*|№\s*)?(\d{3,8})\s*\)/i;        // "(ID 1234)"
    const ID_PREFIX = /^\s*(?:ID|№|NO\.?|#)\s*[:=]?\s*(\d{3,8})\b/i;        // "ID: 1234 ..."
    const ID_TAIL   = /\b(\d{3,8})\s*$/;                                    // "...1234"
    const ID_ANY    = /\b(\d{3,8})\b/;                                      // любое число

    function clean(s) {
        if (s == null) return '';
        return String(s).replace(/ /g, ' ').replace(/\s+/g, ' ').trim();
    }

    function stripIdFromName(s) {
        return clean(s)
            .replace(/\(\s*(?:ID\s*[:=]?\s*|№\s*)?\d{3,8}\s*\)/ig, '')
            .replace(/\bID\s*[:=]?\s*\d{3,8}\b/ig, '')
            .replace(/^\s*(?:ID|№|NO\.?|#)\s*[:=]?\s*\d{3,8}\b\s*[-—–:.,]?\s*/i, '')
            .replace(/\s+[-—–:.,]\s+/g, ' — ')
            .replace(/\s{2,}/g, ' ')
            .trim();
    }

    function extractId(s) {
        const t = clean(s);
        if (!t) return null;
        let m = t.match(ID_PAREN) || t.match(ID_PREFIX);
        if (m) return parseInt(m[1], 10);
        // если строка целиком — число
        m = t.match(/^(\d{3,8})$/);
        if (m) return parseInt(m[1], 10);
        return null;
    }

    function splitLines(text) {
        return String(text || '').split(/\r?\n/);
    }

    /* ---------- TSV / Markdown / CSV detection ---------- */
    function detectDelimiter(line) {
        if (line.indexOf('\t') !== -1) return '\t';
        // Markdown row: "| col | col |"
        if (/^\s*\|.+\|\s*$/.test(line)) return '|';
        // если есть ; и нет , — приоритет ;
        const semis = (line.match(/;/g) || []).length;
        const commas = (line.match(/,/g) || []).length;
        if (semis > commas && semis > 0) return ';';
        if (commas > 0) return ',';
        return null;
    }

    function splitRow(line, delim) {
        if (delim === '|') {
            // Markdown table: убираем пустые края
            const cells = line.split('|').map(c => c.trim());
            if (cells[0] === '') cells.shift();
            if (cells[cells.length - 1] === '') cells.pop();
            return cells;
        }
        if (delim === ',' || delim === ';') {
            // простейший CSV-парсер с поддержкой кавычек
            const out = []; let cur = ''; let inQ = false;
            for (let i = 0; i < line.length; i++) {
                const ch = line[i];
                if (ch === '"') {
                    if (inQ && line[i + 1] === '"') { cur += '"'; i++; }
                    else inQ = !inQ;
                } else if (ch === delim && !inQ) {
                    out.push(cur); cur = '';
                } else cur += ch;
            }
            out.push(cur);
            return out.map(c => c.trim());
        }
        return line.split(delim).map(c => c.trim());
    }

    function isMdSeparator(line) {
        // строка-разделитель Markdown: "| --- | --- |" или "|:---|---:|"
        return /^\s*\|?\s*(:?-{3,}:?\s*\|?\s*)+\s*$/.test(line);
    }

    /* ---------- Header / column detection ---------- */
    function pickIdAndNameColumns(headers) {
        let idIdx = -1, nameIdx = -1;
        headers.forEach((h, i) => {
            const t = clean(h).toLowerCase();
            if (idIdx === -1 && ID_HEADER_RE.test(t)) idIdx = i;
            if (nameIdx === -1 && NAME_HEADER_RE.test(t)) nameIdx = i;
        });
        return { idIdx, nameIdx };
    }

    function looksLikeId(s) {
        return /^\d{3,8}$/.test(clean(s));
    }

    function autoColumnsByContent(rows) {
        // выбрать индекс колонки, где БОЛЬШИНСТВО значений — числа, а соседняя — текст
        if (!rows.length) return { idIdx: -1, nameIdx: -1 };
        const widths = rows.map(r => r.length);
        const ncols = Math.max(...widths);
        const numScore = new Array(ncols).fill(0);
        const txtScore = new Array(ncols).fill(0);
        rows.forEach(r => {
            for (let i = 0; i < ncols; i++) {
                const c = clean(r[i] || '');
                if (!c) continue;
                if (looksLikeId(c)) numScore[i]++;
                else if (c.length > 1) txtScore[i]++;
            }
        });
        const total = rows.length;
        let idIdx = -1; let bestNum = 0;
        numScore.forEach((s, i) => { if (s > bestNum && s >= total * 0.5) { idIdx = i; bestNum = s; } });
        let nameIdx = -1; let bestTxt = 0;
        txtScore.forEach((s, i) => {
            if (i === idIdx) return;
            if (s > bestTxt && s >= total * 0.3) { nameIdx = i; bestTxt = s; }
        });
        return { idIdx, nameIdx };
    }

    /* ---------- JSON ---------- */
    function tryParseJson(text) {
        const s = clean(text);
        if (!s || (s[0] !== '[' && s[0] !== '{')) return null;
        try { return JSON.parse(s); } catch (e) { return null; }
    }

    function fromJsonArray(arr) {
        const out = [];
        if (!Array.isArray(arr)) {
            // {items:[]} | {tariffs:[]} | {data:[]}
            for (const k of ['items', 'tariffs', 'packs', 'services', 'data']) {
                if (Array.isArray(arr && arr[k])) { arr = arr[k]; break; }
            }
            if (!Array.isArray(arr)) return out;
        }
        arr.forEach(it => {
            if (it == null) return;
            if (typeof it === 'number') { out.push({ id: it, name: '' }); return; }
            if (typeof it === 'string') {
                const id = extractId(it);
                if (id != null) out.push({ id, name: stripIdFromName(it) });
                else if (looksLikeId(it)) out.push({ id: parseInt(it, 10), name: '' });
                return;
            }
            if (typeof it === 'object') {
                const idRaw = it.id || it.packId || it.serviceId || it.ratePlanId
                            || it.product_id || it.pack_id || it.service_id;
                let id = null;
                if (idRaw != null) {
                    const n = parseInt(idRaw, 10);
                    if (!isNaN(n)) id = n;
                }
                const name = clean(it.name || it.title || it.label || '');
                if (id != null || name) out.push({ id, name });
            }
        });
        return out;
    }

    /* ---------- Free-form lines ---------- */
    function fromFreeForm(text) {
        const out = [];
        splitLines(text).forEach((rawLine, idx) => {
            const line = clean(rawLine);
            if (!line) return;
            const id = extractId(line);
            if (id != null) {
                const name = stripIdFromName(line);
                out.push({ id, name, line: idx + 1 });
                return;
            }
            // "12345, Foydali" / "12345 - Foydali" / "12345 Foydali"
            const m = line.match(/^(\d{3,8})\s*[,;:\t—–\-|\s]+\s*(.+)$/);
            if (m) {
                out.push({ id: parseInt(m[1], 10), name: clean(m[2]), line: idx + 1 });
                return;
            }
            // строка — просто имя
            if (line.length > 2) out.push({ id: null, name: line, line: idx + 1 });
        });
        return out;
    }

    /* ---------- Tabular ---------- */
    function fromTabular(rows, sourceLines) {
        if (!rows.length) return [];
        // Возможно есть header — проверим первую строку
        const { idIdx: hIdIdx, nameIdx: hNameIdx } = pickIdAndNameColumns(rows[0]);
        let dataRows = rows;
        let idIdx = hIdIdx;
        let nameIdx = hNameIdx;
        if (hIdIdx !== -1 || hNameIdx !== -1) {
            dataRows = rows.slice(1);
        } else {
            const auto = autoColumnsByContent(rows);
            idIdx = auto.idIdx;
            nameIdx = auto.nameIdx;
        }
        const out = [];
        dataRows.forEach((r, rowIdx) => {
            if (!r || r.every(c => !clean(c))) return;
            let id = null;
            let name = '';
            if (idIdx !== -1) {
                const v = clean(r[idIdx]);
                if (looksLikeId(v)) id = parseInt(v, 10);
                else { const ex = extractId(v); if (ex != null) id = ex; }
            }
            if (nameIdx !== -1) name = clean(r[nameIdx]);
            // Fallback: если id ещё не нашли — поищем во всей строке
            if (id == null) {
                for (const c of r) {
                    const ex = extractId(c);
                    if (ex != null) { id = ex; break; }
                }
            }
            // Fallback name: самая длинная не-id ячейка
            if (!name) {
                let best = '';
                for (let i = 0; i < r.length; i++) {
                    if (i === idIdx) continue;
                    const c = clean(r[i]);
                    if (c.length > best.length && !looksLikeId(c)) best = c;
                }
                name = stripIdFromName(best);
            }
            if (id != null || name) out.push({ id, name, line: (sourceLines ? sourceLines[rowIdx] : null) || (rowIdx + 1) });
        });
        return out;
    }

    /* ---------- Public: parseText ---------- */
    function parseText(text) {
        if (!text) return { items: [], errors: [] };
        const errors = [];

        // 1) JSON?
        const j = tryParseJson(text);
        if (j != null) {
            const items = normalize(fromJsonArray(j));
            return { items, errors, source: 'json' };
        }

        const lines = splitLines(text).map(l => l.replace(/\s+$/, ''));
        const nonEmpty = lines.filter(l => l.trim());
        if (!nonEmpty.length) return { items: [], errors: ['Пустой ввод'] };

        // 2) Tabular detection
        const delim = detectDelimiter(nonEmpty[0]);
        if (delim) {
            const rows = [];
            const sourceLines = [];
            lines.forEach((line, i) => {
                if (!line.trim()) return;
                if (delim === '|' && isMdSeparator(line)) return;
                rows.push(splitRow(line, delim));
                sourceLines.push(i + 1);
            });
            if (rows.length >= 1) {
                const items = normalize(fromTabular(rows, sourceLines));
                if (items.length) return { items, errors, source: 'table:' + delim };
            }
        }

        // 3) Free-form
        const items = normalize(fromFreeForm(text));
        return { items, errors, source: 'freeform' };
    }

    /* ---------- Public: parseFile (delegates to backend) ---------- */
    async function parseFile(file) {
        if (!file) throw new Error('parseFile: file required');
        // Простые форматы — TXT/CSV/JSON — парсим в браузере (быстрее, без сети)
        const ext = (file.name || '').toLowerCase().match(/\.[a-z0-9]+$/);
        const localExts = ['.txt', '.csv', '.tsv', '.json', '.md'];
        if (ext && localExts.indexOf(ext[0]) !== -1) {
            const text = await file.text();
            return Object.assign({ filename: file.name }, parseText(text));
        }
        // Бинарные форматы (XLSX/PDF/DOCX) — на backend
        const fd = new FormData();
        fd.append('file', file);
        const resp = await fetch('/api/availability/parse', { method: 'POST', body: fd });
        if (!resp.ok) {
            let msg = 'HTTP ' + resp.status;
            try { const j = await resp.json(); if (j && j.error) msg = j.error; } catch (e) {}
            throw new Error(msg);
        }
        const j = await resp.json();
        return { items: normalize(j.items || []), errors: [], source: 'backend:' + (j.source || 'file'), filename: j.filename || file.name };
    }

    /* ---------- normalize: dedup + sanitize ---------- */
    function normalize(items) {
        const seen = new Map();
        const out = [];
        (items || []).forEach(it => {
            const id = it && it.id != null ? Number(it.id) : null;
            let name = clean((it && it.name) || '');
            // если имя содержит "(ID …)" — выдернуть и почистить
            if (name) {
                const ex = extractId(name);
                if (ex != null && id == null) it.id = ex;
                name = stripIdFromName(name);
            }
            const key = id != null ? 'i:' + id : 'n:' + name.toLowerCase();
            if (!key || key === 'n:') return;
            if (seen.has(key)) {
                // обогащение: если в существующем нет имени — возьмём
                const prev = seen.get(key);
                if (!prev.name && name) prev.name = name;
                if (prev.id == null && id != null) prev.id = id;
                return;
            }
            const rec = { id: (id != null && !isNaN(id) ? id : null), name: name };
            if (it.line != null) rec.line = it.line;
            seen.set(key, rec);
            out.push(rec);
        });
        return out;
    }

    /* ---------- toCsv ---------- */
    function toCsv(items, headers) {
        const rows = [(headers || ['id', 'name'])];
        (items || []).forEach(it => rows.push([it.id == null ? '' : it.id, it.name || '']));
        return rows.map(r => r.map(v => {
            const s = String(v == null ? '' : v);
            return /[",\n;]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
        }).join(',')).join('\r\n');
    }

    global.SbmsOrderParse = {
        parseText: parseText,
        parseFile: parseFile,
        normalize: normalize,
        toCsv: toCsv,
        // expose helpers — на случай тонкой кастомизации
        _extractId: extractId,
        _stripIdFromName: stripIdFromName,
    };
})(typeof window !== 'undefined' ? window : globalThis);
