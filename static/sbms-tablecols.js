/**
 * SbmsTableCols — reusable column manager for HTML tables.
 *
 * Возможности:
 *  • visibility (показ/скрытие) колонок с минимумом 1
 *  • reorder через native HTML5 drag & drop (на TH)
 *  • resize через handle на правом крае TH
 *  • presets: standard / compact / full + custom
 *  • persistence в localStorage (visible + order + widths + selected preset)
 *  • cross-tab sync через storage event
 *  • migration с legacy ключа (массив только-видимости → полная схема)
 *
 * Контракт:
 *   const cols = SbmsTableCols.create({
 *     tableId: 'callsTbl',                  // <table id>
 *     storageKey: 'sbms-calls-cols-v2',     // localStorage ключ
 *     legacyStorageKey: 'sbms-calls-cols-v1', // (опц.) миграция со старого ключа
 *     columns: [{ key, label, group, default, locked, minWidth, defaultWidth }],
 *     groups: [{ id: 'main', label: 'Основное' }],
 *     presets: { standard: [keys], compact: [keys], full: [keys] }, // если нет full — берётся все
 *     defaultPreset: 'standard',
 *     pickerEl: '#callsColsMenu',           // селектор для popover
 *     countEl:  '#callsColsCount',          // селектор для бейджа N/M
 *     onChange: () => renderTable(),        // вызывается при изменении состава/порядка
 *     allowResize: true,
 *     allowReorder: true,
 *   });
 *
 * Возвращает { getActive, getVisible, getOrder, getColumnByKey, setVisible, applyPreset, reset, bindHead, renderPicker, updateCount, _state }.
 *
 * После каждой перерисовки <thead> вызвать `cols.bindHead()` — он применит сохранённые ширины,
 * привесит handle для resize и draggable + dnd-обработчики на TH с data-col-key.
 */
(function (global) {
    'use strict';

    const VERSION = 1;

    function loadState(key) {
        try {
            const raw = localStorage.getItem(key);
            if (!raw) return null;
            const obj = JSON.parse(raw);
            if (!obj || typeof obj !== 'object') return null;
            return obj;
        } catch { return null; }
    }
    function saveState(key, state) {
        try { localStorage.setItem(key, JSON.stringify(state)); } catch {}
    }
    function migrateLegacy(legacyKey) {
        if (!legacyKey) return null;
        try {
            const raw = localStorage.getItem(legacyKey);
            if (!raw) return null;
            const arr = JSON.parse(raw);
            if (!Array.isArray(arr) || !arr.length) return null;
            // legacy схема — массив видимых ключей
            return { v: VERSION, visible: arr.slice(), order: null, widths: {}, preset: null };
        } catch { return null; }
    }
    function escapeHtml(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[c]));
    }
    function toast(level, msg) {
        if (global.SbmsToast && typeof global.SbmsToast[level] === 'function') {
            global.SbmsToast[level](msg);
        }
    }

    function create(opts) {
        const {
            tableId,
            storageKey,
            legacyStorageKey = null,
            columns = [],
            groups = [],
            presets = {},
            defaultPreset = 'standard',
            pickerEl = null,
            countEl = null,
            onChange = () => {},
            allowResize = true,
            allowReorder = true,
        } = opts || {};

        if (!storageKey) throw new Error('SbmsTableCols: storageKey required');
        if (!Array.isArray(columns) || !columns.length) throw new Error('SbmsTableCols: columns required');

        const allKeys = columns.map(c => c.key);
        const colMap = new Map(columns.map(c => [c.key, c]));

        // Default preset = все default:true (либо presets.standard если задано)
        function builtinDefault() {
            return columns.filter(c => c.default).map(c => c.key);
        }
        function presetKeys(name) {
            if (presets[name]) return presets[name].slice();
            if (name === 'standard') return builtinDefault();
            if (name === 'full')    return allKeys.slice();
            if (name === 'compact') return builtinDefault().slice(0, Math.max(3, Math.ceil(builtinDefault().length / 2)));
            return null;
        }
        function defaultOrder() { return allKeys.slice(); }

        // Init state
        let state = loadState(storageKey) || migrateLegacy(legacyStorageKey);
        if (!state) {
            state = {
                v: VERSION,
                visible: presetKeys(defaultPreset) || builtinDefault(),
                order: defaultOrder(),
                widths: {},
                preset: defaultPreset,
            };
            saveState(storageKey, state);
        } else {
            // Validate
            state.v = VERSION;
            if (!Array.isArray(state.visible) || !state.visible.length) {
                state.visible = presetKeys(defaultPreset) || builtinDefault();
            }
            state.visible = state.visible.filter(k => colMap.has(k));
            if (!state.visible.length) state.visible = builtinDefault();

            if (!Array.isArray(state.order) || !state.order.length) {
                state.order = defaultOrder();
            } else {
                // фильтруем неизвестные, дописываем новые в конец
                const seen = new Set();
                state.order = state.order.filter(k => colMap.has(k) && !seen.has(k) && (seen.add(k), true));
                allKeys.forEach(k => { if (!state.order.includes(k)) state.order.push(k); });
            }
            if (!state.widths || typeof state.widths !== 'object') state.widths = {};
            // очистить ширины для несуществующих ключей
            Object.keys(state.widths).forEach(k => { if (!colMap.has(k)) delete state.widths[k]; });
            saveState(storageKey, state);
        }

        // ===== Public getters =====

        function getVisible() { return state.visible.slice(); }
        function getOrder()   { return state.order.slice(); }
        function getWidth(k)  { return state.widths[k]; }
        function getColumnByKey(k) { return colMap.get(k); }

        function getActive() {
            const vis = new Set(state.visible);
            return state.order.filter(k => vis.has(k)).map(k => colMap.get(k)).filter(Boolean);
        }

        // ===== Mutators =====

        function persist() { saveState(storageKey, state); }

        function setVisible(keys, opts2 = {}) {
            const valid = (keys || []).filter(k => colMap.has(k));
            if (!valid.length) {
                toast('warn', 'Должна быть хотя бы одна колонка');
                return false;
            }
            state.visible = valid;
            if (!opts2.silentPreset) state.preset = matchPreset(valid);
            persist();
            updateCount();
            renderPicker();
            onChange();
            return true;
        }

        function setOrder(orderKeys, opts2 = {}) {
            const seen = new Set();
            const valid = (orderKeys || []).filter(k => colMap.has(k) && !seen.has(k) && (seen.add(k), true));
            allKeys.forEach(k => { if (!valid.includes(k)) valid.push(k); });
            state.order = valid;
            if (!opts2.silentPreset) state.preset = null;
            persist();
            renderPicker();
            onChange();
        }

        function setWidth(key, px) {
            if (!colMap.has(key)) return;
            const col = colMap.get(key);
            const min = (col.minWidth || 40);
            const max = (col.maxWidth || 1200);
            px = Math.round(Math.max(min, Math.min(max, px)));
            state.widths[key] = px;
            persist();
            applyWidthInline(key, px);
        }
        function clearWidth(key) {
            delete state.widths[key];
            persist();
            applyWidthInline(key, null);
        }

        function applyPreset(name) {
            const keys = presetKeys(name);
            if (!keys || !keys.length) return;
            state.visible = keys;
            state.preset = name;
            // ширины не сбрасываем — пользователь может настроил их вручную
            persist();
            updateCount();
            renderPicker();
            onChange();
        }

        function reset() {
            state = {
                v: VERSION,
                visible: presetKeys(defaultPreset) || builtinDefault(),
                order: defaultOrder(),
                widths: {},
                preset: defaultPreset,
            };
            persist();
            updateCount();
            renderPicker();
            onChange();
            // также сбросить инлайн ширины с TH/COL
            const tbl = document.getElementById(tableId);
            if (tbl) {
                tbl.querySelectorAll('th[data-col-key]').forEach(th => {
                    th.style.width = ''; th.style.minWidth = ''; th.style.maxWidth = '';
                });
                tbl.querySelectorAll('colgroup col[data-col-key]').forEach(co => {
                    co.style.width = '';
                });
            }
        }

        // ===== Helpers =====

        function matchPreset(visKeys) {
            const set = new Set(visKeys);
            for (const name of ['standard', 'compact', 'full']) {
                const pk = presetKeys(name);
                if (!pk) continue;
                if (pk.length === set.size && pk.every(k => set.has(k))) return name;
            }
            return null;
        }

        function applyWidthInline(key, px) {
            const tbl = document.getElementById(tableId);
            if (!tbl) return;
            const th = tbl.querySelector(`thead th[data-col-key="${CSS.escape(key)}"]`);
            if (th) {
                if (px == null) { th.style.width = ''; th.style.minWidth = ''; th.style.maxWidth = ''; }
                else            { th.style.width = px + 'px'; th.style.minWidth = px + 'px'; th.style.maxWidth = px + 'px'; }
            }
            const co = tbl.querySelector(`colgroup col[data-col-key="${CSS.escape(key)}"]`);
            if (co) {
                co.style.width = (px == null) ? '' : (px + 'px');
            }
        }

        // ===== Picker (popover) =====

        function updateCount() {
            const el = (typeof countEl === 'string') ? document.querySelector(countEl) : countEl;
            if (el) el.textContent = `${state.visible.length}/${columns.length}`;
        }

        function renderPicker() {
            const menu = (typeof pickerEl === 'string') ? document.querySelector(pickerEl) : pickerEl;
            if (!menu) return;

            const visSet = new Set(state.visible);
            // Топ-actions: presets + reset
            const presetBtnsHtml = [];
            ['standard', 'compact', 'full'].forEach(name => {
                if (!presetKeys(name)) return;
                const labels = { standard: '⚲ Стандарт', compact: '▤ Компакт', full: '▦ Полный' };
                const isCur = state.preset === name;
                presetBtnsHtml.push(
                    `<button type="button" data-act="preset" data-preset="${name}" ${isCur ? 'aria-current="true" style="color:var(--brand);border-color:var(--brand);"' : ''}>${labels[name]}</button>`
                );
            });
            presetBtnsHtml.push(`<button type="button" data-act="reset" title="Сбросить настройки таблицы (видимость, порядок, ширина)">⟲ Сброс</button>`);

            // Группы
            const groupOrder = (groups && groups.length) ? groups : [{ id: '_default', label: 'Колонки' }];
            // распределяем колонки по группам в порядке state.order, но рисуем шапки в порядке groups
            const byGroup = new Map();
            state.order.forEach(k => {
                const c = colMap.get(k); if (!c) return;
                const g = c.group || '_default';
                if (!byGroup.has(g)) byGroup.set(g, []);
                byGroup.get(g).push(c);
            });

            const groupsHtml = groupOrder.map(g => {
                const items = byGroup.get(g.id) || [];
                if (!items.length) return '';
                const rows = items.map(c => {
                    const checked = visSet.has(c.key) ? 'checked' : '';
                    const disabled = c.locked ? 'disabled' : '';
                    const lockedCls = c.locked ? ' cols-item--locked' : '';
                    const hint = c.default ? '<span class="cols-item__hint">по умолч.</span>' : '';
                    return `<label class="cols-item${lockedCls}" data-col-key="${escapeHtml(c.key)}">
                        <input type="checkbox" ${checked} ${disabled} data-col-key="${escapeHtml(c.key)}">
                        <span class="cols-item__name">${escapeHtml(c.label)}</span>
                        ${hint}
                    </label>`;
                }).join('');
                return `<div class="cols-group">
                    <div class="cols-group__title">${escapeHtml(g.label)}</div>
                    ${rows}
                </div>`;
            }).join('');

            menu.innerHTML = `
                <div class="cols-menu__actions">
                    ${presetBtnsHtml.join('')}
                </div>
                <div class="cols-menu__hint">Перетаскивайте колонки в шапке таблицы, чтобы изменить порядок. Тяните за правую границу — измените ширину.</div>
                ${groupsHtml}
            `;

            // Bind checkboxes
            menu.querySelectorAll('input[type="checkbox"][data-col-key]').forEach(chk => {
                chk.addEventListener('change', () => {
                    const k = chk.dataset.colKey;
                    const cur = new Set(state.visible);
                    if (chk.checked) cur.add(k); else cur.delete(k);
                    if (cur.size === 0) {
                        chk.checked = true;
                        toast('warn', 'Должна быть хотя бы одна колонка');
                        return;
                    }
                    // сохраняем в порядке state.order (стабильность)
                    const next = state.order.filter(x => cur.has(x));
                    setVisible(next);
                });
            });
            // Action buttons
            menu.querySelectorAll('.cols-menu__actions button[data-act]').forEach(btn => {
                btn.addEventListener('click', () => {
                    const act = btn.dataset.act;
                    if (act === 'preset') {
                        const p = btn.dataset.preset;
                        applyPreset(p);
                        toast('info', `Применён пресет: ${p === 'standard' ? 'стандартный' : p === 'compact' ? 'компактный' : 'полный'}`);
                    } else if (act === 'reset') {
                        reset();
                        toast('success', 'Настройки таблицы сброшены');
                    }
                });
            });
        }

        // ===== Resize handles =====

        function _attachResizeHandle(th) {
            if (!allowResize) return;
            const key = th.dataset.colKey;
            if (!key) return;
            const col = colMap.get(key);
            if (col && col.locked) return;
            if (th.querySelector(':scope > .col-resize')) return;

            const handle = document.createElement('span');
            handle.className = 'col-resize';
            handle.title = 'Потяните, чтобы изменить ширину · двойной клик — авторазмер';
            // не должен мешать onclick=сортировка и dragstart
            handle.draggable = false;
            handle.addEventListener('mousedown', (e) => {
                e.preventDefault();
                e.stopPropagation();
                _startResize(e, th, key);
            });
            handle.addEventListener('click', (e) => { e.stopPropagation(); });
            handle.addEventListener('dblclick', (e) => {
                e.stopPropagation();
                clearWidth(key);
                toast('info', 'Ширина сброшена');
            });
            th.appendChild(handle);
        }

        function _startResize(e, th, key) {
            const col = colMap.get(key) || {};
            const min = col.minWidth || 40;
            const max = col.maxWidth || 1200;
            const startX = e.clientX;
            const startW = th.getBoundingClientRect().width;
            const tbl = document.getElementById(tableId);
            const colEl = tbl ? tbl.querySelector(`colgroup col[data-col-key="${CSS.escape(key)}"]`) : null;

            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';
            th.classList.add('th--resizing');
            // блокируем сортировочный клик после dragend
            _suppressClickAfterResize = true;

            function move(ev) {
                const dx = ev.clientX - startX;
                let w = Math.max(min, Math.min(max, startW + dx));
                th.style.width = w + 'px';
                th.style.minWidth = w + 'px';
                th.style.maxWidth = w + 'px';
                if (colEl) colEl.style.width = w + 'px';
            }
            function up() {
                document.removeEventListener('mousemove', move);
                document.removeEventListener('mouseup', up);
                document.body.style.cursor = '';
                document.body.style.userSelect = '';
                th.classList.remove('th--resizing');
                const final = Math.round(th.getBoundingClientRect().width);
                state.widths[key] = final;
                state.preset = null;
                persist();
                // снимем suppress через тик
                setTimeout(() => { _suppressClickAfterResize = false; }, 0);
            }
            document.addEventListener('mousemove', move);
            document.addEventListener('mouseup', up);
        }
        // Глобальный suppress для перехвата клика после resize — TH имеет onclick=sort
        let _suppressClickAfterResize = false;

        // ===== Drag & drop reorder =====

        let _dragKey = null;

        function _attachReorder(th) {
            if (!allowReorder) return;
            const key = th.dataset.colKey;
            if (!key) return;
            const col = colMap.get(key);
            if (col && col.locked) return;
            if (th.dataset.tcReorder === '1') return;
            th.dataset.tcReorder = '1';
            th.draggable = true;

            th.addEventListener('dragstart', (e) => {
                _dragKey = key;
                th.classList.add('th--dragging');
                if (e.dataTransfer) {
                    e.dataTransfer.effectAllowed = 'move';
                    try { e.dataTransfer.setData('text/plain', key); } catch {}
                }
            });
            th.addEventListener('dragend', () => {
                th.classList.remove('th--dragging');
                _dragKey = null;
                _clearDropMarkers();
            });
            th.addEventListener('dragover', (e) => {
                if (!_dragKey || _dragKey === key) return;
                e.preventDefault();
                if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
                const rect = th.getBoundingClientRect();
                const before = (e.clientX - rect.left) < rect.width / 2;
                th.classList.toggle('th--drop-before', before);
                th.classList.toggle('th--drop-after', !before);
            });
            th.addEventListener('dragleave', () => {
                th.classList.remove('th--drop-before', 'th--drop-after');
            });
            th.addEventListener('drop', (e) => {
                e.preventDefault();
                if (!_dragKey || _dragKey === key) { _clearDropMarkers(); return; }
                const rect = th.getBoundingClientRect();
                const before = (e.clientX - rect.left) < rect.width / 2;
                const next = state.order.filter(k => k !== _dragKey);
                const idx = next.indexOf(key);
                next.splice(before ? idx : idx + 1, 0, _dragKey);
                _clearDropMarkers();
                setOrder(next);
            });
        }
        function _clearDropMarkers() {
            const tbl = document.getElementById(tableId);
            if (!tbl) return;
            tbl.querySelectorAll('.th--drop-before, .th--drop-after').forEach(el => {
                el.classList.remove('th--drop-before', 'th--drop-after');
            });
        }

        // ===== Public bindHead =====

        function bindHead() {
            const tbl = document.getElementById(tableId);
            if (!tbl) return;
            // Применить сохранённые ширины
            tbl.querySelectorAll('thead th[data-col-key]').forEach(th => {
                const key = th.dataset.colKey;
                const w = state.widths[key];
                if (w) {
                    th.style.width = w + 'px';
                    th.style.minWidth = w + 'px';
                    th.style.maxWidth = w + 'px';
                }
                _attachResizeHandle(th);
                _attachReorder(th);
            });
            // colgroup col widths
            tbl.querySelectorAll('colgroup col[data-col-key]').forEach(co => {
                const key = co.dataset.colKey;
                const w = state.widths[key];
                if (w) co.style.width = w + 'px';
            });
            // Перехват клика для подавления сортировки сразу после resize
            if (!tbl.dataset.tcClickGuard) {
                tbl.dataset.tcClickGuard = '1';
                tbl.addEventListener('click', (e) => {
                    if (_suppressClickAfterResize) {
                        e.stopPropagation();
                        e.preventDefault();
                    }
                }, true);
            }
        }

        // ===== Cross-tab sync =====

        global.addEventListener('storage', (ev) => {
            if (ev.key !== storageKey || !ev.newValue) return;
            try {
                const newState = JSON.parse(ev.newValue);
                if (!newState || typeof newState !== 'object') return;
                state.visible = (newState.visible || []).filter(k => colMap.has(k));
                state.order   = (newState.order   || defaultOrder()).filter(k => colMap.has(k));
                allKeys.forEach(k => { if (!state.order.includes(k)) state.order.push(k); });
                state.widths  = (newState.widths && typeof newState.widths === 'object') ? newState.widths : {};
                state.preset  = newState.preset || null;
                updateCount();
                renderPicker();
                onChange();
            } catch {}
        });

        // ===== Outside-click closes <details> if pickerEl is inside one =====

        const dd = (typeof pickerEl === 'string') ? document.querySelector(pickerEl) : pickerEl;
        const ddetails = dd ? dd.closest('details') : null;
        if (ddetails) {
            document.addEventListener('click', (ev) => {
                if (ddetails.open && !ddetails.contains(ev.target)) ddetails.open = false;
            });
        }

        // ===== Initial =====
        renderPicker();
        updateCount();

        return {
            getActive,
            getVisible,
            getOrder,
            getWidth,
            getColumnByKey,
            setVisible,
            setOrder,
            setWidth,
            clearWidth,
            applyPreset,
            reset,
            bindHead,
            renderPicker,
            updateCount,
            _state: () => JSON.parse(JSON.stringify(state)),
        };
    }

    global.SbmsTableCols = { create };
})(window);
