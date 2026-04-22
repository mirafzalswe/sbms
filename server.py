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


def _get_client(data=None):
    """Получить авторизованный SBMSClient. Приоритет:
    1. authToken из запроса — используем напрямую (без повторной авторизации)
    2. login из кеша — если токен ещё свежий
    3. login+password — свежая авторизация + кеширование
    """
    data = data or {}
    token = data.get("authToken")
    if token:
        client = SBMSClient(BASE_URL, TIMEOUT)
        client.token = token
        return client

    login = data.get("login") or os.getenv("SBMS_LOGIN", "DBS_CC_OPERATORS_PSO")
    password = data.get("password") or os.getenv("SBMS_PASSWORD", "Ucell2026$$")

    now = time.time()
    if login in _auth_cache and now - _auth_cache[login]["ts"] < TOKEN_TTL:
        client = SBMSClient(BASE_URL, TIMEOUT)
        client.token = _auth_cache[login]["token"]
        return client

    client = SBMSClient(BASE_URL, TIMEOUT)
    client.authenticate(login, password)
    _auth_cache[login] = {"token": client.token, "ts": now}
    return client


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


@app.route('/tme')
def tme_page():
    resp = send_file('tme.html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


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
    """Авторизация: получить токен. Вызывается один раз, токен переиспользуется."""
    try:
        data = request.get_json() or {}
        login = data.get("login") or os.getenv("SBMS_LOGIN", "DBS_CC_OPERATORS_PSO")
        password = data.get("password") or os.getenv("SBMS_PASSWORD", "Ucell2026$$")

        client = SBMSClient(BASE_URL, TIMEOUT)
        client.authenticate(login, password)

        _auth_cache[login] = {"token": client.token, "ts": time.time()}

        return jsonify({"token": client.token, "expiresIn": TOKEN_TTL})
    except Exception as e:
        return jsonify({"error": str(e)}), 401


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
        lc_raw = None
        try:
            lc_data = client.get_lifecycle_actual(sid)
            lc_raw = lc_data
            if lc_data and isinstance(lc_data, dict):
                # Поля-кандидаты: def, lcStateName, name, stateName
                lifecycle_state = (lc_data.get("def")
                                   or lc_data.get("lcStateName")
                                   or lc_data.get("stateName")
                                   or lc_data.get("name"))
                # Если вложенные items/lcState
                if not lifecycle_state:
                    for key in ("items", "lcStates", "lcStatesList"):
                        sub = lc_data.get(key)
                        if isinstance(sub, list) and sub:
                            first = sub[0] if isinstance(sub[0], dict) else {}
                            lifecycle_state = (first.get("def")
                                               or first.get("lcStateName")
                                               or first.get("stateName")
                                               or first.get("name"))
                            if lifecycle_state:
                                break
                # Если ответ — список на верхнем уровне
            elif lc_data and isinstance(lc_data, list) and lc_data:
                first = lc_data[0] if isinstance(lc_data[0], dict) else {}
                lifecycle_state = (first.get("def")
                                   or first.get("lcStateName")
                                   or first.get("name"))
            # Fallback: второй эндпоинт через customerId
            if not lifecycle_state:
                lc_info = client.get_lifecycle_info(cid)
                if lc_info and isinstance(lc_info, dict):
                    lc_items2 = lc_info.get("items") or lc_info.get("searchResults") or []
                    if lc_items2 and isinstance(lc_items2[0], dict):
                        first = lc_items2[0]
                        lifecycle_state = (first.get("def")
                                           or first.get("lcStateName")
                                           or first.get("stateName")
                                           or first.get("name"))
                        if not lifecycle_state:
                            for key in ("lcStatesList", "lcStates"):
                                sub = first.get(key)
                                if isinstance(sub, list) and sub and isinstance(sub[0], dict):
                                    lifecycle_state = (sub[0].get("def")
                                                       or sub[0].get("lcStateName")
                                                       or sub[0].get("name"))
                                    if lifecycle_state:
                                        break
                    if not lifecycle_state:
                        lc_raw = lc_info
        except Exception as e:
            print(f"[WARN] lifecycle error: {e}")

        return jsonify({
            "customer": {
                "customerId": cid,
                "subscriberId": sid,
                "name": (sr.get("customer") or {}).get("name", "N/A"),
                "ratePlanName": rate_plan.get("name", "N/A"),
                "ratePlanId": rp_id,
                "status": ((sr.get("firstSubscriber") or {}).get("status") or {}).get("name", "N/A"),
                "lifecycleState": lifecycle_state,
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
