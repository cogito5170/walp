"""Task B(토큰 비용 분석) 보조: trace_stats 가 안 재는 반복 패턴(훅 · 같은 경고 · 같은 호출 · 같은 파일 재읽기 · edit/read 연쇄).
집계와 해시만 낸다 -- 글 · 경로 · 명령은 내보내지 않는다.  python3 -m walp.trace_patterns <세션.jsonl> [--sidechain]"""
import collections, hashlib, json, re, shlex, sys
from walp.trace_stats import _tok, kind

H = lambda s: hashlib.sha256(s.encode()).hexdigest()[:10]
READ_CMD = re.compile(r"^\s*(cat|sed -n \S+|head(?: -n? ?\d+)?|tail(?: -n? ?\d+)?|wc(?: -l)?)\s+(.+)$")


def read_targets(name, inp):
    if name == "Read":
        return [inp.get("file_path", "")]
    if name == "Bash":
        out = []
        for seg in re.split(r"&&|;|\|\|", str(inp.get("command", ""))):
            m = READ_CMD.match(seg.split("|")[0].strip())
            if m:
                try:
                    out += [a for a in shlex.split(m.group(2)) if not a.startswith("-")]
                except ValueError:
                    pass
        return out
    return []


def edit_targets(name, inp):
    return [inp.get("file_path", "")] if name in ("Edit", "Write", "MultiEdit", "NotebookEdit") else []


def main(path, side=False):
    turns, order, hooks, reminders = {}, [], collections.Counter(), collections.Counter()
    for line in open(path, encoding="utf-8"):
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        m = d.get("message")
        if not isinstance(m, dict) or (d.get("isSidechain") and not side):
            continue
        if d.get("type") == "assistant" and m.get("usage"):
            tl = [(c.get("name", ""), c.get("input") or {}) for c in m.get("content") or []
                  if isinstance(c, dict) and c.get("type") == "tool_use"]
            if m.get("id") not in turns:
                order.append(m.get("id"))
                turns[m.get("id")] = {"u": m["usage"], "tools": []}
            turns[m.get("id")]["tools"] += tl
        elif d.get("type") == "user":
            c = m.get("content")
            parts = [c] if isinstance(c, str) else []
            if isinstance(c, list):
                for x in c:
                    if isinstance(x, dict) and x.get("type") == "text":
                        parts.append(x.get("text", ""))
                    if isinstance(x, dict) and x.get("type") == "tool_result":   # 도구 결과에 붙는 리마인더
                        cc = x.get("content")
                        parts += [cc] if isinstance(cc, str) else [y.get("text", "") for y in cc or [] if isinstance(y, dict)]
            for p in parts:
                if "hook" in p.lower() and ("feedback" in p.lower() or "blocking" in p.lower()):
                    hooks[H(re.sub(r"\s+", " ", p.strip()))] += 1
                for r in re.findall(r"<system-reminder>(.*?)</system-reminder>", p, re.S):
                    reminders[H(re.sub(r"\s+", " ", r.strip()))] += 1
    T = [turns[i] for i in order]
    toks = [_tok(t["u"]) for t in T]
    kinds = [kind(*t["tools"][0]) if t["tools"] else "답 쓰기" for t in T]
    calls = collections.Counter(H(n + json.dumps(i, sort_keys=True, ensure_ascii=False)) for t in T for n, i in t["tools"])
    reads, edits = collections.Counter(), collections.Counter()
    for t in T:
        for n, i in t["tools"]:
            for p in read_targets(n, i):
                reads[H(p)] += 1
            for p in edit_targets(n, i):
                edits[H(p)] += 1

    def runs(lbl, minlen=3):
        out, i = [], 0
        while i < len(kinds):
            j = i
            while j + 1 < len(kinds) and kinds[j + 1] == kinds[i]:
                j += 1
            if kinds[i] == lbl and j - i + 1 >= minlen:
                out.append((j - i + 1, sum(toks[i + 1:j + 1])))
            i = j + 1
        return out
    total = sum(toks) or 1
    rep = lambda c: {"distinct": len(c), "total": sum(c.values()), "repeats": sum(v - 1 for v in c.values()),
                     "max": max(c.values(), default=0)}
    tri = sum(1 for i in range(len(kinds) - 2) if kinds[i] == kinds[i + 1] == kinds[i + 2] == "고치기")
    trr = sum(1 for i in range(len(kinds) - 2) if kinds[i] == kinds[i + 1] == kinds[i + 2] == "읽기")
    er, rr = runs("고치기"), runs("읽기")
    return {"turns": len(T), "tokens": sum(toks),
            "hook_feedback": rep(hooks), "system_reminders": rep(reminders),
            "same_call_repeats": rep(calls), "file_reads": rep(reads), "file_edits": rep(edits),
            "edit3_windows": tri, "read3_windows": trr,
            "edit_runs>=3": {"n": len(er), "extra_turns": sum(a - 1 for a, _ in er), "extra_share": sum(b for _, b in er) / total},
            "read_runs>=3": {"n": len(rr), "extra_turns": sum(a - 1 for a, _ in rr), "extra_share": sum(b for _, b in rr) / total}}


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1], "--sidechain" in sys.argv), ensure_ascii=False, indent=1))
