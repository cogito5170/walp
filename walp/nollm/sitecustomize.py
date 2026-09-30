"""WALP LLM 차단 — 이 디렉터리가 PYTHONPATH 맨 앞에 있으면 **모든 파이썬 프로세스**(도구가 띄우는 자식까지)가
시작할 때 이것을 읽는다. LLM 호스트로 가는 이름 풀이를 막고, 시도를 WALP_NOLLM_LOG 에 한 줄씩 적는다.

분류(se_tools 의 추정)는 틀릴 수 있다. 그래서 막고 센다 — 'LLM 을 안 썼다' 는 주장을 이 기록이 받친다.
"""
import os
import socket
import time

_HOSTS = ("generativelanguage.googleapis.com", "aiplatform.googleapis.com", "api.anthropic.com",
          "api.openai.com", "claude.ai")
_LOG = os.environ.get("WALP_NOLLM_LOG", "")
_orig = socket.getaddrinfo


def _blocked(host):
    h = (host or "").lower() if isinstance(host, str) else ""
    return any(h == x or h.endswith("." + x) for x in _HOSTS)


def _getaddrinfo(host, *a, **k):
    if _blocked(host):
        if _LOG:
            try:
                with open(_LOG, "a") as f:
                    f.write(f"{time.time():.3f}\tnet\t{host}\t{os.getpid()}\n")
            except OSError:
                pass
        raise OSError(f"WALP: LLM 호스트 차단 ({host})")
    return _orig(host, *a, **k)


socket.getaddrinfo = _getaddrinfo
