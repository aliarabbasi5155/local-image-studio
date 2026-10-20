// A bounded, headless SAM 3 process. Exiting releases all Metal/model memory.
// Arguments: model image output target protect_faces protect_skin [concept...]
// Extra concepts (identity swap: hair, person, clothing...) reuse the single image encoding.
#include "sam3.h"
#include <algorithm>
#include <chrono>
#include <fstream>
#include <iostream>

static bool segment(sam3_state &state, const sam3_model &model, const std::string &text, float threshold,
                    const std::string &directory, const std::string &prefix, std::ofstream &manifest) {
    manifest << "[";
    if (!text.empty()) {
        sam3_pcs_params pcs;
        pcs.text_prompt = text;
        pcs.score_threshold = threshold;
        auto result = sam3_segment_pcs(state, model, pcs);
        std::sort(result.detections.begin(), result.detections.end(),
                  [](const sam3_detection &a, const sam3_detection &b) { return a.score > b.score; });
        size_t count = std::min(result.detections.size(), size_t(8));
        for (size_t i = 0; i < count; ++i) {
            const auto &d = result.detections[i];
            const auto name = prefix + std::to_string(i) + ".png";
            if (!sam3_save_mask(d.mask, directory + "/" + name)) return false;
            if (i) manifest << ",";
            manifest << "{\"file\":\"" << name << "\",\"score\":" << d.score << ",\"box\":[" << d.box.x0 << ","
                     << d.box.y0 << "," << d.box.x1 << "," << d.box.y1 << "]}";
        }
    }
    manifest << "]";
    return true;
}

int main(int argc, char **argv) {
    if (argc < 7 || argc > 13) return 2;
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
    const std::string directory = argv[3];
    std::ofstream manifest(directory + "/result.json");
    manifest << "{\"width\":" << image.width << ",\"height\":" << image.height << ",\"candidates\":";
    if (!segment(*state, *model, argv[4], 0.35f, directory, "candidate-", manifest)) return 5;
    manifest << ",\"faces\":";
    if (!segment(*state, *model, std::string(argv[5]) == "1" ? "face" : "", 0.30f, directory, "face-", manifest)) return 5;
    manifest << ",\"skin\":";
    if (!segment(*state, *model, std::string(argv[6]) == "1" ? "skin" : "", 0.30f, directory, "skin-", manifest)) return 5;
    manifest << ",\"concepts\":{";
    for (int i = 7; i < argc; ++i) {
        manifest << (i > 7 ? "," : "") << "\"" << argv[i] << "\":";
        if (!segment(*state, *model, argv[i], 0.30f, directory, "concept-" + std::to_string(i - 7) + "-", manifest)) return 5;
    }
    double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    manifest << "},\"seconds\":" << seconds << "}";
    return manifest.good() ? 0 : 6;
}
