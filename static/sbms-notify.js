/**
 * SbmsNotify — браузерные уведомления при завершении длинных прогонов.
 *
 *   await SbmsNotify.requestPermission();
 *   SbmsNotify.show('Прогон завершён', { body: '120/120 строк, 5 fail', tag: 'matrix' });
 *   const id = SbmsNotify.startTracking({ key: 'matrix-run', minDurationMs: 5000 });
 *   SbmsNotify.finishTracking(id, { title: 'Матрица', body: '120/120, 5 fail' });
 *
 * Поведение:
 *  - permission запрашивается один раз и кешируется
 *  - уведомления показываются ТОЛЬКО когда вкладка не в фокусе (document.hidden)
 *  - если permission denied — фолбэк на SbmsToast (если доступен)
 *  - startTracking/finishTracking автоматически решает показывать ли,
 *    основываясь на minDurationMs (короткие операции — без notify)
 *  - клик по уведомлению фокусирует вкладку
 */
(function (global) {
    'use strict';

    const LS_KEY = 'sbms-notify-permission';

    function isSupported() {
        return typeof window !== 'undefined' && 'Notification' in window;
    }

    function permissionStatus() {
        if (!isSupported()) return 'unsupported';
        return Notification.permission; // 'granted' | 'denied' | 'default'
    }

    async function requestPermission() {
        if (!isSupported()) return 'unsupported';
        const cur = Notification.permission;
        if (cur === 'granted' || cur === 'denied') {
            try { localStorage.setItem(LS_KEY, cur); } catch (_) {}
            return cur;
        }
        try {
            const result = await Notification.requestPermission();
            try { localStorage.setItem(LS_KEY, result); } catch (_) {}
            return result;
        } catch (e) {
            return 'denied';
        }
    }

    function show(title, opts) {
        opts = opts || {};
        // Если вкладка в фокусе и forceWhenVisible не задан — не показываем системное уведомление,
        // полагаемся на toast (он и так появится).
        const visible = !document.hidden;
        if (visible && !opts.forceWhenVisible) {
            return null;
        }

        if (!isSupported() || Notification.permission !== 'granted') {
            // Фолбэк
            if (global.SbmsToast) {
                const variant = opts.variant || (opts.failed > 0 ? 'warn' : 'success');
                global.SbmsToast[variant](title + (opts.body ? ' · ' + opts.body : ''));
            }
            return null;
        }

        try {
            const n = new Notification(title, {
                body: opts.body || '',
                icon: opts.icon || '/static/logoUcell.png',
                tag:  opts.tag  || 'sbms',
                renotify: opts.renotify !== false,
                silent: opts.silent === true,
            });
            n.onclick = () => {
                window.focus();
                if (typeof opts.onClick === 'function') opts.onClick();
                n.close();
            };
            // Auto-close через 8 сек
            if (opts.duration !== 0) {
                setTimeout(() => { try { n.close(); } catch (_) {} }, opts.duration || 8000);
            }
            return n;
        } catch (e) {
            console.warn('[SbmsNotify] show failed:', e);
            return null;
        }
    }

    /* ===== Tracking-helpers для длинных прогонов ===== */
    const trackers = new Map();
    let _seq = 0;

    function startTracking(opts) {
        opts = opts || {};
        const id = ++_seq;
        trackers.set(id, {
            startedAt: Date.now(),
            key: opts.key || 'run',
            minDurationMs: opts.minDurationMs != null ? opts.minDurationMs : 5000,
        });
        return id;
    }

    function finishTracking(id, opts) {
        const t = trackers.get(id);
        if (!t) return;
        trackers.delete(id);
        const dur = Date.now() - t.startedAt;
        opts = opts || {};
        if (dur < t.minDurationMs) {
            return; // короткая операция — не уведомляем (toast уже сработал)
        }
        const title = opts.title || 'Прогон завершён';
        const body  = opts.body  || (Math.round(dur / 1000) + ' сек');
        show(title, {
            body,
            tag: t.key,
            variant: opts.variant,
            failed: opts.failed,
            forceWhenVisible: false, // если в фокусе — не дублируем toast
        });
    }

    function cancelTracking(id) {
        trackers.delete(id);
    }

    /* ===== Helper: спросить разрешение, если ещё не спрашивали ===== */
    async function ensurePermission() {
        if (!isSupported()) return 'unsupported';
        if (Notification.permission === 'default') {
            return await requestPermission();
        }
        return Notification.permission;
    }

    global.SbmsNotify = {
        isSupported,
        permissionStatus,
        requestPermission,
        ensurePermission,
        show,
        startTracking,
        finishTracking,
        cancelTracking,
    };
})(window);
