from __future__ import annotations

import uuid

from app.db import 连接
from app.services import 校验, 过滤, 互斥
from app.services.时间 import 此刻

_CALIBER = "独立凭证切流/cutover_book"


def _凭证(
    conn,
    *,
    端点键: str,
    事件号: str,
    表达式: str,
    指纹: str,
    版本: int,
    决定: str,
    原因: str,
    t: str,
) -> None:
    conn.execute(
        "INSERT INTO 过滤凭证"
        "(凭证号,端点键,事件号,版本,表达式,指纹,口径,决定,原因,时刻) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            f"FPC-{uuid.uuid4().hex[:12]}",
            端点键,
            事件号,
            版本,
            表达式 or "",
            指纹,
            _CALIBER,
            决定,
            原因,
            t,
        ),
    )


def 建事件(body: dict) -> dict:
    类型 = 校验.非空("事件类型", body.get("事件类型"))
    端点键 = 校验.非空("端点键", body.get("端点键"))
    摘要 = str(body.get("载荷摘要") or "")
    优先级 = str(body.get("优先级") or "普通")
    事件号 = str(body.get("事件号") or f"EVT-{uuid.uuid4().hex[:10]}")
    t = 此刻()
    with 互斥.过滤入队互斥():
        conn = 连接()
        try:
            # 文件/线程互斥闸内再开 SQLite 写事务，保证指纹读取与凭证写入原子。
            conn.execute("BEGIN IMMEDIATE")
            ep = conn.execute("SELECT * FROM 投递端点 WHERE 端点键=?", (端点键,)).fetchone()
            if not ep:
                raise ValueError("端点不存在")
            if int(ep["启用"]) != 1:
                raise ValueError("端点已停用，拒绝入队")

            if conn.execute("SELECT 1 FROM 事件 WHERE 事件号=?", (事件号,)).fetchone():
                raise ValueError("事件号已存在")

            显式表达式 = body.get("过滤表达式")
            if 显式表达式 is None:
                显式表达式 = body.get("过滤器")
            表达式 = str(显式表达式 or "").strip()
            if not 表达式:
                try:
                    表达式 = str(ep["过滤表达式"] or "").strip()
                except (IndexError, KeyError):
                    表达式 = ""
            if not 表达式:
                说明 = str(ep["说明"] or "")
                if 过滤.看起来像表达式(说明):
                    表达式 = 说明.strip()

            检查 = 过滤.校验表达式(表达式)
            版本 = int(ep["过滤版本"] or 0) if "过滤版本" in ep.keys() else 0
            指纹 = 检查["指纹"]
            if not 表达式:
                # 未配置订阅过滤 = 显式 allow-all 策略凭证，而不是“解析异常后恒真”。
                指纹 = 过滤.指纹表达式(f"allow-all:{端点键}:v{版本 or 0}")

            if 表达式 and not 检查["合法"]:
                原因 = f"过滤器恒真短路已拦截；表达式解析失败: {检查['错误']}"
                _凭证(
                    conn,
                    端点键=端点键,
                    事件号=事件号,
                    表达式=表达式,
                    指纹=指纹,
                    版本=版本,
                    决定="拒收",
                    原因=原因,
                    t=t,
                )
                conn.execute(
                    "INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
                    (t, "过滤拒收", 事件号, 原因),
                )
                conn.commit()
                raise ValueError("过滤器表达式解析失败，已拒绝入队；不得静默恒真放行")

            事件上下文 = {
                **body,
                "事件号": 事件号,
                "事件类型": 类型,
                "端点键": 端点键,
                "载荷摘要": 摘要,
                "优先级": 优先级,
            }
            try:
                匹配 = 过滤.表达式匹配(表达式, 事件上下文)
            except Exception as exc:  # noqa: BLE001 - 求值器异常也不得回落恒真
                原因 = f"过滤器求值失败，已拒绝入队: {exc}"
                _凭证(
                    conn,
                    端点键=端点键,
                    事件号=事件号,
                    表达式=表达式,
                    指纹=指纹,
                    版本=版本,
                    决定="拒收",
                    原因=原因,
                    t=t,
                )
                conn.execute(
                    "INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
                    (t, "过滤拒收", 事件号, 原因),
                )
                conn.commit()
                raise ValueError("过滤器表达式求值失败，已拒绝入队；不得静默恒真放行") from exc
            if not 匹配:
                原因 = "事件不匹配端点订阅过滤器"
                _凭证(
                    conn,
                    端点键=端点键,
                    事件号=事件号,
                    表达式=表达式,
                    指纹=指纹,
                    版本=版本,
                    决定="拒收",
                    原因=原因,
                    t=t,
                )
                conn.execute(
                    "INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
                    (t, "过滤拒收", 事件号, 原因),
                )
                conn.commit()
                raise ValueError(原因)

            conn.execute(
                "INSERT INTO 事件("
                "事件号,事件类型,端点键,载荷摘要,优先级,状态,入队时,更新时,"
                "过滤指纹,过滤口径,过滤状态"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (事件号, 类型, 端点键, 摘要, 优先级, "待投", t, t, 指纹, _CALIBER, "已校验准入"),
            )
            _凭证(
                conn,
                端点键=端点键,
                事件号=事件号,
                表达式=表达式,
                指纹=指纹,
                版本=版本,
                决定="准入",
                原因="解析成功且匹配" if 表达式 else "未配置过滤器，策略性全放行",
                t=t,
            )
            conn.execute("INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
                         (t, "入队", 事件号, 类型))
            conn.commit()
            return dict(conn.execute("SELECT * FROM 事件 WHERE 事件号=?", (事件号,)).fetchone())
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
