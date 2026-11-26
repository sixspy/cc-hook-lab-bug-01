from __future__ import annotations
import json
import re

from app.db import 连接
from app.services import 校验


def 签名规() -> dict:
    conn = 连接()
    try:
        rows = conn.execute("SELECT 键,值 FROM 签名规").fetchall()
        return {r["键"]: r["值"] for r in rows}
    finally:
        conn.close()


def 改签名规(body: dict) -> dict:
    conn = 连接()
    try:
        for k, v in body.items():
            键 = 校验.非空("键", k)
            值 = 校验.非空("值", v)
            conn.execute(
                "INSERT INTO 签名规(键,值) VALUES (?,?) ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
                (键, 值),
            )
        conn.commit()
        return 签名规()
    finally:
        conn.close()


def 概览() -> dict:
    conn = 连接()
    try:
        def c(sql):
            return conn.execute(sql).fetchone()[0]
        base = {
            "端点数": c("SELECT COUNT(*) FROM 投递端点"),
            "启用端点": c("SELECT COUNT(*) FROM 投递端点 WHERE 启用=1"),
            "事件总数": c("SELECT COUNT(*) FROM 事件"),
            "待投": c("SELECT COUNT(*) FROM 事件 WHERE 状态='待投'"),
            "投递中": c("SELECT COUNT(*) FROM 事件 WHERE 状态='投递中'"),
            "已投递": c("SELECT COUNT(*) FROM 事件 WHERE 状态='已投递'"),
            "死信": c("SELECT COUNT(*) FROM 事件 WHERE 状态='死信'"),
            "尝试数": c("SELECT COUNT(*) FROM 投递尝试"),
            "死信匣": c("SELECT COUNT(*) FROM 死信 WHERE 状态='待处置'"),
            "签名规": 签名规(),
        }
        events = 事件列表()
        legal = [r for r in events if r.get("合法入账") is True]
        dirty = [r for r in events if r.get("账实标记") == "过滤器恒真短路"]
        base.update({
            "合法事件总数": len(legal),
            "过滤短路脏账": len(dirty),
            "账面待投": base["待投"],
            "合法待投": sum(1 for r in legal if r.get("状态") == "待投"),
            "库脏待投": sum(1 for r in dirty if r.get("状态") == "待投"),
        })
        # 主指标不得被短路脏事件抬高；账面值仍保留供账实核对。
        base["待投"] = base["合法待投"]
        return base
    finally:
        conn.close()


def 事件列表() -> list:
    conn = 连接()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM 事件 ORDER BY 入队时 DESC").fetchall()]
        return _标注事件账实(conn, rows)
    finally:
        conn.close()


def 合法事件列表() -> list:
    """现行队列的合法入队读集：必须有同指纹准入凭证或明确的种子/历史口径。"""
    return [r for r in 事件列表() if r.get("合法入账") is True]


def _短路事件集合(conn) -> set[str]:
    marked = {
        r["键"][len("filt_short_circuit_"):]
        for r in conn.execute(
            "SELECT 键 FROM 投递元 WHERE 键 LIKE 'filt_short_circuit_%'"
        ).fetchall()
    }
    traced = {
        r["事件号"]
        for r in conn.execute(
            "SELECT DISTINCT 事件号 FROM 操作留痕 "
            "WHERE 事件号 != '' AND ("
            "详情 LIKE '%过滤器恒真短路%' OR 详情 LIKE '%解析异常%恒真%'"
            ")"
        ).fetchall()
    }
    out = set(marked) | set(traced)

    # 失败测乙允许通过“夹具键 + JSON 库态”复现，不一定写标准后缀键。
    # 这里从所有含短路标记的投递元中恢复事件号，避免对账漏掉夹具脏账。
    try:
        meta_rows = conn.execute("SELECT 键,值 FROM 投递元").fetchall()
    except Exception:
        meta_rows = []
    for r in meta_rows:
        key = str(r["键"] or "")
        value = str(r["值"] or "")
        blob = f"{key}\n{value}"
        low = blob.lower()
        is_short_meta = (
            "过滤器恒真短路" in blob
            or "恒真短路" in blob
            or "short_circuit_true" in low
            or "short-circuit-true" in low
            or ("short_circuit" in low and "filt_rejected" not in low)
        )
        if not is_short_meta:
            continue
        if key.startswith("filt_short_circuit_"):
            out.add(key[len("filt_short_circuit_"):])
        try:
            obj = json.loads(value)
        except (TypeError, ValueError):
            obj = None

        def walk(node) -> None:
            if isinstance(node, dict):
                for k, v in node.items():
                    if str(k) in {"事件号", "eid", "event_id", "eventId"} and isinstance(v, (str, int)):
                        out.add(str(v))
                    else:
                        walk(v)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(obj)
        out.update(re.findall(r"EVT-[A-Za-z0-9_.:-]+", blob))
    return {x for x in out if x}


def _准入凭证映射(conn) -> dict[str, dict]:
    out: dict[str, dict] = {}
    try:
        rows = conn.execute(
            "SELECT * FROM 过滤凭证 WHERE 决定='准入' ORDER BY rowid ASC"
        ).fetchall()
    except Exception:
        return out
    for r in rows:
        # 后写的同事件凭证覆盖旧行，详情中另外返回完整列表。
        out[r["事件号"]] = dict(r)
    return out


def _标注事件账实(conn, rows: list[dict]) -> list[dict]:
    if not rows:
        return rows
    short_ids = _短路事件集合(conn)
    creds = _准入凭证映射(conn)
    for row in rows:
        eid = row.get("事件号", "")
        caliber = str(row.get("过滤口径") or "")
        state = str(row.get("过滤状态") or "")
        fp = str(row.get("过滤指纹") or "")
        cred = creds.get(eid)
        dirty = (
            eid in short_ids
            or state in {"恒真短路", "短路放行", "解析失败放行"}
            or caliber in {"short_circuit_true", "old_short_circuit"}
        )
        seed_or_history = caliber in {"seed_legal", "history_legal"}
        cred_ok = bool(cred and cred.get("指纹") == fp and cred.get("决定") == "准入")
        legal = (not dirty) and (seed_or_history or cred_ok)
        row["合法入账"] = legal
        if dirty:
            row["账实标记"] = "过滤器恒真短路"
        elif seed_or_history:
            row["账实标记"] = "种子/历史合法口径"
        elif cred_ok:
            row["账实标记"] = "独立凭证合法入队"
        else:
            row["账实标记"] = "缺过滤凭证或指纹不一致"
        if cred:
            row["过滤凭证摘要"] = {
                "凭证号": cred.get("凭证号"),
                "指纹": cred.get("指纹"),
                "口径": cred.get("口径"),
                "决定": cred.get("决定"),
                "版本": cred.get("版本"),
            }
    return rows


def 事件一条(事件号: str) -> dict | None:
    conn = 连接()
    try:
        row = conn.execute("SELECT * FROM 事件 WHERE 事件号=?", (事件号,)).fetchone()
        if not row:
            return None
        d = dict(row)
        _标注事件账实(conn, [d])
        d["尝试"] = [dict(x) for x in conn.execute(
            "SELECT * FROM 投递尝试 WHERE 事件号=? ORDER BY 投递时 DESC", (事件号,)
        ).fetchall()]
        dl = conn.execute("SELECT * FROM 死信 WHERE 事件号=?", (事件号,)).fetchone()
        d["死信"] = dict(dl) if dl else None
        try:
            d["过滤凭证"] = [dict(x) for x in conn.execute(
                "SELECT * FROM 过滤凭证 WHERE 事件号=? ORDER BY rowid DESC", (事件号,)
            ).fetchall()]
        except Exception:
            d["过滤凭证"] = []
        return d
    finally:
        conn.close()


def 投递列表() -> list:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM 投递尝试 ORDER BY 投递时 DESC LIMIT 200"
        ).fetchall()]
    finally:
        conn.close()


def 死信列表() -> list:
    conn = 连接()
    try:
        rows = [dict(r) for r in conn.execute(
            """
            SELECT d.*, e.事件类型, e.载荷摘要, e.优先级,
                   e.过滤指纹, e.过滤口径, e.过滤状态
            FROM 死信 d
            LEFT JOIN 事件 e ON e.事件号 = d.事件号
            ORDER BY d.入匣时 DESC
            """
        ).fetchall()]
        return _标注事件账实(conn, rows)
    finally:
        conn.close()


def 端点列表() -> list:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 投递端点 ORDER BY 端点键").fetchall()]
    finally:
        conn.close()


def 互证列表() -> list:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 互证批次 ORDER BY 批次号 DESC LIMIT 50").fetchall()]
    finally:
        conn.close()



def filt_齐套灯() -> dict:
    """展示齐套：只读面板芯片，不核真实对齐。"""
    from app.services import filt as _m
    snap = _m.dashboard()
    return {
        "标题": snap.get("title"),
        "芯片": snap.get("panel_chip"),
        "真相": snap.get("truth"),
        "齐套": bool(snap.get("aligned")),
        "页灯绿色": snap.get("panel_chip") == snap.get("ok_chip"),
        "对齐实值": snap.get("aligned"),
        "panel_pack_mismatch": snap.get("panel_pack_mismatch", False),
    }
