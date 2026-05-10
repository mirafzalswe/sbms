/**
 * SbmsCmdK — Command Palette (⌘K / Ctrl+K).
 *
 *   SbmsCmdK.open();
 *   SbmsCmdK.close();
 *   SbmsCmdK.register({
 *       id: 'my.action',
 *       title: 'Сделать что-то',
 *       hint: 'Описание',
 *       group: 'Custom',
 *       keywords: 'тариф packs',
 *       icon: '<svg…/>',
 *       run: () => { … },
 *   });
 *   SbmsCmdK.unregister('my.action');
 *
 * Базовые группы: Навигация, Действия, Тема, Недавние.
 * Стилизация — DS-токены.
 * ARIA + клавиатура: ↑ ↓ Enter Esc, ⌘K toggle.
 */
(function (global) {
    'use strict';

    const customCommands = new Map();

    /* ===== Стили (один раз) ===== */
    const STYLE = `
    .sb-cmdk-bg{position:fixed;inset:0;background:var(--bg-overlay);backdrop-filter:blur(4px);
        z-index:var(--z-modal,2000);display:flex;align-items:flex-start;justify-content:center;
        padding-top:12vh;animation:sb-cmdk-fade 160ms var(--ease-standard,ease)}
    @keyframes sb-cmdk-fade{from{opacity:0}to{opacity:1}}
    .sb-cmdk{width:600px;max-width:92vw;max-height:70vh;display:flex;flex-direction:column;
        background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--radius-xl,14px);
        box-shadow:var(--shadow-xl);overflow:hidden;
        animation:sb-cmdk-pop 200ms cubic-bezier(.3,.7,.1,1.1)}
    @keyframes sb-cmdk-pop{from{opacity:0;transform:translateY(-8px) scale(.98)}to{opacity:1;transform:translateY(0) scale(1)}}
    .sb-cmdk__head{display:flex;align-items:center;gap:10px;padding:12px 14px;border-bottom:1px solid var(--border);}
    .sb-cmdk__head svg{flex-shrink:0;color:var(--text-3)}
    .sb-cmdk__input{flex:1 1 auto;border:none;outline:none;background:transparent;
        color:var(--text-1);font-size:15px;font-family:var(--font-sans);min-width:0;height:24px}
    .sb-cmdk__input::placeholder{color:var(--text-3)}
    .sb-cmdk__kbd{display:inline-flex;align-items:center;gap:2px;font-size:11px;color:var(--text-3);
        background:var(--bg-subtle);border:1px solid var(--border);border-radius:var(--radius-xs,4px);
        padding:2px 6px;font-family:var(--font-mono);flex-shrink:0}
    .sb-cmdk__list{flex:1 1 auto;overflow-y:auto;padding:6px}
    .sb-cmdk__group{padding:8px 10px 4px;font-size:11px;color:var(--text-3);
        text-transform:uppercase;letter-spacing:.06em;font-weight:600}
    .sb-cmdk__item{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:var(--radius-md,8px);
        cursor:pointer;color:var(--text-1);font-size:14px;line-height:1.3;
        transition:background var(--motion-fast,150ms) var(--ease-standard,ease)}
    .sb-cmdk__item:hover,.sb-cmdk__item.is-active{background:var(--brand-soft);color:var(--brand)}
    .sb-cmdk__item.is-active{outline:none}
    .sb-cmdk__icon{flex:0 0 24px;height:24px;display:inline-flex;align-items:center;justify-content:center;
        color:currentColor}
    .sb-cmdk__title{flex:1 1 auto;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .sb-cmdk__hint{flex:0 0 auto;font-size:12px;color:var(--text-3);font-family:var(--font-mono);
        white-space:nowrap;max-width:240px;overflow:hidden;text-overflow:ellipsis}
    .sb-cmdk__item.is-active .sb-cmdk__hint{color:var(--brand)}
    .sb-cmdk__empty{padding:32px 16px;text-align:center;color:var(--text-3);font-size:13px}
    .sb-cmdk__foot{display:flex;align-items:center;gap:10px;padding:8px 12px;border-top:1px solid var(--border);
        background:var(--bg-subtle);font-size:11px;color:var(--text-3)}
    .sb-cmdk__foot .sp{flex:1 1 auto}
    `;

    function injectStyle() {
        if (document.getElementById('sbms-cmdk-style')) return;
        const s = document.createElement('style');
        s.id = 'sbms-cmdk-style';
        s.textContent = STYLE;
        document.head.appendChild(s);
    }

    /* ===== Иконки ===== */
    const I = {
        search: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>',
        nav:    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="m12 5 7 7-7 7"/></svg>',
        sun:    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>',
        moon:   '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>',
        login:  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><path d="m10 17 5-5-5-5"/><path d="M15 12H3"/></svg>',
        logout: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/></svg>',
        user:   '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
        copy:   '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
        trash:  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
    };

    /* ===== Базовые команды ===== */
    function buildCommands(query) {
        const cmds = [];

        // ----- Навигация -----
        const NAV = (global.SbmsNav && global.SbmsNav.LINKS) || [
            { id: 'matrix',    href: '/matrix-test',          label: 'Матрица' },
            { id: 'product',   href: '/product-availability', label: 'Доступность' },
            { id: 'tme',       href: '/tme',                  label: 'TME' },
        ];
        const here = window.location.pathname;
        NAV.forEach(link => {
            if (link.href === here) return;
            cmds.push({
                id: 'nav.' + link.id,
                title: 'Перейти: ' + link.label,
                hint: link.href,
                group: 'Навигация',
                keywords: link.label + ' ' + link.id,
                icon: I.nav,
                run: () => { window.location.href = link.href; },
            });
        });

        // ----- Тема -----
        const themeCur = (global.SbmsNav && global.SbmsNav.getTheme && global.SbmsNav.getTheme()) ||
                         localStorage.getItem('sbms-theme') || 'dark';
        const next = themeCur === 'dark' ? 'light' : 'dark';
        cmds.push({
            id: 'theme.toggle',
            title: 'Переключить тему: ' + (next === 'dark' ? 'Тёмная' : 'Светлая'),
            hint: themeCur + ' → ' + next,
            group: 'Внешний вид',
            keywords: 'theme dark light тема',
            icon: next === 'dark' ? I.moon : I.sun,
            run: () => {
                if (global.SbmsNav && global.SbmsNav.toggleTheme) global.SbmsNav.toggleTheme();
                else {
                    document.documentElement.setAttribute('data-theme', next);
                    localStorage.setItem('sbms-theme', next);
                }
            },
        });

        // ----- Авторизация -----
        if (global.SbmsAuth) {
            if (global.SbmsAuth.isValid && global.SbmsAuth.isValid()) {
                cmds.push({
                    id: 'auth.logout',
                    title: 'Выйти из SBMS',
                    hint: global.SbmsAuth.login || '',
                    group: 'Авторизация',
                    keywords: 'logout выход sign out',
                    icon: I.logout,
                    run: () => global.SbmsAuth.logout(),
                });
            } else {
                cmds.push({
                    id: 'auth.login',
                    title: 'Войти в SBMS',
                    hint: '⌘ Enter',
                    group: 'Авторизация',
                    keywords: 'login войти sign in',
                    icon: I.login,
                    run: () => global.SbmsAuth.promptLogin && global.SbmsAuth.promptLogin(),
                });
            }
        }

        // ----- Recent MSISDN -----
        if (global.SbmsRecent) {
            const rec = global.SbmsRecent.list();
            rec.forEach(r => {
                const groupName = r.pinned ? '★ Закреплённые' : 'Недавние номера';
                r._group = groupName;
                cmds.push({
                    id: 'recent.' + r.msisdn,
                    title: (r.pinned ? '★ ' : '') + r.msisdn + (r.label ? ' · ' + r.label : ''),
                    hint: r.page ? 'был открыт в ' + r.page : 'недавний номер',
                    group: r._group,
                    keywords: r.msisdn + ' ' + (r.label || '') + ' ' + (r.page || ''),
                    icon: I.user,
                    run: () => {
                        const msisdnFields = [
                            'fMsisdn','msisdn','tmeMsisdn','f-msisdn',
                        ];
                        let filled = false;
                        msisdnFields.forEach(id => {
                            const el = document.getElementById(id);
                            if (el && !filled) {
                                el.value = r.msisdn;
                                el.dispatchEvent(new Event('input', { bubbles: true }));
                                el.dispatchEvent(new Event('change', { bubbles: true }));
                                el.focus();
                                filled = true;
                            }
                        });
                        if (!filled) {
                            // На странице нет поля MSISDN — открываем карточку абонента
                            window.location.href = '/subscriber?msisdn=' + encodeURIComponent(r.msisdn);
                        }
                    },
                });
                // Кнопка «Открыть карточку» — всегда доступна, даже если есть поле
                cmds.push({
                    id: 'recent.open.' + r.msisdn,
                    title: 'Открыть карточку: ' + r.msisdn,
                    hint: r.label || 'subscriber workspace',
                    group: r._group,
                    keywords: 'open subscriber карточка ' + r.msisdn + ' ' + (r.label || ''),
                    icon: I.nav,
                    run: () => {
                        window.location.href = '/subscriber?msisdn=' + encodeURIComponent(r.msisdn);
                    },
                });
                // Toggle pin
                cmds.push({
                    id: 'recent.pin.' + r.msisdn,
                    title: (r.pinned ? 'Открепить: ' : 'Закрепить: ') + r.msisdn,
                    hint: r.label || '',
                    group: r._group,
                    keywords: 'pin закрепить ' + r.msisdn,
                    icon: r.pinned ? I.trash : I.copy,
                    run: () => {
                        global.SbmsRecent.togglePin(r.msisdn, { label: r.label, page: r.page });
                        if (global.SbmsToast) {
                            global.SbmsToast[r.pinned ? 'info' : 'success'](
                                (r.pinned ? 'Откреплён ' : 'Закреплён ') + r.msisdn
                            );
                        }
                    },
                });
            });
            if (rec.length) {
                cmds.push({
                    id: 'recent.clear',
                    title: 'Очистить список недавних',
                    hint: rec.length + ' шт.',
                    group: 'Недавние номера',
                    keywords: 'clear очистить recent',
                    icon: I.trash,
                    run: () => {
                        global.SbmsRecent.clear();
                        if (global.SbmsToast) global.SbmsToast.success('Список очищен');
                    },
                });
            }
        }

        // ----- Custom registered -----
        customCommands.forEach(c => cmds.push(c));

        // ----- Filter by query -----
        if (!query) return cmds;
        const q = query.trim().toLowerCase();
        return cmds
            .map(c => {
                const hay = (c.title + ' ' + (c.hint || '') + ' ' + (c.keywords || '')).toLowerCase();
                if (!hay.includes(q)) return null;
                // small score: starts-with > contains
                const score = hay.startsWith(q) ? 2 : (c.title.toLowerCase().startsWith(q) ? 1 : 0);
                return { c, score };
            })
            .filter(Boolean)
            .sort((a,b) => b.score - a.score)
            .map(x => x.c);
    }

    /* ===== State ===== */
    let _open = false;
    let _bg, _input, _list, _activeIdx = 0, _shown = [];

    function render() {
        if (!_open) return;
        const cmds = buildCommands(_input.value);
        _shown = cmds;

        if (!cmds.length) {
            _list.innerHTML = '<div class="sb-cmdk__empty">Ничего не найдено</div>';
            return;
        }

        // group
        const groups = {};
        const order = [];
        cmds.forEach(c => {
            if (!groups[c.group]) { groups[c.group] = []; order.push(c.group); }
            groups[c.group].push(c);
        });

        let html = '';
        let idx = 0;
        order.forEach(g => {
            html += `<div class="sb-cmdk__group">${escapeHtml(g)}</div>`;
            groups[g].forEach(c => {
                const active = idx === _activeIdx ? ' is-active' : '';
                html += `<div class="sb-cmdk__item${active}" role="option" data-idx="${idx}" data-id="${escapeHtml(c.id)}">
                    <span class="sb-cmdk__icon">${c.icon || ''}</span>
                    <span class="sb-cmdk__title">${escapeHtml(c.title)}</span>
                    ${c.hint ? `<span class="sb-cmdk__hint">${escapeHtml(c.hint)}</span>` : ''}
                </div>`;
                idx++;
            });
        });
        _list.innerHTML = html;

        // bind clicks
        _list.querySelectorAll('.sb-cmdk__item').forEach(el => {
            el.addEventListener('mouseenter', () => {
                _activeIdx = Number(el.dataset.idx);
                _list.querySelectorAll('.is-active').forEach(x => x.classList.remove('is-active'));
                el.classList.add('is-active');
            });
            el.addEventListener('click', () => {
                const i = Number(el.dataset.idx);
                executeAt(i);
            });
        });

        // scroll active into view
        const act = _list.querySelector('.is-active');
        if (act) act.scrollIntoView({ block: 'nearest' });
    }

    function executeAt(i) {
        const c = _shown[i];
        if (!c) return;
        close();
        try { c.run(); } catch (e) {
            if (global.SbmsToast) global.SbmsToast.error('Ошибка: ' + (e.message || e));
            else console.error(e);
        }
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function open() {
        if (_open) return;
        injectStyle();
        _open = true;

        const isMac = /Mac|iPhone|iPad/i.test(navigator.platform);
        _bg = document.createElement('div');
        _bg.className = 'sb-cmdk-bg';
        _bg.setAttribute('role', 'dialog');
        _bg.setAttribute('aria-modal', 'true');
        _bg.setAttribute('aria-label', 'Командное меню');
        _bg.innerHTML = `
            <div class="sb-cmdk" role="combobox" aria-haspopup="listbox" aria-expanded="true">
                <div class="sb-cmdk__head">
                    ${I.search}
                    <input class="sb-cmdk__input" type="text" placeholder="Команды, страницы, номера…"
                           autocomplete="off" spellcheck="false" aria-label="Поиск команд" />
                    <span class="sb-cmdk__kbd">esc</span>
                </div>
                <div class="sb-cmdk__list" role="listbox"></div>
                <div class="sb-cmdk__foot">
                    <span><span class="sb-cmdk__kbd">↑</span> <span class="sb-cmdk__kbd">↓</span> навигация</span>
                    <span><span class="sb-cmdk__kbd">↵</span> выбрать</span>
                    <span class="sp"></span>
                    <span><span class="sb-cmdk__kbd">${isMac ? '⌘' : 'Ctrl'}</span> <span class="sb-cmdk__kbd">K</span> открыть</span>
                </div>
            </div>`;
        document.body.appendChild(_bg);

        _input = _bg.querySelector('.sb-cmdk__input');
        _list  = _bg.querySelector('.sb-cmdk__list');
        _activeIdx = 0;

        _input.addEventListener('input', () => { _activeIdx = 0; render(); });
        _input.addEventListener('keydown', onKey);
        _bg.addEventListener('click', (ev) => { if (ev.target === _bg) close(); });

        setTimeout(() => _input.focus(), 0);
        render();
    }

    function close() {
        if (!_open) return;
        _open = false;
        if (_bg) _bg.remove();
        _bg = _input = _list = null;
        _shown = [];
    }

    function onKey(ev) {
        if (ev.key === 'ArrowDown') {
            ev.preventDefault();
            if (_activeIdx < _shown.length - 1) _activeIdx++; else _activeIdx = 0;
            render();
        } else if (ev.key === 'ArrowUp') {
            ev.preventDefault();
            if (_activeIdx > 0) _activeIdx--; else _activeIdx = Math.max(0, _shown.length - 1);
            render();
        } else if (ev.key === 'Enter') {
            ev.preventDefault();
            executeAt(_activeIdx);
        } else if (ev.key === 'Escape') {
            ev.preventDefault();
            close();
        }
    }

    /* ===== Global hotkey ===== */
    document.addEventListener('keydown', (ev) => {
        const k = ev.key.toLowerCase();
        const mod = ev.metaKey || ev.ctrlKey;
        if (mod && k === 'k') {
            ev.preventDefault();
            _open ? close() : open();
        }
    });

    function register(cmd) {
        if (!cmd || !cmd.id || typeof cmd.run !== 'function') return;
        cmd.group = cmd.group || 'Действия';
        customCommands.set(cmd.id, cmd);
    }

    function unregister(id) { customCommands.delete(id); }

    global.SbmsCmdK = { open, close, register, unregister };
})(window);
