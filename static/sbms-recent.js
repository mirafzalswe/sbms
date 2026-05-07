/**
 * SbmsRecent — глобальный список последних MSISDN, общий для всех страниц.
 *
 *   SbmsRecent.add('998500173054', { label: 'Иван', page: 'tariff' });
 *   SbmsRecent.list()  // [{msisdn, label, page, ts}, ...] — свежие первыми
 *   SbmsRecent.remove('998500173054')
 *   SbmsRecent.clear()
 *
 * Хранение: localStorage 'sbms-recent-msisdn' (JSON array, max 12 entries).
 * Подписка: SbmsRecent.on(fn) — вызывается при изменениях, fn(list).
 */
(function (global) {
    'use strict';

    const KEY = 'sbms-recent-msisdn';
    const KEY_PINNED = 'sbms-pinned-msisdn';
    const MAX = 12;
    const listeners = new Set();

    function load() {
        try {
            const raw = localStorage.getItem(KEY);
            if (!raw) return [];
            const arr = JSON.parse(raw);
            return Array.isArray(arr) ? arr : [];
        } catch (_) { return []; }
    }

    function save(arr) {
        try { localStorage.setItem(KEY, JSON.stringify(arr)); } catch (_) {}
        emit(combinedList());
    }

    function loadPinned() {
        try {
            const raw = localStorage.getItem(KEY_PINNED);
            if (!raw) return [];
            const arr = JSON.parse(raw);
            return Array.isArray(arr) ? arr : [];
        } catch (_) { return []; }
    }

    function savePinned(arr) {
        try { localStorage.setItem(KEY_PINNED, JSON.stringify(arr)); } catch (_) {}
        emit(combinedList());
    }

    function emit(arr) {
        listeners.forEach(fn => { try { fn(arr); } catch (_) {} });
    }

    function normalize(msisdn) {
        return String(msisdn || '').replace(/[^0-9]/g, '');
    }

    // Объединённый список: pinned сверху, recent снизу.
    // У pinned-записей флаг pinned=true.
    function combinedList() {
        const pinned = loadPinned();
        const recent = load();
        const pinnedSet = new Set(pinned.map(x => x.msisdn));
        const out = pinned.map(x => Object.assign({}, x, { pinned: true }));
        recent.forEach(r => {
            if (!pinnedSet.has(r.msisdn)) out.push(Object.assign({}, r, { pinned: false }));
        });
        return out;
    }

    function add(msisdn, opts) {
        msisdn = normalize(msisdn);
        if (!msisdn) return;
        opts = opts || {};
        const list = load();
        const filtered = list.filter(x => x.msisdn !== msisdn);
        filtered.unshift({
            msisdn,
            label: opts.label || '',
            page:  opts.page  || '',
            ts:    Date.now(),
        });
        save(filtered.slice(0, MAX));
    }

    function remove(msisdn) {
        msisdn = normalize(msisdn);
        if (!msisdn) return;
        save(load().filter(x => x.msisdn !== msisdn));
    }

    function clear() { save([]); }

    // Список = combined (pinned сверху, recent снизу).
    // Старые потребители получают полный набор через .list();
    // если нужно сырое recent — есть .listRecent(), для pinned — .listPinned().
    function list() { return combinedList(); }
    function listRecent() { return load(); }
    function listPinned() { return loadPinned(); }

    function on(fn) { listeners.add(fn); return () => listeners.delete(fn); }

    // ===== Pinned API =====
    function isPinned(msisdn) {
        msisdn = normalize(msisdn);
        if (!msisdn) return false;
        return loadPinned().some(x => x.msisdn === msisdn);
    }

    function pin(msisdn, opts) {
        msisdn = normalize(msisdn);
        if (!msisdn) return;
        opts = opts || {};
        const pinned = loadPinned().filter(x => x.msisdn !== msisdn);
        // Возьмём label/page либо из opts, либо из существующей recent-записи
        const recent = load();
        const fromRecent = recent.find(r => r.msisdn === msisdn) || {};
        pinned.unshift({
            msisdn,
            label: opts.label != null ? opts.label : (fromRecent.label || ''),
            page:  opts.page  != null ? opts.page  : (fromRecent.page  || ''),
            ts:    Date.now(),
        });
        savePinned(pinned);
    }

    function unpin(msisdn) {
        msisdn = normalize(msisdn);
        if (!msisdn) return;
        savePinned(loadPinned().filter(x => x.msisdn !== msisdn));
    }

    function togglePin(msisdn, opts) {
        if (isPinned(msisdn)) unpin(msisdn);
        else pin(msisdn, opts);
    }

    // Cross-tab sync: одна вкладка добавила MSISDN — другие узнают.
    window.addEventListener('storage', (ev) => {
        if (ev.key === KEY || ev.key === KEY_PINNED) emit(combinedList());
    });

    global.SbmsRecent = {
        add, remove, clear, list, listRecent, listPinned, on,
        pin, unpin, togglePin, isPinned,
    };
})(window);
