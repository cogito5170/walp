"""도구 라우터 채점 — 봉인 문장(eval/tool_corpus.tsv)으로.

라벨: `TOOL <이름> [인자=값]` · `ASK` · `REJECT`. 판정:
  TOOL 라벨   맞게 실행(도구 같음) / 되물음(후보에 정답이 있으면 '안전한 되묻기') / 거부 / **다른 도구 실행**
  ASK 라벨    되물음 = 맞음 / 거부 = 안전 / **실행 = 잘못 실행**
  REJECT 라벨 거부 = 맞음 / 되물음 = 안전 / **실행 = 잘못 실행**
인자: 맞게 고른 줄에서 라벨이 적은 인자만 본다 — 수는 같은 값, `<code>` 는 코드가 들어왔는지, 글은 한쪽이
다른 쪽을 품는지(띄어쓰기·대소문자 무시).

기준선: (a) 도구 이름이 글에 그대로 있을 때만(`serdes_link`·`serdes link`) (b) 우리 라우터에서 '1·2등이
가까우면 되묻기' 를 끈 것(늘 1등을 실행).
"""
from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from walp import se_router  # noqa: E402


def parse_label(lab: str):
    lab = lab.strip()
    if lab in ("ASK", "REJECT"):
        return lab, None, {}
    parts = shlex.split(lab, posix=True) if lab.count('"') % 2 == 0 else lab.split()
    tool = parts[1] if len(parts) > 1 else ""
    args = {}
    for p in parts[2:]:
        if "=" in p:
            k, v = p.split("=", 1)
            args[k] = v
    return "TOOL", tool, args


def _n(s):
    return re.sub(r"\s+", "", str(s).lower())


def arg_ok(k, want, got) -> bool:
    if got is None:
        return False
    if want == "<code>":
        return bool(str(got).strip())
    try:
        return abs(float(want) - float(got)) < 1e-6
    except (TypeError, ValueError):
        a, b = _n(want), _n(got)
        return bool(a) and (a in b or b in a)


def name_only(text: str, tools: list) -> dict:
    t = text.lower()
    hit = [x for x in tools if x in t or x.replace("_", " ") in t]
    return {"status": "TOOL", "tool": hit[0], "args": {}} if len(hit) == 1 else {"status": "REJECT"}


def score(rows, router):
    c = dict(n=0, tool_n=0, tool_ok=0, tool_asked=0, tool_refused=0, tool_wrong=0, ask_n=0, ask_ok=0, ask_refused=0,
             ask_exec=0, rej_n=0, rej_ok=0, rej_asked=0, rej_exec=0, arg_n=0, arg_ok=0)
    fails = []
    for text, lab in rows:
        kind, tool, args = parse_label(lab)
        r = router(text)
        st = r.get("status")
        executed = st in ("TOOL", "DENY")   # DENY 는 실행 안 하지만 '그 도구를 골랐다' 로 센다
        c["n"] += 1
        if kind == "TOOL":
            c["tool_n"] += 1
            if executed and r.get("tool") == tool:
                c["tool_ok"] += 1
                for k, v in args.items():
                    c["arg_n"] += 1
                    ok = arg_ok(k, v, r.get("args", {}).get(k))
                    c["arg_ok"] += ok
                    if not ok:
                        fails.append(f"인자 {k}: 기대 {v} · 실제 {r.get('args', {}).get(k)} | {text[:80]}")
            elif executed:
                c["tool_wrong"] += 1
                fails.append(f"다른 도구 실행: 기대 {tool} · 실제 {r.get('tool')} | {text[:90]}")
            elif st == "ASK":
                c["tool_asked"] += 1
                fails.append(f"되물음({r.get('why')} {r.get('candidates') or r.get('missing')}): 기대 {tool} | {text[:80]}")
            else:
                c["tool_refused"] += 1
                fails.append(f"거부: 기대 {tool} | {text[:90]}")
        elif kind == "ASK":
            c["ask_n"] += 1
            if st == "ASK":
                c["ask_ok"] += 1
            elif executed:
                c["ask_exec"] += 1
                fails.append(f"ASK 인데 실행 {r.get('tool')} | {text[:90]}")
            else:
                c["ask_refused"] += 1
        else:
            c["rej_n"] += 1
            if st == "REJECT":
                c["rej_ok"] += 1
            elif executed:
                c["rej_exec"] += 1
                fails.append(f"REJECT 인데 실행 {r.get('tool')} | {text[:90]}")
            else:
                c["rej_asked"] += 1
    c["wrong_execution"] = c["tool_wrong"] + c["ask_exec"] + c["rej_exec"]
    c["tool_accuracy"] = round(c["tool_ok"] / max(1, c["tool_n"]), 4)
    c["arg_accuracy"] = round(c["arg_ok"] / max(1, c["arg_n"]), 4)
    return c, fails


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "eval" / "tool_corpus.tsv"
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if "\t" in line:
            t, lab = line.split("\t", 1)
            rows.append((t, lab))
    dic = se_router.load_dict()
    tools = sorted(dic)
    out = {}
    out["walp_router"], fails = score(rows, lambda t: se_router.route(t, dic))
    out["no_ask_on_tie"], _ = score(rows, lambda t: se_router.route(t, dic, margin=0.0))
    out["name_only"], _ = score(rows, lambda t: name_only(t, tools))
    out["failures"] = fails
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
