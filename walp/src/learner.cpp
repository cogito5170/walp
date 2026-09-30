// C. 정책 학습 — 후보만 만든다. 승인은 여기서 하지 않는다(명세 §4.4).
//
// 실패 분류 → 후보 규칙. 분류는 **틀릴 수 있다**: 시간 초과가 증거 요구가 높아서인지,
// 탐사가 느려서인지, 센서가 고장나서인지 이 층은 모른다. 그래서 한 실패에서 서로 다른
// 가설의 후보를 여러 개 낸다. 어느 것이 맞는지는 검증기(회귀 시험)가 정한다 — 무검증
// 갱신은 그중 첫 번째를 그대로 믿는다. H3 는 이 차이를 잰다.
#include "walp/core.hpp"

#include <cstring>

namespace walp {

PolicyParams default_params_raw() {
    PolicyParams p{};
    p.k_confirm = 3;
    p.theta_pct = 60;
    p.observe_max = 4;
    p.stale_observe_max = 3;
    p.lambda_t = 100;
    p.lambda_g = 100;
    p.reserve_margin = 60;
    p.prior_weight_pct = 80;
    p.max_nodes = 100;     // 명세 §4.3: 깊이 3, 노드 100 이하
    p.depth = 3;
    p.use_uncertainty = 1;
    p.rule_action[RC_STALE] = RA_OBSERVE;
    p.rule_action[RC_ACT_FAULT] = RA_ABORT;
    p.rule_action[RC_CONFIRMED] = RA_DECLARE;
    p.rule_action[RC_BATTERY_LOW] = RA_RETURN;
    p.rule_action[RC_CANDIDATE_NEAR] = RA_OBSERVE;
    p.rule_action[RC_CANDIDATE_FAR] = RA_APPROACH;
    p.rule_action[RC_BLOCKED] = RA_REPLAN;
    p.rule_action[RC_DEFAULT] = RA_EXPLORE;
    p.blocked_wait = 4;
    p.blocker_ttl = 0;
    return p;
}

PolicyParams default_params() { return default_params_raw(); }

// 정적 반례 검사: 후보가 이 범위를 벗어나면 시험 전에 버린다.
// 안전층의 한계(SafetyLimits)는 여기 없다 — 학습은 그것을 아예 못 만진다.
bool params_invariants_ok(const PolicyParams& p, const char** why) {
    const char* w = nullptr;
    if (p.k_confirm < 1 || p.k_confirm > 8) w = "k_confirm out of [1,8]";
    else if (p.theta_pct < 50 || p.theta_pct > 100) w = "theta below majority";
    else if (p.observe_max > 12) w = "observe_max > 12";
    else if (p.stale_observe_max > 10) w = "stale_observe_max > 10";
    else if (p.reserve_margin < 0 || p.reserve_margin > 400) w = "reserve_margin out of [0,400]";
    else if (p.prior_weight_pct > 100) w = "prior_weight > 100";
    else if (p.max_nodes > 400) w = "max_nodes > 400 (WCET budget)";
    else if (p.depth < 1 || p.depth > 3) w = "depth out of [1,3]";
    else if (p.lambda_t < 10 || p.lambda_t > 1000 || p.lambda_g < 10 || p.lambda_g > 1000) w = "lambda out of range";
    else if (p.use_uncertainty != 1) w = "uncertainty gate cannot be learned off";
    else if (p.blocked_wait > 20) w = "blocked_wait > 20";
    else if (p.blocker_ttl != 0 && p.blocker_ttl < 5) w = "blocker_ttl < 5 (re-probing too eagerly)";
    if (!w) {
        // 정책 그래프 타입 검사: 조건마다 허용된 행동만. 잠긴(안전) 규칙은 기본값에서 못 벗어난다.
        const PolicyParams d = default_params_raw();
        for (int c = 0; c < RC_COUNT && !w; ++c) {
            const std::uint8_t a = p.rule_action[c];
            if (a == 0 || a >= RA_COUNT || !(kRuleAllowed[c] & (1u << a))) w = "rule action not allowed for condition";
            else if (kRuleLocked[c] && a != d.rule_action[c]) w = "locked safety rule changed";
        }
    }
    if (why) *why = w ? w : "ok";
    return w == nullptr;
}

enum Change : std::uint16_t {
    CH_K_UP = 1, CH_K_DOWN, CH_THETA_UP, CH_THETA_DOWN, CH_OBSMAX_DOWN, CH_OBSMAX_UP,
    CH_RESERVE_UP, CH_RESERVE_DOWN, CH_PRIOR_UP, CH_PRIOR_DOWN, CH_GAIN_UP, CH_STALE_UP,
    // 규칙 템플릿(정책 그래프 간선 바꾸기)
    CH_RULE_BLOCKED_HOLD, CH_RULE_BLOCKED_REPLAN, CH_RULE_NEAR_APPROACH, CH_RULE_NEAR_SKIP, CH_RULE_FAR_SKIP,
    CH_RULE_NEAR_OBSERVE, CH_WAIT_UP, CH_TTL_ON, CH_TTL_DOWN, CH_TTL_OFF,
    // 한 걸음 이웃에서만 쓰는 것(분류기가 안 내는 방향)
    CH_RULE_FAR_APPROACH, CH_GAIN_DOWN, CH_LAMBDA_T_UP, CH_LAMBDA_T_DOWN, CH_WAIT_DOWN, CH_STALE_DOWN
};

Status PolicyLearner::propose(const EpisodeRecord& e, const PolicyParams& cur, RuleCandidate* out, int max_out,
                              int& n_out) {
    n_out = 0;
    auto add = [&](PolicyParams p, Change ch) {
        if (n_out < max_out) out[n_out++] = RuleCandidate{p, e.outcome, ch};
    };
    PolicyParams p = cur;
    switch (e.outcome) {
    case O_FALSE_DECLARE:
        p = cur; if (p.k_confirm < 8) ++p.k_confirm; add(p, CH_K_UP);
        p = cur; if (p.theta_pct <= 90) p.theta_pct += 10; add(p, CH_THETA_UP);
        p = cur;
        if (cur.rule_action[RC_CANDIDATE_NEAR] != RA_OBSERVE) { p.rule_action[RC_CANDIDATE_NEAR] = RA_OBSERVE; add(p, CH_RULE_NEAR_OBSERVE); }
        break;
    case O_TIMEOUT:
    case O_ABORT_NO_TARGET: {
        const bool observe_heavy = e.observes * 4 > e.steps;          // 재관측에 시간을 많이 썼다
        const bool low_cover = e.scanned_cells * 10 < e.free_cells * 8; // 80% 도 못 훑었다
        const bool blocked_heavy = e.blocked_steps * 10 > e.steps;      // 10% 넘게 막혀 있었다
        // 템플릿 'IF blocked THEN <행동>': 막힘이 잦으면 재계획↔대기를 바꿔 본다.
        // 원인이 정말 막힘인지(아니면 센서·지도 오류인지) 이 층은 모른다 — 검증기가 가린다.
        if (blocked_heavy) {
            // 템플릿 'IF 길이 장애물로 막힘 THEN 오래된 장애물 기억을 잊고 다시 가 본다'
            p = cur;
            if (cur.blocker_ttl == 0) { p.blocker_ttl = 20; add(p, CH_TTL_ON); }
            else if (cur.blocker_ttl > 10) { p.blocker_ttl = std::uint8_t(cur.blocker_ttl / 2); add(p, CH_TTL_DOWN); }
            p = cur;
            if (cur.rule_action[RC_BLOCKED] == RA_REPLAN) { p.rule_action[RC_BLOCKED] = RA_HOLD; add(p, CH_RULE_BLOCKED_HOLD); }
            else if (cur.blocked_wait < 16) { p.blocked_wait = std::uint8_t(cur.blocked_wait + 4); add(p, CH_WAIT_UP); }
            p = cur;
            if (cur.rule_action[RC_BLOCKED] == RA_HOLD) { p.rule_action[RC_BLOCKED] = RA_REPLAN; add(p, CH_RULE_BLOCKED_REPLAN); }
        }
        // 가설이 여럿이라 첫 후보를 '그럴듯해 보이는 쪽'으로 둔다 — 무검증 갱신은 이것을 믿는다
        if (observe_heavy) {
            p = cur; if (p.k_confirm > 1) --p.k_confirm; add(p, CH_K_DOWN);
            p = cur;
            if (cur.rule_action[RC_CANDIDATE_NEAR] == RA_OBSERVE) { p.rule_action[RC_CANDIDATE_NEAR] = RA_SKIP; add(p, CH_RULE_NEAR_SKIP); }
            p = cur; if (p.observe_max > 1) p.observe_max -= 1; add(p, CH_OBSMAX_DOWN);
            p = cur; if (p.theta_pct >= 60) p.theta_pct -= 10; add(p, CH_THETA_DOWN);
        }
        if (low_cover) {
            p = cur; p.reserve_margin = p.reserve_margin >= 30 ? p.reserve_margin - 30 : 0; add(p, CH_RESERVE_DOWN);
            p = cur; if (p.prior_weight_pct <= 90) p.prior_weight_pct += 10; add(p, CH_PRIOR_UP);
            p = cur; if (p.lambda_g <= 900) p.lambda_g += 100; add(p, CH_GAIN_UP);
        }
        if (!observe_heavy && !low_cover) {
            // 다 훑었는데 못 찾았다: 사전확률이 틀렸거나 목표를 지나쳤다
            p = cur; if (p.prior_weight_pct >= 20) p.prior_weight_pct -= 20; add(p, CH_PRIOR_DOWN);
            p = cur; if (p.k_confirm > 1) --p.k_confirm; add(p, CH_K_DOWN);
        }
        if (e.stale_steps * 5 > e.steps) {
            p = cur; if (p.stale_observe_max < 10) p.stale_observe_max += 2; add(p, CH_STALE_UP);
        }
        break;
    }
    case O_STRANDED:
    case O_ABORT_SAFETY:
        p = cur; p.reserve_margin += 40; add(p, CH_RESERVE_UP);
        p = cur; if (p.observe_max > 1) --p.observe_max; add(p, CH_OBSMAX_DOWN);
        break;
    default:
        break;
    }
    return n_out ? Status::Ok : Status::NoEvidence;
}

// 한 걸음 이웃 전부 — 실패 분류를 **거치지 않는다.** 반사실 진단(SE 원리: 델타 디버깅)은 "어느 한 가지를
// 바꾸면 이 반례가 풀리나" 를 실제로 돌려 보고 정한다. 분류기(propose)는 원인을 추측하고, 이것은 추측하지 않는다.
// 불변식을 어기거나 현재와 같은 후보는 뺀다. 힙을 안 쓴다.
static bool params_equal(const PolicyParams& a, const PolicyParams& b) {
    if (a.k_confirm != b.k_confirm || a.theta_pct != b.theta_pct || a.observe_max != b.observe_max ||
        a.stale_observe_max != b.stale_observe_max || a.lambda_t != b.lambda_t || a.lambda_g != b.lambda_g ||
        a.reserve_margin != b.reserve_margin || a.prior_weight_pct != b.prior_weight_pct || a.max_nodes != b.max_nodes ||
        a.depth != b.depth || a.use_uncertainty != b.use_uncertainty || a.blocked_wait != b.blocked_wait ||
        a.blocker_ttl != b.blocker_ttl)
        return false;
    for (int c = 0; c < RC_COUNT; ++c) if (a.rule_action[c] != b.rule_action[c]) return false;
    return true;
}

// scale: 수치 걸음의 배수(1·2·4 …). 배수가 1 보다 크면 수치 이웃만 내고(규칙 바꾸기는 배수가 없다) 변화 코드에
// 32 × log2(scale) 을 더해 구분한다. 평탄한 구간을 한 걸음으로 못 넘을 때 쓴다(가변 이웃 탐색).
int policy_neighbors(const PolicyParams& cur, RuleCandidate* out, int max_out, int scale) {
    int n = 0;
    const int sc = scale < 1 ? 1 : scale;
    int lg = 0;
    for (int x = sc; x > 1; x >>= 1) ++lg;
    auto add = [&](const PolicyParams& p, Change ch) {
        if (n >= max_out || params_equal(p, cur) || !params_invariants_ok(p, nullptr)) return;
        out[n++] = RuleCandidate{p, 0, std::uint16_t(ch + 32 * lg)};
    };
    PolicyParams p;
    p = cur; p.k_confirm = std::uint8_t(p.k_confirm + sc); add(p, CH_K_UP);
    p = cur; if (p.k_confirm > sc) { p.k_confirm = std::uint8_t(p.k_confirm - sc); add(p, CH_K_DOWN); }
    p = cur; p.theta_pct = std::uint8_t(p.theta_pct + 10 * sc); add(p, CH_THETA_UP);
    p = cur; if (p.theta_pct >= 50 + 10 * sc) { p.theta_pct = std::uint8_t(p.theta_pct - 10 * sc); add(p, CH_THETA_DOWN); }
    p = cur; if (p.observe_max > sc) { p.observe_max = std::uint8_t(p.observe_max - sc); add(p, CH_OBSMAX_DOWN); }
    p = cur; p.observe_max = std::uint8_t(p.observe_max + sc); add(p, CH_OBSMAX_UP);
    p = cur; p.reserve_margin += 40 * sc; add(p, CH_RESERVE_UP);
    p = cur; p.reserve_margin = p.reserve_margin >= 30 * sc ? p.reserve_margin - 30 * sc : 0; add(p, CH_RESERVE_DOWN);
    p = cur; p.prior_weight_pct = std::uint16_t(p.prior_weight_pct + 10 * sc); add(p, CH_PRIOR_UP);
    p = cur; if (p.prior_weight_pct >= 20 * sc) { p.prior_weight_pct = std::uint16_t(p.prior_weight_pct - 20 * sc); add(p, CH_PRIOR_DOWN); }
    p = cur; p.lambda_g = std::uint16_t(p.lambda_g + 100 * sc); add(p, CH_GAIN_UP);
    p = cur; if (p.lambda_g > 100 * sc) { p.lambda_g = std::uint16_t(p.lambda_g - 100 * sc); add(p, CH_GAIN_DOWN); }
    p = cur; p.lambda_t = std::uint16_t(p.lambda_t + 50 * sc); add(p, CH_LAMBDA_T_UP);
    p = cur; if (p.lambda_t > 50 * sc) { p.lambda_t = std::uint16_t(p.lambda_t - 50 * sc); add(p, CH_LAMBDA_T_DOWN); }
    p = cur; p.stale_observe_max = std::uint8_t(p.stale_observe_max + 2 * sc); add(p, CH_STALE_UP);
    p = cur; if (p.stale_observe_max >= 2 * sc) { p.stale_observe_max = std::uint8_t(p.stale_observe_max - 2 * sc); add(p, CH_STALE_DOWN); }
    if (sc > 1) {
        p = cur; p.blocked_wait = std::uint8_t(p.blocked_wait + 4 * sc); add(p, CH_WAIT_UP);
        p = cur; if (p.blocked_wait >= 4 * sc) { p.blocked_wait = std::uint8_t(p.blocked_wait - 4 * sc); add(p, CH_WAIT_DOWN); }
        return n;
    }
    p = cur; p.rule_action[RC_BLOCKED] = RA_HOLD; add(p, CH_RULE_BLOCKED_HOLD);
    p = cur; p.rule_action[RC_BLOCKED] = RA_REPLAN; add(p, CH_RULE_BLOCKED_REPLAN);
    p = cur; p.rule_action[RC_CANDIDATE_NEAR] = RA_APPROACH; add(p, CH_RULE_NEAR_APPROACH);
    p = cur; p.rule_action[RC_CANDIDATE_NEAR] = RA_SKIP; add(p, CH_RULE_NEAR_SKIP);
    p = cur; p.rule_action[RC_CANDIDATE_NEAR] = RA_OBSERVE; add(p, CH_RULE_NEAR_OBSERVE);
    p = cur; p.rule_action[RC_CANDIDATE_FAR] = RA_SKIP; add(p, CH_RULE_FAR_SKIP);
    p = cur; p.rule_action[RC_CANDIDATE_FAR] = RA_APPROACH; add(p, CH_RULE_FAR_APPROACH);
    p = cur; p.blocked_wait = std::uint8_t(p.blocked_wait + 4 * sc); add(p, CH_WAIT_UP);
    p = cur; if (p.blocked_wait >= 4 * sc) { p.blocked_wait = std::uint8_t(p.blocked_wait - 4 * sc); add(p, CH_WAIT_DOWN); }
    p = cur; if (p.blocker_ttl == 0) { p.blocker_ttl = 20; add(p, CH_TTL_ON); }
    p = cur; if (p.blocker_ttl > 10) { p.blocker_ttl = std::uint8_t(p.blocker_ttl / 2); add(p, CH_TTL_DOWN); }
    p = cur; if (p.blocker_ttl != 0) { p.blocker_ttl = 0; add(p, CH_TTL_OFF); }
    return n;
}

// ---------------------------------------------------------------- 버전 저장소
PolicyStore::PolicyStore() {
    v_[0] = PolicyVersion{1, 0, default_params(), 1, 0, 0};
    n_ = 1;
    cur_ = 1;
}

const PolicyVersion* PolicyStore::find(std::uint32_t id) const {
    for (int i = 0; i < n_; ++i) if (v_[i].id == id) return &v_[i];
    return nullptr;
}

const PolicyParams& PolicyStore::current() const { return find(cur_)->params; }

std::uint32_t PolicyStore::submit(const RuleCandidate& c, bool approved) {
    if (n_ >= kMax) {                       // 가득 차면 거부 기록부터 지운다(승인 이력은 남긴다)
        int w = -1;
        for (int i = 1; i < n_; ++i) if (v_[i].status == 2) { w = i; break; }
        if (w < 0) return 0;
        for (int i = w; i + 1 < n_; ++i) v_[i] = v_[i + 1];
        --n_;
    }
    const std::uint32_t id = v_[n_ - 1].id + 1;
    v_[n_] = PolicyVersion{id, cur_, c.params, std::uint8_t(approved ? 1 : 2), c.reason, c.change_code};
    ++n_;
    if (approved) cur_ = id;
    return id;
}

bool PolicyStore::rollback(std::uint32_t id) {
    const PolicyVersion* v = find(id);
    if (!v || v->status != 1) return false;
    // 되돌릴 판도 지금의 불변식을 통과해야 한다 — 불변식이 조여진 뒤 옛 판으로 돌아가면 막힌 것이 살아난다
    if (!params_invariants_ok(v->params, nullptr)) return false;
    cur_ = id;
    return true;
}

}  // namespace walp

namespace walp {

std::uint32_t crc32(const std::uint8_t* d, std::size_t n) {
    std::uint32_t c = 0xFFFFFFFFu;
    for (std::size_t i = 0; i < n; ++i) {
        c ^= d[i];
        for (int k = 0; k < 8; ++k) c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1u)));
    }
    return ~c;
}

// 배치: "WALP" | 판(1) | n | cur | 버전 kMax 개(쓰지 않는 자리는 0) | crc32(앞 전부)
std::size_t PolicyStore::serialize(std::uint8_t* buf, std::size_t cap) const {
    if (cap < kImageSize) return 0;
    std::memset(buf, 0, kImageSize);
    std::memcpy(buf, "WALP", 4);
    const std::uint32_t ver = 1, n = std::uint32_t(n_), cur = cur_;
    std::memcpy(buf + 4, &ver, 4);
    std::memcpy(buf + 8, &n, 4);
    std::memcpy(buf + 12, &cur, 4);
    std::memcpy(buf + 16, v_, sizeof(PolicyVersion) * std::size_t(n_));
    const std::uint32_t c = crc32(buf, kImageSize - 4);
    std::memcpy(buf + kImageSize - 4, &c, 4);
    return kImageSize;
}

bool PolicyStore::deserialize(const std::uint8_t* buf, std::size_t len) {
    if (len != kImageSize || std::memcmp(buf, "WALP", 4) != 0) return false;
    std::uint32_t ver, n, cur, c;
    std::memcpy(&ver, buf + 4, 4);
    std::memcpy(&n, buf + 8, 4);
    std::memcpy(&cur, buf + 12, 4);
    std::memcpy(&c, buf + kImageSize - 4, 4);
    if (ver != 1 || n == 0 || n > std::uint32_t(kMax) || c != crc32(buf, kImageSize - 4)) return false;
    PolicyVersion tmp[kMax];
    std::memcpy(tmp, buf + 16, sizeof(PolicyVersion) * n);
    // 현재 버전은 승인된 것이어야 하고, 그 파라미터가 불변식을 지켜야 한다
    bool found = false;
    for (std::uint32_t i = 0; i < n; ++i)
        if (tmp[i].id == cur && tmp[i].status == 1 && params_invariants_ok(tmp[i].params, nullptr)) found = true;
    if (!found) return false;
    std::memcpy(v_, tmp, sizeof(PolicyVersion) * n);
    n_ = int(n);
    cur_ = cur;
    return true;
}

}  // namespace walp
