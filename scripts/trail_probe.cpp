// 原位复现：装载挡板 ascii script17 族，逐帧 tick 找 trail 错误源。
#include "game/AnmRenderer.hpp"
#include "game/AnmManager.hpp"
#include "game/AnmEnvironment.hpp"
#include "game/Rng.hpp"
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
static std::vector<u8> slurp(const char* path) {
    std::FILE* f = std::fopen(path, "rb");
    if (!f) { std::printf("missing %s\n", path); std::exit(1); }
    std::fseek(f, 0, SEEK_END); long n = std::ftell(f); std::fseek(f, 0, SEEK_SET);
    std::vector<u8> out;
    out.resize(size_t(n));
    if (fread(out.data(), 1, out.size(), f) != out.size()) std::exit(1);
    std::fclose(f);
    return out;
}
int main() {
    GameRandom random{0};
    AnmEnvironment env;
    AnmManager manager{random, env};
    auto sig = slurp("artifacts/reference/raw/sig.anm");
    auto ascii = slurp("artifacts/reference/raw/ascii.anm");
    if (!manager.load(1, sig.data(), u32(sig.size()))) { std::printf("sig: %s\n", manager.error.c_str()); return 1; }
    if (!manager.load(2, ascii.data(), u32(ascii.size()))) { std::printf("ascii: %s\n", manager.error.c_str()); return 1; }
    NullGraphics graphics;
    AnmRenderer renderer{graphics};
    const u32 a = manager.create(1, 0);
    const u32 b = manager.create(2, 17, -1, 0, {960, 784, 0});
    std::printf("created %u %u\n", a, b);
    for (int frame = 0; frame < 200; frame++) {
        if (!manager.update(false) || !manager.update(true)) { std::printf("update fail frame %d: %s\n", frame, manager.error.c_str()); return 1; }
        for (u32 layer = 0; layer < 33; ++layer)
            if (!renderer.draw_layer(manager.registry.layer(layer))) {
                std::printf("draw fail frame %d layer %u: %s\n", frame, layer, renderer.error.c_str());
                return 1;
            }
    }
    std::printf("ok 200 frames\n");
    return 0;
}
