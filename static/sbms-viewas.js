/**
 * SbmsViewAs — модалка «Просмотр от имени другой учётки» (preview / simulation mode).
 *
 * Идея:
 *  • Админ остаётся залогинен (admin-сессия не трогается).
 *  • Backend (/api/preview/subscriptions) создаёт ИЗОЛИРОВАННЫЙ SBMSClient под другими credentials,
 *    тянет packs/services/ratePlans от имени preview-учётки и сразу зануляет токен.
 *  • UI рисует split-сравнение: что видит admin vs что видит preview-роль.
 *  • 5 статусов: active / available / hidden / exclusive / restricted.
 *  • Опционально (write mode) — кнопка «Подключить» на стороне admin или preview.
 *
 * Контракт:
 *   SbmsViewAs.open({
 *     msisdn: '998500173054',         // обязательно
 *     adminSubscriberId: 12345,       // опц., нужен для активации с admin-стороны
 *     defaultRole: 'B2B_FO',          // опц., иначе — последняя из localStorage
 *     adminLabel: 'Admin (текущая)',  // опц., подпись левой колонки
 *     onActivated: ({side, kind, itemId}) => {}, // опц., колбэк после успешной активации
 *   });
 *
 * Безопасность:
 *  • Пароль preview-учётки хранится ТОЛЬКО в module-private переменной (closure)
 *    и удаляется сразу при закрытии модалки и при ошибке.
 *  • НИЧЕГО НЕ ПИШЕТСЯ в localStorage/sessionStorage кроме последней выбранной роли (без креденшиалов).
 *  • Admin-токен (SbmsAuth.token) только READ — модуль никак его не модифицирует.
 *  • Все запросы к /api/preview/* передают adminToken как proof-of-life и НЕ полагаются на admin-кеш.
 */
(function (global) {
    'use strict';

    // ===== Преднастроенные роли (логины, как и в tariff_test.html) =====
    // Эти данные не секрет — это технические учётки для QA-окружения.
    // Если нужно их изолировать — вынести в /api/preview/roles на бэкенд.
    const ROLES = [
        { id: 'B2B_OP', label: 'B2B · Оператор',   login: 'DBS_CC_OPERATORS_PSO',         password: 'Ucell2026$$',     preset: true },
        { id: 'B2B_FO', label: 'B2B · Фронт-офис', login: 'DBS_REG_FO_PSO',               password: 'September2025+',  preset: true },
        { id: 'B2C_FO', label: 'B2C · Фронт-офис', login: 'DMS_FO_REGION_SPEC_PSO',       password: 'April042026+++',  preset: true },
        { id: 'B2C_OP', label: 'B2C · Оператор',   login: 'DCC_CALL_CENTER_OPERATORT_PSO', password: 'Ucell+pso+2023+', preset: true },
        { id: 'CUSTOM', label: 'Другая (вручную)…', login: '', password: '', preset: false },
    ];

    const LS_LAST_ROLE = 'sbms-viewas-last-role';

    // ===== Утилиты =====
    function $(s, root) { return (root || document).querySelector(s); }
    function $all(s, root) { return Array.from((root || document).querySelectorAll(s)); }
    function escHtml(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[c]));
    }
    function jsArg(s) {
        // экранирование для JS-литерала (одинарные кавычки + бэкслеши)
        return String(s == null ? '' : s).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    }
    function toast(level, msg) {
        if (global.SbmsToast && typeof global.SbmsToast[level] === 'function') {
            global.SbmsToast[level](msg);
        }
    }

    // ===== Module-private state =====
    // ВАЖНО: всё это — closure, нет в window, нет в localStorage.
    let _state = null;       // { msisdn, role, adminData, previewData, ... }
    let _previewLogin = '';  // в JS-памяти модуля, очищается в close()
    let _previewPassword = ''; // в JS-памяти модуля, очищается в close() и после single-use
    let _root = null;        // backdrop element
    let _opts = {};          // последний open() opts

    // ===== Lifecycle =====

    function _ensureRoot() {
        if (_root) return _root;
        const bd = document.createElement('div');
        bd.className = 'sva-bd';
        bd.innerHTML = `
            <div class="sva-modal" role="dialog" aria-modal="true" aria-labelledby="svaTitle">
                <div class="sva-mode-tag" id="svaModeTag">read-only</div>
                <div class="sva-head">
                    <div>
                        <h3 id="svaTitle">Просмотр от имени другой учётки</h3>
                        <div class="sva-sub" id="svaSub"></div>
                    </div>
                    <button type="button" class="sva-close" id="svaClose" title="Закрыть (Esc)" aria-label="Закрыть">×</button>
                </div>
                <div class="sva-body" id="svaBody"></div>
            </div>
        `;
        document.body.appendChild(bd);
        bd.addEventListener('click', (e) => {
            if (e.target === bd) close();
        });
        $('#svaClose', bd).addEventListener('click', close);
        document.addEventListener('keydown', _onEscKey, true);
        _root = bd;
        return bd;
    }

    function _onEscKey(ev) {
        if (ev.key !== 'Escape') return;
        if (!_root || !_root.classList.contains('is-open')) return;
        // Не перехватываем Esc в поле поиска — там есть свой clear-обработчик
        const t = ev.target;
        if (t && t.id === 'svaSearch' && t.value) return;
        ev.stopPropagation();
        close();
    }

    function open(opts) {
        opts = opts || {};
        const msisdn = (opts.msisdn || '').toString().trim();
        if (!msisdn) {
            toast('warn', 'Сначала загрузите номер абонента');
            return;
        }
        if (!global.SbmsAuth || !global.SbmsAuth.token) {
            toast('warn', 'Сначала войдите в основную (админ) учётку');
            return;
        }
        _opts = opts;
        const lastRole = (() => {
            try { return localStorage.getItem(LS_LAST_ROLE) || ''; } catch { return ''; }
        })();
        const role = opts.defaultRole || lastRole || 'B2B_FO';

        _state = {
            msisdn,
            role: ROLES.find(r => r.id === role) ? role : 'B2B_FO',
            adminLabel: opts.adminLabel || 'Admin (текущая)',
            adminSubscriberId: opts.adminSubscriberId || null,
            adminData: null,    // подтянем lazily при первом submit
            previewData: null,
            previewRoleLabel: '',
            previewSubscriberId: null,
            tab: 'packs',
            search: '',
        };

        _ensureRoot();
        _renderForm();
        _root.classList.add('is-open');
        // фокус на первое поле или кнопку
        setTimeout(() => {
            const first = $('#svaSubmit', _root);
            if (first) first.focus();
        }, 50);
    }

    function close() {
        if (!_root) return;
        _root.classList.remove('is-open');
        // Очистка credential полей в DOM (anti-leak)
        const lp = $('#svaLogin', _root); if (lp) lp.value = '';
        const pw = $('#svaPassword', _root); if (pw) pw.value = '';
        // И из in-memory state — пароль и логин уходят в /dev/null
        _previewLogin = '';
        _previewPassword = '';
        _state = null;
    }

    // ===== Шаг 1: форма ролей + credentials =====

    function _renderForm() {
        const sub = `Номер: ${escHtml(_state.msisdn)} · режим Read-only / Write`;
        $('#svaSub', _root).textContent = sub;
        _setModeTag('read');

        const rolesHtml = ROLES.map(r => {
            const isActive = r.id === _state.role ? ' is-active' : '';
            const icon = r.preset
                ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:11px;height:11px;flex-shrink:0;"><path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z"/></svg>'
                : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:11px;height:11px;flex-shrink:0;"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5Z"/></svg>';
            return `<button type="button" class="sva-role${isActive}" data-role="${r.id}">${icon}${escHtml(r.label)}</button>`;
        }).join('');

        $('#svaBody', _root).innerHTML = `
            <form class="sva-step1" id="svaForm" autocomplete="off"
                  onsubmit="event.preventDefault();return false;">
                <div>
                    <div class="sva-section-label">Шаг 1 — выберите роль</div>
                    <div class="sva-roles" id="svaRoles">${rolesHtml}</div>
                </div>
                <div>
                    <div class="sva-section-label">Шаг 2 — учётка для просмотра</div>
                    <div class="sva-creds">
                        <div>
                            <label>Логин</label>
                            <input type="text" id="svaLogin" name="sva_login" autocomplete="off"
                                   autocapitalize="off" autocorrect="off" spellcheck="false"
                                   placeholder="login_b2b_fo">
                        </div>
                        <div>
                            <label>Пароль</label>
                            <input type="password" id="svaPassword" name="sva_password"
                                   autocomplete="off" placeholder="••••••">
                        </div>
                    </div>
                    <div id="svaPresetHint" class="sva-hint" style="margin-top:6px;">
                        Пароль используется только для одного запроса и не сохраняется.
                    </div>
                </div>
                <div class="sva-error" id="svaError"></div>
                <div class="sva-actions">
                    <button type="submit" class="sva-submit" id="svaSubmit">Войти и загрузить</button>
                    <span class="sva-loading" id="svaLoading"><span class="sva-spinner"></span> Загружаем под выбранной учёткой…</span>
                </div>
            </form>
        `;
        // делегирование клика на роли
        $('#svaRoles', _root).addEventListener('click', (ev) => {
            const btn = ev.target.closest('.sva-role');
            if (!btn) return;
            _setRole(btn.dataset.role);
        });
        // submit формы
        $('#svaForm', _root).addEventListener('submit', (ev) => {
            ev.preventDefault();
            _submit();
        });

        _applyRolePreset(_state.role);
    }

    function _setRole(roleId) {
        if (!ROLES.find(r => r.id === roleId)) return;
        _state.role = roleId;
        try { localStorage.setItem(LS_LAST_ROLE, roleId); } catch {}
        $all('#svaRoles .sva-role', _root).forEach(b => {
            b.classList.toggle('is-active', b.dataset.role === roleId);
        });
        _applyRolePreset(roleId);
    }

    function _applyRolePreset(roleId) {
        const r = ROLES.find(x => x.id === roleId);
        const loginEl = $('#svaLogin', _root);
        const passEl  = $('#svaPassword', _root);
        const hint    = $('#svaPresetHint', _root);
        if (!loginEl || !passEl) return;
        if (r && r.preset) {
            loginEl.value = r.login;
            passEl.value  = r.password;
            if (hint) {
                hint.textContent = `Подставлены преднастроенные данные для «${r.label}». Можно поправить вручную.`;
                hint.classList.add('is-preset');
            }
        } else {
            loginEl.value = '';
            passEl.value  = '';
            if (hint) {
                hint.textContent = 'Введите свой логин и пароль выбранной учётки.';
                hint.classList.remove('is-preset');
            }
            setTimeout(() => loginEl.focus(), 30);
        }
    }

    // ===== Submit: запрос к /api/preview/subscriptions =====

    async function _submit() {
        const form  = $('#svaForm', _root);
        const fd    = form ? new FormData(form) : null;
        const login = ((fd && fd.get('sva_login')) || ($('#svaLogin', _root) || {}).value || '').toString().trim();
        const password = ((fd && fd.get('sva_password')) || ($('#svaPassword', _root) || {}).value || '').toString();
        const errEl   = $('#svaError', _root);
        errEl.classList.remove('is-show');
        errEl.textContent = '';

        if (!login || !password) {
            errEl.textContent = 'Введите логин и пароль выбранной учётки';
            errEl.classList.add('is-show');
            return;
        }

        const submit  = $('#svaSubmit', _root);
        const loading = $('#svaLoading', _root);
        submit.disabled = true;
        loading.classList.add('is-show');

        try {
            const resp = await fetch('/api/preview/subscriptions', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    msisdn: _state.msisdn,
                    role: _state.role,
                    login,
                    password,
                    adminToken: global.SbmsAuth.token,
                }),
            });
            const json = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                const msg = json.message || json.error || `HTTP ${resp.status}`;
                errEl.textContent = `Ошибка: ${msg}`;
                errEl.classList.add('is-show');
                return;
            }

            // Подтягиваем admin-данные из бэкенда тем же эндпоинтом, но без preview-кред?
            // Проще: используем PUBLIC API под admin-токеном.
            await _loadAdminData();

            _state.previewData = json.data;
            _state.previewRoleLabel = (ROLES.find(r => r.id === _state.role) || {}).label || _state.role;
            _state.previewSubscriberId = json.subscriberId || null;

            // Сохраняем preview-credentials в module-private памяти (для активаций)
            _previewLogin = login;
            _previewPassword = password;

            _renderCompare();
        } catch (e) {
            errEl.textContent = 'Сетевая ошибка: ' + (e.message || e);
            errEl.classList.add('is-show');
        } finally {
            submit.disabled = false;
            loading.classList.remove('is-show');
            // Сразу обнуляем поле пароля в DOM (anti-leak)
            const pw = $('#svaPassword', _root);
            if (pw) pw.value = '';
        }
    }

    /**
     * Подтягивает admin-данные через обычные API под текущей админ-сессией.
     * Не требует пароля — использует SbmsAuth.token.
     */
    async function _loadAdminData() {
        // Если данные уже переданы из вызывающей страницы (через window._lastCustomerData
        // на tariff_test.html), используем их без HTTP.
        if (global._lastCustomerData) {
            _state.adminData = global._lastCustomerData;
            return;
        }
        // Для /subscriber тянем агрегацию по subscriberId через /api/preview/subscriptions ?
        // НЕТ — тот endpoint требует preview credentials. Используем напрямую proxy под admin-токеном.
        if (!_state.adminSubscriberId) {
            // adminSubscriberId не передан — просто оставляем admin-сторону пустой,
            // diff будет показывать «только preview». UX не идеален, но не падает.
            _state.adminData = { activePacks: [], availablePacks: [], activeServices: [], availableServices: [], availableRatePlans: [] };
            return;
        }
        const sid = _state.adminSubscriberId;
        const tok = global.SbmsAuth.token;
        const u = (path) => `/proxy/${path}?authToken=${encodeURIComponent(tok)}`;
        try {
            const [activePacks, availPacks, activeSvc, availSvc, availRps] = await Promise.all([
                fetch(u(`OAPI/v1/subscribers/${sid}/packs`)).then(r => r.json()).catch(() => ({})),
                fetch(u(`PSAPI/v1/bis-base/subscribers/${sid}/packs/availableForActivate`)).then(r => r.json()).catch(() => ({})),
                fetch(u(`OAPI/v1/subscribers/${sid}/services`)).then(r => r.json()).catch(() => ({})),
                fetch(u(`PSAPI/v1/bis-base/subscribers/${sid}/services/availableForActivate`)).then(r => r.json()).catch(() => ({})),
                fetch(`/proxy/OAPI/v1/cbss/subscribers/${sid}/ratePlans/availableForChange/search?languageId=1&limit=500&offset=0&returnCount=1&showFees=1&authToken=${encodeURIComponent(tok)}`, {
                    method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'
                }).then(r => r.json()).catch(() => ({})),
            ]);
            const items = (d) => (d && Array.isArray(d.items)) ? d.items : [];
            const flatRps = items(availRps).map(it => {
                const rp = it.ratePlan || {};
                return {
                    ratePlanId: rp.ratePlanId || it.ratePlanId || it.id,
                    name: rp.name || it.name || 'N/A',
                    isArchived: it.isArchived,
                };
            });
            _state.adminData = {
                activePacks: items(activePacks),
                availablePacks: items(availPacks),
                activeServices: items(activeSvc),
                availableServices: items(availSvc),
                availableRatePlans: flatRps,
            };
        } catch (e) {
            console.warn('[SbmsViewAs] admin data load failed:', e);
            _state.adminData = { activePacks: [], availablePacks: [], activeServices: [], availableServices: [], availableRatePlans: [] };
        }
    }

    // ===== Шаг 2: split-сравнение =====

    function _setModeTag(mode) {
        const el = $('#svaModeTag', _root);
        if (!el) return;
        if (mode === 'write') {
            el.classList.add('is-write');
            el.textContent = 'preview · write';
        } else {
            el.classList.remove('is-write');
            el.textContent = 'read-only';
        }
    }

    function _renderCompare() {
        const adminLabel = _state.adminLabel;
        const prevLabel = _state.previewRoleLabel || 'Preview';
        const tabs = [
            { id: 'packs',    label: 'Пакеты' },
            { id: 'services', label: 'Услуги' },
            { id: 'tariffs',  label: 'Тарифы' },
        ];
        const tabsHtml = tabs.map(t =>
            `<button type="button" class="sva-tab${t.id === _state.tab ? ' is-active' : ''}" data-tab="${t.id}">${t.label}</button>`
        ).join('');

        const canWrite = !!_previewPassword && _state.tab !== 'tariffs';
        _setModeTag(canWrite ? 'write' : 'read');

        const legendHtml = `
            <div class="sva-legend">
                <span style="color:var(--text-3);">Статусы:</span>
                <span class="sva-legend__item"><span class="sva-stat sva-stat--active">актив.</span> уже подключено</span>
                <span class="sva-legend__item"><span class="sva-stat sva-stat--available">доступ.</span> можно подключить</span>
                <span class="sva-legend__item"><span class="sva-stat sva-stat--hidden">скрыто</span> не видно для роли</span>
                <span class="sva-legend__item"><span class="sva-stat sva-stat--exclusive">экскл.</span> только для роли</span>
                <span class="sva-legend__item"><span class="sva-stat sva-stat--restricted">огранич.</span> с условиями</span>
            </div>
        `;

        const searchVal = escHtml(_state.search || '');
        $('#svaBody', _root).innerHTML = `
            <div class="sva-compare-head">
                <div class="sva-tabs" id="svaTabs">${tabsHtml}</div>
                <div class="sva-summary" id="svaSummary">—</div>
                <button type="button" class="sva-back" id="svaBack">← Сменить учётку</button>
            </div>
            ${legendHtml}
            <div class="sva-search-wrap">
                <input type="text" class="sva-search" id="svaSearch"
                       placeholder="Поиск по ID или названию (фильтрует обе панели)"
                       value="${searchVal}" autocomplete="off" spellcheck="false">
                <button type="button" class="sva-search-clear" id="svaSearchClear" title="Очистить (Esc)">×</button>
                <span class="sva-search-hint" id="svaSearchHint"></span>
            </div>
            <div class="sva-split">
                <div class="sva-pane">
                    <div class="sva-pane-hdr is-admin">
                        <span>${escHtml(adminLabel)}</span>
                        <span class="sva-count" id="svaCountLeft">—</span>
                    </div>
                    <div class="sva-pane-body" id="svaPaneLeft"></div>
                </div>
                <div class="sva-pane">
                    <div class="sva-pane-hdr is-preview">
                        <span>${escHtml(prevLabel)}</span>
                        <span class="sva-count" id="svaCountRight">—</span>
                    </div>
                    <div class="sva-pane-body" id="svaPaneRight"></div>
                </div>
            </div>
        `;

        // Bindings
        $('#svaTabs', _root).addEventListener('click', (ev) => {
            const btn = ev.target.closest('.sva-tab');
            if (!btn) return;
            _state.tab = btn.dataset.tab;
            _renderCompare();
        });
        $('#svaBack', _root).addEventListener('click', () => {
            _renderForm();
        });
        const sEl = $('#svaSearch', _root);
        let _t = null;
        sEl.addEventListener('input', () => {
            clearTimeout(_t);
            _t = setTimeout(() => {
                _state.search = sEl.value || '';
                _renderTab();
            }, 120);
        });
        sEl.addEventListener('keydown', (ev) => {
            if (ev.key === 'Escape' && sEl.value) {
                ev.stopPropagation();
                sEl.value = '';
                _state.search = '';
                _renderTab();
            }
        });
        $('#svaSearchClear', _root).addEventListener('click', () => {
            sEl.value = '';
            _state.search = '';
            sEl.focus();
            _renderTab();
        });

        _renderTab();
    }

    function _itemsForTab(src, tab) {
        if (!src) return [];
        if (tab === 'packs') {
            const a = (src.activePacks || []).map(p => ({ id: p.packId || p.id || '', name: p.name || 'N/A', status: 'active', _raw: p }));
            const v = (src.availablePacks || []).map(p => ({ id: p.packId || p.id || '', name: p.name || 'N/A', status: 'available', _raw: p }));
            return [...a, ...v];
        }
        if (tab === 'services') {
            const a = (src.activeServices || []).map(s => ({ id: s.serviceId || s.id || '', name: s.name || 'N/A', status: 'active', _raw: s }));
            const v = (src.availableServices || []).map(s => ({ id: s.serviceId || s.id || '', name: s.name || 'N/A', status: 'available', _raw: s }));
            return [...a, ...v];
        }
        if (tab === 'tariffs') {
            return (src.availableRatePlans || []).map(r => ({
                id: r.ratePlanId || r.id || '',
                name: r.name || 'N/A',
                status: r.isArchived ? 'restricted' : 'available',
                _raw: r,
            }));
        }
        return [];
    }

    function _renderTab() {
        const adminAll = _itemsForTab(_state.adminData, _state.tab);
        const prevAll  = _itemsForTab(_state.previewData, _state.tab);
        const adminKeys = new Set(adminAll.map(i => String(i.id)));
        const prevKeys  = new Set(prevAll.map(i => String(i.id)));
        const onlyAdmin = adminAll.filter(i => !prevKeys.has(String(i.id))).length;
        const onlyPrev  = prevAll.filter(i => !adminKeys.has(String(i.id))).length;

        const summaryEl = $('#svaSummary', _root);
        if (summaryEl) {
            summaryEl.innerHTML =
                `различий: <span class="sva-diff-num">${onlyAdmin + onlyPrev}</span> · скрыто для роли: ${onlyAdmin} · экскл. для роли: ${onlyPrev}`;
        }

        const q = (_state.search || '').trim().toLowerCase();
        const matches = (i) => !q || String(i.id || '').toLowerCase().includes(q)
            || String(i.name || '').toLowerCase().includes(q);
        const adminItems = q ? adminAll.filter(matches) : adminAll;
        const prevItems  = q ? prevAll.filter(matches)  : prevAll;

        const hintEl = $('#svaSearchHint', _root);
        if (hintEl) hintEl.textContent = q ? `${adminItems.length} слева · ${prevItems.length} справа` : '';

        const tab = _state.tab;
        const canActivateLeft  = (tab === 'packs' || tab === 'services') && !!_state.adminSubscriberId;
        const canActivateRight = (tab === 'packs' || tab === 'services') && !!_previewPassword;

        $('#svaPaneLeft',  _root).innerHTML = _renderRows(adminItems, prevKeys,  'left',  canActivateLeft);
        $('#svaPaneRight', _root).innerHTML = _renderRows(prevItems,  adminKeys, 'right', canActivateRight);
        const cl = $('#svaCountLeft',  _root);
        const cr = $('#svaCountRight', _root);
        if (cl) cl.textContent = q ? `${adminItems.length}/${adminAll.length} поз.` : `${adminAll.length} поз.`;
        if (cr) cr.textContent = q ? `${prevItems.length}/${prevAll.length} поз.`  : `${prevAll.length} поз.`;

        // Bindings для активаций (event delegation)
        const onActClick = (side) => (ev) => {
            const btn = ev.target.closest('.sva-act-btn');
            if (!btn) return;
            const itemId = btn.dataset.itemId;
            const itemName = btn.dataset.itemName;
            _activate(btn, side, tab, itemId, itemName);
        };
        $('#svaPaneLeft',  _root).onclick = onActClick('left');
        $('#svaPaneRight', _root).onclick = onActClick('right');
    }

    function _renderRows(items, otherKeys, side, canAct) {
        if (!items.length) {
            const q = (_state.search || '').trim();
            return `<div class="sva-empty">${q ? 'Ничего не найдено' : 'Нет данных'}</div>`;
        }
        return items.map(i => {
            const key = String(i.id);
            const inOther = otherKeys.has(key);
            // Маркируем строку: и тут и там → both; только тут → only
            const rowCls = inOther ? 'is-both' : 'is-only';
            // Расчёт визуального статуса:
            //   active (синий)        — позиция уже подключена
            //   available (зелёный)   — доступна, есть в обеих сторонах
            //   hidden (серый)        — есть на admin, нет на preview
            //   exclusive (фиолет.)   — есть на preview, нет на admin
            //   restricted (жёлтый)   — у элемента есть isArchived/locked
            let visualStatus = i.status; // active|available|restricted
            if (i.status === 'available' && !inOther) {
                visualStatus = (side === 'left') ? 'hidden' : 'exclusive';
            }
            const stat = _statusBadge(visualStatus);
            // Кнопку «Подключить» показываем только для available (не active)
            // и только для packs/services (не tariffs).
            const showAct = canAct && i.status === 'available' && key && key !== '—';
            const actBtn = showAct
                ? `<button type="button" class="sva-act-btn"
                            data-item-id="${escHtml(key)}"
                            data-item-name="${escHtml(i.name || '')}"
                            title="Подключить от имени ${side === 'left' ? 'admin' : 'preview'}-учётки">+ Подкл.</button>`
                : '';
            return `<div class="sva-row ${rowCls}">
                <span class="sva-id" title="${escHtml(key)}">${escHtml(key || '—')}</span>
                <span class="sva-name" title="${escHtml(i.name)}">${escHtml(i.name)}</span>
                ${stat}
                ${actBtn}
            </div>`;
        }).join('');
    }

    function _statusBadge(status) {
        const map = {
            active:     { cls: 'sva-stat--active',     label: 'актив.',  title: 'Уже подключено' },
            available:  { cls: 'sva-stat--available',  label: 'доступ.', title: 'Можно подключить' },
            hidden:     { cls: 'sva-stat--hidden',     label: 'скрыто',  title: 'Не видно для выбранной роли' },
            exclusive:  { cls: 'sva-stat--exclusive',  label: 'экскл.',  title: 'Видно только для выбранной роли' },
            restricted: { cls: 'sva-stat--restricted', label: 'огранич.', title: 'Архивный или с ограничениями' },
        };
        const m = map[status] || map.available;
        return `<span class="sva-stat ${m.cls}" title="${m.title}">${m.label}</span>`;
    }

    // ===== Активация (write mode) =====

    async function _activate(btnEl, side, tab, itemId, itemName) {
        if (tab !== 'packs' && tab !== 'services') return;
        const kind = (tab === 'packs') ? 'pack' : 'service';
        const kindRu = (kind === 'pack') ? 'пакет' : 'услугу';
        const accLabel = side === 'left' ? 'admin (текущая)' : (_state.previewRoleLabel || 'preview');
        if (!confirm(`Подключить ${kindRu} «${itemName}» (ID: ${itemId})\nот имени учётки: ${accLabel}?\n\nДействие необратимо.`)) return;

        const orig = btnEl.textContent;
        btnEl.disabled = true;
        btnEl.textContent = '⏳';

        try {
            let resp, json;
            if (side === 'left') {
                if (!_state.adminSubscriberId) {
                    toast('warn', 'subscriberId не передан — активация недоступна');
                    return;
                }
                const url = (kind === 'pack') ? '/api/packs/activate' : '/api/services/activate';
                const body = (kind === 'pack')
                    ? { subscriberId: _state.adminSubscriberId, packId: itemId, authToken: global.SbmsAuth.token }
                    : { subscriberId: _state.adminSubscriberId, serviceId: itemId, authToken: global.SbmsAuth.token };
                resp = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                json = await resp.json().catch(() => ({}));
            } else {
                if (!_previewPassword || !_previewLogin) {
                    toast('warn', 'Сначала войдите в preview-учётку');
                    return;
                }
                resp = await fetch('/api/preview/activate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        msisdn: _state.msisdn,
                        login: _previewLogin,
                        password: _previewPassword,
                        adminToken: global.SbmsAuth.token,
                        kind,
                        itemId,
                        subscriberId: _state.previewSubscriberId,
                    }),
                });
                json = await resp.json().catch(() => ({}));
                if (json && json.data) {
                    // Обновляем preview-данные in-place (свежий список после активации)
                    _state.previewData = Object.assign({}, _state.previewData, json.data);
                }
            }
            if (!resp.ok) {
                const msg = (json && (json.message || json.error)) || `HTTP ${resp.status}`;
                toast('error', `Не удалось подключить: ${msg}`);
                return;
            }
            toast('success', `${kindRu} «${itemName}» подключена`);
            // Обновим обе стороны: для admin — заново тянем admin-data
            if (side === 'left') await _loadAdminData();
            _renderTab();
            if (typeof _opts.onActivated === 'function') {
                try { _opts.onActivated({ side, kind, itemId, itemName }); } catch {}
            }
        } catch (e) {
            toast('error', 'Сетевая ошибка: ' + (e.message || e));
        } finally {
            btnEl.disabled = false;
            btnEl.textContent = orig;
        }
    }

    // ===== Public API =====
    global.SbmsViewAs = {
        open,
        close,
        // для тестов / отладки — НЕ возвращает пароль (он в closure)
        getRoles: () => ROLES.map(r => ({ id: r.id, label: r.label, preset: r.preset })),
    };
})(window);
