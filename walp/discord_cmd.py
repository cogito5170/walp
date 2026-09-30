"""WALP 고정 명령 — `!walp`. **에이전트(LLM) 앞에서** 가로채 사람의 말을 그대로 파서로 보낸다.

사용자 친화성은 저자가 끼지 않은 채로 재야 한다. 에이전트가 사람의 말을 다듬어 넘기면
파서가 아니라 에이전트가 친절한 것이 된다 — 그래서 고정 명령이다(dispatch.py 규약).

  !walp <명령>                      해석하고 시뮬 한 판(예: `!walp 파란 비자카드 찾아줘`)
  !walp 해석 <명령>                 해석만
  !walp 도구 <요청>                 SE 도구를 LLM 없이 골라 LLM 을 막고 부른다(절이 여럿이면 차례로)
  !walp 도구목록 · 도구점검 [결과]   SE 도구 목록 / 도구마다 LLM 차단 실행 점검(배경)
  !walp 루프 <명령>                 LoopAct — 해석 → 후보마다 시뮬 3판(4바퀴·30호출 예산)
  !walp 가르치기 <낱말> <범주> <개념>  모르는 말 가르치기(관리 채널)
  !walp 점수 <1~5> [한마디]         써 본 느낌
  !walp 설문 [점수 열 개]           SUS 설문(인자 없으면 문항을 보인다)
  !walp 결과                        사용성 집계(원장에서 센다)
  !walp 도움
"""
from __future__ import annotations

PREFIX = "!walp"
HELP = ("**WALP** — LLM 없이 명령을 해석하고 시뮬레이터에서 물체를 찾는다.\n"
        "`!walp 파란 비자카드 찾아줘` · `!walp find a red mug, avoid the shelves` · `!walp 해석 <명령>`\n"
        "`!walp 도구 CTLE 개념 알려줘` · `!walp 도구목록` · `!walp 루프 find the container` · `!walp 가르치기 cerulean color blue` · `!walp 점수 4 알아듣기 쉬웠다` · `!walp 설문` · `!walp 결과`\n"
        "셸·쓰기·메일 도구는 바로 안 돌리고 확인 표를 준다: `!walp 도구 실행해줘 `ls logs`` → `!walp 확인 <표>`(관리 채널, 10분).\n"
        "바깥 지식 물음은 LLM 없이 검색해 **받은 문장 그대로** 출처와 함께 보인다: `!walp 찾아보기 <물음>`(모르는 말이 든 물음은 저절로).\n"
        "인사·질문·감사·불만 같은 말에도 답한다(대화 행위 12개 — 잘못 알아들었으면 `!walp 행위 <이름>` 으로 고쳐 주세요). `!walp 진화` 는 쌓인 대화를 재생해 규칙을 진화시킨다(관리).\n"
        "모르는 말은 추측하지 않고 되묻는다. 모르는 말 뒤에 **고쳐 말하면** 뜻을 물어 보고, `네` 면 배워서 다음부터 알아듣는다. 쓴 말과 결과, **WALP 답에 단 반응 이모지와 `!walp` 글을 고친 것**은 사용성 평가를 위해 가명(소금 친 해시 — 같은 사람은 같은 값)으로 기록된다.")


def _누구() -> str:
    try:
        import agent_context
        return str(agent_context.current_author.get())
    except Exception:   # noqa: BLE001
        return "discord"


def run(text: str, runner=None, allow_write: bool = True, who: "str | None" = None, via: str = "discord") -> "str | None":
    t = (text or "").strip()
    if not (t == PREFIX or t.startswith(PREFIX + " ")):
        return None
    from walp import front          # 가볍다(표준 라이브러리만) — 그래도 필요할 때만
    body = t[len(PREFIX):].strip()
    who = who or _누구()
    if not body or body in ("도움", "help"):
        return HELP
    head, _, rest = body.partition(" ")
    rest = rest.strip()
    if head == "해석":
        return front.interpret(rest, who, via) if rest else "해석할 명령을 넣어 달라."
    if head == "도구":
        return front.se_tool(rest, who, via) if rest else "요청을 넣어 달라(예: `!walp 도구 채널 손실 25dB 에서 serdes 링크 BER 재 줘`)."
    if head in ("확인", "취소"):
        if head == "확인" and not allow_write:
            return "확인(부작용 실행)은 관리 채널에서만."
        return front.se_confirm(rest, who, via, cancel=(head == "취소")) if rest else "표를 넣어 달라(예: `!walp 확인 a1b2c3`)."
    if head == "찾아보기":
        return front.search(rest, who, via) if rest else "물음을 넣어 달라(예: `!walp 찾아보기 WebAuthn 이 뭐야`)."
    if head == "찾아보기점검":
        if not allow_write and rest != "결과":
            return "찾아보기점검 시작은 관리 채널에서만 — 결과는 `!walp 찾아보기점검 결과`."
        return front.search_eval(start=(rest != "결과"))
    if head == "도구목록":
        return front.se_catalog()
    if head == "도구점검":
        if not allow_write and rest != "결과":
            return "도구점검 시작은 관리 채널에서만 — 결과는 `!walp 도구점검 결과`."
        return front.se_smoke(start=(rest != "결과"))
    if head == "루프":
        from walp.loop import walp_campaign
        return walp_campaign(rest, k=3) if rest else "점검할 명령을 넣어 달라."
    if head == "가르치기":
        if not allow_write:
            return "가르치기는 관리 채널에서만 — 사전을 바꾸는 일이라서."
        parts = rest.split()
        if len(parts) != 3:
            return "형식: `!walp 가르치기 <낱말> <color|object|brand|zone|modifier> <개념|->`"
        return front.teach(parts[0], parts[1], parts[2], who, via)
    if head == "점수":
        n, _, c = rest.partition(" ")
        if not n.isdigit():
            return "형식: `!walp 점수 <1~5> [한마디]`"
        return front.rate(int(n), c, who, via)
    if head == "설문":
        nums = [int(x) for x in rest.replace(",", " ").split() if x.isdigit()]
        return front.sus(nums, who, via)
    if head == "결과":
        return front.report()
    if head == "행위":
        return front.act_fix(rest or "?", who, via, allow_write)
    if head == "진화":
        if not allow_write:
            return "진화(대화 행위 XCS 재생·관문)는 관리 권한에서만 — 몇 분 걸리고 승격하면 모두의 답이 바뀐다."
        return front.evolve_dialog()
    return front.run(body, who, via, allow_write=allow_write)
