"""SE 도구 배선 — `bot_tools.py` 의 도구 전부와 `dispatch.py` 의 고정 명령 전부를 WALP 의 도구 목록으로.

사용자(2026-09-29): "SE agent에 있는 많은 tool을 최대한 배선해라."

1. **목록은 손으로 안 적는다.** `bot_tools.py` 를 AST 로 읽어 `@tool` 함수의 서명·설명을 뽑는다
   (도구가 늘면 목록이 저절로 는다 — `precheck` 가 검사 목록을 재서 고르는 것과 같은 원리).
2. **부작용을 가른다**(읽기 · 계산 · 쓰기 · 셸 · 네트워크). 쓰기·셸·네트워크는 안전층이 기본으로 막는다.
3. **LLM 을 쓰는지 가른다** — 도구 몸통이 부르는 저장소 모듈을 따라가며(임포트 폐포 + 문자열로 적힌 `.py`
   경로) LLM 표지(Gemini·Claude 호출)를 찾는다. 이것은 **추정**이다. 그래서 실행은 따로 **막고 센다**
   (`nollm/` — LLM 호스트 차단 · 키 제거 · `claude` 심). 추정이 틀리면 실행 기록이 그것을 드러낸다.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# LLM 을 부른다는 표지. 모듈 원문에 이것이 있으면 그 모듈은 LLM 을 쓴다고 본다.
LLM_MARKERS = (
    "generativelanguage.googleapis", "ChatGoogleGenerativeAI", "google.generativeai", "google.genai",
    "gemini_http", "llm_pool", "api.anthropic.com", "import anthropic", '"claude"', "'claude'",
    "claude -p", "call_gemini", "gemini_call", "create_react_agent",
)
# 부작용 표지(몸통·그 모듈 원문)
WRITE_MARKERS = ("edit_file(", "save_memory(", "write_output(", ".write_text(", "open(", "git commit", "git push",
                 "set_key(", "create_pr(")
SHELL_MARKERS = ("subprocess.", "Popen(", "os.system(")   # 자식 프로세스를 띄운다(배경 작업 포함)
NET_MARKERS = ("smtplib", "send_email", "requests.post", "requests.get", "urllib.request")


@dataclass
class Param:
    name: str
    type: str
    default: "str | None"    # None = 필수


@dataclass
class ToolInfo:
    name: str
    source: str               # "bot_tools" | "dispatch"
    params: list = field(default_factory=list)
    doc: str = ""
    kind: str = "compute"     # read | compute | write | shell | network
    llm: bool = False
    llm_via: list = field(default_factory=list)   # 표지를 찾은 모듈(근거)
    prefix: str = ""          # 고정 명령이면 `!…`


# 이름으로 분명한 것 — 근거를 같이 적는다(추정보다 앞선다)
KIND_BY_NAME = {
    "run_shell": "shell", "run_probes": "shell", "run_experiment": "shell", "repair": "shell",
    "edit_file": "write", "set_key": "write", "save_memory": "write", "write_public_answer": "write",
    "create_pr": "write", "dispatch_command": "shell",
    "send_email": "network",
    "read_file": "read", "search_memory": "read", "read_image": "read", "read_pdf": "read",
    "orchestrator_status": "read", "spice_example": "read",
}


def _repo_module_file(mod: str) -> "Path | None":
    p = REPO / (mod.replace(".", "/") + ".py")
    if p.is_file():
        return p
    p = REPO / mod.replace(".", "/") / "__init__.py"
    return p if p.is_file() else None


def _imports_of(path: Path) -> set:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return set()
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            out.add(n.module)
            out |= {f"{n.module}.{a.name}" for a in n.names}
    return out


# 에이전트(LLM) 본체 — 도구가 이것을 임포트해도 '도구가 LLM 을 부른다' 는 뜻이 아니다(배선 파일일 뿐)
AGENT_FILES = {"bot_tools.py", "discord_bot_server.py", "main_public.py", "dispatch.py", "agent_context.py"}


def _py_paths_in(src: str) -> set:
    """코드 속 문자열 상수에 적힌 저장소 .py 경로 — 주석·독스트링은 뺀다(첫 판은 주석까지 따라가
    모든 도구를 LLM 으로 추정했다: 69/69)."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    docs = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)) and n.body:
            b0 = n.body[0]
            if isinstance(b0, ast.Expr) and isinstance(getattr(b0, "value", None), ast.Constant):
                docs.add(id(b0.value))
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
            out |= {m for m in re.findall(r"([\w/]+\.py)", n.value) if (REPO / m).is_file()}
    return {m for m in out if m not in AGENT_FILES}


_llm_cache: dict = {}


def _llm_closure(start_mods: set, start_files: set, depth: int = 1) -> list:
    """저장소 모듈을 depth 단계까지 따라가며 LLM 표지가 든 파일을 돌려준다."""
    seen: set = set()
    frontier = {f for f in (_repo_module_file(m) for m in start_mods) if f} | {REPO / f for f in start_files}
    hits = []
    for _ in range(depth + 1):
        nxt = set()
        for f in frontier:
            if f in seen:
                continue
            seen.add(f)
            if f not in _llm_cache:
                try:
                    txt = f.read_text(encoding="utf-8")
                    code = ast.unparse(ast.parse(txt))    # 주석이 빠진다(독스트링은 남지만 표지는 호출꼴이다)
                except (OSError, SyntaxError):
                    txt = code = ""
                _llm_cache[f] = (any(m in code for m in LLM_MARKERS), _imports_of(f), _py_paths_in(txt))
            is_llm, imps, pys = _llm_cache[f]
            if is_llm:
                hits.append(str(f.relative_to(REPO)))
            nxt |= {g for g in (_repo_module_file(m) for m in imps) if g and g.name not in AGENT_FILES}
            nxt |= {REPO / p for p in pys}
        frontier = nxt - seen
    return sorted(set(hits))


def _strip_doc(fn_src: str) -> str:
    try:
        f = ast.parse(fn_src).body[0]
        if f.body and isinstance(f.body[0], ast.Expr) and isinstance(getattr(f.body[0], "value", None), ast.Constant):
            f.body = f.body[1:] or [ast.Pass()]
        return ast.unparse(f)
    except (SyntaxError, IndexError):
        return fn_src


def bot_tools_catalog() -> list:
    src = (REPO / "bot_tools.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    top_imports = {}   # 이름 → 모듈
    for n in tree.body:
        if isinstance(n, ast.Import):
            for a in n.names:
                top_imports[a.asname or a.name] = a.name
        elif isinstance(n, ast.ImportFrom) and n.module:
            for a in n.names:
                top_imports[a.asname or a.name] = n.module
    out = []
    for n in tree.body:
        if not isinstance(n, ast.FunctionDef):
            continue
        if not any((isinstance(d, ast.Name) and d.id == "tool") or
                   (isinstance(d, ast.Call) and getattr(d.func, "id", "") == "tool") for d in n.decorator_list):
            continue
        a = n.args
        defs = [None] * (len(a.args) - len(a.defaults)) + list(a.defaults)
        params = [Param(x.arg, ast.unparse(x.annotation) if x.annotation else "str",
                        None if d is None else ast.unparse(d)) for x, d in zip(a.args, defs)]
        body = ast.get_source_segment(src, n) or ""
        doc = (ast.get_docstring(n) or "").strip()
        # 몸통이 부르는 저장소 모듈: 최상단 임포트 이름 + 몸통 안 임포트 + 문자열 속 .py
        mods = {top_imports[nm.id] for nm in ast.walk(n) if isinstance(nm, ast.Name) and nm.id in top_imports}
        for sub in ast.walk(n):
            if isinstance(sub, ast.ImportFrom) and sub.module:
                mods.add(sub.module)
                mods |= {f"{sub.module}.{x.name}" for x in sub.names}
            elif isinstance(sub, ast.Import):
                mods |= {x.name for x in sub.names}
        mods = {m for m in mods if _repo_module_file(m) and _repo_module_file(m).name not in AGENT_FILES}
        via = _llm_closure(mods, _py_paths_in(body))
        if any(m in _strip_doc(body) for m in LLM_MARKERS):
            via = ["bot_tools.py(몸통)"] + via
        kind = KIND_BY_NAME.get(n.name)
        if not kind:
            # 이름표에 없으면 몸통(설명 뺀 코드)의 표지로 가른다. 센 쪽이 먼저 — 표지가 없을 때만 compute.
            code = _strip_doc(body)
            kind = ("shell" if any(m in code for m in SHELL_MARKERS) else
                    "write" if any(m in code for m in WRITE_MARKERS) else
                    "network" if any(m in code for m in NET_MARKERS) else "compute")
        out.append(ToolInfo(n.name, "bot_tools", params, doc.split("\n\n")[0][:400], kind, bool(via), via))
    return out


def dispatch_catalog() -> list:
    """dispatch.py 의 명령들 — 모듈마다 PREFIX 와 설명. 인자는 명령 뒤 글 한 줄."""
    src = (REPO / "dispatch.py").read_text(encoding="utf-8")
    m = re.search(r"^명령들 = \(([^)]*)\)", src, re.M)
    names = [x.strip() for x in m.group(1).split(",")] if m else []
    rev = {v: k for k, v in re.findall(r"^from (\w[\w.]*) import discord_cmd as (\w+)", src, re.M)}
    rev.update({v: f"{a}.{b}" for a, b, v in re.findall(r"^from (\w[\w.]*) import (\w+) as (\w+)", src, re.M)})
    rev.update({v: k for k, v in re.findall(r"^import (\w+) as (\w+)", src, re.M)})
    out = []
    for nm in names:
        mod = rev.get(nm)
        if not mod:
            continue
        f = _repo_module_file(mod) or _repo_module_file(mod + ".discord_cmd")
        if not f:
            continue
        txt = f.read_text(encoding="utf-8")
        pm = re.search(r'^PREFIX\s*=\s*["\'](![^"\']+)["\']', txt, re.M)
        if not pm:
            continue
        doc = (ast.get_docstring(ast.parse(txt)) or "").strip().split("\n\n")[0][:300]
        via = _llm_closure({mod}, set())
        out.append(ToolInfo("cmd:" + pm.group(1), "dispatch", [Param("text", "str", '""')], doc, "shell", bool(via),
                            via, pm.group(1)))
    return out


def catalog() -> list:
    return bot_tools_catalog() + dispatch_catalog()


def catalog_hash(cat: list) -> str:
    blob = json.dumps([(t.name, [(p.name, p.type, p.default) for p in t.params], t.kind, t.llm) for t in cat],
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


if __name__ == "__main__":
    import sys
    cat = catalog()
    if "--json" in sys.argv:
        print(json.dumps([asdict(t) for t in cat], ensure_ascii=False, indent=1))
    else:
        for t in cat:
            print(f"{t.name:24s} {t.kind:8s} {'LLM' if t.llm else '-  '} {','.join(t.llm_via)[:90]}")
        print(f"\n도구 {len(cat)}개 · LLM 추정 {sum(t.llm for t in cat)} · 해시 {catalog_hash(cat)}")
