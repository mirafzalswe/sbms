/* SbmsTariffTest — pre-flight модалка + прогресс + редирект на /tariff-test/result.
 *
 * Поведение:
 *  - При нажатии «Тест» в строке тарифа открывается модалка с информацией
 *    «было / станет», опциональными блоками TME и B2C/B2B FO.
 *  - Логины ролей живут в localStorage, пароли — только в sessionStorage.
 *  - При запуске показывается оверлей прогресса; результат — POST /api/tariff/test-run,
 *    после ответа делается redirect на /tariff-test/result/<id>.
 */
(function (global) {
    'use strict';

    const LS = {
        useTme:    'sbms-test-use-tme',
        roleIds:   'sbms-test-role-ids',     // ['B2C_FO','B2B_FO']
    };

    function lsGet(k) { try { return localStorage.getItem(k) || ''; } catch (_) { return ''; } }
    function lsSet(k, v) { try { v ? localStorage.setItem(k, v) : localStorage.removeItem(k); } catch (_) {} }
    function lsGetJson(k, fallback) {
        try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : fallback; }
        catch (_) { return fallback; }
    }
    function lsSetJson(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) {} }

    // Список QA-ролей берём из SbmsViewAs (без паролей — пароли на сервере).
    // Возвращает [{id, label, preset}].
    function getRolesCatalog() {
        try {
            if (window.SbmsViewAs && typeof SbmsViewAs.getRoles === 'function') {
                return (SbmsViewAs.getRoles() || []).filter(r => r.preset);
            }
        } catch (_) {}
        // Фолбэк, если sbms-viewas.js не загружен — синхронизирован с server.py каталогом.
        return [
            { id: 'B2B_OP', label: 'B2B · Оператор',   preset: true },
            { id: 'B2B_FO', label: 'B2B · Фронт-офис', preset: true },
            { id: 'B2C_FO', label: 'B2C · Фронт-офис', preset: true },
            { id: 'B2C_OP', label: 'B2C · Оператор',   preset: true },
        ];
    }

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function fmtFee(v) {
        if (v == null || !Number.isFinite(Number(v))) return '—';
        return Math.round(Number(v)).toLocaleString('ru-RU') + ' сум';
    }

    function injectStyles() {
        if (document.getElementById('sbms-tt-styles')) return;
        const css = `
            .stt-overlay { position:fixed; inset:0; z-index:9990; display:none; align-items:center; justify-content:center; padding:16px; background:var(--bg-overlay,rgba(2,6,23,.72)); backdrop-filter:blur(4px); -webkit-backdrop-filter:blur(4px); }
            .stt-overlay.is-open { display:flex; animation:sttFade .14s ease-out; }
            @keyframes sttFade { from { opacity:0 } to { opacity:1 } }
            @keyframes sttPop  { from { opacity:0; transform:translateY(6px) scale(.98) } to { opacity:1; transform:translateY(0) scale(1) } }
            .stt-dialog { width:100%; max-width:540px; max-height:90vh; overflow:auto; background:var(--bg-surface,#131319); color:var(--text-1,#FAFAFA); border:1px solid var(--border,#27272F); border-radius:var(--radius-lg,12px); box-shadow:var(--shadow-xl,0 20px 40px rgba(0,0,0,.55)); animation:sttPop .18s ease-out; }
            .stt-head { padding:16px 20px 12px 20px; border-bottom:1px solid var(--border-subtle,rgba(63,63,70,.5)); }
            .stt-head__title { font-size:var(--text-md,15px); font-weight:600; line-height:1.2; display:flex; align-items:center; gap:10px; }
            .stt-head__sub { font-size:var(--text-xs,12px); color:var(--text-3,#71717A); margin-top:4px; }
            .stt-body { padding:14px 20px; }
            .stt-meta { background:var(--bg-canvas,#0A0A0F); border:1px solid var(--border,#27272F); border-radius:var(--radius-md,8px); padding:12px 14px; display:grid; grid-template-columns:90px 1fr; gap:6px 12px; align-items:baseline; font-size:13px; }
            .stt-meta__k { color:var(--text-3,#71717A); font-size:11px; text-transform:uppercase; letter-spacing:.04em; font-weight:600; }
            .stt-meta__v { font-family:var(--font-mono,ui-monospace,monospace); }
            .stt-warn { display:flex; gap:8px; padding:10px 12px; margin-top:12px; background:rgba(255,184,77,.10); border:1px solid rgba(255,184,77,.30); border-radius:var(--radius-md,8px); color:var(--warning-text,#ffb84d); font-size:12px; line-height:1.45; }
            .stt-section { margin-top:16px; padding-top:14px; border-top:1px dashed var(--border-subtle,rgba(63,63,70,.4)); }
            .stt-section__head { display:flex; align-items:center; gap:8px; margin-bottom:8px; }
            .stt-section__title { font-size:13px; font-weight:600; color:var(--text-1,#FAFAFA); }
            .stt-section__hint { font-size:11px; color:var(--text-3,#71717A); margin-left:auto; }
            .stt-toggle { display:inline-flex; align-items:center; gap:8px; cursor:pointer; user-select:none; font-size:13px; color:var(--text-2,#A1A1AA); }
            .stt-toggle input { width:14px; height:14px; accent-color:var(--brand,#B86CDA); }
            .stt-fields { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:10px; }
            .stt-field { display:flex; flex-direction:column; gap:4px; }
            .stt-field__label { font-size:11px; color:var(--text-3,#71717A); }
            .stt-input { box-sizing:border-box; height:32px; padding:0 10px; background:var(--bg-canvas,#0A0A0F); color:var(--text-1,#FAFAFA); border:1px solid var(--border,#27272F); border-radius:6px; font-size:13px; outline:none; transition:border-color .12s, box-shadow .12s; }
            .stt-input:focus { border-color:var(--brand,#B86CDA); box-shadow:var(--focus-ring,0 0 0 3px rgba(184,108,218,.32)); }
            .stt-input--full { grid-column:1 / -1; }
            .stt-roles-list { display:flex; flex-direction:column; gap:6px; }
            .stt-role { display:flex; align-items:center; gap:10px; padding:8px 10px; border:1px solid var(--border,#27272F); border-radius:6px; cursor:pointer; user-select:none; transition:border-color .12s, background .12s; background:var(--bg-canvas,#0A0A0F); }
            .stt-role:hover { border-color:var(--brand,#B86CDA); }
            .stt-role:has(.stt-role__cb:checked) { border-color:var(--brand,#B86CDA); background:rgba(184,108,218,.08); }
            .stt-role__cb { width:14px; height:14px; accent-color:var(--brand,#B86CDA); }
            .stt-role__label { font-size:13px; color:var(--text-1,#FAFAFA); flex:1; }
            .stt-role__id { font-size:10px; color:var(--text-3,#71717A); font-family:var(--font-mono,ui-monospace,monospace); padding:1px 6px; background:var(--bg-subtle,#1C1C24); border-radius:4px; }
            .stt-roles-btn { padding:4px 10px; font-size:11px; background:transparent; color:var(--text-3,#71717A); border:1px solid var(--border-subtle,rgba(63,63,70,.5)); border-radius:4px; cursor:pointer; }
            .stt-roles-btn:hover { color:var(--text-1,#FAFAFA); border-color:var(--brand,#B86CDA); }
            .stt-tme-status { font-size:11px; color:var(--text-3,#71717A); display:flex; align-items:center; gap:6px; }
            .stt-tme-status .dot { width:6px; height:6px; border-radius:50%; background:var(--success,#22c55e); }
            .stt-tme-status.is-off .dot { background:var(--warning,#ffb84d); }
            .stt-foot { padding:14px 20px 18px 20px; display:flex; gap:8px; justify-content:flex-end; border-top:1px solid var(--border-subtle,rgba(63,63,70,.5)); }
            .stt-err { min-height:16px; font-size:12px; color:var(--danger-text,#F87171); margin-top:8px; }

            /* Progress overlay */
            .stt-prog { width:100%; max-width:520px; padding:18px 20px; }
            .stt-prog__title { font-size:14px; font-weight:600; margin-bottom:4px; display:flex; align-items:center; gap:10px; }
            .stt-prog__sub { font-size:12px; color:var(--text-3,#71717A); margin-bottom:14px; }
            .stt-step { display:flex; align-items:center; gap:10px; padding:8px 10px; border-radius:6px; font-size:12px; transition:background .12s; }
            .stt-step + .stt-step { margin-top:2px; }
            .stt-step.is-active { background:rgba(184,108,218,.10); color:var(--text-1,#FAFAFA); }
            .stt-step.is-done { color:var(--text-2,#A1A1AA); }
            .stt-step.is-fail { color:var(--danger-text,#F87171); }
            .stt-step__icon { width:18px; height:18px; flex:0 0 18px; display:inline-flex; align-items:center; justify-content:center; font-size:11px; }
            .stt-step.is-pending .stt-step__icon::before { content:'•'; color:var(--text-3,#71717A); }
            .stt-step.is-active  .stt-step__icon::before { content:''; width:12px; height:12px; border-radius:50%; border:2px solid var(--brand,#B86CDA); border-top-color:transparent; animation:sttSpin .9s linear infinite; display:inline-block; }
            .stt-step.is-done    .stt-step__icon::before { content:'✓'; color:var(--success,#22c55e); }
            .stt-step.is-fail    .stt-step__icon::before { content:'✗'; color:var(--danger-text,#F87171); }
            @keyframes sttSpin { to { transform:rotate(360deg) } }
            .stt-prog__cancel { margin-top:12px; text-align:right; }
        `;
        const tag = document.createElement('style');
        tag.id = 'sbms-tt-styles';
        tag.textContent = css;
        document.head.appendChild(tag);
    }

    // ============================================================
    // Pre-flight модалка
    // ============================================================
    function openPreflight(opts) {
        injectStyles();
        const { ratePlanId, newName, recurringFee, current } = opts;
        const oldName = current.name || '—';
        const oldId   = current.ratePlanId || '—';

        const wrap = document.createElement('div');
        wrap.className = 'stt-overlay is-open';
        wrap.setAttribute('role', 'dialog');
        wrap.setAttribute('aria-modal', 'true');

        const useTmeDefault = lsGet(LS.useTme) !== '0';   // по умолчанию ВКЛ
        const savedRoleIds  = lsGetJson(LS.roleIds, ['B2C_FO', 'B2B_FO']);

        const tmeUser = (window.SbmsTme && SbmsTme.getUser && SbmsTme.getUser()) || '';
        const tmeStatusHtml = tmeUser
            ? `<span class="stt-tme-status"><span class="dot"></span>залогинен как <b>${esc(tmeUser)}</b></span>`
            : `<span class="stt-tme-status is-off"><span class="dot"></span>войти попросим перед запуском</span>`;

        const rolesCatalog = getRolesCatalog();
        const rolesChecklist = rolesCatalog.map(r => {
            const checked = savedRoleIds.indexOf(r.id) >= 0 ? 'checked' : '';
            return `
                <label class="stt-role" data-role-id="${esc(r.id)}">
                    <input type="checkbox" class="stt-role__cb" data-role-id="${esc(r.id)}" ${checked}>
                    <span class="stt-role__label">${esc(r.label)}</span>
                    <span class="stt-role__id">${esc(r.id)}</span>
                </label>
            `;
        }).join('');

        wrap.innerHTML = `
            <div class="stt-dialog">
                <div class="stt-head">
                    <div class="stt-head__title">Тестовая смена тарифа</div>
                    <div class="stt-head__sub">Реальная смена + автоматический сбор diff (объёмы · пакеты · услуги · COS · доступное из ролей)</div>
                </div>
                <div class="stt-body">
                    <div class="stt-meta">
                        <span class="stt-meta__k">Сейчас</span>
                        <span class="stt-meta__v" style="color:var(--danger-text,#F87171);">${esc(oldName)} · ID ${esc(oldId)}</span>
                        <span class="stt-meta__k">Станет</span>
                        <span class="stt-meta__v" style="color:var(--success-text,#3ddc97);">${esc(newName)} · ID ${esc(ratePlanId)}</span>
                        <span class="stt-meta__k">Новая АП</span>
                        <span class="stt-meta__v">${esc(fmtFee(recurringFee))}</span>
                    </div>
                    <div class="stt-warn">
                        ⚠ Это <b>реальная</b> смена тарифа. Абонент останется на новом тарифе. Произойдёт списание АП по правилам биллинга.
                    </div>

                    <div class="stt-section">
                        <div class="stt-section__head">
                            <label class="stt-toggle">
                                <input type="checkbox" id="sttUseTme" ${useTmeDefault ? 'checked' : ''}>
                                Снимок COS / PCRF (TME)
                            </label>
                            <span class="stt-section__hint" id="sttTmeHint">${tmeStatusHtml}</span>
                        </div>
                    </div>

                    <div class="stt-section">
                        <div class="stt-section__head">
                            <span class="stt-section__title">Доступные пакеты/тарифы из ролей</span>
                            <span class="stt-section__hint" id="sttRolesCounter"></span>
                        </div>
                        <div style="font-size:11px;color:var(--text-3,#71717A);margin:0 0 8px 0;">
                            Те же QA-учётки, что в «Посмотреть как». Пароли хранятся на сервере — браузер их не видит.
                        </div>
                        <div class="stt-roles-list">
                            ${rolesChecklist}
                            <div style="display:flex;gap:8px;margin-top:6px;">
                                <button type="button" class="stt-roles-btn" data-roles-act="all">Все</button>
                                <button type="button" class="stt-roles-btn" data-roles-act="none">Снять</button>
                            </div>
                        </div>
                    </div>

                    <div class="stt-err" id="sttErr"></div>
                </div>
                <div class="stt-foot">
                    <button type="button" class="sb-btn sb-btn--ghost"  id="sttCancel">Отмена</button>
                    <button type="button" class="sb-btn sb-btn--primary" id="sttRun">Запустить</button>
                </div>
            </div>
        `;
        document.body.appendChild(wrap);

        const close = () => { try { wrap.remove(); } catch (_) {} document.removeEventListener('keydown', onEsc); };
        const onEsc = (e) => { if (e.key === 'Escape') close(); };
        document.addEventListener('keydown', onEsc);
        wrap.addEventListener('click', (e) => { if (e.target === wrap) close(); });
        wrap.querySelector('#sttCancel').addEventListener('click', close);

        const allCbs = () => Array.from(wrap.querySelectorAll('.stt-role__cb'));
        const updateRolesCounter = () => {
            const n = allCbs().filter(c => c.checked).length;
            const cnt = wrap.querySelector('#sttRolesCounter');
            cnt.textContent = n ? (n + ' выбрано') : 'не выбрано';
            cnt.style.color = n ? 'var(--success-text,#3ddc97)' : 'var(--text-3,#71717A)';
        };
        allCbs().forEach(cb => cb.addEventListener('change', updateRolesCounter));
        updateRolesCounter();
        wrap.querySelectorAll('[data-roles-act]').forEach(btn => {
            btn.addEventListener('click', () => {
                const act = btn.getAttribute('data-roles-act');
                allCbs().forEach(cb => { cb.checked = (act === 'all'); });
                updateRolesCounter();
            });
        });

        wrap.querySelector('#sttRun').addEventListener('click', async () => {
            const useTme = wrap.querySelector('#sttUseTme').checked;
            lsSet(LS.useTme, useTme ? '1' : '0');

            const selectedIds = allCbs().filter(c => c.checked).map(c => c.getAttribute('data-role-id'));
            lsSetJson(LS.roleIds, selectedIds);

            // Если включили TME, но не залогинены — попросим войти прямо сейчас
            if (useTme && (!window.SbmsTme || !SbmsTme.getUser())) {
                if (window.SbmsTme && SbmsTme.openLoginModal) {
                    const ok = await SbmsTme.openLoginModal();
                    if (!ok) {
                        wrap.querySelector('#sttErr').textContent = 'Вход в TME отменён — снимок COS пропустим.';
                        wrap.querySelector('#sttUseTme').checked = false;
                    }
                }
            }

            close();
            runTest({
                ratePlanId, newName, recurringFee, current,
                useTme: wrap.querySelector('#sttUseTme').checked || false,
                roleIds: selectedIds,
            });
        });
    }

    // ============================================================
    // Прогресс-оверлей + запуск
    // ============================================================
    function runTest(payload) {
        injectStyles();
        const wrap = document.createElement('div');
        wrap.className = 'stt-overlay is-open';
        wrap.innerHTML = `
            <div class="stt-dialog stt-prog">
                <div class="stt-prog__title">Тестовая смена тарифа</div>
                <div class="stt-prog__sub">MSISDN ${esc(payload.current.msisdn || '')} · цель: <b>${esc(payload.newName)}</b></div>
                <div id="sttSteps">
                    <div class="stt-step is-active"  data-step="snapshot_before"><span class="stt-step__icon"></span><span>Снимок ДО · баланс, скидки, пакеты</span></div>
                    <div class="stt-step is-pending" data-step="pcrf_before"><span class="stt-step__icon"></span><span>Снимок COS/PCRF (TME)</span></div>
                    <div class="stt-step is-pending" data-step="change"><span class="stt-step__icon"></span><span>POST смены тарифа</span></div>
                    <div class="stt-step is-pending" data-step="poll"><span class="stt-step__icon"></span><span>Ожидание подтверждения order</span></div>
                    <div class="stt-step is-pending" data-step="snapshot_after"><span class="stt-step__icon"></span><span>Снимок ПОСЛЕ · сравнение объёмов</span></div>
                    <div class="stt-step is-pending" data-step="preview"><span class="stt-step__icon"></span><span>Preview из B2C FO / B2B FO</span></div>
                </div>
                <div class="stt-err" id="sttRunErr"></div>
                <div class="stt-prog__cancel"><button type="button" class="sb-btn sb-btn--ghost sb-btn--sm" id="sttRunClose" style="display:none;">Закрыть</button></div>
            </div>
        `;
        document.body.appendChild(wrap);

        const stepEl = (k) => wrap.querySelector('.stt-step[data-step="' + k + '"]');
        const setStep = (k, cls) => {
            const el = stepEl(k); if (!el) return;
            el.classList.remove('is-pending', 'is-active', 'is-done', 'is-fail');
            el.classList.add(cls);
        };
        const closeBtn = wrap.querySelector('#sttRunClose');
        closeBtn.addEventListener('click', () => { try { wrap.remove(); } catch (_) {} });

        // Имитация прогресса для UX (одни POST, видимый прогресс по таймеру).
        // Реальные этапы переключаются по прибытии ответа.
        let tick = 0;
        const sequence = ['snapshot_before', 'pcrf_before', 'change', 'poll', 'snapshot_after', 'preview'];
        let lastIdx = 0;
        const timer = setInterval(() => {
            tick += 1;
            // Поднимаем по одному шагу каждые ~6 сек (пока ответа нет)
            const want = Math.min(sequence.length - 1, Math.floor(tick / 6));
            if (want > lastIdx) {
                for (let i = lastIdx; i < want; i++) setStep(sequence[i], 'is-done');
                setStep(sequence[want], 'is-active');
                lastIdx = want;
            }
        }, 1000);

        const finish = (ok, err) => {
            clearInterval(timer);
            if (ok) {
                sequence.forEach(k => setStep(k, 'is-done'));
            } else {
                setStep(sequence[lastIdx], 'is-fail');
                wrap.querySelector('#sttRunErr').textContent = (err && err.message) || String(err || 'Ошибка');
                closeBtn.style.display = '';
            }
        };

        const body = {
            msisdn: payload.current.msisdn,
            newRatePlanId: payload.ratePlanId,
            newRatePlanName: payload.newName,
            useTme: !!payload.useTme,
            tmeUser: (window.SbmsTme && SbmsTme.getUser && SbmsTme.getUser()) || '',
            roleIds: Array.isArray(payload.roleIds) ? payload.roleIds : [],
        };

        SbmsAuth.apiPost('/api/tariff/test-run', body)
            .then(async (resp) => {
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || data.error) {
                    throw new Error(data.error || ('HTTP ' + resp.status));
                }
                finish(true);
                const url = data.redirectUrl || ('/tariff-test/result/' + encodeURIComponent(data.reportId));
                // Небольшая пауза, чтобы пользователь увидел зелёные галочки
                setTimeout(() => { window.location.href = url; }, 600);
            })
            .catch((err) => {
                console.error('[tariff-test]', err);
                finish(false, err);
            });
    }

    // ============================================================
    // Public entry point — вызывается из onclick в renderTariffs
    // ============================================================
    function startTariffTest(ratePlanId, newName, recurringFee) {
        // Текущее состояние абонента берём из window._data, который заполняет subscriber.html
        const c = (window._data && window._data.customer) || {};
        const msisdn = (document.getElementById('msisdn') || {}).value || '';
        if (!c.subscriberId) {
            if (window.SbmsToast) SbmsToast.error('Subscriber ID не найден — перезагрузите абонента');
            return;
        }
        if (Number(c.ratePlanId) === Number(ratePlanId)) {
            if (window.SbmsToast) SbmsToast.warn('Это и есть текущий тариф');
            return;
        }
        openPreflight({
            ratePlanId,
            newName,
            recurringFee,
            current: {
                msisdn,
                customerId: c.customerId,
                subscriberId: c.subscriberId,
                ratePlanId: c.ratePlanId,
                name: c.ratePlanName || c.name,
            },
        });
    }

    global.SbmsTariffTest = { start: startTariffTest };
    global.startTariffTest = startTariffTest; // для inline-onclick в строке тарифа
})(window);
