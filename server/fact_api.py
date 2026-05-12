"""server 包 — Fact Store API。"""

import json
import re
import sqlite3

from .common import json_response, row_to_dict, fact_db, _read_json


# ─── 查询函数 ───


def get_all_facts():
    with fact_db() as conn:
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
    return facts


def search_facts(query):
    with fact_db() as conn:
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
    return facts


# ─── GET ───


def handle_get(path, qs, handler) -> bool:
    if path == "/api/facts":
        q = qs.get("q", [None])[0]
        category = qs.get("category", [None])[0]
        if q:
            facts = search_facts(q)
        else:
            facts = get_all_facts()
        if category:
            facts = [f for f in facts if f["category"] == category]
        json_response(handler, {"facts": facts, "count": len(facts)})
        return True

    if path.startswith("/api/facts/"):
        fact_id = path.split("/")[-1]
        try:
            fact_id = int(fact_id)
        except ValueError:
            json_response(handler, {"error": "Invalid ID"}, 400)
            return True
        with fact_db() as conn:
            row = conn.execute(
                "SELECT * FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()
            if not row:
                json_response(handler, {"error": "Not found"}, 404)
                return True
            fact = row_to_dict(row)
            entities = conn.execute(
                """SELECT e.name FROM entities e
                   JOIN fact_entities fe ON e.entity_id = fe.entity_id
                   WHERE fe.fact_id = ?""",
                (fact_id,),
            ).fetchall()
            fact["entities"] = [dict(e)["name"] for e in entities]
        json_response(handler, fact)
        return True

    if path == "/api/categories":
        with fact_db() as conn:
            rows = conn.execute(
                "SELECT DISTINCT category, COUNT(*) as cnt FROM facts GROUP BY category ORDER BY cnt DESC"
            ).fetchall()
            cats = [{"category": r["category"], "count": r["cnt"]} for r in rows]
        json_response(handler, {"categories": cats})
        return True

    if path == "/api/stats":
        with fact_db() as conn:
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
        json_response(
            handler,
            {"total": total, "categories": categories, "top_entities": top_entities},
        )
        return True

    return False


# ─── POST ───


def handle_post(path, handler) -> bool:
    if path == "/api/facts":
        data = _read_json(handler)
        if not data:
            return True
        with fact_db() as conn:
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
                            "SELECT entity_id FROM entities WHERE name = ?",
                            (ename,),
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
            except sqlite3.IntegrityError as e:
                json_response(handler, {"error": str(e)}, 409)
                return True
        json_response(handler, {"fact_id": fact_id, "message": "Created"}, 201)
        return True

    if re.match(r"/api/facts/\d+/feedback", path):
        fact_id = int(path.split("/")[-2])
        length = int(handler.headers.get("Content-Length", 0))
        body = handler.rfile.read(length)
        data = json.loads(body)
        action = data.get("action", "helpful")
        delta = 0.1 if action == "helpful" else -0.1
        with fact_db() as conn:
            conn.execute(
                "UPDATE facts SET trust_score = MAX(0, MIN(1, trust_score + ?)), helpful_count = helpful_count + 1 WHERE fact_id = ?",
                (delta, fact_id),
            )
            conn.commit()
        json_response(handler, {"message": "Feedback recorded"})
        return True

    return False


# ─── PUT ───


def handle_put(path, handler) -> bool:
    m = re.match(r"/api/facts/(\d+)$", path)
    if m:
        fact_id = int(m.group(1))
        data = _read_json(handler)
        if not data:
            return True
        with fact_db() as conn:
            existing = conn.execute(
                "SELECT * FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()
            if not existing:
                json_response(handler, {"error": "Not found"}, 404)
                return True
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
        json_response(handler, {"message": "Updated"})
        return True
    return False


# ─── DELETE ───


def handle_delete(path, handler) -> bool:
    m = re.match(r"/api/facts/(\d+)$", path)
    if m:
        fact_id = int(m.group(1))
        with fact_db() as conn:
            existing = conn.execute(
                "SELECT fact_id FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()
            if not existing:
                json_response(handler, {"error": "Not found"}, 404)
                return True
            conn.execute("DELETE FROM facts WHERE fact_id = ?", (fact_id,))
            conn.commit()
        json_response(handler, {"message": "Deleted"})
        return True
    return False
