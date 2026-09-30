// 격자 유틸 · C. 상태 추정 · C. 사례 검색 · D. 안전 감독
#include "walp/core.hpp"

#include <cstring>

namespace walp {

void action_delta(std::uint16_t a, int& dx, int& dy) {
    dx = dy = 0;
    switch (a) {
    case kMoveN: dy = -1; break;
    case kMoveS: dy = 1; break;
    case kMoveE: dx = 1; break;
    case kMoveW: dx = -1; break;
    default: break;
    }
}

std::uint16_t move_toward(int fx, int fy, int tx, int ty) {
    if (tx == fx + 1 && ty == fy) return kMoveE;
    if (tx == fx - 1 && ty == fy) return kMoveW;
    if (ty == fy + 1 && tx == fx) return kMoveS;
    if (ty == fy - 1 && tx == fx) return kMoveN;
    return kActNone;
}

static bool passable(const BeliefState& b, int c, std::uint32_t avoid, bool allow_unknown) {
    const std::uint8_t k = b.kind[c];
    if (k == kWall || k == kHazard || k == kBlocker) return false;
    if (k == kUnknown && !allow_unknown) return false;
    if (avoid & (1u << b.zone[c])) return false;
    return true;
}

void bfs(const BeliefState& b, int sx, int sy, std::uint32_t avoid, bool allow_unknown, DistMap& out) {
    const int n = b.w * b.h;
    for (int i = 0; i < n; ++i) out.d[i] = kInf;
    if (sx < 0 || sy < 0 || sx >= b.w || sy >= b.h) return;
    std::uint16_t q[kMaxCells];
    int qh = 0, qt = 0;
    const int s = cell(sx, sy, b.w);
    out.d[s] = 0;
    q[qt++] = std::uint16_t(s);
    static const int DX[4] = {0, 1, 0, -1}, DY[4] = {-1, 0, 1, 0};
    while (qh < qt) {
        const int c = q[qh++];
        const int x = c % b.w, y = c / b.w;
        for (int k = 0; k < 4; ++k) {
            const int nx = x + DX[k], ny = y + DY[k];
            if (nx < 0 || ny < 0 || nx >= b.w || ny >= b.h) continue;
            const int nc = cell(nx, ny, b.w);
            if (out.d[nc] != kInf || !passable(b, nc, avoid, allow_unknown)) continue;
            out.d[nc] = std::uint16_t(out.d[c] + 1);
            q[qt++] = std::uint16_t(nc);
        }
    }
}

std::uint16_t first_step_to(const BeliefState& b, const DistMap& from_target, int sx, int sy) {
    // from_target 은 목표에서 퍼진 거리지도. 지금 자리의 이웃 중 거리가 가장 작은 곳으로.
    static const int DX[4] = {0, 1, 0, -1}, DY[4] = {-1, 0, 1, 0};
    std::uint16_t best = kInf;
    std::uint16_t act = kActNone;
    for (int k = 0; k < 4; ++k) {
        const int nx = sx + DX[k], ny = sy + DY[k];
        if (nx < 0 || ny < 0 || nx >= b.w || ny >= b.h) continue;
        const std::uint16_t d = from_target.d[cell(nx, ny, b.w)];
        if (d < best) { best = d; act = move_toward(sx, sy, nx, ny); }
    }
    return act;
}

// ---------------------------------------------------------------- 상태 추정
void StateEstimator::init(BeliefState& s, const MapHint& m) const {
    std::memset(&s, 0, sizeof s);
    s.w = m.w; s.h = m.h; s.env_type = m.env_type;
    s.home_x = m.home_x; s.home_y = m.home_y;
    s.x = m.home_x; s.y = m.home_y;
    for (int i = 0; i < m.w * m.h; ++i) {
        s.kind[i] = m.wall[i] ? kWall : kUnknown;
        s.zone[i] = m.zone[i];
    }
    s.next_ev = 1;
}

Status StateEstimator::update(const Observation& o, BeliefState& s) {
    // 관측이 없거나(누락) 너무 오래됐으면(지연) 현재 상태로 쓰지 않는다 — 고장 주입 표의 첫 줄.
    // 나이 창 안이어도 이미 받은 관측보다 옛것이면(순서 역전) 받지 않는다 — 받으면 상태가 과거로 돌아간다.
    // (WebAuthn signCount 와 같은 단조 검사. 비교는 tick 으로 한다: 지연 관측도 obs_id 는 새로 받는다.)
    if (!o.valid || o.tick + max_age_ < s.now || o.tick < s.last_fresh_tick) {
        s.stale = true;
        ++s.stale_run;
        return Status::NoEvidence;
    }
    s.stale = false;
    s.stale_run = 0;
    s.last_obs_id = o.obs_id;
    // 행동 결과 감시: 움직이라 했는데 자리가 그대로면(또는 엉뚱하면) 액추에이터를 의심한다
    if (s.expect_move) {
        const int ex = s.x + s.expect_dx, ey = s.y + s.expect_dy;
        if (o.x != ex || o.y != ey) {
            if (++s.move_mismatch_run >= 3) s.actuator_fault = true;
        } else {
            s.move_mismatch_run = 0;
        }
        s.expect_move = false;
    }
    s.x = o.x; s.y = o.y;
    s.battery = o.battery;
    s.last_fresh_tick = o.tick;
    for (int dy = -kViewR; dy <= kViewR; ++dy)
        for (int dx = -kViewR; dx <= kViewR; ++dx) {
            const int x = o.x + dx, y = o.y + dy;
            if (x < 0 || y < 0 || x >= s.w || y >= s.h) continue;
            const int vi = (dy + kViewR) * (2 * kViewR + 1) + (dx + kViewR);
            const int c = cell(x, y, s.w);
            if (s.kind[c] == kWall) continue;   // 평면도의 벽은 믿는다
            s.kind[c] = o.view_kind[vi];
            s.seen_tick[c] = std::uint16_t(o.tick + 1 < 0xFFFF ? o.tick + 1 : 0xFFFF);
            s.scanned[c] = 1;
        }
    for (int i = 0; i < o.n_det && i < kMaxDet; ++i) {
        const Detection& d = o.det[i];
        if (d.x >= s.w || d.y >= s.h || d.type >= kNumObj || d.color >= kNumColor || d.brand >= kNumBrand) continue;
        int h = -1;
        for (int k = 0; k < s.n_hyp; ++k)
            if (s.hyp[k].x == d.x && s.hyp[k].y == d.y) { h = k; break; }
        if (h < 0) {
            if (s.n_hyp < kMaxHyp) h = s.n_hyp++;
            else {  // 가장 적게 본(그리고 목표가 아니라고 판정된) 가설 자리를 쓴다
                int worst = 0;
                for (int k = 1; k < kMaxHyp; ++k)
                    if (s.hyp[k].dismissed > s.hyp[worst].dismissed ||
                        (s.hyp[k].dismissed == s.hyp[worst].dismissed && s.hyp[k].n < s.hyp[worst].n))
                        worst = k;
                h = worst;
            }
            std::memset(&s.hyp[h], 0, sizeof(Hypothesis));
            s.hyp[h].x = d.x; s.hyp[h].y = d.y;
            s.hyp[h].first_ev = s.next_ev;
        }
        Hypothesis& H = s.hyp[h];
        ++H.v_type[d.type];
        ++H.v_color[d.color];
        ++H.v_brand[d.brand];
        ++H.n;
        H.last_ev = s.next_ev++;
    }
    return Status::Ok;
}

// ---------------------------------------------------------------- 사례 검색
void uniform_prior(RetrievedCases& out) {
    out.n = 0;
    out.used = false;
    for (int z = 0; z < kNumZones; ++z) out.zone_prior_pm[z] = 1000 / kNumZones;
}

Status CaseRetriever::retrieve(const GoalSpec& goal, const BeliefState& state, RetrievedCases& out) {
    uniform_prior(out);
    if (!cb_) return Status::NoEvidence;
    // 1) 작업 유형 2) 환경 호환 3) 목표 호환 4) 거리 정렬 5) 없으면 빈 결과
    std::uint16_t idx[kMaxCases];
    std::uint16_t dist[kMaxCases];
    int m = 0;
    for (int i = 0; i < cb_->n && i < kMaxCases; ++i) {
        const CaseRecord& c = cb_->c[i];
        if (c.task_type != goal.goal_type) continue;
        if (c.environment_type == 0 || c.environment_type != state.env_type) continue;
        if (c.outcome_code != 1) continue;             // 성공 사례만 사전확률에 쓴다
        if (c.target_type != goal.target_type) continue;
        // 거리: 요구 속성이 같을수록 가깝다. 사람이 정한 가중치다(편향의 자리).
        std::uint16_t d = 0;
        if (attr_color(c.required_attributes) != attr_color(goal.required_attributes)) d += 2;
        if (attr_brand(c.required_attributes) != attr_brand(goal.required_attributes)) d += 1;
        idx[m] = std::uint16_t(i);
        dist[m] = d;
        ++m;
    }
    if (m == 0) return Status::NoEvidence;
    // 삽입 정렬(거리, 그다음 사례 ID) — 결정론
    for (int i = 1; i < m; ++i) {
        std::uint16_t ki = idx[i], kd = dist[i];
        int j = i - 1;
        while (j >= 0 && (dist[j] > kd || (dist[j] == kd && cb_->c[idx[j]].id > cb_->c[ki].id))) {
            idx[j + 1] = idx[j]; dist[j + 1] = dist[j]; --j;
        }
        idx[j + 1] = ki; dist[j + 1] = kd;
    }
    const int k = m < kMaxRetrieved ? m : kMaxRetrieved;
    std::uint32_t cnt[kNumZones] = {1, 1, 1, 1};   // 라플라스 평활
    for (int i = 0; i < k; ++i) {
        const CaseRecord& c = cb_->c[idx[i]];
        out.ids[i] = c.id;
        if (c.event_type < kNumZones) ++cnt[c.event_type];
    }
    out.n = std::uint16_t(k);
    std::uint32_t tot = 0;
    for (int z = 0; z < kNumZones; ++z) tot += cnt[z];
    for (int z = 0; z < kNumZones; ++z) out.zone_prior_pm[z] = std::uint16_t(cnt[z] * 1000 / tot);
    out.used = true;
    return Status::Ok;
}

// ---------------------------------------------------------------- 안전 감독
// 계획 점수와 무관하게 여기서 막는다. 이 층의 한계값은 정책 학습이 못 바꾼다(SafetyLimits).
bool SafetySupervisor::authorize(const ActionRequest& a, const SafetyState& s, std::uint16_t& reason) {
    reason = RS_NONE;
    if (!enabled_) return true;   // ablation(시뮬 전용)
    const BeliefState& b = *s.belief;
    switch (a.action_type) {
    case kAbort:
        return true;
    case kObserve:
    case kHold: {
        // 보류·관측도 에너지를 쓴다. 집에 있으면 무엇이든 괜찮다(좌초가 아니다).
        if (b.x == b.home_x && b.y == b.home_y) return true;
        const std::int32_t cost = a.action_type == kObserve ? kObserveEnergy : kHoldEnergy;
        home_dist(b, s.constraints & kAvoidMask);
        const std::uint16_t dh = home_.d[cell(b.x, b.y, b.w)];
        const std::int32_t need = (dh == kInf ? 0 : std::int32_t(dh) * kMoveEnergy) + lim_.hard_margin + lim_.wait_reserve;
        if (b.battery - cost < need) { reason = RS_BATTERY_RESERVE; return false; }
        return true;
    }
    case kDeclare: {
        // 근거가 없는 확정은 허가하지 않는다: 그 칸에 가설이 있고 증거 ID 가 맞아야 한다
        for (int k = 0; k < b.n_hyp; ++k)
            if (b.hyp[k].x == a.arg0 && b.hyp[k].y == a.arg1 && b.hyp[k].n > 0) return true;
        reason = RS_NO_EVIDENCE;
        return false;
    }
    case kMoveN: case kMoveE: case kMoveS: case kMoveW: {
        if (b.actuator_fault) { reason = RS_ACTUATOR_FAULT; return false; }
        if (b.stale) { reason = RS_STALE_OBS; return false; }
        int dx, dy;
        action_delta(a.action_type, dx, dy);
        const int nx = b.x + dx, ny = b.y + dy;
        if (nx < 0 || ny < 0 || nx >= b.w || ny >= b.h) { reason = RS_OUT_OF_BOUNDS; return false; }
        const int c = cell(nx, ny, b.w);
        const std::uint8_t k = b.kind[c];
        if (k == kHazard) { reason = RS_HAZARD; return false; }
        if (k == kWall || k == kBlocker) { reason = RS_CELL_BLOCKED; return false; }
        if (k == kUnknown) { reason = RS_CELL_UNKNOWN; return false; }
        if (b.seen_tick[c] == 0 || Tick(b.seen_tick[c]) - 1 + lim_.max_obs_age < s.now) { reason = RS_STALE_OBS; return false; }
        if (s.constraints & (1u << b.zone[c])) { reason = RS_AVOID_ZONE; return false; }
        // 귀환 에너지: 다음 칸에서 집까지(알려진 자유칸 또는 모르는 칸 경유 — 낙관 아닌 하한은
        // 알려진 칸만. 모르는 칸을 막으면 탐사 초반 귀환로가 없어져 늘 거부되므로, 여기서는
        // 벽·위험·장애물만 막은 최단거리를 하한으로 쓰고 하드 여유를 얹는다)
        home_dist(b, s.constraints & kAvoidMask);
        std::uint16_t dn = home_.d[c];
        const std::uint16_t dh = home_.d[cell(b.x, b.y, b.w)];
        if (dn == kInf && dh != kInf) dn = std::uint16_t(dh + 1);   // 아는 길 밖으로 한 걸음 — 비관적으로 한 칸 더
        if (dn == kInf) { reason = RS_CELL_BLOCKED; return false; }
        const std::int32_t after = b.battery - kMoveEnergy;
        const std::int32_t need = std::int32_t(dn) * kMoveEnergy + lim_.hard_margin + lim_.wait_reserve;
        if (after < need && !(dn < dh && after >= std::int32_t(dn) * kMoveEnergy)) {
            reason = RS_BATTERY_RESERVE;
            return false;
        }
        return true;
    }
    default:
        reason = RS_UNKNOWN_ACTION;
        return false;
    }
}

// 귀환 비용은 **아는 자유칸만** 지나는 길로 잰다(비관). 모르는 칸을 지나가게 치면 실제 길보다 짧게
// 잡혀 하한이 너무 늦게 걸린다 — 첫 판이 그래서 좌초를 못 막았다(실측: 800판 중 2판).
// 나온 길은 늘 아는 칸이므로 보통 이 길이 있다. 없으면(시작 직후 등) 모르는 칸을 허용한다.
void SafetySupervisor::home_dist(const BeliefState& b, std::uint32_t avoid) {
    bfs(b, b.home_x, b.home_y, avoid, false, home_);
    if (home_.d[cell(b.x, b.y, b.w)] == kInf) bfs(b, b.home_x, b.home_y, avoid, true, home_);
}

ActionRequest SafetySupervisor::fallback(const SafetyState& s) {
    // 불변식: **집 밖에서 배터리가 0 에 닿지 않는다.** 귀환을 보장할 수는 없다 — 넘겨받은 뒤에 아는
    // 길이 사람에게 막히면 우회로는 훨씬 길다(실측: 269 에서 넘겨받았는데 남쪽 문으로 돌다 좌초).
    // 그래서 귀환이 에너지로 아직 가능할 때만 걷고, 불가능해지면 그 자리에서 전원을 남긴 채 멈춘다.
    const BeliefState& b = *s.belief;
    if (b.x == b.home_x && b.y == b.home_y) return ActionRequest{kAbort, 0, 0, 0};
    home_dist(b, s.constraints & kAvoidMask);
    const std::uint16_t dh = home_.d[cell(b.x, b.y, b.w)];
    if (dh == kInf || b.battery < std::int32_t(dh) * kMoveEnergy) return ActionRequest{kAbort, 0, 0, 0};
    static const int DX[4] = {0, 1, 0, -1}, DY[4] = {-1, 0, 1, 0};
    for (int k = 0; k < 4; ++k) {
        const int nx = b.x + DX[k], ny = b.y + DY[k];
        if (nx < 0 || ny < 0 || nx >= b.w || ny >= b.h) continue;
        const int c = cell(nx, ny, b.w);
        if (home_.d[c] >= dh || b.kind[c] != kFree || b.stale || b.actuator_fault) continue;
        if (b.seen_tick[c] == 0 || Tick(b.seen_tick[c]) - 1 + lim_.max_obs_age < s.now) continue;
        if (s.constraints & (1u << b.zone[c])) continue;
        return ActionRequest{move_toward(b.x, b.y, nx, ny), 0, 0, 1};
    }
    // 집 쪽 한 걸음이 지금은 막혔다: 기다려도 귀환이 여전히 가능할 만큼만 기다린다
    if (b.battery - kHoldEnergy >= std::int32_t(dh) * kMoveEnergy + lim_.hard_margin) return ActionRequest{kHold, 0, 0, 1};
    return ActionRequest{kAbort, 0, 0, 0};
}

}  // namespace walp
