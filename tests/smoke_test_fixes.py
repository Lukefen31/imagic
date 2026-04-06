"""Smoke tests for all v0.4.0 fixes.

Run: python tests/smoke_test_fixes.py
"""
import sys
import os
import re
import inspect
import importlib

# Ensure src is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  -- {detail}")


# =====================================================================
print("\n=== TEST 1: Adaptive Presets — param keys match slider keys ===")
# =====================================================================
from imagic.ai.adaptive_presets import SceneType, get_adaptive_preset

VALID_SLIDER_KEYS = {
    "temperature", "tint", "exposure", "contrast", "highlights", "shadows",
    "whites", "blacks", "texture", "clarity", "dehaze", "vibrance", "saturation",
    "sharp_amount", "sharp_radius", "nr_luminance", "nr_color",
    "hsl_hue_red", "hsl_hue_orange", "hsl_hue_yellow", "hsl_hue_green",
    "hsl_hue_aqua", "hsl_hue_blue", "hsl_hue_purple", "hsl_hue_magenta",
    "hsl_sat_red", "hsl_sat_orange", "hsl_sat_yellow", "hsl_sat_green",
    "hsl_sat_aqua", "hsl_sat_blue", "hsl_sat_purple", "hsl_sat_magenta",
    "hsl_lum_red", "hsl_lum_orange", "hsl_lum_yellow", "hsl_lum_green",
    "hsl_lum_aqua", "hsl_lum_blue", "hsl_lum_purple", "hsl_lum_magenta",
    "split_shadow_hue", "split_shadow_sat", "split_highlight_hue",
    "split_highlight_sat", "split_balance", "vignette_amount",
    "vignette_midpoint", "grain_amount", "rotation", "distortion",
    "perspective_h", "perspective_v",
}

for scene in SceneType:
    preset = get_adaptive_preset(scene)
    bad = [k for k in preset.global_params if k not in VALID_SLIDER_KEYS]
    check(f"Preset {scene.value} global keys", len(bad) == 0,
          f"invalid keys: {bad}")
    for ma in preset.masked_adjustments:
        bad_m = [k for k in ma.get("params", {}) if k not in VALID_SLIDER_KEYS]
        check(f"Preset {scene.value} masked keys", len(bad_m) == 0,
              f"invalid masked keys: {bad_m}")
    # Values should be integers suitable for sliders (not tiny floats)
    for k, v in preset.global_params.items():
        check(f"Preset {scene.value}.{k} is int-range",
              abs(v) >= 1 or v == 0,
              f"value {v} looks like a float fraction, not an integer slider value")


# =====================================================================
print("\n=== TEST 2: FaceBox uses .width/.height (not .w/.h) ===")
# =====================================================================
from imagic.ai.face_detection import FaceBox

fb = FaceBox(x=10, y=20, width=100, height=120, confidence=0.9)
check("FaceBox.width exists", hasattr(fb, "width"), "missing .width")
check("FaceBox.height exists", hasattr(fb, "height"), "missing .height")
check("FaceBox has no .w", not hasattr(fb, "w"), "still has .w attribute")
check("FaceBox has no .h", not hasattr(fb, "h"), "still has .h attribute")
check("FaceBox.width value", fb.width == 100)
check("FaceBox.height value", fb.height == 120)


# =====================================================================
print("\n=== TEST 3: Lens blur accepts integer blur_amount ===")
# =====================================================================
from imagic.ai.lens_blur import apply_lens_blur
sig = inspect.signature(apply_lens_blur)
blur_param = sig.parameters.get("blur_amount")
check("blur_amount param exists", blur_param is not None)
check("blur_amount default is int", isinstance(blur_param.default, int),
      f"default is {type(blur_param.default).__name__}: {blur_param.default}")


# =====================================================================
print("\n=== TEST 4: Lens blur runs with int amount on test image ===")
# =====================================================================
import numpy as np
test_img = np.random.randint(0, 255, (64, 64, 3), dtype=np.uint8)
try:
    result = apply_lens_blur(test_img, blur_amount=50)
    check("Lens blur runs OK with int", result.image.shape == (64, 64, 3))
    check("Lens blur depth map shape", result.depth_map.shape == (64, 64))
except Exception as e:
    check("Lens blur runs OK with int", False, str(e))


# =====================================================================
print("\n=== TEST 5: Face detection runs on test image ===")
# =====================================================================
from imagic.ai.face_detection import detect_faces
try:
    fd_result = detect_faces(test_img)
    check("Face detection returns result", fd_result is not None)
    check("Face detection has faces list", isinstance(fd_result.faces, list))
    # With random noise, probably no faces — that's fine, just no crash
    check("Face detection no crash", True)
except Exception as e:
    check("Face detection no crash", False, str(e))


# =====================================================================
print("\n=== TEST 6: Color wheels sizing fits 320px sidebar ===")
# =====================================================================
# Read the source to check _WHEEL_SIZE
cw_path = os.path.join(os.path.dirname(__file__), "..", "src",
                        "imagic", "views", "widgets", "color_wheels.py")
cw_src = open(cw_path, encoding="utf-8").read()
match = re.search(r"_WHEEL_SIZE\s*=\s*(\d+)", cw_src)
if match:
    wheel_size = int(match.group(1))
    single_width = wheel_size + 16  # setFixedWidth(_WHEEL_SIZE + 16)
    total = single_width * 3 + 2 * 2  # 3 wheels + spacing(2) * 2 gaps
    check(f"Wheel size={wheel_size}, single={single_width}, total={total}",
          total <= 320,
          f"total {total}px exceeds 320px sidebar")
else:
    check("_WHEEL_SIZE found", False, "not found in source")


# =====================================================================
print("\n=== TEST 7: No blue tones in style constants ===")
# =====================================================================
BLUE_PATTERNS = [
    "#12121a", "#1e1e2e", "#16162a", "#1a1a28", "#252538",
    "#1c1c32", "#222230", "#14141e", "#0f0f18", "#2a2a3a",
    "#3a3a4a", "#8888a0", "#e0e0e8",
]

files_to_check = {
    "photo_editor.py": os.path.join(os.path.dirname(__file__), "..", "src",
                                     "imagic", "views", "photo_editor.py"),
    "color_wheels.py": cw_path,
    "export_dialog.py": os.path.join(os.path.dirname(__file__), "..", "src",
                                      "imagic", "views", "export_dialog.py"),
}
for fname, fpath in files_to_check.items():
    src = open(fpath, encoding="utf-8").read()
    found = [p for p in BLUE_PATTERNS if p in src]
    check(f"No blue tones in {fname}", len(found) == 0,
          f"found: {found}")


# =====================================================================
print("\n=== TEST 8: No _display_item references in photo_editor ===")
# =====================================================================
pe_path = files_to_check["photo_editor.py"]
pe_src = open(pe_path, encoding="utf-8").read()
check("No _display_item in photo_editor",
      "_display_item" not in pe_src,
      "still references _display_item")


# =====================================================================
print("\n=== TEST 9: Panel order in _build_panels ===")
# =====================================================================
# Extract panel section names in order from the source
panel_names = re.findall(
    r'_CollapsibleSection\("([^"]+)"\)', pe_src
)
# Find the ordering of the main panels (before EXPERT sections)
main_panels = []
for name in panel_names:
    if name in ("ADVANCED TONE", "ADVANCED DETAIL", "RAW ENGINE"):
        break
    main_panels.append(name)

expected_order = [
    "COLOR GRADE", "BASIC", "PRESENCE", "HSL / COLOR", "DETAIL",
    "COLOR GRADING", "EFFECTS", "TONE CURVE", "COLOR WHEELS",
    "LENS & GEOMETRY", "AI TOOLS",
]
check("Panel order correct", main_panels == expected_order,
      f"got: {main_panels}")


# =====================================================================
print("\n=== TEST 10: _AITaskWorker class exists in photo_editor ===")
# =====================================================================
check("_AITaskWorker class defined",
      "class _AITaskWorker(QThread):" in pe_src)

# Check that AI handlers use the worker (not inline blocking)
for handler in ["_ai_masking", "_ai_lens_blur", "_ai_face_detect",
                "_ai_super_resolution", "_ai_enhance_details",
                "_ai_adaptive_preset"]:
    # Find the method body and check it creates a worker
    pattern = rf"def {handler}\(self\).*?(?=\n    def |\nclass |\Z)"
    match = re.search(pattern, pe_src, re.DOTALL)
    if match:
        body = match.group(0)
        uses_worker = "_AITaskWorker(" in body and "worker.start()" in body
        check(f"{handler} uses threaded worker", uses_worker,
              "still runs inline / no worker.start()")
    else:
        check(f"{handler} found", False, "method not found")


# =====================================================================
print("\n=== TEST 11: Adaptive presets — scene detection runs ===")
# =====================================================================
from imagic.ai.adaptive_presets import detect_scene
try:
    # Dark image → should detect NIGHT
    dark_img = np.full((64, 64, 3), 20, dtype=np.uint8)
    scene = detect_scene(dark_img)
    check("Dark image → NIGHT scene", scene == SceneType.NIGHT,
          f"got {scene}")

    # Bright colorful → should detect something (not crash)
    bright_img = np.random.randint(100, 255, (64, 64, 3), dtype=np.uint8)
    scene2 = detect_scene(bright_img)
    check("Bright image scene detection", scene2 in list(SceneType),
          f"got {scene2}")
except Exception as e:
    check("Scene detection no crash", False, str(e))


# =====================================================================
print("\n=== TEST 12: Glassomorphic styling uses rgba() ===")
# =====================================================================
check("Header uses rgba()",
      "rgba(30, 30, 30, 180)" in pe_src or "rgba(30,30,30,180)" in pe_src,
      "header not using rgba glassomorphic style")
check("Toolbar uses rgba()",
      "rgba(18, 18, 18, 220)" in pe_src or "rgba(18,18,18,220)" in pe_src,
      "toolbar not using rgba glassomorphic style")


# =====================================================================
# Summary
# =====================================================================
print(f"\n{'='*60}")
print(f"RESULTS: {PASS} passed, {FAIL} failed out of {PASS+FAIL} checks")
print(f"{'='*60}")
sys.exit(1 if FAIL > 0 else 0)
