/**
 * SBMS Session Manager — единая точка работы с авторизацией.
 *
 * Модель:
 *  - login (username) запоминается в localStorage (удобство), пароль НЕ хранится.
 *  - token + expiresAt хранятся в sessionStorage (живут в рамках вкладки).
 *  - Все запросы к /api/* и /proxy/* автоматически получают authToken.
 *  - При 401/403 токен инвалидируется и открывается модальное окно входа.
 *  - Визуальный бейдж показывает статус и обратный отсчёт до истечения.
 *
 * Использование на странице:
 *    <script src="/static/sbms-auth.js"></script>
 *    <script>
 *      SbmsAuth.mountBadge('#authBadge');
 *      SbmsAuth.require().then(() => initApp());
 *      // в своих запросах:
 *      fetch(SbmsAuth.proxyUrl('/OAPI/v1/...', {otherParam: 'x'}))
 *      // или:
 *      const res = await SbmsAuth.apiPost('/api/tariff/load-customer', {msisdn});
 *    </script>
 */
(function (global) {
    'use strict';

    const LS_LOGIN = 'sbms.login';
    const SS_TOKEN = 'sbms.token';
    const SS_EXPIRES = 'sbms.expiresAt';
    // Пароль нужен ТОЛЬКО для PSIX-grid процедур (STRAFF_CALLS_R и пр.),
    // которые требуют HTTP Basic поверх SESSION_ID. Хранится в sessionStorage —
    // живёт в пределах вкладки, чистится при logout.
    const SS_PW = 'sbms.pw';
    const DEFAULT_TTL_SEC = 25 * 60; // совпадает с серверным TOKEN_TTL

    const listeners = new Set();
    let _token = sessionStorage.getItem(SS_TOKEN) || null;
    let _expiresAt = Number(sessionStorage.getItem(SS_EXPIRES)) || 0;
    let _login = localStorage.getItem(LS_LOGIN) || '';
    let _tickTimer = null;

    function now() { return Math.floor(Date.now() / 1000); }
    function isValid() { return !!_token && _expiresAt > now(); }
    function remaining() { return Math.max(0, _expiresAt - now()); }

    function setSession(token, ttlSec, loginName, password) {
        _token = token;
        _expiresAt = now() + (ttlSec || DEFAULT_TTL_SEC);
        if (loginName) {
            _login = loginName;
            localStorage.setItem(LS_LOGIN, loginName);
        }
        sessionStorage.setItem(SS_TOKEN, token);
        sessionStorage.setItem(SS_EXPIRES, String(_expiresAt));
        if (password) sessionStorage.setItem(SS_PW, password);
        emit('login');
    }

    function clearSession(reason) {
        _token = null;
        _expiresAt = 0;
        sessionStorage.removeItem(SS_TOKEN);
        sessionStorage.removeItem(SS_EXPIRES);
        sessionStorage.removeItem(SS_PW);
        emit('logout', reason);
    }

    function getPassword() {
        return sessionStorage.getItem(SS_PW) || '';
    }

    function emit(event, payload) {
        listeners.forEach(fn => { try { fn(event, payload); } catch (_) {} });
    }

    function on(fn) { listeners.add(fn); return () => listeners.delete(fn); }

    async function authenticate(login, password) {
        if (!login || !password) throw new Error('Введите логин и пароль');
        const resp = await fetch('/api/auth', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ login, password })
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || !data.token) {
            throw new Error(data.error || `Auth failed (HTTP ${resp.status})`);
        }
        setSession(data.token, data.expiresIn || DEFAULT_TTL_SEC, login, password);
        return data.token;
    }

    function appendAuth(params) {
        const out = Object.assign({}, params || {});
        if (_token) out.authToken = _token;
        return out;
    }

    function proxyUrl(path, params) {
        const qs = new URLSearchParams(appendAuth(params)).toString();
        const p = path.startsWith('/') ? path : '/' + path;
        return `/proxy${p}${qs ? '?' + qs : ''}`;
    }

    async function _handle(resp) {
        if (resp.status === 401 || resp.status === 403) {
            clearSession('expired');
            await promptLogin('Сессия истекла. Войдите снова.');
            throw new Error('Session expired — re-login required');
        }
        return resp;
    }

    async function apiPost(path, body) {
        const resp = await fetch(path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(Object.assign({ authToken: _token }, body || {}))
        });
        return _handle(resp);
    }

    async function apiGet(path, params) {
        const qs = new URLSearchParams(appendAuth(params)).toString();
        const resp = await fetch(`${path}${qs ? '?' + qs : ''}`);
        return _handle(resp);
    }

    async function proxyFetch(path, params, options) {
        const resp = await fetch(proxyUrl(path, params), options);
        return _handle(resp);
    }

    // ==================== UI: Badge + Modal ====================

    const STYLE = `
    .sbms-auth-badge{display:inline-flex;align-items:center;gap:8px;padding:6px 12px;border-radius:999px;font-size:12px;font-weight:600;font-family:system-ui,sans-serif;border:1px solid transparent;cursor:pointer;transition:background .15s}
    .sbms-auth-badge .dot{width:8px;height:8px;border-radius:50%;background:#94a3b8}
    .sbms-auth-badge.ok{background:#10b98122;color:#10b981;border-color:#10b98144}
    .sbms-auth-badge.ok .dot{background:#10b981;box-shadow:0 0 6px #10b981}
    .sbms-auth-badge.warn{background:#f59e0b22;color:#f59e0b;border-color:#f59e0b44}
    .sbms-auth-badge.warn .dot{background:#f59e0b}
    .sbms-auth-badge.off{background:#64748b22;color:#94a3b8;border-color:#64748b44}
    .sbms-auth-badge:hover{filter:brightness(1.15)}
    .sbms-modal-bg{position:fixed;inset:0;background:rgba(2,6,23,.75);backdrop-filter:blur(4px);display:flex;align-items:center;justify-content:center;z-index:9999;font-family:system-ui,sans-serif}
    .sbms-modal{background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:12px;padding:28px;width:380px;max-width:92vw;box-shadow:0 20px 60px rgba(0,0,0,.5)}
    .sbms-modal h2{margin:0 0 4px;font-size:18px;font-weight:600}
    .sbms-modal .sub{color:#94a3b8;font-size:13px;margin-bottom:18px}
    .sbms-modal label{display:block;font-size:12px;color:#94a3b8;margin:10px 0 4px;text-transform:uppercase;letter-spacing:.04em}
    .sbms-modal input{width:100%;padding:10px 12px;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:14px;box-sizing:border-box}
    .sbms-modal input:focus{outline:none;border-color:#3b82f6}
    .sbms-modal .err{color:#ef4444;font-size:12px;min-height:16px;margin-top:10px}
    .sbms-modal .btns{display:flex;gap:8px;margin-top:16px}
    .sbms-modal button{flex:1;padding:10px;border-radius:6px;font-weight:600;cursor:pointer;border:none;font-size:14px}
    .sbms-modal .primary{background:#3b82f6;color:#fff}
    .sbms-modal .primary:hover{background:#2563eb}
    .sbms-modal .primary:disabled{opacity:.6;cursor:wait}
    .sbms-modal .ghost{background:transparent;color:#94a3b8;border:1px solid #334155}
    `;

    function injectStyle() {
        if (document.getElementById('sbms-auth-style')) return;
        const s = document.createElement('style');
        s.id = 'sbms-auth-style';
        s.textContent = STYLE;
        document.head.appendChild(s);
    }

    function fmtRemaining(sec) {
        if (sec <= 0) return 'истекла';
        const m = Math.floor(sec / 60);
        const s = sec % 60;
        return `${m}:${String(s).padStart(2, '0')}`;
    }

    function mountBadge(selector) {
        injectStyle();
        const host = typeof selector === 'string' ? document.querySelector(selector) : selector;
        if (!host) return;
        const el = document.createElement('button');
        el.className = 'sbms-auth-badge off';
        el.type = 'button';
        el.innerHTML = '<span class="dot"></span><span class="text">Не авторизован</span>';
        el.onclick = () => isValid() ? logout() : promptLogin();
        host.appendChild(el);

        function render() {
            const textEl = el.querySelector('.text');
            if (isValid()) {
                const left = remaining();
                el.className = 'sbms-auth-badge ' + (left < 120 ? 'warn' : 'ok');
                textEl.textContent = `${_login || 'сессия'} · ${fmtRemaining(left)}`;
                el.title = 'Кликните, чтобы выйти';
            } else {
                el.className = 'sbms-auth-badge off';
                textEl.textContent = 'Войти';
                el.title = 'Кликните, чтобы войти';
            }
        }

        render();
        on(render);
        if (!_tickTimer) _tickTimer = setInterval(() => emit('tick'), 1000);
        on((ev) => { if (ev === 'tick' && isValid() && remaining() === 0) clearSession('expired'); });
    }

    function logout() {
        clearSession('user');
    }

    function promptLogin(message) {
        if (document.getElementById('sbms-auth-modal')) return Promise.resolve(_token);
        injectStyle();
        return new Promise((resolve, reject) => {
            const bg = document.createElement('div');
            bg.className = 'sbms-modal-bg';
            bg.id = 'sbms-auth-modal';
            bg.innerHTML = `
                <div class="sbms-modal" role="dialog" aria-label="Вход в SBMS">
                    <h2>Вход в SBMS</h2>
                    <div class="sub">${message || 'Укажите учётные данные для работы с API'}</div>
                    <label>Логин</label>
                    <input id="_sbmsL" type="text" autocomplete="username" />
                    <label>Пароль</label>
                    <input id="_sbmsP" type="password" autocomplete="current-password" />
                    <div class="err" id="_sbmsE"></div>
                    <div class="btns">
                        <button class="ghost" type="button" id="_sbmsCancel">Отмена</button>
                        <button class="primary" type="button" id="_sbmsOk">Войти</button>
                    </div>
                </div>`;
            document.body.appendChild(bg);
            const l = bg.querySelector('#_sbmsL');
            const p = bg.querySelector('#_sbmsP');
            const e = bg.querySelector('#_sbmsE');
            const ok = bg.querySelector('#_sbmsOk');
            const cancel = bg.querySelector('#_sbmsCancel');
            l.value = _login || '';
            (l.value ? p : l).focus();

            function close() { bg.remove(); }
            cancel.onclick = () => { close(); reject(new Error('cancelled')); };
            bg.addEventListener('click', (ev) => { if (ev.target === bg) { close(); reject(new Error('cancelled')); } });

            async function submit() {
                e.textContent = '';
                ok.disabled = true;
                ok.textContent = 'Вход...';
                try {
                    await authenticate(l.value.trim(), p.value);
                    close();
                    resolve(_token);
                } catch (err) {
                    e.textContent = err.message || 'Ошибка входа';
                    ok.disabled = false;
                    ok.textContent = 'Войти';
                    p.focus(); p.select();
                }
            }
            ok.onclick = submit;
            [l, p].forEach(el => el.addEventListener('keydown', (ev) => {
                if (ev.key === 'Enter') submit();
                if (ev.key === 'Escape') { close(); reject(new Error('cancelled')); }
            }));
        });
    }

    async function require() {
        if (isValid()) return _token;
        return promptLogin();
    }

    global.SbmsAuth = {
        get token() { return isValid() ? _token : null; },
        get login() { return _login; },
        get password() { return getPassword(); },
        get expiresAt() { return _expiresAt; },
        isValid, remaining,
        authenticate, logout, getPassword,
        setSession, clearSession,
        appendAuth, proxyUrl, apiGet, apiPost, proxyFetch,
        mountBadge, promptLogin, require, on
    };
})(window);
