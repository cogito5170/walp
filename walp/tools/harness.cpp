#include "harness.hpp"

#include <algorithm>
#include <cmath>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <memory>

// ---------------------------------------------------------------- 힙 할당 계수
// 코어 한 스텝 안에서 힙을 쓰는지 잰다. 시뮬레이터 쪽 벡터는 미리 reserve 해 두므로,
// 이 계수가 0 이 아니면 코어(또는 reserve 를 빠뜨린 하니스)가 할당한 것이다.
namespace walpeval {
bool g_count_allocs = false;
std::uint64_t g_alloc_count = 0;
}  // namespace walpeval

namespace walpeval {

using walpsim::World;
using walpsim::RecordingPlatform;
using walpsim::ReplayPlatform;

const char* outcome_name(std::uint8_t o) {
    static const char* k[] = {"none", "success", "timeout", "false_declare", "stranded", "abort_safety",
                              "abort_fault", "abort_no_target"};
    return o < O_COUNT ? k[o] : "?";
}

GoalSpec goal_for(const ScenarioConfig& sc, std::uint32_t extra) {
    GoalSpec g{};
    g.goal_type = kGoalFindObject;
    g.target_type = sc.target_type;
    g.required_attributes = pack_attrs(Color(sc.color), Brand(sc.target_type == kCard ? sc.brand : 0));
    g.constraints = extra;
    g.deadline = Tick(sc.deadline);
    return g;
}

namespace {

struct Collector {
    std::vector<DecisionRecord>* out;
};
void collect(void* ctx, const DecisionRecord& r) {
    auto* c = static_cast<Collector*>(ctx);
    if (c->out->size() < c->out->capacity()) c->out->push_back(r);   // capacity 안에서만 — 스텝 안 할당 금지
}

bool record_traceable(const DecisionRecord& r, std::uint32_t version, std::uint32_t max_obs, EvidenceId max_ev,
                      const CaseBase* cb) {
    if (r.rule_id == R_NONE || r.rule_id >= R_COUNT) return false;
    if (r.policy_version != version) return false;
    if (r.reason_code >= RS_COUNT) return false;
    if (!r.authorized && r.reason_code == RS_NONE) return false;      // 거부에는 사유가 있어야
    if (r.obs_id > max_obs) return false;
    const bool needs_ev = r.rule_id == R_DECLARE_CONFIRMED || r.rule_id == R_DECLARE_FIRST_SIGHT ||
                          r.rule_id == R_OBSERVE_UNCERTAIN || r.rule_id == R_APPROACH_CANDIDATE;
    if (needs_ev && (r.evidence_id == 0 || r.evidence_id >= max_ev)) return false;
    if (r.obs_id == 0 && !(r.rule_id == R_OBSERVE_STALE || r.rule_id == R_HOLD_STALE || r.rule_id == R_ABORT_DEADLINE))
        return false;
    if (r.n_cases > 0) {
        bool found = false;
        for (int i = 0; cb && i < cb->n; ++i) if (cb->c[i].id == r.first_case) found = true;
        if (!found) return false;
    }
    return true;
}

}  // namespace

EpisodeResult run_episode(const ScenarioConfig& sc, const GoalSpec& goal, const Arm& arm, const PolicyParams& p,
                          const CaseBase* cb, const RunOpts& o) {
    EpisodeResult R;
    World world(sc, goal.constraints & kAvoidMask);
    RecordingPlatform rec(world);
    IPlatform& pf = o.record ? static_cast<IPlatform&>(rec) : static_cast<IPlatform&>(world);
    if (o.record) {
        rec.obs.reserve(sc.deadline + 16);
        rec.res.reserve(sc.deadline + 16);
        rec.ticks.reserve(sc.deadline + 16);
    }
    PolicyParams pp = p;
    pp.use_uncertainty = arm.uncertainty ? 1 : 0;
    StateEstimator est(2);
    CaseRetriever retr(cb);
    auto delib = std::make_unique<Deliberator>(arm.mode, &pp);
    delib->force_timeout(o.force_plan_timeout);
    delib->inject_defect(arm.planner_defect);
    SafetySupervisor safety(SafetyLimits{}, arm.safety);
    ExecutiveConfig cfg;
    cfg.use_retrieval = arm.retrieval && cb;
    cfg.policy_version = o.policy_version;
    std::vector<DecisionRecord> decs;
    decs.reserve(std::size_t(sc.deadline) + 16);
    Collector col{&decs};
    auto ex = std::make_unique<Executive>(pf, est, cb ? &retr : nullptr, *delib, safety, cfg, DecisionSink{collect, &col});
    if (o.time_steps) R.step_us.reserve(std::size_t(sc.deadline) + 16);

    const Status bs = ex->begin(goal);
    if (bs != Status::Ok) {
        R.outcome = O_ABORT_FAULT;
        return R;
    }
    R.n_cases = ex->cases().n;
    StepResult sr = StepResult::Continue;
    int guard = 0;
    while (sr == StepResult::Continue && guard++ < sc.deadline + 50) {
        g_alloc_count = 0;
        g_count_allocs = true;
        const auto t0 = std::chrono::steady_clock::now();
        sr = ex->step();
        const auto t1 = std::chrono::steady_clock::now();
        g_count_allocs = false;
        R.heap_allocs_in_step += g_alloc_count;
        const double us = std::chrono::duration<double, std::micro>(t1 - t0).count();
        if (us > R.max_step_us) R.max_step_us = us;
        if (o.time_steps && R.step_us.size() < R.step_us.capacity()) R.step_us.push_back(us);
    }
    EpisodeRecord er = ex->episode();
    std::uint8_t out = O_NONE;
    if (sr == StepResult::Declared) {
        const int x = ex->declared_x(), y = ex->declared_y();
        const bool hit = o.judge_by_goal ? world.satisfies(x, y, std::uint8_t(goal.target_type), attr_color(goal.required_attributes),
                                                           attr_brand(goal.required_attributes))
                                         : world.is_target(x, y);
        out = hit ? O_SUCCESS : O_FALSE_DECLARE;
    }
    else if (sr == StepResult::Deadline) out = O_TIMEOUT;
    else if (sr == StepResult::Aborted) out = er.outcome == O_NONE ? std::uint8_t(O_ABORT_NO_TARGET) : er.outcome;
    else out = O_TIMEOUT;
    if (world.violations().stranded && out != O_SUCCESS) out = O_STRANDED;
    er.outcome = out;
    R.outcome = out;
    R.rec = er;
    R.steps = ex->steps();
    R.observes = er.observes;
    R.denials = er.denials;
    R.blocked_events = ex->blocked_events();
    R.energy_used = sc.battery - world.battery();
    R.viol = world.violations();
    R.target_zone = world.target_zone();
    R.ended_home = world.at_home();
    for (const auto& d : decs) if (d.plan_nodes > R.max_nodes) R.max_nodes = d.plan_nodes;
    // 추적성: 기록만으로 규칙·증거·버전·사례를 되짚을 수 있나
    const EvidenceId max_ev = ex->belief().next_ev;
    for (const auto& d : decs) {
        ++R.trace_total;
        if (record_traceable(d, o.policy_version, 1u << 30, max_ev, cb)) ++R.trace_ok;
    }
    if (o.keep_decisions || o.record) R.decisions = decs;
    if (o.record) {
        R.obs = rec.obs;
        R.res = rec.res;
        R.ticks = rec.ticks;
        R.map = rec.map;
    }
    return R;
}

bool replay_matches(const EpisodeResult& recd, const GoalSpec& goal, const Arm& arm, const PolicyParams& p,
                    const CaseBase* cb, std::uint32_t policy_version, int* first_diff) {
    ReplayPlatform rp(recd.map, recd.obs, recd.res, recd.ticks);
    PolicyParams pp = p;
    pp.use_uncertainty = arm.uncertainty ? 1 : 0;
    StateEstimator est(2);
    CaseRetriever retr(cb);
    auto delib = std::make_unique<Deliberator>(arm.mode, &pp);
    delib->inject_defect(arm.planner_defect);
    SafetySupervisor safety(SafetyLimits{}, arm.safety);
    ExecutiveConfig cfg;
    cfg.use_retrieval = arm.retrieval && cb;
    cfg.policy_version = policy_version;
    std::vector<DecisionRecord> decs;
    decs.reserve(recd.decisions.size() + 64);
    Collector col{&decs};
    auto ex = std::make_unique<Executive>(rp, est, cb ? &retr : nullptr, *delib, safety, cfg, DecisionSink{collect, &col});
    if (ex->begin(goal) != Status::Ok) { if (first_diff) *first_diff = 0; return false; }
    StepResult sr = StepResult::Continue;
    int guard = 0;
    while (sr == StepResult::Continue && guard++ < 6000 && decs.size() < recd.decisions.size() + 8) sr = ex->step();
    const std::size_t n = std::min(decs.size(), recd.decisions.size());
    for (std::size_t i = 0; i < n; ++i) {
        const DecisionRecord& a = decs[i];
        const DecisionRecord& b = recd.decisions[i];
        const bool same = a.timestamp == b.timestamp && a.decision == b.decision && a.rule_id == b.rule_id &&
                          a.evidence_id == b.evidence_id && a.reason_code == b.reason_code &&
                          a.action_type == b.action_type && a.arg0 == b.arg0 && a.arg1 == b.arg1 &&
                          a.obs_id == b.obs_id && a.authorized == b.authorized && a.plan_nodes == b.plan_nodes;
        if (!same) { if (first_diff) *first_diff = int(i); return false; }
    }
    if (decs.size() != recd.decisions.size()) { if (first_diff) *first_diff = int(n); return false; }
    if (first_diff) *first_diff = -1;
    return true;
}

void build_case_base(const std::vector<ScenarioConfig>& scen, CaseBase& cb) {
    std::memset(&cb, 0, sizeof cb);
    Arm b0{"B0", ExploreMode::Fsm, false, L_NONE, true, true};
    const PolicyParams p = default_params();
    for (const auto& sc : scen) {
        if (cb.n >= kMaxCases) break;
        const GoalSpec g = goal_for(sc);
        RunOpts o;
        const EpisodeResult r = run_episode(sc, g, b0, p, nullptr, o);
        World w(sc, 0);
        MapHint m{};
        w.map_hint(m);
        CaseRecord c{};
        c.id = CaseId(cb.n + 1);
        c.task_type = kGoalFindObject;
        c.environment_type = m.env_type;
        c.event_type = r.target_zone;       // 성공했을 때만 뜻이 있다
        c.action_type = 1;
        c.outcome_code = r.outcome == O_SUCCESS ? 1 : 0;
        c.duration_ms = std::uint32_t(r.steps);
        c.policy_version = 1;
        c.target_type = sc.target_type;
        c.required_attributes = g.required_attributes;
        cb.c[cb.n++] = c;
    }
}

SuiteScore score_suite(const std::vector<ScenarioConfig>& suite, const Arm& arm, const PolicyParams& p,
                       const CaseBase* cb) {
    SuiteScore s;
    for (const auto& sc : suite) {
        RunOpts o;
        const EpisodeResult r = run_episode(sc, goal_for(sc), arm, p, cb, o);
        const bool ok = r.outcome == O_SUCCESS;
        s.solved.push_back(ok ? 1 : 0);
        s.success += ok;
        s.false_declare += r.outcome == O_FALSE_DECLARE;
        s.violations += r.viol.total();
    }
    return s;
}

void append_case(CaseBase& cb, const ScenarioConfig& sc, const EpisodeResult& r, std::uint32_t version) {
    if (cb.n >= kMaxCases) {   // 가득 차면 가장 오래된 것을 민다(최근 경험 우선)
        for (int i = 0; i + 1 < cb.n; ++i) cb.c[i] = cb.c[i + 1];
        --cb.n;
    }
    World w(sc, 0);
    MapHint m{};
    w.map_hint(m);
    CaseRecord c{};
    CaseId maxid = 0;
    for (int i = 0; i < cb.n; ++i) if (cb.c[i].id > maxid) maxid = cb.c[i].id;
    c.id = maxid + 1;
    c.task_type = kGoalFindObject;
    c.environment_type = m.env_type;
    c.event_type = r.target_zone;
    c.action_type = 2;
    c.outcome_code = r.outcome == O_SUCCESS ? 1 : 0;
    c.duration_ms = std::uint32_t(r.steps);
    c.policy_version = version;
    c.target_type = sc.target_type;
    c.required_attributes = goal_for(sc).required_attributes;
    cb.c[cb.n++] = c;
}

ValidationResult PolicyValidator::validate(const RuleCandidate& cand, const PolicyParams& current, const CaseBase* cb) {
    ValidationResult v;
    const char* why = nullptr;
    if (!params_invariants_ok(cand.params, &why)) {
        v.reason = std::string("static:") + why;
        return v;
    }
    const SuiteScore ob = score_suite(old_, arm_, current, cb), oa = score_suite(old_, arm_, cand.params, cb);
    const SuiteScore nb = score_suite(new_, arm_, current, cb), na = score_suite(new_, arm_, cand.params, cb);
    v.episodes_run = int(2 * old_.size() + 2 * new_.size());
    episodes_total += v.episodes_run;
    v.old_before = ob.success; v.old_after = oa.success;
    v.new_before = nb.success; v.new_after = na.success;
    for (std::size_t k = 0; k < old_.size(); ++k) v.regressions += ob.solved[k] && !oa.solved[k];
    v.fd_before = ob.false_declare + nb.false_declare;
    v.fd_after = oa.false_declare + na.false_declare;
    v.violations = oa.violations + na.violations;
    if (v.violations > 0) v.reason = "safety_violation";
    else if (v.regressions > max_regress_) v.reason = "regression_on_old_suite";
    else if (v.old_after < v.old_before) v.reason = "old_suite_worse";
    else if (v.fd_after > v.fd_before) v.reason = "more_false_declares";
    else if (v.new_after <= v.new_before) v.reason = "no_gain_on_new_suite";
    else { v.approved = true; v.reason = "approved"; }
    return v;
}

PolicyStore learn_stream(const std::vector<ScenarioConfig>& train, PolicyValidator* validator, const Arm& arm,
                         CaseBase* cb, bool store_experience, LearnLog& log) {
    PolicyStore store;
    PolicyLearner learner;
    char buf[320];
    for (const auto& sc : train) {
        RunOpts o;
        o.policy_version = store.current_id();
        const EpisodeResult r = run_episode(sc, goal_for(sc), arm, store.current(), cb, o);
        if (store_experience && cb && r.outcome == O_SUCCESS) { append_case(*cb, sc, r, store.current_id()); ++log.cases_added; }
        if (r.outcome == O_SUCCESS || arm.learn == L_NONE) continue;
        ++log.failures;
        EpisodeRecord rec = r.rec;
        rec.false_declares = r.outcome == O_FALSE_DECLARE;
        RuleCandidate cand[8];
        int nc = 0;
        learner.propose(rec, store.current(), cand, 8, nc);
        log.proposals += nc;
        if (nc == 0) continue;
        if (arm.learn == L_UNVERIFIED || !validator) {
            const std::uint32_t id = store.submit(cand[0], true);
            ++log.applied_unverified;
            std::snprintf(buf, sizeof buf, "seed %u %s -> v%u change=%u (unverified)", sc.seed, outcome_name(r.outcome), id,
                          cand[0].change_code);
            log.events.push_back(buf);
            continue;
        }
        // 후보를 전부 검증하고, 통과한 것 중 새 환경 모음에서 가장 나은 것 하나만 승인한다.
        // (첫 판은 처음 통과한 것을 바로 받아서, 효과 없는 후보가 우연히 +1 로 통과하면 진짜 해법은
        //  시험도 못 받았다.) 대가: 20 판짜리 모음에서 k 개 중 최고를 고르면 우연히 좋아 보인 것을
        //  고를 위험(선택 편향)이 커진다 — 보고서에 적는다.
        const auto t0 = std::chrono::steady_clock::now();
        int best = -1;
        ValidationResult bestv;
        for (int i = 0; i < nc; ++i) {
            const ValidationResult v = validator->validate(cand[i], store.current(), cb);
            log.validation_episodes += v.episodes_run;
            if (!v.approved) {
                if (v.reason.rfind("static:", 0) == 0) ++log.rejected_static; else ++log.rejected_regression;
                continue;
            }
            if (best < 0 || v.new_after > bestv.new_after ||
                (v.new_after == bestv.new_after && v.regressions < bestv.regressions)) { best = i; bestv = v; }
        }
        for (int i = 0; i < nc; ++i) {
            if (i == best) continue;
            store.submit(cand[i], false);
        }
        if (best >= 0) {
            const std::uint32_t id = store.submit(cand[best], true);
            ++log.approved;
            std::snprintf(buf, sizeof buf, "seed %u %s -> v%u change=%u old %d->%d (regress %d) new %d->%d (%d candidates)",
                          sc.seed, outcome_name(r.outcome), id, cand[best].change_code, bestv.old_before, bestv.old_after,
                          bestv.regressions, bestv.new_before, bestv.new_after, nc);
            log.events.push_back(buf);
        }
        log.validation_seconds += std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
    }
    return store;
}

PolicyStore se_improve(const std::vector<ScenarioConfig>& hunt, const std::vector<ScenarioConfig>& old_suite,
                       const std::vector<ScenarioConfig>& new_suite, const Arm& arm, CaseBase* cb, bool store_experience,
                       long budget, SeLog& log, int max_regress) {
    PolicyStore store;
    char buf[360];
    auto run = [&](const ScenarioConfig& sc, const PolicyParams& p) {
        ++log.episodes;
        RunOpts o;
        o.policy_version = store.current_id();
        return run_episode(sc, goal_for(sc), arm, p, cb, o);
    };
    auto suite = [&](const std::vector<ScenarioConfig>& s, const PolicyParams& p) {
        SuiteScore r;
        for (const auto& sc : s) {
            const EpisodeResult e = run(sc, p);
            const bool ok = e.outcome == O_SUCCESS;
            r.solved.push_back(ok ? 1 : 0);
            r.success += ok;
            r.false_declare += e.outcome == O_FALSE_DECLARE;
            r.violations += e.viol.total();
        }
        return r;
    };
    SuiteScore old_cur = suite(old_suite, store.current()), new_cur = suite(new_suite, store.current());
    std::vector<ScenarioConfig> ratchet, pending;   // 래칫(고친 반례) · 아직 못 고친 반례(최근 8개)
    constexpr std::size_t kPending = 8, kRatchet = 24;
    for (const auto& sc : hunt) {
        if (log.episodes >= budget) break;
        ++log.hunted;
        const EpisodeResult r = run(sc, store.current());
        if (r.outcome == O_SUCCESS) {
            if (store_experience && cb && log.hunted <= log.exp_cap) { append_case(*cb, sc, r, store.current_id()); ++log.cases_added; }
            continue;
        }
        ++log.counterexamples;
        // 반사실 진단: 이웃 전부를 이 반례에 돌린다
        RuleCandidate nb[kMaxNeighbors];
        const int nn = policy_neighbors(store.current(), nb, kMaxNeighbors);
        struct Fix { int i; int witness; };
        std::vector<Fix> fixers;
        for (int i = 0; i < nn && log.episodes < budget; ++i)
            if (run(sc, nb[i].params).outcome == O_SUCCESS) fixers.push_back({i, 0});
        if (fixers.empty()) {
            ++log.unfixable;
            pending.push_back(sc);
            if (pending.size() > kPending) pending.erase(pending.begin());
            continue;
        }
        ++log.fixable;
        // 두 번째 증인: 미해결 반례 중 몇 개를 같이 고치나
        for (auto& f : fixers)
            for (const auto& pc : pending) {
                if (log.episodes >= budget) break;
                f.witness += run(pc, nb[f.i].params).outcome == O_SUCCESS;
            }
        std::stable_sort(fixers.begin(), fixers.end(), [](const Fix& a, const Fix& b) { return a.witness > b.witness; });
        bool promoted = false;
        for (std::size_t k = 0; k < fixers.size() && k < 3 && log.episodes < budget; ++k) {
            const RuleCandidate& cand = nb[fixers[k].i];
            // 래칫: 전에 고친 반례를 하나라도 다시 깨면 탈락
            bool keeps = true;
            for (const auto& rc : ratchet)
                if (run(rc, cand.params).outcome != O_SUCCESS) { keeps = false; break; }
            if (!keeps) { ++log.rejected_ratchet; store.submit(cand, false); continue; }
            const SuiteScore oa = suite(old_suite, cand.params);
            int regress = 0;
            for (std::size_t j = 0; j < old_suite.size(); ++j) regress += old_cur.solved[j] && !oa.solved[j];
            if (oa.violations > 0 || regress > max_regress || oa.success < old_cur.success ||
                oa.false_declare > old_cur.false_declare) {
                ++log.rejected_old; store.submit(cand, false); continue;
            }
            const SuiteScore na = suite(new_suite, cand.params);
            const int gain = na.success - new_cur.success;
            if (na.violations > 0 || gain < 0 || na.false_declare > new_cur.false_declare ||
                fixers[k].witness + gain < 1) {
                ++log.rejected_witness; store.submit(cand, false); continue;
            }
            const std::uint32_t id = store.submit(cand, true);
            ++log.promoted;
            std::snprintf(buf, sizeof buf, "seed %u %s -> v%u change=%u fixers %zu witness %d new %+d old %d->%d regress %d",
                          sc.seed, outcome_name(r.outcome), id, cand.change_code, fixers.size(), fixers[k].witness, gain,
                          old_cur.success, oa.success, regress);
            log.events.push_back(buf);
            old_cur = oa;
            new_cur = na;
            // 래칫에 이 반례와 같이 고쳐진 미해결 반례를 넣는다(새 정책으로 다시 재서 — 이미 센 결과를 믿지 않는다)
            if (ratchet.size() < kRatchet) ratchet.push_back(sc);
            std::vector<ScenarioConfig> still;
            for (const auto& pc : pending) {
                const bool fixed = log.episodes < budget && run(pc, store.current()).outcome == O_SUCCESS;
                if (fixed && ratchet.size() < kRatchet) ratchet.push_back(pc);
                else if (!fixed) still.push_back(pc);
            }
            pending.swap(still);
            promoted = true;
            break;
        }
        if (!promoted) {
            pending.push_back(sc);
            if (pending.size() > kPending) pending.erase(pending.begin());
        }
    }
    log.ratchet = int(ratchet.size());
    return store;
}

double sign_test_p(int wins, int losses) {
    const int n = wins + losses;
    if (n == 0) return 1.0;
    // 이항 꼬리 — n 이 작으니 직접 더한다(로그 공간)
    double p = 0;
    for (int k = wins; k <= n; ++k) p += std::exp(std::lgamma(n + 1.0) - std::lgamma(k + 1.0) - std::lgamma(n - k + 1.0) - n * std::log(2.0));
    return p > 1 ? 1 : p;
}

PolicyStore se_improve2(const std::vector<ScenarioConfig>& hunt, const std::vector<ScenarioConfig>& new_pool,
                        const std::vector<ScenarioConfig>& old_pool, const Arm& arm, CaseBase* cb, bool store_experience,
                        long budget, SeLog& log, const Se2Opts& opt) {
    PolicyStore store;
    char buf[360];
    auto run = [&](const ScenarioConfig& sc, const PolicyParams& p) {
        ++log.episodes;
        RunOpts o;
        o.policy_version = store.current_id();
        return run_episode(sc, goal_for(sc), arm, p, cb, o);
    };
    // 짝지은 표본: 승격 때마다 **새** 조각을 쓴다(같은 표본으로 여러 후보를 고르면 선택 편향 — 쓴 조각은 버린다)
    std::size_t new_at = 0, old_at = 0;
    std::vector<int> votes(256, 0);          // change_code → 이 변화가 고친 반례 수(현재 정책 기준, 승격 때 초기화)
    std::vector<int> tested(256, 0);         // 현재 정책에서 이미 검정해 떨어진 변화
    int tests = 0;
    constexpr int kAll = kMaxNeighbors * 3;
    auto neighbors = [&](const PolicyParams& cur, RuleCandidate* out) {
        int n = policy_neighbors(cur, out, kMaxNeighbors, 1);
        for (int s = 2; s <= opt.max_scale && n < kAll; s *= 2) n += policy_neighbors(cur, out + n, kAll - n < kMaxNeighbors ? kAll - n : kMaxNeighbors, s);
        return n;
    };
    // 한 후보를 검정한다. 결과: 1 승격 · 0 거부 · -1 예산/표본이 모자라 못 함(거부로 적지 않는다)
    auto test = [&](const RuleCandidate& cand, std::uint32_t seed, const char* why) -> int {
        const long need = 2L * 40 + 2L * opt.paired_old;
        if (log.episodes + need > budget || new_at + 40 > new_pool.size() || old_at + std::size_t(opt.paired_old) > old_pool.size()) return -1;
        ++tests;
        tested[cand.change_code] = 1;
        int nw = 0, nl = 0, ow = 0, ol = 0, fd_b = 0, fd_a = 0, viol = 0;
        // 순차 검정: 40판씩, 매 조각 뒤에 본다. 받아들임은 한 번 볼 때 p<alpha(=0.005, 10번 봐도 합쳐 ~0.05).
        // 버림: 80판 이상 봤는데 이긴 것이 진 것 이하 — 예산을 나쁜 후보에 안 쓴다.
        bool pass_new = false;
        for (int chunk = 0; chunk * 40 < opt.paired_new; ++chunk) {
            if (new_at + 40 > new_pool.size() || log.episodes + 80 > budget) break;
            for (int k = 0; k < 40; ++k) {
                const ScenarioConfig& s2 = new_pool[new_at++];
                const EpisodeResult b = run(s2, store.current()), a = run(s2, cand.params);
                nw += b.outcome != O_SUCCESS && a.outcome == O_SUCCESS;
                nl += b.outcome == O_SUCCESS && a.outcome != O_SUCCESS;
                fd_b += b.outcome == O_FALSE_DECLARE; fd_a += a.outcome == O_FALSE_DECLARE; viol += a.viol.total();
            }
            if (viol) break;
            if (sign_test_p(nw, nl) < opt.alpha) { pass_new = true; break; }
            if (chunk >= 1 && nw <= nl) break;
            // 무익 정지(v3): 두 조각 넘게 봤는데 p>0.3 이면 거기서 끊는다 — +51 −49 가 400판(800 에피소드)을 다 먹었다
            if (opt.futility && chunk >= 1 && sign_test_p(nw, nl) > 0.3) break;
        }
        if (pass_new) {
            if (old_at + std::size_t(opt.paired_old) > old_pool.size() || log.episodes + 2L * opt.paired_old > budget) pass_new = false;
            else for (int k = 0; k < opt.paired_old; ++k) {
                const ScenarioConfig& s2 = old_pool[old_at++];
                const EpisodeResult b = run(s2, store.current()), a = run(s2, cand.params);
                ow += b.outcome != O_SUCCESS && a.outcome == O_SUCCESS;
                ol += b.outcome == O_SUCCESS && a.outcome != O_SUCCESS;
                fd_b += b.outcome == O_FALSE_DECLARE; fd_a += a.outcome == O_FALSE_DECLARE; viol += a.viol.total();
            }
        }
        const double p_gain = sign_test_p(nw, nl);
        const double p_old_harm = sign_test_p(ol, ow);     // 옛 환경이 나빠졌다는 쪽의 증거
        const bool ok = pass_new && viol == 0 && p_gain < opt.alpha && p_old_harm >= 0.2 && fd_a <= fd_b + 2;
        std::snprintf(buf, sizeof buf, "seed %u change=%u %s votes %d | new +%d -%d p=%.4f | old +%d -%d p_harm=%.3f | fd %d->%d %s",
                      seed, cand.change_code, why, votes[cand.change_code], nw, nl, p_gain, ow, ol, p_old_harm, fd_b, fd_a,
                      ok ? "PROMOTE" : "reject");
        log.events.push_back(buf);
        if (!ok) {
            if (p_old_harm < 0.2 || viol) ++log.rejected_old; else ++log.rejected_witness;
            store.submit(cand, false);
            return 0;
        }
        store.submit(cand, true);
        ++log.promoted;
        std::fill(votes.begin(), votes.end(), 0);   // 정책이 바뀌었으니 옛 표는 옛 정책에 대한 것이다
        std::fill(tested.begin(), tested.end(), 0);
        return 1;
    };
    static RuleCandidate nb[kMaxNeighbors * 3];
    bool out_of_budget = false;
    // 양쪽 진단(v3): 반례만이 아니라 **성공한 판에도** 이웃을 돌려 '깸' 을 센다. 좋은 변화는 많이 고치는 것이 아니라
    // 거의 안 깨는 것이었다(diagnose --base hold: 대기+16 은 고침 10/36 · 깸 4/84, 잡음 이웃은 고침 19/36 · 깸 20/84).
    std::vector<int> breaks(256, 0);
    int n_ce = 0, n_succ_diag = 0, n_hunt = 0, n_fail = 0;   // 현재 정책 기준(승격 때 초기화)
    std::uint32_t seen_ver = store.current_id();
    for (const auto& sc : hunt) {
        if (log.episodes >= budget || out_of_budget) break;
        if (store.current_id() != seen_ver) {
            seen_ver = store.current_id();
            std::fill(breaks.begin(), breaks.end(), 0);
            n_ce = n_succ_diag = n_hunt = n_fail = 0;
        }
        ++log.hunted;
        ++n_hunt;
        const EpisodeResult r = run(sc, store.current());
        if (r.outcome == O_SUCCESS) {
            if (store_experience && cb && log.hunted <= log.exp_cap) { append_case(*cb, sc, r, store.current_id()); ++log.cases_added; }
            if (opt.two_sided && n_succ_diag < n_ce) {   // 성공 진단 수를 반례 진단 수에 맞춘다
                const int nn = neighbors(store.current(), nb);
                for (int i = 0; i < nn && log.episodes < budget; ++i)
                    if (!tested[nb[i].change_code] && run(sc, nb[i].params).outcome != O_SUCCESS) ++breaks[nb[i].change_code];
                ++n_succ_diag;
            }
            continue;
        }
        ++log.counterexamples;
        ++n_fail;
        const int nn = neighbors(store.current(), nb);
        int fixed_any = 0;
        for (int i = 0; i < nn && log.episodes < budget; ++i)
            if (!tested[nb[i].change_code] && run(sc, nb[i].params).outcome == O_SUCCESS) { ++votes[nb[i].change_code]; ++fixed_any; }
        ++n_ce;
        if (!fixed_any) { ++log.unfixable; continue; }
        ++log.fixable;
        int best = -1;
        double best_score = 0;
        if (opt.two_sided) {
            // 추정 순이득 = 실패율·고침률 − 성공율·깸률. 양쪽 표본이 opt.min_votes 이상 쌓인 뒤에만 고른다
            if (n_ce < opt.min_diag || n_succ_diag < opt.min_diag) continue;
            const double pf = double(n_fail) / n_hunt;
            for (int i = 0; i < nn; ++i) {
                const int c = nb[i].change_code;
                if (tested[c]) continue;
                const double d = pf * votes[c] / n_ce - (1 - pf) * double(breaks[c]) / n_succ_diag;
                if (d >= opt.min_net && (best < 0 || d > best_score)) { best = i; best_score = d; }
            }
        } else {
            for (int i = 0; i < nn; ++i) {
                const int c = nb[i].change_code;
                if (!tested[c] && votes[c] >= opt.min_votes && (best < 0 || votes[c] > votes[nb[best].change_code])) best = i;
            }
        }
        if (best < 0) continue;
        if (opt.max_tests && tests >= opt.max_tests) continue;
        const RuleCandidate cand = nb[best];
        int res = test(cand, sc.seed, "vote");
        if (res < 0) { out_of_budget = true; break; }
        // 패턴 이동(Hooke-Jeeves): 승격한 방향을 **표 없이** 곧장 한 번 더 — 통과하는 동안 계속
        while (res == 1 && opt.pattern_move) {
            RuleCandidate nb2[kMaxNeighbors];
            const int lg = cand.change_code / 32;
            const int n2 = policy_neighbors(store.current(), nb2, kMaxNeighbors, 1 << lg);
            int same = -1;
            for (int i = 0; i < n2; ++i) if (nb2[i].change_code == cand.change_code) same = i;
            if (same < 0) break;
            res = test(nb2[same], sc.seed, "pattern");
            if (res < 0) { out_of_budget = true; break; }
        }
    }
    return store;
}

}  // namespace walpeval

// ---------------------------------------------------------------- 개념 사전 적재
#include <fstream>
#include <sstream>

namespace walpeval {

namespace {
int concept_value(std::uint8_t cat, const std::string& name) {
    static const char* obj[] = {"", "card", "key", "cup", "box"};
    static const char* col[] = {"", "red", "blue", "green", "black"};
    static const char* br[] = {"", "visa", "master"};
    static const char* zn[] = {"floor", "desk", "shelf", "counter"};
    auto find = [&](const char* const* t, int n) { for (int i = 0; i < n; ++i) if (name == t[i] && *t[i]) return i; return -1; };
    switch (cat) {
    case KB_OBJECT: return find(obj, 5);
    case KB_COLOR: return find(col, 5);
    case KB_BRAND: case KB_BRANDCARD: return find(br, 3);
    case KB_ZONE: { for (int i = 0; i < 4; ++i) if (name == zn[i]) return i; return -1; }
    default: return -1;
    }
}
}  // namespace

bool parse_kb_line(const std::string& line, std::string& word, std::uint8_t& cat, std::uint8_t& rel, std::uint8_t& conf,
                   std::uint8_t& mask, std::string& err) {
    std::vector<std::string> f;
    std::stringstream ss(line);
    std::string x;
    while (std::getline(ss, x, ',')) f.push_back(x);
    if (f.size() != 5) { err = "칸이 5개가 아니다"; return false; }
    word = f[0];
    static const std::pair<const char*, std::uint8_t> cats[] = {{"color", KB_COLOR}, {"brand", KB_BRAND},
        {"brandcard", KB_BRANDCARD}, {"object", KB_OBJECT}, {"zone", KB_ZONE}, {"modifier", KB_MOD}};
    static const std::pair<const char*, std::uint8_t> rels[] = {{"canon", REL_CANON}, {"syn", REL_SYN}, {"near", REL_NEAR},
        {"hypo", REL_HYPO}, {"hyper", REL_HYPER}, {"mod", REL_MOD}};
    cat = rel = 0;
    for (auto& c : cats) if (f[1] == c.first) cat = c.second;
    for (auto& r : rels) if (f[3] == r.first) rel = r.second;
    if (!cat) { err = "모르는 범주 " + f[1]; return false; }
    if (!rel) { err = "모르는 관계 " + f[3]; return false; }
    int cv = 0;
    try { cv = std::stoi(f[4]); } catch (...) { err = "conf 가 수가 아니다"; return false; }
    if (cv < 0 || cv > 100) { err = "conf 범위 밖"; return false; }
    conf = std::uint8_t(cv);
    mask = 0;
    if (cat != KB_MOD) {
        std::stringstream cs(f[2]);
        std::string c;
        int k = 0;
        while (std::getline(cs, c, '|')) {
            const int v = concept_value(cat, c);
            if (v < 0) { err = "모르는 개념 " + c; return false; }
            mask |= std::uint8_t(1u << v);
            ++k;
        }
        if (k > 1 && rel != REL_HYPER) { err = "개념이 여럿인데 hyper 가 아니다"; return false; }
        if (k == 1 && rel == REL_HYPER) { err = "hyper 인데 개념이 하나다"; return false; }
    }
    return true;
}

bool load_kb(const std::string& path, KnowledgeBase& kb, std::vector<std::string>& errors, bool learned) {
    std::ifstream f(path);
    if (!f) { if (!learned) errors.push_back("사전을 못 읽었다: " + path); return learned; }
    std::string line;
    int ln = 0;
    while (std::getline(f, line)) {
        ++ln;
        while (!line.empty() && (line.back() == '\r' || line.back() == ' ')) line.pop_back();
        if (line.empty() || line[0] == '#') continue;
        std::string w, err;
        std::uint8_t cat, rel, conf, mask;
        if (!parse_kb_line(line, w, cat, rel, conf, mask, err)) { errors.push_back(path + ":" + std::to_string(ln) + " " + err); continue; }
        const KbAdd r = kb_add(kb, w.c_str(), cat, rel, conf, mask);
        if (r == KbAdd::Conflict) errors.push_back(path + ":" + std::to_string(ln) + " 충돌: " + w);
        else if (r == KbAdd::Full || r == KbAdd::Invalid) errors.push_back(path + ":" + std::to_string(ln) + " 못 넣음: " + w);
    }
    return true;
}

#ifndef WALP_SRC_DIR
#define WALP_SRC_DIR "."
#endif
std::string default_kb_path() {
    if (const char* e = std::getenv("WALP_KB")) return e;
    return std::string(WALP_SRC_DIR) + "/data/lexicon.csv";
}
std::string default_learned_path() {
    if (const char* e = std::getenv("WALP_LEARNED")) return e;
    return std::string(WALP_SRC_DIR) + "/data/learned.csv";
}

}  // namespace walpeval

// ---------------------------------------------------------------- 정책 저장소 영속
#include <cstdio>
#include <unistd.h>
#include <fcntl.h>

namespace walpeval {

bool save_store_atomic(const PolicyStore& st, const std::string& path, bool crash_after_temp, std::size_t truncate_temp_to) {
    std::vector<std::uint8_t> buf(PolicyStore::kImageSize);
    const std::size_t n = st.serialize(buf.data(), buf.size());
    if (n == 0) return false;
    const std::string tmp = path + ".tmp";
    const int fd = ::open(tmp.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) return false;
    const std::size_t want = truncate_temp_to ? truncate_temp_to : n;   // 시험: 쓰다 만 파일
    const bool ok = ::write(fd, buf.data(), want) == ssize_t(want) && ::fsync(fd) == 0;
    ::close(fd);
    if (!ok) return false;
    if (crash_after_temp) return false;   // rename 전에 '죽었다' — 본 파일은 옛 판 그대로
    return std::rename(tmp.c_str(), path.c_str()) == 0;
}

bool load_store(PolicyStore& st, const std::string& path) {
    std::FILE* f = std::fopen(path.c_str(), "rb");
    if (!f) return false;
    std::vector<std::uint8_t> buf(PolicyStore::kImageSize + 16);
    const std::size_t n = std::fread(buf.data(), 1, buf.size(), f);
    std::fclose(f);
    return st.deserialize(buf.data(), n);
}

}  // namespace walpeval
