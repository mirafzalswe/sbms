/**
 * SBMS Nav — единая навигация для всех страниц.
 *
 * Использование:
 *   <link rel="stylesheet" href="/static/sbms-design.css">
 *   <link rel="stylesheet" href="/static/sbms-ui.css">
 *   <script src="/static/sbms-auth.js"></script>
 *   <script src="/static/sbms-nav.js"></script>
 *   <div id="sbmsNav"></div>
 *   <script>
 *     SbmsNav.mount('#sbmsNav', { active: 'tme' });
 *   </script>
 *
 * Параметры:
 *   active   — id активного раздела (см. SbmsNav.LINKS)
 *   onTheme  — callback при переключении темы (тема переключается автоматически)
 *   compact  — если true: только иконки (для узких экранов)
 */
(function (global) {
    'use strict';

    const LS_THEME = 'sbms-theme';

    const LINKS = [
        { id: 'subscriber', href: '/subscriber',           label: 'Абонент' },
        { id: 'compare',    href: '/compare',              label: 'Сравнить' },
        { id: 'history',    href: '/test-history',         label: 'История' },
        { id: 'dashboard',  href: '/',                     label: 'Dashboard' },
        { id: 'tester',     href: '/tester',               label: 'QA Tester' },
        { id: 'tariff',     href: '/tariff-test',          label: 'Тариф' },
        { id: 'matrix',     href: '/matrix-test',          label: 'Матрица' },
        { id: 'product',    href: '/product-availability', label: 'Доступность' },
        { id: 'avail-check',href: '/availability-check',   label: 'Проверка пакетов' },
        { id: 'tme',        href: '/tme',                  label: 'TME' },
    ];

    function getTheme() {
        return localStorage.getItem(LS_THEME) || 'dark';
    }

    function applyTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem(LS_THEME, theme);
    }

    function toggleTheme() {
        const cur = getTheme();
        const next = cur === 'dark' ? 'light' : 'dark';
        applyTheme(next);
        return next;
    }

    // Применяем тему ASAP, чтобы не было FOUC
    applyTheme(getTheme());

    // Lazy-load shared модулей (toast / recent / cmdk) если ещё не подгружены
    function loadScript(src) {
        return new Promise((resolve) => {
            if (document.querySelector(`script[src="${src}"]`)) return resolve();
            const s = document.createElement('script');
            s.src = src;
            s.async = false;
            s.onload = s.onerror = () => resolve();
            document.head.appendChild(s);
        });
    }

    async function ensureCompanions() {
        if (!global.SbmsToast)  await loadScript('/static/sbms-toast.js');
        if (!global.SbmsRecent) await loadScript('/static/sbms-recent.js');
        if (!global.SbmsCmdK)   await loadScript('/static/sbms-cmdk.js');
        if (!global.SbmsNotify) await loadScript('/static/sbms-notify.js');
    }

    /* ===== Recent dropdown ===== */
    function mountRecentDropdown() {
        const wrap = document.createElement('div');
        wrap.style.cssText = 'position:relative;display:inline-block;';

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'sb-cmdk-btn';
        btn.setAttribute('aria-label', 'Недавние номера');
        btn.setAttribute('aria-haspopup', 'menu');
        btn.setAttribute('aria-expanded', 'false');
        btn.title = 'Недавние номера';
        btn.innerHTML =
            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="7" r="4"/><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/></svg>' +
            '<span class="sb-recent-btn__count" id="sbmsRecentCount" style="display:none;">0</span>';

        const menu = document.createElement('div');
        menu.className = 'sb-recent-menu';
        menu.setAttribute('role', 'menu');
        menu.style.display = 'none';

        wrap.appendChild(btn);
        wrap.appendChild(menu);

        function render() {
            const list = (global.SbmsRecent && global.SbmsRecent.list()) || [];
            const counter = btn.querySelector('#sbmsRecentCount');
            if (counter) {
                if (list.length) { counter.textContent = String(list.length); counter.style.display = ''; }
                else counter.style.display = 'none';
            }
            if (!list.length) {
                menu.innerHTML =
                    '<div class="sb-recent-menu__empty">Нет недавних номеров.<br>Загрузите абонента, и он появится здесь.</div>';
                return;
            }
            const pinned = list.filter(x => x.pinned);
            const others = list.filter(x => !x.pinned);

            const renderItem = (r) => {
                const lbl = r.label ? '<span class="sb-recent-menu__label">' + escapeHtml(r.label) + '</span>' : '';
                const pg = r.page ? '<span class="sb-recent-menu__page">' + escapeHtml(r.page) + '</span>' : '';
                const star = r.pinned
                    ? '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="12 2 15 8.5 22 9.3 17 14.1 18.2 21 12 17.8 5.8 21 7 14.1 2 9.3 9 8.5"/></svg>'
                    : '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><polygon points="12 2 15 8.5 22 9.3 17 14.1 18.2 21 12 17.8 5.8 21 7 14.1 2 9.3 9 8.5"/></svg>';
                return '<div class="sb-recent-menu__item' + (r.pinned ? ' is-pinned' : '') + '" role="menuitem" tabindex="0" data-msisdn="' + escapeHtml(r.msisdn) + '">' +
                       '<button type="button" class="sb-recent-menu__star" aria-label="' + (r.pinned ? 'Открепить' : 'Закрепить') + '" data-pin-toggle="' + escapeHtml(r.msisdn) + '" data-label="' + escapeHtml(r.label || '') + '" data-page="' + escapeHtml(r.page || '') + '" title="' + (r.pinned ? 'Открепить' : 'Закрепить') + '">' + star + '</button>' +
                       '<span class="sb-recent-menu__msisdn">' + escapeHtml(r.msisdn) + '</span>' + lbl + pg +
                       '</div>';
            };

            let html = '';
            if (pinned.length) {
                html += '<div class="sb-recent-menu__head">★ Закреплённые · ' + pinned.length + '</div>';
                html += pinned.map(renderItem).join('');
            }
            if (others.length) {
                html += '<div class="sb-recent-menu__head">Недавние · ' + others.length + '</div>';
                html += others.map(renderItem).join('');
            }
            html += '<div class="sb-recent-menu__foot">' +
                '<button type="button" class="sb-recent-menu__btn" data-act="open-cmdk">Открыть в ⌘K</button>' +
                '<button type="button" class="sb-recent-menu__btn" data-act="clear">Очистить недавние</button>' +
            '</div>';
            menu.innerHTML = html;

            // Pin / unpin звёздочка — НЕ должна триггерить переход
            menu.querySelectorAll('.sb-recent-menu__star').forEach(starBtn => {
                starBtn.addEventListener('click', (ev) => {
                    ev.stopPropagation();
                    const m = starBtn.dataset.pinToggle;
                    if (!global.SbmsRecent || !m) return;
                    global.SbmsRecent.togglePin(m, {
                        label: starBtn.dataset.label || '',
                        page:  starBtn.dataset.page  || '',
                    });
                    if (global.SbmsToast) {
                        if (global.SbmsRecent.isPinned(m)) global.SbmsToast.success('Закреплён ' + m);
                        else global.SbmsToast.info('Откреплён ' + m);
                    }
                    render();
                });
            });

            menu.querySelectorAll('.sb-recent-menu__item').forEach(el => {
                el.addEventListener('click', () => {
                    const m = el.dataset.msisdn;
                    close();
                    // Если на странице есть поле MSISDN — заполняем; иначе уходим в /subscriber
                    const fields = ['fMsisdn','msisdn','tmeMsisdn'];
                    let filled = false;
                    fields.forEach(id => {
                        const inp = document.getElementById(id);
                        if (inp && !filled) {
                            inp.value = m;
                            inp.dispatchEvent(new Event('input', { bubbles: true }));
                            inp.dispatchEvent(new Event('change', { bubbles: true }));
                            inp.focus();
                            filled = true;
                        }
                    });
                    if (!filled) {
                        window.location.href = '/subscriber?msisdn=' + encodeURIComponent(m);
                    }
                });
            });
            menu.querySelectorAll('.sb-recent-menu__btn').forEach(el => {
                el.addEventListener('click', () => {
                    const act = el.dataset.act;
                    if (act === 'clear' && global.SbmsRecent) {
                        if (confirm('Очистить список недавних номеров?')) {
                            global.SbmsRecent.clear();
                            if (global.SbmsToast) global.SbmsToast.success('Список очищен');
                            close();
                        }
                    } else if (act === 'open-cmdk' && global.SbmsCmdK) {
                        close();
                        global.SbmsCmdK.open();
                    }
                });
            });
        }

        function open() {
            render();
            menu.style.display = '';
            btn.setAttribute('aria-expanded', 'true');
            // Закрытие по клику вне
            setTimeout(() => document.addEventListener('click', onOutside, true), 0);
            // Esc
            document.addEventListener('keydown', onEsc);
        }
        function close() {
            menu.style.display = 'none';
            btn.setAttribute('aria-expanded', 'false');
            document.removeEventListener('click', onOutside, true);
            document.removeEventListener('keydown', onEsc);
        }
        function onOutside(ev) {
            if (!wrap.contains(ev.target)) close();
        }
        function onEsc(ev) { if (ev.key === 'Escape') close(); }

        btn.addEventListener('click', (ev) => {
            ev.stopPropagation();
            const isOpen = menu.style.display !== 'none';
            isOpen ? close() : open();
        });

        // Подписка на изменения Recent
        function tryHook() {
            if (global.SbmsRecent && global.SbmsRecent.on) {
                global.SbmsRecent.on(() => {
                    if (menu.style.display !== 'none') render();
                    // Обновить счётчик даже если меню закрыто
                    const list = global.SbmsRecent.list();
                    const counter = btn.querySelector('#sbmsRecentCount');
                    if (counter) {
                        if (list.length) { counter.textContent = String(list.length); counter.style.display = ''; }
                        else counter.style.display = 'none';
                    }
                });
                // Первичный апдейт счётчика
                const list = global.SbmsRecent.list();
                const counter = btn.querySelector('#sbmsRecentCount');
                if (counter && list.length) { counter.textContent = String(list.length); counter.style.display = ''; }
            } else {
                setTimeout(tryHook, 200);
            }
        }
        tryHook();

        return wrap;
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function el(tag, attrs, children) {
        const e = document.createElement(tag);
        if (attrs) {
            for (const k in attrs) {
                if (k === 'class') e.className = attrs[k];
                else if (k === 'html') e.innerHTML = attrs[k];
                else if (k.startsWith('on') && typeof attrs[k] === 'function') {
                    e.addEventListener(k.slice(2).toLowerCase(), attrs[k]);
                } else {
                    e.setAttribute(k, attrs[k]);
                }
            }
        }
        if (children) {
            (Array.isArray(children) ? children : [children]).forEach(c => {
                if (c == null) return;
                if (typeof c === 'string') e.appendChild(document.createTextNode(c));
                else e.appendChild(c);
            });
        }
        return e;
    }

    function mount(selector, opts) {
        opts = opts || {};
        const host = typeof selector === 'string' ? document.querySelector(selector) : selector;
        if (!host) {
            console.warn('[SbmsNav] host not found:', selector);
            return;
        }

        const activeId = opts.active || '';

        // Brand
        const brand = el('a', {
            class: 'sb-topbar__brand',
            href: '/',
            'aria-label': 'UCELL SBMS — на главную'
        }, [
            el('img', { src: '/static/logoUcell.png', alt: '' }),
            el('span', null, 'SBMS')
        ]);

        // Nav links
        const nav = el('nav', { class: 'sb-topbar__nav', 'aria-label': 'Главная навигация' });
        LINKS.forEach(link => {
            const a = el('a', {
                class: 'sb-navlink' + (link.id === activeId ? ' is-active' : ''),
                href: link.href,
                'aria-current': link.id === activeId ? 'page' : 'false'
            }, link.label);
            nav.appendChild(a);
        });

        // Command palette button (⌘K)
        const isMac = /Mac|iPhone|iPad/i.test(navigator.platform);
        const cmdkBtn = el('button', {
            class: 'sb-cmdk-btn',
            type: 'button',
            'aria-label': 'Открыть командное меню',
            title: 'Командное меню (' + (isMac ? '⌘K' : 'Ctrl+K') + ')',
            onClick: () => { if (global.SbmsCmdK) global.SbmsCmdK.open(); }
        });
        cmdkBtn.innerHTML =
            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>' +
            '<span class="sb-cmdk-btn__kbd">' + (isMac ? '⌘K' : 'Ctrl+K') + '</span>';

        // Recent dropdown button (история MSISDN)
        const recentBtn = mountRecentDropdown();

        // Auth badge slot
        const authSlot = el('span', { id: 'sbmsAuthBadgeSlot' });

        // Theme toggle
        const themeBtn = el('button', {
            class: 'sb-theme-toggle',
            type: 'button',
            'aria-label': 'Переключить тему',
            title: 'Тема (light / dark)',
            onClick: () => {
                const t = toggleTheme();
                themeBtn.innerHTML = t === 'dark' ? sun() : moon();
                if (typeof opts.onTheme === 'function') opts.onTheme(t);
            }
        });
        themeBtn.innerHTML = getTheme() === 'dark' ? sun() : moon();

        // Actions
        const actions = el('div', { class: 'sb-topbar__actions' }, [cmdkBtn, recentBtn, authSlot, themeBtn]);

        // Top bar
        const bar = el('header', { class: 'sb-topbar', role: 'banner' }, [brand, nav, actions]);

        host.innerHTML = '';
        host.appendChild(bar);

        // Подмонтировать auth badge, если SbmsAuth есть
        if (global.SbmsAuth && typeof global.SbmsAuth.mountBadge === 'function') {
            global.SbmsAuth.mountBadge(authSlot);
        }

        // Подгрузить toast / recent / cmdk (без блокировки)
        ensureCompanions();

        return {
            element: bar,
            setActive(id) {
                bar.querySelectorAll('.sb-navlink').forEach((a, i) => {
                    const isActive = LINKS[i] && LINKS[i].id === id;
                    a.classList.toggle('is-active', isActive);
                    a.setAttribute('aria-current', isActive ? 'page' : 'false');
                });
            }
        };
    }

    function sun() {
        return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>';
    }
    function moon() {
        return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';
    }

    global.SbmsNav = {
        LINKS, mount, getTheme, applyTheme, toggleTheme
    };
})(window);
