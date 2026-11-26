from __future__ import annotations

import uuid

from app.db import 连接
from app.services import 校验, 过滤, 互斥
from app.services.时间 import 此刻

_READ_REF = "cutover_book"
_CALIBER = "独立凭证切流/cutover_book"


def _插入钉凭证(
    conn,
    端点键: str,
    表达式: str,
    版本: int,
    决定: str,
    原因: str = "",
    事件号: str = "",
    时刻值: str | None = None,
) -> str:
    校验结果 = 过滤.校验表达式(表达式)
    指纹 = 校验结果["指纹"] or 过滤.指纹表达式(f"empty:{端点键}:{版本}")
    凭证号 = f"FPC-{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO 过滤凭证"
        "(凭证号,端点键,事件号,版本,表达式,指纹,口径,决定,原因,时刻) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            凭证号,
            端点键,
            事件号,
            版本,
            表达式 or "",
            指纹,
            _CALIBER,
            决定,
            原因,
            时刻值 or 此刻(),
        ),
    )
    return 指纹


def 登记(body: dict) -> dict:
    端点键 = 校验.非空("端点键", body.get("端点键"))
    名称 = 校验.非空("名称", body.get("名称"))
    url = 校验.非空("回调URL", body.get("回调URL"))
    版本 = str(body.get("密钥版本") or "v1")
    说明 = str(body.get("说明") or "")
    表达式 = str(body.get("过滤表达式") if body.get("过滤表达式") is not None else body.get("过滤器") or "").strip()
    if not 表达式 and 过滤.看起来像表达式(说明):
        表达式 = 说明.strip()
    校验结果 = 过滤.校验表达式(表达式)
    过滤态 = "未设置" if not 表达式 else ("有效" if 校验结果["合法"] else "解析失败")
    fp = 校验结果["指纹"]
    t = 此刻()
    with 互斥.过滤入队互斥():
        conn = 连接()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM 投递端点 WHERE 端点键=?", (端点键,)).fetchone():
                raise ValueError("端点键已存在")
            conn.execute(
                "INSERT INTO 投递端点("
                "端点键,名称,回调URL,密钥版本,启用,说明,"
                "过滤表达式,过滤指纹,过滤态,过滤版本"
                ") VALUES (?,?,?,?,1,?,?,?,?,1)",
                (端点键, 名称, url, 版本, 说明, 表达式, fp, 过滤态),
            )
            if 表达式:
                _插入钉凭证(
                    conn,
                    端点键,
                    表达式,
                    1,
                    "钉版" if 校验结果["合法"] else "非法表达式钉版",
                    "" if 校验结果["合法"] else 校验结果["错误"],
                    时刻值=t,
                )
            conn.execute("INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
                         (t, "登记端点", "", 端点键))
            conn.commit()
            return dict(conn.execute("SELECT * FROM 投递端点 WHERE 端点键=?", (端点键,)).fetchone())
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def 改启用(端点键: str, 启用: bool) -> dict:
    conn = 连接()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM 投递端点 WHERE 端点键=?", (端点键,)).fetchone():
            raise ValueError("无此端点")
        conn.execute("UPDATE 投递端点 SET 启用=? WHERE 端点键=?", (1 if 启用 else 0, 端点键))
        conn.commit()
        return dict(conn.execute("SELECT * FROM 投递端点 WHERE 端点键=?", (端点键,)).fetchone())
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def 改过滤器(端点键: str, 表达式: str, 预期指纹: str | None = None) -> dict:
    """原子钉住端点当前过滤器版本与指纹。

    与入队共用同一把文件/线程互斥闸，并在闸内使用 ``BEGIN IMMEDIATE``：
    每个入队凭证要么对应旧版本，要么对应新版本，不可能读到新表达式却钉旧指纹。
    """
    表达式 = str(表达式 or "").strip()
    校验结果 = 过滤.校验表达式(表达式)
    if 表达式 and not 校验结果["合法"]:
        # 允许先写入坏表达式以便复现/修正，但状态必须是解析失败，不能装绿。
        过滤态 = "解析失败"
    elif 表达式:
        过滤态 = "有效"
    else:
        过滤态 = "未设置"
    fp = 校验结果["指纹"]
    t = 此刻()
    with 互斥.过滤入队互斥():
        conn = 连接()
        try:
            conn.execute("BEGIN IMMEDIATE")
            ep = conn.execute("SELECT * FROM 投递端点 WHERE 端点键=?", (端点键,)).fetchone()
            if not ep:
                raise ValueError("无此端点")
            current_fp = ep["过滤指纹"] if "过滤指纹" in ep.keys() else ""
            if 预期指纹 and current_fp and 预期指纹 != current_fp:
                raise ValueError("过滤器指纹已变化，请刷新后重试，拒绝覆盖钉版")
            try:
                next_version = int(ep["过滤版本"] or 0) + 1
            except (KeyError, TypeError, ValueError):
                next_version = 1
            conn.execute(
                "UPDATE 投递端点 SET 过滤表达式=?, 过滤指纹=?, 过滤态=?, 过滤版本=? "
                "WHERE 端点键=?",
                (表达式, fp, 过滤态, next_version, 端点键),
            )
            _插入钉凭证(
                conn,
                端点键,
                表达式,
                next_version,
                "钉版" if 校验结果["合法"] else "非法表达式钉版",
                校验结果.get("错误", ""),
                时刻值=t,
            )
            conn.execute(
                "INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
                (t, "改过滤器", "", f"{端点键}@{next_version}:{fp}"),
            )
            conn.commit()
            row = dict(conn.execute("SELECT * FROM 投递端点 WHERE 端点键=?", (端点键,)).fetchone())
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    # 徽标读自己的新连接，放在事务和主连接关闭之后，避免争用期自读锁。
    row["徽标"] = 过滤器徽标(端点键)
    return row


def 过滤器徽标(端点键: str) -> dict:
    """端点服务真相灯：不再因端点存在就假绿，更不允许恒真短路标记。"""
    conn = 连接()
    try:
        ep = conn.execute("SELECT * FROM 投递端点 WHERE 端点键=?", (端点键,)).fetchone()
        if not ep:
            return {
                "端点键": 端点键,
                "过滤已生效": False,
                "过滤生效": False,
                "有效": False,
                "短路放行": False,
                "恒真短路": False,
                "解析状态": "端点不存在",
                "状态": "端点不存在",
                "指纹": "",
                "active_read": _READ_REF,
            }
        keys = ep.keys()
        expr = ep["过滤表达式"] if "过滤表达式" in keys else ""
        if not expr and "说明" in keys and 过滤.看起来像表达式(ep["说明"]):
            expr = ep["说明"]
        checked = 过滤.校验表达式(expr)
        if not expr:
            state = "未设置"
            effective = False
        elif checked["合法"]:
            state = "有效"
            effective = True
        else:
            state = "解析失败"
            effective = False
        fp = (ep["过滤指纹"] if "过滤指纹" in keys and ep["过滤指纹"] else checked["指纹"]) or ""
        return {
            "端点键": 端点键,
            "过滤已生效": effective,
            "过滤生效": effective,
            "有效": effective,
            # 修复后任何解析失败都 fail-closed；这两个键是明确的旧假绿反指标。
            "短路放行": False,
            "恒真短路": False,
            "解析状态": state,
            "状态": state,
            "错误": checked.get("错误", ""),
            "指纹": fp,
            "版本": ep["过滤版本"] if "过滤版本" in keys else 0,
            "active_read": _READ_REF,
            "口径": _CALIBER,
        }
    finally:
        conn.close()
