#!/usr/bin/env python3
"""
ЛОКАЛЬНЫЙ debug-скрипт для STRAFF_CALLS_R.

Назначение: дёргает PSIX напрямую тем же способом, что и SBMSClient.get_calls,
и печатает ПОЛНЫЙ сырой ответ SBMS в консоль — чтобы понять, почему
ROWs=0 (нет данных у абонента / неверные параметры / структура другая).

ВАЖНО:
  • Запускать только локально. Полный ответ содержит SESSION_ID и LOGIN —
    в UI / логи / сеть он попадать не должен.
  • Ничего никуда не отправляет, только print().

Использование:
    python3 debug_calls.py <MSISDN>

Берёт логин/пароль из .env (SBMS_LOGIN / SBMS_PASSWORD), MSISDN — из аргумента
или TEST_MSISDN.
"""
import os
import sys
from datetime import datetime, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from sbms_client import SBMSClient


def main():
    base_url = os.getenv("SBMS_BASE_URL") or "https://sbms.ucell"
    login    = os.getenv("SBMS_LOGIN")
    password = os.getenv("SBMS_PASSWORD")
    msisdn   = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("TEST_MSISDN") or "").strip()

    if not (login and password):
        print("ERROR: SBMS_LOGIN/SBMS_PASSWORD не заданы в .env", file=sys.stderr)
        sys.exit(1)
    if not msisdn:
        print("ERROR: укажите MSISDN: python3 debug_calls.py 998500173054", file=sys.stderr)
        sys.exit(1)

    client = SBMSClient(base_url, timeout=30)
    print(f"[1] Auth as {login} on {base_url} …", flush=True)
    client.authenticate(login, password)
    print(f"    SESSION_ID = {client.token!r}")
    print(f"[2] search_customer({msisdn}) …", flush=True)
    sd = client.search_customer(msisdn)
    sr = (sd or {}).get("searchResults") or [{}]
    sr0 = sr[0] if sr else {}
    sid = sr0.get("subscriberId")
    cid = sr0.get("customerId")
    print(f"    subscriberId = {sid}")
    print(f"    customerId   = {cid}")

    sess = requests.Session()
    sess.verify = False
    sess.auth = (login, password)
    headers = {
        "Origin": base_url,
        "Referer": f"{base_url}/",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "*/*",
        "Accept-Language": "ru,en;q=0.9",
    }

    end_dt = datetime.now()
    for days in (60, 180, 540):
        sd_str = (end_dt - timedelta(days=days)).strftime("%d.%m.%Y %H:%M:%S")
        ed_str = end_dt.strftime("%d.%m.%Y %H:%M:%S")
        body = {
            "I_BEGIN_DATE": sd_str,
            "I_END_DATE":   ed_str,
            "I_CLT_ID":     cid or "",
            "I_SUBS_ID":    sid or "",
            "I_STND_ID":    1,
            "I_COST_PLUS":  0,
            "I_CALL_OUT":   0,
            "I_TIME_ZONE":  0,
            "R_MSISDN":     0,
            "P_CHARGE_MODE": 0,
            "I_ORDER":      -5,
            "I_START_ROW":  1,
            "I_END_ROW":    100,
            "SESSION_ID":   client.token,
            "INTM":         "BIS",
            "REM_IOPER_ID": 1,
        }
        print(f"\n[3.{days}] POST /PSIX/grid/STRAFF_CALLS_R period={days}d …", flush=True)
        r = sess.post(
            f"{base_url}/PSIX/grid/STRAFF_CALLS_R",
            data=body,
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        print(f"    HTTP {r.status_code}, len={len(r.text)}")
        print("    --- BEGIN RAW RESPONSE ---")
        print(r.text)
        print("    --- END RAW RESPONSE ---")
        # Если есть данные — выходим, дальше уже понятно
        if "<ROW>" in r.text or "<ROW " in r.text:
            print("    ✓ В ответе есть <ROW> — данные присутствуют, проверяйте парсер.")
            return

    # Fallback: UCL_OAPI_GET_CHARGE
    print(f"\n[4] GET /PSIX/scli/UCL_OAPI_GET_CHARGE fallback (period=540d) …", flush=True)
    r = sess.get(
        f"{base_url}/PSIX/scli/UCL_OAPI_GET_CHARGE",
        params={
            "SESSION_ID": client.token,
            "SUBSCRIBER_MSISDN": msisdn,
            "P_DATE_FROM": (end_dt - timedelta(days=540)).strftime("%d.%m.%Y %H:%M:%S"),
            "P_DATE_TILL": ed_str,
            "P_SUBS_ID":   sid or "",
        },
        headers=headers,
        timeout=30,
    )
    print(f"    HTTP {r.status_code}, len={len(r.text)}")
    print("    --- BEGIN RAW RESPONSE ---")
    print(r.text)
    print("    --- END RAW RESPONSE ---")


if __name__ == "__main__":
    main()
