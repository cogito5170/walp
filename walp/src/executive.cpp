// D. 실행 루프 — 한 스텝에 한 행동만 하고 다시 관측한다(명세 §3.3 의 기본값).
//
//   관측 → 추정 → (시작 때 한 번) 검색 → 계획 → 안전 허가 → 실행 → 기록
//
// 계획 점수가 높아도 안전층을 건너뛸 길은 없다: 모든 행동이 authorize 를 지나고, 거부되면
// 그 스텝은 보류(Hold)로 바뀌어 기록된다.
#include "walp/core.hpp"

#include <cstring>

namespace walp {

Executive::Executive(IPlatform& platform, StateEstimator& est, IRetriever* retriever, Deliberator& planner,
                     ISafetySupervisor& safety, ExecutiveConfig cfg, DecisionSink sink)
    : pf_(platform), est_(est), retr_(retriever), planner_(planner), safety_(safety), cfg_(cfg), sink_(sink) {}

Status Executive::begin(const GoalSpec& goal) {
    goal_ = goal;
    if (goal_.deadline == 0) goal_.deadline = GoalManager::kDefaultDeadline;
    if (pf_.map_hint(map_) != Status::Ok) return Status::Fault;
    GoalManager gm;
    std::uint16_t reason = 0;
    const Status gs = gm.validate(goal_, map_, reason);
    if (gs != Status::Ok) return gs;           // 임무 시작 금지
    est_.init(b_, map_);
    planner_.reset();
    start_ = pf_.now();
    steps_ = observes_ = denials_ = deny_run_ = stale_steps_ = 0;
    outcome_ = O_NONE;
    dx_ = dy_ = -1;
    uniform_prior(cases_);
    if (cfg_.use_retrieval && retr_) {
        // 검색 실패(Fault 등)와 '정상적인 빈 결과'(NoEvidence)를 가른다
        est_.set_now(b_, pf_.now());
        const Status rs = retr_->retrieve(goal_, b_, cases_);
        if (rs != Status::Ok && rs != Status::NoEvidence) return rs;
        if (rs == Status::NoEvidence) uniform_prior(cases_);
    }
    return Status::Ok;
}

void Executive::emit(const Plan& p, const ActionRequest& a, bool authorized, std::uint16_t reason, Decision d,
                     RuleId rule) {
    if (!sink_.fn) return;
    DecisionRecord r{};
    r.timestamp = pf_.now();
    r.decision = d;
    r.rule_id = rule;
    r.evidence_id = p.evidence_id;
    r.reason_code = reason;
    r.action_type = a.action_type;
    r.arg0 = a.arg0;
    r.arg1 = a.arg1;
    r.policy_version = cfg_.policy_version;
    r.obs_id = b_.last_obs_id;
    r.n_cases = cases_.n;
    r.first_case = cases_.n ? cases_.ids[0] : 0;
    r.plan_nodes = p.nodes;
    r.authorized = authorized;
    sink_.fn(sink_.ctx, r);
}

StepResult Executive::step() {
    const Tick now = pf_.now();
    est_.set_now(b_, now);
    // 기한
    if (now - start_ >= goal_.deadline) {
        Plan p{};
        ActionRequest a{kAbort, 0, 0, 0};
        emit(p, a, true, RS_DEADLINE, Decision::Abort, R_ABORT_DEADLINE);
        outcome_ = O_TIMEOUT;
        return StepResult::Deadline;
    }
    ++steps_;

    // 관측·추정. 관측 호출 자체가 실패하면(플랫폼 고장) 보류하고 기록한다.
    Observation obs{};
    const Status os = pf_.observe(obs);
    if (os != Status::Ok) obs.valid = false;
    const Status es = est_.update(obs, b_);
    if (es != Status::Ok) ++stale_steps_;

    Plan plan{};
    planner_.plan(goal_, b_, cases_, plan);
    if (plan.count == 0) {
        plan.count = 1;
        plan.actions[0] = ActionRequest{kHold, 0, 0, 1};
        plan.decision = Decision::Hold;
        plan.rule_id = R_ABORT_NO_PLAN;
    }
    ActionRequest a = plan.actions[0];

    SafetyState ss{&b_, goal_.constraints, now};
    std::uint16_t reason = 0;
    const bool ok = safety_.authorize(a, ss, reason);
    Decision d = plan.decision;
    RuleId rule = plan.rule_id;
    if (!ok && reason == RS_BATTERY_RESERVE && safety_.fallback(ss).action_type != kHold) {
        // (fallback 이 kAbort 면: 집에서 끝내거나, 길이 막히고 대기 여유를 다 써 그 자리에서 안전 정지)
        // 에너지 하한: 안전층이 제어를 넘겨받는다 — 계획 대신 안전층의 행동을 실행한다
        const ActionRequest fb = safety_.fallback(ss);
        ++denials_;
        deny_run_ = 0;
        emit(plan, fb, true, RS_BATTERY_RESERVE, fb.action_type == kAbort ? Decision::Abort : Decision::Act, R_SAFETY_RETURN);
        if (fb.action_type == kAbort) { outcome_ = O_ABORT_SAFETY; return StepResult::Aborted; }
        ExecutionResult er{};
        if (pf_.execute(fb, er) == Status::Ok && fb.action_type >= kMoveN && fb.action_type <= kMoveW) {
            int ddx, ddy;
            action_delta(fb.action_type, ddx, ddy);
            b_.expect_move = true;
            b_.expect_dx = std::int8_t(ddx);
            b_.expect_dy = std::int8_t(ddy);
        }
        return StepResult::Continue;
    }
    if (!ok && reason == RS_BATTERY_RESERVE) {
        // 집 쪽 한 걸음도 없다(길이 막혔다) — 대기. 보류도 에너지를 쓰지만 대기 여유 안이다.
        // 거부 연쇄로 세지 않는다: 여기서 중단하면 집 밖에서 멈춘 채 끝나 좌초와 같다.
        ++denials_;
        emit(plan, a, false, reason, Decision::Hold, R_SAFETY_RETURN);
        ActionRequest hold{kHold, 0, 0, 1};
        ExecutionResult er{};
        pf_.execute(hold, er);
        return StepResult::Continue;
    }
    if (!ok) {
        ++denials_;
        ++deny_run_;
        // 막힘으로 거부됐는데 '막히면 멈춰' 조건이면 보류, 아니면 다음 스텝에 재계획
        d = Decision::Hold;
        rule = R_HOLD_DENIED;
        if (deny_run_ >= cfg_.deny_abort_run) {
            ActionRequest ab{kAbort, 0, 0, 0};
            emit(plan, ab, true, reason, Decision::Abort, R_ABORT_DENY_LOOP);
            outcome_ = O_ABORT_SAFETY;
            return StepResult::Aborted;
        }
        // 거부된 행동은 실행하지 않고 제자리 보류로 바꾼다
        emit(plan, a, false, reason, d, rule);
        ActionRequest hold{kHold, 0, 0, 1};
        ExecutionResult er{};
        pf_.execute(hold, er);
        return StepResult::Continue;
    }
    deny_run_ = 0;

    if (a.action_type == kAbort) {
        emit(plan, a, true, plan.reason_code, Decision::Abort, rule);
        outcome_ = (rule == R_ABORT_FAULT) ? O_ABORT_FAULT : O_ABORT_NO_TARGET;
        return StepResult::Aborted;
    }

    emit(plan, a, true, plan.reason_code, d, rule);
    ExecutionResult er{};
    const Status xs = pf_.execute(a, er);
    if (a.action_type == kObserve) {
        ++observes_;
        // 재관측을 부른 가설에 표시해 무한 재관측을 막는다
        for (int k = 0; k < b_.n_hyp; ++k)
            if (b_.hyp[k].x == a.arg0 && b_.hyp[k].y == a.arg1 && rule == R_OBSERVE_UNCERTAIN) {
                if (++b_.hyp[k].observes_spent > 250) b_.hyp[k].observes_spent = 250;
            }
    }
    if (xs != Status::Ok) {
        // 실행 실패는 성공으로 기록하지 않는다 — 고장 상태로 넘긴다
        if (a.action_type >= kMoveN && a.action_type <= kMoveW) {
            if (++b_.move_mismatch_run >= 3) b_.actuator_fault = true;
        }
        return StepResult::Continue;
    }
    if (a.action_type >= kMoveN && a.action_type <= kMoveW) {
        int ddx, ddy;
        action_delta(a.action_type, ddx, ddy);
        b_.expect_move = true;
        b_.expect_dx = std::int8_t(ddx);
        b_.expect_dy = std::int8_t(ddy);
    }
    if (a.action_type == kDeclare) {
        dx_ = a.arg0;
        dy_ = a.arg1;
        outcome_ = O_SUCCESS;   // 맞았는지는 코어가 모른다 — 평가기가 정답과 대조한다
        return StepResult::Declared;
    }
    return StepResult::Continue;
}

EpisodeRecord Executive::episode() const {
    EpisodeRecord e{};
    e.outcome = outcome_;
    e.steps = steps_;
    e.observes = observes_;
    e.denials = denials_;
    e.stale_steps = stale_steps_;
    std::uint16_t sc = 0, fc = 0, dis = 0;
    for (int i = 0; i < b_.w * b_.h; ++i) {
        if (b_.kind[i] != kWall) ++fc;
        if (b_.scanned[i]) ++sc;
    }
    for (int k = 0; k < b_.n_hyp; ++k) if (b_.hyp[k].observes_spent > 0) ++dis;
    e.scanned_cells = sc;
    e.free_cells = fc;
    e.energy_left = b_.battery;
    e.hyps_dismissed = dis;
    e.blocked_steps = planner_.blocked_steps();
    e.policy_version = cfg_.policy_version;
    return e;
}

}  // namespace walp
