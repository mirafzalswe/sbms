#!/usr/bin/env python3
"""
UCELL SBMS API - Proxy Server
==============================
Решает проблему CORS при работе через браузер.
Dashboard доступен по адресу http://localhost:5000

Запуск: python server.py
"""

from flask import Flask, request, Response, send_file, jsonify
import requests as http_client
import urllib3
import os
import json
import time
import traceback
from collections import defaultdict
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None
from sbms_client import SBMSClient
from sbms_checks import save_report, list_reports, get_report, extract_recurring_charge, extract_product_id_from_charges, extract_volumes, extract_volumes_by_product_id
try:
    from discount_mapper import get_discount_description
except ImportError:
    def get_discount_description(discount_plan_id):
        return ""
from sbms_runner import TestRunner

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

if load_dotenv:
    load_dotenv()

app = Flask(__name__)

BASE_URL = os.getenv("SBMS_BASE_URL", "https://sbms.ucell")
TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))

# ============================================================
# TOKEN CACHE — авторизация один раз, переиспользование токена
# ============================================================
_auth_cache = {}  # login -> {"token": str, "ts": float}
TOKEN_TTL = 25 * 60  # 25 минут (сессия SBMS обычно 30 мин)


class AuthRequired(Exception):
    """Поднимается, когда у запроса нет ни валидного authToken, ни credentials."""
    pass


def _get_client(data=None):
    """Получить авторизованный SBMSClient. Приоритет:
    1. authToken из запроса — используем напрямую
    2. login+password из запроса — свежая авторизация + кеширование
    3. login из кеша — если токен ещё свежий
    4. .env credentials — только если явно разрешено (CLI/автотесты), UI всегда
       должен прислать свои credentials либо authToken.
    """
    data = data or {}
    token = data.get("authToken")
    if token:
        client = SBMSClient(BASE_URL, TIMEOUT)
        client.token = token
        return client

    login = data.get("login")
    password = data.get("password")
    now = time.time()

    if login and password:
        client = SBMSClient(BASE_URL, TIMEOUT)
        client.authenticate(login, password)
        _auth_cache[login] = {"token": client.token, "ts": now}
        return client

    if login and login in _auth_cache and now - _auth_cache[login]["ts"] < TOKEN_TTL:
        client = SBMSClient(BASE_URL, TIMEOUT)
        client.token = _auth_cache[login]["token"]
        return client

    # Фолбэк на .env допустим только для CLI-сценариев; UI всегда прислал бы credentials.
    env_login = os.getenv("SBMS_LOGIN")
    env_pass = os.getenv("SBMS_PASSWORD")
    if env_login and env_pass and os.getenv("SBMS_ALLOW_ENV_AUTH") == "1":
        if env_login in _auth_cache and now - _auth_cache[env_login]["ts"] < TOKEN_TTL:
            client = SBMSClient(BASE_URL, TIMEOUT)
            client.token = _auth_cache[env_login]["token"]
            return client
        client = SBMSClient(BASE_URL, TIMEOUT)
        client.authenticate(env_login, env_pass)
        _auth_cache[env_login] = {"token": client.token, "ts": now}
        return client

    raise AuthRequired("Требуется авторизация: пришлите authToken или login+password")


@app.errorhandler(AuthRequired)
def _auth_required(e):
    return jsonify({"error": str(e), "code": "AUTH_REQUIRED"}), 401


def _normalize_lc_history(data):
    """Привести ответ /subslcstates/history/search к плоскому списку для UI."""
    if not isinstance(data, dict):
        return []
    raw_items = data.get("items") or []
    norm = []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        st = it.get("lcState") if isinstance(it.get("lcState"), dict) else {}
        ct = it.get("conversionType") if isinstance(it.get("conversionType"), dict) else {}
        audit = it.get("audit") if isinstance(it.get("audit"), dict) else {}
        norm.append({
            "id": it.get("id"),
            "state": st.get("def") or st.get("name"),
            "stateId": st.get("id"),
            "conversion": ct.get("def") if isinstance(ct, dict) else None,
            "stateDate": it.get("lcStateDate") or it.get("startDate"),
            "startDate": it.get("startDate"),
            "endDate": it.get("endDate"),
            "balanceEndDate": it.get("balanceEndDate"),
            "naviUser": audit.get("naviUser") if isinstance(audit, dict) else None,
            "naviDate": audit.get("naviDate") if isinstance(audit, dict) else None,
        })
    return norm


def _is_open_lc_endpoint(end_date):
    """endDate ~ '2999-...' = открытый интервал, текущее состояние."""
    if not end_date:
        return True
    return str(end_date).startswith("2999")


def _pick_current_lc(norm_history):
    """Найти текущее состояние: открытый endDate или самое позднее по stateDate."""
    if not norm_history:
        return None
    for h in norm_history:
        if _is_open_lc_endpoint(h.get("endDate")):
            return h
    return max(norm_history, key=lambda x: x.get("stateDate") or "")


@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, PATCH, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response


@app.route('/')
def index():
    resp = send_file('dashboard.html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


@app.route('/proxy/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'])
def proxy(path):
    if request.method == 'OPTIONS':
        return Response('', status=200)

    url = f"{BASE_URL}/{path}"
    params = dict(request.args)
    data = request.get_data()

    headers = {}
    if request.content_type:
        headers['Content-Type'] = request.content_type

    try:
        resp = http_client.request(
            method=request.method,
            url=url,
            params=params,
            data=data,
            headers=headers,
            verify=False,
            timeout=TIMEOUT
        )

        response = Response(
            resp.content,
            status=resp.status_code,
        )

        if 'Content-Type' in resp.headers:
            response.headers['Content-Type'] = resp.headers['Content-Type']

        return response

    except http_client.exceptions.ConnectionError:
        return Response(
            json.dumps({"error": "Cannot connect to SBMS server", "url": url}),
            status=502,
            content_type='application/json'
        )
    except http_client.exceptions.Timeout:
        return Response(
            json.dumps({"error": "Request timeout", "url": url}),
            status=504,
            content_type='application/json'
        )
    except Exception as e:
        return Response(
            json.dumps({"error": str(e), "url": url}),
            status=500,
            content_type='application/json'
        )


@app.route('/tester')
def tester():
    resp = send_file('tester.html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


@app.route('/tariff-test')
def tariff_test():
    resp = send_file('tariff_test.html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


@app.route('/matrix-test')
def matrix_test_page():
    resp = send_file('matrix_test.html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


@app.route('/tme')
def tme_page():
    resp = send_file('tme.html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


# ============================================================
# MATRIX TEST — матрица переходов между тарифами
# ============================================================
import matrix_parser as _matrix_parser
import matrix_runner as _matrix_runner


@app.route('/api/matrix/parse', methods=['POST'])
def api_matrix_parse():
    """Принимает файл матрицы (PDF/DOCX/XLSX/CSV/JSON) и возвращает каноничный JSON."""
    if 'file' not in request.files:
        return jsonify({"error": "Файл не приложен (поле 'file' пустое)"}), 400
    f = request.files['file']
    name = f.filename or ""
    content = f.read()
    if not content:
        return jsonify({"error": "Файл пустой"}), 400
    try:
        spec = _matrix_parser.parse(name, content)
    except Exception as e:
        return jsonify({"error": str(e), "filename": name}), 400
    spec["_stats"] = _matrix_parser.stats(spec)
    spec["_filename"] = name
    return jsonify(spec)


@app.route('/api/matrix/run', methods=['POST'])
def api_matrix_run():
    """Запускает прогон матрицы. Возвращает report (синхронно).

    Body JSON:
    {
      "msisdn": "...",
      "spec": { columns, rows },
      "admin": {"login": "...", "password": "..."},
      "viewer": {"login": "...", "password": "..."},
      "only_rows": [optional list of row.id],
      "wait_after_change": 5,
      "order_timeout": 60
    }
    """
    data = request.get_json(silent=True) or {}
    msisdn = (data.get("msisdn") or "").strip()
    spec = data.get("spec")
    admin_creds = data.get("admin") or {}
    viewer_creds = data.get("viewer") or {}
    if not msisdn or not spec or not admin_creds.get("login") or not viewer_creds.get("login"):
        return jsonify({"error": "Нужны msisdn, spec, admin{login,password}, viewer{login,password}"}), 400

    try:
        admin_client = _get_client(admin_creds)
    except Exception as e:
        return jsonify({"error": f"Admin auth failed: {e}", "scope": "admin"}), 401
    try:
        viewer_client = _get_client(viewer_creds)
    except Exception as e:
        return jsonify({"error": f"Viewer auth failed: {e}", "scope": "viewer"}), 401

    only_rows = data.get("only_rows") or None
    wait_after = float(data.get("wait_after_change", 5))
    order_timeout = int(data.get("order_timeout", 60))
    resume_from = (data.get("resume_from") or "").strip() or None

    try:
        report = _matrix_runner.run_matrix(
            msisdn=msisdn,
            spec=spec,
            admin_client=admin_client,
            viewer_client=viewer_client,
            only_rows=only_rows,
            wait_after_change=wait_after,
            order_timeout=order_timeout,
            resume_from=resume_from,
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    return jsonify(report)


@app.route('/api/matrix/run-stream', methods=['POST'])
def api_matrix_run_stream():
    """SSE-вариант: транслирует прогресс прогона построчно.
    Тело такое же, как у /api/matrix/run."""
    data = request.get_json(silent=True) or {}
    msisdn = (data.get("msisdn") or "").strip()
    spec = data.get("spec")
    admin_creds = data.get("admin") or {}
    viewer_creds = data.get("viewer") or {}
    if not msisdn or not spec or not admin_creds.get("login") or not viewer_creds.get("login"):
        return jsonify({"error": "Нужны msisdn, spec, admin{login,password}, viewer{login,password}"}), 400
    try:
        admin_client = _get_client(admin_creds)
        viewer_client = _get_client(viewer_creds)
    except Exception as e:
        return jsonify({"error": f"Auth failed: {e}"}), 401
    only_rows = data.get("only_rows") or None
    wait_after = float(data.get("wait_after_change", 5))
    order_timeout = int(data.get("order_timeout", 60))
    resume_from = (data.get("resume_from") or "").strip() or None

    import queue, threading
    q: "queue.Queue[dict]" = queue.Queue()
    abort_event = threading.Event()

    def cb(evt):
        q.put(evt)

    def worker():
        try:
            report = _matrix_runner.run_matrix(
                msisdn=msisdn, spec=spec,
                admin_client=admin_client, viewer_client=viewer_client,
                only_rows=only_rows, wait_after_change=wait_after,
                order_timeout=order_timeout, progress_cb=cb,
                resume_from=resume_from, abort_event=abort_event,
            )
            # Если abort сработал — runner сам уже отправил "aborted" + сохранил
            # отчёт; финальный "final" не нужен.
            if not abort_event.is_set():
                q.put({"event": "final", "report": report})
        except Exception as e:
            q.put({"event": "error", "error": str(e)})
        finally:
            q.put({"event": "_close"})

    threading.Thread(target=worker, daemon=True).start()

    def stream():
        try:
            while True:
                evt = q.get()
                if evt.get("event") == "_close":
                    break
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        finally:
            # Клиент закрыл соединение (Stop, обновил вкладку, упала сеть) —
            # сигналим воркеру корректно завершиться между строками.
            abort_event.set()

    return Response(stream(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.route('/api/matrix/history')
def api_matrix_history():
    return jsonify(_matrix_runner.list_matrix_reports())


@app.route('/api/matrix/history/<report_id>')
def api_matrix_history_detail(report_id):
    rep = _matrix_runner.get_matrix_report(report_id)
    if not rep:
        return jsonify({"error": "report not found"}), 404
    return jsonify(rep)


# ============================================================
# TME (Trouble Management Engine) — tme.billing.domain
# ============================================================
TME_BASE_URL = os.getenv("TME_BASE_URL", "https://tme.billing.domain")
_tme_auth_cache = {}  # username -> {"token": str, "ts": float, "raw": dict}


@app.route('/api/tme/auth', methods=['POST'])
def api_tme_auth():
    """Авторизация в TME. Проксирует POST на {TME_BASE_URL}/api/v1/authenticate."""
    try:
        data = request.get_json() or {}
        username = data.get("username")
        password = data.get("password")
        if not username or not password:
            return jsonify({"error": "username and password are required"}), 400

        url = f"{TME_BASE_URL}/api/v1/authenticate"
        resp = http_client.post(
            url,
            json={"username": username, "password": password},
            headers={"Content-Type": "application/json"},
            verify=False,
            timeout=TIMEOUT,
        )

        try:
            body = resp.json()
        except ValueError:
            body = {"raw": resp.text}

        if resp.status_code >= 400:
            return jsonify({
                "error": "Authentication failed",
                "status": resp.status_code,
                "body": body,
            }), resp.status_code

        token = None
        if isinstance(body, dict):
            for key in ("token", "accessToken", "access_token", "jwt", "id_token"):
                if body.get(key):
                    token = body[key]
                    break
            if not token and isinstance(body.get("data"), dict):
                for key in ("token", "accessToken", "access_token", "jwt"):
                    if body["data"].get(key):
                        token = body["data"][key]
                        break

        _tme_auth_cache[username] = {
            "token": token,
            "ts": time.time(),
            "raw": body,
        }

        return jsonify({
            "token": token,
            "status": resp.status_code,
            "body": body,
            "url": url,
        })
    except http_client.exceptions.ConnectionError as e:
        return jsonify({"error": "Cannot connect to TME server", "details": str(e), "url": TME_BASE_URL}), 502
    except http_client.exceptions.Timeout:
        return jsonify({"error": "TME request timeout", "url": TME_BASE_URL}), 504
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/tme/tariffs', methods=['POST'])
def api_tme_tariffs():
    """Список тарифов TME — запускает сценарий get_rtpl_for_change."""
    try:
        data = request.get_json() or {}
        token = data.get("tmeToken")
        url = f"{TME_BASE_URL}/api/v1/scenarios/get_rtpl_for_change/run"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = http_client.post(
            url,
            params={"page": 1, "page_size": 500},
            json={},
            headers=headers,
            verify=False,
            timeout=TIMEOUT,
        )
        try:
            body = resp.json()
        except ValueError:
            body = {"raw": resp.text}
        items = body.get("result", []) if isinstance(body, dict) else []
        return jsonify({"items": items, "status": resp.status_code, "url": resp.url})
    except http_client.exceptions.ConnectionError as e:
        return jsonify({"error": "Cannot connect to TME server", "details": str(e)}), 502
    except http_client.exceptions.Timeout:
        return jsonify({"error": "TME request timeout"}), 504
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/tme/scenario/run', methods=['POST'])
def api_tme_scenario_run():
    """Запуск произвольного TME-сценария.
    POST {TME_BASE_URL}/api/v1/scenarios/{scenario}/run?<query>  с JSON-телом."""
    try:
        data = request.get_json() or {}
        scenario = (data.get("scenario") or "").strip()
        if not scenario:
            return jsonify({"error": "scenario is required"}), 400

        token = data.get("tmeToken")
        body = data.get("body") if isinstance(data.get("body"), (dict, list)) else {}
        query = data.get("query") if isinstance(data.get("query"), dict) else {"page": 1, "page_size": 50}

        url = f"{TME_BASE_URL}/api/v1/scenarios/{scenario}/run"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        resp = http_client.post(
            url,
            params=query,
            json=body,
            headers=headers,
            verify=False,
            timeout=TIMEOUT,
        )

        try:
            resp_body = resp.json()
        except ValueError:
            resp_body = {"raw": resp.text}

        return jsonify({
            "status": resp.status_code,
            "url": resp.url,
            "requestBody": body,
            "body": resp_body,
        }), (200 if resp.ok else resp.status_code)
    except http_client.exceptions.ConnectionError as e:
        return jsonify({"error": "Cannot connect to TME server", "details": str(e), "url": TME_BASE_URL}), 502
    except http_client.exceptions.Timeout:
        return jsonify({"error": "TME request timeout", "url": TME_BASE_URL}), 504
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/config')
def get_config():
    return {
        "base_url": BASE_URL,
        "timeout": TIMEOUT,
        "login": os.getenv("SBMS_LOGIN", "DBS_CC_OPERATORS_PSO"),
        "password": "********",
        "msisdn": os.getenv("TEST_MSISDN", "998500173054"),
    }


@app.route('/api/auth', methods=['POST'])
def api_auth():
    """Авторизация: получить токен. Credentials берутся из тела запроса.
    Возвращает token + expiresIn (секунды) + expiresAt (unix timestamp)."""
    try:
        data = request.get_json() or {}
        login = (data.get("login") or "").strip()
        password = data.get("password") or ""
        if not login or not password:
            return jsonify({"error": "login и password обязательны", "code": "AUTH_MISSING"}), 400

        client = SBMSClient(BASE_URL, TIMEOUT)
        client.authenticate(login, password)
        ts = time.time()
        _auth_cache[login] = {"token": client.token, "ts": ts}

        return jsonify({
            "token": client.token,
            "login": login,
            "expiresIn": TOKEN_TTL,
            "expiresAt": int(ts + TOKEN_TTL),
        })
    except Exception as e:
        return jsonify({"error": str(e), "code": "AUTH_FAILED"}), 401


@app.route('/api/auth/verify', methods=['POST'])
def api_auth_verify():
    """Проверить, жив ли токен. Дёргает лёгкий эндпоинт SBMS."""
    data = request.get_json() or {}
    token = data.get("authToken")
    if not token:
        return jsonify({"valid": False, "error": "no token"}), 401
    try:
        client = SBMSClient(BASE_URL, TIMEOUT)
        client.token = token
        # searchBase с заведомо несуществующим MSISDN — быстрая проверка токена
        resp = client._get("/OAPI/v1/customers/searchBase", {
            "identification": "000000000000", "authToken": token
        })
        if resp.status_code in (200, 204):
            return jsonify({"valid": True})
        return jsonify({"valid": False, "status": resp.status_code}), 401
    except Exception as e:
        return jsonify({"valid": False, "error": str(e)}), 401


@app.route('/api/test/run', methods=['POST'])
def run_test():
    try:
        test_case = request.get_json()
        if not test_case:
            return jsonify({"error": "No JSON body"}), 400

        login = test_case.get("login") or os.getenv("SBMS_LOGIN", "DBS_CC_OPERATORS_PSO")
        password = test_case.get("password") or os.getenv("SBMS_PASSWORD", "Ucell2026$$")
        auth_token = test_case.get("authToken")

        runner = TestRunner(BASE_URL, login, password, TIMEOUT, auth_token=auth_token)
        report = runner.run(test_case)

        filename = save_report(report)
        result = report.to_dict()
        result["savedAs"] = filename

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/test/history')
def test_history():
    return jsonify(list_reports())


@app.route('/api/test/history/<report_id>')
def test_history_detail(report_id):
    data = get_report(report_id)
    if data is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(data)


@app.route('/api/tariff/load-customer', methods=['POST'])
def tariff_load_customer():
    """Загрузить клиента + доступные тарифы (read-only)."""
    try:
        data = request.get_json()
        if not data or not data.get("msisdn"):
            return jsonify({"error": "MSISDN обязателен"}), 400

        client = _get_client(data)

        search_data = client.search_customer(data["msisdn"])
        if not search_data or (search_data.get("listInfo") or {}).get("count", 0) == 0:
            return jsonify({"error": "Клиент не найден"}), 404

        sr = search_data["searchResults"][0]
        cid = sr.get("customerId")
        sid = sr.get("subscriberId")
        rate_plan = (sr.get("firstSubscriber") or {}).get("ratePlan") or {}
        rp_id = rate_plan.get("ratePlanId")

        balance_data = client.get_available_balance(cid)
        balance = balance_data.get("availableBalance", balance_data.get("availableAmount")) if balance_data else None

        avail_rps = client.get_available_rateplans(sid)
        avail_items = avail_rps.get("items", []) if avail_rps else []

        # Нормализация: items содержат вложенный ratePlan {name, ratePlanId}
        # Вытащить на верхний уровень для удобства фронтенда
        flat_items = []
        for item in avail_items:
            rp_obj = item.get("ratePlan") or {}
            flat_items.append({
                "ratePlanId": rp_obj.get("ratePlanId") or item.get("ratePlanId") or item.get("id"),
                "name": rp_obj.get("name") or item.get("name", "N/A"),
                "isArchived": item.get("isArchived"),
                "recurringFlag": item.get("recurringFlag"),
            })

        current_fee = None
        current_product_id = None
        nc_raw = None
        if rp_id:
            nc = client.get_rateplan_next_charges(sid, rp_id)
            nc_raw = nc
            if nc:
                current_fee = extract_recurring_charge(nc)
                current_product_id = extract_product_id_from_charges(nc)
                if current_fee is None:
                    print(f"[WARN] АП не найдена. rp_id={rp_id}, nc keys={list(nc.keys()) if isinstance(nc, dict) else type(nc)}")

        # rtDiscounts — объёмы (минуты, SMS, МБ)
        volumes = {"minutes": 0, "sms": 0, "mb": 0, "money": 0}
        volumes_by_tariff = None
        tariff_details = []
        rt_data = client.get_rt_discounts(sid)
        if rt_data:
            all_totals, _ = extract_volumes(rt_data)
            volumes = all_totals
            if current_product_id:
                tariff_totals, tariff_dets, tariff_count = extract_volumes_by_product_id(rt_data, current_product_id)
                if tariff_count > 0:
                    volumes_by_tariff = tariff_totals
                    tariff_details = tariff_dets

        # Пакеты (активные + доступные)
        def safe_items(data):
            if data and isinstance(data, dict):
                return data.get("items", []) or []
            return []

        active_packs = safe_items(client.get_active_packs(sid))
        avail_packs = safe_items(client.get_available_packs(sid))
        active_services = safe_items(client.get_active_services(sid))
        avail_services = safe_items(client.get_available_services(sid))

        # Жизненный цикл абонента
        lifecycle_state = None
        lifecycle_details = {}   # extra поля: дата активации, дата смены состояния, история
        lc_raw = None

        def _pick_lc_state(d):
            if not isinstance(d, dict):
                return None
            return (d.get("def") or d.get("lcStateName") or d.get("stateName") or d.get("name"))

        def _pick_lc_dates(d):
            """Собрать вероятные поля дат из объекта lifecycle."""
            if not isinstance(d, dict):
                return {}
            keys = ("activationDate", "activatedDate", "beginDate", "startDate",
                    "stateBeginDate", "stateStartDate", "endDate", "stateEndDate",
                    "lastChangeDate", "changedAt", "blockDate")
            return {k: d[k] for k in keys if d.get(k)}

        try:
            lc_data = client.get_lifecycle_actual(sid)
            lc_raw = lc_data
            if lc_data and isinstance(lc_data, dict):
                lifecycle_state = _pick_lc_state(lc_data)
                lifecycle_details.update(_pick_lc_dates(lc_data))
                # Если вложенные items/lcState
                if not lifecycle_state:
                    for key in ("items", "lcStates", "lcStatesList"):
                        sub = lc_data.get(key)
                        if isinstance(sub, list) and sub:
                            first = sub[0] if isinstance(sub[0], dict) else {}
                            lifecycle_state = _pick_lc_state(first)
                            lifecycle_details.update(_pick_lc_dates(first))
                            if lifecycle_state:
                                break
            elif lc_data and isinstance(lc_data, list) and lc_data:
                first = lc_data[0] if isinstance(lc_data[0], dict) else {}
                lifecycle_state = _pick_lc_state(first)
                lifecycle_details.update(_pick_lc_dates(first))

            # Вторая попытка + история состояний — через customerId
            lc_info = client.get_lifecycle_info(cid)
            if lc_info and isinstance(lc_info, dict):
                lc_items2 = lc_info.get("items") or lc_info.get("searchResults") or []
                if lc_items2 and isinstance(lc_items2[0], dict):
                    first = lc_items2[0]
                    if not lifecycle_state:
                        lifecycle_state = _pick_lc_state(first)
                    lifecycle_details.update({k: v for k, v in _pick_lc_dates(first).items()
                                              if k not in lifecycle_details})
                    history = []
                    for key in ("lcStatesList", "lcStates"):
                        sub = first.get(key)
                        if isinstance(sub, list):
                            for st in sub:
                                if not isinstance(st, dict):
                                    continue
                                history.append({
                                    "state": _pick_lc_state(st),
                                    "startDate": (st.get("beginDate") or st.get("startDate")
                                                  or st.get("stateBeginDate")),
                                    "endDate": (st.get("endDate") or st.get("stateEndDate")),
                                })
                            break
                    if history:
                        lifecycle_details["history"] = history
                        if not lifecycle_state:
                            # В истории последнее состояние без endDate — текущее
                            for st in history:
                                if not st.get("endDate"):
                                    lifecycle_state = st.get("state")
                                    break
                if not lifecycle_state and not lifecycle_details:
                    lc_raw = lc_info
        except Exception as e:
            print(f"[WARN] lifecycle error: {e}")

        # Дата следующего списания АП (chargeEndDate объекта Rate plan)
        next_charge_date = None
        try:
            obj_data = client.get_subscriber_objects(sid)
            if isinstance(obj_data, dict):
                items = obj_data.get("items") or []
                rp_obj = None
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    ot = it.get("objectType") or {}
                    type_name = (ot.get("name") or "").lower() if isinstance(ot, dict) else ""
                    type_id = ot.get("objectTypeId") if isinstance(ot, dict) else None
                    if type_id == 1 or "rate plan" in type_name or "тариф" in type_name:
                        rp_obj = it
                        break
                if rp_obj is None and items and isinstance(items[0], dict):
                    rp_obj = items[0]
                if isinstance(rp_obj, dict):
                    next_charge_date = (rp_obj.get("chargeEndDate")
                                        or rp_obj.get("nextChargeDate"))
            print(f"[DEBUG] subscriber_objects sid={sid}: "
                  f"chargeEndDate={next_charge_date}")
        except Exception as e:
            print(f"[WARN] subscriber_objects error: {e}")

        # Самый надёжный источник — /subslcstates/history/search.
        # Используем для текущего состояния (если предыдущие методы вернули пусто)
        # и сохраняем полный нормализованный список для модалки «История ЖЦ».
        lc_history_list = []
        lc_history_raw_keys = None
        try:
            lc_hist_raw = client.get_lifecycle_history(sid)
            if isinstance(lc_hist_raw, dict):
                lc_history_raw_keys = list(lc_hist_raw.keys())
            lc_history_list = _normalize_lc_history(lc_hist_raw)
            print(f"[DEBUG] lifecycle_history sid={sid}: "
                  f"raw_keys={lc_history_raw_keys}, "
                  f"items={len(lc_history_list)}")
            current = _pick_current_lc(lc_history_list)
            if current:
                if not lifecycle_state and current.get("state"):
                    lifecycle_state = current["state"]
                if current.get("stateDate") and not lifecycle_details.get("stateBeginDate"):
                    lifecycle_details["stateBeginDate"] = current["stateDate"]
        except Exception as e:
            print(f"[WARN] lifecycle_history error: {e}")

        return jsonify({
            "customer": {
                "customerId": cid,
                "subscriberId": sid,
                "name": (sr.get("customer") or {}).get("name", "N/A"),
                "ratePlanName": rate_plan.get("name", "N/A"),
                "ratePlanId": rp_id,
                "status": ((sr.get("firstSubscriber") or {}).get("status") or {}).get("name", "N/A"),
                "lifecycleState": lifecycle_state,
                "lifecycleDetails": lifecycle_details or None,
                "lifecycleHistory": lc_history_list or None,
                "nextChargeDate": next_charge_date,
                "balance": balance,
                "currentFee": current_fee,
                "currentProductId": current_product_id,
                "_debug": {
                    "lifecycleRaw": lc_raw,
                    "nextChargesRaw": nc_raw,
                } if (lifecycle_state is None or current_fee is None) else None,
                "volumes": volumes,
                "volumesByTariff": volumes_by_tariff,
                "tariffDetails": tariff_details
            },
            "availableRatePlans": flat_items,
            "activePacks": [{
                "packInstanceId": p.get("subscriberPackId") or p.get("packInstanceId") or p.get("id"),
                "packId": (p.get("pack") or {}).get("packId") or p.get("packId"),
                "name": (p.get("pack") or {}).get("name") or p.get("name", "N/A"),
                "status": (p.get("status") or {}).get("name", "Активен")
            } for p in active_packs if p],
            "availablePacks": [{
                "packId": p.get("packId"),
                "name": p.get("name", "N/A"),
                "fee": p.get("fee")
            } for p in avail_packs if p],
            "activeServices": [{
                "serviceInstanceId": s.get("serviceId"),
                "serviceId": s.get("serviceId"),
                "name": s.get("name", "N/A"),
                "status": (s.get("status") or {}).get("name", "Активна")
            } for s in active_services if s],
            "availableServices": [{
                "serviceId": s.get("serviceId"),
                "name": s.get("name", "N/A"),
                "fee": s.get("fee"),
                "status": (s.get("status") or {}).get("name", "")
            } for s in avail_services if s]
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/lifecycle/history', methods=['POST'])
def api_lifecycle_history():
    """История смен жизненного цикла абонента (для модалки в UI).

    Body: { "subscriberId": <int>, "msisdn": <optional>, авторизация ... }
    Response: { "items": [<нормализованные записи>], "current": <текущее состояние или null> }
    """
    try:
        data = request.get_json() or {}
        sid = data.get("subscriberId")
        # Поддержка fallback по MSISDN — если фронт не сохранил sid
        if not sid and data.get("msisdn"):
            tmp_client = _get_client(data)
            sr = tmp_client.search_customer(data["msisdn"])
            if sr and (sr.get("listInfo") or {}).get("count"):
                sid = sr["searchResults"][0].get("subscriberId")
        if not sid:
            return jsonify({"error": "subscriberId обязателен"}), 400

        client = _get_client(data)
        raw = client.get_lifecycle_history(sid)
        items = _normalize_lc_history(raw)
        current = _pick_current_lc(items)
        return jsonify({
            "subscriberId": sid,
            "current": current,
            "items": items,
            "count": len(items),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/tariff/run', methods=['POST'])
def tariff_run_test():
    """Полный цикл: смена тарифа + проверка."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON body"}), 400

        auth_token = data.get("authToken")
        login = data.get("login") or os.getenv("SBMS_LOGIN", "DBS_CC_OPERATORS_PSO")
        password = data.get("password") or os.getenv("SBMS_PASSWORD", "Ucell2026$$")

        test_case = {
            "msisdn": data.get("msisdn"),
            "testType": "tariff_change",
            "targetName": data.get("targetName", ""),
            "login": login,
            "password": password,
            "expected": data.get("expected", {})
        }

        runner = TestRunner(BASE_URL, login, password, TIMEOUT, auth_token=auth_token)
        report = runner.run(test_case)

        filename = save_report(report)
        result = report.to_dict()
        result["savedAs"] = filename

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/pack/run', methods=['POST'])
def pack_run_test():
    """Полный цикл: подключение пакета + проверка."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON body"}), 400

        auth_token = data.get("authToken")
        login = data.get("login") or os.getenv("SBMS_LOGIN", "DBS_CC_OPERATORS_PSO")
        password = data.get("password") or os.getenv("SBMS_PASSWORD", "Ucell2026$$")

        test_case = {
            "msisdn": data.get("msisdn"),
            "testType": "pack_activate",
            "targetName": data.get("targetName", ""),
            "login": login,
            "password": password,
            "expected": data.get("expected", {})
        }

        runner = TestRunner(BASE_URL, login, password, TIMEOUT, auth_token=auth_token)
        report = runner.run(test_case)

        filename = save_report(report)
        result = report.to_dict()
        result["savedAs"] = filename

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================
# API: PACKS & SERVICES ACTIVATION/DEACTIVATION
# ============================================================

@app.route("/api/debug/subscriptions", methods=["POST"])
def api_debug_subscriptions():
    """Возвращает сырые данные пакетов и услуг (для отладки полей API)."""
    try:
        data = request.get_json()
        subscriber_id = data.get("subscriberId")
        if not subscriber_id:
            return jsonify({"error": "subscriberId required"}), 400
        client = _get_client(data)
        raw_active_packs = client.get_active_packs(subscriber_id)
        raw_active_services = client.get_active_services(subscriber_id)
        return jsonify({
            "activePacks_raw": raw_active_packs,
            "activeServices_raw": raw_active_services
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/packs/activate", methods=["POST"])
def api_activate_pack():
    """Активация пакета."""
    try:
        data = request.get_json()
        subscriber_id = data.get("subscriberId")
        pack_id = data.get("packId")

        if not subscriber_id or not pack_id:
            return jsonify({"error": "subscriberId and packId required"}), 400

        client = _get_client(data)
        result = client.activate_pack(subscriber_id, pack_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/packs/deactivate", methods=["POST"])
def api_deactivate_pack():
    """Деактивация пакета."""
    try:
        data = request.get_json()
        subscriber_id = data.get("subscriberId")
        pack_instance_id = data.get("packInstanceId")

        if not subscriber_id or not pack_instance_id:
            return jsonify({"error": "subscriberId and packInstanceId required"}), 400

        client = _get_client(data)
        result = client.deactivate_pack(subscriber_id, pack_instance_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/services/activate", methods=["POST"])
def api_activate_service():
    """Активация услуги."""
    try:
        data = request.get_json()
        subscriber_id = data.get("subscriberId")
        service_id = data.get("serviceId")

        if not subscriber_id or not service_id:
            return jsonify({"error": "subscriberId and serviceId required"}), 400

        client = _get_client(data)
        result = client.activate_service(subscriber_id, service_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/services/deactivate", methods=["POST"])
def api_deactivate_service():
    """Деактивация услуги."""
    try:
        data = request.get_json()
        subscriber_id = data.get("subscriberId")
        service_instance_id = data.get("serviceInstanceId")

        if not subscriber_id or not service_instance_id:
            return jsonify({"error": "subscriberId and serviceInstanceId required"}), 400

        client = _get_client(data)
        result = client.deactivate_service(subscriber_id, service_instance_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# LIMITS OVERVIEW — все скидки с привязкой к источнику
# ============================================================
@app.route("/api/limits/all", methods=["POST"])
def api_limits_all():
    """Вернуть все rtDiscounts, сгруппированные по источнику (тариф/пакет/услуга)."""
    try:
        data = request.get_json()
        msisdn = data.get("msisdn")

        if not msisdn:
            return jsonify({"error": "msisdn required"}), 400

        client = _get_client(data)
        search_data = client.search_customer(msisdn)
        if not search_data or (search_data.get("listInfo") or {}).get("count", 0) == 0:
            return jsonify({"error": "Клиент не найден"}), 404

        sr = search_data["searchResults"][0]
        sid = sr.get("subscriberId")
        rate_plan = (sr.get("firstSubscriber") or {}).get("ratePlan") or {}
        rp_id   = rate_plan.get("ratePlanId")
        rp_name = rate_plan.get("name", "Текущий тариф")

        # ── все скидки ───────────────────────────────────────
        rt_data = client.get_rt_discounts(sid)
        items   = (rt_data or {}).get("items", [])

        # ── productId → источник ──────────────────────────────
        product_map = {}   # productId -> {name, type, id}

        # Текущий тариф
        if rp_id:
            nc = client.get_rateplan_next_charges(sid, rp_id)
            if nc:
                pid = extract_product_id_from_charges(nc)
                if pid:
                    product_map[pid] = {"name": rp_name, "type": "tariff", "id": rp_id}

        # Активные пакеты → через nextCharges достаём productId
        def safe_items(resp):
            return (resp or {}).get("items", []) if isinstance((resp or {}), dict) else []

        active_packs = safe_items(client.get_active_packs(sid))
        for p in active_packs:
            pack_obj  = p.get("pack") or {}
            pack_id   = pack_obj.get("packId")   or p.get("packId")
            pack_name = pack_obj.get("name")      or p.get("name", "Пакет")
            inst_id   = p.get("subscriberPackId") or p.get("packInstanceId")
            if pack_id:
                try:
                    pnc = client.get_pack_next_charges(sid, pack_id)
                    if pnc:
                        pid = extract_product_id_from_charges(pnc)
                        if pid:
                            product_map[pid] = {
                                "name": pack_name, "type": "pack",
                                "id": pack_id, "instanceId": inst_id
                            }
                except Exception:
                    pass

        # Активные услуги → через nextCharges достаём productId
        active_services = safe_items(client.get_active_services(sid))
        for s in active_services:
            svc_id   = s.get("serviceId")
            svc_name = s.get("name", "Услуга")
            if svc_id:
                try:
                    snc = client.get_service_next_charges(sid, svc_id)
                    if snc:
                        pid = extract_product_id_from_charges(snc)
                        if pid:
                            product_map[pid] = {
                                "name": svc_name, "type": "service",
                                "id": svc_id, "instanceId": svc_id
                            }
                except Exception:
                    pass

        # ── группировка по productId ──────────────────────────
        UNITS = {
            0:  {"label": "Деньги",   "unit": "сум"},
            1:  {"label": "Минуты",   "unit": "мин"},
            7:  {"label": "SMS",      "unit": "sms"},
            14: {"label": "Интернет", "unit": "МБ"},
        }

        groups_map = defaultdict(list)
        for item in items:
            groups_map[item.get("productId")].append(item)

        result_groups = []
        for pid, disc_items in groups_map.items():
            source = product_map.get(pid) or {
                "name": f"productId: {pid}", "type": "unknown", "id": pid
            }
            discounts = []
            for d in disc_items:
                uid      = d.get("measureUnitId", -1)
                ui       = UNITS.get(uid, {"label": f"unit_{uid}", "unit": ""})
                max_vol  = d.get("maxVolume",  0) or 0
                spent    = d.get("spentVolume", 0) or 0
                dpid     = d.get("discountPlanId")
                desc     = get_discount_description(dpid) if dpid else ""
                discounts.append({
                    "measureUnitId":  uid,
                    "label":         ui["label"],
                    "unit":          ui["unit"],
                    "maxVolume":     max_vol,
                    "spentVolume":   spent,
                    "remaining":     round(max_vol - spent, 4),
                    "discountPlanId": dpid,
                    "discountName":  d.get("discountName", "") or desc,
                    "startDate":     d.get("startDate", ""),
                    "endDate":       d.get("endDate", ""),
                    "discountType":  d.get("discountType"),
                    "discountDetailId": d.get("discountDetailId"),
                })

            result_groups.append({
                "productId":  pid,
                "sourceName": source["name"],
                "sourceType": source["type"],
                "sourceId":   source.get("id"),
                "discounts":  discounts,
            })

        # сортировка: тариф → пакеты → услуги → остальное
        order = {"tariff": 0, "pack": 1, "service": 2, "unknown": 3}
        result_groups.sort(key=lambda g: order.get(g["sourceType"], 9))

        return jsonify({"groups": result_groups, "totalItems": len(items)})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ============================================================
# HISTORY — последние изменения по номеру (тариф, пакеты, услуги, платежи, lifecycle)
# ============================================================

def _parse_dt(value):
    """Распарсить дату из ответа SBMS (ISO или DD.MM.YYYY HH:MM:SS) → datetime или None."""
    from datetime import datetime
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000 if value > 1e12 else value)
        except Exception:
            return None
    s = str(value).strip()
    if not s:
        return None
    fmts = [
        "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
        "%d.%m.%Y %H:%M:%S", "%d.%m.%Y",
        "%d/%m/%Y %H:%M:%S", "%d/%m/%Y",
    ]
    for f in fmts:
        try:
            return datetime.strptime(s.split("+")[0].split("Z")[0], f)
        except Exception:
            pass
    return None


def _iso(dt):
    return dt.isoformat() if dt else None


def _get_first(d, *keys):
    """Вернуть первое непустое значение из словаря по списку ключей."""
    if not isinstance(d, dict):
        return None
    for k in keys:
        v = d.get(k)
        if v not in (None, "", []):
            return v
    return None


@app.route('/api/history', methods=['POST'])
def api_history():
    """Сводная история изменений по номеру.

    Body: {msisdn, login?/password?/authToken?, days?, limit?}
    Returns: { events: [{type, date, title, description, status, amount, raw}, ...] }
    События отсортированы по дате DESC (самые новые сверху).
    """
    from datetime import datetime, timedelta

    try:
        data = request.get_json() or {}
        msisdn = data.get("msisdn")
        if not msisdn:
            return jsonify({"error": "msisdn обязателен"}), 400

        days = int(data.get("days") or 90)
        limit = int(data.get("limit") or 200)

        client = _get_client(data)

        # 1) Найти абонента
        sd = client.search_customer(msisdn)
        if not sd or (sd.get("listInfo") or {}).get("count", 0) == 0:
            return jsonify({"error": "Клиент не найден"}), 404
        sr = sd["searchResults"][0]
        cid = sr.get("customerId")
        sid = sr.get("subscriberId")

        now = datetime.now()
        date_from = now - timedelta(days=days)
        date_to = now + timedelta(days=1)

        events = []
        warnings = []

        # 2) Заказы на смену тарифа
        try:
            orders = client.search_rateplan_orders(sid, limit=limit)
            items = (orders or {}).get("items") or (orders or {}).get("ratePlanOrders") or []

            # Сортируем по дате, чтобы корректно вывести "переход X → Y"
            def _ord_date(o):
                return _parse_dt(_get_first(o, "startDate", "changeDate",
                                            "executionDate", "createDate")) \
                    or _parse_dt("1970-01-01")
            items_sorted = sorted(items, key=_ord_date)
            prev_name = None
            for o in items_sorted:
                if not isinstance(o, dict):
                    continue
                rp = o.get("ratePlan") if isinstance(o.get("ratePlan"), dict) else {}
                new_name = rp.get("name") or _get_first(o, "ratePlanName", "name")
                status_obj = o.get("status") if isinstance(o.get("status"), dict) else {}
                status_name = status_obj.get("name") if isinstance(status_obj, dict) else (
                    str(status_obj) if status_obj else "")
                dt = _parse_dt(_get_first(o, "startDate", "changeDate",
                                          "executionDate", "createDate", "endDate"))

                if prev_name and new_name and prev_name != new_name:
                    subtitle = f"{prev_name} → {new_name}"
                elif new_name:
                    subtitle = new_name
                else:
                    subtitle = f"Заказ #{o.get('ratePlanOrderId') or ''}"

                desc_parts = []
                user = o.get("changeUser")
                if user:
                    desc_parts.append(f"оператор: {user}")
                if o.get("subscriberComment"):
                    desc_parts.append(str(o["subscriberComment"]))
                if o.get("ratePlanOrderId"):
                    desc_parts.append(f"order {o['ratePlanOrderId']}")

                events.append({
                    "type": "tariff_change",
                    "date": _iso(dt),
                    "title": "Смена тарифа",
                    "subtitle": subtitle,
                    "description": " · ".join(desc_parts) if desc_parts else "",
                    "status": status_name,
                    "amount": None,
                    "raw": o,
                })
                if new_name:
                    prev_name = new_name
        except Exception as e:
            warnings.append(f"orders: {e}")

        # 3) История пакетов и услуг (PSIX combhist — XML с ROW-ами)
        try:
            sd_str = date_from.strftime("%d.%m.%Y") + "+00:00:00"
            ed_str = date_to.strftime("%d.%m.%Y") + "+23:59:59"
            ch = client.get_combined_history(cid, sid, sd_str, ed_str, limit=limit)
            ch_items = (ch or {}).get("items") or []

            for it in ch_items:
                if not isinstance(it, dict):
                    continue
                action_raw = (it.get("action") or "").upper()           # PACKAGE_ACTION, SERVICE_ACTION, RATE_PLAN_ACTION
                event_text = (it.get("event") or "").strip()            # "Ожидает оплаты", "Отключен", "Подключён"
                obj_name = it.get("name") or it.get("description") or "—"
                obj_desc = it.get("description") or ""
                dt = _parse_dt(it.get("startDate") or it.get("naviDate"))
                ev_lc = event_text.lower()

                is_pack = "PACKAGE" in action_raw or "PACK" in action_raw
                is_service = "SERVICE" in action_raw or "SERV" in action_raw
                is_tariff = "RATE_PLAN" in action_raw or "TARIFF" in action_raw or "RTPL" in action_raw
                is_deact = any(w in ev_lc for w in (
                    "отключ", "deact", "remove", "delete", "off", "снят"
                ))
                is_pending = any(w in ev_lc for w in ("ожида", "pending", "очеред"))

                if is_tariff:
                    base = "tariff_change"
                    title = "Смена тарифа"
                elif is_pack:
                    base = "pack_deactivate" if is_deact else "pack_activate"
                    title = "Отключение пакета" if is_deact else "Подключение пакета"
                elif is_service:
                    base = "service_deactivate" if is_deact else "service_activate"
                    title = "Отключение услуги" if is_deact else "Подключение услуги"
                else:
                    base = "subscription_deactivate" if is_deact else "subscription_activate"
                    title = "Отключение подписки" if is_deact else "Подключение подписки"

                # Если событие "ожидает оплаты" — это активация в очереди
                if is_pending and not is_deact and not base.endswith("_deactivate"):
                    title = title.replace("Подключение", "Заявка на подключение")

                desc_parts = []
                if obj_desc and obj_desc != obj_name:
                    desc_parts.append(obj_desc)
                if it.get("naviUser"):
                    desc_parts.append(f"оператор: {it['naviUser']}")
                if it.get("comment"):
                    desc_parts.append(it["comment"])

                events.append({
                    "type": base,
                    "date": _iso(dt),
                    "title": title,
                    "subtitle": obj_name,
                    "description": " · ".join(desc_parts) if desc_parts else "",
                    "status": event_text,
                    "amount": it.get("amount"),
                    "raw": it,
                })
        except Exception as e:
            warnings.append(f"combined_history: {e}")

        # 4) История lifecycle (состояние абонента: Active / Suspend / Closed / S1)
        try:
            lc = client.get_lifecycle_history(sid)
            lc_items = []
            if isinstance(lc, dict):
                for k in ("items", "lcStatesList", "lcStates", "history", "data"):
                    v = lc.get(k)
                    if isinstance(v, list):
                        lc_items = v
                        break
            elif isinstance(lc, list):
                lc_items = lc

            for st in lc_items:
                if not isinstance(st, dict):
                    continue
                lc_state = st.get("lcState") if isinstance(st.get("lcState"), dict) else {}
                state_name = (
                    lc_state.get("def") or lc_state.get("name")
                    or _get_first(st, "def", "lcStateName", "stateName", "name", "state")
                    or "—"
                )
                conv = st.get("conversionType") if isinstance(st.get("conversionType"), dict) else {}
                conv_def = conv.get("def") if isinstance(conv, dict) else None
                dt = _parse_dt(_get_first(st, "lcStateDate", "startDate", "beginDate",
                                          "stateBeginDate", "actionDate", "changeDate"))
                audit = st.get("audit") if isinstance(st.get("audit"), dict) else {}
                user = audit.get("naviUser") if isinstance(audit, dict) else None

                desc_parts = []
                if conv_def:
                    desc_parts.append(conv_def)
                if user and user != "BIS":
                    desc_parts.append(f"оператор: {user}")
                if st.get("note"):
                    desc_parts.append(str(st["note"]))

                events.append({
                    "type": "lifecycle",
                    "date": _iso(dt),
                    "title": "Изменение состояния",
                    "subtitle": state_name,
                    "description": " · ".join(desc_parts) if desc_parts else "Жизненный цикл абонента",
                    "status": state_name,
                    "amount": None,
                    "raw": st,
                })
        except Exception as e:
            warnings.append(f"lifecycle_history: {e}")

        # 5) Платежи (FIM payments — отдельный реестр, может быть пуст у некоторых учёток)
        try:
            df_str = date_from.strftime("%Y-%m-%dT%H:%M:%S")
            pays = client.get_payments(cid, date_from=df_str, limit=limit)
            p_items = (pays or {}).get("items") or (pays or {}).get("payments") or []
            for p in p_items:
                if not isinstance(p, dict):
                    continue
                dt = _parse_dt(_get_first(p, "paymentDate", "date", "createDate"))
                amount = _get_first(p, "amount", "sum", "value")
                method = (p.get("paymentMethod") or {}) if isinstance(p.get("paymentMethod"), dict) else {}
                method_name = method.get("name") or _get_first(p, "method", "channel") or "—"
                events.append({
                    "type": "payment",
                    "date": _iso(dt),
                    "title": "Пополнение баланса",
                    "subtitle": f"+{amount} сум" if amount is not None else "Платёж",
                    "description": method_name,
                    "status": _get_first((p.get("status") or {}), "name") if isinstance(p.get("status"), dict) else "",
                    "amount": amount,
                    "raw": p,
                })
        except Exception as e:
            warnings.append(f"payments: {e}")

        # 6) Дневная разбивка движений по балансу — пополнения + АП + разовые
        # Источник: /OAPI/v1/sbms/customers/{cid}/balances/events/days
        # eventTypeId: 1=Вызовы, 6=АП, 7=Разовые, 9=Платежи, 11=Корректировки,
        #              13=Обещанные платежи, 14=Биллинговые скидки, 15=ФА
        EV_TYPE_TITLE = {
            9:  ("payment",          "Пополнение баланса",      "income"),
            6:  ("charge_recurring", "Списание абонплаты",       "spend"),
            7:  ("charge_one_time",  "Разовое списание",         "spend"),
            11: ("adjustment",       "Корректировка баланса",    "both"),
            13: ("promised_payment", "Обещанный платёж",         "income"),
            14: ("adjustment",       "Биллинговая скидка",       "income"),
            1:  ("charge_one_time",  "Списание за вызовы/SMS/трафик", "spend"),
        }

        # Чтобы не плодить дубли с FIM-payments — запомним даты, где payments уже добавили
        existing_payment_days = {(ev["date"] or "")[:10] for ev in events if ev["type"] == "payment"}

        try:
            # balance_events — тяжёлый endpoint (~5 КБ на день).
            # Ограничиваем максимум 90 днями, чтобы запрос успевал.
            be_from = max(date_from, now - timedelta(days=90))
            df_str = be_from.strftime("%Y-%m-%dT%H:%M:%S")
            dt_str = date_to.strftime("%Y-%m-%dT%H:%M:%S")
            be = client.get_balance_events_days(cid, df_str, dt_str)
            be_items = (be or {}).get("items") or []
            for day in be_items:
                if not isinstance(day, dict):
                    continue
                day_date = _parse_dt(day.get("balanceDate"))
                aggregates = day.get("aggregateEvents") or []
                for agg in aggregates:
                    if not isinstance(agg, dict):
                        continue
                    et = agg.get("eventType") or {}
                    et_id = et.get("eventTypeId")
                    et_name = et.get("name") or ""
                    cfg = EV_TYPE_TITLE.get(et_id)
                    if not cfg:
                        continue
                    ev_type, title, kind = cfg
                    income = abs(float(agg.get("incomeAmount") or 0))
                    spend = abs(float(agg.get("spendAmount") or 0))

                    # Пропустить пустые
                    if income == 0 and spend == 0:
                        continue
                    # Не дублировать платежи, которые уже пришли из FIM
                    if ev_type == "payment" and day_date and day_date.strftime("%Y-%m-%d") in existing_payment_days:
                        continue

                    if kind == "income":
                        amount = income; sign = "+"
                    elif kind == "spend":
                        amount = spend;  sign = "−"
                    else:  # "both"
                        if income >= spend:
                            amount = income; sign = "+"
                        else:
                            amount = spend;  sign = "−"

                    if amount == 0:
                        continue

                    fmt_amount = f"{int(round(amount)):,}".replace(",", " ")
                    subtitle = f"{sign}{fmt_amount} сум"

                    events.append({
                        "type": ev_type,
                        "date": _iso(day_date),
                        "title": title,
                        "subtitle": subtitle,
                        "description": et_name,
                        "status": "",
                        "amount": amount if sign == "+" else -amount,
                        "raw": agg,
                    })
        except Exception as e:
            warnings.append(f"balance_events: {e}")

        # Сортировка: самые свежие сверху, события без даты — в конец
        def _sort_key(ev):
            d = ev.get("date") or ""
            return (1 if d else 0, d)

        events.sort(key=_sort_key, reverse=True)

        # Усечение по limit
        events = events[:limit]

        # Подсчёт по типам — для UI-фильтра
        counts = {}
        for ev in events:
            counts[ev["type"]] = counts.get(ev["type"], 0) + 1

        return jsonify({
            "msisdn": msisdn,
            "customerId": cid,
            "subscriberId": sid,
            "rangeDays": days,
            "from": _iso(date_from),
            "to": _iso(date_to),
            "totalEvents": len(events),
            "counts": counts,
            "events": events,
            "warnings": warnings or None,
        })
    except AuthRequired:
        raise
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ============================================================
# PREVIEW (View-as) — read-only просмотр от имени другой учётки
# ============================================================
@app.route("/api/preview/subscriptions", methods=["POST"])
def preview_subscriptions():
    """Read-only preview подписок номера от имени другой учётки (B2B FO / B2C FO / etc).

    Не затрагивает админ-сессию: создаёт ИЗОЛИРОВАННЫЙ SBMSClient,
    его токен НЕ кладётся в _auth_cache. Anti-abuse: требуется живой админ-токен
    в поле adminToken (проверяется тривиально — наличие непустого значения).

    Body: {msisdn, role, login, password, adminToken}
    Returns: {ok, role, msisdn, currentRatePlan, data: {activePacks, ...}}
    """
    data = request.get_json(silent=True) or {}
    msisdn = (data.get("msisdn") or "").strip()
    role = (data.get("role") or "custom").strip()
    p_login = (data.get("login") or "").strip()
    p_password = data.get("password") or ""
    admin_token = (data.get("adminToken") or "").strip()

    # Маскированный лог — без пароля
    print(f"[PREVIEW] msisdn={msisdn} role={role} login={p_login} pwd=*** admin_token={'set' if admin_token else 'missing'}")

    if not admin_token:
        return jsonify({"error": "ADMIN_AUTH_REQUIRED",
                        "message": "Сначала войдите в основную (админ) учётку"}), 401

    if not msisdn or not p_login or not p_password:
        return jsonify({"error": "BAD_REQUEST",
                        "message": "Поля msisdn / login / password обязательны"}), 400

    # Изолированный клиент: НЕ через _get_client, кеш не трогаем
    pclient = SBMSClient(BASE_URL, TIMEOUT)
    try:
        pclient.authenticate(p_login, p_password)
    except Exception as e:
        return jsonify({"error": "AUTH_FAILED",
                        "message": f"Не удалось войти под {p_login}: {str(e)[:200]}"}), 401

    try:
        sd = pclient.search_customer(msisdn)
        if not sd or (sd.get("listInfo") or {}).get("count", 0) == 0:
            return jsonify({"error": "SUBSCRIBER_NOT_FOUND",
                            "message": f"Номер {msisdn} не найден под учёткой {p_login}"}), 404

        sr = sd["searchResults"][0]
        cid = sr.get("customerId")
        sid = sr.get("subscriberId")
        rate_plan = (sr.get("firstSubscriber") or {}).get("ratePlan") or {}

        def _items(d):
            return d.get("items", []) if isinstance(d, dict) else []

        active_packs = _items(pclient.get_active_packs(sid))
        avail_packs = _items(pclient.get_available_packs(sid))
        active_svc = _items(pclient.get_active_services(sid))
        avail_svc = _items(pclient.get_available_services(sid))

        avail_rps = pclient.get_available_rateplans(sid)
        flat_rps = []
        for it in (_items(avail_rps) or []):
            rp = it.get("ratePlan") or {}
            flat_rps.append({
                "ratePlanId": rp.get("ratePlanId") or it.get("ratePlanId") or it.get("id"),
                "name": rp.get("name") or it.get("name", "N/A"),
                "isArchived": it.get("isArchived"),
            })

        result = {
            "ok": True,
            "role": role,
            "msisdn": msisdn,
            "subscriberId": sid,
            "customerId": cid,
            "currentRatePlan": {
                "ratePlanId": rate_plan.get("ratePlanId"),
                "name": rate_plan.get("name"),
            },
            "data": {
                "activePacks": active_packs,
                "availablePacks": avail_packs,
                "activeServices": active_svc,
                "availableServices": avail_svc,
                "availableRatePlans": flat_rps,
            },
        }
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "PREVIEW_FAILED", "message": str(e)[:300]}), 500
    finally:
        # Токен preview-сессии нам больше не нужен; в SBMS истечёт сам.
        pclient.token = None

    return jsonify(result)


@app.route("/api/preview/activate", methods=["POST"])
def preview_activate():
    """Активация пакета/услуги от имени другой учётки (preview).

    Использует ИЗОЛИРОВАННЫЙ SBMSClient — admin-кеш не трогается.
    Креды preview-учётки приходят в каждом запросе и нигде не сохраняются.

    Body: {msisdn, login, password, adminToken, kind: 'pack'|'service', itemId, subscriberId?}
    Returns: {ok, kind, itemId, subscriberId, result, data: {refreshed lists}}
    """
    data = request.get_json(silent=True) or {}
    msisdn = (data.get("msisdn") or "").strip()
    p_login = (data.get("login") or "").strip()
    p_password = data.get("password") or ""
    admin_token = (data.get("adminToken") or "").strip()
    kind = (data.get("kind") or "").strip()
    item_id = data.get("itemId")
    sid = data.get("subscriberId")

    print(f"[PREVIEW-ACT] msisdn={msisdn} login={p_login} kind={kind} item={item_id} "
          f"sid={sid} admin_token={'set' if admin_token else 'missing'}")

    if not admin_token:
        return jsonify({"error": "ADMIN_AUTH_REQUIRED",
                        "message": "Сначала войдите в основную (админ) учётку"}), 401
    if not p_login or not p_password:
        return jsonify({"error": "BAD_REQUEST",
                        "message": "Поля login и password обязательны"}), 400
    if kind not in ("pack", "service"):
        return jsonify({"error": "BAD_REQUEST",
                        "message": "kind должен быть 'pack' или 'service'"}), 400
    if not item_id:
        return jsonify({"error": "BAD_REQUEST",
                        "message": "itemId обязателен"}), 400

    pclient = SBMSClient(BASE_URL, TIMEOUT)
    try:
        pclient.authenticate(p_login, p_password)
    except Exception as e:
        return jsonify({"error": "AUTH_FAILED",
                        "message": f"Не удалось войти под {p_login}: {str(e)[:200]}"}), 401

    try:
        if not sid:
            if not msisdn:
                return jsonify({"error": "BAD_REQUEST",
                                "message": "Нужен subscriberId или msisdn"}), 400
            sd = pclient.search_customer(msisdn)
            if not sd or (sd.get("listInfo") or {}).get("count", 0) == 0:
                return jsonify({"error": "SUBSCRIBER_NOT_FOUND",
                                "message": f"Номер {msisdn} не найден под учёткой {p_login}"}), 404
            sid = sd["searchResults"][0].get("subscriberId")

        if kind == "pack":
            result = pclient.activate_pack(sid, item_id)
        else:
            result = pclient.activate_service(sid, item_id)

        def _items(d):
            return d.get("items", []) if isinstance(d, dict) else []

        refreshed = {
            "activePacks": _items(pclient.get_active_packs(sid)),
            "availablePacks": _items(pclient.get_available_packs(sid)),
            "activeServices": _items(pclient.get_active_services(sid)),
            "availableServices": _items(pclient.get_available_services(sid)),
        }

        return jsonify({
            "ok": True,
            "kind": kind,
            "itemId": item_id,
            "subscriberId": sid,
            "result": result,
            "data": refreshed,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "ACTIVATE_FAILED", "message": str(e)[:300]}), 500
    finally:
        pclient.token = None


# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    port = int(os.getenv("SERVER_PORT", "5000"))
    print()
    print("=" * 55)
    print("  UCELL SBMS API - Proxy Server")
    print("=" * 55)
    print(f"  Dashboard:    http://localhost:{port}")
    print(f"  QA Tester:    http://localhost:{port}/tester")
    print(f"  Tariff Test:  http://localhost:{port}/tariff-test")
    print(f"  TME Auth:     http://localhost:{port}/tme")
    print(f"  Proxy:        http://localhost:{port}/proxy/...")
    print(f"  Target:     {BASE_URL}")
    print(f"  Timeout:    {TIMEOUT}s")
    print("=" * 55)
    print()
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host='0.0.0.0', port=port, debug=debug, threaded=True)
