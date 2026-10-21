// 原位复现：七 bank + TitleScene，首帧步进（浏览器首帧挂起定位）。
#include "game/AnmRenderer.hpp"
#include "game/AnmManager.hpp"
#include "game/TitleScene.hpp"
#include "game/RunPause.hpp"
#include <cstdio>
#include <vector>
using namespace th13;
struct NullGraphics final : ZunGraphics {
    touhou::graphics::PipelineState pipeline_state;
    touhou::graphics::PipelineState& pipeline() override { return pipeline_state; }
    u32 texture(const AnmResource&, u32) override { return 0; }
    void bind_texture(u32) override {}
    void set_layout(touhou::graphics::VertexLayout) override {}
    void set_matrix(touhou::graphics::MatrixKind, const Matrix4&) override {}
    void triangles(u32, const void*, u32) override {}
    void primitives(touhou::graphics::Topology, u32, const void*, u32) override {}
    bool select_target(const AnmResource*, u32) override { return true; }
    bool clear_target(u32, const GraphicsViewport*) override { return true; }
    bool clear_depth(const GraphicsViewport*) override { return true; }
    void set_viewport(const GraphicsViewport&) override {}
};
struct Host final : TitleScenePlatform {
    bool clear_title_overlay() override { return true; }
    bool queue_title_music(i32, i32, const std::string&) override { return true; }
    bool clear_current_wave() override { return true; }
    bool title_exit(i32) override { return true; }
    bool fade_out_title_music() override { return true; }
    bool sound(i32) override { return true; }
    bool begin_transition(u32&) override { return true; }
    bool start_game(i32) override { return true; }
    bool hud_text(const HudTextDraw&) override { return true; }
};
static std::vector<u8> slurp(const char* path) {
    std::FILE* f = std::fopen(path, "rb");
    if (!f) { std::printf("missing %s\n", path); std::exit(1); }
    std::fseek(f, 0, SEEK_END); long n = std::ftell(f); std::fseek(f, 0, SEEK_SET);
    std::vector<u8> out;
    out.resize(size_t(n));
    if (std::fread(out.data(), 1, out.size(), f) != out.size()) std::exit(1);
    std::fclose(f);
    return out;
}
int main(int argc, char** argv) {
    GameRandom random{0};
    AnmEnvironment env;
    AnmManager manager{random, env};
    env.raster_scale = 1;
    env.screen_offsets = {320, 16, 320, 16};
    env.playfield_origin = {128, 16};
    const char* banks[][2] = {
        {"text.anm", "0"}, {"sig.anm", "1"}, {"ascii.anm", "2"},  {"effect.anm", "8"},
        {"title.anm", "16"}, {"title_v.anm", "17"}, {"help.anm", "19"},
    };
    for (auto& bank : banks) {
        auto bytes = slurp((std::string("artifacts/reference/raw/") + bank[0]).c_str());
        if (!manager.load(atoi(bank[1]), bytes.data(), u32(bytes.size()))) {
            std::printf("load %s: %s\n", bank[0], manager.error.c_str());
            return 1;
        }
        if (atoi(bank[1]) == 2) env.fallback_sprite_resource = manager.resource(2);
        std::printf("loaded %s\n", bank[0]);
    }
    NullGraphics graphics;
    AnmRenderer renderer{graphics};
    Host host;
    SessionState progress;
    TitleSelectionSettings selection;
    FrameScheduler scheduler;
    TitleScene title(progress, selection, manager, host, scheduler, TitleSceneResources{});
    if (!title.error.empty()) { std::printf("title: %s\n", title.error.c_str()); return 1; }
    std::printf("title ok; stepping 60 frames with Z pressed\n");
    for (int frame = 0; frame < 60; frame++) {
        std::printf("frame %d\n", frame);
        std::fflush(stdout);
        // 模拟 ApplicationState 帧序：anim 主链(9) → 场景(6/20 注册在先) → anim 副链(34) → Draw。
        if (!manager.update(false)) { std::printf("anim main fail: %s\n", manager.error.c_str()); return 1; }
        title.controls({0x01, 0x01, 0x01, 0});
        if (scheduler.update() < 0) { std::printf("update fail: %s\n", title.error.c_str()); return 1; }
        if (!manager.update(true)) { std::printf("anim alt fail: %s\n", manager.error.c_str()); return 1; }
        if (scheduler.draw() < 0) { std::printf("draw fail\n"); return 1; }
        for (u32 layer = 0; layer < 33; ++layer)
            if (!renderer.draw_layer(manager.registry.layer(layer))) { std::printf("layer %u draw fail: %s\n", layer, renderer.error.c_str()); return 1; }
    }
    std::printf("ok 60 frames\n");
    return 0;
}
