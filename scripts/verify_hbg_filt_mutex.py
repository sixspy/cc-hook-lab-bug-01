#!/usr/bin/env python3
"""hook-bug-01 两路并发互斥探针。

用法：
  python verify_hbg_filt_mutex.py --data-dir /tmp/hbg --mode writer --rounds 80
  python verify_hbg_filt_mutex.py --data-dir /tmp/hbg --mode enqueuer --rounds 40

两路分别是“改过滤器”和“入队”。脚本用与主干相同的 flock + 线程闸 +
BEGIN IMMEDIATE，并在每次入队后核对事件行/凭证指纹/表达式判定同一口径。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

p = argparse.ArgumentParser()
p.add_argument("--data-dir", required=True)
p.add_argument("--mode", choices=["writer", "enqueuer", "mixed"], required=True)
p.add_argument("--worker", default="1")
p.add_argument("--rounds", type=int, default=40)
args = p.parse_args()

data_dir = Path(args.data_dir).resolve()
data_dir.mkdir(parents=True, exist_ok=True)
os.environ["数据目录"] = str(data_dir)
os.environ.setdefault("夹具目录", str(Path(__file__).resolve().parents[1] / "fixtures"))

from app import db, seed  # noqa: E402

db.数据目录 = data_dir
db.库路径 = data_dir / "投递台.db"
seed.初始化库()

from app.services import 端点, 入队, 过滤  # noqa: E402

KEY = "ep-hbg-mutex"
EVENT_TYPE = "hbg.filt.mutex.event"
MATCH = '事件类型 == "hbg.filt.mutex.event"'
MISMATCH = '事件类型 == "hbg.filt.mutex.other"'

try:
    端点.登记({
        "端点键": KEY,
        "名称": "互斥探针",
        "回调URL": "https://hook.example.local/mutex",
        "过滤表达式": MATCH,
    })
except ValueError:
    pass


def verify_credential(eid: str, accepted: bool) -> dict:
    conn = db.连接()
    try:
        ev = conn.execute("SELECT * FROM 事件 WHERE 事件号=?", (eid,)).fetchone()
        cred = conn.execute(
            "SELECT * FROM 过滤凭证 WHERE 事件号=? ORDER BY rowid DESC LIMIT 1",
            (eid,),
        ).fetchone()
        if accepted:
            if ev is None or cred is None:
                raise AssertionError(f"{eid}: accepted but missing event/credential")
            if ev["过滤指纹"] != cred["指纹"]:
                raise AssertionError(f"{eid}: event fp != credential fp")
            if cred["决定"] != "准入":
                raise AssertionError(f"{eid}: credential decision is {cred['决定']}")
            if not 过滤.表达式匹配(cred["表达式"], {"事件类型": EVENT_TYPE}):
                raise AssertionError(f"{eid}: accepted credential does not match its pinned expr")
            return {"eid": eid, "fp": cred["指纹"], "decision": "准入"}
        if ev is not None:
            raise AssertionError(f"{eid}: rejected event leaked into current queue")
        if cred is None or cred["决定"] != "拒收":
            raise AssertionError(f"{eid}: rejected but missing rejection credential")
        return {"eid": eid, "fp": cred["指纹"], "decision": "拒收"}
    finally:
        conn.close()


def run_writer() -> dict:
    for i in range(args.rounds):
        端点.改过滤器(KEY, MATCH if i % 2 == 0 else MISMATCH)
    端点.改过滤器(KEY, MATCH)
    return {"mode": "writer", "rounds": args.rounds, "final": MATCH}


def run_enqueuer() -> dict:
    accepted = 0
    rejected = 0
    violations = []
    started = time.time()
    for i in range(args.rounds):
        eid = f"EVT-MUTEX-W{args.worker}-{i:04d}-{os.getpid()}"
        try:
            ev = 入队.建事件({
                "事件号": eid,
                "事件类型": EVENT_TYPE,
                "端点键": KEY,
                "载荷摘要": f"worker={args.worker};i={i}",
            })
            verify_credential(eid, True)
            assert ev["过滤指纹"]
            accepted += 1
        except ValueError:
            try:
                verify_credential(eid, False)
                rejected += 1
            except AssertionError as exc:
                violations.append(str(exc))
        except AssertionError as exc:
            violations.append(str(exc))
    result = {
        "mode": "enqueuer",
        "worker": args.worker,
        "rounds": args.rounds,
        "accepted": accepted,
        "rejected": rejected,
        "violations": violations,
        "elapsed_ms": int((time.time() - started) * 1000),
    }
    if violations:
        print(json.dumps({"ok": False, **result}, ensure_ascii=False, indent=2))
        raise SystemExit(2)
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    if args.mode == "writer":
        print(json.dumps({"ok": True, **run_writer()}, ensure_ascii=False, indent=2))
    elif args.mode == "enqueuer":
        run_enqueuer()
    else:
        # mixed：同一进程内两路角色顺序执行，主要用于手工冒烟。
        print(json.dumps(run_writer(), ensure_ascii=False))
        run_enqueuer()
