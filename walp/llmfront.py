"""walp-front — WALP 를 LLM CLI(Claude · Gemini) 앞에 얇게 세운다. 잡담은 WALP 가 끝내고, 나머지만 LLM 으로 넘긴다.

    Control      WALP 행동 버스(L0 반응 · L1 되묻기, 학습된 체계 data/front_model.json). 인사 · 감사 · 작별 · 자기소개 ·
                 할 수 있는 것 · 쓰는 법이면 여기서 답한다 -- LLM 없음, 10 ms 안팎, 토큰 0
    Sequencing   그 밖의 말, 또는 WALP 가 "모른다" 고 한 말은 위로 보낸다
    Deliberative `claude -p` 또는 `gemini -p` (사용자의 로그인 · MCP 설정을 그대로 쓴다)

숙고층(L3, STRIPS)과 기억층은 켜지 않는다 -- C++ 코어 빌드가 필요 없고(맥에서 Xcode 없이 돈다), 잡담 판정은 같다
(worldplan 봉인 모음 v1 · v2 의 120 문장에서 켠 것과 끈 것의 잡담 판정이 0 개 달랐다).

**알고 쓸 것 -- 잰 것(worldplan 봉인 모음, 사전등록):** 이 층은 토큰을 38~44% 줄였지만, 일정 요청이 섞인 말
("고마워~ 근데 latam 을 화요일로 옮겨줘")을 잡담으로 읽어 **60 문장 중 9~10 개를 LLM 에 안 보내고 잡담으로 답했다.**
WALP 는 한 말에 행위 하나만 고른다. 그 대가를 받아들이는 곳에서만 켜라. 숫자: worldplan `eval/PREREG_앞단비교*.md`.

    walp-front route "안녕"                       WALP 의 판정만(JSON) -- LLM 을 부르지 않는다
    walp-front install-hook [--settings 경로]     Claude Code 설정(기본 ~/.claude/settings.json)에 그 훅을 건다(다른 훅은 그대로)
    walp-front uninstall-hook [--settings 경로]   뗀다
    walp-front hook                               Claude Code 의 UserPromptSubmit 훅(표준입력 JSON) -- 잡담이면 모형에
                                                  보내지 않고 WALP 가 답한다. '//' 로 시작하는 말 · 슬래시 명령은 그대로 보낸다
    walp-front ask "안녕" [--llm auto|claude|gemini|off] [--replies 답.json] [--json]
    python3 -m walp.llmfront ...                  같은 것

라이브러리:

    from walp.llmfront import SmallTalk
    st = SmallTalk()                 # 묶여 온 체계를 읽는다(0.1 초 안팎)
    st.act("고마워")                 # -> "thanks"  (잡담이 아니거나 모르면 None)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODEL = HERE / "data" / "front_model.json"
SMALL = ("greet", "thanks", "bye", "about_self", "capability", "help")
REPLY = {
    "greet": "안녕하세요. 무엇을 도와드릴까요?",
    "thanks": "천만에요.",
    "bye": "안녕히 가세요.",
    "about_self": "저는 WALP 앞단입니다. 짧은 인사는 제가 바로 답하고, 나머지는 뒤의 LLM 이 답합니다.",
    "capability": "짧은 대화는 제가 바로 답하고, 그 밖의 질문은 뒤의 LLM(Claude 또는 Gemini)에게 넘깁니다.",
    "help": "하고 싶은 것을 그냥 말로 적으세요. 인사가 아니면 뒤의 LLM 이 답합니다.",
}


class SmallTalk:
    """학습된 WALP 체계로 잡담만 고른다. 숙고 · 기억 층은 끈다(C++ 코어 없음)."""

    def __init__(self, model: "str | Path | None" = None):
        os.environ.setdefault("WALP_LLM", "0")           # WALP 자기 숙고층(Claude API)은 끈다 -- LLM 은 이 도구가 고른다
        from walp import behavior as B                     # noqa: PLC0415
        self.B = B
        p = Path(model or os.environ.get("WALP_FRONT_MODEL") or MODEL)
        self.model = p
        self.bus = B.버스짓기(B.from_json(json.loads(p.read_text(encoding="utf-8"))), 숙고켜기=False, 기억켜기=False)

    def judge(self, text: str) -> dict:
        r = self.bus.돌기(text)
        act = (r.get("행한것") or {}).get("행위")
        unknown = bool(self.B.모름(r))
        return {"act": act, "unknown": unknown, "winner": r.get("승자"), "small": act in SMALL and not unknown}

    def act(self, text: str) -> "str | None":
        j = self.judge(text)
        return j["act"] if j["small"] else None


def _zero() -> dict:
    return {"input": 0, "cache_creation": 0, "cache_read": 0, "output": 0, "total": 0}


def _json_from(p: subprocess.CompletedProcess) -> "dict | None":
    for out in (p.stdout, p.stderr):                       # gemini 는 오류 JSON 을 stderr 에 쓴다(0.62 실측)
        i = (out or "").find("{")
        if i >= 0:
            try:
                return json.loads(out[i:])
            except json.JSONDecodeError:
                continue
    return None


def call_llm(name: str, text: str, timeout: int = 300) -> dict:
    """사용자의 CLI 를 그대로 부른다(로그인 · MCP · 설정은 사용자의 것). 답과 토큰을 돌려준다."""
    if name == "claude":
        cmd = ["claude", "-p", text, "--output-format", "json"]
    elif name == "gemini":
        cmd = ["gemini", "-p", text, "--output-format", "json"]
    else:
        raise ValueError(name)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return {"error": "not_found", "answer": f"{cmd[0]} 가 없다", "tokens": _zero()}
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "answer": f"{cmd[0]} 가 제때 답하지 못했다", "tokens": _zero()}
    d = _json_from(p)
    if d is None:
        return {"error": "bad_output", "answer": (p.stderr or p.stdout).strip()[-300:], "tokens": _zero()}
    if name == "claude":
        u = d.get("usage") or {}
        tok = {"input": u.get("input_tokens") or 0, "cache_creation": u.get("cache_creation_input_tokens") or 0,
               "cache_read": u.get("cache_read_input_tokens") or 0, "output": u.get("output_tokens") or 0}
        tok["total"] = sum(tok.values())
        err = "auth" if d.get("is_error") and "log" in str(d.get("result", "")).lower() else ("is_error" if d.get("is_error") else None)
        return {"answer": d.get("result") or "", "tokens": tok, "cost_usd": d.get("total_cost_usd"), **({"error": err} if err else {})}
    if d.get("error"):
        e = d["error"]
        auth = e.get("code") == 41 or "auth" in str(e.get("message", "")).lower()
        return {"error": "auth" if auth else "is_error", "tokens": _zero(),
                "answer": "Gemini 로그인이 필요하다 -- `gemini` 를 한 번 띄워 로그인하라" if auth else str(e.get("message"))[:300]}
    t = {"prompt": 0, "cached": 0, "candidates": 0, "thoughts": 0, "total": 0}
    for m in ((d.get("stats") or {}).get("models") or {}).values():
        for k in t:
            t[k] += (m.get("tokens") or {}).get(k) or 0
    tok = {"input": max(0, t["prompt"] - t["cached"]), "cache_creation": 0, "cache_read": t["cached"],
           "output": t["candidates"] + t["thoughts"]}
    tok["total"] = t["total"] or sum(tok.values())
    return {"answer": d.get("response") or "", "tokens": tok, "cost_usd": None}


def pick(choice: str) -> list:
    if choice == "off":
        return []
    names = ["gemini", "claude"] if choice == "auto" else [x.strip() for x in choice.split(",") if x.strip()]
    bad = [n for n in names if n not in ("claude", "gemini")]
    if bad:
        raise ValueError(f"--llm: auto | claude | gemini | off, got {bad}")
    return [n for n in names if shutil.which(n)] if choice == "auto" else names


def ask(text: str, llm: str = "auto", replies: "dict | None" = None, st: "SmallTalk | None" = None) -> dict:
    t0 = time.perf_counter()
    st = st or SmallTalk()
    act = st.act(text)
    if act:
        return {"answer": (replies or REPLY).get(act, REPLY[act]), "by": "walp", "act": act, "tokens": _zero(),
                "ms": round((time.perf_counter() - t0) * 1000, 1)}
    tried, r = [], {"answer": "넘길 LLM 이 없다(--llm off 이거나 claude · gemini 가 안 깔렸다)", "tokens": _zero(), "error": "no_llm"}
    for name in pick(llm):
        r = call_llm(name, text)
        tried.append(name + (f" {r['error']}" if r.get("error") in ("auth", "not_found") else ""))
        if r.get("error") not in ("auth", "not_found"):
            break
    return {**r, "by": tried[-1].split()[0] if tried else "none", "route": " -> ".join(["walp 모름"] + tried),
            "ms": round((time.perf_counter() - t0) * 1000, 1)}


BYPASS = "//"


def hook(stdin: str, st: "SmallTalk | None" = None, replies: "dict | None" = None) -> "dict | None":
    """Claude Code UserPromptSubmit 훅. 잡담이면 {"decision": "block", "reason": 답} -- 모형을 안 부르고 그 답을 사람에게 보인다.
    아니면 None(아무것도 안 찍는다 -> 말이 그대로 모형에 간다). 못 읽는 입력도 None -- 훅이 사람의 말을 막는 쪽으로 틀리지 않게."""
    try:
        prompt = str(json.loads(stdin or "{}").get("prompt") or "")
    except (json.JSONDecodeError, AttributeError):
        return None
    p = prompt.strip()
    if not p or p.startswith(BYPASS) or p.startswith("/") or os.environ.get("WALP_FRONT_HOOK") == "0":
        return None
    act = (st or SmallTalk()).act(p)
    if not act:
        return None
    reply = (replies or REPLY).get(act, REPLY[act])
    return {"decision": "block",
            "reason": f"{reply}\n(WALP 잡담층이 답했다 -- 모형에 보내지 않음, 토큰 0. 일이 담긴 말이었다면 앞에 // 를 붙여 다시 보내라)"}


HOOK_TAG = "walp-front hook"


def _hook_command() -> str:
    exe = shutil.which("walp-front")
    if exe:
        return f'"{exe}" hook'
    return f'"{sys.executable}" -m walp.llmfront hook'


def install_hook(settings: "str | Path | None" = None, remove: bool = False) -> dict:
    """설정 파일의 hooks.UserPromptSubmit 에 이 훅을 하나만 둔다(이미 있으면 바꾸지 않는다). 바꾸기 전 것은 .bak-walp 로."""
    p = Path(settings or Path.home() / ".claude" / "settings.json").expanduser()
    d = json.loads(p.read_text(encoding="utf-8")) if p.is_file() and p.read_text(encoding="utf-8").strip() else {}
    groups = d.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])
    ours = lambda h: HOOK_TAG in h.get("command", "") or "walp.llmfront hook" in h.get("command", "")
    before = json.dumps(d, sort_keys=True)
    for g in groups:
        g["hooks"] = [h for h in g.get("hooks", []) if not ours(h)]
    groups[:] = [g for g in groups if g.get("hooks")]
    if not remove:
        groups.append({"hooks": [{"type": "command", "command": _hook_command(), "timeout": 30}]})
    if not groups:
        del d["hooks"]["UserPromptSubmit"]
        if not d["hooks"]:
            del d["hooks"]
    changed = json.dumps(d, sort_keys=True) != before
    if changed:
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.is_file():
            p.with_name(p.name + ".bak-walp").write_bytes(p.read_bytes())
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"settings": str(p), "changed": changed, "installed": not remove}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="walp-front", description="WALP 를 LLM CLI 앞에 세운다 -- 잡담은 WALP, 나머지는 LLM")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("route", help="WALP 판정만(LLM 을 부르지 않는다)"); a.add_argument("text")
    a = sub.add_parser("ask", help="잡담이면 WALP 가, 아니면 LLM 이 답한다"); a.add_argument("text")
    a.add_argument("--llm", default=os.environ.get("WALP_FRONT_LLM", "auto"), help="auto | claude | gemini | claude,gemini | off")
    a.add_argument("--replies", help="부류별 답 JSON {greet: ..., thanks: ...}")
    a.add_argument("--json", action="store_true")
    for name in ("install-hook", "uninstall-hook"):
        sub.add_parser(name, help="Claude Code 설정에 훅을 건다/뗀다").add_argument("--settings")
    h = sub.add_parser("hook", help="Claude Code UserPromptSubmit 훅(표준입력 JSON)")
    h.add_argument("--replies")
    for p in (sub.choices["route"], a, h):
        p.add_argument("--model", help="학습된 체계 JSON (기본: 묶여 온 data/front_model.json)")
    args = ap.parse_args(argv)
    if args.cmd in ("install-hook", "uninstall-hook"):
        print(json.dumps(install_hook(args.settings, remove=args.cmd == "uninstall-hook"), ensure_ascii=False))
        return 0
    if args.cmd == "hook":
        try:
            raw = sys.stdin.read()
            replies = json.loads(Path(args.replies).read_text(encoding="utf-8")) if args.replies else None
            out = hook(raw, SmallTalk(args.model), replies)
        except Exception as e:  # noqa: BLE001 -- 훅이 터져도 사람의 말은 막지 않는다(그대로 모형에 간다)
            print(f"walp-front hook: {type(e).__name__}: {e}", file=sys.stderr)
            return 0
        if out:
            print(json.dumps(out, ensure_ascii=False))
        return 0
    st = SmallTalk(args.model)
    if args.cmd == "route":
        print(json.dumps(st.judge(args.text), ensure_ascii=False))
        return 0
    replies = json.loads(Path(args.replies).read_text(encoding="utf-8")) if args.replies else None
    r = ask(args.text, args.llm, replies, st)
    if args.json:
        print(json.dumps(r, ensure_ascii=False))
    else:
        print(r.get("answer", ""))
        print(f"-- {r.get('route') or 'walp ' + str(r.get('act'))} · 토큰 {r['tokens']['total']:,} · {r['ms']:.0f} ms", file=sys.stderr)
    return 1 if r.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
