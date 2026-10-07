// A bounded, headless SAM 3 process. Exiting releases all Metal/model memory.
#include "sam3.h"
#include <algorithm>
#include <chrono>
#include <fstream>
#include <iostream>

int main(int argc, char **argv) {
    if (argc != 7) return 2;
    const auto start = std::chrono::steady_clock::now();
    sam3_params params;
    params.model_path = argv[1];
    params.n_threads = 4;
    auto model = sam3_load_model(params);
    if (!model || sam3_is_visual_only(*model)) return 3;
    auto state = sam3_create_state(*model, params);
    auto image = sam3_load_image(argv[2]);
    if (!state || image.width <= 0 || image.height <= 0 ||
        !sam3_encode_image(*state, *model, image)) return 4;
    std::ofstream manifest(std::string(argv[3]) + "/result.json");
    manifest << "{\"width\":" << image.width << ",\"height\":" << image.height;
    for (int pass = 0; pass < 3; ++pass) {
        manifest << (pass == 0 ? ",\"candidates\":[" : pass == 1 ? ",\"faces\":[" : ",\"skin\":[");
        const std::string text = pass == 0 ? argv[4] : pass == 1 ? "face" : "skin";
        if (!text.empty() && (pass == 0 || std::string(argv[pass == 1 ? 5 : 6]) == "1")) {
            sam3_pcs_params pcs;
            pcs.text_prompt = text;
            pcs.score_threshold = pass ? 0.30f : 0.35f;
            auto result = sam3_segment_pcs(*state, *model, pcs);
            std::sort(result.detections.begin(), result.detections.end(),
                      [](const sam3_detection &a, const sam3_detection &b) { return a.score > b.score; });
            size_t count = std::min(result.detections.size(), size_t(8));
            for (size_t i = 0; i < count; ++i) {
                const auto &d = result.detections[i];
                const auto name = std::string(pass == 0 ? "candidate-" : pass == 1 ? "face-" : "skin-") + std::to_string(i) + ".png";
                if (!sam3_save_mask(d.mask, std::string(argv[3]) + "/" + name)) return 5;
                if (i) manifest << ",";
                manifest << "{\"file\":\"" << name << "\",\"score\":" << d.score << "}";
            }
        }
        manifest << "]";
    }
    double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    manifest << ",\"seconds\":" << seconds << "}";
    return manifest.good() ? 0 : 6;
}
