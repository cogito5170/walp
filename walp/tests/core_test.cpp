// 코어 시험 — 명세 §6C 고장 주입 표 · v0.2 §7C · 해석 상태 · 재시작 원자성 · 재현성 · 힙 없음.
// 기대 반응은 시험 전에 정해 둔다(여기 적힌 것이 그 기대다). 하나라도 어긋나면 exit 1.
#include <cstdio>
#include <cmath>
#include <cstring>
#include <memory>
#include <string>
#include <unistd.h>

#include "../tools/harness.hpp"

using namespace walp;
using namespace walpeval;
using walpsim::World;

static int g_fail = 0;
#define CHECK(cond, name)                                                  \
    do {                                                                   \
        if (cond) std::printf("OK   %s\n", name);                           \
        else { std::printf("FAIL %s  (%s:%d)\n", name, __FILE__, __LINE__); ++g_fail; } \
    } while (0)

static KnowledgeBase* load() {
    static KnowledgeBase kb;
    static bool done = false;
    if (!done) {
        std::memset(&kb, 0, sizeof kb);
        std::vector<std::string> err;
        load_kb(std::string(WALP_SRC_DIR) + "/data/lexicon.csv", kb, err, false);
        if (!err.empty()) std::printf("사전 오류 %zu개: %s\n", err.size(), err[0].c_str());
        done = true;
    }
    return &kb;
}

static ScenarioConfig scen(std::uint32_t seed) {
    ScenarioConfig s;
    s.seed = seed;
    s.target_type = kCard;
    s.color = kBlue;
    s.brand = kVisa;
    s.family = walpsim::F_OFFICE;
    return s;
}

// 한 판을 손으로 돌리는 틀(고장을 스텝 중간에 넣으려고)
struct Rig {
    ScenarioConfig sc;
    World world;
    PolicyParams p = default_params();
    StateEstimator est{2};
    std::unique_ptr<Deliberator> del;
    SafetySupervisor safety;
    std::vector<DecisionRecord> recs;
    std::unique_ptr<Executive> ex;
    static void sink(void* c, const DecisionRecord& r) { static_cast<Rig*>(c)->recs.push_back(r); }
    explicit Rig(const ScenarioConfig& s, bool safety_on = true)
        : sc(s), world(s, 0), del(new Deliberator(ExploreMode::Fsm, &p)), safety(SafetyLimits{}, safety_on) {
        ExecutiveConfig cfg;
        ex.reset(new Executive(world, est, nullptr, *del, safety, cfg, DecisionSink{sink, this}));
    }
};

static bool is_move(std::uint16_t a) { return a >= kMoveN && a <= kMoveW; }

int main() {
    KnowledgeBase* kb = load();
    CHECK(kb->n > 150, "개념 사전 적재(150 항목 이상)");

    // ---------------------------------------------------------------- 해석 상태(v0.2 §3.3)
    {
        CommandParser p(kb);
        auto st = [&](const char* t) { return p.parse_detailed(t).interp; };
        CHECK(st("find the blue visa card") == Interp::Known, "KNOWN: 기준어만");
        CHECK(st("Locate the azure drinking cup") == Interp::NovelComposite, "NOVEL_COMPOSITE: 유사어+수식어+하위어");
        CHECK(st("find the container") == Interp::Ambiguous, "AMBIGUOUS: 상위어(컵|상자)");
        CHECK(p.parse_detailed("find the container").n_cand == 2, "AMBIGUOUS 는 후보 둘을 보인다");
        CHECK(st("find a visa container") == Interp::Conflict, "CONFLICT: 브랜드는 카드뿐인데 상위어에 카드가 없다");
        CHECK(st("find a yellow cup") == Interp::Unknown, "UNKNOWN: 사전에 없는 색 — 추측하지 않는다");
        CHECK(st("bring me a red cup") == Interp::OutOfScope, "OUT_OF_SCOPE: 다른 동사");
        CHECK(st("find a card that is not blue") == Interp::OutOfScope, "부정은 거부");
        CHECK(st("find it") == Interp::Ambiguous, "지시 대상 없음 → 되묻기");
        const ParseResult r = p.parse_detailed("find a teal mug");
        CHECK(r.interp == Interp::Ambiguous && r.n_cand == 2, "teal(파랑|초록) × 머그 → 후보 둘");
        CommandParser norel(kb, ParserOptions{true, false, 75});
        CHECK(norel.parse_detailed("Locate the azure drinking cup").interp == Interp::Unknown, "ablation: 관계를 끄면 azure 는 UNKNOWN");
    }
    // ---------------------------------------------------------------- v0.4 구절 틀(A)
    // 틀 창 안의 모르는 연결어만 넘어가고, 뜻을 뒤집을 수 있는 자리는 전부 거부로 남는지 — 적대 문장을 먼저 적었다.
    {
        CommandParser p(kb);
        char canon[256];
        auto got = [&](const char* t) {
            const ParseResult r = p.parse_detailed(t);
            if (r.status != Status::Ok) return std::string("NO:") + r.reason;
            goal_to_canon(r.cand[0], canon, sizeof canon);
            return std::string(canon);
        };
        struct { const char* t; const char* want; } acc[] = {
            {"find the red key and if something's blocking you, replan", "OK type=key color=red blocked=replan"},
            {"find the card. stay away from the desk. you have 120 steps.", "OK type=card avoid=desk deadline=120"},
            {"Find the red visa card but stay away from the counter", "OK type=card color=red brand=visa avoid=counter"},
            {"find a red key, double-check if unsure", "OK type=key color=red uncertain=observe"},
            {"검은 텀블러를 찾아 주세요, 바닥 쪽은 피해 주시고요", "OK type=cup color=black avoid=floor"},
            {"초록 컵 찾아봐 막혀 있으면 다시 계획하고", "OK type=cup color=green blocked=replan"},
        };
        for (auto& a : acc) CHECK(got(a.t) == a.want, (std::string("구절 틀 받음: ") + a.t).c_str());
        const char* rej[] = {
            "find the red key unless blocked replan", "find the red key in more than 50 steps",
            "find the red key, if blocked don't replan", "find the red key within 50 steps from the lamp",
            "find a key within 50 widgets", "빨간 열쇠 찾아 막히면 절대 다시 계획하지 마",
            "find the red key if unsure never look again", "find the red key avoid the desk lamp",
            "find the red key, if blocked replan, otherwise stop", "find the red key if unsure look away",
            "find the red key in at least 40 steps", "빨간 열쇠 50걸음 이상 걸려도 찾아", "find the blue card after 30 steps",
        };
        for (const char* t : rej) CHECK(got(t).rfind("NO:", 0) == 0, (std::string("구절 틀이 뜻을 뒤집지 않는다(거부): ") + t).c_str());
        ParserOptions off; off.use_constr = false;
        CommandParser p0(kb, off);
        CHECK(p0.parse_detailed("find the card. stay away from the desk. you have 120 steps.").status == Status::Unsupported,
              "ablation: 틀을 끄면 모르는 연결어(have)에서 거부(v0.3 규칙)");
        CHECK(p.parse_detailed("find the card. stay away from the desk. you have 120 steps.").tolerated == 1, "넘어간 말의 수를 센다");
    }
    // 사전 갱신 충돌 검사
    {
        KnowledgeBase k2 = *kb;
        CHECK(kb_add(k2, "안", KB_COLOR, REL_SYN, 90, 1u << kBlue) == KbAdd::Conflict, "문법어('안')를 색으로 가르치면 충돌");
        CHECK(kb_add(k2, "blue", KB_OBJECT, REL_SYN, 90, 1u << kCup) == KbAdd::Conflict, "기존 낱말을 다른 뜻으로 → 충돌");
        CHECK(kb_add(k2, "blue", KB_COLOR, REL_CANON, 100, 1u << kBlue) == KbAdd::Duplicate, "같은 뜻 재등록 → 중복");
        CHECK(kb_add(k2, "cerulean", KB_COLOR, REL_NEAR, 85, 1u << kBlue) == KbAdd::Added, "새 낱말 → 추가");
        CommandParser p2(&k2);
        CHECK(p2.parse_detailed("find a cerulean key").status == Status::Ok, "추가한 지식이 바로 해석에 쓰인다");
    }
    // 목표 관리
    {
        World w(scen(11), 0);
        MapHint m{};
        w.map_hint(m);
        GoalManager gm;
        std::uint16_t rs;
        GoalSpec g = goal_for(scen(11));
        CHECK(gm.validate(g, m, rs) == Status::Ok, "정상 목표는 받는다");
        GoalSpec g2 = g; g2.target_type = kKey;
        CHECK(gm.validate(g2, m, rs) != Status::Ok, "열쇠에 브랜드 → 임무 시작 금지");
        GoalSpec g3 = g; g3.constraints |= 1u << m.zone[cell(m.home_x, m.home_y, m.w)];
        CHECK(gm.validate(g3, m, rs) != Status::Ok, "집이 피할 구역 → 임무 시작 금지");
    }

    // ---------------------------------------------------------------- 고장 주입(명세 §6C)
    // 1) 센서 누락: 오래된 관측을 현재로 오인하지 않는다 — 그동안 이동이 실행되지 않는다
    {
        Rig R(scen(21));
        R.ex->begin(goal_for(R.sc));
        for (int i = 0; i < 3; ++i) R.ex->step();
        const std::size_t mark = R.recs.size();
        R.world.force_dropout_next(6);
        for (int i = 0; i < 6; ++i) R.ex->step();
        bool moved = false, stale_rules = true;
        for (std::size_t i = mark; i < R.recs.size(); ++i) {
            if (is_move(R.recs[i].action_type) && R.recs[i].authorized) moved = true;
            if (R.recs[i].rule_id != R_OBSERVE_STALE && R.recs[i].rule_id != R_HOLD_STALE) stale_rules = false;
        }
        CHECK(!moved, "센서 누락 중 이동 없음");
        CHECK(stale_rules, "센서 누락 중 결정은 재관측/보류뿐");
    }
    // 2) 지연 관측(3틱 묵은 것): 현재로 쓰지 않는다
    {
        Rig R(scen(22));
        R.ex->begin(goal_for(R.sc));
        for (int i = 0; i < 6; ++i) R.ex->step();
        const std::uint8_t x0 = R.ex->belief().x, y0 = R.ex->belief().y;
        R.world.force_latency_next(1);
        const std::size_t mark = R.recs.size();
        R.ex->step();
        CHECK(R.ex->belief().stale, "지연 관측 → stale 표시");
        CHECK(R.ex->belief().x == x0 && R.ex->belief().y == y0, "지연 관측으로 자세를 갱신하지 않는다");
        CHECK(!(is_move(R.recs[mark].action_type) && R.recs[mark].authorized), "지연 관측 스텝에 이동 없음");
    }
    // 2b) 순서 역전 관측: 나이 창(2틱) 안이어도 이미 받은 것보다 옛것이면 현재로 쓰지 않는다.
    //     시뮬은 지연을 늘 3틱으로만 넣어서 이 자리는 위 2) 가 못 밟는다 — 추정기를 직접 부른다.
    {
        StateEstimator est{2};
        MapHint m{};
        m.w = 5; m.h = 5; m.home_x = 0; m.home_y = 0;
        BeliefState b;
        est.init(b, m);
        Observation o{};
        o.valid = true;
        o.obs_id = 1; o.tick = 10; o.x = 2; o.y = 2; o.battery = 90;
        est.set_now(b, 10);
        CHECK(est.update(o, b) == Status::Ok && !b.stale, "제때 온 관측은 받는다");
        Observation old = o;
        old.obs_id = 2; old.tick = 9; old.x = 1; old.y = 1; old.battery = 95;
        est.set_now(b, 11);
        CHECK(est.update(old, b) == Status::NoEvidence && b.stale, "순서 역전(창 안) → stale");
        CHECK(b.x == 2 && b.y == 2 && b.battery == 90 && b.last_fresh_tick == 10, "순서 역전 관측으로 상태를 되돌리지 않는다");
        Observation same = o;
        same.obs_id = 3; same.x = 3;
        CHECK(est.update(same, b) == Status::Ok && b.x == 3, "같은 시각의 관측은 받는다(대조)");
    }
    // 3) 모순 관측: 신뢰도를 재평가하고 판단을 보류 — 잘못 확정하지 않는다
    {
        int declares_wrong = 0, runs = 0;
        for (std::uint32_t sd = 30; sd < 40; ++sd) {
            ScenarioConfig s = scen(sd);
            s.noise = 0.0;
            Rig R(s);
            R.world.force_contradiction(true);
            R.ex->begin(goal_for(s));
            StepResult r = StepResult::Continue;
            for (int i = 0; i < 400 && r == StepResult::Continue; ++i) r = R.ex->step();
            ++runs;
            if (r == StepResult::Declared && !R.world.is_target(R.ex->declared_x(), R.ex->declared_y())) ++declares_wrong;
        }
        CHECK(runs == 10 && declares_wrong == 0, "모순 관측이 매 프레임 붙어도 엉뚱한 것을 확정하지 않는다(10판)");
    }
    // 4) 검색 결과 없음: 빈 결과로 균등 사전확률, 근거 없는 확정은 허가되지 않는다
    {
        auto cb = std::make_unique<CaseBase>();
        std::memset(cb.get(), 0, sizeof(CaseBase));
        cb->n = 1;
        cb->c[0] = CaseRecord{1, kGoalFindObject, 99, kDesk, 1, 1, 10, 1, kCard, 0};
        CaseRetriever rt(cb.get());
        BeliefState b{};
        b.env_type = walpsim::F_OFFICE;
        RetrievedCases out{};
        const Status s = rt.retrieve(goal_for(scen(1)), b, out);
        CHECK(s == Status::NoEvidence && !out.used && out.n == 0, "맞는 사례 없음 → NoEvidence, 균등");
        SafetySupervisor sup;
        BeliefState bb{};
        bb.w = 20; bb.h = 16;
        SafetyState ss{&bb, 0, 1};
        std::uint16_t rs;
        CHECK(!sup.authorize(ActionRequest{kDeclare, 5, 5, 1}, ss, rs) && rs == RS_NO_EVIDENCE, "근거(가설) 없는 확정은 거부");
    }
    // 5) 계획 시간 초과: 기한 내 실패를 돌려주고 보류(안전층이 제어) — 이동하지 않는다
    {
        ScenarioConfig s = scen(51);
        Arm arm{"t", ExploreMode::Search, false, L_NONE, true, true};
        RunOpts o;
        o.force_plan_timeout = true;
        o.keep_decisions = true;
        const EpisodeResult r = run_episode(s, goal_for(s), arm, default_params(), nullptr, o);
        bool moved = false, timeout_hold = false;
        for (const auto& d : r.decisions) {
            if (is_move(d.action_type) && d.authorized && d.rule_id == R_EXPLORE_SEARCH) moved = true;
            if (d.rule_id == R_HOLD_PLAN_TIMEOUT && d.action_type == kHold) timeout_hold = true;
        }
        CHECK(timeout_hold && !moved, "계획 시간 초과 → 보류, 탐사 이동 없음");
        CHECK(r.outcome != O_SUCCESS, "계획 없이 성공으로 기록하지 않는다");
    }
    // 6) 액추에이터 실패: 성공으로 기록하지 않고 고장 상태로 넘어간다
    {
        Rig R(scen(61));
        R.world.force_act_fail(true);
        R.ex->begin(goal_for(R.sc));
        StepResult r = StepResult::Continue;
        for (int i = 0; i < 50 && r == StepResult::Continue; ++i) r = R.ex->step();
        CHECK(R.ex->belief().actuator_fault, "이동 실패 반복 → 액추에이터 고장 판정");
        CHECK(r == StepResult::Aborted && R.ex->episode().outcome == O_ABORT_FAULT, "고장 → 중단(ABORT_FAULT)");
    }
    // 7) 잘못된 정책 갱신: 검증 실패면 기존 승인 버전을 유지
    {
        PolicyStore st;
        const std::uint32_t v1 = st.current_id();
        PolicyParams bad = default_params();
        bad.k_confirm = 0;
        CHECK(!params_invariants_ok(bad, nullptr), "불변식 위반 후보(k=0) 탐지");
        PolicyParams locked = default_params();
        locked.rule_action[RC_BATTERY_LOW] = RA_HOLD;
        CHECK(!params_invariants_ok(locked, nullptr), "잠긴 안전 규칙(배터리→귀환) 변경 탐지");
        PolicyParams typed = default_params();
        typed.rule_action[RC_BLOCKED] = RA_DECLARE;
        CHECK(!params_invariants_ok(typed, nullptr), "규칙 타입 위반(막힘→확정) 탐지");
        // v0.3 반사실 진단의 이웃: 전부 불변식 통과 · 전부 현재와 다름 · 잠긴 규칙 그대로 · 경계에서도 넘지 않음
        {
            RuleCandidate nb[kMaxNeighbors];
            PolicyParams edge = default_params();
            edge.k_confirm = 8; edge.theta_pct = 100; edge.blocked_wait = 20;   // 위로 못 가는 가장자리
            bool all_ok = true, all_diff = true, locked_same = true;
            int total = 0;
            for (const PolicyParams& cur : {default_params(), edge}) {
                const int n = policy_neighbors(cur, nb, kMaxNeighbors);
                total += n;
                for (int i = 0; i < n; ++i) {
                    all_ok = all_ok && params_invariants_ok(nb[i].params, nullptr);
                    const PolicyParams& q = nb[i].params;
                    const bool diff = std::memcmp(q.rule_action, cur.rule_action, sizeof cur.rule_action) != 0 ||
                        q.k_confirm != cur.k_confirm || q.theta_pct != cur.theta_pct || q.observe_max != cur.observe_max ||
                        q.stale_observe_max != cur.stale_observe_max || q.lambda_t != cur.lambda_t || q.lambda_g != cur.lambda_g ||
                        q.reserve_margin != cur.reserve_margin || q.prior_weight_pct != cur.prior_weight_pct ||
                        q.blocked_wait != cur.blocked_wait || q.blocker_ttl != cur.blocker_ttl;
                    all_diff = all_diff && diff;
                    for (int c = 0; c < RC_COUNT; ++c)
                        if (kRuleLocked[c]) locked_same = locked_same && nb[i].params.rule_action[c] == cur.rule_action[c];
                }
            }
            CHECK(total >= 30 && all_ok, "한 걸음 이웃: 전부 불변식 통과(가장자리 포함)");
            CHECK(all_diff, "한 걸음 이웃: 현재와 같은 후보 없음");
            CHECK(locked_same, "한 걸음 이웃: 잠긴 안전 규칙을 안 건드린다");
        }
        // 배율 이웃(v3): 불변식 통과 · 변화 코드가 배율마다 갈린다(+32·log2) · 규칙 바꾸기는 배율 1 에만 · 대기 +16 이 있다
        {
            RuleCandidate a[kMaxNeighbors], b[kMaxNeighbors];
            PolicyParams hold = default_params();
            hold.rule_action[RC_BLOCKED] = RA_HOLD;
            const int na = policy_neighbors(hold, a, kMaxNeighbors, 1), nb4 = policy_neighbors(hold, b, kMaxNeighbors, 4);
            bool ok4 = nb4 > 0, codes = true, has16 = false, no_rule = true;
            for (int i = 0; i < nb4; ++i) {
                ok4 = ok4 && params_invariants_ok(b[i].params, nullptr);
                codes = codes && b[i].change_code >= 64 && b[i].change_code < 96;
                has16 = has16 || (b[i].params.blocked_wait == hold.blocked_wait + 16);
                for (int c = 0; c < RC_COUNT; ++c) no_rule = no_rule && b[i].params.rule_action[c] == hold.rule_action[c];
            }
            bool codes1 = na > 0;
            for (int i = 0; i < na; ++i) codes1 = codes1 && a[i].change_code < 32;
            CHECK(ok4 && codes && codes1 && has16 && no_rule, "배율 이웃: 불변식 통과 · 코드 갈림 · 규칙 안 건드림 · 대기 +16 있음");
        }
        // 부호 검정 손계산 대조: P(X>=10|n=10)=1/1024, P(X>=5|n=10)=638/1024, 표본 없으면 1
        CHECK(std::fabs(sign_test_p(10, 0) - 1.0 / 1024) < 1e-9 && std::fabs(sign_test_p(5, 5) - 638.0 / 1024) < 1e-9 &&
              sign_test_p(0, 0) == 1.0, "부호 검정 꼬리확률 = 손계산");
        std::vector<ScenarioConfig> suite;
        for (std::uint32_t i = 0; i < 4; ++i) suite.push_back(scen(70 + i));
        Arm arm{"t", ExploreMode::Fsm, false, L_VERIFIED, true, true};
        PolicyValidator val(suite, suite, arm);
        const ValidationResult vr = val.validate(RuleCandidate{bad, 0, 0}, st.current(), nullptr);
        CHECK(!vr.approved && vr.reason.rfind("static:", 0) == 0, "검증기가 정적 검사에서 거부");
        st.submit(RuleCandidate{bad, 0, 0}, false);
        CHECK(st.current_id() == v1, "거부된 후보는 실행 정책을 안 바꾼다");
        PolicyParams good = default_params();
        good.k_confirm = 4;
        const std::uint32_t v2 = st.submit(RuleCandidate{good, 0, 0}, true);
        CHECK(st.current_id() == v2 && st.rollback(v1) && st.current_id() == v1, "승인 버전으로 롤백");
        CHECK(!st.rollback(v1 + 1), "거부된 버전으로는 롤백 못 한다");
        {   // 승인됐던 판이라도 지금의 불변식을 어기면 되돌리지 않는다(롤백은 검증기를 안 거치는 길이다)
            PolicyStore st2;
            PolicyParams badp = default_params();
            badp.k_confirm = 0;
            const std::uint32_t vb = st2.submit(RuleCandidate{badp, 0, 0}, true);
            const std::uint32_t vg = st2.submit(RuleCandidate{default_params(), 0, 0}, true);
            CHECK(st2.current_id() == vg && !st2.rollback(vb) && st2.current_id() == vg, "불변식을 어기는 판으로는 롤백 못 한다");
        }
    }
    // 8) 정책 갱신 도중 재시작(v0.2 §7C): 옛 승인판이 남는다
    {
        const std::string path = std::string("/tmp/walp_store_test_") + std::to_string(getpid()) + ".bin";
        PolicyStore st;
        PolicyParams good = default_params();
        good.k_confirm = 4;
        CHECK(save_store_atomic(st, path), "v1 저장");
        st.submit(RuleCandidate{good, 0, 0}, true);
        CHECK(!save_store_atomic(st, path, true), "v2 를 임시 파일까지만 쓰고 '죽음'");
        PolicyStore back;
        CHECK(load_store(back, path) && back.current_id() == 1, "재시작 → v1 이 현재(임시 파일 무시)");
        save_store_atomic(st, path, true, 100);   // 쓰다 만 임시 파일
        PolicyStore back2;
        CHECK(load_store(back2, path) && back2.current_id() == 1, "쓰다 만 임시 파일 → 여전히 v1");
        // 본 파일이 깨지면(CRC) 받지 않는다 — 부르는 쪽은 기본 정책으로 남는다
        std::FILE* f = std::fopen(path.c_str(), "r+b");
        std::fseek(f, 40, SEEK_SET);
        std::fputc(0x5A, f);
        std::fclose(f);
        PolicyStore back3;
        CHECK(!load_store(back3, path) && back3.current_id() == 1, "CRC 깨진 파일은 거부, 저장소 불변");
        CHECK(save_store_atomic(st, path), "정상 저장");
        PolicyStore back4;
        CHECK(load_store(back4, path) && back4.current_id() == st.current_id() && back4.current().k_confirm == 4,
              "정상 저장 → 재시작 뒤 v2");
        std::remove(path.c_str());
        std::remove((path + ".tmp").c_str());
    }
    // 9) 안전 감독기 규칙(계획과 무관하게)
    {
        BeliefState b{};
        b.w = 5; b.h = 5; b.x = 2; b.y = 2; b.home_x = 2; b.home_y = 2; b.battery = 1000; b.now = 10;
        for (int i = 0; i < 25; ++i) { b.kind[i] = kFree; b.seen_tick[i] = 10; }
        SafetySupervisor sup;
        std::uint16_t rs;
        SafetyState ss{&b, 0, 10};
        CHECK(sup.authorize(ActionRequest{kMoveE, 0, 0, 1}, ss, rs), "정상 이동 허가");
        b.kind[cell(3, 2, 5)] = kHazard;
        CHECK(!sup.authorize(ActionRequest{kMoveE, 0, 0, 1}, ss, rs) && rs == RS_HAZARD, "위험 칸 거부");
        b.kind[cell(3, 2, 5)] = kUnknown;
        CHECK(!sup.authorize(ActionRequest{kMoveE, 0, 0, 1}, ss, rs) && rs == RS_CELL_UNKNOWN, "모르는 칸 거부");
        b.kind[cell(3, 2, 5)] = kFree;
        b.seen_tick[cell(3, 2, 5)] = 3;
        CHECK(!sup.authorize(ActionRequest{kMoveE, 0, 0, 1}, ss, rs) && rs == RS_STALE_OBS, "오래 전에 본 칸 거부");
        b.seen_tick[cell(3, 2, 5)] = 10;
        b.zone[cell(3, 2, 5)] = kShelf;
        SafetyState ss2{&b, 1u << kShelf, 10};
        CHECK(!sup.authorize(ActionRequest{kMoveE, 0, 0, 1}, ss2, rs) && rs == RS_AVOID_ZONE, "피할 구역 거부");
        b.zone[cell(3, 2, 5)] = kFloor;
        b.battery = 15;
        CHECK(!sup.authorize(ActionRequest{kMoveE, 0, 0, 1}, ss, rs) && rs == RS_BATTERY_RESERVE, "귀환 에너지 부족 거부");
    }
    // 10) 계획기 결함을 넣어도 안전층이 막는다(시뮬 전용 비교)
    {
        int viol_on = 0, viol_off = 0;
        for (std::uint32_t sd = 100; sd < 112; ++sd) {
            ScenarioConfig s = scen(sd);
            s.fault.dyn_hazards = 3;
            s.fault.dropout = 0.15;
            Arm on{"on", ExploreMode::Search, false, L_NONE, true, true};
            on.planner_defect = true;
            Arm off = on;
            off.safety = false;
            RunOpts o;
            viol_on += run_episode(s, goal_for(s), on, default_params(), nullptr, o).viol.total();
            viol_off += run_episode(s, goal_for(s), off, default_params(), nullptr, o).viol.total();
        }
        std::printf("     결함 계획기 12판: 위반 안전층 있음 %d · 없음 %d\n", viol_on, viol_off);
        CHECK(viol_on == 0, "결함 계획기 + 안전층 → 위반 0 (12판 관측)");
        CHECK(viol_off > 0, "음성 대조: 안전층을 빼면 위반이 실제로 난다(시험이 비어 있지 않다)");
    }

    // 11) 에너지 불변식(2026-09-29 빈틈): 보류·관측도 에너지를 쓴다 — 집 밖에서 배터리가 0 에 닿지 않는다.
    //     RED 증거: 고치기 전 코드(8f58542)에서 같은 모양의 1800판 검사가 좌초 1건을 냈다. 드문 사건이라
    //     여기서는 위험한 파라미터(귀환 여유 0 · 10)와 험한 조건을 골라 360판을 돌린다.
    {
        int strand = 0, succ = 0, eps = 0;
        for (int pv = 0; pv < 2; ++pv) {
            PolicyParams P = default_params();
            P.reserve_margin = pv ? 0 : 10;
            for (int mode = 0; mode < 2; ++mode)
                for (std::uint32_t sd = 8000; sd < 8090; ++sd) {
                    ScenarioConfig s = scen(sd);
                    s.fault.act_fail = 0.03; s.fault.act_silent = 0.02; s.fault.dyn_blockers = 3; s.fault.dyn_hazards = 2;
                    if (sd % 2) { s.fault.door_blockers = 3; s.fault.door_move = 0.15; }
                    Arm a{"e", mode ? ExploreMode::Search : ExploreMode::Fsm, false, L_NONE, true, true};
                    RunOpts o;
                    const EpisodeResult r = run_episode(s, goal_for(s), a, P, nullptr, o);
                    strand += r.viol.stranded; succ += r.outcome == O_SUCCESS; ++eps;
                }
        }
        std::printf("     에너지 불변식 %d판: 좌초 %d · 성공 %d\n", eps, strand, succ);
        CHECK(strand == 0, "귀환 여유를 0·10 으로 낮춘 정책도 집 밖에서 배터리 0 에 닿지 않는다(360판 관측)");
        // 안전층 단위: 집 밖에서 보류·관측도 하한 아래면 거부, 넘겨받은 행동은 귀환이 불가능하면 제자리 정지
        BeliefState b{};
        b.w = 8; b.h = 1; b.x = 5; b.y = 0; b.home_x = 0; b.home_y = 0; b.now = 10;
        for (int i = 0; i < 8; ++i) { b.kind[i] = kFree; b.seen_tick[i] = 10; }
        SafetySupervisor sup;
        std::uint16_t rs;
        SafetyState ss{&b, 0, 10};
        b.battery = 50 + 20 + 40 + 1;   // 집까지 5칸(50) + 하드 여유 20 + 대기 여유 40
        CHECK(sup.authorize(ActionRequest{kHold, 0, 0, 1}, ss, rs), "여유가 넉넉하면 보류 허가");
        b.battery = 100;
        CHECK(!sup.authorize(ActionRequest{kHold, 0, 0, 1}, ss, rs) && rs == RS_BATTERY_RESERVE, "여유 아래면 보류도 거부");
        CHECK(sup.fallback(ss).action_type == kMoveW, "넘겨받은 행동 = 집 쪽 한 걸음");
        b.battery = 40;   // 5칸 귀환에 50 이 필요한데 40 — 걷다가 좌초한다
        CHECK(sup.fallback(ss).action_type == kAbort, "귀환이 에너지로 불가능하면 걷지 않고 제자리 안전 정지");
    }

    // ---------------------------------------------------------------- 재현성 · 힙
    {
        ScenarioConfig s = scen(200);
        s.fault.dropout = 0.1;
        s.fault.dyn_blockers = 2;
        Arm arm{"t", ExploreMode::Search, false, L_NONE, true, true};
        RunOpts o;
        o.record = true;
        const EpisodeResult r = run_episode(s, goal_for(s), arm, default_params(), nullptr, o);
        int fd = 0;
        CHECK(replay_matches(r, goal_for(s), arm, default_params(), nullptr, 1, &fd), "기록 재생 → 결정열 동일");
        PolicyParams alt = default_params();
        alt.lambda_t = 300;
        alt.k_confirm = 5;
        CHECK(!replay_matches(r, goal_for(s), arm, alt, nullptr, 1, &fd), "음성 대조: 정책을 바꾸면 결정열이 갈라진다");
        CHECK(r.heap_allocs_in_step == 0, "실행 스텝 안에서 힙 할당 0");
        CHECK(r.trace_ok == r.trace_total && r.trace_total > 0, "모든 결정이 규칙·증거·버전으로 되짚인다");
    }

    std::printf("\n%s — 실패 %d\n", g_fail ? "FAIL" : "PASS", g_fail);
    return g_fail ? 1 : 0;
}
