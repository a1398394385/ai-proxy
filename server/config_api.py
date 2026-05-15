"""server 包 — 模型配置 API（upstreams/models/routes）。"""

import json
import re
import socket
import ssl
import sqlite3
import time
import http.client
from pathlib import Path
from urllib.parse import urlparse

from .common import json_response, _read_json, config_db, _reload_proxies, get_port
from proxy.config_manager import Migrations


# ─── 上游检测 ───


def _test_upstream_connectivity(upstream: dict) -> dict:
    """测试上游连通性：TCP + HTTP GET。"""
    parsed = urlparse(upstream["base_url"])
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    result = {"reachable": False, "http_status": None, "latency_ms": 0}

    start = time.time()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        sock.connect((host, port))
        result["latency_ms"] = int((time.time() - start) * 1000)
    except (socket.timeout, OSError) as e:
        result["error"] = str(e)
        return result
    finally:
        sock.close()

    http_path = parsed.path.rstrip("/") + "/" if parsed.path else "/"
    start = time.time()
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", http_path)
        resp = conn.getresponse()
        result["reachable"] = True
        result["http_status"] = resp.status
        result["latency_ms"] = int((time.time() - start) * 1000)
        if resp.status == 401:
            result["warning"] = "返回 401，API Key 可能无效，但网络可达"
        if resp.status == 404:
            result["warning"] = "返回 404，端点可能不存在，但服务存活"
    except Exception as e:
        result["error"] = str(e)
    finally:
        conn.close()

    return result


def _call_upstream_models(upstream: dict) -> dict:
    """调用上游 /v1/models 获取可用模型列表。"""
    parsed = urlparse(upstream["base_url"])
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    scheme = parsed.scheme

    base_path = parsed.path.rstrip("/")
    candidate_paths = [base_path + "/v1/models", base_path + "/models"]

    headers = {"Accept": "application/json"}
    api_key = upstream.get("api_key", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    ssl_context = None
    if scheme == "https" and not upstream.get("ssl_verify", True):
        ssl_context = ssl._create_unverified_context()

    result = {"reachable": False, "model_ids": [], "error": None}

    for request_path in candidate_paths:
        try:
            if scheme == "https":
                conn = http.client.HTTPSConnection(
                    host, port, timeout=15, context=ssl_context
                )
            else:
                conn = http.client.HTTPConnection(host, port, timeout=15)

            try:
                conn.request("GET", request_path, headers=headers)
                resp = conn.getresponse()
                raw_body = resp.read()
                status = resp.status

                if 200 <= status < 300:
                    result["reachable"] = True
                    try:
                        data = json.loads(raw_body)
                    except json.JSONDecodeError as e:
                        result["error"] = f"上游返回了无效 JSON: {e}"
                        return result

                    model_list = data.get("data", [])
                    model_ids = [
                        item["id"]
                        for item in model_list
                        if isinstance(item, dict) and item.get("id")
                    ]
                    result["model_ids"] = model_ids
                    return result
                elif status in (404, 405):
                    continue
                else:
                    result["reachable"] = True
                    result["error"] = f"HTTP {status}"
                    return result
            finally:
                conn.close()
        except (socket.gaierror, socket.timeout, OSError) as e:
            result["error"] = str(e)
            return result

    result["reachable"] = True
    result["error"] = "所有候选路径均返回 404/405"

    return result


# ─── routes/agent-routes 共享逻辑 ───

_ROUTE_DEFAULTS = {
    "routes": "responses",
    "agent-routes": "chat_completions",
}


def _list_routes(kind, request_type, handler):
    with config_db() as db:
        list_fn = db.list_routes if kind == "routes" else db.list_agent_routes
        routes = list_fn(request_type=request_type)
    json_response(handler, {"routes": routes})
    return True


def _get_route_detail(kind, route_id, handler):
    with config_db() as db:
        get_fn = db.get_route if kind == "routes" else db.get_agent_route
        route = get_fn(route_id)
    if route:
        json_response(handler, route)
    else:
        json_response(handler, {"error": "Not found"}, 404)
    return True


def _add_route(kind, data, handler):
    request_type = data.get("request_type", _ROUTE_DEFAULTS[kind])
    if request_type not in ("responses", "messages", "chat_completions"):
        json_response(
            handler,
            {"error": "request_type must be one of: responses, messages, chat_completions"},
            400,
        )
        return True
    with config_db() as db:
        model = db.get_model(data["target_model_id"])
        if not model:
            json_response(handler, {"error": "target_model_id 不存在"}, 400)
            return True
        if not model.get("upstream_active"):
            json_response(handler, {"error": "目标模型所属上游已禁用"}, 400)
            return True
        try:
            add_fn = db.add_route if kind == "routes" else db.add_agent_route
            rid = add_fn(data)
        except (sqlite3.IntegrityError, ValueError) as e:
            json_response(handler, {"error": str(e)}, 409)
            return True
    _reload_proxies()
    json_response(handler, {"id": rid, "message": "Created"}, 201)
    return True


def _update_route(kind, route_id, data, handler):
    with config_db() as db:
        try:
            update_fn = db.update_route if kind == "routes" else db.update_agent_route
            update_fn(route_id, data)
        except (sqlite3.IntegrityError, ValueError) as e:
            json_response(handler, {"error": str(e)}, 409)
            return True
    _reload_proxies()
    json_response(handler, {"message": "Updated"})
    return True


def _delete_route(kind, route_id, handler):
    with config_db() as db:
        get_fn = db.get_route if kind == "routes" else db.get_agent_route
        route = get_fn(route_id)
        if not route:
            json_response(handler, {"error": "Not found"}, 404)
            return True
        if kind == "routes" and route["source"] == "*":
            routes = db.list_routes()
            star_count = sum(1 for r in routes if r["source"] == "*")
            if star_count <= 1:
                json_response(
                    handler, {"error": "不能删除最后一条 * fallback 路由"}, 409
                )
                return True
        try:
            delete_fn = db.delete_route if kind == "routes" else db.delete_agent_route
            delete_fn(route_id)
        except sqlite3.IntegrityError as e:
            json_response(handler, {"error": str(e)}, 409)
            return True
    _reload_proxies()
    json_response(handler, {"message": "Deleted"})
    return True


# ─── GET ───


def handle_get(path, qs, handler) -> bool:
    if path == "/api/upstreams":
        with config_db() as db:
            upstreams = db.list_upstreams()
        json_response(handler, {"upstreams": upstreams})
        return True

    m = re.match(r"/api/upstreams/([^/]+)$", path)
    if m:
        with config_db() as db:
            u = db.get_upstream(int(m.group(1)))
        if u:
            json_response(handler, u)
        else:
            json_response(handler, {"error": "Not found"}, 404)
        return True

    if path == "/api/models":
        upstream_filter = qs.get("upstream_id", [None])[0]
        with config_db() as db:
            models = db.list_models(upstream_id=upstream_filter)
        json_response(handler, {"models": models})
        return True

    m = re.match(r"/api/models/(\d+)$", path)
    if m:
        with config_db() as db:
            model = db.get_model(int(m.group(1)))
        if model:
            json_response(handler, model)
        else:
            json_response(handler, {"error": "Not found"}, 404)
        return True

    if path == "/api/routes":
        request_type = qs.get("request_type", [None])[0]
        return _list_routes("routes", request_type, handler)

    m = re.match(r"/api/routes/(\d+)$", path)
    if m:
        return _get_route_detail("routes", int(m.group(1)), handler)
    if path == "/api/agent-routes":
        request_type = qs.get("request_type", [None])[0]
        return _list_routes("agent-routes", request_type, handler)

    m = re.match(r"/api/agent-routes/(\d+)$", path)
    if m:
        return _get_route_detail("agent-routes", int(m.group(1)), handler)


    if path == "/api/config/status":
        with config_db() as db:
            counts = db.get_counts()
        proxy_reachable = False
        try:
            proxy_port = get_port("codex_proxy", 48743)
            conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=2)
            conn.request("GET", "/health")
            resp = conn.getresponse()
            proxy_reachable = resp.status == 200
            conn.close()
        except Exception:
            pass
        json_response(handler, {"proxy_reachable": proxy_reachable, "config_db": counts})
        return True

    return False


# ─── POST ───


def handle_post(path, handler) -> bool:
    if path == "/api/migrate":
        try:
            from proxy.paths import DATA_DB
            result = Migrations(DATA_DB).migrate()
            json_response(handler, result)
        except Exception as e:
            json_response(handler, {"error": str(e)}, 500)
        return True

    if path == "/api/upstreams":
        data = _read_json(handler)
        if not data:
            return True
        try:
            with config_db() as db:
                uid = db.add_upstream(data)
        except sqlite3.IntegrityError as e:
            json_response(handler, {"error": str(e)}, 409)
            return True
        _reload_proxies()
        json_response(handler, {"id": uid, "message": "Created"}, 201)
        return True

    test_m = re.match(r"/api/upstreams/([^/]+)/test$", path)
    if test_m:
        uid = int(test_m.group(1))
        with config_db() as db:
            u = db.get_upstream(uid)
        if not u:
            json_response(handler, {"error": "Not found"}, 404)
            return True
        result = _test_upstream_connectivity(u)
        json_response(handler, result)
        return True

    detect_m = re.match(r"/api/upstreams/([^/]+)/detect-models$", path)
    if detect_m:
        uid = int(detect_m.group(1))
        with config_db() as db:
            u = db.get_upstream(uid)
        if not u:
            json_response(handler, {"error": "Not found"}, 404)
            return True
        if not u.get("is_active"):
            json_response(handler, {"error": "上游已禁用"}, 400)
            return True
        detect_result = _call_upstream_models(u)
        with config_db() as db:
            existing_models = [m["name"] for m in db.list_models(upstream_id=uid)]
        all_ids = detect_result.get("model_ids", [])
        discovered = [mid for mid in all_ids if mid not in existing_models]
        json_response(
            handler,
            {
                "reachable": detect_result["reachable"],
                "discovered": discovered,
                "existing": [mid for mid in all_ids if mid in existing_models],
                "error": detect_result.get("error"),
            },
        )
        return True

    bulk_m = re.match(r"/api/upstreams/([^/]+)/models/bulk$", path)
    if bulk_m:
        uid = int(bulk_m.group(1))
        data = _read_json(handler)
        if not data:
            return True
        models = data.get("models")
        if not isinstance(models, list):
            json_response(handler, {"error": "缺少 models 数组"}, 400)
            return True
        with config_db() as db:
            u = db.get_upstream(uid)
            if not u:
                json_response(handler, {"error": "Not found"}, 404)
                return True
            result = db.add_models_bulk(uid, models)
        status = 201 if result["added"] > 0 else 200
        json_response(handler, result, status)
        return True

    if path == "/api/models":
        data = _read_json(handler)
        if not data:
            return True
        try:
            with config_db() as db:
                mid = db.add_model(data)
        except sqlite3.IntegrityError as e:
            json_response(handler, {"error": str(e)}, 409)
            return True
        _reload_proxies()
        json_response(handler, {"id": mid, "message": "Created"}, 201)
        return True

    if path == "/api/routes":
        data = _read_json(handler)
        if not data:
            return True
        return _add_route("routes", data, handler)

    if path == "/api/agent-routes":
        data = _read_json(handler)
        if not data:
            return True
        return _add_route("agent-routes", data, handler)


    if path == "/api/config/reload":
        result = {}
        try:
            proxy_port = get_port("codex_proxy", 48743)
            conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=5)
            conn.request("POST", "/admin/reload")
            resp = conn.getresponse()
            body = json.loads(resp.read())
            conn.close()
            result["proxy"] = body
        except Exception as e:
            result["proxy"] = {"status": "error", "message": str(e)}
        json_response(handler, result)
        return True

    return False


# ─── PUT ───


def handle_put(path, handler) -> bool:
    m = re.match(r"/api/upstreams/([^/]+)$", path)
    if m:
        data = _read_json(handler)
        if not data:
            return True
        try:
            with config_db() as db:
                db.update_upstream(int(m.group(1)), data)
        except sqlite3.IntegrityError as e:
            json_response(handler, {"error": str(e)}, 409)
            return True
        _reload_proxies()
        json_response(handler, {"message": "Updated"})
        return True

    m = re.match(r"/api/models/(\d+)$", path)
    if m:
        data = _read_json(handler)
        if not data:
            return True
        try:
            with config_db() as db:
                db.update_model(int(m.group(1)), data)
        except sqlite3.IntegrityError as e:
            json_response(handler, {"error": str(e)}, 409)
            return True
        _reload_proxies()
        json_response(handler, {"message": "Updated"})
        return True
    m = re.match(r"/api/agent-routes/(\d+)$", path)
    if m:
        data = _read_json(handler)
        if not data:
            return True
        return _update_route("agent-routes", int(m.group(1)), data, handler)

    m = re.match(r"/api/routes/(\d+)$", path)
    if m:
        data = _read_json(handler)
        if not data:
            return True
        return _update_route("routes", int(m.group(1)), data, handler)
    return False


# ─── DELETE ───


def handle_delete(path, handler) -> bool:
    m = re.match(r"/api/upstreams/([^/]+)$", path)
    if m:
        uid = int(m.group(1))
        with config_db() as db:
            u = db.get_upstream(uid)
            if not u:
                json_response(handler, {"error": "Not found"}, 404)
                return True
            active_routes = db.upstream_active_routes(uid)
            if active_routes:
                json_response(
                    handler,
                    {
                        "error": "该上游有路由正在使用，请先在路由管理中解绑",
                        "referenced_routes": active_routes,
                    },
                    409,
                )
                return True
            db.delete_upstream_with_models(uid)
        _reload_proxies()
        json_response(handler, {"message": "Deleted"})
        return True

    m = re.match(r"/api/models/(\d+)$", path)
    if m:
        mid = int(m.group(1))
        with config_db() as db:
            result = db.delete_model(mid)
        if "error" in result:
            json_response(handler, result, 409)
            return True
        _reload_proxies()
        json_response(handler, {"message": "Deleted"})
        return True

    m = re.match(r"/api/routes/(\d+)$", path)
    if m:
        return _delete_route("routes", int(m.group(1)), handler)

    m = re.match(r"/api/agent-routes/(\d+)$", path)
    if m:
        return _delete_route("agent-routes", int(m.group(1)), handler)

    return False
