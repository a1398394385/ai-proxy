#!/usr/bin/env python
"""Hermes Data Browser — 本地浏览 Hermes 内部数据的 Web 工具
支持：Fact Store 浏览 + Token 使用统计
"""

import json
import os
import sqlite3
import re
import time
import http.client
import socket
import ssl
from datetime import datetime, timedelta
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
from pathlib import Path

from proxy.config_manager import ConfigDB, Migrations
from proxy.common import get_port, get_host, load_config, CONFIG, CONFIG_PATH

# 配置
DB_PATH = os.path.expanduser("~/.hermes/memory_store.db")
CONFIG_DB_PATH = Path(os.path.expanduser("~/.hermes/config.db"))
ACCESS_LOG_DB_PATH = Path("data/access_log.db")


def get_config_db():
    return ConfigDB(CONFIG_DB_PATH)


MAX_BODY_SIZE = 10 * 1024 * 1024  # 10MB


def _read_json(handler):
    """读取请求体 JSON，错误时发送 400 并返回 None。"""
    length = int(handler.headers.get("Content-Length", 0))
    if length > MAX_BODY_SIZE:
        json_response(handler, {"error": "Request body too large"}, 413)
        return None
    body = handler.rfile.read(length)
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        json_response(handler, {"error": "Invalid JSON"}, 400)
        return None


def _reload_proxies():
    try:
        proxy_port = get_port("codex_proxy", 48743)
        conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=2)
        conn.request("POST", "/admin/reload")
        conn.getresponse().read()
        conn.close()
    except Exception:
        pass


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

    http_path = path.rstrip("/") + "/" if path else "/"
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


# ─── 自动检测上游模型 ───


def _call_upstream_models(upstream: dict) -> dict:
    """调用上游 /v1/models 获取可用模型列表。"""
    parsed = urlparse(upstream["base_url"])
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    scheme = parsed.scheme

    # 构建候选路径：先 /v1/models（OpenAI 标准格式），再 /models
    base_path = parsed.path.rstrip("/")
    candidate_paths = [base_path + "/v1/models", base_path + "/models"]

    # 构建请求头
    headers = {"Accept": "application/json"}
    api_key = upstream.get("api_key", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # 确定 SSL 上下文
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
                    # 路径不对，尝试下一个候选路径
                    continue
                else:
                    # 其他 4xx/5xx：服务器应答了，网络可达但不成功
                    result["reachable"] = True
                    result["error"] = f"HTTP {status}"
                    return result
            finally:
                conn.close()
        except (socket.gaierror, socket.timeout, OSError) as e:
            result["error"] = str(e)
            return result

    # 所有候选路径都返回 404/405
    result["reachable"] = True
    result["error"] = "所有候选路径均返回 404/405"

    return result


STATE_DB_PATH = os.path.expanduser("~/.hermes/state.db")
CC_SWITCH_DB_PATH = os.path.expanduser("~/.cc-switch/cc-switch.db")
load_config(CONFIG_PATH)
HOST = get_host("data_browser", "127.0.0.1")
PORT = get_port("data_browser", 18742)

_stats_service_instance = None


def _get_stats_service():
    """懒加载获取 StatsService 单例。"""
    global _stats_service_instance
    if _stats_service_instance is None:
        from stats_service import StatsService

        _stats_service_instance = StatsService(
            access_log_db_path=str(ACCESS_LOG_DB_PATH),
            config_db_path=str(CONFIG_DB_PATH),
            state_db_path=STATE_DB_PATH,
            cc_switch_db_path=CC_SWITCH_DB_PATH,
        )
    return _stats_service_instance


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_state_db():
    conn = sqlite3.connect(STATE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_access_log_db():
    conn = sqlite3.connect(str(ACCESS_LOG_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def json_response(handler, data, status=200):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def row_to_dict(row):
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, bytes):
            d[k] = None
    return d


# ===== Fact Store 功能 =====
def get_all_facts():
    conn = get_db()
    rows = conn.execute("SELECT * FROM facts ORDER BY fact_id DESC").fetchall()
    facts = [row_to_dict(r) for r in rows]
    for f in facts:
        entities = conn.execute(
            """SELECT e.name, e.entity_type FROM entities e
               JOIN fact_entities fe ON e.entity_id = fe.entity_id
               WHERE fe.fact_id = ?""",
            (f["fact_id"],),
        ).fetchall()
        f["entities"] = [dict(e)["name"] for e in entities]
    conn.close()
    return facts


def search_facts(query):
    conn = get_db()
    rows = conn.execute(
        """SELECT f.* FROM facts f
           JOIN facts_fts ON facts_fts.rowid = f.fact_id
           WHERE facts_fts MATCH ?
           ORDER BY f.fact_id DESC""",
        (query,),
    ).fetchall()
    facts = [row_to_dict(r) for r in rows]
    for f in facts:
        entities = conn.execute(
            """SELECT e.name FROM entities e
               JOIN fact_entities fe ON e.entity_id = fe.entity_id
               WHERE fe.fact_id = ?""",
            (f["fact_id"],),
        ).fetchall()
        f["entities"] = [dict(e)["name"] for e in entities]
    conn.close()
    return facts


class HermesDataHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[HermesData] {args[0]}")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"
        )
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        qs = parse_qs(parsed.query)

        # ===== 模型配置 API =====
        if path == "/api/upstreams":
            db = get_config_db()
            upstreams = db.list_upstreams()
            db.close()
            return json_response(self, {"upstreams": upstreams})

        m = re.match(r"/api/upstreams/([^/]+)$", path)
        if m:
            db = get_config_db()
            u = db.get_upstream(m.group(1))
            db.close()
            if u:
                return json_response(self, u)
            return json_response(self, {"error": "Not found"}, 404)

        if path == "/api/models":
            upstream_filter = qs.get("upstream_id", [None])[0]
            db = get_config_db()
            models = db.list_models(upstream_id=upstream_filter)
            db.close()
            return json_response(self, {"models": models})

        m = re.match(r"/api/models/(\d+)$", path)
        if m:
            db = get_config_db()
            model = db.get_model(int(m.group(1)))
            db.close()
            if model:
                return json_response(self, model)
            return json_response(self, {"error": "Not found"}, 404)

        if path == "/api/routes":
            request_type = qs.get("request_type", [None])[0]
            db = get_config_db()
            routes = db.list_routes(request_type=request_type)
            db.close()
            return json_response(self, {"routes": routes})

        m = re.match(r"/api/routes/(\d+)$", path)
        if m:
            db = get_config_db()
            route = db.get_route(int(m.group(1)))
            db.close()
            if route:
                return json_response(self, route)
            return json_response(self, {"error": "Not found"}, 404)

        if path == "/api/config/status":
            db = get_config_db()
            counts = db.get_counts()
            db.close()
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
            return json_response(
                self,
                {
                    "proxy_reachable": proxy_reachable,
                    "config_db": counts,
                },
            )

        # ===== Fact Store API =====
        if path == "/api/facts":
            q = qs.get("q", [None])[0]
            category = qs.get("category", [None])[0]
            if q:
                facts = search_facts(q)
            else:
                facts = get_all_facts()
            if category:
                facts = [f for f in facts if f["category"] == category]
            return json_response(self, {"facts": facts, "count": len(facts)})

        if path.startswith("/api/facts/"):
            fact_id = path.split("/")[-1]
            try:
                fact_id = int(fact_id)
            except ValueError:
                return json_response(self, {"error": "Invalid ID"}, 400)
            conn = get_db()
            row = conn.execute(
                "SELECT * FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()
            if not row:
                conn.close()
                return json_response(self, {"error": "Not found"}, 404)
            fact = row_to_dict(row)
            entities = conn.execute(
                """SELECT e.name FROM entities e
                   JOIN fact_entities fe ON e.entity_id = fe.entity_id
                   WHERE fe.fact_id = ?""",
                (fact_id,),
            ).fetchall()
            fact["entities"] = [dict(e)["name"] for e in entities]
            conn.close()
            return json_response(self, fact)

        if path == "/api/categories":
            conn = get_db()
            rows = conn.execute(
                "SELECT DISTINCT category, COUNT(*) as cnt FROM facts GROUP BY category ORDER BY cnt DESC"
            ).fetchall()
            cats = [{"category": r["category"], "count": r["cnt"]} for r in rows]
            conn.close()
            return json_response(self, {"categories": cats})

        if path == "/api/stats":
            conn = get_db()
            total = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
            cats = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM facts GROUP BY category ORDER BY cnt DESC"
            ).fetchall()
            categories = {r["category"]: r["cnt"] for r in cats}
            top_entities = conn.execute(
                """SELECT e.name, COUNT(*) as cnt FROM entities e
                   JOIN fact_entities fe ON e.entity_id = fe.entity_id
                   GROUP BY e.name ORDER BY cnt DESC LIMIT 20"""
            ).fetchall()
            top_entities = [
                {"name": r["name"], "count": r["cnt"]} for r in top_entities
            ]
            conn.close()
            return json_response(
                self,
                {
                    "total": total,
                    "categories": categories,
                    "top_entities": top_entities,
                },
            )

        # ===== Token 统计 API =====
        if path == "/api/token_stats":
            period = qs.get("period", ["week"])[0]
            if period not in ("day", "week", "month"):
                period = "week"
            stats = _get_stats_service().fetch_summary(period)
            return json_response(self, stats)

        if path == "/api/token_stats/by_model":
            period = qs.get("period", ["week"])[0]
            if period not in ("day", "week", "month"):
                period = "week"
            models = _get_stats_service().fetch_by_model(period)
            return json_response(self, {"models": models, "count": len(models)})

        if path == "/api/token_stats/trend":
            period = qs.get("period", ["week"])[0]
            if period not in ("day", "week", "month"):
                period = "week"
            trends = _get_stats_service().fetch_trend(period)
            return json_response(self, {"trends": trends, "count": len(trends)})

        if path == "/api/token_stats/summary":
            return json_response(self, _get_stats_service().fetch_all_summaries())

        # 新增：请求日志（分页）
        if path == "/api/token_stats/requests":
            stats_service = _get_stats_service()
            period = qs.get("period", ["week"])[0]
            if period not in ("day", "week", "month"):
                return json_response(self, {"error": "Invalid period"}, 400)
            model = qs.get("model", [None])[0]
            request_type = qs.get("request_type", [None])[0]
            try:
                limit = int(qs.get("limit", ["50"])[0])
                offset = int(qs.get("offset", ["0"])[0])
            except (ValueError, TypeError):
                return json_response(self, {"error": "Invalid limit/offset"}, 400)
            if limit > 200:
                return json_response(
                    self, {"error": "Limit exceeds maximum (200)"}, 400
                )
            result = stats_service.fetch_requests(
                period=period,
                model=model,
                request_type=request_type,
                limit=limit,
                offset=offset,
            )
            return json_response(self, result)

        # 新增：按上游统计
        if path == "/api/token_stats/by_upstream":
            stats_service = _get_stats_service()
            period = qs.get("period", ["week"])[0]
            if period not in ("day", "week", "month"):
                return json_response(self, {"error": "Invalid period"}, 400)
            result = stats_service.fetch_by_upstream(period=period)
            return json_response(self, result)

        # 新增：按模型请求（路径参数）
        m = re.match(r"/api/token_stats/by_model/([^/]+)/requests$", path)
        if m:
            stats_service = _get_stats_service()
            model = unquote(m.group(1))
            period = qs.get("period", ["week"])[0]
            if period not in ("day", "week", "month"):
                return json_response(self, {"error": "Invalid period"}, 400)
            try:
                limit = int(qs.get("limit", ["50"])[0])
                offset = int(qs.get("offset", ["0"])[0])
            except (ValueError, TypeError):
                return json_response(self, {"error": "Invalid limit/offset"}, 400)
            if limit > 200:
                return json_response(
                    self, {"error": "Limit exceeds maximum (200)"}, 400
                )
            result = stats_service.fetch_by_model_requests(
                model=model, period=period, limit=limit, offset=offset
            )
            return json_response(self, result)

        # 静态文件
        static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
        file_path = os.path.join(static_dir, path.lstrip("/"))
        if path == "/":
            file_path = os.path.join(static_dir, "index.html")
        real_file = os.path.realpath(file_path)
        real_static = os.path.realpath(static_dir)
        if not real_file.startswith(real_static + os.sep):
            self.send_response(403)
            self.end_headers()
            return
        if os.path.isfile(file_path):
            ext_map = {
                ".html": "text/html",
                ".css": "text/css",
                ".js": "application/javascript",
            }
            ext = os.path.splitext(file_path)[1]
            mime = ext_map.get(ext, "application/octet-stream")
            with open(file_path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", f"{mime}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        # ===== 模型配置 API =====
        if path == "/api/migrate":
            try:
                result = Migrations(Path.home() / ".hermes" / "config.db").migrate()
                return json_response(self, result)
            except Exception as e:
                return json_response(self, {"error": str(e)}, 500)

        if path == "/api/upstreams":
            data = _read_json(self)
            if not data:
                return
            db = get_config_db()
            try:
                uid = db.add_upstream(data)
                db.close()
                _reload_proxies()
                return json_response(self, {"id": uid, "message": "Created"}, 201)
            except sqlite3.IntegrityError as e:
                db.close()
                return json_response(self, {"error": str(e)}, 409)

        test_m = re.match(r"/api/upstreams/([^/]+)/test$", path)
        if test_m:
            uid = test_m.group(1)
            db = get_config_db()
            u = db.get_upstream(uid)
            db.close()
            if not u:
                return json_response(self, {"error": "Not found"}, 404)
            result = _test_upstream_connectivity(u)
            return json_response(self, result)

        # ─── 自动检测 + 批量添加模型 ───
        detect_m = re.match(r"/api/upstreams/([^/]+)/detect-models$", path)
        if detect_m:
            uid = detect_m.group(1)
            db = get_config_db()
            u = db.get_upstream(uid)
            db.close()
            if not u:
                return json_response(self, {"error": "Not found"}, 404)

            # 校验上游处于启用状态
            if not u.get("is_active"):
                return json_response(self, {"error": "上游已禁用"}, 400)

            # 调用检测函数
            detect_result = _call_upstream_models(u)

            # 获取该上游已有模型名
            db = get_config_db()
            existing_models = [m["name"] for m in db.list_models(upstream_id=uid)]
            db.close()

            # 构建响应：discovered = 不在已有模型中的新模型
            all_ids = detect_result.get("model_ids", [])
            discovered = [mid for mid in all_ids if mid not in existing_models]

            return json_response(
                self,
                {
                    "reachable": detect_result["reachable"],
                    "discovered": discovered,
                    "existing": [mid for mid in all_ids if mid in existing_models],
                    "error": detect_result.get("error"),
                },
            )

        bulk_m = re.match(r"/api/upstreams/([^/]+)/models/bulk$", path)
        if bulk_m:
            uid = bulk_m.group(1)
            data = _read_json(self)
            if not data:
                return  # _read_json 已发送 400

            models = data.get("models")
            if not isinstance(models, list):
                return json_response(self, {"error": "缺少 models 数组"}, 400)

            db = get_config_db()
            u = db.get_upstream(uid)
            if not u:
                db.close()
                return json_response(self, {"error": "Not found"}, 404)

            result = db.add_models_bulk(uid, models)
            db.close()

            status = 201 if result["added"] > 0 else 200
            return json_response(self, result, status)

        if path == "/api/models":
            data = _read_json(self)
            if not data:
                return
            db = get_config_db()
            try:
                mid = db.add_model(data)
                db.close()
                _reload_proxies()
                return json_response(self, {"id": mid, "message": "Created"}, 201)
            except sqlite3.IntegrityError as e:
                db.close()
                return json_response(self, {"error": str(e)}, 409)

        if path == "/api/routes":
            data = _read_json(self)
            if not data:
                return
            # 校验 request_type
            request_type = data.get("request_type", "responses")
            if request_type not in ("responses", "messages", "chat_completions"):
                return json_response(
                    self,
                    {
                        "error": "request_type must be one of: responses, messages, chat_completions"
                    },
                    400,
                )
            db = get_config_db()
            model = db.get_model(data["target_model_id"])
            if not model:
                db.close()
                return json_response(self, {"error": "target_model_id 不存在"}, 400)
            if not model.get("upstream_active"):
                db.close()
                return json_response(self, {"error": "目标模型所属上游已禁用"}, 400)
            try:
                rid = db.add_route(data)
                db.close()
                _reload_proxies()
                return json_response(self, {"id": rid, "message": "Created"}, 201)
            except (sqlite3.IntegrityError, ValueError) as e:
                db.close()
                return json_response(self, {"error": str(e)}, 409)

        if path == "/api/config/reload":
            result = {}
            # Reload AI Proxy
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
            return json_response(self, result)

        if path == "/api/db/query":
            data = _read_json(self)
            if not data:
                return
            sql = data.get("sql", "").strip()

            # 安全校验：只允许 SELECT
            if not sql.upper().startswith("SELECT"):
                return json_response(self, {"error": "只允许 SELECT 查询"}, 400)

            # 安全校验：禁止多语句
            if ";" in sql:
                return json_response(self, {"error": "禁止多语句 SQL"}, 400)

            # 白名单表名校验
            table_pattern = re.compile(r"FROM\s+(\w+)", re.IGNORECASE)
            for match in table_pattern.finditer(sql):
                table_name = match.group(1)
                if table_name not in ("debug_log", "token_stats"):
                    return json_response(
                        self, {"error": f"禁止访问表: {table_name}"}, 403
                    )

            # LIMIT 处理
            limit_pattern = re.compile(r"LIMIT\s+(\d+)", re.IGNORECASE)
            limit_match = limit_pattern.search(sql)
            if limit_match:
                limit_val = int(limit_match.group(1))
                if limit_val > 500:
                    sql = limit_pattern.sub("LIMIT 500", sql)
            else:
                sql += " LIMIT 500"

            # 执行查询
            conn = get_access_log_db()
            try:
                cursor = conn.execute(sql)
                columns = [col[0] for col in cursor.description]
                rows = [list(row) for row in cursor.fetchall()]
                conn.close()
                return json_response(self, {"columns": columns, "rows": rows})
            except Exception as e:
                conn.close()
                return json_response(self, {"error": str(e)}, 400)

        # ===== Fact Store API =====
        if path == "/api/facts":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                return json_response(self, {"error": "Invalid JSON"}, 400)
            conn = get_db()
            try:
                cursor = conn.execute(
                    """INSERT INTO facts (content, category, tags, trust_score)
                       VALUES (?, ?, ?, ?)""",
                    (
                        data.get("content", ""),
                        data.get("category", "general"),
                        data.get("tags", ""),
                        data.get("trust_score", 0.5),
                    ),
                )
                conn.commit()
                fact_id = cursor.lastrowid
                if "entities" in data and isinstance(data["entities"], list):
                    for ename in data["entities"]:
                        entity = conn.execute(
                            "SELECT entity_id FROM entities WHERE name = ?", (ename,)
                        ).fetchone()
                        if not entity:
                            ec = conn.execute(
                                "INSERT INTO entities (name) VALUES (?)", (ename,)
                            )
                            eid = ec.lastrowid
                        else:
                            eid = dict(entity)["entity_id"]
                        conn.execute(
                            "INSERT OR IGNORE INTO fact_entities (fact_id, entity_id) VALUES (?, ?)",
                            (fact_id, eid),
                        )
                    conn.commit()
                conn.close()
                return json_response(
                    self, {"fact_id": fact_id, "message": "Created"}, 201
                )
            except sqlite3.IntegrityError as e:
                conn.close()
                return json_response(self, {"error": str(e)}, 409)

        if re.match(r"/api/facts/\d+/feedback", path):
            fact_id = int(path.split("/")[-2])
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body)
            action = data.get("action", "helpful")
            delta = 0.1 if action == "helpful" else -0.1
            conn = get_db()
            conn.execute(
                "UPDATE facts SET trust_score = MAX(0, MIN(1, trust_score + ?)), helpful_count = helpful_count + 1 WHERE fact_id = ?",
                (delta, fact_id),
            )
            conn.commit()
            conn.close()
            return json_response(self, {"message": "Feedback recorded"})

        return json_response(self, {"error": "Not found"}, 404)

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        # ===== 模型配置 API =====
        m = re.match(r"/api/upstreams/([^/]+)$", path)
        if m:
            data = _read_json(self)
            if not data:
                return
            db = get_config_db()
            try:
                db.update_upstream(m.group(1), data)
                db.close()
                _reload_proxies()
                return json_response(self, {"message": "Updated"})
            except sqlite3.IntegrityError as e:
                db.close()
                return json_response(self, {"error": str(e)}, 409)

        m = re.match(r"/api/models/(\d+)$", path)
        if m:
            data = _read_json(self)
            if not data:
                return
            db = get_config_db()
            try:
                db.update_model(int(m.group(1)), data)
                db.close()
                _reload_proxies()
                return json_response(self, {"message": "Updated"})
            except sqlite3.IntegrityError as e:
                db.close()
                return json_response(self, {"error": str(e)}, 409)

        m = re.match(r"/api/routes/(\d+)$", path)
        if m:
            data = _read_json(self)
            if not data:
                return
            db = get_config_db()
            try:
                db.update_route(int(m.group(1)), data)
                db.close()
                _reload_proxies()
                return json_response(self, {"message": "Updated"})
            except (sqlite3.IntegrityError, ValueError) as e:
                db.close()
                return json_response(self, {"error": str(e)}, 409)

        # ===== Fact Store API =====
        m = re.match(r"/api/facts/(\d+)$", path)
        if m:
            fact_id = int(m.group(1))
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                return json_response(self, {"error": "Invalid JSON"}, 400)
            conn = get_db()
            existing = conn.execute(
                "SELECT * FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()
            if not existing:
                conn.close()
                return json_response(self, {"error": "Not found"}, 404)
            conn.execute(
                """UPDATE facts SET content = ?, category = ?, tags = ?, trust_score = ?
                   WHERE fact_id = ?""",
                (
                    data.get("content", existing["content"]),
                    data.get("category", existing["category"]),
                    data.get("tags", existing["tags"]),
                    data.get("trust_score", existing["trust_score"]),
                    fact_id,
                ),
            )
            conn.commit()
            conn.close()
            return json_response(self, {"message": "Updated"})
        return json_response(self, {"error": "Not found"}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        # ===== 模型配置 API =====
        m = re.match(r"/api/upstreams/([^/]+)$", path)
        if m:
            uid = m.group(1)
            db = get_config_db()
            u = db.get_upstream(uid)
            if not u:
                db.close()
                return json_response(self, {"error": "Not found"}, 404)
            active_routes = db.upstream_active_routes(uid)
            if active_routes:
                db.close()
                return json_response(
                    self,
                    {
                        "error": "上游有活跃路由引用，无法禁用",
                        "referenced_routes": active_routes,
                    },
                    409,
                )
            db.disable_upstream(uid)
            db.close()
            _reload_proxies()
            return json_response(self, {"message": "Disabled"})

        m = re.match(r"/api/models/(\d+)$", path)
        if m:
            mid = int(m.group(1))
            db = get_config_db()
            result = db.delete_model(mid)
            db.close()
            if "error" in result:
                return json_response(self, result, 409)
            _reload_proxies()
            return json_response(self, {"message": "Deleted"})

        m = re.match(r"/api/routes/(\d+)$", path)
        if m:
            rid = int(m.group(1))
            db = get_config_db()
            route = db.get_route(rid)
            if not route:
                db.close()
                return json_response(self, {"error": "Not found"}, 404)
            if route["source"] == "*":
                routes = db.list_routes()
                star_count = sum(1 for r in routes if r["source"] == "*")
                if star_count <= 1:
                    db.close()
                    return json_response(
                        self,
                        {
                            "error": "不能删除最后一条 * fallback 路由",
                        },
                        409,
                    )
            try:
                db.delete_route(rid)
                db.close()
                _reload_proxies()
                return json_response(self, {"message": "Deleted"})
            except sqlite3.IntegrityError as e:
                db.close()
                return json_response(self, {"error": str(e)}, 409)

        # ===== Fact Store API =====
        m = re.match(r"/api/facts/(\d+)$", path)
        if m:
            fact_id = int(m.group(1))
            conn = get_db()
            existing = conn.execute(
                "SELECT fact_id FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()
            if not existing:
                conn.close()
                return json_response(self, {"error": "Not found"}, 404)
            conn.execute("DELETE FROM facts WHERE fact_id = ?", (fact_id,))
            conn.commit()
            conn.close()
            return json_response(self, {"message": "Deleted"})
        return json_response(self, {"error": "Not found"}, 404)


def main():
    if not os.path.exists(DB_PATH):
        print(f"Database not found: {DB_PATH}")
        return
    if not os.path.exists(STATE_DB_PATH):
        print(f"Warning: State database not found: {STATE_DB_PATH}")
        print("Token statistics will not be available.")

    server = HTTPServer((HOST, PORT), HermesDataHandler)
    print(f"=" * 50)
    print(f"Hermes Data Browser")
    print(f"=" * 50)
    print(f"访问地址: http://{HOST}:{PORT}")
    print(f"功能: Fact Store + Token 使用统计")
    print(f"按 Ctrl+C 停止")
    print(f"=" * 50)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭...")
        server.server_close()


if __name__ == "__main__":
    main()
