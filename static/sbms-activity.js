/* ============================================================
   SBMS Activity — reusable timeline / activity feed.

   Универсальный feed событий по абоненту:
     • смены тарифа · подключение/отключение пакетов и услуг
     • lifecycle (Active / Suspend / Closed)
     • платежи · абонплата · разовые списания
     • корректировки · обещанные платежи

   Источник по умолчанию — POST /api/history (см. server.py)
   но fetcher переопределяемый — модуль можно использовать
   для любого источника events: [{type,date,title,subtitle,description,status,amount,raw}].

   API
   ---
     SbmsActivity.mount({
       rootEl,                       — DOM-узел контейнера
       msisdn,                       — номер
       fetcher?: async ({msisdn,days,limit}) => {events, counts, warnings?},
       initialDays?:  90,
       initialLimit?: 200,
       onEventClick?: (ev) => void,
     }) → { load(force), reload(), destroy(), setMsisdn(s) }

   Зависимости (опциональные, gracefully degrade):
     window.SbmsAuth   — для добавления authToken к POST /api/history
     window.SbmsToast  — для feedback при copy()
   ============================================================ */
(function (global) {
    'use strict';

    if (global.SbmsActivity) return;

    /* ---------- Event type catalog ---------- */
    /* iconKey → одна из 16-ти SVG-иконок в ICONS */
    /* color → semantic accent: brand|info|success|warning|danger|neutral */
    const TYPES = {
        tariff_change:           { label: 'Смена тарифа',         iconKey: 'arrow-cycle', color: 'brand'   },
        pack_activate:           { label: 'Подключение пакета',   iconKey: 'box-plus',    color: 'success' },
        pack_deactivate:         { label: 'Отключение пакета',    iconKey: 'box-minus',   color: 'warning' },
        service_activate:        { label: 'Подключение услуги',   iconKey: 'bell-plus',   color: 'success' },
        service_deactivate:      { label: 'Отключение услуги',    iconKey: 'bell-minus',  color: 'warning' },
        subscription_activate:   { label: 'Подключение подписки', iconKey: 'link-plus',   color: 'success' },
        subscription_deactivate: { label: 'Отключение подписки',  iconKey: 'link-minus',  color: 'warning' },
        lifecycle:               { label: 'Состояние номера',     iconKey: 'sim',         color: 'info'    },
        payment:                 { label: 'Пополнение',           iconKey: 'card-plus',   color: 'success' },
        charge_recurring:        { label: 'Списание АП',          iconKey: 'calendar',    color: 'warning' },
        charge_one_time:         { label: 'Разовое списание',     iconKey: 'bolt',        color: 'warning' },
        adjustment:              { label: 'Корректировка',        iconKey: 'scale',       color: 'info'    },
        promised_payment:        { label: 'Обещанный платёж',     iconKey: 'hourglass',   color: 'info'    },
        unknown:                 { label: 'Прочее',                iconKey: 'dot',         color: 'neutral' },
    };

    const ICONS = (function () {
        const SVG = (path) =>
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
            'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + path + '</svg>';
        return {
            'arrow-cycle': SVG('<path d="M3 12a9 9 0 0 1 15.5-6.3L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15.5 6.3L3 16"/><path d="M3 21v-5h5"/>'),
            'box-plus':    SVG('<path d="M21 8 12 3 3 8v8l9 5 9-5V8z"/><path d="M3 8l9 5 9-5"/><path d="M12 13v5"/>'),
            'box-minus':   SVG('<path d="M21 8 12 3 3 8v8l9 5 9-5V8z"/><path d="M3 8l9 5 9-5"/><path d="M9 16h6"/>'),
            'bell-plus':   SVG('<path d="M6 8a6 6 0 0 1 12 0c0 7 3 7 3 9H3c0-2 3-2 3-9z"/><path d="M9 19a3 3 0 0 0 6 0"/>'),
            'bell-minus':  SVG('<path d="M6 8a6 6 0 0 1 12 0c0 7 3 7 3 9H3c0-2 3-2 3-9z"/><path d="M9 19a3 3 0 0 0 6 0"/><path d="M9 12h6"/>'),
            'link-plus':   SVG('<path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 1 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/>'),
            'link-minus':  SVG('<path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 1 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/><path d="M8 16l8-8" stroke-dasharray="2 2"/>'),
            'sim':         SVG('<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 12h6M12 9v6"/>'),
            'card-plus':   SVG('<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20"/><path d="M16 16h4M18 14v4"/>'),
            'calendar':    SVG('<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>'),
            'bolt':        SVG('<path d="m13 2-7 12h6l-1 8 7-12h-6z"/>'),
            'scale':       SVG('<path d="M12 3v18M5 7l-3 6h6zM19 7l-3 6h6z"/>'),
            'hourglass':   SVG('<path d="M6 2h12M6 22h12"/><path d="M6 2c0 6 6 6 6 10s-6 4-6 10M18 2c0 6-6 6-6 10s6 4 6 10"/>'),
            'dot':         SVG('<circle cx="12" cy="12" r="3"/>'),
            'search':      SVG('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>'),
            'refresh':     SVG('<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>'),
            'copy':        SVG('<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>'),
            'chevron':     SVG('<path d="m6 9 6 6 6-6"/>'),
        };
    })();

    /* ---------- helpers ---------- */
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function debounce(fn, ms) {
        let t = null;
        return function () {
            const args = arguments, ctx = this;
            clearTimeout(t);
            t = setTimeout(() => fn.apply(ctx, args), ms);
        };
    }

    function fmtDate(iso) {
        if (!iso) return null;
        const d = new Date(iso);
        if (isNaN(d.getTime())) return null;
        return d;
    }

    function fmtTime(d) {
        if (!d) return '';
        const hh = String(d.getHours()).padStart(2, '0');
        const mm = String(d.getMinutes()).padStart(2, '0');
        return hh + ':' + mm;
    }

    function fmtDay(d) {
        if (!d) return '—';
        const today = new Date();
        const sameDay = d.toDateString() === today.toDateString();
        if (sameDay) return 'Сегодня';
        const y = new Date(today); y.setDate(y.getDate() - 1);
        if (d.toDateString() === y.toDateString()) return 'Вчера';
        const months = ['янв','фев','мар','апр','мая','июн','июл','авг','сен','окт','ноя','дек'];
        const day = d.getDate();
        const mon = months[d.getMonth()];
        const yr = d.getFullYear();
        const cy = today.getFullYear();
        return `${day} ${mon}` + (yr !== cy ? ` ${yr}` : '');
    }

    function dayKey(d) {
        if (!d) return 'no-date';
        return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0')
                                + '-' + String(d.getDate()).padStart(2, '0');
    }

    function copyToClipboard(text) {
        if (!text) return Promise.resolve(false);
        if (global.navigator && global.navigator.clipboard && global.navigator.clipboard.writeText) {
            return global.navigator.clipboard.writeText(text).then(() => true, () => false);
        }
        try {
            const ta = global.document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            global.document.body.appendChild(ta);
            ta.select();
            global.document.execCommand('copy');
            global.document.body.removeChild(ta);
            return Promise.resolve(true);
        } catch (e) { return Promise.resolve(false); }
    }

    function toast(kind, msg) {
        if (global.SbmsToast && global.SbmsToast[kind]) {
            try { global.SbmsToast[kind](msg); return; } catch (e) {}
        }
        if (kind === 'error') console.error('[SbmsActivity]', msg);
        else console.log('[SbmsActivity]', msg);
    }

    /* ---------- statuses ---------- */
    /* Эвристика mapping `event.status` (произвольная строка от API) → один из 4: success/warning/failed/pending */
    function classifyStatus(ev) {
        const s = String((ev && ev.status) || '').toLowerCase().trim();
        if (!s) {
            // Default по типу события
            if (ev.type === 'pack_deactivate' || ev.type === 'service_deactivate' ||
                ev.type === 'subscription_deactivate' || ev.type === 'charge_one_time' ||
                ev.type === 'charge_recurring') return 'warning';
            return 'success';
        }
        if (/(ошибк|fail|fault|reject|отказ|denied|invalid)/.test(s)) return 'failed';
        if (/(ожида|pending|очеред|wait|in.?progress)/.test(s)) return 'pending';
        if (/(выполн|complet|done|success|active|подключ|оплач|paid|ок$|ok$)/.test(s)) return 'success';
        if (/(отключ|deactiv|cancel|expired|истёк|закрыт)/.test(s)) return 'warning';
        return 'success';
    }

    const STATUS_LABEL = { success:'Успех', warning:'Внимание', failed:'Ошибка', pending:'В ожидании' };

    /* ---------- default fetcher ---------- */
    async function defaultFetcher(params) {
        const body = {
            msisdn: params.msisdn,
            days:   params.days,
            limit:  params.limit,
        };
        if (global.SbmsAuth && global.SbmsAuth.token) body.authToken = global.SbmsAuth.token;
        const resp = await fetch('/api/history', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (resp.status === 401 && global.SbmsAuth && global.SbmsAuth.clearSession) {
            global.SbmsAuth.clearSession('expired');
            throw new Error('Сессия истекла — войдите заново');
        }
        if (!resp.ok) {
            let msg = 'HTTP ' + resp.status;
            try { const j = await resp.json(); if (j && j.error) msg = j.error; if (j && j.message) msg = j.message; } catch (e) {}
            throw new Error(msg);
        }
        return resp.json();
    }

    /* ============================================================
       Instance
       ============================================================ */
    function mount(opts) {
        opts = opts || {};
        const root      = opts.rootEl;
        if (!root) throw new Error('SbmsActivity.mount: rootEl required');

        const fetcher   = opts.fetcher || defaultFetcher;
        const onClick   = typeof opts.onEventClick === 'function' ? opts.onEventClick : null;

        const state = {
            msisdn:      opts.msisdn || '',
            days:        Number(opts.initialDays) || 90,
            limit:       Number(opts.initialLimit) || 200,
            events:      [],
            counts:      {},
            warnings:    [],
            search:      '',
            typeFilter:  new Set(),       // empty = all
            statusFilter:new Set(),       // empty = all
            expanded:    new Set(),
            loading:     false,
            error:       null,
            loadedFor:   null,            // msisdn+days+limit signature
        };

        /* ---------- DOM scaffold ---------- */
        root.classList.add('sa-root');
        root.innerHTML = `
            <div class="sa-toolbar">
                <div class="sa-toolbar__row">
                    <div class="sa-period" role="tablist" aria-label="Период">
                        <button type="button" data-days="7"   class="sa-pill">7 дней</button>
                        <button type="button" data-days="30"  class="sa-pill">30 дней</button>
                        <button type="button" data-days="90"  class="sa-pill is-active">90 дней</button>
                        <button type="button" data-days="180" class="sa-pill">180</button>
                        <button type="button" data-days="365" class="sa-pill">Год</button>
                    </div>
                    <div class="sa-search">
                        <span class="sa-search__icon">${ICONS['search']}</span>
                        <input type="search" class="sa-search__input" placeholder="Поиск по названию, оператору, ID…" autocomplete="off">
                    </div>
                    <button type="button" class="sa-refresh" title="Обновить">
                        ${ICONS['refresh']}<span>Обновить</span>
                    </button>
                </div>
                <div class="sa-toolbar__row sa-toolbar__row--filters">
                    <div class="sa-chips" data-role="types"></div>
                    <div class="sa-meta" data-role="meta"></div>
                </div>
            </div>
            <div class="sa-body" data-role="body"></div>
        `;

        const els = {
            period:    root.querySelector('.sa-period'),
            search:    root.querySelector('.sa-search__input'),
            refresh:   root.querySelector('.sa-refresh'),
            chips:     root.querySelector('[data-role="types"]'),
            meta:      root.querySelector('[data-role="meta"]'),
            body:      root.querySelector('[data-role="body"]'),
        };

        /* ---------- listeners ---------- */
        els.period.addEventListener('click', (ev) => {
            const btn = ev.target.closest('[data-days]');
            if (!btn) return;
            const d = Number(btn.dataset.days);
            if (!d || d === state.days) return;
            els.period.querySelectorAll('.sa-pill').forEach(p => p.classList.toggle('is-active', p === btn));
            state.days = d;
            load(true);
        });

        els.search.addEventListener('input', debounce(() => {
            state.search = (els.search.value || '').trim().toLowerCase();
            renderBody();
        }, 120));

        els.refresh.addEventListener('click', () => load(true));

        /* Делегированный клик по chip-фильтру и по body (expand/copy) */
        els.chips.addEventListener('click', (ev) => {
            const chip = ev.target.closest('[data-chip-type]');
            if (!chip) return;
            const t = chip.dataset.chipType;
            if (t === '__all__') {
                state.typeFilter.clear();
            } else {
                if (state.typeFilter.has(t)) state.typeFilter.delete(t);
                else state.typeFilter.add(t);
            }
            renderChips();
            renderBody();
        });

        els.body.addEventListener('click', (ev) => {
            const copyBtn = ev.target.closest('[data-sa-copy]');
            if (copyBtn) {
                const idx = Number(copyBtn.dataset.saCopy);
                const e = state.events[idx];
                if (!e) return;
                copyToClipboard(JSON.stringify(e.raw || e, null, 2)).then(ok => {
                    if (ok) toast('success', 'Событие скопировано в буфер');
                    else toast('warn', 'Не удалось скопировать');
                });
                return;
            }
            const item = ev.target.closest('[data-sa-event]');
            if (!item) return;
            // Не разворачиваем при клике по интерактивным элементам внутри
            if (ev.target.closest('button, a, input, [data-no-expand]')) {
                if (ev.target.closest('[data-sa-event-toggle]')) {
                    /* fall through to toggle */
                } else return;
            }
            const id = item.dataset.saEvent;
            if (state.expanded.has(id)) state.expanded.delete(id);
            else state.expanded.add(id);
            const idx = Number(item.dataset.saIdx);
            const ev0 = state.events[idx];
            if (onClick && ev0) { try { onClick(ev0); } catch (e) {} }
            renderBody();   // re-render only body
        });

        /* ---------- public API ---------- */
        function setMsisdn(m) {
            state.msisdn = String(m || '').replace(/[^0-9]/g, '');
            state.loadedFor = null;
        }

        async function load(force) {
            if (!state.msisdn) {
                renderEmpty('Введите MSISDN');
                return;
            }
            const sig = state.msisdn + '|' + state.days + '|' + state.limit;
            if (!force && state.loadedFor === sig && state.events.length) {
                renderAll();
                return;
            }
            state.loading = true; state.error = null;
            renderLoading();
            try {
                const data = await fetcher({ msisdn: state.msisdn, days: state.days, limit: state.limit });
                state.events   = (data && data.events)   || [];
                state.counts   = (data && data.counts)   || {};
                state.warnings = (data && data.warnings) || [];
                state.loadedFor = sig;
                renderAll();
            } catch (e) {
                state.error = e.message || String(e);
                renderError();
            } finally {
                state.loading = false;
            }
        }

        function reload() { return load(true); }

        function destroy() {
            els.period.replaceWith(els.period.cloneNode(true));
            root.classList.remove('sa-root');
            root.innerHTML = '';
        }

        /* ---------- render ---------- */
        function renderAll() {
            renderChips();
            renderBody();
        }

        function renderChips() {
            const total = state.events.length;
            const active = state.typeFilter.size;
            const all = `<button type="button" class="sa-chip ${active === 0 ? 'is-active' : ''}" data-chip-type="__all__">
                            Все<span class="sa-chip__cnt">${total}</span>
                        </button>`;
            const order = Object.keys(state.counts).sort((a, b) => state.counts[b] - state.counts[a]);
            const chips = order.map(t => {
                const cfg = TYPES[t] || TYPES.unknown;
                const isOn = state.typeFilter.has(t);
                return `<button type="button" class="sa-chip sa-chip--${cfg.color} ${isOn ? 'is-active' : ''}"
                                data-chip-type="${esc(t)}" title="${esc(cfg.label)}">
                            <span class="sa-chip__icon">${ICONS[cfg.iconKey]}</span>
                            ${esc(cfg.label)}
                            <span class="sa-chip__cnt">${state.counts[t]}</span>
                        </button>`;
            }).join('');
            els.chips.innerHTML = all + chips;
        }

        function passesFilters(ev) {
            if (state.typeFilter.size && !state.typeFilter.has(ev.type)) return false;
            if (state.search) {
                const hay = (ev.title + ' ' + (ev.subtitle || '') + ' ' + (ev.description || '') +
                             ' ' + (ev.status || '') + ' ' + ev.type).toLowerCase();
                if (hay.indexOf(state.search) === -1) return false;
            }
            return true;
        }

        function groupByDay(events) {
            const groups = new Map();   // dayKey → {dayLabel, items: []}
            events.forEach(ev => {
                const d = fmtDate(ev.date);
                const k = dayKey(d);
                if (!groups.has(k)) {
                    groups.set(k, { day: d, label: fmtDay(d), items: [] });
                }
                groups.get(k).items.push(ev);
            });
            // порядок: уже отсортированы desc извне; группы тоже desc
            return [...groups.values()];
        }

        function renderLoading() {
            els.body.innerHTML = `
                <div class="sa-status">
                    <div class="sa-spinner"></div>
                    <div class="sa-status__text">Загружаем события за ${state.days} дн…</div>
                </div>`;
        }

        function renderError() {
            els.body.innerHTML = `
                <div class="sa-status sa-status--err">
                    <div class="sa-status__title">Не удалось загрузить</div>
                    <div class="sa-status__text">${esc(state.error || 'Ошибка')}</div>
                    <button type="button" class="sa-btn sa-btn--ghost" data-act="retry">Повторить</button>
                </div>`;
            const r = els.body.querySelector('[data-act="retry"]');
            if (r) r.addEventListener('click', () => load(true), { once: true });
        }

        function renderEmpty(message) {
            els.body.innerHTML = `
                <div class="sa-status sa-status--empty">
                    <div class="sa-status__text">${esc(message || 'Нет данных за выбранный период')}</div>
                </div>`;
        }

        function renderMeta(visible, total, warnings) {
            let html = `<span>Показано: <b>${visible}</b> из <b>${total}</b></span>`;
            if (warnings && warnings.length) {
                html += `<span class="sa-meta__warn" title="Источники с ошибкой">⚠ ${warnings.length} источ.</span>`;
            }
            els.meta.innerHTML = html;
        }

        function renderBody() {
            const total = state.events.length;
            if (state.loading) return renderLoading();
            if (state.error)   return renderError();
            if (!total)        { renderMeta(0, 0, state.warnings); return renderEmpty(); }

            const filtered = state.events.filter(passesFilters);
            renderMeta(filtered.length, total, state.warnings);
            if (!filtered.length) {
                els.body.innerHTML = `
                    <div class="sa-status sa-status--empty">
                        <div class="sa-status__text">Нет событий по текущим фильтрам</div>
                    </div>`;
                return;
            }

            const groups = groupByDay(filtered);
            els.body.innerHTML = groups.map(g => {
                const items = g.items.map((ev, i) => renderEvent(ev, state.events.indexOf(ev))).join('');
                return `
                    <section class="sa-day">
                        <header class="sa-day__head">
                            <span class="sa-day__label">${esc(g.label)}</span>
                            <span class="sa-day__count">${g.items.length}</span>
                        </header>
                        <ul class="sa-list">${items}</ul>
                    </section>`;
            }).join('');
        }

        function renderEvent(ev, globalIdx) {
            const cfg = TYPES[ev.type] || TYPES.unknown;
            const status = classifyStatus(ev);
            const id = String(globalIdx);
            const isOpen = state.expanded.has(id);

            const d = fmtDate(ev.date);
            const time = fmtTime(d) || '—:—';

            const amountHtml = renderAmount(ev);
            const statusBadge = ev.status
                ? `<span class="sa-status-tag sa-status-tag--${status}" title="${esc(STATUS_LABEL[status] || status)}">${esc(ev.status)}</span>`
                : '';

            const desc = ev.description ? `<div class="sa-event__desc">${esc(ev.description)}</div>` : '';

            const detailsHtml = isOpen ? renderDetails(ev, globalIdx) : '';

            return `
                <li class="sa-event sa-event--${cfg.color} ${isOpen ? 'is-open' : ''}"
                    data-sa-event="${id}" data-sa-idx="${globalIdx}">
                    <div class="sa-event__icon" title="${esc(cfg.label)}">${ICONS[cfg.iconKey]}</div>
                    <div class="sa-event__time" title="${esc(ev.date || '')}">${esc(time)}</div>
                    <div class="sa-event__main">
                        <div class="sa-event__head">
                            <span class="sa-event__title">${esc(ev.title || cfg.label)}</span>
                            ${ev.subtitle ? `<span class="sa-event__sub">${esc(ev.subtitle)}</span>` : ''}
                            ${amountHtml}
                            ${statusBadge}
                        </div>
                        ${desc}
                        ${detailsHtml}
                    </div>
                    <button type="button" class="sa-event__toggle" data-sa-event-toggle aria-expanded="${isOpen}"
                            title="${isOpen ? 'Свернуть' : 'Раскрыть детали'}">${ICONS['chevron']}</button>
                </li>`;
        }

        function renderAmount(ev) {
            const a = ev.amount;
            if (a == null || a === '' || isNaN(Number(a))) return '';
            const n = Number(a);
            const sign = n > 0 ? '+' : (n < 0 ? '−' : '');
            const cls  = n > 0 ? 'sa-amt--pos' : (n < 0 ? 'sa-amt--neg' : '');
            const fmt = Math.abs(Math.round(n)).toLocaleString('ru-RU');
            return `<span class="sa-amt ${cls}">${sign}${fmt} сум</span>`;
        }

        function renderDetails(ev, globalIdx) {
            const raw = ev.raw || {};
            // Безопасный JSON-pretty (truncate at 5KB)
            let json = '';
            try { json = JSON.stringify(raw, null, 2); }
            catch (e) { json = String(raw); }
            if (json.length > 5000) json = json.slice(0, 5000) + '\n…(обрезано)';

            // Pull-out — самые полезные поля как key/value
            const fields = pickFields(raw, ev);
            const fieldsHtml = fields.length ? `
                <dl class="sa-kv">
                    ${fields.map(([k, v]) => `<div class="sa-kv__row"><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}
                </dl>` : '';

            return `
                <div class="sa-event__details" data-no-expand>
                    ${fieldsHtml}
                    <details class="sa-raw">
                        <summary>Сырые данные</summary>
                        <pre class="sa-raw__pre">${esc(json)}</pre>
                    </details>
                    <div class="sa-event__actions">
                        <button type="button" class="sa-btn sa-btn--ghost" data-sa-copy="${globalIdx}" title="Скопировать JSON">
                            ${ICONS['copy']}<span>Копировать JSON</span>
                        </button>
                    </div>
                </div>`;
        }

        function pickFields(raw, ev) {
            const out = [];
            const map = [
                ['ratePlanOrderId','Order ID'],
                ['changeUser','Оператор'],
                ['naviUser','Оператор'],
                ['paymentDate','Дата платежа'],
                ['amount','Сумма'],
                ['status','Статус'],
                ['startDate','Начало'],
                ['endDate','Окончание'],
                ['changeDate','Дата изменения'],
                ['comment','Комментарий'],
                ['note','Примечание'],
                ['source','Источник'],
                ['action','Действие'],
                ['event','Событие'],
                ['conversionType','Причина'],
            ];
            const seen = new Set();
            for (const [k, label] of map) {
                let v = raw && raw[k];
                if (v && typeof v === 'object') {
                    v = v.name || v.def || v.value || '';
                }
                if (v != null && v !== '' && !seen.has(label)) {
                    out.push([label, v]);
                    seen.add(label);
                }
            }
            return out.slice(0, 6);
        }

        // initial empty state
        if (state.msisdn) load(false);
        else renderEmpty('Введите MSISDN');

        return { load, reload, destroy, setMsisdn, _state: state };
    }

    /* ---------- Public API ---------- */
    global.SbmsActivity = {
        mount: mount,
        TYPES: TYPES,
        classifyStatus: classifyStatus,
    };
})(typeof window !== 'undefined' ? window : globalThis);
