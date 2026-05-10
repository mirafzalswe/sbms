#!/usr/bin/env python3
"""Раннер прогона «Доступность продукта» по списку тарифов.

Алгоритм для каждого тарифа из списка:
1. admin_client.change_rateplan(subscriber_id, target_rp_id)
2. ждать применения через _wait_order_complete (как в matrix_runner)
3. viewer_client.get_available_packs / get_available_services
4. проверить, есть ли product_id в списке доступных
5. записать результат: pass (доступен), fail (недоступен), blocked (admin не смог сменить)

Поддерживаются:
- SSE-стрим прогресса через progress_cb
- abort_event для корректной остановки между тарифами
- инкрементальный сейв отчёта на диск (test_history/product_avail_<id>.json)

ВАЖНО: возврат тарифа на исходный значение НЕ выполняется — пользователь
явно подтвердил, что это не нужно.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from typing import Callable

# Переиспользуем утилиту ожидания смены тарифа из матричного раннера —
# логика идентична, дублировать смысла нет.
from matrix_runner import _wait_order_complete, AbortRun  # noqa: F401

HISTORY_DIR = os.path.join(os.path.dirname(__file__), "test_history")
os.makedirs(HISTORY_DIR, exist_ok=True)

# Отчёты этого раннера префиксуем product_avail_, чтобы не путать с matrix_*.
_REPORT_PREFIX = "product_avail_"

# Retry-параметры для проверки доступности после смены тарифа.
# Смена тарифа асинхронная: ratePlan меняется быстро (~1s через searchBase),
# но каталог availableForActivate пересчитывается биллингом с задержкой 3-15s.
# 4 попытки с интервалом 3s = до 9 доп. секунд ожидания на тариф (после первой проверки).
MAX_AVAIL_RETRIES = 3
AVAIL_RETRY_DELAY = 3.0


def _items(resp) -> list:
    if not isinstance(resp, dict):
        return []
    its = resp.get("items") or resp.get("data") or []
    return its if isinstance(its, list) else []


# Поля, в которых SBMS может хранить ID пакета/услуги. У одного и того же
# пакета параллельно живут packId (короткий, для активации) и productId
# (длинный, фигурирует в rtDiscounts/nextCharges) — это РАЗНЫЕ числа,
# поэтому пользователь может ввести любой из них и должно сработать.
_PACK_ID_FIELDS    = ("packId", "productId", "subscriberPackId", "id", "packInstanceId")
_SERVICE_ID_FIELDS = ("serviceId", "productId", "subscriberServiceId", "id", "serviceInstanceId")


def _to_int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError, AttributeError):
        return None


def _collect_ids(item: dict, fields: tuple) -> dict[int, str]:
    """Собрать все ID-поля item (включая вложенные .pack.* / .service.*) → {id: field_name}."""
    out: dict[int, str] = {}
    if not isinstance(item, dict):
        return out
    nested_keys = ("pack", "service", "product")
    sources = [(item, "")] + [
        (item.get(k), f"{k}.") for k in nested_keys if isinstance(item.get(k), dict)
    ]
    for src, prefix in sources:
        for f in fields:
            if f in src:
                ival = _to_int(src.get(f))
                if ival is not None and ival not in out:
                    out[ival] = prefix + f
    return out


def _extract_index(resp, fields: tuple) -> tuple[dict[int, dict], list[dict]]:
    """Вернуть (index_by_id, items_summary).

    index_by_id: {id: {item: <raw>, matched_field: str, name, fee, status}}.
      Один и тот же item может быть проиндексирован несколько раз — по
      packId, по productId и т.п. Пользователь введёт любой из них — найдём.
    items_summary: первые 3 item'а (с ключами/значениями) — для диагностики
      в отчёте, если пользователь не нашёл свой продукт.
    """
    index: dict[int, dict] = {}
    items = _items(resp)
    for it in items:
        if not isinstance(it, dict):
            continue
        ids = _collect_ids(it, fields)
        if not ids:
            continue
        nested = it.get("pack") or it.get("service") or {}
        meta = {
            "name":   it.get("name") or nested.get("name") or "",
            "fee":    it.get("fee")  if it.get("fee")  is not None else nested.get("fee"),
            "status": (it.get("status") or {}).get("name") if isinstance(it.get("status"), dict) else None,
        }
        for ival, field in ids.items():
            if ival in index:
                continue  # первое попадание — оно главное
            index[ival] = {**meta, "matched_field": field}
    summary = [
        {"keys": list(it.keys())[:20],
         "ids":  _collect_ids(it, fields),
         "name": it.get("name") or ((it.get("pack") or it.get("service") or {}).get("name"))}
        for it in items[:3] if isinstance(it, dict)
    ]
    return index, summary


def _emit(cb: Callable | None, **payload):
    if cb:
        try:
            cb(payload)
        except Exception:
            pass


def _report_path(report_id: str) -> str:
    return os.path.join(HISTORY_DIR, f"{_REPORT_PREFIX}{report_id}.json")


def _save_report_atomic(report: dict) -> str | None:
    rid = report.get("id")
    if not rid:
        return None
    path = _report_path(rid)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return path
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return None


def run_product_availability(
    *,
    msisdn: str,
    product_id: int,
    product_kind: str,           # 'pack' | 'service'
    tariffs: list[dict],          # [{id, name}]
    admin_client,
    viewer_client,
    only_ids: list[int] | None = None,
    wait_after_change: float = 5.0,
    order_timeout: int = 60,
    progress_cb: Callable | None = None,
    resume_from: str | None = None,
    abort_event=None,
) -> dict:
    """Полный прогон. Возвращает report dict.

    Параметры:
    - msisdn — номер абонента, на котором гоняем (один!).
    - product_id — ID пакета/услуги, доступность которого проверяем.
    - product_kind — 'pack' (вызывается get_available_packs) или 'service'
      (get_available_services). Тип задаётся пользователем явно — один и тот же
      числовой ID может означать разные сущности.
    - tariffs — список тарифов [{id, name}], для каждого по очереди делается
      смена тарифа + проверка доступности.
    - only_ids — подмножество тарифов из списка (по id), которое нужно прогнать.
    - resume_from — id отчёта; если задан — пропускаем уже сделанные тарифы.
    - abort_event — threading.Event(); set() = корректно остановиться между
      тарифами (внутри одной смены прерывать нельзя — оставит абонента
      на полу-переключённом тарифе).
    """
    if product_kind not in ("pack", "service"):
        raise ValueError(f"product_kind должен быть 'pack' или 'service', получено: {product_kind!r}")
    try:
        product_id = int(product_id)
    except (TypeError, ValueError) as e:
        raise ValueError(f"product_id должен быть числом, получено: {product_id!r}") from e

    # ---- Resume vs new report ---------------------------------------------
    prior_results: list[dict] = []
    prior_counters = {"pass": 0, "fail": 0, "blocked": 0}
    done_tariff_ids: set[int] = set()

    if resume_from:
        prior = get_product_avail_report(resume_from)
        if not prior:
            raise RuntimeError(f"Отчёт {resume_from} не найден — невозможно продолжить")
        if (prior.get("msisdn") or "") != msisdn:
            raise RuntimeError(
                f"MSISDN отчёта ({prior.get('msisdn')}) не совпадает с текущим ({msisdn}) — "
                "продолжение возможно только на том же номере"
            )
        if int(prior.get("product_id") or 0) != product_id or (prior.get("product_kind") or "") != product_kind:
            raise RuntimeError(
                "Параметры продукта отчёта не совпадают с текущими — продолжение невозможно"
            )
        report_id = prior["id"]
        started_at = prior.get("started_at") or (datetime.utcnow().isoformat() + "Z")
        prior_results = list(prior.get("results") or [])
        for r in prior_results:
            try:
                done_tariff_ids.add(int(r.get("tariff_id")))
            except (TypeError, ValueError):
                pass
        prior_counters = dict(prior.get("counters") or prior_counters)
        for k in ("pass", "fail", "blocked"):
            prior_counters.setdefault(k, 0)
    else:
        report_id = uuid.uuid4().hex[:12]
        started_at = datetime.utcnow().isoformat() + "Z"

    # ---- Resolve subscriber -----------------------------------------------
    search = admin_client.search_customer(msisdn) or {}
    sr = (search.get("searchResults") or [None])[0]
    if not sr:
        raise RuntimeError(f"Абонент с MSISDN {msisdn} не найден (admin)")
    subscriber_id = sr.get("subscriberId")
    customer_id = sr.get("customerId")
    initial_rateplan = ((sr.get("firstSubscriber") or {}).get("ratePlan") or {}).get("ratePlanId")
    customer_name = sr.get("name") or sr.get("customerName") or ""
    if not subscriber_id:
        raise RuntimeError("Не удалось определить subscriberId абонента")

    # ---- Filter list -------------------------------------------------------
    queue: list[dict] = list(tariffs or [])
    if only_ids:
        only_set = {int(x) for x in only_ids}
        queue = [t for t in queue if int(t.get("id", -1)) in only_set]

    skipped_done = 0
    if done_tariff_ids:
        before = len(queue)
        queue = [t for t in queue if int(t.get("id", -1)) not in done_tariff_ids]
        skipped_done = before - len(queue)

    total = len(queue)
    _emit(progress_cb, event="start",
          total=total, subscriber_id=subscriber_id, customer_name=customer_name,
          initial_rateplan=initial_rateplan,
          product_id=product_id, product_kind=product_kind,
          report_id=report_id, resumed=bool(resume_from), skipped_done=skipped_done,
          done_tariff_ids=sorted(done_tariff_ids))

    results: list[dict] = list(prior_results)
    counters = dict(prior_counters)
    t0 = time.time()

    def _is_aborted() -> bool:
        try:
            return bool(abort_event is not None and abort_event.is_set())
        except Exception:
            return False

    def _build_partial_report(*, finished: bool) -> dict:
        return {
            "id": report_id,
            "type": "product_availability",
            "msisdn": msisdn,
            "subscriber_id": subscriber_id,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "initial_rateplan": initial_rateplan,
            "product_id": product_id,
            "product_kind": product_kind,
            "started_at": started_at,
            "finished_at": (datetime.utcnow().isoformat() + "Z") if finished else None,
            "duration": round(time.time() - t0, 2),
            "tariffs_total": len(results) + len(queue),
            "results": results,
            "counters": counters,
            "status": "completed" if finished else "in_progress",
            "resumable": not finished,
        }

    for idx, tariff in enumerate(queue):
        # Проверка abort ПЕРЕД сменой тарифа.
        if _is_aborted():
            partial = _build_partial_report(finished=False)
            saved = _save_report_atomic(partial)
            if saved:
                partial["_saved_path"] = saved
            _emit(progress_cb, event="aborted", report=partial,
                  reason="user_stop", processed=len(results) - len(prior_results),
                  remaining=len(queue) - idx)
            return partial

        try:
            tariff_id = int(tariff["id"])
        except (KeyError, TypeError, ValueError):
            # битый элемент — пропускаем, фиксируем как blocked, чтобы не висел
            results.append({
                "tariff_id": None,
                "tariff_name": (tariff or {}).get("name") or "",
                "blocked": True,
                "block_reason": "invalid tariff id",
                "status": "blocked",
            })
            counters["blocked"] += 1
            continue
        tariff_name = tariff.get("name") or str(tariff_id)
        row_started = time.time()

        _emit(progress_cb, event="row_start", index=idx,
              tariff_id=tariff_id, tariff_name=tariff_name)

        # 1) Сменить тариф под админом
        change_resp = admin_client.change_rateplan(subscriber_id, tariff_id)
        order_id = None
        if isinstance(change_resp, dict):
            order_id = (
                change_resp.get("ratePlanOrderId")
                or change_resp.get("orderId")
                or change_resp.get("id")
            )

        change_ok = bool(order_id) and "error" not in (change_resp or {})
        order_status = None
        wait_elapsed = 0.0
        wait_debug: dict = {}
        if change_ok:
            ok, order_status, wait_elapsed, wait_debug = _wait_order_complete(
                admin_client, subscriber_id, order_id,
                target_rp_id=tariff_id, msisdn=msisdn,
                timeout_sec=order_timeout, poll_sec=2,
            )
            change_ok = ok
        else:
            wait_debug = {
                "order_id_from_resp": str(order_id),
                "change_resp_keys": list((change_resp or {}).keys()),
            }

        if not change_ok:
            row_result = {
                "tariff_id": tariff_id,
                "tariff_name": tariff_name,
                "blocked": True,
                "block_reason": f"change failed (status={order_status or 'no_order_id'})",
                "change_response": change_resp,
                "wait_debug": wait_debug,
                "status": "blocked",
                "duration": round(time.time() - row_started, 2),
            }
            results.append(row_result)
            counters["blocked"] += 1
            _save_report_atomic(_build_partial_report(finished=False))
            _emit(progress_cb, event="row_done", index=idx, row=row_result,
                  counters=counters, elapsed=round(time.time() - t0, 1))
            continue

        # 2) Пауза перед первой проверкой
        if wait_after_change > 0:
            time.sleep(wait_after_change)

        # 3) Запросить ДОСТУПНЫЕ ДЛЯ АКТИВАЦИИ продукты под viewer'ом — С RETRY.
        # Используем /PSAPI/v1/bis-base/subscribers/{sid}/packs/availableForActivate
        # (или .../services/availableForActivate) — это каталог того, что viewer
        # МОЖЕТ активировать на абоненте под текущим тарифом. Семантика теста:
        # «продукт доступен для подключения под тарифом X».
        #
        # ВАЖНО: смена тарифа асинхронная. Даже если searchBase.ratePlanId уже
        # показывает новый тариф, биллинг пересчитывает каталог availableForActivate
        # с задержкой 3-15 секунд. Поэтому делаем retry-цикл: если продукта ещё
        # нет — ждём и пробуем снова. Это устраняет ложные fail когда order висит
        # «В ПРОЦЕССЕ ПОДКЛЮЧЕНИЯ».
        fields = _PACK_ID_FIELDS if product_kind == "pack" else _SERVICE_ID_FIELDS
        viewer_resp = None
        viewer_err = None
        index: dict = {}
        items_sample: list = []
        attempts = 0
        for attempt in range(MAX_AVAIL_RETRIES + 1):
            attempts = attempt + 1
            try:
                if product_kind == "pack":
                    viewer_resp = viewer_client.get_available_packs(subscriber_id)
                else:
                    viewer_resp = viewer_client.get_available_services(subscriber_id)
                viewer_err = None
            except Exception as e:
                viewer_err = str(e)
            index, items_sample = _extract_index(viewer_resp, fields)
            if product_id in index:
                break  # нашли — больше ждать не надо
            if attempt < MAX_AVAIL_RETRIES:
                time.sleep(AVAIL_RETRY_DELAY)

        is_available = product_id in index
        product_info = index.get(product_id, {}) if is_available else {}

        status = "pass" if is_available else "fail"
        if status == "pass":
            counters["pass"] += 1
        else:
            counters["fail"] += 1

        items_count = len(_items(viewer_resp))
        # Диагностический хинт — самая частая причина fail с пустым ответом:
        # viewer-учётка просто не видит каталог availableForActivate этого абонента (ACL).
        diag_hint = None
        if not is_available and items_count == 0 and not viewer_err:
            diag_hint = (
                "viewer вернул пустой каталог availableForActivate (count=0). "
                f"Скорее всего у учётки '{getattr(viewer_client, '_login', '?')}' "
                "нет прав видеть доступные для активации продукты этого абонента. "
                "Попробуйте использовать ту же учётку, что и admin (через карточку «✏ Вручную»)."
            )

        row_result = {
            "tariff_id": tariff_id,
            "tariff_name": tariff_name,
            "blocked": False,
            "order_id": order_id,
            "order_status": order_status,
            "wait_order_sec": round(wait_elapsed, 2),
            "viewer_login": getattr(viewer_client, "_login", None),
            "available_count": items_count,
            "available_id_count": len(index),
            "is_available": is_available,
            "check_attempts": attempts,
            "matched_field": product_info.get("matched_field"),
            "status": status,
            "product_name":   product_info.get("name"),
            "product_fee":    product_info.get("fee"),
            "product_status": product_info.get("status"),
            "viewer_error": viewer_err,
            "diag_hint": diag_hint,
            # Диагностика: первые 3 item'а — какие ID-поля у них есть.
            # Помогает понять, в каком формате SBMS отдаёт ID на этом env.
            "items_sample": items_sample if not is_available else None,
            "duration": round(time.time() - row_started, 2),
        }
        results.append(row_result)
        _save_report_atomic(_build_partial_report(finished=False))
        _emit(progress_cb, event="row_done", index=idx, row=row_result,
              counters=counters, elapsed=round(time.time() - t0, 1))

    report = _build_partial_report(finished=True)
    saved = _save_report_atomic(report)
    if saved:
        report["_saved_path"] = saved
    else:
        report["_save_error"] = "atomic write failed"

    _emit(progress_cb, event="done", report=report)
    return report


def list_product_avail_reports() -> list[dict]:
    out = []
    if not os.path.isdir(HISTORY_DIR):
        return out
    for f in sorted(os.listdir(HISTORY_DIR), reverse=True):
        if not (f.startswith(_REPORT_PREFIX) and f.endswith(".json")):
            continue
        p = os.path.join(HISTORY_DIR, f)
        try:
            with open(p, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            out.append({
                "id": d.get("id"),
                "msisdn": d.get("msisdn"),
                "product_id": d.get("product_id"),
                "product_kind": d.get("product_kind"),
                "started_at": d.get("started_at"),
                "finished_at": d.get("finished_at"),
                "duration": d.get("duration"),
                "counters": d.get("counters"),
                "tariffs_total": d.get("tariffs_total"),
                "status": d.get("status") or ("completed" if d.get("finished_at") else "in_progress"),
                "resumable": bool(d.get("resumable")),
            })
        except Exception:
            continue
    return out


def get_product_avail_report(report_id: str) -> dict | None:
    p = _report_path(report_id)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None
