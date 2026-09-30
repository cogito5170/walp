## run_shell(command:str)
이 저장소(REPO_DIR)에서 임의의 셸 명령을 실행한다. admin/public 채널 둘 다 쓸 수
있다 -- public은 화이트리스트가 없어 위험을 사용자가 감수하고 명시적으로 요청한 것이다.
결과는 stdout/stderr을 그대로 반환한다.

## run_probes(commands:str, minutes:int=2)
**탐침 여러 개를 한꺼번에** 돌린다 -- 줄마다 명령 하나(최대 12줄), 저장소(또는 계획판)에서 나란히
실행해 명령마다 (끝값 · 걸린초 · 출력 꼬리) 표로 돌려준다. 가설 하나를 확인하려고 명령을 하나씩 돌리지
말고, 갈릴 만한 탐침 3~6개를 한 번에 던져라: 예) "python3 tests/test_x.py" · "git log -3 --oneline -- 파일" ·
"grep -n 이름 파일" · "python3 -c 'import 모듈'". 각 명령은 toolgate 를 지난다. minutes 는 명령마다의 상한.

## run_experiment(command:str, minutes:int=3)
실험·검증용 명령을 깨끗한 격리 판에서 돌린다: HEAD 를 임시 워크트리로 꺼내 그 안에서
실행하므로 저장소 작업 트리에 아무 흔적이 안 남고, 비밀 환경변수도 지운 채 돈다.
코드 실험, 테스트 실행, "고치면 어떻게 되나" 확인은 run_shell 이 아니라 이걸 쓰라 --
run_shell 은 진짜 저장소에서 돌아 실수가 그대로 남는다. command 는 bash -lc 로,
워크트리 루트에서 실행된다. minutes 는 벽시계 제한(1~10분).

## read_file(path:str, start:int=1, lines:int=400)
저장소 파일을 줄 번호를 붙여 읽는다. edit_file 의 old 를 정확히 짚으려면 실제 글자
(들여쓰기·줄바꿈 포함)를 봐야 하므로, 고치기 전에 반드시 이걸로 그 자리를 봐라.
path 는 저장소 기준 상대경로. start/lines 로 잘라 읽는다(한 번에 최대 400줄).
.env 는 못 읽는다(비밀값).

## edit_file(path:str, old:str, new:str)
파일의 한 자리를 정확히 고친다: old 가 파일에 **정확히 한 번** 있을 때만 new 로 바꾼다.
0번이면 거절(read_file 로 실제 글자를 보고 그대로 대라), 2번 이상이면 거절(앞뒤를 더 붙여
하나로 좁혀라), 빈 old 는 거절(그건 전체 쓰기다).
**기존 파일을 고칠 때는 run_shell 의 sed/heredoc 대신 이걸 써라** -- 전체 덮어쓰기가
drift.sh(4cd4473)와 봇 자신(1a82685)을 부순 사고의 형태다. 게이트(gates/)·판정 원장·
.env·.git 은 이 도구로 못 만진다 -- 게이트는 self_challenge 승격으로만.

## set_key(name:str, value:str)
사용자가 **채팅으로** 준 값(비밀번호 · 토큰 · 주소)을 .env 에 적는다 -- 되묻지 말고 바로.
실측 2026-09-11: 사용자가 값을 줬는데 봇이 재시작(배포)되자 잊고 다시 물었다. 대화 기억은
재시작하면 사라진다 -- .env 에 적힌 것만 남는다. name 은 대문자·숫자·밑줄(예: SMTP_APP_PASSWORD).
값은 답에 되비치지 마라. 적고 나면 막혔던 일을 바로 이어서 하라.

## codify_paper(arxiv_id:str)
논문(arXiv)의 **수식·알고리즘을 실행 가능한 코드로** 바꾼다. dig/paper 로 논문을 읽을 글자로
내린 뒤 각 수식·알고리즘을 파이썬 함수로 짓고 **sandbox 에서 돌려** 검증한다(판정은 끝값이 한다 --
네가 '됐다' 고 말하지 마라). 검증 통과한 코드만 codify/out 에 저장되고 graph 에 색인된다.
수식 하나만 코드화하려면 `python3 codify/run.py --종류 수식 --원문 '<식>' --예시 '[...]'` 를 run_shell 로.

## research(goal:str)
목표 하나를 **한 호흡에** 연구한다 -- 소개만 하고 떠넘기지 마라. 목표를 그대로 검색하지 않고
(너무 구체적이면 논문이 0건이다) **일반 방법론 질의 여럿**으로 풀어 제2의 뇌(dig/harvest)로
arXiv·GitHub·HF 를 넓게 모으고, 막히면 그 막힘을 다시 추상화해 더 넓게 모으기를 되풀이한다
(3~5 바퀴). 모은 방법론은 codify 로 코드화해 sandbox 에서 검증하고, 과정->결과를 압축해
public_agent_memory 에 결론으로 남긴다. 판정은 코드가 한다(수집 색인 수·코드화 끝값). 도메인 무관 --
목표가 구체적 작업이든 학술 질문이든 같다. 몇 분 걸릴 수 있다. 결과(색인·코드 파일·결론·메모)를 답에 붙여라.

## create_pr(title:str, body:str='')
지금 갈래를 origin 에 밀고 **PR 을 연다. 머지는 하지 않는다 -- 사람이 GitHub 에서 누른다.**
main 에서는 거절(갈래를 먼저 만들어라). 밀기는 gitsync 규칙(merge 로 따라잡기, --force 없음).
GITHUB_TOKEN 이 없으면 그렇다고 돌려준다 -- `!열쇠 GITHUB_TOKEN=<값>` 꼴로 딱 그 값만 청하라.
'커밋했다·PR 열었다' 는 이 도구가 돌려준 URL 로만 말하라 -- 해시나 번호를 지어내지 마라.

## dispatch_command(command:str)
사람의 부탁을 **실제 실행으로 옮긴다.** command 에 사람의 말을 그대로 넘겨도 되고(`"RIS 최신 논문 좀 모아줘"`),
고정 명령(`!연구 …`)을 직접 줘도 된다 -- 어느 명령인지는 저장소의 표(dispatch.고르기)가 고른다. 명령 목록을
보여 주거나 '무엇을 원하시나요' 로 끝내지 마라. 못 고르면 까닭을 돌려주니 그때 도구를 직접 불러라. 예: 수집·틈 → `!수집 틈으로`, 논문 코드화 →
`!코드화 논문 <id>`, 연구 → `!연구 <목표>`, 검사 → `!실험 게이트`/`!평가 과제`, 기억 간추리기 → `!기억 밤`,
변경 검사 → `!감사`, 경로 비용 → `!경로 요약`, 고치기 → `!고치기 <명령> :: <증상>`, 계획 → `!계획 켜기 <요청>`.
**`!목표 승

## security_audit(deep:bool=False)
**이 호스트 자신**의 보안 상태를 읽기 전용으로 점검한다 -- 열린 포트 · 파일/키 권한 · SUID ·
세계 쓰기 · 위험 계정 · 방화벽. 판정은 코드가 규칙으로 낸다(네가 '안전해 보인다' 고 말하지 마라).
deep=True 면 dig 수집 + search_memory 대조까지. **남의 기계를 공격하거나 익스플로잇을 실행하지
않는다** -- 읽기뿐이다. 사용자 노트북을 점검하려면 그 노트북에서 이 봇을 돌려야 한다.

## repair(command:str, symptom:str)
문제를 **스스로 푸는 루프**. command 는 재현 명령(끝값 0 이면 해결), symptom 은 오류 문구.
코드가 돈다: sandbox 실측 -> 제2의 뇌(dig/harvest + 색인)에서 원인 -> 수리기 제안(패치/명령)
-> 격리해서 시도 -> 실측 ... 최대 3바퀴. 해결이면 그렇다고, 아니면 해 본 것과 **사람만 할 수
있는 한 가지**를 돌려준다. 실패 이유는 public_agent_memory 에 남아 밤에 장기기억이 된다.
오류를 만나면 네가 손으로 세 번 시도하지 말고 이것을 불러라. 몇 분 걸릴 수 있다.

## send_email(to:str, subject:str, body:str, attach:str='')
메일을 보낸다 -- SMTP 접속은 여기가 한다. **네가 smtplib 코드를 짜거나 발급 절차를
설명하지 마라.** 수단(보내는 주소·앱 비밀번호)이 없으면 이 도구가 "무엇이 없고 어떻게
주는지" 를 돌려준다 -- 그 말을 사용자에게 **그대로** 전하라(선택지를 나열하지 말고).
사용자가 `!열쇠 이름=값` 으로 줬다고 하면 같은 인자로 다시 불러라 -- 바로 나간다.
.env 는 이 도구가 별칭·값의 꼴로 알아서 뒤진다 -- 네가 read_file 로 .env 를 읽지 마라.
to 에 "me" 를 주면 USER_EMAIL 로 간다. 본문에 [교수님 성함] 같은 자리표가 남아 있으면 안 보낸다
-- 네가 다 채워서 다시 불러라(실존 인물 이름을 지어 서명하지 말고 직함·위원회로).
제목 앞의 `[보고

## delegate(question:str, scope:str)
파일 여럿을 살펴야 하는 물음을 싼 탐색기에 **동시에** 던지고, 원문에 실재하는 인용만
받는다. scope 는 글롭(띄어쓰기로 여럿: "graph/*.py router/*.py"). 파일 수십 개를 네가
cat 으로 다 읽지 마라 -- 이걸로 던져서 파일:줄 인용을 받은 뒤, 필요한 자리만 read_file
로 봐라. 인용은 코드가 파일과 대조해서 지어낸 것은 버리고 퇴짜로 센다. 결과에 '퇴짜' 가
많으면 탐색기가 헛것을 봤다는 뜻이니 범위를 좁혀라.

## simulate_inspection(정책:str='pi', dt:float=0.02)
**항공기 결함검사 드론의 3D 동적 시뮬레이션 HTML 을 만든다.**

## simulate_formation(메일:str='')
**무인체계 편대(스웜)의 위협회피 동적 시뮬레이션 HTML 을 만든다.**

## ruh2_battery(무엇:str='사이클', c_rate:float=0.5, temp_c:float=25.0, precharge:bool=True, protect:bool=True)
**Ru 수소 전극 니켈–수소(Ni–H₂) 배터리 프로토타입 RuH2-P1 의 모델 분석을 글로 돌려준다.**

## ruh2_make(산출물:str='보고서', 장면:str='battery')
**RuH2-P1 산출물을 실제로 만들어 답과 함께 올린다** (설계에 쓴 도구 그대로).

## report_pdf(markdown_text:str, 제목:str='', strict:bool=True)
**기술 보고서 Markdown 을 저장소의 보고서 구성 정책(reportkit/POLICY.md)대로 PDF 로 만든다.**

## recon_rover(무엇:str='요약', 시나리오:str='')
**자율 정찰·정보수집 로버 RECON-R1 설계와 시뮬레이션 결과를 글로 돌려준다** (2026-09-29 세션, recon/).

## recon_make(산출물:str='상태', 대상:str='전부')
**RECON-R1 산출물을 실제로 만든다** (설계에 쓴 도구 그대로).

## render_space(대상:str='hongdae/F1', 시점:str='aerial,eye')
**공간(매장·실내 배치 또는 SAR 지형 세계)을 2D 평면도 + 실사 3D 렌더(three.js PBR) + 인터랙티브 HTML 로 그린다.**

## draw_circuit(code:str='', example:str='', check:bool=True)
**회로도를 그린다.** CMOS·NMOS·PMOS·저항·축전기·코일·전류원·연산증폭기 등.

## run_rtl(design:str, testbench:str, top:str='tb', seconds:int=60, waveform:bool=True, min_coverage:float=0.0)
**Compile and actually RUN Verilog/SystemVerilog** (iverilog + vvp).

## lint_rtl(design:str, strict:bool=True)
**Static-check Verilog with Verilator** (`--lint-only -Wall`).

## synth_rtl(design:str, top:str='')
**Synthesize with Yosys and count cells** -- the 'A' in PPA.

## prove_rtl(design:str, top:str='', depth:int=20, unbounded:bool=True, seconds:int=280)
**Formally PROVE a property** over all inputs (Yosys SAT / temporal induction).

## place_rtl(design:str, target_mhz:float, top:str='', chip:str='hx8k', seconds:int=500)
**Place, route and time the design on a real FPGA** (Yosys + nextpnr-ice40).

## serdes_link(loss_db:float=20.0, snr_db:float=26.0, bits:int=100000, ctle_peaking_db:float=0.0, ffe_taps:int=0, dfe_taps:int=0, tap_bits:int=0, keep_fraction:float=1.0, ideal_decision:bool=False, eye:bool=True, reflections:str='', dfe_positions:str='', sps:int=8, seed:int=0)
**Actually simulate a wireline SerDes link and measure BER** (channel/CTLE/FFE/DFE).

## quant_sweep(widths:str='2,3,4,6,8,12', loss_db:float=20.0, snr_db:float=26.0, bits:int=150000, ffe_taps:int=11, dfe_taps:int=8, sps:int=8, seed:int=7)
**BER versus tap word length** -- the quantisation trade-off curve, measured.

## adc_sweep(widths:str='4,5,6,7,8', full_scales:str='2.0,2.5,3.0,4.0', loss_db:float=25.0, snr_db:float=30.0, bits:int=300000, ffe_taps:int=11, dfe_taps:int=8, coef_bits:int=0, sps:int=8, seed:int=7)
**BER versus ADC resolution AND full scale** -- the two cannot be chosen apart.

## loss_sweep(losses:str='15,20,25,30,35', widths:str='7,8,9,10', target_ber:float=0.001, bits:int=800000, seeds:str='7,11,23,42', quantize_adc:bool=True, adc_full_scale:float=2.5, ffe_taps:int=11, dfe_taps:int=8, sps:int=8)
**How far does a given word length hold as the channel gets worse?**

## nn_equalizer(loss_db:float=25.0, snr_db:float=30.0, compression:float=1.0, bits:int=300000, window:int=10, hidden:int=16, epochs:int=12, dfe_taps:int=0, weight_bits:int=0, keep_fraction:float=1.0, reflections:str='', adc_bits:int=0, sps:int=8, seed:int=7)
**Neural-network equaliser** -- and the control that says when it is allowed to win.

## eq_area(kind:str='nn', taps:int=5, hidden:int=2, bits:int=7, frac:int=4, target_mhz:float=50.0, chip:str='hx8k', seconds:int=300)
**Area and Fmax of an equaliser, on a real device** (yosys + nextpnr, iCE40).

## ip_signoff(design:str, testbench:str, top:str='tb', min_coverage:float=80.0, target_mhz:float=0.0, chip:str='hx8k', deliverables:str='', seconds:int=300)
**Run the IP sign-off gates an IP/design house actually passes before delivery.**

## run_spice(netlist:str, checks:str='', seconds:int=90)
**Actually SIMULATE an analog circuit** with ngspice (DC / AC / transient).

## monte_carlo(netlist:str, spread:str, runs:int=30, checks:str='', seed:int=1234, seconds:int=90)
**Process variation: run the circuit N times with parameters drawn from a Gaussian.**

## concept(name:str='', level:str='', domain:str='', track:str='')
**Look up an IC design concept** — the defining equation, what it governs, and the
example you can actually run for it.

## textbook(question:str, sections:int=5, section_id:str='')
**Answer from the textbook, not from memory** — search the 170-chapter IP design
book in `edu/` and get back the exact sections that bear on the question.

## spice_example(name:str='')
**List or fetch a ready-made, verified analog netlist** for `run_spice`.

## read_image(path:str, question:str='')
**사진·스크린샷을 실제로 본다.** 첨부 파일이 그림이면 `cat` 하지 말고 이걸 써라.

## read_pdf(path:str, 모드:str='', 쪽:str='', 물음:str='')
**PDF 를 골라서 읽는다.** 큰 문서는 read_image 로 통째로 보내면 토큰이 터진다.

## search_memory(query:str)
저장된 장기 기억에서 query와 관련된 내용을 찾는다.

## save_memory(topic:str, content:str)
새로 알게 된 사실을 장기 기억에 저장한다 (git에 커밋되어 다음 대화에도 남는다).

## write_public_answer(filename:str, content:str)
공개 채널 에이전트의 답변/결과물을 파일로 남긴다. Public_agent/ 폴더 아래에만
저장되고 git에 커밋된다(push는 하지 않음, 관리자가 검토 후 push). filename은
디렉터리 없이 파일명만 지정한다 (예: answer.py, result.md).

## orchestrator_solve(problem:str)
문제 하나를 orchestrator 파이프라인(계획->실행->검증->수리 루프)으로 푼다.

## orchestrator_status(run:str='')
orchestrator 런의 진행 상황을 본다: 프로세스 생사, 노드별 검증 상태, 마지막 실패
사유, 최종 결과, 로그 끝부분. run 이 비면 가장 최근 런을 본다.

## orchestrator_resume(run:str)
죽었거나 미완으로 끝난 orchestrator 런을 이어서 돌린다. 검증된 노드는 건너뛰고
실패한 노드부터 다시 시도한다. 봇이 재배포로 재시작되면 돌던 런도 같이 죽으므로,
orchestrator_status 가 '미완이고 프로세스도 없다'고 하면 이걸 쓴다.

## orchestrator_stop(run:str='')
돌고 있는 orchestrator 런을 멈춘다(프로세스 그룹째). 산출물은 파일로 남으므로
orchestrator_resume 으로 이어서 돌릴 수 있다. run 이 비면 가장 최근 런을 멈춘다.

