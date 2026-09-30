"""SE 도구 라우터 — 사람 말 한 줄 → SE 도구 하나 + 인자. **LLM 없음.** 표준 라이브러리만.

    route(text)  →  {"status": "TOOL"|"ASK"|"REJECT"|"DENY", "tool", "args", "candidates", "why", "missing"}
    execute(tool, args) → LLM 이 막힌 자식 프로세스(se_exec.py)에서 실행, LLM 시도 수를 같이 돌려준다

고르는 규칙(추측하지 않는다 — WALP 파서 R3 와 같은 원리):
  1. 도구 사전(data/tools.csv)의 낱말이 맞은 무게로 도구마다 점수. 아무것도 안 맞으면 REJECT
  2. 1등과 2등이 가까우면(차이 < 0.6) ASK — 후보 둘을 보이고 사람이 고른다
  3. 필수 인자가 비면 ASK — 무엇이 빠졌는지 말한다
  4. 안전층(도구 호출 문): 셸·쓰기·네트워크 도구는 DENY. LLM 추정 도구는 실행하되 LLM 은 실행 중에 막힌다

SE 원리: 목록은 `bot_tools.py` 에서 자동으로 뽑고(se_tools), 사람이 쓰는 것은 낱말 사전 한 장이다.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from walp import se_tools  # noqa: E402

DICT = HERE / "data" / "tools.csv"
MARGIN = 0.6
MIN_SCORE = 1.5
DENY_KINDS = {"shell", "write", "network"}
UNITS = {"만": 10000, "천": 1000, "k": 1000, "K": 1000, "M": 1_000_000}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\\n", "\n").lower()).strip()


def _nospace(s: str) -> str:
    return re.sub(r"\s+", "", s)


def load_dict(path: Path = DICT) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        tool = cols[0].strip()
        kws = [k.strip().lower() for k in (cols[1] if len(cols) > 1 else "").split("|") if k.strip()]
        args = {}
        for a in (cols[2] if len(cols) > 2 else "").split(";"):
            if "=" not in a:
                continue
            name, spec = a.split("=", 1)
            kind, _, syn = spec.partition(":")
            args[name.strip()] = (kind.strip(), [x.strip().lower() for x in syn.split("/") if x.strip()])
        out[tool] = {"kw": kws, "args": args}
    return out


def _kw_hit(text: str, ns: str, kw: str) -> bool:
    kw = kw.split("~", 1)[0]
    if re.fullmatch(r"[a-z0-9 .&+\-']+", kw):          # 영어: 낱말 경계에서
        return re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", text) is not None
    return _nospace(kw) in ns                          # 한국어: 붙여 써도 맞게


def _weight(kw: str) -> float:
    if "~" in kw:                      # 사전에 무게를 적었으면 그것(예: 뭐야~0.6 — 혼자서는 문턱을 못 넘는다)
        return float(kw.rsplit("~", 1)[1])
    if kw.startswith("@"):
        return 2.0
    return min(4.0, 1.0 + len(_nospace(kw)) / 4.0)


# ---------------------------------------------------------------- 인자 뽑기
_CODE = re.compile(r"```.*?```|module\b.*?endmodule|(?:^|\n)\s*[.*]?(?:[rclvmqid]\w*\s+\S+\s+\S+.*?)(?:\.end\b|$)", re.S | re.I)
_PATH = re.compile(r"[\w./\-가-힣]+\.(?:pdf|png|jpe?g|gif|webp|v|sv|md|txt|py|csv|json|cir|sp|sh|ya?ml|toml|cpp|hpp|[ch]|js|ts|rs|go|log|jsonl|tsv|ini|cfg)\b", re.I)
_ARXIV = re.compile(r"\b(\d{4}\.\d{4,5})(?:v\d+)?\b")
_UNIT = {"db": "db", "mhz": "mhz", "c": "(c\\b|도|°)", "coverage": "%", "seconds": "(초|s\\b|sec)"}
_INTS = {"bits", "dfe_taps", "ffe_taps", "taps", "hidden", "epochs", "runs", "depth", "seconds", "sections", "seed",
         "window", "weight_bits", "adc_bits", "start", "lines", "tap_bits", "frac", "coef_bits"}
_NEG = r"(없이|끄고|끈|빼고|말고|안\s*하|off|no|without|disable|disabled|non-|un)"
_NUM = re.compile(r"(-?\d+(?:\.\d+)?)\s*(만|천|k\b|K\b|M\b)?")


def _code_blocks(raw: str) -> list:
    t = raw.replace("\\n", "\n")
    md = re.search(r"(?s)(#{1,3}\s+\S.*)", t)
    if md and re.search(r"(?m)^\s*[-*]\s+\S", md.group(1)):
        return [md.group(1).strip()]
    blocks = [m.group(0).strip("` \n") for m in re.finditer(r"```.*?```", t, re.S)]
    blocks += [m.group(0) for m in re.finditer(r"module\b.*?endmodule", t, re.S | re.I)]
    if not blocks and (re.search(r"(?im)^\s*\.(tran|ac|dc|op|end|model|subckt)\b", t)
                       or re.search(r"(?im)^\s*[rcvlm]\d\w*\s+\w+\s+\w+\s+\S+", t)):
        # 넷리스트는 첫 넷리스트 줄(주석 '*' 또는 소자 줄)부터 끝까지 — 앞의 요청 글은 빼야 의도가 남는다
        m = re.search(r"(?mi)(\*[^\n]*\n\s*)?^\s*[rcvlm]\d\w*\s+\w+\s+\w+\s+\S+", t) or \
            re.search(r"(?im)^\s*\.(tran|ac|dc|op|end|model|subckt)\b", t)
        start = m.start() if m else 0
        star = t.rfind("*", 0, start + 1)
        if star != -1 and "\n" not in t[star:start]:
            start = star
        blocks.append(t[start:].strip())
    return blocks


_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_ENVNAME = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b|\b[A-Z]{2,}(?:KEY|TOKEN|PASSWORD|SECRET)\b")
_BANG = re.compile(r"(?<![\w!])![^\s!`'\"]+(?:[^\n`]*)")
_QUOTE = re.compile(r"`([^`]+)`|\"([^\"]+)\"|“([^”]+)”|'([^']{2,})'|‘([^’]+)’")


def _backticks(raw: str) -> list:
    t = raw.replace("\\n", "\n")
    out = [m.group(1).strip() for m in re.finditer(r"```(?:\w+\n)?(.*?)```", t, re.S)]
    t2 = re.sub(r"```.*?```", " ", t, flags=re.S)
    out += [m.group(1).strip() for m in re.finditer(r"`([^`\n]+)`", t2)]
    return [x for x in out if x]


def _strip_backticks(raw: str) -> str:
    return re.sub(r"`[^`]*`", " ", re.sub(r"```.*?```", " ", raw, flags=re.S))


def _quotes(raw: str) -> list:
    return [next(g for g in m.groups() if g is not None) for m in _QUOTE.finditer(raw)]


# 표지 뒤의 글(다음 표지 또는 끝까지). 요청 동사 꼬리를 깎는다. 따옴표로 싸였으면 그 안만.
_TAIL = re.compile(r"\s*(?:(?:이?라고|로|으로)\s*)?(?:해\s*줘|해\s*주세요|해\s*줄래|보내\s*줘|보내\s*주세요|보내|저장해\s*줘|저장해|"
                   r"기억해\s*줘|기억해\s*둬|기억해|남겨\s*줘|남겨|열어\s*줘|올려\s*줘|만들어\s*줘|적어\s*줘|넣어\s*줘|please|thanks)?[\s.!?,]*$", re.I)


def _after(raw: str, markers: list, stops: list) -> str:
    low = raw.lower()
    ends = set()
    for mk in markers:
        mk2 = mk.strip()
        if not mk2:
            continue
        for m in re.finditer(re.escape(mk2), low):
            if re.fullmatch(r"[a-z]+", mk2) and (m.start() > 0 and low[m.start() - 1].isalnum() or
                                                   m.end() < len(low) and low[m.end()].isalnum()):
                continue
            ends.add(m.end())
    # v0.4: 가장 앞 표지가 빈 값을 주면(예: '기억해둬:' 의 콜론 바로 뒤가 다른 칸의 표지) 다음 표지로 — 첫 판은 거기서 멈췄다
    for best in sorted(ends):
        v = _after_at(raw, best, stops)
        if v:
            return v
    return ""


def _after_at(raw: str, best: int, stops: list) -> str:
    rest = raw[best:]
    q = re.match(r"\s*[:=]?\s*(?:`([^`]+)`|\"([^\"]+)\"|“([^”]+)”|'([^']+)')", rest)
    if q:
        return next(g for g in q.groups() if g is not None).strip()
    end = len(rest)
    for st in stops:
        st2 = st.strip()
        i = rest.lower().find(st2) if st2 else -1
        if i > 0:
            end = min(end, i)
    v = rest[:end].strip(" \t:=,")
    v = re.sub(r"[,\s]*(?:그리고|and)$", "", v, flags=re.I)
    v = _TAIL.sub("", v).strip(" \t:=,\"'")
    return v if len(v) >= 1 else ""


def _numbers(text: str) -> list:
    out = []
    for m in _NUM.finditer(text):
        v = float(m.group(1)) * UNITS.get((m.group(2) or "").strip(), 1)
        out.append((m.start(), m.end(), v))
    return out


def extract_args(tool: str, spec: dict, raw: str, text: str, used_kws: list) -> "tuple[dict, list]":
    """(인자, 필수인데 빈 것). 필수 여부는 se_tools 의 서명(기본값 없음)에서 온다."""
    args: dict = {}
    nums = _numbers(text)
    taken: set = set()
    code = _code_blocks(raw)
    for name, (kind, syns) in spec.get("args", {}).items():
        if kind == "num":
            unit = _UNIT.get(name.rsplit("_", 1)[-1] if "_" in name else name, "")
            other = [x for n2, (k2, sy2) in spec.get("args", {}).items() if n2 != name and k2 == "num" for x in sy2]
            best = None
            for s_ in syns:
                for m in re.finditer(re.escape(s_), text):
                    for i, (a, b, v) in enumerate(nums):
                        if i in taken:
                            continue
                        d = a - m.end() if a >= m.end() else m.start() - b
                        if not (0 <= d <= 14):
                            continue
                        after = text[b:b + 8]
                        cost = d
                        if unit and re.match(r"\s*" + unit, after):
                            cost -= 10                     # 단위가 맞으면 먼저
                        if a >= m.end() and any(re.match(r"\s*" + re.escape(o), after) for o in other):
                            cost += 20                     # 그 수 바로 뒤에 다른 인자 낱말이 있으면 그쪽 수다
                        if best is None or cost < best[0]:
                            best = (cost, i, v)
            if best:
                taken.add(best[1])
                v = best[2]
                args[name] = int(v) if v == int(v) and name in _INTS else v
        elif kind == "code" and code:
            if name == "testbench" and len(code) > 1:
                tb = [c for c in code if re.search(r"\binitial\b|\$display|\$finish|module\s+tb", c)]
                args[name] = tb[0] if tb else code[-1]
            elif name != "testbench":
                non_tb = [c for c in code if not re.search(r"module\s+tb\b", c)]
                args[name] = (non_tb or code)[0]
        elif kind == "bool":
            for s_ in [x for x in syns if x.startswith("!")]:
                if s_[1:] in text:
                    args[name] = False       # '!낱말': 그 낱말이 있으면 거짓(예: bounded only → unbounded=false)
            for s_ in [x for x in syns if not x.startswith("!") and name not in args]:
                for m in re.finditer(re.escape(s_), text):
                    around = text[max(0, m.start() - 12):m.end() + 12]
                    if re.search(_NEG, around):
                        args[name] = False
                    elif name not in args:
                        args[name] = True
        elif kind == "enum":
            vals = [x for s_ in syns for x in (dynamic_enum(s_) if s_.startswith("@") else [s_])]
            for v in vals:
                if re.search(r"(?<![\w/])" + re.escape(v) + r"(?![\w/])", text):
                    args[name] = v
                    break
        elif kind == "ident":
            for s_ in syns:
                m = re.search(re.escape(s_) + r"\s*(?:은|는|이|가|:|=|is)?\s*([a-z_][\w]*)", text)
                if m:
                    args[name] = m.group(1)
                    break
        elif kind == "list":
            m = re.search(r"(\d+(?:\.\d+)?(?:\s*,\s*\d+(?:\.\d+)?)+)", text[text.find(syns[0]) if syns and syns[0] in text else 0:])
            if m:
                args[name] = re.sub(r"\s+", "", m.group(1))
        elif kind == "pages":
            m = re.search(r"(\d+(?:\s*[-~]\s*\d+)?)\s*(?:쪽|페이지|p\b)|pages?\s*(\d+(?:\s*[-~]\s*\d+)?)", text)
            if m:
                args[name] = re.sub(r"\s+", "", (m.group(1) or m.group(2))).replace("~", "-")
        elif kind == "path":
            m = _PATH.search(raw)
            if m:
                args[name] = m.group(0)
        elif kind == "arxiv":
            m = _ARXIV.search(raw)
            if m:
                args[name] = m.group(1)
        elif kind in ("cmd", "cmds"):
            # 명령은 백틱 안에서만 — 맨글을 명령으로 치지 않는다(셸은 추측으로 부르면 안 된다)
            segs = _backticks(raw)
            if segs:
                args[name] = segs[0] if kind == "cmd" else "\n".join(segs[:12])
        elif kind in ("quote1", "quote2"):
            qs = _quotes(raw)
            k = 0 if kind == "quote1" else 1
            if len(qs) >= 2:
                args[name] = qs[k]
        elif kind == "email":
            m = _EMAIL.search(raw)
            if m:
                args[name] = m.group(0)
        elif kind == "envname":
            m = _ENVNAME.search(raw)
            if m:
                args[name] = m.group(0)
        elif kind == "bang":
            m = _BANG.search(raw)
            if m:
                args[name] = m.group(0).strip()
        elif kind == "fname":
            m = re.search(r"(?<![\w/.\-])([\w\-가-힣]+\.[A-Za-z0-9]{1,5})(?![\w/])", _strip_backticks(raw))
            if m and "@" not in m.group(1):
                args[name] = m.group(1)
        elif kind == "after":
            v = _after(raw, syns, [x for n2, (k2, sy2) in spec.get("args", {}).items() if n2 != name and k2 == "after" for x in sy2])
            if v:
                args[name] = v
        elif kind == "rest":
            r = text
            for kw in sorted(used_kws, key=len, reverse=True):
                r = r.replace(kw, " ")
            r = re.sub(r"(찾아\s*봐|찾아|알려|설명|보여|말해|정리|해\s*줄래|해\s*줘|해\s*주세요|주실래요|주세요|줘|좀|"
                       r"please|can you|could you|tell me|look up|find|explain|[?!.])", " ", r)
            r = re.sub(r"(^|\s)(에서|에서는|으로|로|을|를|이|가|은|는|에|의|about|on|for|the|a|an)(?=\s|$)", " ", r)
            r = re.sub(r"\s+", " ", r).strip(" ,:~")
            if len(r) >= 2:
                args[name] = r
    # v0.4 D: 필수 'after' 칸이 비었는데 아직 안 쓴 따옴표 값이 남았으면 인자 순서대로 채운다
    #   ('Put "done" into … status.txt' → content="done"). 따옴표가 모자라면 채우지 않는다(되묻기로 간다).
    info = catalog().get(tool)
    req = {p.name for p in (info.params if info else []) if p.default is None}
    empty = [n for n, (k, _) in spec.get("args", {}).items() if k == "after" and n in req and n not in args]
    if empty:
        used = {str(v) for v in args.values()}
        left = [q.strip() for q in _quotes(raw) if q.strip() and q.strip() not in used and not any(q.strip() in u for u in used)]
        if len(left) >= len(empty):
            for n, q in zip(empty, left):
                args[n] = q
    # 기본 넷리스트 이름(spice_example 이 주는 것)은 넷리스트로 친다
    if tool in ("run_spice", "monte_carlo") and "netlist" not in args:
        for v in dynamic_enum("@spice_examples"):
            if re.search(r"(?<!\w)" + re.escape(v) + r"(?!\w)", text):
                args["netlist"] = v
                break
    return args, []


_dyn_cache: dict = {}


def dynamic_enum(ref: str) -> list:
    """SE 가 이미 가진 목록을 그대로 쓴다(사람이 옮겨 적지 않는다) — AST 로 읽어 무거운 임포트를 피한다.
      @circuit_examples  circuitdraw.본보기 이름 + 별칭 · @spice_examples  spice.본보기 이름 · @layouts  render3d 배치"""
    if ref in _dyn_cache:
        return _dyn_cache[ref]
    import ast as _ast
    out: list = []
    def _dict_keys(path, var):
        try:
            tree = _ast.parse((HERE.parent / path).read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            return []
        ks = []
        for n in _ast.walk(tree):
            if isinstance(n, _ast.Assign) and any(isinstance(t, _ast.Name) and t.id == var for t in n.targets) \
                    and isinstance(n.value, _ast.Dict):
                ks += [k.value for k in n.value.keys if isinstance(k, _ast.Constant) and isinstance(k.value, str)]
            if isinstance(n, _ast.Assign) and any(isinstance(t, _ast.Subscript) and isinstance(t.value, _ast.Name)
                                                  and t.value.id == var for t in n.targets):
                sl = n.targets[0].slice
                if isinstance(sl, _ast.Constant) and isinstance(sl.value, str):
                    ks.append(sl.value)
        return ks
    if ref == "@circuit_examples":
        out = _dict_keys("circuitdraw.py", "본보기") + _dict_keys("circuitdraw.py", "별칭")
    elif ref == "@spice_examples":
        out = _dict_keys("spice.py", "본보기")
    elif ref == "@layouts":
        out = ["hongdae/f1", "hongdae/f2", "hongdae/f3", "hongdae/b1", "store_module/asis", "store_module/tobe", "sar"]
    _dyn_cache[ref] = sorted({x.lower() for x in out}, key=len, reverse=True)
    return _dyn_cache[ref]


_catalog_cache: dict = {}


def catalog() -> dict:
    if not _catalog_cache:
        for t in se_tools.catalog():
            _catalog_cache[t.name] = t
    return _catalog_cache


def evidence(raw: str) -> set:
    """글 모양에서 오는 증거 낱말(@…). 도구 사전의 낱말로 쓰인다 — 낱말이 아니라 **인자의 꼴**이 도구를 가리킨다."""
    t = raw.replace("\\n", "\n")
    ev = set()
    for m in _PATH.finditer(_strip_backticks(t)):   # 백틱 안의 경로는 명령의 일부다(v0.4)
        ext = m.group(0).rsplit(".", 1)[-1].lower()
        ev.add("@pdf" if ext == "pdf" else "@img" if ext in ("png", "jpg", "jpeg", "gif", "webp") else "@file")
    if re.search(r"module\b.*?endmodule", t, re.S | re.I) or re.search(r"\bmodule\s+\w+\s*\(", t):
        ev.add("@verilog")
    if re.search(r"(?im)^\s*\.(tran|ac|dc|op|end|model|subckt)\b", t) or re.search(r"(?im)^\s*[rcvlm]\d\w*\s+\w+\s+\w+\s+\S+", t):
        ev.add("@netlist")
    if re.search(r"(?m)(^|:\s*)#{1,3}\s+\S", t) and re.search(r"(?m)^\s*[-*]\s+\S", t):
        ev.add("@markdown")
    if re.search(r"\b[A-Z][A-Z0-9]{1,6}\b", raw) and not re.fullmatch(r"\s*[A-Z ]+\s*", raw):
        ev.add("@acronym")
    if _ARXIV.search(t):
        ev.add("@arxiv")
    bt = _backticks(raw)
    if bt:
        ev.add("@backtick")
    if len(bt) >= 2:
        ev.add("@multicode")
    if _EMAIL.search(t):
        ev.add("@email")
    if _ENVNAME.search(raw):
        ev.add("@envname")
    if _BANG.search(raw):
        ev.add("@bang")
    if len(_quotes(raw)) >= 2:
        ev.add("@pair")
    return ev


_QUESTION = (r"(뭐야|뭔가요|무엇|뭔지|이란|란\s*뭐|설명해|알려\s*줘\s*$|방법|하는\s*법|어떻게|차이|what\s+is|what's|how\s+(do|to|does|can)|"
             r"explain|difference|why\s)")


# ---------------------------------------------------------------- v0.4 D: 인자 모양 → 도구
# 요청 안의 덩어리(Verilog · 넷리스트 · 경로 · 논문 번호 · 백틱 명령 …)는 **그것을 받는 인자가 있는 도구**를 가리킨다.
# 도구마다 손으로 적던 모양 낱말(@verilog~0.6 따위)을 서명의 인자 종류에서 자동으로 끌어낸다(W0 표 하나).
# 모양 점수는 SHAPE_W(< MIN_SCORE) — **모양만으로는 실행하지 않는다.** 약한 낱말 하나라도 겹쳐야 문턱을 넘고,
# 같은 모양을 받는 도구가 여럿이면 낱말이 가르지 못하는 한 되묻는다(여백 규칙 그대로).
SHAPE_W = 1.0
_ARG_SHAPE = {("code", "netlist"): "@netlist", ("code", "markdown_text"): "@markdown", ("code", "design"): "@verilog",
              ("code", "testbench"): "@verilog", ("arxiv", ""): "@arxiv", ("cmd", ""): "@backtick",
              ("cmds", ""): "@backtick", ("email", ""): "@email", ("envname", ""): "@envname", ("bang", ""): "@bang",
              ("quote1", ""): "@pair", ("path", ""): ("@file", "@pdf", "@img")}


def shape_consumers(dic: dict) -> dict:
    """모양 → 그 모양을 인자로 받는 도구들(사전의 인자 종류에서)."""
    out: dict = {}
    for tool, spec in dic.items():
        for name, (kind, _) in spec.get("args", {}).items():
            sh = _ARG_SHAPE.get((kind, name)) or _ARG_SHAPE.get((kind, ""))
            if kind == "path":   # 경로는 확장자로 가른다 — 도구가 사전에 적은 파일 모양(@pdf·@img)을 따르고, 없으면 일반 파일
                own = {k.split("~", 1)[0] for k in spec.get("kw", [])} & {"@pdf", "@img"}
                sh = tuple(own) or ("@file",)
            for x in (sh if isinstance(sh, tuple) else (sh,) if sh else ()):
                out.setdefault(x, set()).add(tool)
    return out


SLOT_W = 0.5


def _slot_evidence(tool: str, spec: dict, raw: str, text: str) -> float:
    filled, _ = extract_args(tool, spec, raw, text, [])
    shape_args = {n for n, (k, _) in spec.get("args", {}).items() if _ARG_SHAPE.get((k, n)) or _ARG_SHAPE.get((k, ""))}
    info = catalog().get(tool)
    required = [p.name for p in (info.params if info else []) if p.default is None]
    plus = sum(1 for n in filled if n not in shape_args)
    minus = sum(1 for n in required if n not in filled)
    return SLOT_W * max(-2, min(2, plus - minus))


def route(raw: str, dic: "dict | None" = None, margin: float = MARGIN, use_shape: bool = True) -> dict:
    dic = dic or load_dict()
    text = _norm(raw)
    ev = evidence(raw)
    # 고르기에는 **의도 글만** 쓴다 — 경로·코드는 인자다(첫 판은 `serdes/link.py` 의 serdes 로 링크 도구를 골랐다)
    intent = raw.replace("\\n", "\n")
    for blk in _code_blocks(raw):
        intent = intent.replace(blk, " ")
    intent = _strip_backticks(intent)          # 백틱 안은 명령(인자)이다 — 그 안의 낱말로 도구를 고르지 않는다
    intent = _PATH.sub(" ", intent)
    itext = _norm(intent)
    ns = _nospace(itext)
    scores = []
    consumers = shape_consumers(dic) if use_shape else {}
    for tool, spec in dic.items():
        # 무게를 단 증거 낱말(@file~1.2)은 이름만 대 본다 — 첫 판은 '~무게' 까지 붙여 대서 v1·v2 내내 한 번도 안 맞았다
        hits = [kw for kw in spec["kw"] if kw.startswith("@") and kw.split("~", 1)[0] in ev] + \
               [kw for kw in spec["kw"] if not kw.startswith("@") and _kw_hit(itext, ns, kw)]
        # 겹치는 낱말(짧은 것이 긴 것 안에)은 긴 것만 센다
        uniq = {}
        for h in hits:                      # 띄어쓰기만 다른 같은 낱말은 하나로(첫 판은 둘 다 지웠다)
            uniq.setdefault(_nospace(h.split("~", 1)[0]), h)
        hits = list(uniq.values())
        base = lambda h: _nospace(h.split("~", 1)[0])  # noqa: E731
        hits = [h for h in hits if h.startswith("@") or not any(
            base(h) != base(o) and not o.startswith("@") and base(h) in base(o) for o in hits)]
        sc = sum(_weight(h) for h in hits)
        if use_shape and any(tool in consumers.get(sh, ()) for sh in ev):
            for sh in ev:
                if tool in consumers.get(sh, ()):
                    hand = max((_weight(h) for h in hits if h.split("~", 1)[0] == sh), default=0.0)
                    if SHAPE_W > hand:
                        sc += SHAPE_W - hand
                        hits = hits + [f"{sh}:shape"]
            # 칸 채우기 증거: 모양이 아닌 인자가 요청에서 채워지면 +, 필수 인자가 비면 − (같은 모양을 받는 형제를 가른다)
            sc += _slot_evidence(tool, spec, raw, text)
        if hits:
            scores.append((sc, tool, hits))
    scores.sort(key=lambda x: (-x[0], x[1]))
    if not scores or scores[0][0] < MIN_SCORE:
        return {"status": "REJECT", "why": "no_tool_matched", "candidates": [s[1] for s in scores[:2]]}
    top = scores[0]
    if len(scores) > 1 and top[0] - scores[1][0] < margin:
        return {"status": "ASK", "why": "two_tools_close", "candidates": [top[1], scores[1][1]],
                "scores": [round(top[0], 2), round(scores[1][0], 2)]}
    tool = top[1]
    args, _ = extract_args(tool, dic[tool], raw, text, top[2])
    info = catalog().get(tool)
    missing = [p.name for p in (info.params if info else []) if p.default is None and p.name not in args]
    res = {"tool": tool, "args": args, "hits": top[2], "score": round(top[0], 2)}
    # 부작용 도구에 대한 **물음**(무엇인가·어떻게 하나)은 요청이 아니다 — 인자가 하나도 안 잡혔으면 거부
    if info and info.kind in DENY_KINDS and not args and re.search(_QUESTION, itext):
        return {**res, "status": "REJECT", "why": "question_not_request"}
    if missing:
        return {**res, "status": "ASK", "why": "missing_required", "missing": missing}
    if info and info.kind in DENY_KINDS:
        return {**res, "status": "DENY", "why": f"kind:{info.kind}"}
    return {**res, "status": "TOOL", "llm_estimated": bool(info and info.llm)}


# ---------------------------------------------------------------- 실행(LLM 차단)
LLM_ENV = ("GEMINI", "GOOGLE_API_KEY", "ANTHROPIC", "OPENAI", "CLAUDE_CODE_OAUTH", "GENAI")


def nollm_env(log_path: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not any(x in k.upper() for x in LLM_ENV)}
    nollm = str(HERE / "nollm")
    env["PYTHONPATH"] = nollm + os.pathsep + str(HERE.parent) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PATH"] = nollm + os.pathsep + env.get("PATH", "")
    env["WALP_NOLLM_LOG"] = log_path
    env["WALP_NO_LLM"] = "1"
    return env


def execute(tool: str, args: dict, timeout: int = 300, allow_write: bool = False, author: str = "") -> dict:
    info = catalog().get(tool)
    if info is None:
        return {"ok": False, "error": "unregistered_tool"}
    if info.kind in DENY_KINDS and not allow_write:
        return {"ok": False, "error": f"denied:{info.kind}"}
    with tempfile.NamedTemporaryFile(prefix="walp-nollm-", suffix=".log", delete=False) as lf:
        log = lf.name
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, str(HERE / "se_exec.py")], input=json.dumps({"tool": tool, "args": args, "allow_write": bool(allow_write), "author": author}),
                           capture_output=True, text=True, timeout=timeout, env=nollm_env(log), cwd=str(HERE.parent))
        lines = [l for l in p.stdout.splitlines() if l.startswith("{")]
        out = json.loads(lines[-1]) if lines else {"ok": False, "error": "no_output: " + p.stderr[-300:]}
    except subprocess.TimeoutExpired:
        out = {"ok": False, "error": f"timeout {timeout}s"}
    try:
        attempts = [l.split("\t")[1:3] for l in Path(log).read_text().splitlines() if l.strip()]
        os.unlink(log)
    except OSError:
        attempts = []
    out["llm_attempts"] = len(attempts)
    out["llm_blocked"] = attempts[:5]
    out["wall_s"] = round(time.time() - t0, 2)
    return out


if __name__ == "__main__":
    msg = " ".join(sys.argv[1:])
    r = route(msg)
    print(json.dumps(r, ensure_ascii=False))
    if r["status"] == "TOOL" and "--run" in os.environ.get("WALP_ROUTER_FLAGS", ""):
        print(json.dumps(execute(r["tool"], r["args"]), ensure_ascii=False)[:2000])
