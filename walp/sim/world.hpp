// 호스트 시뮬레이터 — 정책 코어와 **다른 모듈**이다. 코어는 IPlatform 으로만 이것을 본다.
// 정답(물체 위치·위험·장애물)은 여기 안에만 있고, 안전 위반도 여기서 **따로** 센다
// (코어의 거부 기록과 대조하는 독립 계수).
#pragma once
#include <cstdint>
#include <random>
#include <vector>

#include "walp/core.hpp"

namespace walpsim {

using namespace walp;

enum Family : std::uint8_t {
    F_OFFICE = 1,        // 목표가 책상 구역에 80%
    F_SHOP = 2,          // 카운터에 80%
    F_HOME = 3,          // 사례에 없는 환경 유형 — 균등 배치
    F_OFFICE_SHIFT = 4,  // 사무실이라고 **표시**되지만 목표는 선반에 80% (분포 이동)
};

struct FaultConfig {
    double dropout = 0.0;       // 관측 누락 확률
    double latency = 0.0;       // 3틱 묵은 관측이 올 확률
    double contradiction = 0.0; // 같은 칸에 속성이 뒤집힌 탐지가 하나 더 붙을 확률
    double act_fail = 0.0;      // 이동 명령이 실패(결과 Fault)할 확률
    double act_silent = 0.0;    // 이동이 안 됐는데 결과는 Ok 인 확률
    int dyn_blockers = 0;       // 돌아다니는 장애물(사람) 수
    int dyn_hazards = 0;        // 도중에 생기는 위험(쏟은 물) 수
    int door_blockers = 0;      // 문간에 서성이는 사람(0..4) — 적응 시험의 '바뀐 환경'
    double door_move = 0.2;     // 스텝마다 비켜서거나 돌아올 확률
};

struct ScenarioConfig {
    std::uint32_t seed = 1;
    Family family = F_OFFICE;
    double noise = 0.12;        // 속성 오인 확률(거리 2 에서는 두 배)
    FaultConfig fault{};
    std::int32_t battery = 1300;
    int deadline = 300;
    // 목표 명세(평가기가 goal 을 만든다)
    std::uint8_t target_type = kCard;
    std::uint8_t color = kBlue;
    std::uint8_t brand = kVisa;
};

struct Violations {
    int hazard_entered = 0;
    int collisions = 0;         // 장애물로 이동 시도(정답 기준)
    int avoid_entered = 0;
    int stranded = 0;           // 배터리 0 이하, 집 밖
    int total() const { return hazard_entered + collisions + avoid_entered + stranded; }
};

struct SimObject {
    int x, y;
    std::uint8_t type, color, brand;
};

class World : public IPlatform {
public:
    World(const ScenarioConfig& cfg, std::uint32_t avoid_mask);
    Status map_hint(MapHint& out) override;
    Status observe(Observation& out) override;
    Status execute(const ActionRequest& a, ExecutionResult& r) override;
    Tick now() const override { return tick_; }

    // 평가기 전용(코어는 못 부른다)
    bool is_target(int x, int y) const;
    const Violations& violations() const { return viol_; }
    int target_x() const { return objs_[0].x; }
    int target_y() const { return objs_[0].y; }
    std::uint8_t target_zone() const { return zone_[idx(objs_[0].x, objs_[0].y)]; }
    std::int32_t battery() const { return battery_; }
    int w() const { return w_; }
    int h() const { return h_; }
    bool at_home() const { return rx_ == hx_ && ry_ == hy_; }
    // 강제 고장(고장 주입 시험용)
    void force_dropout_next(int n) { force_drop_ = n; }
    void force_latency_next(int n) { force_lat_ = n; }
    void force_act_fail(bool on) { force_act_fail_ = on; }
    void force_contradiction(bool on) { force_contra_ = on; }

private:
    int idx(int x, int y) const { return y * w_ + x; }
    void build();
    void place_objects();
    void step_dynamics();
    bool free_cell(int x, int y) const;
    Observation snapshot();

    ScenarioConfig cfg_;
    std::uint32_t avoid_;
    std::mt19937 rng_;
    std::mt19937 obs_rng_;   // 관측 잡음은 다른 흐름 — 행동이 달라도 세계 생성이 같게
    int w_ = 20, h_ = 16;
    int hx_ = 1, hy_ = 1, rx_ = 1, ry_ = 1;
    std::int32_t battery_;
    Tick tick_ = 1;
    std::uint32_t next_obs_id_ = 1;
    std::vector<std::uint8_t> wall_, zone_, hazard_, blocker_;
    std::vector<SimObject> objs_;
    std::vector<std::pair<int, int>> dyn_;   // 움직이는 장애물 위치
    std::vector<std::pair<int, int>> doors_; // 문 칸
    struct DoorGuy { int door; bool aside; int x, y; };
    std::vector<DoorGuy> door_guys_;
    std::vector<Observation> history_;       // 지연 고장용
    Violations viol_{};
    int force_drop_ = 0, force_lat_ = 0;
    bool force_act_fail_ = false, force_contra_ = false;
};

// 기록된 관측·결과를 그대로 되돌려 주는 플랫폼(재현성 검사)
class ReplayPlatform : public IPlatform {
public:
    ReplayPlatform(const MapHint& m, std::vector<Observation> obs, std::vector<ExecutionResult> res,
                   std::vector<Tick> ticks)
        : map_(m), obs_(std::move(obs)), res_(std::move(res)), ticks_(std::move(ticks)) {}
    Status map_hint(MapHint& out) override { out = map_; return Status::Ok; }
    Status observe(Observation& out) override {
        if (oi_ >= obs_.size()) { out = Observation{}; return Status::Fault; }
        out = obs_[oi_++];
        return Status::Ok;
    }
    Status execute(const ActionRequest&, ExecutionResult& r) override {
        if (ri_ >= res_.size()) { r = ExecutionResult{Status::Fault, 0}; return Status::Fault; }
        r = res_[ri_++];
        ++ti_;
        return r.status;
    }
    // 기록은 실행 뒤 시각을 모았다: 아직 한 번도 실행 안 했으면 시작 시각(1)
    Tick now() const override { return ti_ == 0 ? 1 : ticks_[ti_ - 1]; }
private:
    MapHint map_;
    std::vector<Observation> obs_;
    std::vector<ExecutionResult> res_;
    std::vector<Tick> ticks_;
    std::size_t oi_ = 0, ri_ = 0, ti_ = 0;
};

// 기록하는 플랫폼 래퍼: 코어가 본 관측과 결과를 모은다
class RecordingPlatform : public IPlatform {
public:
    explicit RecordingPlatform(IPlatform& inner) : in_(inner) {}
    Status map_hint(MapHint& out) override { Status s = in_.map_hint(out); map = out; return s; }
    Status observe(Observation& out) override {
        Status s = in_.observe(out);
        if (s != Status::Ok) out.valid = false;
        obs.push_back(out);
        return Status::Ok;
    }
    Status execute(const ActionRequest& a, ExecutionResult& r) override {
        Status s = in_.execute(a, r);
        r.status = s;
        res.push_back(r);
        ticks.push_back(in_.now());
        return s;
    }
    Tick now() const override { return in_.now(); }
    MapHint map{};
    std::vector<Observation> obs;
    std::vector<ExecutionResult> res;
    std::vector<Tick> ticks;
private:
    IPlatform& in_;
};

}  // namespace walpsim
