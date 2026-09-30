# WALP — 빌드. 산출물은 BUILD 아래에만 쓴다(검사가 저장소에 흔적을 안 남기게 임시 자리를 줄 수 있다).
#   make                       # build/ 에 코어 라이브러리·CLI·시험
#   make test                  # 코어 시험(고장 주입·재현성·파서·힙 없음)
#   make BUILD=/tmp/x test     # 다른 자리에
BUILD ?= build
CXX ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -Werror -Iinclude -DWALP_SRC_DIR=\"$(CURDIR)\"
# 코어는 예외·RTTI 없이도 빌드된다(MCU 툴체인 가정) — core 대상에서 따로 확인한다
CORE_FLAGS = -fno-exceptions -fno-rtti

CORE_SRC = src/parser.cpp src/state.cpp src/planner.cpp src/executive.cpp src/learner.cpp
CORE_OBJ = $(patsubst src/%.cpp,$(BUILD)/core/%.o,$(CORE_SRC))
HOST_SRC = sim/world.cpp tools/harness.cpp tools/alloc_count.cpp
HOST_OBJ = $(patsubst %.cpp,$(BUILD)/host/%.o,$(HOST_SRC))

all: $(BUILD)/walp_cli $(BUILD)/core_test

$(BUILD)/core/%.o: src/%.cpp include/walp/*.hpp
	@mkdir -p $(dir $@)
	$(CXX) $(CXXFLAGS) $(CORE_FLAGS) -c $< -o $@

$(BUILD)/host/%.o: %.cpp include/walp/*.hpp sim/world.hpp tools/harness.hpp
	@mkdir -p $(dir $@)
	$(CXX) $(CXXFLAGS) -c $< -o $@

$(BUILD)/host/tools/alloc_count.o: tools/alloc_count.cpp tools/harness.hpp
	@mkdir -p $(dir $@)
	$(CXX) $(CXXFLAGS) -fno-builtin -Wno-mismatched-new-delete -c $< -o $@

$(BUILD)/libwalp_core.a: $(CORE_OBJ)
	ar rcs $@ $^

$(BUILD)/walp_cli: tools/walp_cli.cpp $(HOST_OBJ) $(BUILD)/libwalp_core.a
	$(CXX) $(CXXFLAGS) $< $(HOST_OBJ) $(BUILD)/libwalp_core.a -o $@

$(BUILD)/core_test: tests/core_test.cpp $(HOST_OBJ) $(BUILD)/libwalp_core.a
	$(CXX) $(CXXFLAGS) $< $(HOST_OBJ) $(BUILD)/libwalp_core.a -o $@

test: $(BUILD)/core_test
	$(BUILD)/core_test

clean:
	rm -rf $(BUILD)

.PHONY: all test clean
