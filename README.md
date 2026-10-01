# WALP — Weight-free Adaptive Language Policy

**LLM · 사전학습 가중치 · GPU 없이, 사람과 대화하며 받은 고침과 반응만으로 규칙과 행동을 길러 가는 작은 로봇 두뇌.**
과업은 하나다 — 격자 세계(방 넷)에서 물체(카드 · 열쇠 · 컵 · 상자)를 색 · 브랜드로 찾기. 새 이론이 아니다
(Soar/Rosie · CBR · BDI · Brooks 포섭 · XCS 계보의 공학적 조합, `paper/선행조사/WALP*.md`).

## 돌리기

```bash
python3 -m walp.chat                      # 대화(저장소 뿌리에서, g++·make 필요) — 한 줄에 한 말, '끝' 으로 나간다
make -C walp BUILD=/tmp/wb all test       # C++ 코어 + 시뮬 + CLI, 코어 시험
```

내 PC 에 설치하기: [`PC_실행.md`](walp/PC_실행.md). 디스코드: `!walp <말>`. MCP: `python3 walp/mcp_server.py`.

## walp-front — WALP 를 Claude · Gemini CLI 앞에 세우는 도구

잡담(인사 · 감사 · 작별 · 자기소개 · 할 수 있는 것 · 쓰는 법)은 WALP 가 10 ms 안팎에 토큰 0 으로 답하고, 나머지만
`claude -p` / `gemini -p` 로 넘긴다(사용자의 로그인 · MCP 설정을 그대로 쓴다). 학습된 체계(`walp/data/front_model.json`)가 묶여 있고,
숙고 · 기억 층을 끄므로 **C++ 코어를 빌드하지 않는다** — 맥에서 Xcode 없이 돈다. 표준 라이브러리만.

```bash
pip install git+https://github.com/cogito5170/walp
walp-front ask "안녕하세요"                         # WALP 가 답한다 (토큰 0)
walp-front ask "다음 주 회의 잡아줘" --llm gemini    # WALP 가 모른다 -> gemini 가 답한다
walp-front ask "..." --llm auto                     # gemini 먼저, 로그인 · 설치 실패면 claude
walp-front route "고마워"                           # WALP 판정만(JSON)
```

```python
from walp.llmfront import SmallTalk
SmallTalk().act("고마워")      # "thanks" -- 잡담이 아니거나 모르면 None
```

### Claude Code 에도 — 훅으로

```bash
walp-front install-hook              # ~/.claude/settings.json 의 UserPromptSubmit 훅으로 건다(다른 설정 · 훅은 그대로, .bak-walp 백업)
walp-front uninstall-hook            # 뗀다
```

Claude Code 에 보내는 말마다 WALP 가 먼저 본다. 잡담이면 **모형에 보내지 않고** WALP 의 답을 보인다. 진짜 `claude -p` 로 잰 것
(2026-10-01): "고마워요" → 245 ms · 토큰 0 · 모형 호출 0 / "1+1 은?" → 그대로 모형에(1,766 ms · 2,480 토큰).
일이 담긴 말을 WALP 가 잡담으로 잘못 막았으면 **앞에 `//` 를 붙여 다시 보내면** 그대로 간다. 슬래시 명령(`/clear` 등)은 안 막는다.
잠깐 끄려면 `WALP_FRONT_HOOK=0`. 훅이 터지거나 입력을 못 읽으면 막지 않는다(사람의 말을 잃는 쪽으로 틀리지 않게).

**알고 쓸 것.** worldplan 의 봉인 모음(사전등록, 60 문장 × 2)에서 이 층은 LLM 토큰을 **38~44%** 줄였지만, 일정 요청이 섞인 말
("고마워~ 근데 latam 을 화요일로 옮겨줘")을 잡담으로 읽어 **60 문장 중 9~10 개**를 LLM 에 안 보내고 잡담으로 답했다
(사전등록 안전 기준 5% 를 못 넘었다 — worldplan `eval/PREREG_앞단비교*.md`). WALP 는 한 말에 행위 하나만 고른다.
숙고 · 기억 층을 끈 판정은 그 120 문장에서 켠 것과 0 개 달랐다(그 밖의 말에서는 다를 수 있다).
검사: `python3 -m unittest tests.test_llmfront`.

## 다음 — 실행 정책층(설계 · V&V, 아직 안 지음)

잡담 필터에서 에이전트 루프의 **턴 경계마다 LLM 이 필요한지** 고르는 층으로. 이 세션 추적(1,454 턴 · 611M 토큰)에서 출발했다:
비용은 **턴 수 × 턴당 맥락(평균 419k)** 이고, 루틴 걸음(git · PR · 기다리기 · 검사)이 40~45% — 그러나 하위 에이전트 추적에서는
0.7% 였다(일의 종류에 달렸다). [`walp/docs/실행정책층/`](walp/docs/실행정책층/): 선행조사 · 설계 · V&V.
`python3 -m walp.trace_stats <세션.jsonl>` 로 어느 저장소의 세션이든 잰다(집계만, 글은 안 낸다).

## LLM 앞단 — 세 층

**Control**(아래 행동 버스, LLM 없음) → **Sequencing**(스스로 답할지 · 계획을 돌릴지 · "모른다" 로 위로 보낼지) →
**Deliberative**(Claude API `claude-opus-5-5`, `deliberate.py`). 모르는 것만 LLM 에게 묻고, 그 답의 **부류**를 배워 다음 진화에 쓴다.
키(`ANTHROPIC_API_KEY` 또는 `ant auth login`)가 없으면 예전처럼 사람에게 되묻는다. 설계 · 결과: `eval/PREREG_LLM앞단.md`.

## 어떻게 도나 — 행동 기반(포섭)

분류기는 **센서**로 내려가고, 행동들이 한 턴에 모두 계산한 뒤 위 층이 아래 층의 말을 억제한다(`behavior.py`).

| 층 | 행동 | 배우나 |
|---|---|---|
| L3 | 숙고 — 여러 걸음 요청을 STRIPS 로 계획해 걸음마다 시뮬(`strips.py`) | 아니 |
| L2 | 기억 — 사람이 고친 말 | 고침으로 |
| L1.5 | 배움 — 사람이 가르친 새 뜻과 그 답(행위 늘리기) | 가르침으로 |
| L1 | 되묻기 — 흔들리면 L0 을 억제하고 자연어 선택지로 묻는다 | 자기 XCS |
| L0 | 반응 — 답 틀을 직접 골라 실행 | 자기 XCS |
| 센서 | 성장망(XCS 조건 → 은닉 단위) + ESN · 글자조각 · 찾기 파서 | 진화 때 |

- 형식 없이 말한다. 되물으면 "저에 대한 거요" · "응 그거" · "둘 다 아니야" 처럼 답하면 된다(`행위 <이름>` 은 지름길로만).
- 신호(고침 · 감사 · 불만)가 20개 쌓이면 배경에서 진화한다. 교체는 **래칫** — 봉인 관문 또는 떼어 둔 사람 고침에서 이기고, 봉인에서 1%p 넘게 안 떨어질 때만.
- 대화 원장 · 배운 낱말 · 비밀 상태는 git 에 남기지 않는다(`usability.py` · `.gitignore`).

## 잰 것 (전부 사전등록 · 봉인 모음 한 번씩 — `eval/PREREG_*.md`)

| 무엇 | 결과 | 한계 |
|---|---|---|
| 행동 구조(봉인 v6) | 문장당 효용 0.85 vs 예전 사슬 0.58 | 되묻기의 **학습**은 확신 문턱 하나와 평균으로 같았다 · 되물음이 늘 풀린다고 가정 |
| 행위 늘리기(봉인 대본 v1) | 평소 말 가로챔 0% | 같은 뜻 **다른 말**은 1.7% 만 알아듣는다 — 뜻이 아니라 글자 겉을 본다 |
| LLM 앞단(봉인 요청 흐름 v1, 신탁 LLM) | LLM 호출 48.8% → 38.2%(배움) | 답 캐시가 헷갈리는 짝에 남의 답을 줘(6/43) 스스로 틀림이 8.2% → 9.8% — **캐시를 껐다.** 부류 학습만으로는 호출이 거의 안 줄었다 |
| 숙고기(봉인 계획 모음) | 92.9% | 거부해야 할 요청 6/25 에 계획을 세웠다 |
| 진화(XCS 안) | GA 가 덮기만보다 낫다(85/0) | 성장망의 은닉층은 0~3개만 자랐다 — 진화의 실용 효과는 안 보였다 |

## 문서

| 문서 | 무엇 |
|---|---|
| [`PC_실행.md`](walp/PC_실행.md) | 설치 · Gemini 대화 상대 · 무엇이 자라나 |
| [`eval/`](walp/eval/) | 사전등록(`PREREG_*.md`) · 봉인 모음 · 결과(`results/`) · held-out 이력(`HELDOUT.md`) |
| [`docs/RESULTS.md`](walp/docs/RESULTS.md) | v0.2–v0.4 의 코어 · 도구 배선 · 자기개선 측정 기록 |
| [`docs/SE_비교.md`](walp/docs/SE_비교.md) · [`docs/OPUS_비교.md`](walp/docs/OPUS_비교.md) | SE 에이전트 · Opus 와의 비교 |
| [`docs/MCP.md`](walp/docs/MCP.md) | MCP 층 결정 |
| [`docs/구조.md`](walp/docs/구조.md) | 파일별 구조 |
| [`docs/참고저장소/`](walp/docs/참고저장소/) | 인증 오픈소스 넷(Keycloak 등) 코드 분석 — WALP 에 옮길 수 있는가(2026-09-29) |

코어(`src/`)는 `-fno-exceptions -fno-rtti -Werror` 로 빌드되고 파일 · OS 를 부르지 않는다. 파일별 역할은 [`docs/구조.md`](walp/docs/구조.md) 와 각 파일 머리말.
