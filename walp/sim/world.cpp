#include "world.hpp"

#include <algorithm>
#include <cstring>
#include <deque>

namespace walpsim {

World::World(const ScenarioConfig& cfg, std::uint32_t avoid_mask)
    : cfg_(cfg), avoid_(avoid_mask), rng_(cfg.seed * 2654435761u + 17), obs_rng_(cfg.seed * 40503u + 99),
      battery_(cfg.battery) {
    history_.reserve(std::size_t(cfg.deadline) + 64);   // 스텝 안에서 할당하지 않게
    build();
}

bool World::free_cell(int x, int y) const {
    if (x < 0 || y < 0 || x >= w_ || y >= h_) return false;
    const int i = idx(x, y);
    return !wall_[i] && !hazard_[i] && !blocker_[i];
}

void World::build() {
    for (int attempt = 0; attempt < 50; ++attempt) {
        wall_.assign(w_ * h_, 0);
        zone_.assign(w_ * h_, kFloor);
        hazard_.assign(w_ * h_, 0);
        blocker_.assign(w_ * h_, 0);
        auto U = [&](int a, int b) { return std::uniform_int_distribution<int>(a, b)(rng_); };
        for (int x = 0; x < w_; ++x) { wall_[idx(x, 0)] = 1; wall_[idx(x, h_ - 1)] = 1; }
        for (int y = 0; y < h_; ++y) { wall_[idx(0, y)] = 1; wall_[idx(w_ - 1, y)] = 1; }
        // 방 넷: 가운데 세로벽·가로벽, 문 하나씩(가로벽은 양쪽에 하나씩)
        const int vx = w_ / 2, hy = h_ / 2;
        for (int y = 1; y < h_ - 1; ++y) wall_[idx(vx, y)] = 1;
        for (int x = 1; x < w_ - 1; ++x) wall_[idx(x, hy)] = 1;
        const int d1 = U(2, hy - 2), d2 = U(hy + 2, h_ - 3), d3 = U(2, vx - 2), d4 = U(vx + 2, w_ - 3);
        wall_[idx(vx, d1)] = 0; wall_[idx(vx, d2)] = 0; wall_[idx(d3, hy)] = 0; wall_[idx(d4, hy)] = 0;
        doors_ = {{vx, d1}, {vx, d2}, {d3, hy}, {d4, hy}};
        // 구역: 방마다 책상 패치 둘, 선반 줄 하나, 카운터 줄 하나를 흩는다(방마다 다른 벽 쪽)
        struct Room { int x0, y0, x1, y1; };
        const Room rooms[4] = {{1, 1, vx - 1, hy - 1}, {vx + 1, 1, w_ - 2, hy - 1},
                               {1, hy + 1, vx - 1, h_ - 2}, {vx + 1, hy + 1, w_ - 2, h_ - 2}};
        for (const Room& r : rooms) {
            for (int k = 0; k < 2; ++k) {           // 책상 2x2
                const int x = U(r.x0 + 1, r.x1 - 2), y = U(r.y0 + 1, r.y1 - 2);
                for (int dy = 0; dy < 2; ++dy) for (int dx = 0; dx < 2; ++dx) zone_[idx(x + dx, y + dy)] = kDesk;
            }
            // 선반: 방의 위나 아래 벽을 따라
            {
                const int y = U(0, 1) ? r.y0 : r.y1;
                const int x = U(r.x0, r.x1 - 3);
                for (int dx = 0; dx < 4; ++dx) zone_[idx(x + dx, y)] = kShelf;
            }
            // 카운터: 왼쪽이나 오른쪽 벽을 따라
            {
                const int x = U(0, 1) ? r.x0 : r.x1;
                const int y = U(r.y0, r.y1 - 2);
                for (int dy = 0; dy < 3; ++dy) zone_[idx(x, y + dy)] = kCounter;
            }
        }
        hx_ = rx_ = 1; hy_ = ry_ = 1;
        zone_[idx(hx_, hy_)] = kFloor;
        // 평면도에 없는 정적 장애물 10 · 위험 4 (집·문 앞은 피한다)
        auto near_door = [&](int x, int y) {
            return (std::abs(x - vx) <= 1 && (std::abs(y - d1) <= 1 || std::abs(y - d2) <= 1)) ||
                   (std::abs(y - hy) <= 1 && (std::abs(x - d3) <= 1 || std::abs(x - d4) <= 1));
        };
        auto put = [&](std::vector<std::uint8_t>& v, int n) {
            for (int k = 0; k < n;) {
                const int x = U(1, w_ - 2), y = U(1, h_ - 2);
                if (!free_cell(x, y) || (std::abs(x - hx_) <= 2 && std::abs(y - hy_) <= 2) || near_door(x, y)) continue;
                v[idx(x, y)] = 1;
                ++k;
            }
        };
        put(blocker_, 10);
        put(hazard_, 4);
        // 집에서 모든 자유칸에 닿는가
        std::vector<int> seen(w_ * h_, 0);
        std::deque<int> q{idx(hx_, hy_)};
        seen[q.front()] = 1;
        int reach = 1;
        while (!q.empty()) {
            const int c = q.front(); q.pop_front();
            const int x = c % w_, y = c / w_;
            const int DX[4] = {0, 1, 0, -1}, DY[4] = {-1, 0, 1, 0};
            for (int k = 0; k < 4; ++k) {
                const int nx = x + DX[k], ny = y + DY[k];
                if (!free_cell(nx, ny) || seen[idx(nx, ny)]) continue;
                seen[idx(nx, ny)] = 1; ++reach; q.push_back(idx(nx, ny));
            }
        }
        int nfree = 0;
        for (int y = 0; y < h_; ++y) for (int x = 0; x < w_; ++x) if (free_cell(x, y)) ++nfree;
        if (reach != nfree) continue;
        place_objects();
        // 동적 장애물
        dyn_.clear();
        for (int k = 0; k < cfg_.fault.dyn_blockers;) {
            const int x = U(1, w_ - 2), y = U(1, h_ - 2);
            if (!free_cell(x, y) || (std::abs(x - hx_) <= 3 && std::abs(y - hy_) <= 3)) continue;
            dyn_.push_back({x, y});
            ++k;
        }
        door_guys_.clear();
        for (int k = 0; k < cfg_.fault.door_blockers && k < 4; ++k)
            door_guys_.push_back({k, false, doors_[k].first, doors_[k].second});
        return;
    }
}

void World::place_objects() {
    objs_.clear();
    auto U = [&](int a, int b) { return std::uniform_int_distribution<int>(a, b)(rng_); };
    auto P = [&]() { return std::uniform_real_distribution<double>(0, 1)(rng_); };
    std::vector<int> cells_by_zone[kNumZones];
    std::vector<int> all;
    for (int y = 1; y < h_ - 1; ++y)
        for (int x = 1; x < w_ - 1; ++x) {
            if (!free_cell(x, y) || (x == hx_ && y == hy_)) continue;
            cells_by_zone[zone_[idx(x, y)]].push_back(idx(x, y));
            all.push_back(idx(x, y));
        }
    std::vector<std::uint8_t> used(w_ * h_, 0);
    auto pick = [&](const std::vector<int>& v) {
        for (int t = 0; t < 200; ++t) {
            const int c = v[U(0, int(v.size()) - 1)];
            if (!used[c]) { used[c] = 1; return c; }
        }
        for (int c : all) if (!used[c]) { used[c] = 1; return c; }
        return all[0];
    };
    int favored = -1;
    if (cfg_.family == F_OFFICE) favored = kDesk;
    else if (cfg_.family == F_SHOP) favored = kCounter;
    else if (cfg_.family == F_OFFICE_SHIFT) favored = kShelf;
    const int tc = (favored >= 0 && P() < 0.8 && !cells_by_zone[favored].empty()) ? pick(cells_by_zone[favored]) : pick(all);
    objs_.push_back({tc % w_, tc / w_, cfg_.target_type, cfg_.color, cfg_.brand});
    // 방해물: 같은 종류 다른 색 · (카드면) 같은 색 다른 브랜드 · 다른 종류 같은 색 · 무작위 둘
    auto other = [&](int cur, int n) { int v; do { v = U(1, n - 1); } while (v == cur); return v; };
    auto add = [&](std::uint8_t t, std::uint8_t c, std::uint8_t b) {
        // 방해물도 목표와 같은 선호 구역에 절반은 둔다 — 사전확률이 '거기 가면 끝'이 되지 않게
        const int cc = (favored >= 0 && P() < 0.5 && !cells_by_zone[favored].empty()) ? pick(cells_by_zone[favored]) : pick(all);
        objs_.push_back({cc % w_, cc / w_, t, c, t == kCard ? b : std::uint8_t(0)});
    };
    const std::uint8_t T = cfg_.target_type, C = cfg_.color, B = cfg_.brand;
    add(T, std::uint8_t(other(C, kNumColor)), B ? B : std::uint8_t(kVisa));
    if (T == kCard) add(T, C, std::uint8_t(B == kVisa ? kMaster : kVisa));
    else add(T, std::uint8_t(other(C, kNumColor)), 0);
    add(std::uint8_t(other(T, kNumObj)), C, kVisa);
    add(std::uint8_t(U(1, kNumObj - 1)), std::uint8_t(U(1, kNumColor - 1)), std::uint8_t(U(1, 2)));
    add(std::uint8_t(U(1, kNumObj - 1)), std::uint8_t(U(1, kNumColor - 1)), std::uint8_t(U(1, 2)));
    // 무작위 방해물이 우연히 목표와 똑같으면 바꾼다(정답이 둘이면 안 된다)
    for (std::size_t i = 1; i < objs_.size(); ++i) {
        SimObject& o = objs_[i];
        const bool same = o.type == T && (C == 0 || o.color == C) && (T != kCard || B == 0 || o.brand == B);
        if (same) o.color = std::uint8_t(other(C ? C : 1, kNumColor));
    }
}

Status World::map_hint(MapHint& out) {
    std::memset(&out, 0, sizeof out);
    out.w = std::uint8_t(w_);
    out.h = std::uint8_t(h_);
    // 분포 이동 시나리오도 '사무실' 로 표시된다 — 표지와 실제가 어긋나는 것이 이 시험의 요점
    out.env_type = cfg_.family == F_OFFICE_SHIFT ? F_OFFICE : cfg_.family;
    out.home_x = std::uint8_t(hx_);
    out.home_y = std::uint8_t(hy_);
    for (int i = 0; i < w_ * h_; ++i) { out.wall[i] = wall_[i]; out.zone[i] = zone_[i]; }
    return Status::Ok;
}

Observation World::snapshot() {
    Observation o{};
    o.obs_id = next_obs_id_++;
    o.tick = tick_;
    o.valid = true;
    o.x = std::uint8_t(rx_);
    o.y = std::uint8_t(ry_);
    o.battery = battery_;
    auto P = [&]() { return std::uniform_real_distribution<double>(0, 1)(obs_rng_); };
    auto U = [&](int a, int b) { return std::uniform_int_distribution<int>(a, b)(obs_rng_); };
    for (int dy = -kViewR; dy <= kViewR; ++dy)
        for (int dx = -kViewR; dx <= kViewR; ++dx) {
            const int x = rx_ + dx, y = ry_ + dy;
            const int vi = (dy + kViewR) * (2 * kViewR + 1) + (dx + kViewR);
            if (x < 0 || y < 0 || x >= w_ || y >= h_) { o.view_kind[vi] = kWall; continue; }
            const int i = idx(x, y);
            bool dyn = false;
            for (auto& p : dyn_) if (p.first == x && p.second == y) dyn = true;
            for (auto& g : door_guys_) if (g.x == x && g.y == y) dyn = true;
            o.view_kind[vi] = wall_[i] ? kWall : hazard_[i] ? kHazard : (blocker_[i] || dyn) ? kBlocker : kFree;
            o.view_zone[vi] = zone_[i];
        }
    for (const SimObject& ob : objs_) {
        const int d = std::max(std::abs(ob.x - rx_), std::abs(ob.y - ry_));
        if (d > kViewR || o.n_det >= kMaxDet) continue;
        if (P() > (d <= 1 ? 0.95 : 0.75)) continue;
        const double e = cfg_.noise * (d == 2 ? 2.0 : 1.0);
        Detection det{std::uint8_t(ob.x), std::uint8_t(ob.y), ob.type, ob.color, ob.brand, std::uint8_t(100 * (1 - e))};
        auto flip = [&](std::uint8_t cur, int n) { int v; do { v = U(1, n - 1); } while (v == cur); return std::uint8_t(v); };
        if (P() < e / 2) det.type = flip(ob.type, kNumObj);
        if (P() < e) det.color = flip(ob.color, kNumColor);
        if (det.type == kCard) { if (ob.brand == 0 || P() < e) det.brand = flip(ob.brand ? ob.brand : 1, kNumBrand); }
        else det.brand = 0;
        o.det[o.n_det++] = det;
        if ((force_contra_ || P() < cfg_.fault.contradiction) && o.n_det < kMaxDet) {
            Detection c2 = det;
            c2.color = flip(det.color, kNumColor);
            o.det[o.n_det++] = c2;
        }
    }
    return o;
}

Status World::observe(Observation& out) {
    Observation cur = snapshot();
    history_.push_back(cur);
    auto P = [&]() { return std::uniform_real_distribution<double>(0, 1)(obs_rng_); };
    if (force_drop_ > 0 || P() < cfg_.fault.dropout) {
        if (force_drop_ > 0) --force_drop_;
        out = Observation{};
        out.obs_id = cur.obs_id;
        out.valid = false;
        return Status::Ok;
    }
    if ((force_lat_ > 0 || P() < cfg_.fault.latency) && history_.size() > 4) {
        if (force_lat_ > 0) --force_lat_;
        out = history_[history_.size() - 4];
        out.obs_id = cur.obs_id;   // 번호는 새로 받지만 내용과 시각은 3틱 전 것
        return Status::Ok;
    }
    out = cur;
    return Status::Ok;
}

void World::step_dynamics() {
    auto U = [&](int a, int b) { return std::uniform_int_distribution<int>(a, b)(rng_); };
    for (auto& p : dyn_) {
        if (U(0, 1)) continue;
        const int DX[4] = {0, 1, 0, -1}, DY[4] = {-1, 0, 1, 0};
        const int k = U(0, 3);
        const int nx = p.first + DX[k], ny = p.second + DY[k];
        if (!free_cell(nx, ny) || (nx == rx_ && ny == ry_)) continue;
        p = {nx, ny};
    }
    // 문간 사람: 문에 서 있다가 가끔 옆으로 비켜서고 다시 돌아온다(로봇 자리로는 안 간다)
    auto P = [&]() { return std::uniform_real_distribution<double>(0, 1)(rng_); };
    for (auto& g : door_guys_) {
        if (P() >= cfg_.fault.door_move) continue;
        const auto dp = doors_[g.door];
        if (!g.aside) {
            // 비켜설 때는 문 축(문과 그 앞뒤 칸)에서 벗어난 자유칸으로 간다. 첫 판은 문 바로 앞 칸으로
            // 비켜서서(문 옆은 전부 벽이라 그 칸밖에 없다) 문이 사실상 늘 막혀 있었다.
            const bool vertical_wall = wall_[idx(dp.first, dp.second - 1)] && wall_[idx(dp.first, dp.second + 1)];
            for (int t = 0; t < 12; ++t) {
                const int nx = dp.first + U(-2, 2), ny = dp.second + U(-2, 2);
                const bool on_axis = vertical_wall ? (ny == dp.second) : (nx == dp.first);
                if (on_axis || !free_cell(nx, ny) || (nx == rx_ && ny == ry_)) continue;
                g.x = nx; g.y = ny; g.aside = true;
                break;
            }
        } else if (!(dp.first == rx_ && dp.second == ry_)) {
            g.x = dp.first; g.y = dp.second; g.aside = false;
        }
    }
    // 도중에 생기는 위험: 20~150 틱 사이 어느 때 한 칸
    if (cfg_.fault.dyn_hazards > 0 && tick_ >= 20 && tick_ <= 150 && U(0, 130) < cfg_.fault.dyn_hazards) {
        const int x = U(1, w_ - 2), y = U(1, h_ - 2);
        if (free_cell(x, y) && !(x == rx_ && y == ry_) && !(x == hx_ && y == hy_) && !is_target(x, y))
            hazard_[idx(x, y)] = 1;
    }
}

bool World::is_target(int x, int y) const { return !objs_.empty() && objs_[0].x == x && objs_[0].y == y; }

Status World::execute(const ActionRequest& a, ExecutionResult& r) {
    r = ExecutionResult{Status::Ok, 0};
    auto P = [&]() { return std::uniform_real_distribution<double>(0, 1)(rng_); };
    Status st = Status::Ok;
    switch (a.action_type) {
    case kMoveN: case kMoveE: case kMoveS: case kMoveW: {
        int dx, dy;
        action_delta(a.action_type, dx, dy);
        const int nx = rx_ + dx, ny = ry_ + dy;
        battery_ -= kMoveEnergy;
        r.energy_used = kMoveEnergy;
        if (battery_ < 0) { st = Status::Fault; break; }
        if (force_act_fail_ || P() < cfg_.fault.act_fail) { st = Status::Fault; break; }
        if (P() < cfg_.fault.act_silent) break;   // 결과는 Ok, 실제로는 안 움직임
        if (nx < 0 || ny < 0 || nx >= w_ || ny >= h_ || wall_[idx(nx, ny)]) { st = Status::Fault; ++viol_.collisions; break; }
        bool dyn = false;
        for (auto& p : dyn_) if (p.first == nx && p.second == ny) dyn = true;
        for (auto& g : door_guys_) if (g.x == nx && g.y == ny) dyn = true;
        if (blocker_[idx(nx, ny)] || dyn) { st = Status::Fault; ++viol_.collisions; break; }
        rx_ = nx; ry_ = ny;
        if (hazard_[idx(nx, ny)]) ++viol_.hazard_entered;
        if (avoid_ & (1u << zone_[idx(nx, ny)])) ++viol_.avoid_entered;
        break;
    }
    case kObserve: battery_ -= kObserveEnergy; r.energy_used = kObserveEnergy; break;
    case kHold: battery_ -= kHoldEnergy; r.energy_used = kHoldEnergy; break;
    case kDeclare: case kAbort: break;
    default: st = Status::InvalidInput; break;
    }
    if (battery_ <= 0 && !(rx_ == hx_ && ry_ == hy_) && viol_.stranded == 0) viol_.stranded = 1;
    ++tick_;
    step_dynamics();
    r.status = st;
    return st;
}

}  // namespace walpsim
