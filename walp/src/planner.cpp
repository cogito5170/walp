// C. 계획 — 전술 규칙(확정·재관측·접근·귀환)은 공통, 탐사 목표 고르기만 FSM / 제한 탐색으로 갈린다.
//
// 공정성: 두 모드는 같은 행동 공간·같은 경로 탐색(BFS)·같은 믿음을 쓴다. 다른 것은
// '다음에 어디를 보러 갈까' 하나뿐이다. 그래야 B0 vs B2 가 '계획 평가' 의 효과만 잰다.
#include "walp/core.hpp"

#include <cstring>

namespace walp {
namespace {

int argmax_u16(const std::uint16_t* v, int n, std::uint32_t& best, std::uint32_t& total) {
    int bi = 0;
    best = 0; total = 0;
    for (int i = 0; i < n; ++i) {
        total += v[i];
        if (v[i] > best) { best = v[i]; bi = i; }
    }
    return bi;
}

void set_single(Plan& out, std::uint16_t act, std::int32_t a0, std::int32_t a1, Decision d, RuleId r,
                EvidenceId ev, std::uint16_t reason) {
    out.count = 1;
    out.actions[0] = ActionRequest{act, a0, a1, 1};
    out.decision = d;
    out.rule_id = r;
    out.evidence_id = ev;
    out.reason_code = reason;
}

}  // namespace

// 속성 다수표로 목표 여부를 판정한다. 불확실성을 끈 ablation 에서는 '한 번이라도 맞게 보였나'.
Deliberator::Match Deliberator::match(const GoalSpec& g, const Hypothesis& h) const {
    if (h.n == 0 || h.dismissed) return Match::No;
    const Color want_c = attr_color(g.required_attributes);
    const Brand want_b = attr_brand(g.required_attributes);
    if (!p_->use_uncertainty) {
        // 첫 목격에 확정: 어느 한 표라도 원하는 값이면 맞다고 본다(가장 최근 관측 기준을 흉내)
        const bool t = h.v_type[g.target_type] > 0;
        const bool c = want_c == kColorAny || h.v_color[want_c] > 0;
        const bool b = want_b == kBrandAny || h.v_brand[want_b] > 0;
        return (t && c && b) ? Match::Confirmed : Match::No;
    }
    std::uint32_t best, tot;
    const int th = p_->theta_pct;
    bool all_conf = true, any_no = false;
    auto check = [&](const std::uint16_t* v, int n, int want) {
        const int bi = argmax_u16(v, n, best, tot);
        const std::uint32_t wv = v[want];
        if (tot == 0) { all_conf = false; return; }
        if (bi == want && wv * 100 >= std::uint32_t(th) * tot) return;   // 확정 쪽
        all_conf = false;
        // 원하는 값이 거의 안 나오고 다른 값이 충분히 쌓였으면 '아님'
        if (wv * 100 < 25u * tot && tot >= p_->k_confirm) any_no = true;
    };
    check(h.v_type, kNumObj, g.target_type);
    if (want_c) check(h.v_color, kNumColor, want_c);
    if (want_b) check(h.v_brand, kNumBrand, want_b);
    if (any_no) return Match::No;
    if (all_conf && h.n >= p_->k_confirm) return Match::Confirmed;
    return Match::Candidate;
}

std::int32_t Deliberator::cell_weight(const BeliefState& s, int ci, const RetrievedCases& c) const {
    // 칸 하나의 가치 = 그 구역의 사전확률(천분율)을 prior_weight 로 균등과 섞은 것.
    // 구역 크기로 나누지 않는다: 작은 구역(책상)에 몰린 사전확률을 칸마다 크게 쳐 주는 것이
    // 바로 사례 검색이 주려는 정보이고, 균등일 때는 모든 칸이 같아져 순수 면적 이득이 된다.
    const int z = s.zone[ci];
    const std::int32_t uni = 1000 / kNumZones;
    const std::int32_t pri = c.used ? c.zone_prior_pm[z] : uni;
    const std::int32_t w = p_->prior_weight_pct;
    return (pri * w + uni * (100 - w)) / 100;   // 0..1000
}

std::int32_t Deliberator::view_gain(const BeliefState& s, int wx, int wy, const RetrievedCases& c,
                                    std::uint32_t avoid, std::uint16_t* stamp, std::uint16_t mark) const {
    std::int32_t g = 0;
    for (int dy = -kViewR; dy <= kViewR; ++dy)
        for (int dx = -kViewR; dx <= kViewR; ++dx) {
            const int x = wx + dx, y = wy + dy;
            if (x < 0 || y < 0 || x >= s.w || y >= s.h) continue;
            const int ci = cell(x, y, s.w);
            if (s.scanned[ci] || s.kind[ci] == kWall) continue;
            if (avoid & (1u << s.zone[ci])) continue;   // 못 들어가도 보이긴 하지만 거기 둔 물체는 목표 밖이 아니다 — 보수적으로 뺀다
            if (stamp) {
                if (stamp[ci] == mark) continue;
                stamp[ci] = mark;
            }
            g += cell_weight(s, ci, c);
        }
    return g;
}

void Deliberator::build_fsm_order(const GoalSpec& g, const BeliefState& s, const RetrievedCases& c) {
    // 뱀 모양(행 교대) 순회로 경유점을 깐다. 사례가 있으면 사전확률 이득 순으로 한 번 안정 정렬한다.
    fsm_n_ = 0;
    const int step = 2 * kViewR;   // 4칸 간격이면 시야 5칸이 겹치며 덮는다
    for (int y = kViewR; y < s.h + kViewR; y += step) {
        const int yy = y < s.h ? y : s.h - 1;
        const bool rev = ((y / step) & 1) != 0;
        for (int i = 0; i < s.w; i += step) {
            const int x0 = rev ? (s.w - 1 - kViewR - i) : (kViewR + i);
            const int xx = x0 < 0 ? 0 : (x0 >= s.w ? s.w - 1 : x0);
            if (fsm_n_ < kMaxCells / 4) fsm_order_[fsm_n_++] = std::uint16_t(cell(xx, yy, s.w));
        }
    }
    if (c.used && p_->prior_weight_pct > 0) {
        std::int32_t gain[kMaxCells / 4];
        for (int i = 0; i < fsm_n_; ++i)
            gain[i] = view_gain(s, fsm_order_[i] % s.w, fsm_order_[i] / s.w, c, g.constraints & kAvoidMask, nullptr, 0);
        for (int i = 1; i < fsm_n_; ++i) {   // 안정 삽입 정렬, 이득 큰 것 먼저
            std::uint16_t k = fsm_order_[i];
            std::int32_t kg = gain[i];
            int j = i - 1;
            while (j >= 0 && gain[j] < kg) { fsm_order_[j + 1] = fsm_order_[j]; gain[j + 1] = gain[j]; --j; }
            fsm_order_[j + 1] = k; gain[j + 1] = kg;
        }
    }
    fsm_idx_ = 0;
    for (int i = 0; i < fsm_n_; ++i) fsm_done_[i] = 0;
    fsm_ready_ = true;
}

Status Deliberator::explore_fsm(const GoalSpec& g, const BeliefState& s, const RetrievedCases& c, Plan& out) {
    if (!fsm_ready_) build_fsm_order(g, s, c);
    const std::uint32_t avoid = g.constraints & kAvoidMask;
    pbfs(s, s.x, s.y, avoid, dm_);
    // 고정 순서에서 아직 볼 것이 남았고 갈 수 있는 다음 경유점
    // 순서는 고정이다. 끝난(다 봤거나 도착한) 경유점만 영구히 넘기고, 지금 못 가는 경유점은
    // 남겨 둔 채 다음 것을 본다 — 막힌 문이 열리면 다시 간다.
    bool leading = true;
    for (std::uint16_t j = fsm_idx_; j < fsm_n_; ++j) {
        const int w = fsm_order_[j];
        const int wx = w % s.w, wy = w / s.w;
        // 경유점 한 칸 안에 들어왔으면 '도착'이다. (이것이 없으면 경유점이 벽일 때 대신 고른 이웃
        // 칸 둘 사이를 영영 오간다: 실측 112스텝에 40칸만 훑었다)
        const int adx = wx > s.x ? wx - s.x : s.x - wx, ady = wy > s.y ? wy - s.y : s.y - wy;
        if (!fsm_done_[j] && (view_gain(s, wx, wy, c, avoid, nullptr, 0) <= 0 || (adx > ady ? adx : ady) <= 1))
            fsm_done_[j] = 1;
        if (fsm_done_[j]) {
            if (leading) fsm_idx_ = std::uint16_t(j + 1);
            continue;
        }
        leading = false;
        // 경유점 자체가 벽·위험이면 시야가 닿는 가장 가까운 갈 수 있는 칸으로 대신 간다
        int tx = wx, ty = wy;
        if (dm_.d[w] == kInf || w == cell(s.x, s.y, s.w)) {
            std::uint16_t bd = kInf;
            for (int dy = -1; dy <= 1; ++dy)
                for (int dx = -1; dx <= 1; ++dx) {
                    const int x = wx + dx, y = wy + dy;
                    if (x < 0 || y < 0 || x >= s.w || y >= s.h) continue;
                    const std::uint16_t d = dm_.d[cell(x, y, s.w)];
                    if (d < bd && d > 0) { bd = d; tx = x; ty = y; }
                }
            if (bd == kInf) continue;
        }
        pbfs(s, tx, ty, avoid, dm2_);
        const std::uint16_t a = first_step_to(s, dm2_, s.x, s.y);
        if (a == kActNone) continue;
        out.wx = std::uint8_t(tx); out.wy = std::uint8_t(ty);
        out.has_target = true;
        set_single(out, a, 0, 0, Decision::Act, R_EXPLORE_FSM, 0, RS_NONE);
        out.nodes = 1;
        return Status::Ok;
    }
    return Status::NoPlan;
}

Status Deliberator::explore_search(const GoalSpec& g, const BeliefState& s, const RetrievedCases& c, Plan& out) {
    if (force_timeout_ || p_->max_nodes == 0) {
        out.reason_code = RS_PLAN_TIMEOUT;
        return Status::Timeout;
    }
    const std::uint32_t avoid = g.constraints & kAvoidMask;
    pbfs(s, s.x, s.y, avoid, dm_);
    // 후보: 격자 경유점 중 도달 가능하고 볼 것이 남은 것. 이득/(거리+1) 상위 M 개.
    constexpr int M = 6;
    int cand[M];
    std::int32_t cscore[M];
    std::int32_t cgain[M];
    int nc = 0;
    const int stride = 2;
    for (int y = 0; y < s.h; y += stride)
        for (int x = 0; x < s.w; x += stride) {
            const int ci = cell(x, y, s.w);
            if (dm_.d[ci] == kInf || dm_.d[ci] == 0) continue;
            const std::int32_t gn = view_gain(s, x, y, c, avoid, nullptr, 0);
            if (gn <= 0) continue;
            const std::int32_t sc = gn * 16 / (std::int32_t(dm_.d[ci]) + 2);
            if (nc < M) { cand[nc] = ci; cscore[nc] = sc; cgain[nc] = gn; ++nc; }
            else {
                int wi = 0;
                for (int k = 1; k < M; ++k) if (cscore[k] < cscore[wi]) wi = k;
                if (sc > cscore[wi]) { cand[wi] = ci; cscore[wi] = sc; cgain[wi] = gn; }
            }
        }
    if (nc == 0) return Status::NoPlan;
    for (int i = 0; i < nc; ++i) pbfs(s, cand[i] % s.w, cand[i] / s.w, avoid, cand_dm_[i]);

    // 깊이 제한 탐색: 경유점 수열의 J = λt·거리 + λe·에너지 − λg·Σγ^k·이득(겹침 제거)
    // 노드 예산을 넘으면 그때까지의 최선을 쓴다(예산이 곧 시간 상한).
    const int depth = p_->depth < 1 ? 1 : (p_->depth > 3 ? 3 : p_->depth);
    std::int64_t best_j = INT64_MAX;
    int best_first = -1;
    std::uint16_t nodes = 0;
    std::uint16_t mark = 1;
    std::memset(stamp_, 0, sizeof(std::uint16_t) * std::size_t(s.w * s.h));

    // 명시적 스택(재귀 없이): 수열 seq[0..d)
    int seq[3];
    int it[3] = {0, 0, 0};
    int d = 0;
    for (;;) {
        if (it[d] >= nc) {
            if (d == 0) break;
            --d; ++it[d];
            continue;
        }
        // seq 에 이미 있는 후보는 건너뛴다
        bool dup = false;
        for (int k = 0; k < d; ++k) if (seq[k] == it[d]) dup = true;
        if (dup) { ++it[d]; continue; }
        if (nodes >= p_->max_nodes) break;
        ++nodes;
        seq[d] = it[d];
        // 이 수열을 값매김
        ++mark;
        if (mark == 0) { std::memset(stamp_, 0, sizeof(std::uint16_t) * std::size_t(s.w * s.h)); mark = 1; }
        std::int64_t T = 0, G = 0;
        std::int64_t gamma = 1000;
        bool ok = true;
        for (int k = 0; k <= d; ++k) {
            const int ci = cand[seq[k]];
            std::uint16_t dist = k == 0 ? dm_.d[ci] : cand_dm_[seq[k - 1]].d[ci];
            if (dist == kInf) { ok = false; break; }
            T += dist;
            G += gamma * view_gain(s, ci % s.w, ci / s.w, c, avoid, stamp_, mark) / 1000;
            gamma = gamma * 85 / 100;
        }
        if (ok) {
            // J 는 작을수록 좋다. 에너지는 거리에 비례하므로 λe 는 λt 에 흡수했다(같은 칸 이동).
            // 단위를 맞춘다: 한 칸 이동의 값 = 균등 사전확률에서 새 칸 다섯 개를 보는 값(5×250).
            // (첫 판은 이동 한 칸을 10000, 새 칸 하나를 250 으로 쳐서 '이득 있는 가장 가까운 곳'만
            //  골랐다 — 실측: 100스텝에 225칸 중 130칸만 훑고 헤맸다)
            constexpr std::int64_t kStepValue = 5 * (1000 / kNumZones);
            const std::int64_t J = std::int64_t(p_->lambda_t) * T * kStepValue - std::int64_t(p_->lambda_g) * G;
            // 수열 길이가 다르면 비교가 불공정하다 — 이득률(J/(d+1))로 비교
            const std::int64_t Jn = J / (d + 1);
            if (Jn < best_j) { best_j = Jn; best_first = seq[0]; }
        }
        if (d + 1 < depth && d + 1 < nc) { ++d; it[d] = 0; }
        else ++it[d];
    }
    (void)cgain;
    if (best_first < 0) {
        out.reason_code = RS_PLAN_TIMEOUT;
        return Status::Timeout;
    }
    const int tc = cand[best_first];
    const std::uint16_t a = first_step_to(s, cand_dm_[best_first], s.x, s.y);
    if (a == kActNone) return Status::NoPlan;
    out.wx = std::uint8_t(tc % s.w); out.wy = std::uint8_t(tc / s.w);
    out.has_target = true;
    set_single(out, a, 0, 0, Decision::Act, R_EXPLORE_SEARCH, 0, RS_NONE);
    out.nodes = nodes;
    out.cost = std::int32_t(best_j / 1000);
    return Status::Ok;
}

Status Deliberator::explore(const GoalSpec& g, const BeliefState& s, const RetrievedCases& c, Plan& out) {
    return mode_ == ExploreMode::Fsm ? explore_fsm(g, s, c, out) : explore_search(g, s, c, out);
}

Status Deliberator::plan(const GoalSpec& g, const BeliefState& s, const RetrievedCases& c, Plan& out) {
    std::memset(&out, 0, sizeof out);
    const std::uint32_t avoid = g.constraints & kAvoidMask;

    // 1. 관측을 현재로 못 쓰면: 다시 관측하거나 보류. 오래된 관측으로 움직이지 않는다.
    if (s.stale && !defect_) {
        if (s.stale_run <= p_->stale_observe_max)
            set_single(out, kObserve, 0, 0, Decision::Observe, R_OBSERVE_STALE, 0, RS_STALE_OBS);
        else
            set_single(out, kHold, 0, 0, Decision::Hold, R_HOLD_STALE, 0, RS_STALE_OBS);
        return Status::Ok;
    }
    if (s.actuator_fault) {
        set_single(out, kAbort, 0, 0, Decision::Abort, R_ABORT_FAULT, 0, RS_ACTUATOR_FAULT);
        return Status::Ok;
    }

    // 2. 확정된 가설이 있으면 선언(근거 = 그 가설의 마지막 증거)
    int best_c = -1, best_d = 0x7FFF;
    for (int k = 0; k < s.n_hyp; ++k) {
        const Hypothesis& h = s.hyp[k];
        const Match m = match(g, h);
        const int d = (h.x > s.x ? h.x - s.x : s.x - h.x) + (h.y > s.y ? h.y - s.y : s.y - h.y);
        if (m == Match::Confirmed) {
            set_single(out, kDeclare, h.x, h.y, Decision::Act,
                       p_->use_uncertainty ? R_DECLARE_CONFIRMED : R_DECLARE_FIRST_SIGHT, h.last_ev, RS_NONE);
            return Status::Ok;
        }
        if (m == Match::Candidate && h.observes_spent <= p_->observe_max && d < best_d) { best_c = k; best_d = d; }
    }

    // 3. 귀환: 집까지 거리 × 이동에너지 + 정책 여유 보다 배터리가 적으면 돌아간다
    bfs(s, s.home_x, s.home_y, avoid, true, dm2h_);
    const std::uint16_t dh = dm2h_.d[cell(s.x, s.y, s.w)];
    const std::int32_t need = (dh == kInf ? 0 : std::int32_t(dh)) * kMoveEnergy + p_->reserve_margin;
    if (returning_ || s.battery <= need + 2 * kMoveEnergy) {
        returning_ = true;
        if (dh == 0) {
            set_single(out, kAbort, 0, 0, Decision::Abort, R_ABORT_HOME_NO_TARGET, 0, RS_BATTERY_RESERVE);
            return Status::Ok;
        }
        const std::uint16_t a = first_step_to(s, dm2h_, s.x, s.y);
        set_single(out, a == kActNone ? std::uint16_t(kHold) : a, 0, 0, a == kActNone ? Decision::Hold : Decision::Act,
                   R_RETURN_BATTERY, 0, RS_BATTERY_RESERVE);
        out.wx = s.home_x; out.wy = s.home_y; out.has_target = true;
        return Status::Ok;
    }

    // 4. 후보(불확실) — 정책 그래프의 CANDIDATE_NEAR / CANDIDATE_FAR 규칙. 사용자가 '불확실하면
    //    건너뛰기' 라고 했으면 그것이 학습된 규칙보다 앞선다(사용자 제약 > 학습 규칙).
    if (best_c >= 0 && p_->use_uncertainty) {
        const Hypothesis& h = s.hyp[best_c];
        std::uint8_t act = best_d <= 1 ? p_->rule_action[RC_CANDIDATE_NEAR] : p_->rule_action[RC_CANDIDATE_FAR];
        if (g.constraints & kCondUncertainSkip) act = RA_SKIP;
        if (act == RA_OBSERVE) {
            set_single(out, kObserve, h.x, h.y, Decision::Observe, R_OBSERVE_UNCERTAIN, h.last_ev, RS_NONE);
            return Status::Ok;
        }
        if (act == RA_APPROACH) {
            bfs(s, h.x, h.y, avoid, true, dm_);
            const std::uint16_t a = first_step_to(s, dm_, s.x, s.y);
            if (a != kActNone && best_d > 0) {
                set_single(out, a, h.x, h.y, Decision::Act, R_APPROACH_CANDIDATE, h.last_ev, RS_NONE);
                out.wx = h.x; out.wy = h.y;
                out.has_target = true;
                return Status::Ok;
            }
            if (best_d <= 1) {   // 이미 붙어 있다 — 다가갈 데가 없으니 관측으로
                set_single(out, kObserve, h.x, h.y, Decision::Observe, R_OBSERVE_UNCERTAIN, h.last_ev, RS_NONE);
                return Status::Ok;
            }
        }
        // RA_SKIP: 후보를 두고 탐사를 잇는다(다시 지나가며 증거가 쌓이면 확정된다)
    }

    // 5. 탐사 + 막힘 판정. 근처에 장애물이 있으면 '장애물이 없다면 갔을 길'과 비교해,
    //    그 길이 막혔으면(없어지거나 4칸 이상 길어지면) BLOCKED 조건이 참이다.
    bool blocker_near = false;
    for (int dy = -kViewR; dy <= kViewR && !blocker_near; ++dy)
        for (int dx = -kViewR; dx <= kViewR; ++dx) {
            const int x = s.x + dx, y = s.y + dy;
            if (x >= 0 && y >= 0 && x < s.w && y < s.h && s.kind[cell(x, y, s.w)] == kBlocker) { blocker_near = true; break; }
        }
    bool blocked = false;
    std::uint16_t ideal_first = kActNone;
    if (blocker_near && mode_ == ExploreMode::Search) {
        Plan ideal{};
        ignore_blockers_ = true;
        const Status si = explore(g, s, c, ideal);
        ignore_blockers_ = false;
        if (si == Status::Ok && ideal.has_target) {
            ideal_first = ideal.actions[0].action_type;
            bfs(s, ideal.wx, ideal.wy, avoid, true, dm2_);
            const std::uint16_t d_real = dm2_.d[cell(s.x, s.y, s.w)];
            ignore_blockers_ = true;
            bfs_i(s, ideal.wx, ideal.wy, avoid, dm_);
            ignore_blockers_ = false;
            const std::uint16_t d_ideal = dm_.d[cell(s.x, s.y, s.w)];
            if (d_real == kInf || d_real >= d_ideal + 4) blocked = true;
        }
    } else if (blocker_near) {
        // FSM: 지금 경유점까지의 길을 같은 방식으로 본다(순서를 건드리지 않는다)
        if (fsm_ready_ && fsm_idx_ < fsm_n_) {
            const int w = fsm_order_[fsm_idx_];
            bfs(s, w % s.w, w / s.w, avoid, true, dm2_);
            const std::uint16_t d_real = dm2_.d[cell(s.x, s.y, s.w)];
            ignore_blockers_ = true;
            bfs_i(s, w % s.w, w / s.w, avoid, dm_);
            ignore_blockers_ = false;
            const std::uint16_t d_ideal = dm_.d[cell(s.x, s.y, s.w)];
            if (d_ideal != kInf && (d_real == kInf || d_real >= d_ideal + 4)) blocked = true;
        }
    }
    (void)ideal_first;
    if (blocked) {
        ++blocked_steps_;
        std::uint8_t act = p_->rule_action[RC_BLOCKED];
        std::uint8_t wait = p_->blocked_wait;
        if (g.constraints & kCondBlockedHold) { act = RA_HOLD; wait = 5; }      // 사용자가 '막히면 멈춰'
        else if (g.constraints & kCondBlockedStated) act = RA_REPLAN;             // 사용자가 '막히면 재계획'
        if (act == RA_HOLD && blocked_hold_run_ < wait) {
            ++blocked_hold_run_;
            set_single(out, kHold, 0, 0, Decision::Hold, R_HOLD_BLOCKED, 0, RS_CELL_BLOCKED);
            return Status::Ok;
        }
    } else {
        blocked_hold_run_ = 0;
    }

    Status st = explore(g, s, c, out);
    if (st == Status::NoPlan && !blocked) {
        // 볼 곳이 '없는' 것인가, 장애물 때문에 '못 가는' 것인가 — 후자면 BLOCKED 다
        Plan ideal{};
        ignore_blockers_ = true;
        const Status si = explore(g, s, c, ideal);
        ignore_blockers_ = false;
        if (si == Status::Ok) {
            blocked = true;
            ++blocked_steps_;
            std::uint8_t act = p_->rule_action[RC_BLOCKED];
            std::uint8_t wait = p_->blocked_wait;
            if (g.constraints & kCondBlockedHold) { act = RA_HOLD; wait = 5; }
            else if (g.constraints & kCondBlockedStated) act = RA_REPLAN;
            if (act == RA_HOLD && blocked_hold_run_ < wait) {
                ++blocked_hold_run_;
                set_single(out, kHold, 0, 0, Decision::Hold, R_HOLD_BLOCKED, 0, RS_CELL_BLOCKED);
                return Status::Ok;
            }
        }
    }
    if (st == Status::Timeout) {
        set_single(out, kHold, 0, 0, Decision::Hold, R_HOLD_PLAN_TIMEOUT, 0, RS_PLAN_TIMEOUT);
        return Status::Timeout;
    }
    if (st != Status::Ok) {
        // 지금은 갈 곳이 없다: 집 쪽으로 가되 매 스텝 다시 본다(막힌 길이 열리면 탐사를 잇는다).
        // 귀환을 '고정'하는 것은 배터리 때뿐이다. (첫 판은 여기서도 고정해서, 문간 사람 한 번에
        // 임무를 영영 접었다 — 실측: 문 막힘 환경 60판 중 42판이 28스텝 만에 집으로 갔다)
        if (dh == 0 || dh == kInf) {
            set_single(out, kAbort, 0, 0, Decision::Abort, R_ABORT_NO_PLAN, 0, RS_NO_PLAN);
            return Status::NoPlan;
        }
        const std::uint16_t a = first_step_to(s, dm2h_, s.x, s.y);
        set_single(out, a == kActNone ? std::uint16_t(kHold) : a, 0, 0, Decision::Replan, R_ABORT_NO_PLAN, 0, RS_NO_PLAN);
        return Status::Ok;
    }
    if (blocked) { out.decision = Decision::Replan; out.rule_id = R_REPLAN_BLOCKED; }
    return Status::Ok;
}

void Deliberator::pbfs(const BeliefState& b, int sx, int sy, std::uint32_t avoid, DistMap& out) const {
    if (defect_) {   // 결함 주입: 위험·장애물도 지나갈 수 있다고 본다(피할 구역·벽만 막는다)
        const int n = b.w * b.h;
        for (int i = 0; i < n; ++i) out.d[i] = kInf;
        std::uint16_t q[kMaxCells];
        int qh = 0, qt = 0;
        out.d[cell(sx, sy, b.w)] = 0;
        q[qt++] = std::uint16_t(cell(sx, sy, b.w));
        static const int DX[4] = {0, 1, 0, -1}, DY[4] = {-1, 0, 1, 0};
        while (qh < qt) {
            const int c = q[qh++];
            for (int k = 0; k < 4; ++k) {
                const int nx = c % b.w + DX[k], ny = c / b.w + DY[k];
                if (nx < 0 || ny < 0 || nx >= b.w || ny >= b.h) continue;
                const int nc = cell(nx, ny, b.w);
                if (out.d[nc] != kInf || b.kind[nc] == kWall) continue;
                out.d[nc] = std::uint16_t(out.d[c] + 1);
                q[qt++] = std::uint16_t(nc);
            }
        }
        return;
    }
    if (ignore_blockers_) { bfs_i(b, sx, sy, avoid, out); return; }
    if (p_->blocker_ttl == 0) { bfs(b, sx, sy, avoid, true, out); return; }
    // 장애물 기억 수명: 오래 못 본 장애물 칸은 지나갈 수 있을지 모른다고 본다
    const int n = b.w * b.h;
    for (int i = 0; i < n; ++i) out.d[i] = kInf;
    std::uint16_t q[kMaxCells];
    int qh = 0, qt = 0;
    out.d[cell(sx, sy, b.w)] = 0;
    q[qt++] = std::uint16_t(cell(sx, sy, b.w));
    static const int DX[4] = {0, 1, 0, -1}, DY[4] = {-1, 0, 1, 0};
    while (qh < qt) {
        const int c = q[qh++];
        for (int k = 0; k < 4; ++k) {
            const int nx = c % b.w + DX[k], ny = c / b.w + DY[k];
            if (nx < 0 || ny < 0 || nx >= b.w || ny >= b.h) continue;
            const int nc = cell(nx, ny, b.w);
            const std::uint8_t kd = b.kind[nc];
            if (out.d[nc] != kInf || kd == kWall || kd == kHazard || (avoid & (1u << b.zone[nc]))) continue;
            if (kd == kBlocker && Tick(b.seen_tick[nc]) + p_->blocker_ttl > b.now + 1) continue;   // 아직 싱싱한 장애물
            out.d[nc] = std::uint16_t(out.d[c] + 1);
            q[qt++] = std::uint16_t(nc);
        }
    }
}

// 장애물을 없는 셈 친 거리(막힘 판정 전용). 벽·위험·피할 구역은 그대로 막는다.
void Deliberator::bfs_i(const BeliefState& b, int sx, int sy, std::uint32_t avoid, DistMap& out) const {
    const int n = b.w * b.h;
    for (int i = 0; i < n; ++i) out.d[i] = kInf;
    std::uint16_t q[kMaxCells];
    int qh = 0, qt = 0;
    const int st = cell(sx, sy, b.w);
    out.d[st] = 0;
    q[qt++] = std::uint16_t(st);
    static const int DX[4] = {0, 1, 0, -1}, DY[4] = {-1, 0, 1, 0};
    while (qh < qt) {
        const int c = q[qh++];
        const int x = c % b.w, y = c / b.w;
        for (int k = 0; k < 4; ++k) {
            const int nx = x + DX[k], ny = y + DY[k];
            if (nx < 0 || ny < 0 || nx >= b.w || ny >= b.h) continue;
            const int nc = cell(nx, ny, b.w);
            const std::uint8_t kd = b.kind[nc];
            if (out.d[nc] != kInf || kd == kWall || kd == kHazard || (avoid & (1u << b.zone[nc]))) continue;
            out.d[nc] = std::uint16_t(out.d[c] + 1);
            q[qt++] = std::uint16_t(nc);
        }
    }
}

}  // namespace walp
