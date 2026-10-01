"""WALP 실행 정책층 P1~P3 -- 에이전트 루프의 턴 경계에서 LLM 턴을 없앨 수 있는 자리. 설계: docs/실행정책층/설계.md

    P1 같은 상태 재사용   같은 말 + 같은 저장소 상태면 이전 결론을 다시 낸다
    P2 발행 매크로        commit -> 깨끗한 워크트리 검사 -> 밀기 -> PR -> 머지 -> 머지 확인 을 도구 한 번으로
    P3 기다리기 이벤트    폴링 턴 대신, 끝날 때까지 막고 요약 한 줄

    walp-exec state [--repo .]                               상태 해시(P1)
    walp-exec publish -m "메시지" [--test 명령] [--push] [--merge]   P2 -- 바깥 동작은 깃발을 줄 때만
    walp-exec wait --pid N [--log 파일] [--timeout 초]       P3
    walp-exec shadow-hook                                    그림자 모드 훅(표준입력) -- 막지 않고 원장에 제안만 적는다
    walp-exec install-shadow [--settings 경로]               Claude Code 에 그림자 훅을 건다 / uninstall-shadow
    walp-exec replay <세션.jsonl> [--sidechain]              오프라인 그림자(기록된 추적에 정책을 대 본다)
    walp-exec report [--ledger 경로]                          실제 그림자 원장의 일치율 · 아낀 턴

원장(`~/.walp/shadow.jsonl`)에는 **해시 · 종류 · 수만** 적는다. 말 · 명령 · 파일 내용은 안 적는다.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LEDGER = Path(os.environ.get("WALP_SHADOW_LEDGER") or Path.home() / ".walp" / "shadow.jsonl")
P2_WINDOW = 12

# ---------------- 걸음 알아보기(레포에 매이지 않는다) ----------------

_COMMIT = re.compile(r"\bgit\b[^|;&]*\bcommit\b")
_PUSH = re.compile(r"\bgit\b[^|;&]*\bpush\b")
_GHPR = re.compile(r"\bgh pr (create|merge)\b")
_POLL = re.compile(r"\bkill -0\b|\bsleep \d|\bpgrep\b|\bwait \$|for i in \$\(seq")
_STATE = re.compile(r"\bgit\b[^|;&]*\b(commit|push|reset|merge|rebase|checkout|cherry-pick|am|pull|stash|rm|mv|add)\b"
                    r"|\bsed -i\b|\bcat >|\btee\b|<<\s*'?EOF|\brm -|\bmv\b|\bcp\b|\bpatch\b")
_CD = re.compile(r"(?:^|&&|;)\s*cd\s+([^\s;&|]+)")


def is_pr_tool(name: str) -> bool:
    return bool(re.search(r"create_pull_request|merge_pull_request", name))


def bash_kind(cmd: str) -> "set[str]":
    k = set()
    if _COMMIT.search(cmd):
        k.add("commit")
    if _PUSH.search(cmd):
        k.add("push")
    if _GHPR.search(cmd):
        k.add("pr")
    if _POLL.search(cmd):
        k.add("poll")
    if _STATE.search(cmd):
        k.add("state")
    return k


def target_repo(tool: str, inp: dict, cwd: str) -> str:
    """도구 호출이 겨누는 저장소(대리): Bash 는 첫 cd, 파일 도구는 파일 경로, 아니면 세션 cwd."""
    if tool == "Bash":
        m = _CD.search(str(inp.get("command", "")))
        if m:
            return os.path.normpath(os.path.join(cwd or "/", os.path.expanduser(m.group(1).strip("'\""))))
        return cwd
    fp = inp.get("file_path") or inp.get("notebook_path")
    return os.path.dirname(fp) if fp else cwd


def under(path: str, repo: str) -> bool:
    path, repo = os.path.normpath(path or "/"), os.path.normpath(repo or "/")
    return path == repo or path.startswith(repo + os.sep)


# ---------------- P1 상태 해시 ----------------

def _git(repo, *a, timeout=20) -> str:
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, timeout=timeout).stdout


def state_hash(repo: "str | Path" = ".") -> "str | None":
    """HEAD · 브랜치 · 원격 추적 ref · 작업 트리 변경. git 저장소가 아니면 None(모른다 -> 재사용하지 않는다)."""
    try:
        top = _git(repo, "rev-parse", "--show-toplevel").strip()
        if not top:
            return None
        parts = [_git(top, "rev-parse", "HEAD"), _git(top, "rev-parse", "--abbrev-ref", "HEAD"),
                 _git(top, "for-each-ref", "--format=%(refname) %(objectname)", "refs/remotes"),
                 _git(top, "status", "--porcelain", "-uall")]
    except (OSError, subprocess.SubprocessError):
        return None
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:16]


def norm_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", str(text or "")).strip().encode()).hexdigest()[:16]


# ---------------- P2 발행 매크로 ----------------

def detect_test(repo: Path) -> "list[str] | None":
    if (repo / "package.json").is_file():
        return ["npm", "test", "--silent"]
    if (repo / "Cargo.toml").is_file():
        return ["cargo", "test", "-q"]
    if (repo / "go.mod").is_file():
        return ["go", "test", "./..."]
    if (repo / "tests").is_dir() and any((repo / "tests").glob("test_*.py")):
        return [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."]
    return None


def publish(repo: "str | Path", message: str, test: "str | None" = None, push: bool = False, merge: bool = False,
            base: str = "main", runner=subprocess.run, files: "list | None" = None) -> dict:
    """한 번에 발행. 걸음마다 결정론적으로 확인하고, 하나라도 안 되면 **그 자리에서 멈추고** 까닭을 돌려준다(LLM 이 이어받는다).
    바깥 동작(밀기 · PR · 머지)은 깃발을 줄 때만 한다."""
    repo = Path(_git(repo, "rev-parse", "--show-toplevel").strip() or repo)
    done = []

    def run(cmd, cwd=repo, timeout=1800, env=None):
        return runner(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, env=env)

    def stop(at, r=None, why=""):
        tail = ((r.stdout or "") + (r.stderr or ""))[-800:] if r is not None else why
        return {"ok": False, "stopped_at": at, "done": done, "tail": tail}

    # 추적되는 파일의 변경만 담는다. 새 파일은 files 로 **이름을 댄 것만** -- 'git add -A' 는 로그 · 원장 같은
    # 추적 안 되는 흔적까지 쓸어 담았다(실제 그림자에서 slow2.log 가 커밋 · 밀기까지 됐다)
    run(["git", "add", "-u"])
    if files:
        r = run(["git", "add", "--", *[str(f) for f in files]])
        if r.returncode:
            return stop("commit", r)
    left = [x for x in run(["git", "ls-files", "--others", "--exclude-standard"]).stdout.splitlines() if x]
    if run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        return stop("commit", why="담을 변경이 없다" + (f" (추적 안 되는 파일 {len(left)} 개는 files 로 이름을 대야 담는다)" if left else ""))
    r = run(["git", "commit", "-q", "-m", message])
    if r.returncode:
        return stop("commit", r)
    sha = run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
    done.append(f"commit {sha}" + (f" (안 담은 추적 안 되는 파일 {len(left)}: {', '.join(left[:5])})" if left else ""))
    cmd = test.split() if isinstance(test, str) and test else detect_test(repo)
    if cmd:
        wt = Path(tempfile.mkdtemp(prefix="walp-publish-")) / "wt"
        r = run(["git", "worktree", "add", "-q", "--detach", str(wt), "HEAD"])
        if r.returncode:
            return stop("worktree", r)
        try:
            r = run(cmd, cwd=wt, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})   # 검사가 .pyc 흔적을 안 남기게
            dirty = run(["git", "status", "--porcelain", "-uno"], cwd=wt).stdout.strip()
        finally:
            run(["git", "worktree", "remove", "--force", str(wt)])
        if r.returncode:
            return stop("test", r)
        if dirty:
            return stop("test", why="검사가 추적되는 파일을 바꿨다(흔적): " + dirty[:300])
        done.append("깨끗한 워크트리 검사 통과")
    else:
        done.append("검사 명령을 못 찾음(건너뜀)")
    if not push:
        return {"ok": True, "done": done, "next": "밀기는 --push 를 줄 때만"}
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    r = run(["git", "push", "-q", "-u", "origin", branch])
    if r.returncode:
        return stop("push", r)
    done.append(f"push {branch}")
    if not merge:
        return {"ok": True, "done": done}
    if branch == base:
        return {"ok": True, "done": done + [f"{base} 에 바로 밀었다(PR 없음)"]}
    if not shutil.which("gh"):
        return stop("pr", why="gh 가 없다 -- PR · 머지는 LLM 이 GitHub 도구로 이어서")
    r = run(["gh", "pr", "create", "--fill", "--base", base, "--head", branch])
    if r.returncode and "already exists" not in (r.stderr or ""):
        return stop("pr", r)
    r = run(["gh", "pr", "merge", branch, "--merge"])
    if r.returncode:
        return stop("merge", r)
    r = run(["gh", "pr", "view", branch, "--json", "state,mergeCommit"])
    try:
        v = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        v = {}
    if v.get("state") != "MERGED":
        return stop("verify", r, "머지됐다는 조회 결과가 없다")
    done.append(f"merged {((v.get('mergeCommit') or {}).get('oid') or '')[:7]}")
    return {"ok": True, "done": done}


# ---------------- P3 기다리기 ----------------

def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:                                                        # 좀비(끝났지만 거둬지지 않음)는 끝난 것
        st = Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0]
        return st != "Z"
    except OSError:
        return True


_ERR = re.compile(r"Traceback \(most recent call last\)|\bError\b|\bFAILED\b|\bfatal:")


def _log_state(log: "str | None", tail: int) -> dict:
    if not log or not Path(log).is_file():
        return {}
    lines = Path(log).read_text(encoding="utf-8", errors="replace").splitlines()
    return {"log_lines": len(lines), "log_tail": lines[-tail:], "log_has_error": any(_ERR.search(x) for x in lines)}


def wait(pid: int, log: "str | None" = None, timeout: float = 3600, every: float = 2.0, tail: int = 15,
         progress_every: float = 0, on_progress=None) -> dict:
    """PID 로 기다린다(이름 패턴은 쓰지 않는다 -- 한글 명령줄 · 자기 자신을 잡는 문제). 끝나면 한 번 돌려준다.
    status: ended(끝남) · error(끝났는데 로그에 오류 흔적) · timeout(시간 초과, 아직 돈다) · not_found(처음부터 없는 PID).
    종료 코드는 남의 PID 라 알 수 없다 -- 로그로만 가른다. progress_every 초마다 진행(로그 줄 수 · 끝 줄)을 on_progress 로
    내보내고, 결과의 progress 에도 남긴다(중간 보고를 잃지 않게)."""
    t0 = time.time()
    if not _alive(pid):
        return {"pid": pid, "status": "not_found", "ended": True, "seconds": 0.0, **_log_state(log, tail)}
    progress, last = [], t0
    while _alive(pid) and time.time() - t0 < timeout:
        time.sleep(every)
        if progress_every and time.time() - last >= progress_every:
            last = time.time()
            ls = _log_state(log, 1)
            p = {"seconds": round(last - t0, 1), "log_lines": ls.get("log_lines"), "last": (ls.get("log_tail") or [None])[-1]}
            progress.append(p)
            if on_progress:
                on_progress(p)
    ended = not _alive(pid)
    out = {"pid": pid, "ended": ended, "seconds": round(time.time() - t0, 1), **_log_state(log, tail)}
    out["status"] = "timeout" if not ended else ("error" if out.get("log_has_error") else "ended")
    if progress:
        out["progress"] = progress[-20:]
    return out


# ---------------- 그림자 모드: 훅 -> 원장 ----------------

def _append(rec: dict, ledger: Path = None) -> None:
    ledger = Path(ledger or LEDGER)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _prior(session: str, ledger: Path) -> list:
    if not ledger.is_file():
        return []
    out = []
    for line in ledger.open(encoding="utf-8"):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append(r)
    return out


def shadow_event(ev: dict, ledger: "Path | None" = None) -> "dict | None":
    """훅 입력 하나 -> 원장 한 줄(제안 포함). 아무것도 막지 않는다."""
    ledger = Path(ledger or LEDGER)
    name = ev.get("hook_event_name")
    sess = str(ev.get("session_id", ""))[:36]
    cwd = ev.get("cwd") or os.getcwd()
    rec = {"t": round(time.time(), 3), "s": sess, "e": name}
    if name == "UserPromptSubmit":
        h, st = norm_hash(ev.get("prompt", "")), state_hash(cwd)
        rec.update(prompt=h, state=st)
        # P1: 같은 말을 전에 받았고, 그 턴이 끝났을 때(Stop)의 상태가 지금과 같다
        seen = [r for r in _prior(sess, ledger) if r.get("e") == "Stop" and r.get("prompt") == h and r.get("state") == st]
        if st and seen:
            rec["propose"] = "P1"
    elif name in ("PostToolUse", "PostToolUseFailure"):     # 실패한 호출은 PostToolUseFailure 로 온다(claude CLI 실측)
        tool = ev.get("tool_name", "")
        inp = ev.get("tool_input") or {}
        k = sorted(bash_kind(str(inp.get("command", "")))) if tool == "Bash" else (["pr"] if is_pr_tool(tool) else [])
        if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            k = ["edit", "state"]
        rec.update(tool=tool if not tool.startswith("mcp__") else "mcp", kind=k)
        call = _walp_call(tool, inp)
        if call:
            rec["call"] = call
            rec.update({"ok": False, "status": "tool_error"} if name == "PostToolUseFailure"
                       else _call_outcome(ev.get("tool_response")))
        if "commit" in k:
            rec["propose"] = "P2"
        if "poll" in k:
            rec["propose"] = "P3"
    elif name == "Stop":
        last = [r for r in _prior(sess, ledger) if r.get("s") == sess and r.get("e") == "UserPromptSubmit"]
        rec.update(state=state_hash(cwd), prompt=last[-1]["prompt"] if last else None,
                   transcript=ev.get("transcript_path"))
    else:
        return None
    _append(rec, ledger)
    return rec


def _walp_call(tool: str, inp: dict) -> "str | None":
    """walp 의 P2 · P3 도구를 부른 것인가(MCP 이름 또는 walp-exec 명령)."""
    if tool.endswith("walp_publish") or (tool == "Bash" and re.search(r"walp-exec\s+publish|walp\.execpolicy\s+publish",
                                                                     str(inp.get("command", "")))):
        return "P2"
    if tool.endswith("walp_wait") or (tool == "Bash" and re.search(r"walp-exec\s+wait|walp\.execpolicy\s+wait",
                                                                  str(inp.get("command", "")))):
        return "P3"
    return None


def _call_outcome(resp) -> dict:
    """도구 응답에서 결과 상태만 뽑는다(ok · stopped_at · status -- 정해진 낱말만, 글은 버린다)."""
    texts = []
    if isinstance(resp, dict):
        texts += [str(resp.get("stdout", ""))] + [str(c.get("text", "")) for c in resp.get("content", []) or []
                                                  if isinstance(c, dict)]
    elif isinstance(resp, list):
        texts += [str(c.get("text", "")) for c in resp if isinstance(c, dict)]
    elif isinstance(resp, str):
        texts.append(resp)
    for t in texts:
        i = t.find("{")
        try:
            d = json.loads(t[i:]) if i >= 0 else None
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict):
            ok = d.get("ok") if "ok" in d else d.get("status") == "ended"
            return {"ok": bool(ok), "status": str(d.get("stopped_at") or d.get("status") or ("ok" if ok else "?"))[:20]}
    return {"ok": None, "status": "unknown"}


SHADOW_TAG = "walp-exec shadow-hook"


def install_shadow(settings=None, remove=False) -> dict:
    p = Path(settings or Path.home() / ".claude" / "settings.json").expanduser()
    d = json.loads(p.read_text(encoding="utf-8")) if p.is_file() and p.read_text(encoding="utf-8").strip() else {}
    exe = shutil.which("walp-exec")
    cmd = f'"{exe}" shadow-hook' if exe else f'"{sys.executable}" -m walp.execpolicy shadow-hook'
    ours = lambda h: re.search(r'walp-exec"?\s+shadow-hook|walp\.execpolicy"?\s+shadow-hook', h.get("command", ""))
    before = json.dumps(d, sort_keys=True)
    hooks = d.setdefault("hooks", {})
    for ev in ("UserPromptSubmit", "PostToolUse", "PostToolUseFailure", "Stop"):
        groups = hooks.setdefault(ev, [])
        for g in groups:
            g["hooks"] = [h for h in g.get("hooks", []) if not ours(h)]
        groups[:] = [g for g in groups if g.get("hooks")]
        if not remove:
            g = {"hooks": [{"type": "command", "command": cmd, "timeout": 20}]}
            if ev.startswith("PostToolUse"):
                g["matcher"] = "*"
            groups.append(g)
        if not groups:
            del hooks[ev]
    if not hooks:
        del d["hooks"]
    changed = json.dumps(d, sort_keys=True) != before
    if changed:
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.is_file():
            p.with_name(p.name + ".bak-walp-shadow").write_bytes(p.read_bytes())
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"settings": str(p), "changed": changed, "installed": not remove}


# ---------------- 실제 그림자 원장의 보고 ----------------

def _turn_tokens(transcript: "str | None") -> "list[tuple[float, int]]":
    """추적의 (시각, 그 턴의 토큰) -- 원장 사건을 턴에 맞대려고."""
    if not transcript or not Path(transcript).is_file():
        return []
    import datetime as _dt
    out, seen = [], set()
    for line in Path(transcript).open(encoding="utf-8"):
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        m = d.get("message")
        if d.get("type") == "assistant" and isinstance(m, dict) and m.get("usage") and m.get("id") not in seen:
            seen.add(m.get("id"))
            u = m["usage"]
            ts = _dt.datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")).timestamp()
            out.append((ts, sum((u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                          "cache_read_input_tokens", "output_tokens"))))
    return out


def report(ledger: "Path | None" = None) -> dict:
    rows = _prior("", Path(ledger or LEDGER))
    by = collections.defaultdict(list)
    for r in rows:
        by[r["s"]].append(r)
    res = {k: {"proposals": 0, "agree": 0, "wrong": 0, "escalate": 0, "saved_turns": 0, "saved_tokens": 0}
           for k in ("P1", "P2", "P3")}
    for sess, ev in by.items():
        ev.sort(key=lambda r: r["t"])
        tr = next((r.get("transcript") for r in reversed(ev) if r.get("transcript")), None)
        turns = _turn_tokens(tr)

        def tok_between(a, b):
            xs = [t for ts, t in turns if a < ts <= b]
            return len(xs), sum(xs)
        for i, r in enumerate(ev):
            p = r.get("propose")
            if not p:
                continue
            res[p]["proposals"] += 1
            if p == "P1":
                stop = next((x for x in ev[i + 1:] if x["e"] == "Stop"), None)
                if stop and stop.get("state") == r.get("state"):
                    res[p]["agree"] += 1
                    n, t = tok_between(r["t"], stop["t"])
                    res[p]["saved_turns"] += n
                    res[p]["saved_tokens"] += t
                else:
                    res[p]["wrong"] += 1
            elif p == "P2":
                # commit 이 든 그 호출부터 센다 -- 'commit && push' 처럼 한 호출에 같이 있을 수 있다(사전등록: commit 뒤 12턴 안)
                nxt = [r] + [x for x in ev[i + 1:] if x["e"].startswith("PostToolUse")][:P2_WINDOW]
                pushed = next((j for j, x in enumerate(nxt) if "push" in x.get("kind", [])), None)
                pr = next((j for j, x in enumerate(nxt) if "pr" in x.get("kind", [])), None)
                edit = next((j for j, x in enumerate(nxt) if "edit" in x.get("kind", [])), None)
                if pushed is None:
                    res[p]["wrong"] += 1
                elif edit is not None and edit < max(pushed, pr or 0):
                    res[p]["escalate"] += 1
                elif pr is not None:
                    res[p]["agree"] += 1
                    end = nxt[max(j for j, x in enumerate(nxt) if {"push", "pr"} & set(x.get("kind", [])))]["t"]
                    n, t = tok_between(r["t"], end)
                    res[p]["saved_turns"] += n
                    res[p]["saved_tokens"] += t
                else:
                    res[p]["wrong"] += 1
            elif p == "P3":
                k = 1
                for x in ev[i + 1:]:
                    if not x["e"].startswith("PostToolUse"):
                        continue
                    if "poll" in x.get("kind", []):
                        k += 1
                    else:
                        break
                if i > 0 and "poll" in (ev[i - 1].get("kind") or []):
                    res[p]["proposals"] -= 1                           # 연쇄의 첫 것만 센다
                    continue
                res[p]["agree"] += 1
                res[p]["saved_turns"] += k - 1
                polls = [x for x in ev[i:] if x["e"].startswith("PostToolUse")][:k]
                if k > 1:
                    _, t = tok_between(polls[0]["t"], polls[-1]["t"])
                    res[p]["saved_tokens"] += t
    for v in res.values():
        v["agree_rate"] = v["agree"] / v["proposals"] if v["proposals"] else None
    # 실제로 부른 도구(P2 · P3): 호출 수 · 성공 · 실패 · 재시도(실패 뒤 같은 세션에서 다시 부름) · 그 턴의 토큰
    calls = {k: {"calls": 0, "ok": 0, "fail": 0, "unknown": 0, "retries": 0, "tokens": 0, "status": {}} for k in ("P2", "P3")}
    for sess, ev in by.items():
        tr = next((r.get("transcript") for r in reversed(ev) if r.get("transcript")), None)
        turns = _turn_tokens(tr)
        failed = set()
        for r in ev:
            c = r.get("call")
            if not c:
                continue
            v = calls[c]
            v["calls"] += 1
            v["ok" if r.get("ok") is True else "fail" if r.get("ok") is False else "unknown"] += 1
            v["status"][r.get("status", "?")] = v["status"].get(r.get("status", "?"), 0) + 1
            if c in failed:
                v["retries"] += 1
            (failed.add if r.get("ok") is False else failed.discard)(c)
            prev = [t for ts, t in turns if ts <= r["t"]]
            v["tokens"] += prev[-1] if prev else 0                  # 그 도구를 부른 턴(직전 턴)의 토큰
    res["calls"] = calls
    return res


# ---------------- 오프라인 그림자: 기록된 추적에 정책을 대 본다 ----------------

def replay(transcript: str, sidechain: bool = False) -> dict:
    """사전등록의 정의 그대로. P1 의 상태는 대리(그 저장소를 바꾸는 도구 호출이 있었나)."""
    steps = []        # ("user", prompt_hash) | ("turn", tokens, [(tool, input)], cwd)
    seen = set()
    for line in Path(transcript).open(encoding="utf-8"):
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        m = d.get("message")
        if not isinstance(m, dict) or (d.get("isSidechain") and not sidechain):
            continue
        if d.get("type") == "user":
            c = m.get("content")
            txt = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c if isinstance(x, dict)
                                                         and x.get("type") == "text") if isinstance(c, list) else ""
            if txt.strip() and not txt.lstrip().startswith("<"):
                steps.append(("user", norm_hash(txt), d.get("cwd") or ""))
        elif d.get("type") == "assistant" and m.get("usage"):
            u = m["usage"]
            tools = [(c.get("name", ""), c.get("input") or {}) for c in (m.get("content") or [])
                     if isinstance(c, dict) and c.get("type") == "tool_use"]
            t = sum((u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
                                              "output_tokens"))
            if m.get("id") in seen:
                prev = steps[-1]
                if prev[0] == "turn":
                    steps[-1] = ("turn", prev[1], prev[2] + tools, prev[3])
                continue
            seen.add(m.get("id"))
            steps.append(("turn", t, tools, d.get("cwd") or ""))

    def kinds(tool, inp):
        if tool == "Bash":
            return bash_kind(str(inp.get("command", "")))
        if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            return {"edit", "state"}
        if is_pr_tool(tool):
            return {"pr"}
        return set()

    total = sum(s[1] for s in steps if s[0] == "turn")
    res = {k: {"proposals": 0, "agree": 0, "wrong": 0, "escalate": 0, "saved_turns": 0, "saved_tokens": 0}
           for k in ("P1", "P2", "P3")}
    # P1: 같은 말을 전에 받았고, 그 뒤 지금까지 그 저장소를 바꾸는 호출이 없었다
    last_end = {}                                         # prompt_hash -> 그 말에 대한 턴들이 끝난 위치
    users = [i for i, s in enumerate(steps) if s[0] == "user"]
    for n, i in enumerate(users):
        h, cwd = steps[i][1], steps[i][2]
        end = users[n + 1] if n + 1 < len(users) else len(steps)
        turns = [s for s in steps[i + 1:end] if s[0] == "turn"]
        if h in last_end:
            prev = last_end[h]
            changed_between = any(("state" in kinds(t, inp)) and under(target_repo(t, inp, s[3]), cwd)
                                  for s in steps[prev:i] if s[0] == "turn" for t, inp in s[2])
            if not changed_between:
                res["P1"]["proposals"] += 1
                changed_now = any(("state" in kinds(t, inp)) and under(target_repo(t, inp, s[3]), cwd)
                                  for s in turns for t, inp in s[2])
                if changed_now:
                    res["P1"]["wrong"] += 1
                else:
                    res["P1"]["agree"] += 1
                    res["P1"]["saved_turns"] += len(turns)
                    res["P1"]["saved_tokens"] += sum(s[1] for s in turns)
        last_end[h] = end
    # P2 · P3: 턴 열 위에서
    T = [s for s in steps if s[0] == "turn"]
    K = [set().union(*[kinds(t, inp) for t, inp in s[2]]) if s[2] else set() for s in T]
    i = 0
    while i < len(T):
        if "commit" in K[i]:
            res["P2"]["proposals"] += 1
            win = list(range(i, min(len(T), i + P2_WINDOW + 1)))
            push = next((j for j in win if "push" in K[j]), None)
            pr = next((j for j in win if "pr" in K[j] and (push is None or j >= push)), None)
            edit = next((j for j in win[1:] if "edit" in K[j]), None)
            if push is None:
                res["P2"]["wrong"] += 1
            elif edit is not None and edit < max(push, pr or push):
                res["P2"]["escalate"] += 1
            elif pr is not None:
                res["P2"]["agree"] += 1
                last = max(j for j in win if K[j] & {"push", "pr"})   # 마지막 발행 걸음까지(사전등록)
                res["P2"]["saved_turns"] += last - i
                res["P2"]["saved_tokens"] += sum(T[j][1] for j in range(i + 1, last + 1))
            else:
                res["P2"]["wrong"] += 1
        if "poll" in K[i] and (i == 0 or "poll" not in K[i - 1]):
            j = i
            while j + 1 < len(T) and "poll" in K[j + 1]:
                j += 1
            res["P3"]["proposals"] += 1
            res["P3"]["agree"] += 1
            res["P3"]["saved_turns"] += j - i
            res["P3"]["saved_tokens"] += sum(T[x][1] for x in range(i + 1, j + 1))
        i += 1
    for v in res.values():
        v["agree_rate"] = v["agree"] / v["proposals"] if v["proposals"] else None
        v["saved_share"] = v["saved_tokens"] / total if total else 0
    return {"turns": len(T), "tokens": total, "policies": res}


# ---------------- 명령줄 ----------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="walp-exec", description="WALP 실행 정책층 P1~P3")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("state"); a.add_argument("--repo", default=".")
    a = sub.add_parser("publish"); a.add_argument("-m", "--message", required=True); a.add_argument("--repo", default=".")
    a.add_argument("--add", action="append", default=[], help="담을 새 파일(여러 번). 이름을 댄 새 파일만 담는다")
    a.add_argument("--test"); a.add_argument("--push", action="store_true"); a.add_argument("--merge", action="store_true")
    a.add_argument("--base", default="main")
    a = sub.add_parser("wait"); a.add_argument("--pid", type=int, required=True); a.add_argument("--log")
    a.add_argument("--timeout", type=float, default=3600)
    a.add_argument("--progress-every", type=float, default=0, help="초마다 진행 한 줄을 stderr 로")
    sub.add_parser("shadow-hook")
    for n in ("install-shadow", "uninstall-shadow"):
        sub.add_parser(n).add_argument("--settings")
    a = sub.add_parser("replay"); a.add_argument("transcript"); a.add_argument("--sidechain", action="store_true")
    a = sub.add_parser("report"); a.add_argument("--ledger")
    args = ap.parse_args(argv)
    if args.cmd == "shadow-hook":
        try:
            shadow_event(json.loads(sys.stdin.read() or "{}"))
        except Exception as e:  # noqa: BLE001 -- 그림자는 아무것도 막지 않는다
            print(f"walp-exec shadow-hook: {type(e).__name__}: {e}", file=sys.stderr)
        return 0
    if args.cmd == "state":
        out = {"state": state_hash(args.repo)}
    elif args.cmd == "publish":
        out = publish(args.repo, args.message, args.test, args.push, args.merge, args.base, files=args.add)
    elif args.cmd == "wait":
        out = wait(args.pid, args.log, args.timeout, progress_every=args.progress_every,
                   on_progress=lambda p: print(json.dumps(p, ensure_ascii=False), file=sys.stderr, flush=True))
    elif args.cmd in ("install-shadow", "uninstall-shadow"):
        out = install_shadow(args.settings, remove=args.cmd == "uninstall-shadow")
    elif args.cmd == "replay":
        out = replay(args.transcript, args.sidechain)
    else:
        out = report(Path(args.ledger) if args.ledger else None)
    print(json.dumps(out, ensure_ascii=False, indent=1 if args.cmd in ("replay", "report") else None))
    if args.cmd == "wait":
        return {"ended": 0, "error": 1, "timeout": 2, "not_found": 3}.get(out["status"], 1)
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
