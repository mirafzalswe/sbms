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
from concurrent.futures import ThreadPoolExecutor
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None
from sbms_client import SBMSClient, _psix_safe_snippet, _redact_snippet
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

# send_file резолвит относительные пути от cwd. Чтобы работало при запуске
# из любого каталога, считаем абсолютный путь к templates/ один раз.
APP_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(APP_DIR, "templates")


def _send_template(name):
    return send_file(os.path.join(TEMPLATES_DIR, name))

# ============================================================
# TOKEN CACHE — авторизация один раз, переиспользование токена
# ============================================================
_auth_cache = {}  # login -> {"token": str, "ts": float, "password": str}
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
        # Часть PSIX-grid процедур (STRAFF_CALLS_R и пр.) требует Basic.
        # Если в кеше есть запись с таким же токеном — берём оттуда login/password.
        for cached_login, entry in _auth_cache.items():
            if entry.get("token") == token and entry.get("password"):
                client._login = cached_login
                client._password = entry["password"]
                break
        return client

    login = data.get("login")
    password = data.get("password")
    now = time.time()

    if login and password:
        client = SBMSClient(BASE_URL, TIMEOUT)
        client.authenticate(login, password)
        _auth_cache[login] = {"token": client.token, "ts": now, "password": password}
        return client

    if login and login in _auth_cache and now - _auth_cache[login]["ts"] < TOKEN_TTL:
        client = SBMSClient(BASE_URL, TIMEOUT)
        client.token = _auth_cache[login]["token"]
        client._login = login
        client._password = _auth_cache[login].get("password")
        return client

    # Фолбэк на .env допустим только для CLI-сценариев; UI всегда прислал бы credentials.
    env_login = os.getenv("SBMS_LOGIN")
    env_pass = os.getenv("SBMS_PASSWORD")
    if env_login and env_pass and os.getenv("SBMS_ALLOW_ENV_AUTH") == "1":
        if env_login in _auth_cache and now - _auth_cache[env_login]["ts"] < TOKEN_TTL:
            client = SBMSClient(BASE_URL, TIMEOUT)
            client.token = _auth_cache[env_login]["token"]
            client._login = env_login
            client._password = env_pass
            return client
        client = SBMSClient(BASE_URL, TIMEOUT)
        client.authenticate(env_login, env_pass)
        _auth_cache[env_login] = {"token": client.token, "ts": now, "password": env_pass}
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
    resp = _send_template('dashboard.html')
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
    resp = _send_template('tester.html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


@app.route('/tariff-test')
def tariff_test():
    resp = _send_template('tariff_test.html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


@app.route('/matrix-test')
def matrix_test_page():
    resp = _send_template('matrix_test.html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


@app.route('/tme')
def tme_page():
    resp = _send_template('tme.html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


@app.route('/subscriber')
def subscriber_page():
    resp = _send_template('subscriber.html')
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
# PRODUCT AVAILABILITY — проверка доступности пакета/услуги на списке тарифов
# ============================================================
import product_availability_parser as _pa_parser
import product_availability_runner as _pa_runner


@app.route('/product-availability')
def product_availability_page():
    resp = _send_template('product_availability.html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


@app.route('/api/product-availability/parse-tariffs', methods=['POST'])
def api_pa_parse_tariffs():
    """Парсит источник со списком тарифов и возвращает [{id, name}].

    Тип источника определяется по приоритету:
    1. multipart-файл (поле 'file') → parse_file
    2. JSON-тело { "text": "..." } → parse_text
    3. JSON-тело { "json": [...] }   → parse_json
    """
    # 1) файл
    if 'file' in request.files:
        f = request.files['file']
        name = f.filename or ""
        content = f.read()
        if not content:
            return jsonify({"error": "Файл пустой"}), 400
        try:
            tariffs = _pa_parser.parse_file(name, content)
        except Exception as e:
            return jsonify({"error": str(e), "filename": name}), 400
        return jsonify({"tariffs": tariffs, "count": len(tariffs), "source": "file", "filename": name})

    data = request.get_json(silent=True) or {}
    text = data.get("text")
    json_data = data.get("json")
    try:
        if text is not None:
            tariffs = _pa_parser.parse_text(text)
            if not tariffs:
                return jsonify({"error": "Не удалось распознать ни одного тарифа в тексте"}), 400
            return jsonify({"tariffs": tariffs, "count": len(tariffs), "source": "text"})
        if json_data is not None:
            tariffs = _pa_parser.parse(json_data=json_data)
            return jsonify({"tariffs": tariffs, "count": len(tariffs), "source": "json"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"error": "Нужен файл (multipart 'file'), либо JSON {text} / {json}"}), 400


def _pa_validate_run_payload(data):
    """Возвращает (errors|None, parsed_kwargs)."""
    msisdn = (data.get("msisdn") or "").strip()
    product_id = data.get("product_id")
    product_kind = (data.get("product_kind") or "").strip().lower()
    tariffs = data.get("tariffs")
    admin_creds = data.get("admin") or {}
    viewer_creds = data.get("viewer") or {}
    errors = []
    if not msisdn:
        errors.append("msisdn обязателен")
    if product_id in (None, "", 0):
        errors.append("product_id обязателен")
    if product_kind not in ("pack", "service"):
        errors.append("product_kind должен быть 'pack' или 'service'")
    if not isinstance(tariffs, list) or not tariffs:
        errors.append("tariffs должен быть непустым списком [{id, name}]")
    if not admin_creds.get("login"):
        errors.append("admin{login,password} обязателен")
    if not viewer_creds.get("login"):
        errors.append("viewer{login,password} обязателен")
    if errors:
        return errors, None
    try:
        product_id = int(product_id)
    except (TypeError, ValueError):
        return ["product_id должен быть числом"], None
    return None, {
        "msisdn": msisdn,
        "product_id": product_id,
        "product_kind": product_kind,
        "tariffs": tariffs,
        "admin_creds": admin_creds,
        "viewer_creds": viewer_creds,
        "only_ids": data.get("only_ids") or None,
        "wait_after_change": float(data.get("wait_after_change", 5)),
        "order_timeout": int(data.get("order_timeout", 60)),
        "resume_from": (data.get("resume_from") or "").strip() or None,
    }


@app.route('/api/product-availability/run', methods=['POST'])
def api_pa_run():
    """Синхронный прогон. Возвращает report."""
    data = request.get_json(silent=True) or {}
    errors, kw = _pa_validate_run_payload(data)
    if errors:
        return jsonify({"error": "; ".join(errors)}), 400
    try:
        admin_client = _get_client(kw["admin_creds"])
    except Exception as e:
        return jsonify({"error": f"Admin auth failed: {e}", "scope": "admin"}), 401
    try:
        viewer_client = _get_client(kw["viewer_creds"])
    except Exception as e:
        return jsonify({"error": f"Viewer auth failed: {e}", "scope": "viewer"}), 401
    try:
        report = _pa_runner.run_product_availability(
            msisdn=kw["msisdn"],
            product_id=kw["product_id"],
            product_kind=kw["product_kind"],
            tariffs=kw["tariffs"],
            admin_client=admin_client,
            viewer_client=viewer_client,
            only_ids=kw["only_ids"],
            wait_after_change=kw["wait_after_change"],
            order_timeout=kw["order_timeout"],
            resume_from=kw["resume_from"],
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    return jsonify(report)


@app.route('/api/product-availability/run-stream', methods=['POST'])
def api_pa_run_stream():
    """SSE-вариант. Тело такое же, как у /run."""
    data = request.get_json(silent=True) or {}
    errors, kw = _pa_validate_run_payload(data)
    if errors:
        return jsonify({"error": "; ".join(errors)}), 400
    try:
        admin_client = _get_client(kw["admin_creds"])
        viewer_client = _get_client(kw["viewer_creds"])
    except Exception as e:
        return jsonify({"error": f"Auth failed: {e}"}), 401

    import queue as _q
    import threading
    q: "_q.Queue[dict]" = _q.Queue()
    abort_event = threading.Event()

    def cb(evt):
        q.put(evt)

    def worker():
        try:
            report = _pa_runner.run_product_availability(
                msisdn=kw["msisdn"], product_id=kw["product_id"],
                product_kind=kw["product_kind"], tariffs=kw["tariffs"],
                admin_client=admin_client, viewer_client=viewer_client,
                only_ids=kw["only_ids"], wait_after_change=kw["wait_after_change"],
                order_timeout=kw["order_timeout"], progress_cb=cb,
                resume_from=kw["resume_from"], abort_event=abort_event,
            )
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
            abort_event.set()

    return Response(stream(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.route('/api/product-availability/history')
def api_pa_history():
    return jsonify(_pa_runner.list_product_avail_reports())


@app.route('/api/product-availability/history/<report_id>')
def api_pa_history_detail(report_id):
    rep = _pa_runner.get_product_avail_report(report_id)
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
        _auth_cache[login] = {"token": client.token, "ts": ts, "password": password}

        return jsonify({
            "token": client.token,
            "login": login,
            "expiresIn": TOKEN_TTL,
            "expiresAt": int(ts + TOKEN_TTL),
        })
    except Exception as e:
        return jsonify({"error": str(e), "code": "AUTH_FAILED"}), 401


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


@app.route('/api/history/all')
def history_all():
    """Агрегированная история всех прогонов (без фильтра по MSISDN).

    Query params (опциональные):
      - kind: tariff | matrix | product
      - msisdn: фильтр по номеру
      - q: подстрока для поиска по target/имени
      - limit: max 200 (default 100)
      - offset: для пагинации (default 0)
    """
    kind_filter = (request.args.get('kind') or '').strip().lower()
    msisdn_filter = ''.join(ch for ch in (request.args.get('msisdn') or '') if ch.isdigit())
    q = (request.args.get('q') or '').strip().lower()
    try:
        limit = max(1, min(200, int(request.args.get('limit') or 100)))
    except Exception:
        limit = 100
    try:
        offset = max(0, int(request.args.get('offset') or 0))
    except Exception:
        offset = 0

    hist_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_history')
    if not os.path.isdir(hist_dir):
        return jsonify({'items': [], 'totals': {'tariff': 0, 'matrix': 0, 'product': 0}})

    items = []
    totals = {'tariff': 0, 'matrix': 0, 'product': 0}

    for fn in os.listdir(hist_dir):
        if not fn.endswith('.json'):
            continue
        filepath = os.path.join(hist_dir, fn)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue

        kind = 'tariff'
        if fn.startswith('matrix_') or data.get('type') == 'matrix':
            kind = 'matrix'
        elif fn.startswith('product_avail_') or data.get('type') == 'product_availability':
            kind = 'product'

        totals[kind] = totals.get(kind, 0) + 1

        if kind_filter and kind != kind_filter:
            continue

        rec_msisdn = ''.join(ch for ch in str(data.get('msisdn', '')) if ch.isdigit())
        if msisdn_filter and rec_msisdn != msisdn_filter:
            continue

        if kind == 'tariff':
            summary = data.get('summary', {}) or {}
            ts = data.get('timestamp', '')
            rec_id = data.get('testId') or fn.replace('.json', '')
            target = data.get('targetName') or data.get('testType', '')
            duration = data.get('duration')
            customer_name = (data.get('customerInfo') or {}).get('name', '')
        else:
            counters = data.get('counters', {}) or {}
            summary = {
                'total':   counters.get('total', 0),
                'passed':  counters.get('pass', counters.get('passed', 0)),
                'failed':  counters.get('fail', counters.get('failed', 0)),
                'skipped': counters.get('skip', counters.get('skipped', 0)),
                'warnings': counters.get('warn', counters.get('warnings', 0)),
            }
            ts = data.get('started_at', '') or data.get('finished_at', '')
            rec_id = data.get('id') or fn.replace('.json', '')
            if kind == 'matrix':
                target = 'Матрица переходов'
            else:
                pid = data.get('product_id')
                pkind = data.get('product_kind', '')
                target = f"Доступность {pkind}={pid}" if pid else 'Доступность продукта'
            duration = data.get('duration')
            customer_name = data.get('customer_name', '')

        if q:
            hay = (str(target or '') + ' ' + str(customer_name or '') + ' ' + rec_msisdn).lower()
            if q not in hay:
                continue

        items.append({
            'kind':      kind,
            'id':        rec_id,
            'ts':        ts,
            'msisdn':    rec_msisdn,
            'customerName': customer_name or '',
            'target':    target,
            'duration':  duration,
            'status':    data.get('status', 'completed' if data.get('finished_at') else 'completed'),
            'summary':   summary,
            'file':      fn,
        })

    items.sort(key=lambda x: x.get('ts', ''), reverse=True)
    total = len(items)
    page_items = items[offset:offset + limit] if offset < total else []
    return jsonify({
        'items': page_items,
        'totals': totals,
        'count': total,
        'offset': offset,
        'limit': limit,
    })


@app.route('/test-history')
def test_history_page():
    resp = _send_template('test_history.html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


@app.route('/api/subscriber/history')
def subscriber_history():
    """Агрегированная история всех прогонов по MSISDN.

    Сканирует test_history/, фильтрует по MSISDN, определяет тип каждого отчёта
    (tariff/QA tester | matrix | product_availability) и возвращает унифицированный
    формат: [{kind, id, ts, msisdn, summary, status}, ...] (свежие первыми, max 80).
    """
    raw_msisdn = (request.args.get('msisdn') or '').strip()
    msisdn = ''.join(ch for ch in raw_msisdn if ch.isdigit())
    if not msisdn:
        return jsonify({'error': 'msisdn required'}), 400

    hist_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_history')
    if not os.path.isdir(hist_dir):
        return jsonify({'msisdn': msisdn, 'items': []})

    items = []
    for fn in os.listdir(hist_dir):
        if not fn.endswith('.json'):
            continue
        filepath = os.path.join(hist_dir, fn)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue

        rec_msisdn = ''.join(ch for ch in str(data.get('msisdn', '')) if ch.isdigit())
        if rec_msisdn != msisdn:
            continue

        # Определяем kind по типу/префиксу файла
        kind = 'tariff'
        if fn.startswith('matrix_') or data.get('type') == 'matrix':
            kind = 'matrix'
        elif fn.startswith('product_avail_') or data.get('type') == 'product_availability':
            kind = 'product'

        # Унифицированный summary
        if kind == 'tariff':
            summary = data.get('summary', {}) or {}
            ts = data.get('timestamp', '')
            rec_id = data.get('testId') or fn.replace('.json', '')
            target = data.get('targetName') or data.get('testType', '')
            duration = data.get('duration')
        else:
            counters = data.get('counters', {}) or {}
            summary = {
                'total':   counters.get('total', 0),
                'passed':  counters.get('pass', counters.get('passed', 0)),
                'failed':  counters.get('fail', counters.get('failed', 0)),
                'skipped': counters.get('skip', counters.get('skipped', 0)),
                'warnings': counters.get('warn', counters.get('warnings', 0)),
            }
            ts = data.get('started_at', '') or data.get('finished_at', '')
            rec_id = data.get('id') or fn.replace('.json', '')
            if kind == 'matrix':
                target = 'Матрица переходов'
            else:
                pid = data.get('product_id')
                pkind = data.get('product_kind', '')
                target = f"Доступность {pkind}={pid}" if pid else 'Доступность продукта'
            duration = data.get('duration')

        items.append({
            'kind':       kind,
            'id':         rec_id,
            'ts':         ts,
            'msisdn':     rec_msisdn,
            'target':     target,
            'duration':   duration,
            'status':     data.get('status', 'completed' if data.get('finished_at') else 'completed'),
            'summary':    summary,
            'file':       fn,
        })

    # Свежие первыми
    items.sort(key=lambda x: x.get('ts', ''), reverse=True)
    return jsonify({'msisdn': msisdn, 'items': items[:80]})


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

        # Доступные тарифы: вызываем ОБА эндпоинта и сливаем.
        #   - GET /availableForChange (без /search) → отдаёт ratePlanCost (activationCost +
        #     subscriptionFeeDetail.amount + periodMeasureUnit). Это источник АП и
        #     стоимости перехода.
        #   - POST /availableForChange/search → даёт более широкий список с recurringFlag,
        #     используется как страховка, если GET не вернул items.
        fees_resp = None
        search_resp = None
        try:
            fees_resp = client.get_available_rateplans_with_fees(sid)
        except Exception as _e:
            print(f"[WARN] GET availableForChange (fees) failed: {_e}")
        try:
            search_resp = client.get_available_rateplans(sid)
        except Exception as _e:
            print(f"[WARN] POST availableForChange/search failed: {_e}")

        fees_items   = (fees_resp   or {}).get("items", []) or []
        search_items = (search_resp or {}).get("items", []) or []

        # Сначала строим map ratePlanId → запись из GET (с fees).
        merged = {}
        for it in fees_items:
            if not isinstance(it, dict):
                continue
            rp_obj = it.get("ratePlan") or {}
            rp_id_v = rp_obj.get("ratePlanId") or it.get("ratePlanId") or it.get("id")
            if rp_id_v is None:
                continue
            cost = it.get("ratePlanCost") or {}
            fee_detail = cost.get("subscriptionFeeDetail") or {}
            merged[str(rp_id_v)] = {
                "ratePlanId":     rp_id_v,
                "name":           rp_obj.get("name") or it.get("name", "N/A"),
                "isArchived":     it.get("isArchived"),
                "recurringFlag":  it.get("recurringFlag"),
                "recurringFee":   fee_detail.get("amount"),
                "activationCost": cost.get("activationCost"),
                "feePeriod":      fee_detail.get("periodMeasureUnit"),
                # Сырой блок — фронт может читать напрямую как fallback
                "ratePlanCost":   cost or None,
            }

        # Дополняем недостающими записями из POST/search (без fees).
        for it in search_items:
            if not isinstance(it, dict):
                continue
            rp_obj = it.get("ratePlan") or {}
            rp_id_v = rp_obj.get("ratePlanId") or it.get("ratePlanId") or it.get("id")
            if rp_id_v is None:
                continue
            key = str(rp_id_v)
            if key in merged:
                # Уже есть из GET — recurringFlag добираем, если в GET его не было
                if merged[key].get("recurringFlag") is None:
                    merged[key]["recurringFlag"] = it.get("recurringFlag")
                continue
            merged[key] = {
                "ratePlanId":     rp_id_v,
                "name":           rp_obj.get("name") or it.get("name", "N/A"),
                "isArchived":     it.get("isArchived"),
                "recurringFlag":  it.get("recurringFlag"),
                "recurringFee":   None,
                "activationCost": None,
                "feePeriod":      None,
            }

        flat_items = list(merged.values())

        # Диагностика — печатаем сколько тарифов получили и сколько с fees.
        with_fees = sum(1 for x in flat_items if x.get("recurringFee") is not None)
        print(f"[tariff/load-customer] sid={sid} → {len(flat_items)} тарифов, "
              f"с АП: {with_fees}, GET items={len(fees_items)}, "
              f"POST items={len(search_items)}")

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
            "_tariffsDiag": {
                "feesItemsCount":   len(fees_items),
                "searchItemsCount": len(search_items),
                "withFees":         with_fees,
                "totalMerged":      len(flat_items),
                "feesRespOk":       fees_resp is not None,
                "searchRespOk":     search_resp is not None,
                "firstFeeItemSample": (fees_items[0] if fees_items else None),
            },
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


@app.route('/api/tariff/change', methods=['POST'])
def tariff_change_direct():
    """Простая прямая смена тарифа без прогона тест-цикла.

    Body: {subscriberId, ratePlanId, [authToken|login+password]}
    Response: {ok, ratePlanOrderId, raw} либо {error}.
    """
    try:
        data = request.get_json() or {}
        sid = data.get("subscriberId")
        rp_id = data.get("ratePlanId")
        if not sid or not rp_id:
            return jsonify({"error": "subscriberId и ratePlanId обязательны"}), 400

        client = _get_client(data)
        resp = client.change_rateplan(sid, rp_id)

        if isinstance(resp, dict) and resp.get("error"):
            return jsonify({"error": f"SBMS вернул {resp.get('error')}", "details": resp}), 502

        order_id = None
        if isinstance(resp, dict):
            order_id = resp.get("ratePlanOrderId") or resp.get("orderId")

        return jsonify({"ok": True, "ratePlanOrderId": order_id, "raw": resp})
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
# QUOTA — все квоты/лимиты абонента (PSIX UCELL_GET_SUBSCRIBER_ALL_QUOTA)
# ============================================================

# Технические название → human-readable + категория.
# Префиксы и фрагменты, которые повторяются в названиях квот SBMS:
#   Q_*  — quota (бонусы/счётчики)
#   S_*  — service (привязанный сервис)
#   Un_  — unlimited (бессрочный/безлимитный счётчик)
#   Soc_Msg / Soc_Net — соц. сети (мессенджеры / Facebook / Instagram)
#   Inet / GPRS / Data — интернет
#   Voice / Min / Call — минуты
#   Sms — SMS
#   Bonus — бонус
# Маппинг достаточен, чтобы не падать на новых квотах: всё, что не распознано —
# уходит в группу `other` с raw-названием, чтобы оператор всё равно видел запись.
# NB: в Python `_` — word-character, поэтому `\b` НЕ работает как граница
# между `Un_` и `Soc` в имени `Q_Un_Soc_Msg`. Используем явный класс
# разделителей `(?:^|[_\-\s])` / `(?:$|[_\-\s])` — он покрывает префиксы
# и суффиксы вроде `Q_`, `_Msg`, `_Un_`.
_SEP_L = r"(?:^|[_\-\s])"
_SEP_R = r"(?:$|[_\-\s])"

_QUOTA_TYPE_RULES = (
    # (категория, regex-паттерн по technical name)
    ("social",   rf"(?i){_SEP_L}soc(?:[_\-]?(?:msg|net|fb|ig|insta|telegram|tg|whats|wa))?{_SEP_R}"),
    ("social",   r"(?i)(facebook|instagram|whatsapp|telegram|tiktok|imo|youtube)"),
    ("internet", rf"(?i){_SEP_L}(inet|net|gprs|data|mb|gb|web|traffic|trf){_SEP_R}"),
    ("voice",    rf"(?i){_SEP_L}(voice|min|call|onnet|offnet|intl|ucell|interconnect){_SEP_R}"),
    ("sms",      rf"(?i){_SEP_L}(sms|msg){_SEP_R}"),
    ("bonus",    r"(?i)(bonus|gift|promo|loyalty|free|extra)"),
)

# Грубое определение единицы измерения по technical name квоты, когда API
# не отдаёт unit явно. Используется только для прогресс-баров и форматирования.
# Порядок важен: «штуки» (соц.сообщения) проверяем ДО общего "msg"-→ sms,
# чтобы Q_Un_Soc_Msg правильно ушёл в pcs (счётчик сообщений), а не в SMS.
_QUOTA_UNIT_RULES = (
    ("pcs", rf"(?i){_SEP_L}soc[_\-]?msg{_SEP_R}"),
    ("MB",  rf"(?i){_SEP_L}(mb|gb|inet|gprs|net|traffic|data|trf){_SEP_R}"),
    ("min", rf"(?i){_SEP_L}(min|voice|call|onnet|offnet|intl|interconnect){_SEP_R}"),
    ("sms", rf"(?i){_SEP_L}(sms|msg){_SEP_R}"),
    ("pcs", rf"(?i){_SEP_L}(count|cnt|piece|times){_SEP_R}"),
)

import re as _quota_re


def _classify_quota(name):
    if not name:
        return "other"
    for cat, pattern in _QUOTA_TYPE_RULES:
        if _quota_re.search(pattern, name):
            return cat
    return "other"


def _quota_unit_from_name(name):
    if not name:
        return ""
    for unit, pattern in _QUOTA_UNIT_RULES:
        if _quota_re.search(pattern, name):
            return unit
    return ""


def _humanize_quota_name(name):
    """Q_Un_Soc_Msg → 'Соц. сети (безлимит)'. Best-effort, fallback — оригинал."""
    if not name:
        return ""
    raw = str(name).strip()
    # Уберём служебный префикс Q_ / S_
    base = _quota_re.sub(r"^[QS]_", "", raw, flags=_quota_re.IGNORECASE)
    parts = [p for p in _quota_re.split(r"[_\-\s]+", base) if p]
    LEX = {
        "un":   "безлимит",
        "soc":  "соц. сети",
        "msg":  "сообщения",
        "net":  "трафик",
        "inet": "интернет",
        "gprs": "интернет",
        "data": "интернет",
        "mb":   "МБ",
        "gb":   "ГБ",
        "min":  "минуты",
        "voice": "голос",
        "call": "звонки",
        "sms":  "SMS",
        "onnet":  "Ucell",
        "offnet": "другие сети",
        "intl":   "межгород",
        "bonus":  "бонус",
        "free":   "бесплатно",
        "promo":  "промо",
        "fb":     "Facebook",
        "ig":     "Instagram",
        "wa":     "WhatsApp",
        "tg":     "Telegram",
    }
    out = []
    for p in parts:
        key = p.lower()
        out.append(LEX.get(key, p))
    return " · ".join(out) or raw


def _norm_quota_date(value):
    """SBMS-дата → ISO-строка либо None, если это «бессрочно».

    SBMS-маркеры «нет даты обнуления / бессрочно»:
      - 01.01.0001 [HH:MM:SS]   — старый формат
      - 0001-01-01[T...]        — ISO-вариант
      - -1--T::                 — формат /PSIX/ucell/ (см. реальный дамп)
      - 9999-* / 2999-*         — бесконечно-в-будущем

    Всё, что не парсится валидно, считаем `None` (UI покажет как
    «бессрочно», если сама квота не expired).
    """
    if value in (None, "", "null"):
        return None
    s = str(value).strip()
    if not s:
        return None
    # Маркеры бессрочности
    if s.startswith("-1") or s.startswith("-"):
        return None
    if s.startswith("01.01.0001") or s.startswith("0001-01-01"):
        return None
    # «Бесконечный» год (>=2999/9999)
    if s[:4] in ("2999", "9999"):
        return None
    # Если в строке вообще нет цифр в году (типа "-1--T::") → бессрочно.
    # Это страховка, если регулярное выражение из SBMS изменится.
    import re as _re
    head = s.split("T")[0].split(" ")[0]
    if not _re.search(r"\d{4}", head):
        return None
    # Парсим в ISO
    try:
        from datetime import datetime
        for fmt in (
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y %H:%M",
            "%d.%m.%Y",
        ):
            try:
                dt = datetime.strptime(s, fmt)
                return dt.strftime("%Y-%m-%dT%H:%M:%S")
            except ValueError:
                continue
    except Exception:
        pass
    return s  # отдадим как есть — фронт справится


def _to_num(v):
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        try:
            return float(str(v).replace(",", ".").strip())
        except Exception:
            return None


# Соответствие «технические» поля → канонические ключи. SBMS может отдавать
# их под разными именами (русские теги, английские, slug), нужно поддержать все.
_QUOTA_FIELD_ALIASES = {
    # Реальные имена полей в ответе SBMS PSIX/ucell (см. дамп):
    #   QTANAME, QTAVALUE, QTACONSUMPTION, QTABALANCE,
    #   QTASTARTDATETIME, QTARSTDATETIME, SRVNAME
    # Остальные алиасы — на случай вариаций между сборками SBMS.
    "name":     ("QTANAME", "QUOTA_NAME", "Наименование", "Name", "QuotaName", "name", "QUOTA",
                 "P_QUOTA_NAME", "QUOTA_TYPE"),
    "service":  ("SRVNAME", "SERVICE_NAME", "Сервис", "Service", "ServiceName", "service",
                 "SERVICE", "P_SERVICE_NAME"),
    "total":    ("QTAVALUE", "VOLUME", "Объем", "Объём", "Volume", "TOTAL_VOLUME", "TotalVolume",
                 "total", "TOTAL", "P_VOLUME", "QUOTA_VOLUME"),
    "used":     ("QTACONSUMPTION", "USED_VOLUME", "Израсходовано", "Used", "UsedVolume", "used",
                 "SPENT", "SPENT_VOLUME", "P_USED_VOLUME", "USED"),
    "rem":      ("QTABALANCE", "REMAINDER", "REMAINING", "Остаток", "Remaining",
                 "REMAINING_VOLUME", "Remaining_Volume", "REMAIN", "rem", "P_REMAINDER", "REST"),
    "act_date": ("QTASTARTDATETIME", "ACTIVATION_DATE", "ДатаАктивации", "Дата активации",
                 "ActivationDate", "DATE_ACTIVATION", "act_date", "START_DATE", "StartDate",
                 "P_ACTIVATION_DATE"),
    "exp_date": ("QTARSTDATETIME", "EXPIRATION_DATE", "ДатаОбнуления", "Дата обнуления",
                 "ExpirationDate", "RESET_DATE", "DATE_EXPIRATION", "exp_date", "END_DATE",
                 "EndDate", "P_EXPIRATION_DATE", "RENEWAL_DATE"),
}


def _pick_quota_field(item, key):
    for alias in _QUOTA_FIELD_ALIASES.get(key, ()):
        if alias in item and item[alias] not in (None, ""):
            return item[alias]
    return None


@app.route("/api/subscriber/quota", methods=["POST"])
def api_subscriber_quota():
    """Все квоты/бонусы/лимиты абонента (PSIX UCELL_GET_SUBSCRIBER_ALL_QUOTA).

    Body: { msisdn: "998...", authToken?: "..." }

    Response:
      {
        items: [
          {
            name, nameHuman, service,
            category: internet|sms|voice|social|bonus|other,
            unit: "MB" | "min" | "sms" | "pcs" | "",
            total, used, remaining, percent,
            activationDate (ISO|null),
            expirationDate (ISO|null),
            unlimited: bool,         // expirationDate == 01.01.0001
            status: active | expiring | exhausted | inactive | unlimited,
            raw: {...}                // оригинал
          },
          ...
        ],
        groups: { internet:[...], sms:[...], voice:[...], social:[...], bonus:[...], other:[...] },
        count, fetchedAt
      }
    """
    try:
        data = request.get_json() or {}
        msisdn = (data.get("msisdn") or "").strip()
        if not msisdn:
            return jsonify({"error": "msisdn required"}), 400

        client = _get_client(data)
        search = client.search_customer(msisdn)
        if not search or (search.get("listInfo") or {}).get("count", 0) == 0:
            return jsonify({"error": "Клиент не найден"}), 404
        sr = search["searchResults"][0]
        sid = sr.get("subscriberId")
        if not sid:
            return jsonify({"error": "subscriberId не получен"}), 502

        raw = client.get_subscriber_all_quota(sid, msisdn=msisdn)
        # Если upstream вернул HTTP-ошибку или мы не смогли распарсить ответ —
        # сразу прокидываем структурированную ошибку, а не молча отдаём пустой
        # список. UI различает причины по полю `errorKind`:
        #   auth_expired       — фронт обнуляет токен и просит перелогиниться
        #   auth_or_permission — то же + подсказка про права
        #   upstream_error     — сетевой/бэкенд-сбой, retry осмысленен
        #   empty_body         — процедура отдала 200, но wrapper-only
        upstream_err = raw.get("error")
        upstream_status = raw.get("http_status")
        error_kind = raw.get("error_kind")
        if upstream_err and not raw.get("items"):
            # 401/403 от upstream → 401 в UI (триггерим SbmsAuth-флоу).
            # 404/прочее → 502 (proxy/gateway-style ошибка).
            ui_status = 401 if error_kind in ("auth_expired",) else 502
            # На «вероятно auth» 404 тоже отдаём 401, чтобы UI авто-обновил сессию.
            if error_kind == "auth_or_permission":
                ui_status = 401
            return jsonify({
                "error":      upstream_err,
                "errorKind":  error_kind,
                "upstream":   upstream_status,
                "attempts":   [_scrub_attempt_for_ui(a) for a in (raw.get("attempts") or [])],
                "imsi":       raw.get("imsi"),
                "msisdn":     raw.get("msisdn"),
                "hint":       (
                    "Откройте модалку входа и переавторизуйтесь — токен SBMS истёк."
                    if ui_status == 401 else
                    "PSIX-эндпоинт не ответил. Проверьте VPN/сеть до sbms.ucell."
                ),
            }), ui_status
        items_raw = raw.get("items") or []

        # ── Нормализация ──────────────────────────────────────
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        soon = now + timedelta(days=3)

        out_items = []
        for it in items_raw:
            if not isinstance(it, dict):
                continue
            name     = _pick_quota_field(it, "name")
            service  = _pick_quota_field(it, "service")
            total    = _to_num(_pick_quota_field(it, "total"))
            used     = _to_num(_pick_quota_field(it, "used"))
            rem      = _to_num(_pick_quota_field(it, "rem"))
            act_iso  = _norm_quota_date(_pick_quota_field(it, "act_date"))
            exp_raw  = _pick_quota_field(it, "exp_date")
            exp_iso  = _norm_quota_date(exp_raw)
            unlimited = exp_iso is None and exp_raw not in (None, "")  # дата была, но это 01.01.0001
            # remaining derive
            if rem is None and (total is not None and used is not None):
                rem = round(total - used, 4)
            if total is None and (used is not None and rem is not None):
                total = round(used + rem, 4)
            percent = None
            if total and total > 0:
                used_eff = used if used is not None else (total - (rem or 0))
                percent = max(0.0, min(100.0, round(used_eff / total * 100.0, 2)))

            # Статус
            if total is not None and total > 0 and rem is not None and rem <= 0:
                status = "exhausted"
            elif unlimited:
                status = "unlimited"
            elif exp_iso:
                try:
                    exp_dt = datetime.strptime(exp_iso, "%Y-%m-%dT%H:%M:%S")
                    if exp_dt < now:
                        status = "inactive"
                    elif exp_dt <= soon:
                        status = "expiring"
                    else:
                        status = "active"
                except Exception:
                    status = "active"
            else:
                status = "active"

            category = _classify_quota(name) if name else "other"
            unit = _quota_unit_from_name(name)

            out_items.append({
                "name":            name,
                "nameHuman":       _humanize_quota_name(name),
                "service":         service,
                "category":        category,
                "unit":            unit,
                "total":           total,
                "used":            used,
                "remaining":       rem,
                "percent":         percent,
                "activationDate":  act_iso,
                "expirationDate":  exp_iso,
                "unlimited":       unlimited,
                "status":          status,
                "raw":             it,
            })

        groups = defaultdict(list)
        for it in out_items:
            groups[it["category"]].append(it)

        # Сортировка: бессрочные внизу, истёкшие в самом низу,
        # внутри — по % использования убывающе (важнее всего «вот-вот закончится»).
        STATUS_ORDER = {"expiring": 0, "active": 1, "exhausted": 2, "unlimited": 3, "inactive": 4}
        out_items.sort(key=lambda it: (
            STATUS_ORDER.get(it["status"], 9),
            -(it["percent"] or 0),
            it["name"] or "",
        ))

        return jsonify({
            "items":      out_items,
            "groups":     {k: groups[k] for k in groups},
            "count":      len(out_items),
            "subscriberId": sid,
            "fetchedAt":  int(time.time()),
            "raw":        {"http_status": raw.get("http_status"), "block": raw.get("block")},
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ============================================================
# CALLS — события абонента (PSIX STRAFF_CALLS_R)
# ============================================================

# Регекспы для финальной проверки на границе API: если в snippet всё-таки
# просочились приметы чужой сессии (32+ символьный токен SBMS, ACCESS_LEVEL_ID,
# и т.п.) — задавим их и здесь. Belt-and-suspenders поверх _psix_safe_snippet.
import re as _re_secret
_SBMS_TOKEN_LIKE_RE = _re_secret.compile(r"AAAI[A-Za-z0-9_.\-]{16,}")  # сигнатура SBMS-токена


def _scrub_attempt_for_ui(attempt):
    """Финальная очистка одной attempt-записи перед отдачей в UI.

    1) Если есть snippet — прогоняем повторно через _redact_snippet
       (на случай новых тегов или будущих рефакторингов).
    2) Дополнительно затираем SBMS-токенные сигнатуры по эвристике.
    3) Никогда не отдаём raw/body/text-поля целиком — только snippet.
    """
    if not isinstance(attempt, dict):
        return attempt
    safe = {}
    for k, v in attempt.items():
        # Запрещаем потенциально сырые поля.
        if k in {"raw", "body", "text", "response", "html", "xml"}:
            continue
        if k == "snippet" and isinstance(v, str):
            v = _redact_snippet(v)
            v = _SBMS_TOKEN_LIKE_RE.sub("***", v)
        safe[k] = v
    return safe


def _scrub_attempts_for_ui(attempts):
    if not isinstance(attempts, list):
        return []
    return [_scrub_attempt_for_ui(a) for a in attempts]


@app.route("/api/calls/list", methods=["POST"])
def api_calls_list():
    """Список вызовов/событий абонента через PSIX STRAFF_CALLS_R.

    Body: {msisdn, authToken? | login+password?, subscriberId?, customerId?,
           startDate?, endDate?, limit?, offset?}
    Возвращает items с укороченным набором полей для UI.
    """
    try:
        data = request.get_json(silent=True) or {}
        msisdn = (data.get("msisdn") or "").strip()
        sid = data.get("subscriberId")
        cid = data.get("customerId")

        if not (sid or msisdn):
            return jsonify({"error": "msisdn или subscriberId обязательны"}), 400

        client = _get_client(data)

        # STRAFF_CALLS_R требует HTTP Basic. Если в payload пришли login/password
        # явно — кладём их в клиент (плюс обновляем кеш для последующих вызовов).
        explicit_login = (data.get("login") or "").strip()
        explicit_password = data.get("password") or ""
        if explicit_login and explicit_password:
            client._login = explicit_login
            client._password = explicit_password
            _auth_cache[explicit_login] = {
                "token": client.token,
                "ts": time.time(),
                "password": explicit_password,
            }

        # Без пароля STRAFF_CALLS_R вернёт пустой wrapper-ответ. Сообщаем UI явно,
        # чтобы он заставил пользователя ввести пароль (sessionStorage мог быть пуст).
        if not getattr(client, "_password", None):
            return jsonify({
                "error": "Для STRAFF_CALLS требуется пароль (HTTP Basic). Введите пароль повторно.",
                "code": "PASSWORD_REQUIRED",
            }), 401

        # Если sid не передан — найдём по msisdn
        if not sid:
            sd = client.search_customer(msisdn)
            if not sd or not sd.get("searchResults"):
                return jsonify({"error": "Клиент не найден"}), 404
            sr = sd["searchResults"][0]
            sid = sr.get("subscriberId")
            cid = cid or sr.get("customerId")

        result = client.get_calls(
            subscriber_id=sid,
            customer_id=cid,
            msisdn=msisdn or None,
            start_date=data.get("startDate"),
            end_date=data.get("endDate"),
            limit=int(data.get("limit") or 500),
            offset=int(data.get("offset") or 0),
            debug=bool(data.get("debug")),
        )

        # Серверная диагностика: при пустом результате логируем, что именно
        # вернул SBMS (без сырых SESSION_ID — но с длиной/именами блоков).
        items_now = list(result.get("items") or [])
        if not items_now:
            atts = result.get("attempts") or []
            summary = []
            for a in atts:
                summary.append(
                    f"{a.get('method','?')} {a.get('path','?')}"
                    f" status={a.get('status')} len={a.get('len')}"
                    f" block={a.get('block')!r} count={a.get('count', 0)}"
                )
            print(
                f"[calls/list] EMPTY result for sid={sid} msisdn={msisdn} "
                f"procedure_ok={result.get('procedure_ok')}\n  "
                + "\n  ".join(summary),
                flush=True,
            )

        # Возвращаем все поля ROW-а (форматы отличаются между процедурами:
        # UCL_OAPI_GET_CHARGE / UCL_GET_RTPL_CHARGE_HIST / etc).
        # UI знает несколько привычных алиасов и сам выбирает что показать.
        items = list(result.get("items") or [])
        return jsonify({
            "items": items,
            "count": len(items),
            "subscriberId": sid,
            "block": result.get("block"),
            "procedureOk": result.get("procedure_ok", False),
            "periodDays": result.get("period_days"),
            "begin": result.get("begin"),
            "end": result.get("end"),
            # Финальный scrub на границе API: даже если внутри клиента
            # что-то поменяется и snippet окажется небезопасным —
            # сюда просочиться не должно.
            "attempts": _scrub_attempts_for_ui(result.get("attempts") or []),
        })
    except AuthRequired:
        raise
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
# COMPARE — параллельный side-by-side двух абонентов
# ============================================================
@app.route('/compare')
def page_compare():
    """Страница сравнения двух абонентов."""
    try:
        return Response(open('templates/compare.html', 'r', encoding='utf-8').read(), mimetype='text/html; charset=utf-8')
    except FileNotFoundError:
        return jsonify({"error": "compare.html не найден"}), 404


@app.route("/api/compare/subscribers", methods=["POST"])
def compare_subscribers():
    """Параллельная загрузка двух абонентов для side-by-side сравнения.

    Body: {msisdn1, msisdn2, authToken?, login?, password?}
    Returns: {a: {customer, balance, packs, services, ratePlans, discounts, ...}, b: {...}}

    Каждый абонент загружается независимо — ошибка одного не блокирует второго.
    Запросы параллельные через ThreadPoolExecutor (max_workers=2).
    """
    try:
        data = request.get_json() or {}
        msisdn1 = (data.get("msisdn1") or "").strip()
        msisdn2 = (data.get("msisdn2") or "").strip()

        if not msisdn1 or not msisdn2:
            return jsonify({"error": "BAD_REQUEST",
                            "message": "msisdn1 и msisdn2 обязательны"}), 400
        if msisdn1 == msisdn2:
            return jsonify({"error": "SAME_MSISDN",
                            "message": "Введите два разных номера"}), 400

        # Один client — токен общий для обоих запросов под admin-сессией.
        # Запросы READ-only, requests.Session под капотом достаточно потокобезопасна
        # для GET/POST с разными URL.
        client = _get_client(data)

        def _safe_items(resp):
            return (resp or {}).get("items", []) if isinstance((resp or {}), dict) else []

        def _safe(fn, *args, **kw):
            """Не падать если конкретный sub-запрос упал — возвращаем None."""
            try:
                return fn(*args, **kw)
            except Exception as ex:
                print(f"[compare] sub-call failed: {fn.__name__}: {ex}")
                return None

        def load_one(msisdn):
            try:
                sd = client.search_customer(msisdn)
                if not sd or (sd.get("listInfo") or {}).get("count", 0) == 0:
                    return {"msisdn": msisdn, "ok": False, "error": "NOT_FOUND",
                            "message": f"Номер {msisdn} не найден"}

                sr = sd["searchResults"][0]
                cid = sr.get("customerId")
                sid = sr.get("subscriberId")
                rate_plan = (sr.get("firstSubscriber") or {}).get("ratePlan") or {}
                customer_obj = sr.get("customer") or {}

                # Параллельные sub-запросы внутри одного абонента
                with ThreadPoolExecutor(max_workers=4) as inner:
                    f_balance     = inner.submit(_safe, client.get_available_balance, cid) if cid else None
                    f_active_p    = inner.submit(_safe, client.get_active_packs, sid)
                    f_avail_p     = inner.submit(_safe, client.get_available_packs, sid)
                    f_active_s    = inner.submit(_safe, client.get_active_services, sid)
                    f_avail_s     = inner.submit(_safe, client.get_available_services, sid)
                    f_avail_rp    = inner.submit(_safe, client.get_available_rateplans, sid)
                    f_rt_disc     = inner.submit(_safe, client.get_rt_discounts, sid)

                    balance_data = f_balance.result() if f_balance else None
                    active_packs = _safe_items(f_active_p.result())
                    avail_packs  = _safe_items(f_avail_p.result())
                    active_svc   = _safe_items(f_active_s.result())
                    avail_svc    = _safe_items(f_avail_s.result())
                    avail_rp_raw = f_avail_rp.result() or {}
                    rt_disc      = _safe_items(f_rt_disc.result())

                # Уплощаем доступные тарифы (структура у SBMS вложенная)
                flat_rps = []
                for it in (avail_rp_raw.get("items", []) or []):
                    if not isinstance(it, dict):
                        continue
                    rp = it.get("ratePlan") or {}
                    flat_rps.append({
                        "ratePlanId": rp.get("ratePlanId") or it.get("ratePlanId") or it.get("id"),
                        "name": rp.get("name") or it.get("name", "N/A"),
                        "isArchived": it.get("isArchived"),
                        "recurringFlag": it.get("recurringFlag"),
                    })

                balance = None
                if balance_data:
                    balance = balance_data.get("availableBalance",
                                               balance_data.get("availableAmount"))

                return {
                    "msisdn": msisdn,
                    "ok": True,
                    "customerId": cid,
                    "subscriberId": sid,
                    "customer": {
                        "name": customer_obj.get("name") or sr.get("name") or "—",
                        "categoryName": customer_obj.get("categoryName") or "",
                        "customerType": customer_obj.get("customerType") or "",
                    },
                    "currentRatePlan": {
                        "ratePlanId": rate_plan.get("ratePlanId"),
                        "name": rate_plan.get("name"),
                    },
                    "balance": balance,
                    "activePacks": active_packs,
                    "availablePacks": avail_packs,
                    "activeServices": active_svc,
                    "availableServices": avail_svc,
                    "availableRatePlans": flat_rps,
                    "discounts": rt_disc,
                }
            except Exception as ex:
                traceback.print_exc()
                return {"msisdn": msisdn, "ok": False, "error": "LOAD_FAILED",
                        "message": str(ex)[:300]}

        # Параллельная загрузка двух абонентов
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_a = pool.submit(load_one, msisdn1)
            f_b = pool.submit(load_one, msisdn2)
            a = f_a.result()
            b = f_b.result()

        return jsonify({"a": a, "b": b})

    except AuthRequired as e:
        return jsonify({"error": "AUTH_REQUIRED", "message": str(e)}), 401
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "COMPARE_FAILED", "message": str(e)[:300]}), 500


# ============================================================
# AVAILABILITY CHECK — bulk-проверка доступности пакетов/услуг под разными ролями
# ============================================================
@app.route('/availability-check')
def page_availability_check():
    resp = _send_template('availability_check.html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


def _norm_input_items(raw):
    """Привести вход {id?, name?} к набору словарей с цельными типами.

    Принимает list[dict] / list[int] / list[str]. Возвращает list[{id:int|None, name:str}].
    """
    out = []
    if not isinstance(raw, list):
        return out
    for x in raw:
        if x is None:
            continue
        if isinstance(x, (int, float)):
            out.append({"id": int(x), "name": ""})
            continue
        if isinstance(x, str):
            s = x.strip()
            if not s:
                continue
            if s.isdigit():
                out.append({"id": int(s), "name": ""})
            else:
                out.append({"id": None, "name": s})
            continue
        if isinstance(x, dict):
            rid = x.get("id")
            try:
                rid = int(rid) if rid not in (None, "") else None
            except (TypeError, ValueError):
                rid = None
            nm = (x.get("name") or "").strip()
            if rid is None and not nm:
                continue
            out.append({"id": rid, "name": nm})
    return out


def _index_products(items, kind):
    """Из ответа API сделать словари по id и lower(name).

    items: list[dict] от get_active_packs / get_available_packs / etc.
    kind: 'pack' | 'service' — определяет ключ ID (packId / serviceId).
    """
    by_id = {}
    by_name = {}
    id_key = "packId" if kind == "pack" else "serviceId"
    for it in items or []:
        if not isinstance(it, dict):
            continue
        pid = it.get(id_key) or it.get("id")
        try:
            pid = int(pid) if pid not in (None, "") else None
        except (TypeError, ValueError):
            pid = None
        nm = (it.get("name") or "").strip()
        if pid is not None:
            by_id[pid] = it
        if nm:
            by_name.setdefault(nm.lower(), it)
    return by_id, by_name


def _check_role_for_subscriber(client, msisdn, packs_in, services_in):
    """Под клиентом client: найти sid, собрать active+avail для packs и services,
    вернуть для каждого input-item статус и raw.

    Возвращает: {currentRatePlan, packs: [{status, id, name, raw}], services: [...], subscriberId}
    """
    sd = client.search_customer(msisdn)
    if not sd or (sd.get("listInfo") or {}).get("count", 0) == 0:
        return {"error": "Номер не найден под этой ролью"}
    sr = sd["searchResults"][0]
    sid = sr.get("subscriberId")
    rp = (sr.get("firstSubscriber") or {}).get("ratePlan") or {}

    def _items(d): return d.get("items", []) if isinstance(d, dict) else []

    active_packs   = _items(client.get_active_packs(sid))
    avail_packs    = _items(client.get_available_packs(sid))
    active_svc     = _items(client.get_active_services(sid))
    avail_svc      = _items(client.get_available_services(sid))

    ap_by_id, ap_by_name = _index_products(active_packs,  "pack")
    av_by_id, av_by_name = _index_products(avail_packs,   "pack")
    as_by_id, as_by_name = _index_products(active_svc,    "service")
    sv_by_id, sv_by_name = _index_products(avail_svc,     "service")

    def _classify(item, active_id_idx, active_name_idx, avail_id_idx, avail_name_idx, kind):
        rid = item.get("id")
        nm = (item.get("name") or "").strip()
        nm_l = nm.lower()
        # 1) ACTIVE — уже подключено
        if rid is not None and rid in active_id_idx:
            raw = active_id_idx[rid]; api_name = (raw.get("name") or "").strip()
            return {"status": "active", "id": rid, "name": api_name or nm,
                    "consistencyOk": (not nm or nm.lower() == api_name.lower()),
                    "matchedBy": "id", "raw": raw}
        if nm and nm_l in active_name_idx:
            raw = active_name_idx[nm_l]
            api_id = raw.get("packId" if kind == "pack" else "serviceId")
            return {"status": "active", "id": api_id, "name": (raw.get("name") or nm),
                    "consistencyOk": (rid is None or rid == api_id),
                    "matchedBy": "name", "raw": raw}
        # 2) AVAILABLE — доступно для подключения
        if rid is not None and rid in avail_id_idx:
            raw = avail_id_idx[rid]; api_name = (raw.get("name") or "").strip()
            return {"status": "available", "id": rid, "name": api_name or nm,
                    "consistencyOk": (not nm or nm.lower() == api_name.lower()),
                    "matchedBy": "id", "raw": raw}
        if nm and nm_l in avail_name_idx:
            raw = avail_name_idx[nm_l]
            api_id = raw.get("packId" if kind == "pack" else "serviceId")
            return {"status": "available", "id": api_id, "name": (raw.get("name") or nm),
                    "consistencyOk": (rid is None or rid == api_id),
                    "matchedBy": "name", "raw": raw}
        # 3) HIDDEN — не возвращается под этой ролью
        return {"status": "hidden", "id": rid, "name": nm, "consistencyOk": True,
                "matchedBy": None, "raw": None}

    return {
        "subscriberId": sid,
        "currentRatePlan": {"ratePlanId": rp.get("ratePlanId"), "name": rp.get("name")},
        "packs":    [_classify(p, ap_by_id, ap_by_name, av_by_id, av_by_name, "pack")    for p in packs_in],
        "services": [_classify(s, as_by_id, as_by_name, sv_by_id, sv_by_name, "service") for s in services_in],
    }


@app.route('/api/availability/check', methods=['POST'])
def api_availability_check():
    """Bulk-проверка доступности пакетов и услуг для номера под несколькими ролями.

    Body:
      {
        "msisdn": "...",
        "packs":    [{id?, name?}, ...],
        "services": [{id?, name?}, ...],
        "includeAdmin": true|false,        # запросить также под текущей админ-сессией
        "roles": [
          {"id": "B2B_OP", "label": "...", "login": "...", "password": "..."},
          ...
        ],
        "adminToken": "..."                # обязательно если includeAdmin или есть roles
      }

    Returns:
      {
        msisdn, totalChecked, byRole: {
          admin:   { currentRatePlan, results },
          B2B_OP:  { ... },
          ...
        },
        warnings
      }

    Никакие подключения/смены тарифа не выполняются — только READ.
    Каждая роль обрабатывается в изолированном SBMSClient.
    """
    from concurrent.futures import ThreadPoolExecutor

    data = request.get_json(silent=True) or {}
    msisdn = (data.get("msisdn") or "").strip()
    if not msisdn:
        return jsonify({"error": "BAD_REQUEST", "message": "msisdn обязателен"}), 400

    packs_in    = _norm_input_items(data.get("packs"))
    services_in = _norm_input_items(data.get("services"))
    if not packs_in and not services_in:
        return jsonify({"error": "BAD_REQUEST",
                        "message": "Список пакетов и услуг пуст — нечего проверять"}), 400

    include_admin = bool(data.get("includeAdmin", True))
    roles = data.get("roles") or []
    admin_token = (data.get("adminToken") or "").strip()

    if (include_admin or roles) and not admin_token:
        return jsonify({"error": "ADMIN_AUTH_REQUIRED",
                        "message": "Сначала войдите в основную (админ) учётку"}), 401

    if not include_admin and not roles:
        return jsonify({"error": "BAD_REQUEST",
                        "message": "Выберите хотя бы одну роль для проверки"}), 400

    # Маскированный лог
    try:
        roles_str = ",".join((r.get("id") or r.get("login") or "?") for r in roles)
    except Exception:
        roles_str = "?"
    print(f"[AVAIL-CHECK] msisdn={msisdn} roles=admin={include_admin}/{roles_str} "
          f"packs={len(packs_in)} services={len(services_in)}")

    by_role = {}
    warnings = []

    def _run_admin():
        try:
            client = _get_client({"authToken": admin_token})
            return ("admin", _check_role_for_subscriber(client, msisdn, packs_in, services_in))
        except AuthRequired:
            return ("admin", {"error": "Сессия админа истекла"})
        except Exception as e:
            return ("admin", {"error": str(e)[:200]})

    def _run_role(role_def):
        rid = (role_def.get("id") or "custom").strip()
        login = (role_def.get("login") or "").strip()
        password = role_def.get("password") or ""
        if not login or not password:
            return (rid, {"error": "Нет login/password"})
        pclient = SBMSClient(BASE_URL, TIMEOUT)
        try:
            pclient.authenticate(login, password)
        except Exception as e:
            return (rid, {"error": f"AUTH_FAILED: {str(e)[:160]}"})
        try:
            return (rid, _check_role_for_subscriber(pclient, msisdn, packs_in, services_in))
        except Exception as e:
            return (rid, {"error": str(e)[:200]})
        finally:
            try: pclient.token = None
            except Exception: pass

    tasks = []
    if include_admin:
        tasks.append(_run_admin)
    for r in roles:
        tasks.append(lambda r=r: _run_role(r))

    if tasks:
        with ThreadPoolExecutor(max_workers=min(len(tasks), 5)) as ex:
            for k, v in ex.map(lambda fn: fn(), tasks):
                if isinstance(v, dict) and v.get("error"):
                    warnings.append(f"{k}: {v['error']}")
                by_role[k] = v

    return jsonify({
        "msisdn": msisdn,
        "totalChecked": len(packs_in) + len(services_in),
        "totalPacks": len(packs_in),
        "totalServices": len(services_in),
        "byRole": by_role,
        "warnings": warnings or None,
    })


@app.route('/api/availability/parse', methods=['POST'])
def api_availability_parse():
    """Алиас на /api/product-availability/parse-tariffs — но в полях возвращает
    {items:[{id,name}]} вместо {tariffs:[...]} для семантической чистоты."""
    if 'file' in request.files:
        f = request.files['file']
        name = f.filename or ""
        content = f.read()
        if not content:
            return jsonify({"error": "Файл пустой"}), 400
        try:
            items = _pa_parser.parse_file(name, content)
        except Exception as e:
            return jsonify({"error": str(e), "filename": name}), 400
        return jsonify({"items": items, "count": len(items), "source": "file", "filename": name})

    data = request.get_json(silent=True) or {}
    text = data.get("text")
    json_data = data.get("json")
    try:
        if text is not None:
            items = _pa_parser.parse_text(text)
            if not items:
                return jsonify({"items": [], "count": 0, "source": "text",
                                "warning": "Не распознано ни одной записи"})
            return jsonify({"items": items, "count": len(items), "source": "text"})
        if json_data is not None:
            items = _pa_parser.parse(json_data=json_data)
            return jsonify({"items": items, "count": len(items), "source": "json"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"error": "Нужен файл (multipart 'file'), либо JSON {text} / {json}"}), 400


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
