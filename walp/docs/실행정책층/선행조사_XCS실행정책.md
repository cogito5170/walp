# 선행조사 — XCS 를 에이전트 루프의 걸음 경계 정책으로 (설계 §4)

조사 2026-10-01, 하위 에이전트 한 번(웹 검색 21 질의 + 원문 읽기 시도). `선행조사.md` 의 '아직 못 본 곳' 중
**XCS/LCS + LLM** 칸을 메우려던 조사다. 묻는 것은 하나다: *걸음 경계마다 `LLM 부르기 · 직전 결과 재사용(P1) · 발행 매크로(P2) ·
이벤트 기다리기(P3) · 사람에게 묻기` 중 하나를 XCS(+GA)가 고르고, 보상이 결정론적 검증 − 토큰 − 잘못 자동 실행 벌점인 것* 이
이미 있나.

**읽기 수준을 먼저 밝힌다.** 프록시가 이번에도 거의 다 막았다 — `arxiv.org`(abs · html · export) · `alphaxiv` · `openreview` ·
`dl.acm.org` · `link.springer.com` · `dblp.org` · `gecco-2025/2026.sigevo.org` · `fg-oc.gi.de`(XCS 30주년 특집 공모) ·
`rlj.cs.umass.edu` · `mdpi.com` · `researchgate` · `semanticscholar` · `openalex` · `crossref` · `aclanthology` · `ieeexplore` 전부
EGRESS_BLOCKED / 접속 불가를 직접 확인했다. `gh search` 도 이 세션에선 403(저장소 범위 밖). **열린 곳은 `github.com` 페이지뿐**이었다.
그래서 **논문은 전문도 초록 페이지도 하나도 못 읽었다 — 아래 논문 인용은 전부 [출처:조각]** 이다(검색 결과 요약에서 본 것, 수치 미확인).
[출처:전문] 은 GitHub 문서 자체(README · 이슈 본문)를 읽은 것이고, 그 저장소가 딸린 논문을 읽었다는 뜻이 아니다.

## 가장 가까운 선행연구

**When2Ask** — "Enabling Intelligent Interactions between an Agent and an LLM: A Reinforcement Learning Approach",
arXiv:2306.03604, RLC 2024 (RLJ) [출처:조각]

- 무엇: Planner–Actor–Mediator 틀. 중재자(mediator)의 **묻기 정책을 RL 로 배워** 걸음마다 *LLM 플래너에 물을지 / 지금 계획을 계속할지* 를
  고른다. 불필요한 질의를 벌해 상호작용 비용을 줄인다. MiniGrid · Habitat 에서 잼.
- 왜 가장 가깝나: "걸음마다, LLM 이 아닌 학습된 정책이, LLM 호출 여부를 비용을 넣은 보상으로 고른다" 는 우리 설계 §4 의 뼈대와 같다.
  이번 조사에서 XCS/LCS 쪽에서는 이보다 가까운 것을 못 찾았다(아래 '못 본 곳' — 찾을 곳을 대부분 못 열었다).

같은 자리를 다투는 두 번째 후보: **AgenticCache** (arXiv:2604.24039, 2026, Kim · Wu · Tambe) [출처:조각] — 런타임 **계획 전이 캐시**를
조회해 **걸음마다의 LLM 호출을 건너뛰고**, 뒤에서 LLM 이 캐시 항목을 비동기로 검증 · 다듬는다. 요약 수치: 성공률 +22% · 지연 −65% ·
토큰 −50%. 체화(embodied) 다중 에이전트 벤치 넷. "재사용으로 LLM 을 건너뛴다" 는 P1 쪽으로는 When2Ask 보다 가깝고, 정책이 학습된
분류기가 아니라 빈도 캐시라는 점이 다르다.

## 우리가 다른 점 — 주장이 아니라 후보로

1. **정책 표현이 XCS(정확도 기반 적합도 + GA 로 규칙 일반화)** 다. When2Ask 는 신경망 RL 정책, AgenticCache 는 빈도 캐시.
   XCS 로 "LLM 을 부를지" 를 고른 것은 이번에도 못 찾았다 — 단 GECCO/IWLCS 원문을 **못 열었으므로** 이것은 결과가 아니다.
2. **행동이 이분(묻기/안 묻기)이 아니라 다섯**: LLM · 같은 상태 재사용 · 결정론적 매크로 · 이벤트 기다리기 · 사람에게 묻기.
   '사람에게 묻기' 를 같은 행동 집합에 넣은 것은 에스컬레이션 문헌(아래)과 겹친다.
3. **보상의 성공 항이 결정론적 검증**(검사 통과 · API `merged: true`)이고, 잘못 자동 실행에 μ ≫ λ 벌점. When2Ask 는 과제 보상 − 질의 비용,
   AgenticCache 는 LLM 이 캐시를 다시 확인한다(결정론적 검사가 아니다) — 둘 다 [출처:조각] 기준의 이해다.
4. **재사용 키가 저장소 상태 해시**(HEAD · 원격 · 작업 트리). 상태 키 캐시 자체는 TVCache 가 이미 한다(아래) — 차이는 그것이
   *훈련 시* 도구 결과 캐시이고, 고르는 정책이 없다는 점뿐이다.
5. **영역**: 격자세계 · 체화 벤치가 아니라 코딩 에이전트(Claude Code · Gemini CLI)의 실제 추적에서 잰 상한(설계 §1–2).
6. 바꾸기는 사전등록 · 봉인 모음 래칫으로만 — 이 저장소의 방식.

**1 이 유일하게 '방법' 의 차이**이고 2–6 은 조합 · 영역의 차이다. 1 조차 "XCS 가 신경망 정책보다 이 문제에 낫다" 는 근거가 아직 없다 —
해석 가능성(규칙을 사람이 읽고 V&V 로 봉인)이 이유라면 그것을 재야 한다.

## 그 밖에 겹치는 것

**XCS/LCS 를 소프트웨어 공정의 결정에 쓴 것 — 가장 가까운 LCS 쪽 선행**
- Rosenbauer · Stein 외, XCS/XCSF 로 **CI 의 시험 사례 우선순위 · 선택**(GECCO 2020 계열; "A Learning Classifier System for Automated Test
  Case Prioritization and Selection", SN Computer Science, doi:10.1007/s42979-022-01255-1; "Transfer Learning for Automated Test Case
  Prioritization Using XCSF") [출처:조각]. CI 이력에서 결정론적 신호(시험 결과)로 보상하는 LCS — 우리와 같은 "결정론적 보상 + XCS" 이나
  LLM 게이팅이 아니다.
- XCS 와 심층 RL 의 혼성(Deep RL with a Classifier System, Springer 2022/2025 장) · Active RL 로드맵(arXiv:2201.03947) · Organic Computing 에서
  XCS 를 자기적응 구성요소로 쓰는 계보 [출처:조각].
- LCS 조사 논문: "A Survey on Learning Classifier Systems from 2022 to 2024" (doi:10.1145/3638530.3664165, GECCO'24 Companion) ·
  "Recent Advances in Evolutionary Rule-Based Machine Learning (2024 to 2026)" (doi:10.1145/3795101.3814713) [출처:조각 — 제목만, 본문의 LLM 언급 여부 미확인].
  **두 번째가 가장 먼저 읽어야 할 문서다.** 막혀서 못 읽었다.
- XCS 30주년 특집 공모(Evolutionary Intelligence, 마감 2024-11-30 연장) [출처:조각 — 공모 페이지 차단]. 실린 논문 목록은 못 봤다.
- GECCO 2025/2026 의 "LLMs for and with Evolutionary Computation" 워크숍 · 2026 "LLM-designed EA" 경진 [출처:조각] — LLM 이 EA 를 설계하는
  방향이지, 진화 규칙이 LLM 호출을 고르는 방향이 아니다.

**걸음마다 LLM 을 건너뛰는 학습된/경량 정책**
- ProgRouter(arXiv:2608.25992) · Learning Agent Routing From Early Experience(2605.07180) · Budget-Aware Agentic Routing(2602.21227) ·
  Agent-as-a-Router(2606.22902) [출처:조각] — 걸음마다 **어느 LLM/에이전트** 를 고른다. "LLM 없음" 을 행동으로 두는지는 조각에서 안 보인다.
- When2Tool(arXiv:2605.09252) · To Call or Not to Call(2605.00737) [출처:조각] — 도구를 부를지(LLM 을 부를지가 아니라). 은닉 상태 탐침.
- Controlling Performance and Budget of a Centralized Multi-agent LLM System with RL(2511.02755) [출처:조각].

**상태 키 재사용 · 기록 재생 · 계획 캐시**
- TVCache(arXiv:2602.10986, ICML 2026 포스터) [논문 출처:조각 · README 출처:전문] — 도구 호출 **열의 최장 접두**로 샌드박스 상태를 식별해 결과를
  재사용(적중 최대 70%). README 기준: RL **훈련 시** 전용, 재사용 여부는 접두 일치로 결정론적 — 학습된 정책 없음.
- AgentRR(arXiv:2505.17716) [출처:조각] — 기록 → 요약 → 재생, 경험마다 **검사 함수**(전제 · 불변식)를 만들어 재생 중 벗어난 행동을 거절.
  "자동 실행은 검사를 지나야" 하는 우리 불변식 2 와 가장 가깝다.
- Agentic Plan Caching(2506.14852) · Temporal Semantic Caching in Plan-Execute(2605.20630) · Hierarchical Caching for Agentic Workflows
  (MDPI MAKE 8(2):30, 2026; 차단) [출처:조각].
- memory-reuse(github.com/pranit-p/memory-reuse) [출처:전문 — README] — 정확 · 도구(TTL) · 의미 캐시. 키는 입력 해시 + 범위, **환경 상태는 키에
  없음**, 재사용 결과 검증 없음, 학습된 정책 없음(조언용 분석기만).

**기다리기의 이벤트화(P3)**
- openai/codex 이슈 #35259 [출처:전문 — 이슈 본문] — 기다리기/상태 폴링만 한 모델 턴이 로컬 토큰의 **19.8%**, 긴 하위 과제 하나에서 ~45%.
  제안된 고침: 하네스 안에서 이벤트 기반 기다리기 · 상태가 안 바뀌었으면 모형 호출 안 하기. **우리 P1 · P3 와 같은 관찰을 공급자 이슈가 먼저 했다**
  (설계 §1 의 기다리기 11.1% 와 같은 부류의 수). 학습된 정책은 아니다.
- Deep Researcher Agent "Zero-Cost Monitoring"(arXiv:2604.05854, 폴링 대비 10–20×) [출처:조각] · Claude Code Monitor 도구 해설 블로그 [출처:조각].

**사람에게 묻기 · 기권**
- Act or Escalate?(2604.08588) · Bayesian Self-Escalation(2608.24087) · Agentic Abstention(2606.28733) · AgentAbstain(2607.10059) ·
  Abstain and Validate(2510.03217, 프로그램 수리) · Look Before You Leap: Pre-Action Verification(2609.11957, 값싼 결정론적 검사로 행동 승인/거절) ·
  Cascaded LMs for Human-AI Decision-Making(2506.11887) [출처:조각] — 비용 문턱으로 자동/에스컬레이션을 고르는 틀. 우리의 μ ≫ λ 와 같은 구조.

## 아직 못 본 곳

- **IWLCS 논문집(GECCO Companion 2023–2026)과 ERBML 2024–2026 개관(doi:10.1145/3795101.3814713)의 본문.** ACM DL · dblp · sigevo 가 막혔다.
  XCS 가 LLM 호출 · 도구 선택 · 에이전트 행동 선택에 쓰였다면 여기 먼저 나올 것이다. 이번 결론의 가장 큰 구멍이다.
- **Evolutionary Intelligence XCS 30주년 특집의 실린 논문 목록**(Springer 차단). 20주년 특집(Vol.8 No.2–3, 2015)은 제목만 봤다.
- Springer LNCS 의 Organic Computing · ARCS · EvoStar(EvoApps) 쪽 LCS 응용 — 검색 조각에서만 스쳤다.
- 일본(Nakata · Shiraishi 계열) · 독일(Stein · Heider 계열) 연구실 최근 원고 — 저자 이름 검색에서 LLM 결합은 조각에 안 보였지만, 원고 목록은 못 열었다.
- When2Ask 의 **인용 논문 목록**(semanticscholar 차단) — 후속 중 규칙 기반 · 진화 정책을 쓴 것이 있는지가 가장 직접적인 반증 경로다.
- AgenticCache · AgentRR 의 본문: 캐시 적중 판정이 상태 해시인지 · 검증이 결정론적인지 · 학습된 선택기가 있는지.
- 중국어 · 일본어 문헌, 특허(USPTO 조각에 "LLM 으로 규칙 생성" 류만 보였다).
- `선행조사.md` 에서 이어지는 빈 곳(의미 캐시 원문 · 추측 실행 · 공급자 비용 보고)은 이번에도 조각 수준만 늘었다.

**이 아이디어를 새롭지 않게 만들 수 있는 남은 가능성**
1. IWLCS 2024–2026 에 "XCS 로 LLM 질의/도구 선택" 논문이 있다 — 개관 논문 하나로 확인 가능하다.
2. When2Ask 후속 중 행동을 '재사용 · 매크로 · 사람' 으로 넓힌 것이 있다(인용 목록).
3. 코딩 에이전트 하네스(Codex · Claude Code · OpenHands)가 이미 "상태 불변이면 모형 미호출" 을 규칙으로 넣었다 — codex #35259 가 그것을 *요청*했으니
   이후 반영됐을 수 있다. 그러면 P1 · P3 은 공학이지 기여가 아니고, 남는 것은 XCS 로 *배운다* 는 점뿐이다.
4. "학습된 분류기로 캐시 적중을 판정" 하는 의미 캐시(Verified Semantic Caching 2602.13165 등)가 상태 키 + 검증을 이미 한다.

## 질의 목록

1. learning classifier system XCS large language model agent
2. IWLCS GECCO 2025 learning classifier systems LLM
3. Evolutionary Intelligence special issue XCS 30 years anniversary
4. learning classifier system tool selection agent decide when to call LLM
5. "learning classifier system" "large language model" rule
6. When2Ask follow-up learned mediator when to query LLM cost reinforcement learning 2024 2025
7. XCS classifier system rule-based policy LLM agent orchestration evolutionary rule-based machine learning 2025
8. semantic cache LLM agent state key environment state hash reuse tool results verification
9. learning classifier system combined with large language model GECCO 2026
10. LLM-guided learning classifier system rule discovery LLM as mutation operator XCS
11. agent workflow record and replay verification fallback to LLM on mismatch deterministic replay
12. learned router per step skip LLM call agent "no LLM" lightweight policy cost token budget agentic
13. "XCS" OR "XCSF" classifier system ChatGPT OR GPT OR "language model" 2024 2025 2026 paper
14. "learning classifier systems" "LLM" Urbanowicz OR Stein OR Nakata OR Heider 2025
15. organic computing learning classifier system self-adaptive system LLM hybrid rule-based fallback
16. LLM agent "abstain" escalate to human learned policy verifier cost penalty wrong automatic action coding agent
17. TVCache stateful tool-value cache LLM agents sandbox state
18. AgentRR record and replay LLM agents check function experience replay
19. event-driven wait instead of polling LLM agent background process token cost
20. "classifier system" LLM agents interpretable rules gating hybrid symbolic "when to invoke" language model
21. XCS learning classifier system test case prioritization continuous integration Rosenbauer Stein
22. agentic plan caching follow-up 2026 workflow reuse verification coding agent cache hit fallback LLM
23. LLM initialized population learning classifier system OR "LLM-generated rules" XCS reinforcement learning agent hybrid 2025 2026
24. AgenticCache cache-driven asynchronous planning embodied agents plan transitions

원문 읽기 시도(막힘): dl.acm.org/doi/10.1145/3795101.3814713 · fg-oc.gi.de/mitteilung/cfp-si-xcs30 · rlj.cs.umass.edu(When2Ask PDF) ·
arxiv.org/abs/2306.03604 · gecco-2025/2026.sigevo.org 워크숍 · link.springer.com/journal/12065 · mdpi.com/2504-4990/8/2/30.
읽음: github.com/pranit-p/memory-reuse · github.com/fwyc0573/TVCache · github.com/openai/codex/issues/35259.
