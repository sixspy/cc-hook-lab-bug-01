"""订阅过滤器的安全解析、指纹与匹配。

这里刻意不使用 ``eval``/``exec``。解析失败统一抛 :class:`FilterParseError`，
由入队事务落“拒收凭证”后 fail-closed，禁止把异常吞掉再返回恒真。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

_BONE = "hook-bug-01-订阅过滤表达式短路"


class FilterParseError(ValueError):
    """过滤器表达式无法安全解析。"""


_TOKEN_RE = re.compile(
    r"""
    \s*(?:
        (?P<string>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')
      | (?P<number>\d+(?:\.\d+)?)
      | (?P<op>==|!=|>=|<=|~=|&&|\|\||[=!<>()])
      | (?P<name>[^\s=!<>()'"]+)
    )
    """,
    re.VERBOSE,
)

_CMP_WORDS = {
    "contains": "contains",
    "包含": "contains",
    "like": "contains",
    "~=": "contains",
}
_FIELD_ALIASES = {
    "type": "事件类型",
    "event_type": "事件类型",
    "event.type": "事件类型",
    "事件类型": "事件类型",
    "endpoint": "端点键",
    "endpoint_key": "端点键",
    "端点": "端点键",
    "端点键": "端点键",
    "id": "事件号",
    "event_id": "事件号",
    "事件号": "事件号",
    "summary": "载荷摘要",
    "payload": "载荷摘要",
    "载荷": "载荷摘要",
    "载荷摘要": "载荷摘要",
    "priority": "优先级",
    "优先级": "优先级",
    "status": "状态",
    "状态": "状态",
}


def 指纹表达式(expression: str | None) -> str:
    text = str(expression or "").strip()
    if not text:
        return ""
    return "fpf_" + hashlib.sha256(f"{_BONE}|{text}".encode("utf-8")).hexdigest()[:24]


def 看起来像表达式(text: str | None) -> bool:
    """区分显式表达式与端点说明里的普通散文。

    旧实现把 ``说明`` 一律当表达式，导致“账务”这种种子说明也参与过滤；
    只有带运算符/括号/代码危险标记的文本才回溯作为旧表达式读取。
    """
    s = str(text or "").strip()
    if not s:
        return False
    if any(token in s for token in ("==", "!=", ">=", "<=", "&&", "||", "->", "raise", "eval(", "exec(", "import ")):
        return True
    if s.startswith(("!", "=", "(")) or any(ch in s for ch in "=<>()"):
        return True
    if re.search(r"\s(and|or|not|contains|包含)\s", s, re.IGNORECASE):
        return True
    return False


def _tokenize(text: str) -> list[tuple[str, Any]]:
    if not isinstance(text, str):
        raise FilterParseError("表达式必须是字符串")
    pos = 0
    tokens: list[tuple[str, Any]] = []
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise FilterParseError(f"无法解析的字符 near {text[pos:pos + 12]!r}")
        if m.group("string") is not None:
            raw = m.group("string")
            tokens.append(("lit", raw[1:-1]))
        elif m.group("number") is not None:
            num = m.group("number")
            tokens.append(("lit", float(num) if "." in num else int(num)))
        elif m.group("op") is not None:
            tokens.append(("op", m.group("op")))
        else:
            tokens.append(("name", m.group("name")))
        pos = m.end()
    tokens.append(("eof", None))
    return tokens


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.tokens = _tokenize(text)
        self.i = 0

    def peek(self) -> tuple[str, Any]:
        return self.tokens[self.i]

    def next(self) -> tuple[str, Any]:
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def expect(self, kind: str, value: Any | None = None) -> Any:
        k, v = self.next()
        if k != kind or (value is not None and v != value):
            raise FilterParseError(f"期望 {kind}:{value!r}，得到 {k}:{v!r}")
        return v

    def parse(self) -> tuple:
        node = self.parse_or()
        if self.peek()[0] != "eof":
            raise FilterParseError(f"表达式尾部无法解析: {self.peek()[1]!r}")
        return node

    def parse_or(self) -> tuple:
        left = self.parse_and()
        while self._logic_op("or"):
            right = self.parse_and()
            left = ("logic", "or", left, right)
        return left

    def parse_and(self) -> tuple:
        left = self.parse_not()
        while self._logic_op("and"):
            right = self.parse_not()
            left = ("logic", "and", left, right)
        return left

    def _logic_op(self, word: str) -> bool:
        kind, value = self.peek()
        if word == "and" and ((kind == "name" and str(value).lower() == "and") or value == "&&"):
            self.next()
            return True
        if word == "or" and ((kind == "name" and str(value).lower() == "or") or value == "||"):
            self.next()
            return True
        return False

    def parse_not(self) -> tuple:
        kind, value = self.peek()
        if (kind == "op" and value == "!") or (kind == "name" and str(value).lower() == "not"):
            self.next()
            return ("unary", "not", self.parse_not())
        return self.parse_comparison()

    def parse_comparison(self) -> tuple:
        left = self.parse_primary()
        kind, value = self.peek()
        op: str | None = None
        if kind == "op" and value in {"==", "!=", ">=", "<=", ">", "<", "=", "~="}:
            self.next()
            op = "==" if value == "=" else value
        elif kind == "name" and str(value).lower() in _CMP_WORDS:
            self.next()
            op = _CMP_WORDS[str(value).lower()]
        if op is None:
            return left
        right = self.parse_primary()
        return ("cmp", op, left, right)

    def parse_primary(self) -> tuple:
        kind, value = self.next()
        if kind == "lit":
            return ("lit", value)
        if kind == "op" and value == "(":
            node = self.parse_or()
            self.expect("op", ")")
            return node
        if kind == "name":
            return ("ref", str(value))
        raise FilterParseError(f"意外的标记 {kind}:{value!r}")


_DANGEROUS_NAMES = {"raise", "import", "eval", "exec", "__import__", "compile"}


def _校验静态节点(node: tuple) -> None:
    kind = node[0]
    if kind == "cmp":
        op = node[1]
        right = node[3]
        if op == "~=" and right[0] == "lit" and isinstance(right[1], str):
            try:
                re.compile(right[1])
            except re.error as exc:
                raise FilterParseError(f"非法正则: {exc}") from exc
        _校验静态节点(node[2])
        _校验静态节点(node[3])
    elif kind == "logic":
        _校验静态节点(node[2])
        _校验静态节点(node[3])
    elif kind == "unary":
        _校验静态节点(node[2])


def 解析表达式(expression: str | None) -> tuple | None:
    text = str(expression or "").strip()
    if not text:
        return None
    if any(bad in text for bad in ("\x00",)):
        raise FilterParseError("表达式包含非法字符")
    # 裸 ``raise`` 会被普通语法当成字段引用；这些代码标记必须明确判为解析失败，
    # 不能留给上层“求值异常后恒真”的旧短路。字符串字面量是 lit，不会误伤。
    tokens = _tokenize(text)
    for typ, value in tokens:
        if typ == "name" and str(value).lower() in _DANGEROUS_NAMES:
            raise FilterParseError(f"非法过滤器表达式代码标记: {value}")
    # 旧实现把 startswith("!") 直接作为解析异常夹具。单独的 ``!bad`` 若被当成
    # “not 一个不存在字段”，会得到 True，仍是恒真短路；这里明确拒收裸标记。
    stripped = text.strip()
    if stripped.startswith("!"):
        rest = stripped[1:].strip()
        if re.fullmatch(r"[^\s=!<>()'\"]+", rest or ""):
            raise FilterParseError("非法过滤器表达式：裸 ! 标记缺少比较条件")
    node = _Parser(text).parse()
    _校验静态节点(node)
    return node


def 校验表达式(expression: str | None) -> dict:
    text = str(expression or "").strip()
    fp = 指纹表达式(text)
    if not text:
        return {"合法": True, "已设置": False, "错误": "", "指纹": "", "归一化": ""}
    try:
        ast = 解析表达式(text)
        return {"合法": True, "已设置": True, "错误": "", "指纹": fp, "归一化": repr(ast)}
    except FilterParseError as exc:
        return {"合法": False, "已设置": True, "错误": str(exc), "指纹": fp, "归一化": ""}


def _summary_fields(summary: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in re.finditer(r"([\w一-鿿_.-]+)\s*[:=]\s*([^;,，；]+)", str(summary or "")):
        out[m.group(1)] = m.group(2).strip()
    return out


def _read_ref(path: str, event: dict[str, Any]) -> Any:
    p = str(path).strip()
    if p in _FIELD_ALIASES:
        return event.get(_FIELD_ALIASES[p])
    low = p.lower()
    if low in _FIELD_ALIASES:
        return event.get(_FIELD_ALIASES[low])
    if "." in p:
        head, tail = p.split(".", 1)
        if head.lower() in {"payload", "载荷", "summary", "载荷摘要"}:
            fields = _summary_fields(str(event.get("载荷摘要") or event.get("summary") or ""))
            return fields.get(tail)
    if p in event:
        return event[p]
    fields = _summary_fields(str(event.get("载荷摘要") or ""))
    return fields.get(p)


def _as_number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _compare(op: str, left: Any, right: Any) -> bool:
    if op == "contains":
        return left is not None and str(right) in str(left)
    if op == "~=":
        try:
            return left is not None and re.search(str(right), str(left)) is not None
        except re.error as exc:
            raise FilterParseError(f"非法正则: {exc}") from exc
    ln, rn = _as_number(left), _as_number(right)
    if ln is not None and rn is not None and str(left).strip() != "" and isinstance(left, (int, float, str)):
        if op == "==":
            return ln == rn
        if op == "!=":
            return ln != rn
        if op == ">":
            return ln > rn
        if op == "<":
            return ln < rn
        if op == ">=":
            return ln >= rn
        if op == "<=":
            return ln <= rn
    a = "" if left is None else str(left)
    b = "" if right is None else str(right)
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    if op == ">":
        return a > b
    if op == "<":
        return a < b
    if op == ">=":
        return a >= b
    if op == "<=":
        return a <= b
    raise FilterParseError(f"不支持的运算符 {op}")


def _eval(node: tuple, event: dict[str, Any]) -> Any:
    kind = node[0]
    if kind == "lit":
        return node[1]
    if kind == "ref":
        return _read_ref(node[1], event or {})
    if kind == "unary":
        return not bool(_eval(node[2], event))
    if kind == "logic":
        left = bool(_eval(node[2], event))
        if node[1] == "and":
            return left and bool(_eval(node[3], event))
        return left or bool(_eval(node[3], event))
    if kind == "cmp":
        return _compare(node[1], _eval(node[2], event), _eval(node[3], event))
    raise FilterParseError("未知表达式节点")


def 表达式匹配(expression: str | None, event: dict[str, Any]) -> bool:
    """空表达式视为该端点未订阅过滤，放行；非法表达式抛出而非恒真。"""
    ast = 解析表达式(expression)
    if ast is None:
        return True
    return bool(_eval(ast, event or {}))
