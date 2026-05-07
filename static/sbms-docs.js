/* ============================================================
   SBMS Docs — reusable «Документация» + «Приказ» buttons.

   API
   ---
     SbmsDocs.openDocs(name)           — открыть Confluence-поиск
     SbmsDocs.openOrders(name)         — открыть MyCR-поиск (приказы, FAQ)
     SbmsDocs.cell(name [, opts])      — вернуть HTML двух кнопок
     SbmsDocs.cellTd(name [, opts])    — то же, но обёрнутое в <td>…</td>
     SbmsDocs.configure({               — переопределить базовые URL
       confluenceBase, mycrBase, mycrCategory
     })
     SbmsDocs.bind(rootEl)             — навесить event-делегацию (опционально,
                                          по умолчанию используется inline onclick)

   Использование (inline, как в tariff_test):
     `<td>${SbmsDocs.cell(name)}</td>`

   Если name пустой / null → возвращается «—» (disabled state).
   Кнопки tab-able, имеют aria-label, открываются в новой вкладке (noopener).

   Подключение:
     <link rel="stylesheet" href="/static/sbms-docs.css">
     <script src="/static/sbms-docs.js"></script>
   ============================================================ */
(function (global) {
    'use strict';

    if (global.SbmsDocs) return;

    var DEFAULTS = {
        confluenceBase: 'https://confluence.local.domain',
        mycrBase: 'https://mycr-new',
        mycrCategory: 'faq',
    };
    var CFG = Object.assign({}, DEFAULTS);

    /* Inline SVG (stroke icons, ~14×14 inside 26×26 button) */
    var ICON_DOC =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<path d="M2 4.5A1.5 1.5 0 0 1 3.5 3H10a3 3 0 0 1 2 .8A3 3 0 0 1 14 3h6.5A1.5 1.5 0 0 1 22 4.5v13a1.5 1.5 0 0 1-1.5 1.5H14a2 2 0 0 0-2 2 2 2 0 0 0-2-2H3.5A1.5 1.5 0 0 1 2 17.5v-13Z"/>' +
        '<path d="M12 5v16"/></svg>';
    var ICON_ORDER =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<path d="M9 4h6a1 1 0 0 1 1 1v1H8V5a1 1 0 0 1 1-1Z"/>' +
        '<path d="M16 5h2.5A1.5 1.5 0 0 1 20 6.5v13A1.5 1.5 0 0 1 18.5 21h-13A1.5 1.5 0 0 1 4 19.5v-13A1.5 1.5 0 0 1 5.5 5H8"/>' +
        '<path d="m9 13 2 2 4-4"/></svg>';

    function escAttr(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    function normalize(name) {
        return String(name == null ? '' : name).trim();
    }

    function openDocs(name) {
        var q = normalize(name);
        if (!q) return false;
        var url = CFG.confluenceBase.replace(/\/$/, '') +
            '/dosearchsite.action?queryString=' + encodeURIComponent(q);
        global.open(url, '_blank', 'noopener,noreferrer');
        return true;
    }

    function openOrders(name) {
        var q = normalize(name);
        if (!q) return false;
        // MyCR encodes spaces as «+» (form-encoded), не «%20».
        var term = encodeURIComponent(q).replace(/%20/g, '+');
        var url = CFG.mycrBase.replace(/\/$/, '') +
            '/search/search_main/?search_cross=' + term +
            '&category=' + encodeURIComponent(CFG.mycrCategory);
        global.open(url, '_blank', 'noopener,noreferrer');
        return true;
    }

    /**
     * @param {string} name — название тарифа/пакета/услуги
     * @param {{kind?: 'tariff'|'pack'|'service', label?: string}} [opts]
     * @returns {string} HTML двух icon-кнопок (или «—», если name пустое)
     */
    function cell(name, opts) {
        var q = normalize(name);
        if (!q) {
            return '<span class="sd-empty" title="Название отсутствует">—</span>';
        }
        var safe = escAttr(q);
        var labelDoc = (opts && opts.label) || ('Документация в Confluence: ' + q);
        var labelOrd = 'Приказы в MyCR: ' + q;
        return '<span class="sd-cell">' +
            '<button type="button" class="sd-btn sd-btn--doc" ' +
                'aria-label="' + escAttr(labelDoc) + '" ' +
                'title="' + escAttr(labelDoc) + '" ' +
                'data-sd-action="docs" data-sd-name="' + safe + '">' + ICON_DOC + '</button>' +
            '<button type="button" class="sd-btn sd-btn--order" ' +
                'aria-label="' + escAttr(labelOrd) + '" ' +
                'title="' + escAttr(labelOrd) + '" ' +
                'data-sd-action="orders" data-sd-name="' + safe + '">' + ICON_ORDER + '</button>' +
            '</span>';
    }

    function cellTd(name, opts) {
        return '<td class="sd-td" style="text-align:center;width:62px;">' + cell(name, opts) + '</td>';
    }

    /** Делегированный click-listener — навешивается один раз на любой контейнер. */
    function bind(root) {
        var el = root || global.document.body;
        if (!el || el.__sbmsDocsBound) return;
        el.__sbmsDocsBound = true;
        el.addEventListener('click', function (ev) {
            var btn = ev.target && ev.target.closest && ev.target.closest('[data-sd-action]');
            if (!btn) return;
            ev.preventDefault();
            ev.stopPropagation();
            var act = btn.getAttribute('data-sd-action');
            var nm = btn.getAttribute('data-sd-name') || '';
            if (act === 'docs') openDocs(nm);
            else if (act === 'orders') openOrders(nm);
        });
    }

    function configure(opts) {
        if (!opts) return Object.assign({}, CFG);
        Object.keys(opts).forEach(function (k) {
            if (CFG.hasOwnProperty(k) && opts[k] != null) CFG[k] = opts[k];
        });
        return Object.assign({}, CFG);
    }

    var api = {
        openDocs: openDocs,
        openOrders: openOrders,
        cell: cell,
        cellTd: cellTd,
        bind: bind,
        configure: configure,
    };

    Object.defineProperty(api, 'config', {
        get: function () { return Object.assign({}, CFG); },
        enumerable: true,
    });

    global.SbmsDocs = api;

    /* Авто-bind на DOMReady — чтобы работало без явного вызова. */
    function autoBind() { bind(global.document.body); }
    if (global.document && global.document.readyState !== 'loading') {
        autoBind();
    } else if (global.document) {
        global.document.addEventListener('DOMContentLoaded', autoBind, { once: true });
    }
})(typeof window !== 'undefined' ? window : globalThis);
