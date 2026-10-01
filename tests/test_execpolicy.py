"""실행 정책층 P1~P3 -- 진짜 git 저장소(bare 원격) · 진짜 백그라운드 프로세스로 돌린다."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from walp import execpolicy as X  # noqa: E402

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


_SAVED_ENV = {}


def setUpModule():
    # publish 의 기본 runner 는 env 를 안 넘긴다 -- 기계의 git 신원에 기대지 않게 여기서 세운다
    for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        _SAVED_ENV[k] = os.environ.get(k)
        os.environ[k] = ENV[k]


def tearDownModule():
    for k, v in _SAVED_ENV.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def sh(*a, cwd=None):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True, env=ENV, check=True).stdout


def repo_with_remote(test_ok=True):
    d = Path(tempfile.mkdtemp())
    sh("git", "init", "-q", "--bare", "-b", "main", str(d / "remote.git"))
    r = d / "work"
    sh("git", "init", "-q", "-b", "main", str(r))
    sh("git", "-C", str(r), "remote", "add", "origin", str(d / "remote.git"))
    (r / "tests").mkdir()
    (r / "tests" / "__init__.py").write_text("")
    (r / "tests" / "test_a.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n"
                                            f"        self.assertTrue({test_ok})\n")
    sh("git", "-C", str(r), "add", "-A")
    sh("git", "-C", str(r), "commit", "-q", "-m", "init")
    sh("git", "-C", str(r), "push", "-q", "-u", "origin", "main")
    return r


class P1State(unittest.TestCase):
    def test_상태가_바뀌면_해시가_바뀐다(self):
        r = repo_with_remote()
        a = X.state_hash(r)
        self.assertEqual(a, X.state_hash(r))                       # 아무것도 안 하면 같다
        (r / "x.txt").write_text("1")
        b = X.state_hash(r)
        self.assertNotEqual(a, b)                                  # 작업 트리
        sh("git", "-C", str(r), "add", "-A"); sh("git", "-C", str(r), "commit", "-q", "-m", "x")
        c = X.state_hash(r)
        self.assertNotEqual(b, c)                                  # HEAD
        sh("git", "-C", str(r), "push", "-q", "origin", "main")
        self.assertNotEqual(c, X.state_hash(r))                    # 원격 추적 ref

    def test_git_이_아니면_모른다(self):
        self.assertIsNone(X.state_hash(tempfile.mkdtemp()))


class P2Publish(unittest.TestCase):
    def test_검사_통과면_밀기까지(self):
        r = repo_with_remote()
        (r / "a.py").write_text("x = 1\n")
        out = X.publish(r, "a 추가", push=True)
        self.assertTrue(out["ok"], out)
        self.assertEqual(sh("git", "-C", str(r), "rev-parse", "HEAD"), sh("git", "-C", str(r), "rev-parse", "origin/main"))
        self.assertIn("깨끗한 워크트리 검사 통과", out["done"])

    def test_검사가_실패하면_밀지_않고_멈춘다(self):
        r = repo_with_remote()
        (r / "tests" / "test_a.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n"
                                                "        self.assertTrue(False)\n")
        before = sh("git", "-C", str(r), "rev-parse", "origin/main")
        out = X.publish(r, "깨진 것", push=True)
        self.assertEqual((out["ok"], out["stopped_at"]), (False, "test"))
        self.assertEqual(before, sh("git", "-C", str(r), "rev-parse", "origin/main"))   # 원격 그대로

    def test_커밋에_안_담긴_파일은_깨끗한_워크트리가_잡는다(self):
        # CLAUDE.md 의 그 병: 작업 디렉터리에만 있는 파일로 검사가 초록이 된다
        r = repo_with_remote()
        (r / "tests" / "test_a.py").write_text("import unittest\nfrom helper import X\nclass T(unittest.TestCase):\n"
                                                "    def test_a(self):\n        self.assertEqual(X, 1)\n")
        (r / ".gitignore").write_text("helper.py\n")
        (r / "helper.py").write_text("X = 1\n")                     # 무시되어 커밋에 안 들어간다
        out = X.publish(r, "helper 빠짐", push=True)
        self.assertEqual(out.get("stopped_at"), "test", out)

    def test_밀기는_깃발을_줄_때만(self):
        r = repo_with_remote()
        (r / "b.py").write_text("y = 2\n")
        before = sh("git", "-C", str(r), "rev-parse", "origin/main")
        out = X.publish(r, "b")
        self.assertTrue(out["ok"])
        self.assertEqual(before, sh("git", "-C", str(r), "rev-parse", "origin/main"))

    def test_담을_것이_없으면_멈춘다(self):
        self.assertEqual(X.publish(repo_with_remote(), "빈 것")["stopped_at"], "commit")

    def test_원격이_앞서면_밀기에서_멈춘다(self):
        r = repo_with_remote()
        other = Path(tempfile.mkdtemp()) / "o"
        sh("git", "clone", "-q", str(r.parent / "remote.git"), str(other))
        (other / "o.txt").write_text("o")
        sh("git", "-C", str(other), "add", "-A"); sh("git", "-C", str(other), "commit", "-q", "-m", "o")
        sh("git", "-C", str(other), "push", "-q", "origin", "main")
        (r / "c.py").write_text("z = 3\n")
        out = X.publish(r, "c", push=True)
        self.assertEqual(out["stopped_at"], "push")                # --force 를 쓰지 않는다 -- LLM 이 merge 로 잇는다


    def test_검사용_임시_폴더를_남기지_않는다(self):
        r = repo_with_remote()
        (r / "b.txt").write_text("b")
        tmp = Path(tempfile.mkdtemp())
        old = tempfile.tempdir
        tempfile.tempdir = str(tmp)
        try:
            out = X.publish(r, "b")
        finally:
            tempfile.tempdir = old
        self.assertTrue(out["ok"], out)
        self.assertEqual(list(tmp.iterdir()), [])


class P3Wait(unittest.TestCase):
    def test_끝날_때까지_막고_한_번_돌려준다(self):
        log = Path(tempfile.mkdtemp()) / "job.log"
        p = subprocess.Popen(["bash", "-c", f"for i in 1 2 3; do echo 줄$i >> {log}; sleep 0.3; done"])
        t0 = time.time()
        out = X.wait(p.pid, str(log), timeout=30, every=0.1)
        p.wait()
        self.assertTrue(out["ended"])
        self.assertGreaterEqual(time.time() - t0, 0.8)
        self.assertEqual(out["log_tail"][-1], "줄3")

    def test_시간이_넘으면_끝나지_않았다고_말한다(self):
        p = subprocess.Popen(["sleep", "5"])
        try:
            out = X.wait(p.pid, timeout=0.3, every=0.1)
            self.assertFalse(out["ended"])
        finally:
            p.kill(); p.wait()


    def test_잘못된_PID_는_기다리지_않는다(self):
        # os.kill(0|-1, 0) 은 그룹 · 전체 검사라 성공한다 -- 예전에는 timeout 끝까지 '살아 있다' 로 막았다
        self.assertFalse(X._alive(0))
        self.assertFalse(X._alive(-1))
        with self.assertRaises(ValueError):
            X.wait(0, timeout=5, every=0.05)
        r = subprocess.run([sys.executable, "-m", "walp.execpolicy", "wait", "--pid", "0"],
                           cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 2)


class Shadow(unittest.TestCase):
    def test_원장에_글을_안_적고_제안만(self):
        led = Path(tempfile.mkdtemp()) / "s.jsonl"
        r = repo_with_remote()
        ev = lambda **k: X.shadow_event({"session_id": "S", "cwd": str(r), **k}, led)
        ev(hook_event_name="UserPromptSubmit", prompt="비밀 요청 A")
        ev(hook_event_name="PostToolUse", tool_name="Bash", tool_input={"command": "git commit -m '비밀 메시지'"})
        ev(hook_event_name="PostToolUse", tool_name="Bash", tool_input={"command": "kill -0 123; sleep 5"})
        ev(hook_event_name="Stop")
        rec = ev(hook_event_name="UserPromptSubmit", prompt="비밀 요청 A")     # 같은 말 · 같은 상태
        self.assertEqual(rec.get("propose"), "P1")
        ev(hook_event_name="Stop")
        (r / "n.txt").write_text("n")                                          # 턴 **사이에** 상태가 바뀌었다
        rec = ev(hook_event_name="UserPromptSubmit", prompt="비밀 요청 A")
        self.assertNotEqual(rec.get("propose"), "P1")
        text = led.read_text()
        for s in ("비밀", "git commit", "kill -0"):
            self.assertNotIn(s, text)
        props = [json.loads(x).get("propose") for x in text.splitlines()]
        self.assertEqual([p for p in props if p], ["P2", "P3", "P1"])

    def test_설치는_세_자리에_하나씩(self):
        p = Path(tempfile.mkdtemp()) / "settings.json"
        p.write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "walp-front hook"}]}]}}))
        X.install_shadow(p)
        self.assertFalse(X.install_shadow(p)["changed"])
        d = json.loads(p.read_text())
        for ev in ("UserPromptSubmit", "PostToolUse", "Stop"):
            n = sum("shadow-hook" in h["command"] for g in d["hooks"][ev] for h in g["hooks"])
            self.assertEqual(n, 1, ev)
        self.assertIn("walp-front hook", json.dumps(d))                          # 남의 훅은 그대로
        X.install_shadow(p, remove=True)
        self.assertNotIn("shadow-hook", p.read_text())
        self.assertIn("walp-front hook", p.read_text())


    def test_훅은_아무것도_돌려주지_않는다(self):
        # P1 자동 재사용은 꺼져 있다: 제안이 나와도 훅의 stdout 은 비어 있어야 Claude Code 가 아무것도 바꾸지 않는다
        r = repo_with_remote()
        led = Path(tempfile.mkdtemp()) / "s.jsonl"
        env = {**ENV, "WALP_SHADOW_LEDGER": str(led)}
        evs = [{"hook_event_name": "UserPromptSubmit", "prompt": "같은 말"}, {"hook_event_name": "Stop"},
               {"hook_event_name": "UserPromptSubmit", "prompt": "같은 말"}, "깨진 입력"]
        for ev in evs:
            inp = json.dumps({"session_id": "S", "cwd": str(r), **ev}) if isinstance(ev, dict) else ev
            out = subprocess.run([sys.executable, "-m", "walp.execpolicy", "shadow-hook"], input=inp, cwd=ROOT,
                                 capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual((out.returncode, out.stdout), (0, ""))
        self.assertIn("P1", [json.loads(x).get("propose") for x in led.read_text().splitlines()])

    def test_다른_설치기와_번갈아_돌아도_제자리이고_백업은_원본이다(self):
        from walp import llmfront as F
        p = Path(tempfile.mkdtemp()) / "settings.json"
        orig = '{"model": "opus"}'
        p.write_text(orig)
        X.install_shadow(p)
        F.install_hook(p)
        self.assertFalse(X.install_shadow(p)["changed"])
        self.assertFalse(F.install_hook(p)["changed"])
        self.assertEqual(p.with_name("settings.json.bak-walp-shadow").read_text(), orig)


class Report(unittest.TestCase):
    def test_한_호출에_commit_과_push(self):
        # 실제 그림자에서 찾은 결함: 'git commit ... && git push' 가 한 호출이면 밀기를 못 보고 '틀림' 이라 했다
        led = Path(tempfile.mkdtemp()) / "s.jsonl"
        rows = [{"t": 1, "s": "S", "e": "PostToolUse", "kind": ["commit", "push", "state"], "propose": "P2"},
                {"t": 2, "s": "S", "e": "PostToolUse", "kind": ["pr"]},
                {"t": 3, "s": "S", "e": "PostToolUse", "kind": ["pr"]},
                {"t": 4, "s": "S", "e": "Stop", "state": "x"}]
        led.write_text("\n".join(json.dumps(r) for r in rows))
        r = X.report(led)["P2"]
        self.assertEqual((r["proposals"], r["agree"], r["wrong"]), (1, 1, 0))


    def test_모양이_다른_줄과_시각_없는_추적에도_죽지_않는다(self):
        d = Path(tempfile.mkdtemp())
        tr = d / "t.jsonl"
        tr.write_text(json.dumps({"type": "assistant", "message": {"id": "m1", "usage": {"output_tokens": 5}}}) + "\n")
        led = d / "s.jsonl"
        rows = [{"t": 1, "s": "S", "e": "PostToolUse", "kind": ["poll"], "propose": "P3"},
                {"note": "사람이 손으로 넣은 줄"}, [1, 2],
                {"t": 2, "s": "S", "e": "Stop", "state": "x", "transcript": str(tr)}]
        led.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        self.assertEqual(X.report(led)["P3"]["proposals"], 1)

    def test_turn_tokens_는_하위_에이전트_줄을_뺀다(self):
        tr = Path(tempfile.mkdtemp()) / "t.jsonl"
        row = lambda i, n, side: {"type": "assistant", "isSidechain": side, "timestamp": f"2026-10-01T00:00:0{i}Z",
                                  "message": {"id": f"m{i}", "usage": {"output_tokens": n}}}
        tr.write_text("\n".join(json.dumps(row(*a)) for a in [(1, 7, False), (2, 5000, True)]) + "\n")
        self.assertEqual([t for _, t in X._turn_tokens(str(tr))], [7])


class Replay(unittest.TestCase):
    def test_사전등록_정의대로(self):
        def turn(i, t, tool=None, inp=None):
            c = [{"type": "tool_use", "id": f"u{i}", "name": tool, "input": inp or {}}] if tool else []
            return {"type": "assistant", "cwd": "/r", "message": {"id": f"m{i}", "content": c,
                    "usage": {"cache_read_input_tokens": t, "output_tokens": 0}}}
        user = lambda s: {"type": "user", "cwd": "/r", "message": {"content": s}}
        rows = [user("일 해줘"),
                turn(1, 10, "Bash", {"command": "cd /r && git commit -m x"}),
                turn(2, 20, "Bash", {"command": "cd /r && git push origin b"}),
                turn(3, 30, "mcp__github__create_pull_request"),
                turn(4, 40, "mcp__github__merge_pull_request"),
                turn(5, 50, "Bash", {"command": "kill -0 9 && sleep 10"}),
                turn(6, 60, "Bash", {"command": "kill -0 9 && sleep 10"}),
                turn(7, 70, "Bash", {"command": "kill -0 9 && sleep 10"}),
                user("Stop hook feedback: 65 unpushed"), turn(8, 80),
                user("Stop hook feedback: 65 unpushed"), turn(9, 90),                         # 같은 말 · 사이에 변경 없음
                user("Stop hook feedback: 65 unpushed"), turn(10, 100, "Bash", {"command": "cd /r && git reset --hard x"})]
        p = Path(tempfile.mkdtemp()) / "t.jsonl"
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
        r = X.replay(str(p))["policies"]
        self.assertEqual((r["P2"]["agree"], r["P2"]["saved_turns"], r["P2"]["saved_tokens"]), (1, 3, 20 + 30 + 40))
        self.assertEqual((r["P3"]["proposals"], r["P3"]["saved_turns"], r["P3"]["saved_tokens"]), (1, 2, 60 + 70))
        self.assertEqual((r["P1"]["proposals"], r["P1"]["agree"], r["P1"]["wrong"]), (2, 1, 1))
        self.assertEqual(r["P1"]["saved_tokens"], 90)


if __name__ == "__main__":
    unittest.main()
