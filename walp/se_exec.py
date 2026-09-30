"""SE 도구 하나를 **LLM 이 막힌 프로세스**에서 부른다. 표준입력 JSON {tool, args} → 표준출력 JSON.

부모(walp.se_router)가 이 파일을 자식 프로세스로 띄운다: PYTHONPATH 맨 앞에 nollm/(sitecustomize 로 LLM 호스트
차단), PATH 맨 앞에 nollm/(claude 대역), LLM 키 환경변수 제거. bot_tools 는 LangChain·Gemini 를 모듈 맨 위에서
임포트하므로 그 셋만 빈 대역으로 채운다 — 도구 몸통은 그대로 돈다.
"""
from __future__ import annotations

import json
import sys
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _stub(name, **attrs):
    m = types.ModuleType(name)
    m.__path__ = []
    m.__dict__.update(attrs)
    sys.modules[name] = m


def _tool(f=None, **kw):
    if f is None or isinstance(f, str):
        return lambda g: _tool(g)
    f.func = f
    f.is_se_tool = True
    return f


class _NoLLM:
    def __init__(self, *a, **k):
        import os
        log = os.environ.get("WALP_NOLLM_LOG")
        if log:   # 네트워크 전에 막힌 시도도 센다(첫 판은 이 자리를 안 세서 research 의 시도가 0 으로 찍혔다)
            try:
                with open(log, "a") as f:
                    f.write(f"{time.time():.3f}\tstub\tChatGoogleGenerativeAI\t{os.getpid()}\n")
            except OSError:
                pass
        raise RuntimeError("WALP: LLM(ChatGoogleGenerativeAI) 생성 차단")


def load_bot_tools():
    for real in ("langchain_core", "langchain_google_genai", "langgraph"):
        try:
            __import__(real)
            break   # 진짜가 있으면(VM) 대역을 안 쓴다 — 그래도 LLM 은 네트워크에서 막힌다
        except ImportError:
            pass
    else:
        _stub("langchain_core")
        _stub("langchain_core.tools", tool=_tool)
        _stub("langchain_core.callbacks", BaseCallbackHandler=object)
        _stub("langchain_core.callbacks.base", BaseCallbackHandler=object)
        _stub("langchain_google_genai", ChatGoogleGenerativeAI=_NoLLM)
        _stub("langgraph")
        _stub("langgraph.prebuilt", create_react_agent=lambda *a, **k: None)
    import bot_tools
    return bot_tools


def _log_attempt(kind: str, what: str) -> None:
    import os
    log = os.environ.get("WALP_NOLLM_LOG")
    if log:
        try:
            with open(log, "a") as f:
                f.write(f"{time.time():.3f}\t{kind}\t{what}\t{os.getpid()}\n")
        except OSError:
            pass


def block_gateway() -> None:
    """SE 의 LLM 관문(router/call.py 부르기)을 '적고 거절' 로 바꾼다 — 네트워크에 닿기 전에 막히는 시도도 센다."""
    try:
        from router import call as R
    except Exception:  # noqa: BLE001 — 관문이 없으면 막을 것도 없다(네트워크 차단은 그대로 선다)
        return
    def _refuse(역할, prompt, repo=None):
        _log_attempt("gateway", str(역할))
        raise RuntimeError("WALP: LLM 관문(router.call.부르기) 차단")
    R.부르기 = _refuse


def main():
    block_gateway()
    req = json.loads(sys.stdin.read() or "{}")
    # 부른 사람을 자식에게도 세운다 — 도구마다 가진 게스트 차단(agent_context.is_blocked)이 여기서도 걸리게.
    # 첫 판은 이것이 없어 막힌 게스트가 `!walp 도구` 로 read_file 을 썼다(실측 2026-09-29)
    if req.get("author"):
        try:
            import agent_context
            agent_context.current_author.set(str(req["author"]))
        except Exception:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": "호출자를 세우지 못했다 — 실행하지 않는다"}))
            return 1
    t0 = time.time()
    out = {"tool": req.get("tool")}
    try:
        name = req["tool"]
        if name.startswith("cmd:"):
            import dispatch
            r = dispatch.run(req["args"].get("text", ""), None, bool(req.get("allow_write", False)))
            out.update(ok=r is not None, result=(r or "")[:6000])
        else:
            bt = load_bot_tools()
            fn = getattr(bt, name)
            f = getattr(fn, "func", fn)
            r = f(**req.get("args", {}))
            out.update(ok=True, result=str(r)[:6000])
    except Exception as e:  # noqa: BLE001 — 도구 오류는 결과다
        out.update(ok=False, error=f"{type(e).__name__}: {e}"[:600])
    out["seconds"] = round(time.time() - t0, 2)
    sys.stdout.write("\n" + json.dumps(out, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
