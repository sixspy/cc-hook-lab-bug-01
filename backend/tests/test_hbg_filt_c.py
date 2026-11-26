"""丙：改过滤器与入队并发时不得产生两套口径。

每个被接受的事件都必须和同事务钉住的过滤器指纹/表达式一致；
每个拒绝都必须留下同指纹拒收凭证。旧实现没有过滤器版本、凭证或
事务互斥，并发下无法证明入队口径，因此本争用测试必须先红。
"""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="hbg_filt_c_"))
os.environ["数据目录"] = str(_TMP)
os.environ["夹具目录"] = "/workspace/fixtures"

from app import db, seed  # noqa: E402

db.数据目录 = _TMP
db.库路径 = _TMP / "hbg_filt_c.db"

from app.services import 入队, 端点  # noqa: E402

KEY = "ep-hbg-filt-c"
EVENT_TYPE = "hbg.filt.c.contended"
MATCH_EXPR = '事件类型 == "hbg.filt.c.contended"'
MISMATCH_EXPR = '事件类型 == "hbg.filt.c.other"'


def setup_module() -> None:
    # 全量 pytest 收集时后续模块可能改写 db.库路径；每个模块开跑前重新钉回自己的库。
    db.数据目录 = _TMP
    db.库路径 = _TMP / "hbg_filt_c.db"
    seed.初始化库()
    端点.登记(
        {
            "端点键": KEY,
            "名称": "丙并发过滤器端点",
            "回调URL": "https://hook.example.local/hbg-c",
            "过滤表达式": MATCH_EXPR,
        }
    )


def _cred(eid: str):
    conn = db.连接()
    try:
        return conn.execute(
            "SELECT * FROM 过滤凭证 WHERE 事件号=? ORDER BY rowid DESC LIMIT 1",
            (eid,),
        ).fetchone()
    finally:
        conn.close()


def test_顺序基线_匹配准入_不匹配拒收() -> None:
    端点.改过滤器(KEY, MATCH_EXPR)
    ok = 入队.建事件(
        {"事件号": "EVT-HBG-C-SEQ-OK", "事件类型": EVENT_TYPE, "端点键": KEY}
    )
    assert ok["状态"] == "待投"
    assert ok["过滤指纹"] and ok["过滤指纹"] != "stale-fp"

    端点.改过滤器(KEY, MISMATCH_EXPR)
    try:
        入队.建事件(
            {"事件号": "EVT-HBG-C-SEQ-NO", "事件类型": EVENT_TYPE, "端点键": KEY}
        )
        raise AssertionError("不匹配事件不得入队")
    except ValueError:
        pass

    conn = db.连接()
    try:
        assert conn.execute(
            "SELECT 1 FROM 事件 WHERE 事件号='EVT-HBG-C-SEQ-NO'"
        ).fetchone() is None
        cred = conn.execute(
            "SELECT * FROM 过滤凭证 WHERE 事件号='EVT-HBG-C-SEQ-NO' "
            "ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        assert cred is not None and cred["决定"] == "拒收"
    finally:
        conn.close()


def test_并发改过滤器与入队_指纹和判定必须同一口径() -> None:
    # 恢复为可匹配表达式，随后写线程在两个合法表达式间切换。
    端点.改过滤器(KEY, MATCH_EXPR)

    barrier = threading.Barrier(3)
    stop = threading.Event()
    errors: list[BaseException] = []
    accepted: list[str] = []
    rejected: list[str] = []
    lock = threading.Lock()

    def record_error(exc: BaseException) -> None:
        with lock:
            errors.append(exc)

    def writer() -> None:
        try:
            barrier.wait(timeout=10)
            i = 0
            while not stop.is_set():
                expr = MATCH_EXPR if i % 2 == 0 else MISMATCH_EXPR
                端点.改过滤器(KEY, expr)
                i += 1
        except BaseException as exc:  # noqa: BLE001 - 争用测试要回收线程异常
            record_error(exc)
        finally:
            stop.set()

    def enqueuer(worker: int, count: int) -> None:
        try:
            barrier.wait(timeout=10)
            for i in range(count):
                eid = f"EVT-HBG-C-W{worker}-{i:03d}"
                try:
                    ev = 入队.建事件(
                        {
                            "事件号": eid,
                            "事件类型": EVENT_TYPE,
                            "端点键": KEY,
                            "载荷摘要": f"w={worker};i={i}",
                        }
                    )
                    assert ev["过滤指纹"]
                    with lock:
                        accepted.append(eid)
                except ValueError:
                    with lock:
                        rejected.append(eid)
        except BaseException as exc:  # noqa: BLE001
            record_error(exc)

    t_write = threading.Thread(target=writer, name="hbg-filter-writer")
    t1 = threading.Thread(target=enqueuer, args=(1, 30), name="hbg-enqueue-1")
    t2 = threading.Thread(target=enqueuer, args=(2, 30), name="hbg-enqueue-2")
    t_write.start()
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)
    stop.set()
    t_write.join(timeout=30)

    assert not t1.is_alive() and not t2.is_alive(), "入队线程超时，可能发生锁/事务卡死"
    assert not errors, f"并发过程出现异常: {errors!r}"
    # 随机调度不保证 race 阶段一定同时出现两种结果；两种命运已在上面的顺序
    # 基线中固定验证。这里必须验证的是 60 次并发入队都有唯一且同口径的凭证。
    assert len(accepted) + len(rejected) == 60
    conn = db.连接()
    try:
        total_events = conn.execute(
            "SELECT COUNT(*) AS c FROM 事件 WHERE 端点键=?", (KEY,)
        ).fetchone()["c"]
        total_creds = conn.execute(
            "SELECT COUNT(*) AS c FROM 过滤凭证 WHERE 端点键=? AND 决定='准入'",
            (KEY,),
        ).fetchone()["c"]
        # 顺序基线也产生准入凭证，因此凭证数只会更多；关键是每个事件都有钉版指纹。
        assert total_events >= len(accepted)
        missing = conn.execute(
            "SELECT e.事件号 FROM 事件 e LEFT JOIN 过滤凭证 c "
            "ON c.事件号=e.事件号 AND c.决定='准入' "
            "WHERE e.端点键=? AND (e.过滤指纹='' OR c.指纹 IS NULL OR c.指纹 != e.过滤指纹)",
            (KEY,),
        ).fetchall()
        assert not missing, [r["事件号"] for r in missing]
    finally:
        conn.close()

    from app.services import 过滤

    for eid in accepted:
        row_cred = _cred(eid)
        assert row_cred is not None, f"{eid} 缺少独立凭证"
        assert row_cred["决定"] == "准入"
        assert row_cred["指纹"] != "stale-fp"
        assert 过滤.表达式匹配(row_cred["表达式"], {"事件类型": EVENT_TYPE}) is True

    for eid in rejected:
        row = db.连接().execute("SELECT 1 FROM 事件 WHERE 事件号=?", (eid,)).fetchone()
        assert row is None, f"拒收事件 {eid} 不得进入现行队列表"
        cred = _cred(eid)
        assert cred is not None and cred["决定"] == "拒收"
        assert cred["指纹"] != "stale-fp"
