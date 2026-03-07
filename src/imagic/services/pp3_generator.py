"""Per-photo PP3 profile generator — creates a unique RawTherapee profile
tuned to each image's individual characteristics.

Instead of applying a single static preset to every photo, this module
analyses per-photo metrics (exposure, noise, sharpness, ISO, clipping,
histogram brightness, composition) and generates a customised PP3 that
optimizes each image individually.

The generator also supports layering an **artistic color grade** on top
of the technical corrections.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


# ======================================================================
# Artistic color grades
# ======================================================================

@dataclass
class ColorGrade:
    """Definition of an artistic color grading look."""
    name: str
    description: str
    # Tone curve adjustments (RawTherapee parametric curve control points)
    tone_curve: str = ""
    # Vibrance / saturation tweaks
    vibrance_pastels: int = 0
    vibrance_saturated: int = 0
    # Channel mixer RGB adjustments
    channel_mixer_red: str = ""
    channel_mixer_green: str = ""
    channel_mixer_blue: str = ""
    # Vignetting
    vignette_amount: int = 0
    vignette_radius: int = 50
    vignette_strength: int = 1
    # Soft light glow
    soft_light_strength: int = 0
    # Color toning (split toning)
    split_tone_enabled: bool = False
    split_tone_shadow_hue: int = 0
    split_tone_shadow_sat: int = 0
    split_tone_highlight_hue: int = 0
    split_tone_highlight_sat: int = 0
    split_tone_balance: int = 0
    # Film grain
    film_grain_enabled: bool = False
    film_grain_iso: int = 400
    film_grain_strength: int = 25
    # Black & white
    is_bw: bool = False
    bw_method: str = "Luminance"
    # Extra contrast
    contrast_boost: int = 0
    # Saturation override (additive on top of technical)
    saturation_shift: int = 0


# Built-in artistic grades
GRADES: Dict[str, ColorGrade] = {
    "natural": ColorGrade(
        name="Natural",
        description="Clean, true-to-life colours with minimal artistic intervention",
        vibrance_pastels=10,
        vibrance_saturated=5,
        soft_light_strength=5,
    ),
    "film_warm": ColorGrade(
        name="Film Warm",
        description="Warm golden tones reminiscent of Kodak Portra film",
        tone_curve="3;0;0.02;0.25;0.30;0.5;0.54;0.75;0.76;1;0.97;",
        vibrance_pastels=15,
        vibrance_saturated=10,
        soft_light_strength=15,
        split_tone_enabled=True,
        split_tone_shadow_hue=40,
        split_tone_shadow_sat=30,
        split_tone_highlight_hue=50,
        split_tone_highlight_sat=20,
        split_tone_balance=20,
        saturation_shift=5,
    ),
    "film_cool": ColorGrade(
        name="Film Cool",
        description="Cool blue-green tones inspired by Fuji Pro film stocks",
        tone_curve="3;0;0;0.25;0.28;0.5;0.52;0.75;0.74;1;0.98;",
        vibrance_pastels=10,
        vibrance_saturated=8,
        soft_light_strength=12,
        split_tone_enabled=True,
        split_tone_shadow_hue=210,
        split_tone_shadow_sat=25,
        split_tone_highlight_hue=190,
        split_tone_highlight_sat=15,
        split_tone_balance=-10,
        saturation_shift=-5,
    ),
    "moody": ColorGrade(
        name="Moody",
        description="Dark, desaturated look with lifted blacks and teal shadows",
        tone_curve="3;0;0.06;0.20;0.22;0.5;0.48;0.75;0.72;1;0.94;",
        vibrance_pastels=-5,
        vibrance_saturated=-10,
        soft_light_strength=20,
        split_tone_enabled=True,
        split_tone_shadow_hue=190,
        split_tone_shadow_sat=35,
        split_tone_highlight_hue=35,
        split_tone_highlight_sat=15,
        split_tone_balance=-20,
        contrast_boost=15,
        saturation_shift=-15,
    ),
    "vibrant": ColorGrade(
        name="Vibrant",
        description="Punchy, vivid colours with strong contrast — ideal for landscapes",
        tone_curve="3;0;0;0.20;0.15;0.5;0.55;0.80;0.85;1;1;",
        vibrance_pastels=40,
        vibrance_saturated=30,
        soft_light_strength=25,
        contrast_boost=20,
        saturation_shift=15,
    ),
    "cinematic": ColorGrade(
        name="Cinematic",
        description="Teal & orange Hollywood look with subtle film grain",
        tone_curve="3;0;0.03;0.25;0.28;0.5;0.52;0.75;0.73;1;0.96;",
        vibrance_pastels=10,
        vibrance_saturated=5,
        soft_light_strength=18,
        split_tone_enabled=True,
        split_tone_shadow_hue=195,
        split_tone_shadow_sat=40,
        split_tone_highlight_hue=35,
        split_tone_highlight_sat=30,
        split_tone_balance=10,
        film_grain_enabled=True,
        film_grain_iso=400,
        film_grain_strength=20,
        contrast_boost=10,
    ),
    "faded": ColorGrade(
        name="Faded",
        description="Washed-out vintage look with lifted blacks and muted colours",
        tone_curve="3;0;0.08;0.25;0.30;0.5;0.50;0.75;0.70;1;0.92;",
        vibrance_pastels=-10,
        vibrance_saturated=-15,
        soft_light_strength=10,
        split_tone_enabled=True,
        split_tone_shadow_hue=220,
        split_tone_shadow_sat=15,
        split_tone_highlight_hue=45,
        split_tone_highlight_sat=10,
        split_tone_balance=0,
        saturation_shift=-20,
    ),
    "bw_classic": ColorGrade(
        name="B&W Classic",
        description="Timeless black & white with rich tonal range",
        is_bw=True,
        bw_method="Luminance",
        contrast_boost=15,
        soft_light_strength=15,
        film_grain_enabled=True,
        film_grain_iso=200,
        film_grain_strength=15,
    ),
}


# ======================================================================
# Per-photo analysis data
# ======================================================================

@dataclass
class PhotoMetrics:
    """Per-photo metrics used to generate a tailored PP3 profile.

    Populated from the AI analysis results + EXIF data.
    """
    # From quality scorer labels
    sharpness: float = 0.5      # 0-1, higher = sharper
    exposure: float = 0.5       # 0-1, higher = better exposed
    noise: float = 0.5          # 0-1, higher = cleaner
    detail: float = 0.5         # 0-1, higher = more detail
    composition: float = 0.5    # 0-1, higher = better composed

    # From histogram analysis
    mean_brightness: float = 128.0   # 0-255
    clip_dark_pct: float = 0.0       # percentage of near-black pixels
    clip_light_pct: float = 0.0      # percentage of near-white pixels

    # From EXIF
    iso: Optional[int] = None
    aperture: Optional[float] = None
    focal_length: Optional[float] = None
    shutter_speed: Optional[str] = None

    # Auto-crop data (set by auto_crop module)
    crop_x: int = 0
    crop_y: int = 0
    crop_w: int = 0
    crop_h: int = 0
    crop_enabled: bool = False
    image_w: int = 0
    image_h: int = 0


def metrics_from_photo(photo_dict: dict) -> PhotoMetrics:
    """Build ``PhotoMetrics`` from a Photo record's stored data."""
    import json

    m = PhotoMetrics()

    # Parse cull_reasons JSON for per-metric scores
    raw_reasons = photo_dict.get("cull_reasons", "")
    if raw_reasons:
        try:
            reasons = json.loads(raw_reasons) if isinstance(raw_reasons, str) else raw_reasons
            for r in reasons:
                metric = r.get("metric", "").lower()
                score = r.get("score")
                if score is None:
                    continue
                if metric == "sharpness":
                    m.sharpness = score
                elif metric == "exposure":
                    m.exposure = score
                elif metric == "noise":
                    m.noise = score
                elif metric == "detail":
                    m.detail = score
                elif metric == "composition":
                    m.composition = score
        except (json.JSONDecodeError, TypeError):
            pass

    # EXIF
    m.iso = photo_dict.get("exif_iso")
    m.aperture = photo_dict.get("exif_aperture")
    m.focal_length = photo_dict.get("exif_focal_length")
    m.shutter_speed = photo_dict.get("exif_shutter_speed")

    # Derive brightness/clipping from exposure score heuristic
    # (real histogram analysis is done in analyze_photo_histogram)
    m.mean_brightness = 128.0  # default; overridden by histogram analysis

    return m


def analyze_photo_histogram(thumb_path: Path, metrics: PhotoMetrics) -> None:
    """Analyze the thumbnail histogram to populate brightness & clipping."""
    try:
        from PIL import Image
        import numpy as np

        img = Image.open(thumb_path).convert("L")
        arr = np.asarray(img, dtype=np.float64)
        metrics.mean_brightness = float(np.mean(arr))
        metrics.clip_dark_pct = float(np.sum(arr < 5) / arr.size) * 100
        metrics.clip_light_pct = float(np.sum(arr > 250) / arr.size) * 100
        metrics.image_w = img.width
        metrics.image_h = img.height
    except Exception:
        logger.debug("Could not analyze histogram for %s", thumb_path)


# ======================================================================
# PP3 Generator
# ======================================================================

def generate_pp3(
    metrics: PhotoMetrics,
    color_grade: Optional[ColorGrade] = None,
    output_path: Optional[Path] = None,
    manual_overrides: Optional[Dict] = None,
) -> str:
    """Generate a complete RawTherapee PP3 profile tailored to one photo.

    Args:
        metrics: Per-photo analysis metrics.
        color_grade: Optional artistic color grade to layer on top.
        output_path: If provided, write the PP3 to this file.
        manual_overrides: Optional dict of manual slider values that
            shift or override the auto-computed parameters.  Keys:
            ``exposure`` (-100..100), ``contrast`` (-100..100),
            ``saturation`` (-100..100), ``sharpness`` (0..200),
            ``noise_reduction`` (0..100), ``wb_warmth`` (-50..50).

    Returns:
        The PP3 content as a string.
    """
    if color_grade is None:
        color_grade = GRADES["natural"]
    if manual_overrides is None:
        manual_overrides = {}

    # ------------------------------------------------------------------
    # Technical corrections derived from per-photo metrics
    # ------------------------------------------------------------------

    # Detect intentionally dark / low-light scenes.
    # High ISO + some highlight presence = the photographer accepted the
    # darkness (concerts, clubs, moody interiors).  Pushing exposure here
    # just amplifies grain and destroys the mood.
    iso = metrics.iso or 100
    noise_clean = metrics.noise  # 1.0 = clean, 0.0 = very noisy
    brightness_norm = metrics.mean_brightness / 255.0
    has_highlights = metrics.clip_light_pct > 0.5  # bright spots exist
    high_iso = iso >= 1600
    very_high_iso = iso >= 3200

    # dampen_factor: 0.0 = full adjustment, 1.0 = nearly zero adjustment
    # High ISO + existing highlights → strong dampening (intentionally dark)
    # High ISO + noisy → strong dampening (pushing will worsen grain)
    dampen_factor = 0.0
    if very_high_iso and has_highlights:
        # Concert / stage scenario: darkness is intentional, highlights
        # prove the scene has enough light in the focal areas.
        dampen_factor = 0.75
    elif very_high_iso and noise_clean < 0.5:
        # Already noisy at high ISO — any boost amplifies grain badly.
        dampen_factor = 0.65
    elif very_high_iso:
        dampen_factor = 0.50
    elif high_iso and has_highlights:
        dampen_factor = 0.40
    elif high_iso and noise_clean < 0.5:
        dampen_factor = 0.35
    elif high_iso:
        dampen_factor = 0.25

    # Exposure compensation: based on brightness analysis
    if brightness_norm < 0.25:
        raw_exp = 0.4 + (0.25 - brightness_norm) * 3.0  # up to +1.15
        exp_comp = round(raw_exp * (1.0 - dampen_factor), 2)
    elif brightness_norm > 0.75:
        exp_comp = round(-0.3 - (brightness_norm - 0.75) * 2.0, 2)  # down to -0.8
    else:
        exp_comp = round((0.5 - brightness_norm) * 0.6, 2)  # gentle nudge

    # Cap exposure comp for high-ISO to avoid grain blow-up
    if very_high_iso:
        exp_comp = min(exp_comp, 0.25)
    elif high_iso:
        exp_comp = min(exp_comp, 0.45)

    # Brightness boost for dark images — strongly reduced for high ISO
    raw_bright = max(0, (0.5 - brightness_norm) * 30)
    brightness_adj = max(0, int(raw_bright * (1.0 - dampen_factor)))

    # Contrast: boost for flat images, reduce for already-contrasty ones
    base_contrast = 15
    if metrics.exposure < 0.3:
        base_contrast = 10  # underexposed — don't add too much contrast
    elif metrics.exposure > 0.7:
        base_contrast = 20
    contrast = base_contrast + color_grade.contrast_boost

    # Saturation
    saturation = max(-100, min(100, color_grade.saturation_shift))

    # Shadow compression: more conservative for high-ISO dark scenes
    shadow_comp = 50
    if metrics.clip_dark_pct > 5:
        raw_shadow = 50 + metrics.clip_dark_pct * 3
        shadow_comp = min(100, int(raw_shadow * (1.0 - dampen_factor * 0.5)))
    elif brightness_norm < 0.3:
        shadow_comp = int(75 * (1.0 - dampen_factor * 0.5))

    # Highlight compression: protect highlights in bright images
    hl_comp = 60
    if metrics.clip_light_pct > 3:
        hl_comp = min(100, int(60 + metrics.clip_light_pct * 5))

    # Black point — much more restrained for high-ISO to preserve mood
    black = 0
    if brightness_norm < 0.2:
        raw_black = (0.2 - brightness_norm) * 1500
        black = min(300, int(raw_black * (1.0 - dampen_factor)))

    # ------------------------------------------------------------------
    # Noise reduction: scale with ISO and measured noise
    # ------------------------------------------------------------------

    # Luminance NR curve control points
    if iso >= 6400 or noise_clean < 0.3:
        lum_nr = "1;0.15;0.35;0.35;0.35;0.55;0.04;0.35;0.35;"
        lum_detail = 30
    elif iso >= 3200 or noise_clean < 0.5:
        lum_nr = "1;0.10;0.25;0.35;0.35;0.55;0.04;0.35;0.35;"
        lum_detail = 40
    elif iso >= 1600 or noise_clean < 0.65:
        lum_nr = "1;0.06;0.15;0.35;0.35;0.55;0.04;0.35;0.35;"
        lum_detail = 50
    elif iso >= 800:
        lum_nr = "1;0.03;0.08;0.35;0.35;0.55;0.04;0.35;0.35;"
        lum_detail = 60
    else:
        lum_nr = "1;0.01;0.04;0.35;0.35;0.55;0.04;0.35;0.35;"
        lum_detail = 70

    chroma_auto_factor = 1.0
    if iso >= 6400:
        chroma_auto_factor = 1.5
    elif iso >= 3200:
        chroma_auto_factor = 1.2

    # ------------------------------------------------------------------
    # Sharpening: based on measured sharpness
    # ------------------------------------------------------------------
    if metrics.sharpness < 0.2:
        sharp_amount = 200
        sharp_radius = 0.8
        sharp_contrast = 15
    elif metrics.sharpness < 0.45:
        sharp_amount = 160
        sharp_radius = 0.6
        sharp_contrast = 12
    elif metrics.sharpness < 0.7:
        sharp_amount = 130
        sharp_radius = 0.5
        sharp_contrast = 10
    else:
        sharp_amount = 100
        sharp_radius = 0.4
        sharp_contrast = 8

    # Reduce sharpening for high-ISO to avoid amplifying noise
    if iso >= 3200:
        sharp_amount = int(sharp_amount * 0.7)
        sharp_radius = max(0.3, sharp_radius - 0.1)

    # Micro sharpening for detail-poor images
    micro_enabled = metrics.detail < 0.4
    micro_strength = max(20, min(60, int((0.5 - metrics.detail) * 100)))
    micro_contrast = 20

    # ------------------------------------------------------------------
    # Apply manual overrides (shift auto-computed values)
    # ------------------------------------------------------------------
    if manual_overrides:
        # Exposure: slider -100..100 maps to -2.0..+2.0 EV shift
        ov_exp = manual_overrides.get("exposure", 0)
        if ov_exp:
            exp_comp = round(exp_comp + ov_exp / 50.0, 2)

        # Contrast: slider -100..100 added directly
        ov_con = manual_overrides.get("contrast", 0)
        if ov_con:
            contrast = max(-100, min(100, contrast + ov_con))

        # Saturation: slider -100..100 added directly
        ov_sat = manual_overrides.get("saturation", 0)
        if ov_sat:
            saturation = max(-100, min(100, saturation + ov_sat))

        # Sharpness: slider 0..200 scales the computed amount
        ov_sharp = manual_overrides.get("sharpness", 100)
        if ov_sharp != 100:
            sharp_amount = max(0, int(sharp_amount * ov_sharp / 100))

        # Noise reduction: slider 0..100, 0 = disabled
        ov_nr = manual_overrides.get("noise_reduction", 0)
        if ov_nr > 0:
            # Boost luminance NR detail threshold inversely
            lum_detail = max(10, lum_detail - ov_nr // 2)

        # WB warmth: slider -50..+50, shifts temperature
        ov_wb = manual_overrides.get("wb_warmth", 0)
        # Applied later in the WB section

    # ------------------------------------------------------------------
    # Local contrast
    # ------------------------------------------------------------------
    lc_amount = 0.20
    if metrics.detail < 0.3:
        lc_amount = 0.30
    elif metrics.detail > 0.6:
        lc_amount = 0.12

    # ------------------------------------------------------------------
    # Vibrance (technical + artistic)
    # ------------------------------------------------------------------
    vibrance_pastels = max(-100, min(100,
        10 + color_grade.vibrance_pastels))
    vibrance_saturated = max(-100, min(100,
        5 + color_grade.vibrance_saturated))

    # ------------------------------------------------------------------
    # Soft light
    # ------------------------------------------------------------------
    soft_light = max(0, min(100, color_grade.soft_light_strength))
    if metrics.exposure < 0.3:
        soft_light = max(5, soft_light - 5)  # less glow on dark images
    # Further reduce for high-ISO dark scenes to avoid haze
    if dampen_factor > 0:
        soft_light = max(0, int(soft_light * (1.0 - dampen_factor * 0.6)))

    # ------------------------------------------------------------------
    # White balance override
    # ------------------------------------------------------------------
    wb_setting = "Camera"
    wb_temperature = 0
    ov_wb = manual_overrides.get("wb_warmth", 0) if manual_overrides else 0
    if ov_wb:
        # Switch from Camera WB to a custom temperature.
        # 5500K is daylight neutral; shift ± 100K per slider unit.
        wb_setting = "Custom"
        wb_temperature = 5500 + ov_wb * 100

    # ------------------------------------------------------------------
    # Tone curve (artistic)
    # ------------------------------------------------------------------
    tone_curve = color_grade.tone_curve or "0;"

    # ------------------------------------------------------------------
    # Crop (if auto-crop data present)
    # ------------------------------------------------------------------
    crop_section = ""
    if metrics.crop_enabled and metrics.crop_w > 0 and metrics.crop_h > 0:
        crop_section = f"""
[Crop]
Enabled=true
X={metrics.crop_x}
Y={metrics.crop_y}
W={metrics.crop_w}
H={metrics.crop_h}
FixedRatio=false
Guide=Rule of thirds"""

    # ------------------------------------------------------------------
    # Film grain (artistic)
    # ------------------------------------------------------------------
    grain_section = ""
    if color_grade.film_grain_enabled:
        grain_section = f"""
[FilmSimulation]
Enabled=false

[Film Grain]
Enabled=true
ISO={color_grade.film_grain_iso}
Strength={color_grade.film_grain_strength}"""

    # ------------------------------------------------------------------
    # Split toning (artistic)
    # ------------------------------------------------------------------
    color_toning_section = ""
    if color_grade.split_tone_enabled:
        color_toning_section = f"""
[ColorToning]
Enabled=true
Method=Splitlr
LumaMode=true
Satprotectionthreshold=80
Saturatedopacity=80
Strength=50
ShadowsColorSaturation={color_grade.split_tone_shadow_hue};{color_grade.split_tone_shadow_sat};
HighightsColorSaturation={color_grade.split_tone_highlight_hue};{color_grade.split_tone_highlight_sat};
Balance={color_grade.split_tone_balance}"""

    # ------------------------------------------------------------------
    # Black & white (artistic)
    # ------------------------------------------------------------------
    bw_section = ""
    if color_grade.is_bw:
        bw_section = f"""
[Black & White]
Enabled=true
Method={color_grade.bw_method}
Auto=true
ComplementaryColors=true
Luminance Red=33
Luminance Green=33
Luminance Blue=33
GammaRed=0
GammaGreen=0
GammaBlue=0"""

    # ------------------------------------------------------------------
    # Assemble the PP3
    # ------------------------------------------------------------------
    pp3 = f"""[Version]
AppVersion=5.12
Version=351

[General]
Rank=0
ColorLabel=0
InTrash=false

[Exposure]
Auto=false
Compensation={exp_comp}
Brightness={brightness_adj}
Contrast={contrast}
Saturation={saturation}
Black={black}
HighlightCompr={hl_comp}
HighlightComprThreshold=0
ShadowCompr={shadow_comp}
CurveMode=Standard
CurveMode2=Standard
Curve={tone_curve}
Curve2=0;

[HLRecovery]
Enabled=true
Method=Blend

[Local Contrast]
Enabled=true
Radius=80
Amount={lc_amount:.2f}
Darkness=1
Lightness=1

[Sharpening]
Enabled=true
Contrast={sharp_contrast}
Method=usm
Radius={sharp_radius:.1f}
Amount={sharp_amount}
Threshold=20;80;2000;1200;
OnlyEdges=false
HalocontrolEnabled=true
HalocontrolAmount=85

[SharpenMicro]
Enabled={'true' if micro_enabled else 'false'}
Matrix=false
Strength={micro_strength}
Contrast={micro_contrast}
Uniformity=5

[Vibrance]
Enabled=true
Pastels={vibrance_pastels}
Saturated={vibrance_saturated}
PSThreshold=0;75;
ProtectSkins=true
AvoidColorShift=true
PastSatTog=true

[White Balance]
Setting={wb_setting}
Temperature={wb_temperature}
Green=1
Equal=1

[Defringing]
Enabled=true
Radius=2
Threshold=13

[Noise Reduction]
Enabled=true
Enhanced=true
LuminanceMethod=CurveAdj
LCurve={lum_nr}
LuminanceDetail={lum_detail}
LuminanceSmoothing=0
ChrominanceMethod=Automatic
ChrominanceAutoFactor={chroma_auto_factor}
ChrominaanceStrength=1
ChrominanceSmoothingTop=10
ChrominanceSmoothingBottom=3

[PostDemosaicSharpening]
Enabled=true
Contrast=10
AutoContrast=true
AutoRadius=true

[Color Management]
InputProfile=(camera)
ToneCurve=false
ApplyLookTable=true
ApplyBaselineExposureOffset=true
ApplyHueSatMap=true
WorkingProfile=ProPhoto
OutputProfile=RTv4_sRGB
OutputProfileIntent=Perceptual
OutputBPC=true

[Soft Light]
Enabled=true
Strength={soft_light}

[LensProfile]
LcMode=lfauto
UseDistortion=true
UseVignette=true
UseCA=true
{crop_section}
{grain_section}
{color_toning_section}
{bw_section}

[Demosaicing]
Method=amaze

[MetaData]
Mode=0
"""

    # Clean up extra blank lines
    lines = pp3.split("\n")
    cleaned = []
    prev_blank = False
    for line in lines:
        is_blank = line.strip() == ""
        if is_blank and prev_blank:
            continue
        cleaned.append(line)
        prev_blank = is_blank
    pp3 = "\n".join(cleaned)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(pp3, encoding="utf-8")
        logger.info("Generated per-photo PP3: %s", output_path.name)

    return pp3
