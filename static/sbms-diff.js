/**
 * SbmsDiff — визуальный diff двух JSON-объектов.
 *
 *   SbmsDiff.render(before, after, container, opts);
 *
 *   opts:
 *     title         — заголовок над таблицей (string, optional)
 *     collapseEqual — скрыть строки, где значение не изменилось (default: true)
 *     ignore        — массив путей которые игнорировать ['meta.ts', 'requestId']
 *     numberFormat  — кастомный форматтер чисел (val) => string
 *     onlyKeys      — показать только эти top-level поля
 *
 *   Возвращает: { element, stats: { added, removed, changed, equal } }
 *
 * Стилизация: классы .sb-diff-* поверх sbms-design.css токенов.
 */
(function (global) {
    'use strict';

    const STYLE = `
    .sb-diff{background:var(--bg-surface);border:1px solid var(--border);
        border-radius:var(--radius-lg);overflow:hidden;font-family:var(--font-sans)}
    .sb-diff__head{display:flex;align-items:center;justify-content:space-between;
        gap:12px;padding:10px 14px;border-bottom:1px solid var(--border);
        background:var(--bg-subtle)}
    .sb-diff__title{font-size:12px;font-weight:600;color:var(--text-2);
        text-transform:uppercase;letter-spacing:.04em}
    .sb-diff__stats{display:inline-flex;gap:6px;font-size:11px}
    .sb-diff__chip{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;
        border-radius:999px;font-weight:600;font-variant-numeric:tabular-nums;
        background:var(--neutral-soft);color:var(--text-2)}
    .sb-diff__chip--added   {background:var(--success-soft);color:var(--success-text)}
    .sb-diff__chip--removed {background:var(--danger-soft);color:var(--danger-text)}
    .sb-diff__chip--changed {background:var(--warning-soft);color:var(--warning-text)}
    .sb-diff__chip--equal   {background:var(--neutral-soft);color:var(--text-3)}
    .sb-diff__toggle{font-size:11px;color:var(--text-2);background:transparent;
        border:1px solid var(--border);padding:2px 8px;border-radius:var(--radius-sm);
        cursor:pointer;font-family:var(--font-sans)}
    .sb-diff__toggle:hover{background:var(--bg-elevated);color:var(--text-1)}
    .sb-diff__toggle.is-on{background:var(--brand-soft);color:var(--brand);border-color:transparent}
    .sb-diff table{width:100%;border-collapse:separate;border-spacing:0;font-size:13px}
    .sb-diff th{background:var(--bg-subtle);font-size:11px;color:var(--text-3);
        text-transform:uppercase;letter-spacing:.04em;font-weight:600;
        padding:8px 12px;text-align:left;border-bottom:1px solid var(--border-strong);
        position:sticky;top:0;z-index:1}
    .sb-diff td{padding:6px 12px;border-bottom:1px solid var(--border);vertical-align:top;
        font-family:var(--font-mono);font-size:12px;line-height:1.45;color:var(--text-1)}
    .sb-diff tr:last-child td{border-bottom:0}
    .sb-diff tbody tr:hover td{background:var(--bg-subtle)}
    .sb-diff__path{font-family:var(--font-mono);color:var(--text-2);font-weight:500;
        font-size:12px;white-space:nowrap}
    .sb-diff__val{word-break:break-all;max-width:280px;overflow-wrap:break-word;
        color:var(--text-1)}
    .sb-diff__val--null{color:var(--text-3);font-style:italic}
    .sb-diff__val--obj{color:var(--text-2)}
    .sb-diff__delta{font-family:var(--font-mono);font-size:11px;font-weight:600;white-space:nowrap}
    .sb-diff tr.row-added td.cell-after  {background:var(--success-soft)}
    .sb-diff tr.row-added td.cell-after .sb-diff__val{color:var(--success-text)}
    .sb-diff tr.row-removed td.cell-before {background:var(--danger-soft)}
    .sb-diff tr.row-removed td.cell-before .sb-diff__val{color:var(--danger-text)}
    .sb-diff tr.row-changed td.cell-before {background:var(--danger-soft)}
    .sb-diff tr.row-changed td.cell-before .sb-diff__val{color:var(--danger-text)}
    .sb-diff tr.row-changed td.cell-after  {background:var(--success-soft)}
    .sb-diff tr.row-changed td.cell-after  .sb-diff__val{color:var(--success-text)}
    .sb-diff tr.row-equal td .sb-diff__val{color:var(--text-3)}
    .sb-diff__num-pos{color:var(--success-text)}
    .sb-diff__num-neg{color:var(--danger-text)}
    .sb-diff__empty{padding:32px;text-align:center;color:var(--text-3);font-size:13px}
    `;

    function injectStyle() {
        if (document.getElementById('sbms-diff-style')) return;
        const s = document.createElement('style');
        s.id = 'sbms-diff-style';
        s.textContent = STYLE;
        document.head.appendChild(s);
    }

    /* ========== Diff core ========== */

    // Сбор всех путей key1.key2[0].key3 из двух объектов
    function flatten(obj, prefix, out) {
        if (obj == null || typeof obj !== 'object') {
            out[prefix || ''] = obj;
            return out;
        }
        if (Array.isArray(obj)) {
            // массив скаляров — целиком, иначе по индексу
            const allScalar = obj.every(v => v == null || typeof v !== 'object');
            if (allScalar) {
                out[prefix || ''] = obj;
            } else {
                if (!obj.length) out[prefix || ''] = [];
                obj.forEach((v, i) => flatten(v, (prefix ? prefix + '[' + i + ']' : '[' + i + ']'), out));
            }
            return out;
        }
        const keys = Object.keys(obj);
        if (!keys.length) {
            out[prefix || ''] = {};
            return out;
        }
        keys.forEach(k => {
            const next = prefix ? prefix + '.' + k : k;
            flatten(obj[k], next, out);
        });
        return out;
    }

    function isEqual(a, b) {
        if (a === b) return true;
        if (a == null || b == null) return a === b;
        if (typeof a !== typeof b) return false;
        if (typeof a !== 'object') return false;
        if (Array.isArray(a) !== Array.isArray(b)) return false;
        try {
            return JSON.stringify(a) === JSON.stringify(b);
        } catch (_) { return false; }
    }

    function fmtVal(v, numberFormat) {
        if (v === undefined) return { html: '<span class="sb-diff__val sb-diff__val--null">—</span>', cls: 'val-undef' };
        if (v === null)      return { html: '<span class="sb-diff__val sb-diff__val--null">null</span>', cls: 'val-null' };
        if (typeof v === 'boolean') return { html: '<span class="sb-diff__val">' + (v ? 'true' : 'false') + '</span>', cls: 'val-bool' };
        if (typeof v === 'number') {
            const formatted = (typeof numberFormat === 'function') ? numberFormat(v) : String(v);
            return { html: '<span class="sb-diff__val">' + escapeHtml(formatted) + '</span>', cls: 'val-num' };
        }
        if (typeof v === 'string') {
            return { html: '<span class="sb-diff__val">' + escapeHtml(v || '""') + '</span>', cls: 'val-str' };
        }
        // object / array
        let s;
        try { s = JSON.stringify(v); } catch (_) { s = String(v); }
        if (s && s.length > 120) s = s.slice(0, 117) + '…';
        return { html: '<span class="sb-diff__val sb-diff__val--obj">' + escapeHtml(s) + '</span>', cls: 'val-obj' };
    }

    function fmtDelta(before, after) {
        if (typeof before === 'number' && typeof after === 'number') {
            const d = after - before;
            if (d === 0) return '';
            const sign = d > 0 ? '+' : '';
            const cls = d > 0 ? 'sb-diff__num-pos' : 'sb-diff__num-neg';
            return '<span class="sb-diff__delta ' + cls + '">' + sign + d.toLocaleString('ru-RU') + '</span>';
        }
        return '';
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function shouldIgnore(path, ignoreList) {
        if (!ignoreList || !ignoreList.length) return false;
        return ignoreList.some(p =>
            path === p ||
            path.startsWith(p + '.') ||
            path.startsWith(p + '[')
        );
    }

    function diff(before, after, opts) {
        opts = opts || {};
        const A = flatten(before || {}, '', {});
        const B = flatten(after  || {}, '', {});
        const allKeys = Array.from(new Set([...Object.keys(A), ...Object.keys(B)])).sort();

        const rows = [];
        const stats = { added: 0, removed: 0, changed: 0, equal: 0 };

        allKeys.forEach(k => {
            if (k === '') return;
            if (shouldIgnore(k, opts.ignore)) return;
            if (opts.onlyKeys && opts.onlyKeys.length) {
                const top = k.split('.')[0].replace(/\[.*$/, '');
                if (opts.onlyKeys.indexOf(top) === -1) return;
            }
            const inA = Object.prototype.hasOwnProperty.call(A, k);
            const inB = Object.prototype.hasOwnProperty.call(B, k);
            const va = A[k]; const vb = B[k];

            let kind;
            if (inA && !inB) { kind = 'removed'; stats.removed++; }
            else if (!inA && inB) { kind = 'added'; stats.added++; }
            else if (!isEqual(va, vb)) { kind = 'changed'; stats.changed++; }
            else { kind = 'equal'; stats.equal++; }

            rows.push({ path: k, before: va, after: vb, kind });
        });

        return { rows, stats };
    }

    /* ========== Render ========== */

    function render(before, after, container, opts) {
        opts = opts || {};
        injectStyle();

        const host = typeof container === 'string' ? document.querySelector(container) : container;
        if (!host) {
            console.warn('[SbmsDiff] container not found:', container);
            return null;
        }

        const collapseEqual = opts.collapseEqual !== false; // default true
        let collapsed = collapseEqual;

        const { rows, stats } = diff(before, after, opts);

        const wrap = document.createElement('div');
        wrap.className = 'sb-diff';

        const head = document.createElement('div');
        head.className = 'sb-diff__head';
        head.innerHTML = `
            <span class="sb-diff__title">${escapeHtml(opts.title || 'Изменения')}</span>
            <span class="sb-diff__stats">
                ${stats.added   ? `<span class="sb-diff__chip sb-diff__chip--added">+${stats.added}</span>`     : ''}
                ${stats.changed ? `<span class="sb-diff__chip sb-diff__chip--changed">~${stats.changed}</span>` : ''}
                ${stats.removed ? `<span class="sb-diff__chip sb-diff__chip--removed">−${stats.removed}</span>` : ''}
                ${stats.equal   ? `<span class="sb-diff__chip sb-diff__chip--equal">=${stats.equal}</span>`     : ''}
            </span>
            <button type="button" class="sb-diff__toggle ${collapsed ? '' : 'is-on'}">
                ${collapsed ? 'Показать неизменённые' : 'Скрыть неизменённые'}
            </button>`;
        wrap.appendChild(head);

        const tableBox = document.createElement('div');
        tableBox.style.maxHeight = opts.maxHeight || '60vh';
        tableBox.style.overflow = 'auto';
        wrap.appendChild(tableBox);

        function paint() {
            const visibleRows = collapsed ? rows.filter(r => r.kind !== 'equal') : rows;
            if (!visibleRows.length) {
                tableBox.innerHTML = `<div class="sb-diff__empty">${collapsed && rows.length ? 'Нет изменений (всё совпадает)' : 'Нет данных для сравнения'}</div>`;
                return;
            }
            let html = '<table><thead><tr><th style="width:30%;">Поле</th><th style="width:30%;">Было</th><th style="width:30%;">Стало</th><th>Δ</th></tr></thead><tbody>';
            visibleRows.forEach(r => {
                const va = fmtVal(r.before, opts.numberFormat);
                const vb = fmtVal(r.after,  opts.numberFormat);
                const d  = fmtDelta(r.before, r.after);
                html += `<tr class="row-${r.kind}">
                    <td><span class="sb-diff__path">${escapeHtml(r.path)}</span></td>
                    <td class="cell-before">${r.kind === 'added' ? '<span class="sb-diff__val sb-diff__val--null">—</span>' : va.html}</td>
                    <td class="cell-after">${r.kind === 'removed' ? '<span class="sb-diff__val sb-diff__val--null">—</span>' : vb.html}</td>
                    <td>${d}</td>
                </tr>`;
            });
            html += '</tbody></table>';
            tableBox.innerHTML = html;
        }

        head.querySelector('.sb-diff__toggle').addEventListener('click', (ev) => {
            collapsed = !collapsed;
            const btn = ev.currentTarget;
            btn.textContent = collapsed ? 'Показать неизменённые' : 'Скрыть неизменённые';
            btn.classList.toggle('is-on', !collapsed);
            paint();
        });

        host.innerHTML = '';
        host.appendChild(wrap);
        paint();

        return { element: wrap, stats };
    }

    global.SbmsDiff = { render, diff };
})(window);
