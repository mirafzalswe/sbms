#!/usr/bin/env python3
"""
Регрессионные тесты безопасности debug-snippet ответов PSIX/SELFCARE.

Цель: гарантировать, что чужая сессия (SESSION_ID, LOGIN, ACCESS_LEVEL_ID)
никогда не попадает в attempts[].snippet, отдаваемый /api/calls в UI.

Запуск:
    python3 test_redaction.py
"""
import sys

from sbms_client import _redact_snippet, _psix_safe_snippet


# Эталоны секретов из реального лога (анонимизировано — но именно эти
# значения в проде утекали в debug-бокс).
SECRETS = (
    "AAAI8wEwsO2vTupgFcTzbL4Hok_TEMtG",
    "AAAI8wEwsO2T6bNsp.fqK0HNiw1TRzu6",
    "AAAI8wEwsO2bu5cOiNjmNCB.6_g3tiXR",
    "AAAI8wEwsO2kVDLvns_1fr9MMBySDQzu",
    "MIRAFZAL.BAHODIROV",
    "100059666",
)


# Реальные ответы PSIX, как они приходят от SBMS.
REAL_RESPONSES = [
    # 1) Голая SELFCARE-обёртка (warm-up STRAFF_INIT_FORM_PARAMS_R)
    """<?xml version='1.0' encoding='utf-8'?>
<SELFCARE>
<HOSTNAME>has-sbmsx1</HOSTNAME>
<CHANNEL>WWW</CHANNEL>
<SESSION_ID>AAAI8wEwsO2vTupgFcTzbL4Hok_TEMtG</SESSION_ID>
<LOGIN>MIRAFZAL.BAHODIROV</LOGIN>
<ACCESS_LEVEL_ID>100059666</ACCESS_LEVEL_ID>
<LANG_ID>1</LANG_ID>
</SELFCARE>""",

    # 2) STRAFF_CALLS_R с HAS_GET_USER_ATTRIBUTES (виден в свежем баге)
    """<?xml version='1.0' encoding='utf-8'?>
<SELFCARE>
<HOSTNAME>has-sbmsx1</HOSTNAME>
<CHANNEL>WWW</CHANNEL>
<SESSION_ID>AAAI8wEwsO2T6bNsp.fqK0HNiw1TRzu6</SESSION_ID>
<LOGIN>MIRAFZAL.BAHODIROV</LOGIN>
<ACCESS_LEVEL_ID>100059666</ACCESS_LEVEL_ID>
<LANG_ID>1</LANG_ID>
<HAS_GET_USER_ATTRIBUTES>
<CMS_ACRS_USER_ID>123</CMS_ACRS_USER_ID>
</HAS_GET_USER_ATTRIBUTES>
<DATA><ROW><CALL_DATE>01.01.2026 10:00:00</CALL_DATE><CHARGES>120</CHARGES></ROW></DATA>
</SELFCARE>""",

    # 3) Ответ с реальными ROW (полезные данные есть)
    """<SELFCARE>
<SESSION_ID>AAAI8wEwsO2bu5cOiNjmNCB.6_g3tiXR</SESSION_ID>
<LOGIN>MIRAFZAL.BAHODIROV</LOGIN>
<DATA>
  <ROW><CALL_DATE>05.05.2026 09:12:33</CALL_DATE><CHARGES>0</CHARGES><DURATION>30</DURATION></ROW>
  <ROW><CALL_DATE>05.05.2026 09:14:01</CALL_DATE><CHARGES>50</CHARGES><DURATION>61</DURATION></ROW>
</DATA>
</SELFCARE>""",

    # 4) Обрезанный посреди значения LOGIN (snippet[:200] от реального ответа)
    "<SELFCARE><SESSION_ID>AAAI8wEwsO2kVDLvns_1fr9MMBySDQzu</SESSION_ID><LOGIN>MIRAFZAL.BAHODIROV</LOG",

    # 5) Обрезанный в открывающем теге
    "<SELFCARE><SESSION_ID>AAAI8wEwsO2kVDLvns_1fr9MMBySDQzu</SESSION_ID><LOG",

    # 6) Битый XML с секретом в середине
    "garbage<SESSION_ID>AAAI8wEwsO2T6bNsp.fqK0HNiw1TRzu6</SESSION_ID>more garbage",

    # 7) lowercase теги
    "<selfcare><session_id>AAAI8wEwsO2vTupgFcTzbL4Hok_TEMtG</session_id></selfcare>",

    # 8) Ответ с ошибкой
    """<SELFCARE>
<SESSION_ID>AAAI8wEwsO2bu5cOiNjmNCB.6_g3tiXR</SESSION_ID>
<LOGIN>MIRAFZAL.BAHODIROV</LOGIN>
<STATUS>ERROR</STATUS>
<ERR_CODE>1234</ERR_CODE>
<ERR_TEXT>Invalid subscriber id</ERR_TEXT>
</SELFCARE>""",

    # 9) Гипотетический будущий тег (TOKEN, PASSWORD) — должен попасть в deny-list
    "<SELFCARE><TOKEN>secret_xyz_42</TOKEN><PASSWORD>hunter2</PASSWORD></SELFCARE>",
]


def _has_secret(text):
    if text is None:
        return False
    return any(s in text for s in SECRETS + ("secret_xyz_42", "hunter2"))


def main():
    failures = 0
    print("=" * 70)
    print(" REDACTION REGRESSION TESTS")
    print("=" * 70)

    # --- 1) _redact_snippet (low-level deny-list) ---
    print("\n[1/4] _redact_snippet — deny-list по 9 эталонам:")
    for i, src in enumerate(REAL_RESPONSES, 1):
        out = _redact_snippet(src)
        if _has_secret(out):
            print(f"  ❌ #{i}: leak in _redact_snippet → {out!r}")
            failures += 1
        else:
            print(f"  ✓ #{i}")

    # --- 2) _psix_safe_snippet (allow-list через XML) ---
    print("\n[2/4] _psix_safe_snippet — allow-list, обрезка до 400:")
    for i, src in enumerate(REAL_RESPONSES, 1):
        out = _psix_safe_snippet(src, max_len=400)
        if _has_secret(out):
            print(f"  ❌ #{i}: leak → {out!r}")
            failures += 1
        else:
            print(f"  ✓ #{i} → {(out or '')[:80]}…")

    # --- 3) _psix_safe_snippet с короткой обрезкой (200) ---
    print("\n[3/4] _psix_safe_snippet — обрезка до 200:")
    for i, src in enumerate(REAL_RESPONSES, 1):
        out = _psix_safe_snippet(src, max_len=200)
        if _has_secret(out):
            print(f"  ❌ #{i}: leak → {out!r}")
            failures += 1
        elif out and len(out) > 200:
            print(f"  ❌ #{i}: длиннее лимита ({len(out)} > 200)")
            failures += 1
        else:
            print(f"  ✓ #{i} (len={len(out or '')})")

    # --- 4) Полезные данные (ROW, ERR_*) сохраняются ---
    print("\n[4/4] Полезные узлы остаются видимы:")
    case_with_rows = REAL_RESPONSES[2]
    out = _psix_safe_snippet(case_with_rows, max_len=400)
    if "CALL_DATE" not in (out or ""):
        print(f"  ❌ ROW не сохранён → {out!r}")
        failures += 1
    else:
        print(f"  ✓ ROW сохранён")

    case_with_err = REAL_RESPONSES[7]
    out = _psix_safe_snippet(case_with_err, max_len=400)
    if "ERR_TEXT" not in (out or "") or "Invalid subscriber id" not in (out or ""):
        print(f"  ❌ ERR_TEXT не сохранён → {out!r}")
        failures += 1
    else:
        print(f"  ✓ ERR_TEXT сохранён")

    # --- Итог ---
    print("\n" + "=" * 70)
    if failures:
        print(f"  ❌ FAIL: {failures} проблем(ы)")
        sys.exit(1)
    print("  ✅ ALL OK — секреты не утекают, диагностика остаётся видимой")
    print("=" * 70)


if __name__ == "__main__":
    main()
