/**
 * SbmsToast — глобальные неблокирующие уведомления.
 *
 *   SbmsToast.success('Тариф изменён');
 *   SbmsToast.error('Ошибка авторизации', { duration: 6000 });
 *   SbmsToast.warn('Сессия истекает через 2 мин');
 *   SbmsToast.info('Запрос отправлен', { action: { label: 'Отменить', onClick: cancel }});
 *   const id = SbmsToast.show('…', { variant: 'success', sticky: true });
 *   SbmsToast.dismiss(id);
 *
 * Стилизация — sb-toast / sb-toast-host из sbms-ui.css.
 */
(function (global) {
    'use strict';

    const HOST_ID = 'sbms-toast-host';
    let _seq = 0;

    function host() {
        let h = document.getElementById(HOST_ID);
        if (!h) {
            h = document.createElement('div');
            h.id = HOST_ID;
            h.className = 'sb-toast-host';
            h.setAttribute('role', 'region');
            h.setAttribute('aria-label', 'Уведомления');
            h.setAttribute('aria-live', 'polite');
            document.body.appendChild(h);
        }
        return h;
    }

    function icon(variant) {
        const M = {
            success: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
            warn:    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/></svg>',
            danger:  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/></svg>',
            info:    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>',
        };
        return M[variant] || '';
    }

    function show(message, opts) {
        opts = opts || {};
        const variant = opts.variant || 'info';     // success | warn | danger | info
        const duration = opts.sticky ? 0 : (opts.duration || 4000);
        const id = ++_seq;

        const el = document.createElement('div');
        el.className = 'sb-toast sb-toast--' + variant;
        el.setAttribute('role', 'status');
        el.dataset.toastId = String(id);
        el.style.cursor = 'pointer';

        const ico = document.createElement('span');
        ico.style.cssText = 'flex:0 0 auto;display:inline-flex;align-items:center;justify-content:center;color:var(--' +
            (variant === 'danger' ? 'danger-text' :
             variant === 'warn'   ? 'warning-text' :
             variant === 'success'? 'success-text' : 'info-text') + ');';
        ico.innerHTML = icon(variant);

        const msg = document.createElement('span');
        msg.style.cssText = 'flex:1 1 auto;line-height:1.4;';
        msg.textContent = message;

        el.appendChild(ico);
        el.appendChild(msg);

        if (opts.action && opts.action.label && typeof opts.action.onClick === 'function') {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.textContent = opts.action.label;
            btn.style.cssText = 'flex:0 0 auto;margin-left:auto;background:transparent;border:none;color:var(--brand);font-weight:600;font-size:13px;cursor:pointer;padding:0 4px;';
            btn.addEventListener('click', (ev) => {
                ev.stopPropagation();
                try { opts.action.onClick(); } finally { dismiss(id); }
            });
            el.appendChild(btn);
        }

        el.addEventListener('click', () => dismiss(id));

        host().appendChild(el);

        if (duration > 0) {
            setTimeout(() => dismiss(id), duration);
        }
        return id;
    }

    function dismiss(id) {
        const el = host().querySelector(`[data-toast-id="${id}"]`);
        if (!el) return;
        el.style.transition = 'opacity 180ms ease, transform 180ms ease';
        el.style.opacity = '0';
        el.style.transform = 'translateY(8px)';
        setTimeout(() => el.remove(), 200);
    }

    function dismissAll() {
        host().querySelectorAll('.sb-toast').forEach(e => {
            const id = Number(e.dataset.toastId);
            if (id) dismiss(id);
        });
    }

    global.SbmsToast = {
        show,
        dismiss,
        dismissAll,
        success: (m, o) => show(m, Object.assign({ variant: 'success' }, o || {})),
        error:   (m, o) => show(m, Object.assign({ variant: 'danger' },  o || {})),
        warn:    (m, o) => show(m, Object.assign({ variant: 'warn' },    o || {})),
        info:    (m, o) => show(m, Object.assign({ variant: 'info' },    o || {})),
    };
})(window);
