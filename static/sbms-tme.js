/* SbmsTme — клиент TME-сценариев с безопасным auth.
 *
 * Принципы:
 *  - JWT токен живёт ТОЛЬКО на backend (_tme_auth_cache).
 *  - Frontend хранит ТОЛЬКО username в sessionStorage (не localStorage, не пароль, не токен).
 *  - При 401/code=TME_AUTH_REQUIRED|TME_AUTH_EXPIRED показывается модалка входа,
 *    после успешного логина — автоматический ретрай исходного запроса.
 *  - Пароль передаётся один раз и сразу обнуляется (нигде не хранится).
 */
(function (global) {
    'use strict';

    const SS_USER_KEY = 'sbms-tme-user';
    const SS_EXP_KEY  = 'sbms-tme-exp';

    function getUser() {
        try { return sessionStorage.getItem(SS_USER_KEY) || ''; } catch (_) { return ''; }
    }
    function setUser(u) {
        try {
            if (u) sessionStorage.setItem(SS_USER_KEY, u);
            else   sessionStorage.removeItem(SS_USER_KEY);
        } catch (_) {}
    }
    function setExp(v) {
        try {
            if (v) sessionStorage.setItem(SS_EXP_KEY, String(v));
            else   sessionStorage.removeItem(SS_EXP_KEY);
        } catch (_) {}
    }
    function clearSession() { setUser(''); setExp(0); }

    // ------- модалка логина -------
    let _modalEl = null;
    let _pendingResolve = null;

    function injectStyles() {
        if (document.getElementById('sbms-tme-modal-styles')) return;
        const css = '\
            .sbms-tme-overlay {\
                position: fixed; inset: 0; z-index: 9998;\
                display: none; align-items: center; justify-content: center;\
                padding: 16px;\
                background: var(--bg-overlay, rgba(2, 6, 23, 0.72));\
                backdrop-filter: blur(4px);\
                -webkit-backdrop-filter: blur(4px);\
                animation: sbmsTmeFade .14s ease-out;\
            }\
            @keyframes sbmsTmeFade { from { opacity: 0 } to { opacity: 1 } }\
            @keyframes sbmsTmePop  { from { opacity: 0; transform: translateY(6px) scale(.98) } to { opacity: 1; transform: translateY(0) scale(1) } }\
            .sbms-tme-dialog {\
                width: 100%; max-width: 420px;\
                background: var(--bg-surface, #131319);\
                color: var(--text-1, #FAFAFA);\
                border: 1px solid var(--border, #27272F);\
                border-radius: var(--radius-lg, 12px);\
                box-shadow: var(--shadow-xl, 0 20px 40px rgba(0,0,0,.55));\
                font-family: var(--font-sans, Inter, system-ui, sans-serif);\
                animation: sbmsTmePop .18s ease-out;\
                overflow: hidden;\
            }\
            .sbms-tme-head {\
                display: flex; align-items: center; gap: 10px;\
                padding: 16px 20px 12px 20px;\
                border-bottom: 1px solid var(--border-subtle, rgba(63,63,70,.5));\
            }\
            .sbms-tme-head__icon {\
                width: 28px; height: 28px; flex: 0 0 28px;\
                border-radius: 8px;\
                display: inline-flex; align-items: center; justify-content: center;\
                background: var(--brand-soft, rgba(184,108,218,.12));\
                color: var(--brand, #B86CDA);\
                font-size: 14px; font-weight: 700;\
            }\
            .sbms-tme-head__title {\
                font-size: var(--text-md, 15px);\
                font-weight: var(--weight-semibold, 600);\
                line-height: 1.2;\
            }\
            .sbms-tme-head__sub {\
                font-size: var(--text-xs, 12px);\
                color: var(--text-3, #71717A);\
                margin-top: 2px;\
            }\
            .sbms-tme-body { padding: 14px 20px 4px 20px; }\
            .sbms-tme-field { margin-bottom: 12px; }\
            .sbms-tme-field__label {\
                display: block;\
                font-size: var(--text-xs, 12px);\
                color: var(--text-2, #A1A1AA);\
                margin-bottom: 6px;\
            }\
            .sbms-tme-input {\
                width: 100%; box-sizing: border-box;\
                height: var(--control-height, 36px);\
                padding: 0 12px;\
                background: var(--bg-canvas, #0A0A0F);\
                color: var(--text-1, #FAFAFA);\
                border: 1px solid var(--border, #27272F);\
                border-radius: var(--radius-md, 8px);\
                font: inherit; font-size: var(--text-sm, 13px);\
                transition: border-color .12s, box-shadow .12s;\
                outline: none;\
            }\
            .sbms-tme-input::placeholder { color: var(--text-3, #71717A); }\
            .sbms-tme-input:focus {\
                border-color: var(--brand, #B86CDA);\
                box-shadow: var(--focus-ring, 0 0 0 3px rgba(184,108,218,.32));\
            }\
            .sbms-tme-err {\
                min-height: 16px;\
                font-size: var(--text-xs, 12px);\
                color: var(--danger-text, #F87171);\
                margin: 2px 0 6px 0;\
            }\
            .sbms-tme-foot {\
                display: flex; gap: 8px; justify-content: flex-end;\
                padding: 6px 20px 18px 20px;\
            }\
            .sbms-tme-hint {\
                margin: 0 20px 14px 20px;\
                padding: 8px 10px;\
                background: var(--bg-subtle, #1C1C24);\
                border: 1px solid var(--border-subtle, rgba(63,63,70,.5));\
                border-radius: var(--radius-sm, 6px);\
                color: var(--text-3, #71717A);\
                font-size: 11px;\
                line-height: 1.45;\
                display: flex; gap: 6px; align-items: flex-start;\
            }\
            .sbms-tme-hint__dot {\
                width: 6px; height: 6px; flex: 0 0 6px;\
                border-radius: 50%; margin-top: 6px;\
                background: var(--success, #22C55E);\
            }\
        ';
        const tag = document.createElement('style');
        tag.id = 'sbms-tme-modal-styles';
        tag.textContent = css;
        document.head.appendChild(tag);
    }

    function ensureModal() {
        if (_modalEl) return _modalEl;
        injectStyles();
        const wrap = document.createElement('div');
        wrap.id = 'sbmsTmeModal';
        wrap.className = 'sbms-tme-overlay';
        wrap.setAttribute('role', 'dialog');
        wrap.setAttribute('aria-modal', 'true');
        wrap.setAttribute('aria-labelledby', 'sbmsTmeTitle');
        wrap.innerHTML =
            '<div class="sbms-tme-dialog" role="document">' +
              '<div class="sbms-tme-head">' +
                '<span class="sbms-tme-head__icon" aria-hidden="true">T</span>' +
                '<div>' +
                  '<div class="sbms-tme-head__title" id="sbmsTmeTitle">Вход в TME</div>' +
                  '<div class="sbms-tme-head__sub">Нужен для запроса PCRF/COS сервисов</div>' +
                '</div>' +
              '</div>' +
              '<form id="sbmsTmeForm" autocomplete="off" novalidate>' +
                '<div class="sbms-tme-body">' +
                  '<div class="sbms-tme-field">' +
                    '<label class="sbms-tme-field__label" for="sbmsTmeLogin">Логин</label>' +
                    '<input id="sbmsTmeLogin" class="sbms-tme-input" type="text" autocomplete="username" required>' +
                  '</div>' +
                  '<div class="sbms-tme-field">' +
                    '<label class="sbms-tme-field__label" for="sbmsTmePass">Пароль</label>' +
                    '<input id="sbmsTmePass" class="sbms-tme-input" type="password" autocomplete="current-password" required>' +
                  '</div>' +
                  '<div class="sbms-tme-err" id="sbmsTmeErr" role="alert" aria-live="polite"></div>' +
                '</div>' +
                '<div class="sbms-tme-hint">' +
                  '<span class="sbms-tme-hint__dot"></span>' +
                  '<span>Токен хранится только на сервере. В браузере остаётся только логин — пароль и токен сюда не сохраняются.</span>' +
                '</div>' +
                '<div class="sbms-tme-foot">' +
                  '<button type="button" id="sbmsTmeCancel" class="sb-btn sb-btn--ghost sb-btn--sm">Отмена</button>' +
                  '<button type="submit" id="sbmsTmeSubmit" class="sb-btn sb-btn--primary sb-btn--sm">Войти</button>' +
                '</div>' +
              '</form>' +
            '</div>';
        document.body.appendChild(wrap);

        const form   = wrap.querySelector('#sbmsTmeForm');
        const cancel = wrap.querySelector('#sbmsTmeCancel');
        const errEl  = wrap.querySelector('#sbmsTmeErr');
        const submit = wrap.querySelector('#sbmsTmeSubmit');

        function close(result) {
            wrap.style.display = 'none';
            errEl.textContent = '';
            const passEl = wrap.querySelector('#sbmsTmePass');
            if (passEl) passEl.value = ''; // пароль зануляем сразу
            const r = _pendingResolve; _pendingResolve = null;
            if (r) r(result);
        }

        cancel.addEventListener('click', function () { close(null); });
        wrap.addEventListener('click', function (e) { if (e.target === wrap) close(null); });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && wrap.style.display !== 'none' && wrap.style.display) close(null);
        });

        form.addEventListener('submit', async function (e) {
            e.preventDefault();
            const login = wrap.querySelector('#sbmsTmeLogin').value.trim();
            const pass  = wrap.querySelector('#sbmsTmePass').value;
            if (!login || !pass) return;
            errEl.textContent = '';
            submit.disabled = true;
            const submitOriginal = submit.textContent;
            submit.textContent = 'Входим…';
            try {
                const resp = await fetch('/api/tme/auth', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username: login, password: pass }),
                });
                const data = await resp.json().catch(function () { return {}; });
                if (!resp.ok || !data.token) {
                    errEl.textContent = (data && (data.error || (data.body && data.body.message))) || 'Ошибка входа в TME';
                    submit.disabled = false; submit.textContent = submitOriginal;
                    return;
                }
                setUser(login);
                if (data.expiresAt) setExp(data.expiresAt);
                close({ username: login });
            } catch (err) {
                errEl.textContent = 'Сеть/сервер недоступны';
                submit.disabled = false; submit.textContent = submitOriginal;
            } finally {
                // пароль зануляется в close(); если не дошли — занулим явно тут
                const passEl = wrap.querySelector('#sbmsTmePass');
                if (passEl) passEl.value = '';
            }
        });

        _modalEl = wrap;
        return wrap;
    }

    function openLoginModal(prefillLogin) {
        const wrap = ensureModal();
        wrap.querySelector('#sbmsTmeLogin').value = prefillLogin || getUser() || '';
        wrap.querySelector('#sbmsTmePass').value = '';
        wrap.querySelector('#sbmsTmeErr').textContent = '';
        const submit = wrap.querySelector('#sbmsTmeSubmit');
        submit.disabled = false; submit.textContent = 'Войти';
        wrap.style.display = 'flex';
        setTimeout(function () {
            const u = wrap.querySelector('#sbmsTmeLogin').value
                ? wrap.querySelector('#sbmsTmePass')
                : wrap.querySelector('#sbmsTmeLogin');
            try { u.focus(); u.select && u.select(); } catch (_) {}
        }, 50);
        return new Promise(function (resolve) { _pendingResolve = resolve; });
    }

    // ------- основной API -------
    async function fetchPcrfServices(msisdn, opts) {
        opts = opts || {};
        const allowLoginPrompt = opts.silent !== true;
        if (!msisdn) throw new Error('msisdn required');

        async function call() {
            const resp = await fetch('/api/tme/pcrf-services', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ msisdn: String(msisdn), tmeUser: getUser() }),
            });
            const data = await resp.json().catch(function () { return {}; });
            return { resp: resp, data: data };
        }

        let { resp, data } = await call();
        if (resp.status === 401 && (data.code === 'TME_AUTH_REQUIRED' || data.code === 'TME_AUTH_EXPIRED')) {
            if (!allowLoginPrompt) {
                const e = new Error(data.error || 'TME auth required');
                e.code = data.code; e.status = 401; throw e;
            }
            // токен на сервере истёк/не было — открываем модалку, после успеха ретраим
            const ok = await openLoginModal(getUser());
            if (!ok) {
                const e = new Error('TME login cancelled');
                e.code = 'TME_AUTH_CANCELLED'; throw e;
            }
            ({ resp, data } = await call());
        }
        if (!resp.ok) {
            const e = new Error((data && data.error) || ('TME error ' + resp.status));
            e.code = data && data.code; e.status = resp.status; e.body = data;
            throw e;
        }
        return data;
    }

    // tone из бэкенда → CSS-цвет (CSS-переменные с фолбэками)
    const TONE_FG = {
        success: 'var(--success-text,#3ddc97)',
        warn:    'var(--warning-text,#ffb84d)',
        info:    'var(--info-text,#4ea1ff)',
        danger:  'var(--danger-text,#ff6b6b)',
        muted:   'var(--text-3,#8893a4)',
    };
    const TONE_BG = {
        success: 'rgba(61,220,151,.12)',
        warn:    'rgba(255,184,77,.14)',
        info:    'rgba(78,161,255,.12)',
        danger:  'rgba(255,107,107,.14)',
        muted:   'rgba(136,147,164,.12)',
    };

    function badge(label, tone, rawTitle) {
        const fg = TONE_FG[tone] || TONE_FG.muted;
        const bg = TONE_BG[tone] || TONE_BG.muted;
        const titleAttr = rawTitle ? ' title="raw: ' + escapeAttr(rawTitle) + '"' : '';
        return '<span class="sbms-tme-badge"' + titleAttr + ' style="display:inline-block;padding:2px 8px;' +
               'border-radius:10px;font-size:11px;line-height:1.6;background:' + bg + ';color:' + fg + ';">' +
               escapeHtml(label) + '</span>';
    }
    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function escapeAttr(s) { return escapeHtml(s); }

    function fmtDate(iso) {
        if (!iso) return '—';
        // "2026-05-07T16:46:08" → "07.05.26 16:46"
        const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})[T\s](\d{2}):(\d{2})/);
        if (!m) return iso;
        return m[3] + '.' + m[2] + '.' + m[1].slice(2) + ' ' + m[4] + ':' + m[5];
    }

    global.SbmsTme = {
        getUser:           getUser,
        clearSession:      clearSession,
        openLoginModal:    openLoginModal,
        fetchPcrfServices: fetchPcrfServices,
        renderBadge:       badge,
        fmtDate:           fmtDate,
    };
})(window);
