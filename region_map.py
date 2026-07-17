#!/usr/bin/env python3
"""Generates a PNG per dimension showing which regions (32x32-chunk .mca
files) have been generated, colored by how recently each region file was
last modified (a rough proxy for "when was this last visited/touched").

Only looks at region file existence + mtime, not actual chunk contents -
this is a "what has been touched, and how recently" map, not a biome or
terrain map. Spawn (block 0,0) is marked with a red dot.

Run manually: `python3 region_map.py`. Output goes to config.MAP_OUTPUT_DIR.
Sending these to Discord periodically is a planned future step, not done here.
"""

import datetime
import os
import re
import statistics

from PIL import Image, ImageDraw, ImageFont

import config

REGION_RE = re.compile(r"r\.(-?\d+)\.(-?\d+)\.mca$")

# Age-gradient colors: oldest modified regions are cool/dim, most recently
# modified are warm/bright. Never-generated regions stay pure background.
OLD_COLOR = (45, 65, 120)
NEW_COLOR = (255, 215, 90)
MAP_BACKGROUND_COLOR = (8, 8, 10)
FRAME_COLOR = (58, 61, 71)
SPAWN_COLOR = (230, 30, 30)

FONT_DIR = "/usr/share/fonts/truetype/dejavu"
TITLE_FONT = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")
BODY_FONT = os.path.join(FONT_DIR, "DejaVuSans.ttf")

# UI chrome (padding, fonts, legend) is sized relative to MAP_SCALE so it
# stays legible - these constants were tuned by eye at the old MAP_SCALE=3;
# UI_SCALE_REF preserves that same look as MAP_SCALE changes.
UI_SCALE_REF = 3
PAD = 12
TITLE_HEIGHT = 30
SUBTITLE_LINE_HEIGHT = 16
LEGEND_HEIGHT = 60
TITLE_FONT_SIZE = 20
BODY_FONT_SIZE = 13


def parse_region_coords(region_dir):
    """Returns a list of (x, z, filename, mtime)."""
    coords = []
    for fn in os.listdir(region_dir):
        match = REGION_RE.match(fn)
        if match:
            path = os.path.join(region_dir, fn)
            coords.append((int(match.group(1)), int(match.group(2)), fn, os.path.getmtime(path)))
    return coords


def split_outliers(coords):
    """Separates (x, z, filename, mtime) entries into (main_cluster, outliers)
    using a median/MAD-based robust threshold per axis, independent of scale
    so it adapts to each dimension's normal spread (Nether coords are 1/8 of
    the Overworld's, for example)."""
    if len(coords) < 3:
        return coords, []

    xs = [c[0] for c in coords]
    zs = [c[1] for c in coords]
    median_x, median_z = statistics.median(xs), statistics.median(zs)
    mad_x = statistics.median(abs(x - median_x) for x in xs)
    mad_z = statistics.median(abs(z - median_z) for z in zs)
    # 1.4826 converts MAD to a std-dev-equivalent under a normal distribution.
    threshold_x = max(config.MAP_OUTLIER_MIN_THRESHOLD, config.MAP_OUTLIER_MAD_MULTIPLIER * 1.4826 * mad_x)
    threshold_z = max(config.MAP_OUTLIER_MIN_THRESHOLD, config.MAP_OUTLIER_MAD_MULTIPLIER * 1.4826 * mad_z)

    main_cluster, outliers = [], []
    for entry in coords:
        x, z = entry[0], entry[1]
        if abs(x - median_x) > threshold_x or abs(z - median_z) > threshold_z:
            outliers.append(entry)
        else:
            main_cluster.append(entry)
    return main_cluster, outliers


def _lerp_color(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _age_color(mtime, min_mtime, max_mtime):
    if max_mtime == min_mtime:
        t = 1.0
    else:
        t = (mtime - min_mtime) / (max_mtime - min_mtime)
    return _lerp_color(OLD_COLOR, NEW_COLOR, t)


def render_map(label, main_cluster, output_path, outlier_count=0, scale=None):
    scale = scale or config.MAP_SCALE
    ui_scale = max(1.0, scale / UI_SCALE_REF)
    pad = round(PAD * ui_scale)
    title_height = round(TITLE_HEIGHT * ui_scale)
    subtitle_line_height = round(SUBTITLE_LINE_HEIGHT * ui_scale)
    legend_height = round(LEGEND_HEIGHT * ui_scale)
    title_font_size = round(TITLE_FONT_SIZE * ui_scale)
    body_font_size = round(BODY_FONT_SIZE * ui_scale)

    xs = [c[0] for c in main_cluster]
    zs = [c[1] for c in main_cluster]
    mtimes = [c[3] for c in main_cluster]
    min_x, max_x = min(xs), max(xs)
    min_z, max_z = min(zs), max(zs)
    min_mtime, max_mtime = min(mtimes), max(mtimes)

    width_regions = max_x - min_x + 1
    height_regions = max_z - min_z + 1
    map_width = width_regions * scale
    map_height = height_regions * scale

    title_font = ImageFont.truetype(TITLE_FONT, title_font_size)
    body_font = ImageFont.truetype(BODY_FONT, body_font_size)
    title_text = label.replace("_", " ").upper()
    # One item per line rather than one long joined line - these images are
    # often small (Nether/End/etc.), where a single "a | b | c | d" line would
    # be wider than the map itself.
    blocks_per_region = 512  # 32 chunks/region x 16 blocks/chunk
    width_blocks_k = width_regions * blocks_per_region / 1000
    height_blocks_k = height_regions * blocks_per_region / 1000
    subtitle_lines = [
        f"{width_regions} x {height_regions} regions",
        f"{width_blocks_k:.1f}k x {height_blocks_k:.1f}k blocks",
        f"{len(main_cluster)} regions generated",
    ]
    if outlier_count:
        subtitle_lines.append(f"{outlier_count} outlier(s) excluded")
    subtitle_height = len(subtitle_lines) * subtitle_line_height + round(6 * ui_scale)

    # Small maps (Nether/End/etc.) can be narrower than their own title text -
    # widen the canvas to fit whichever is widest, map stays left-aligned.
    content_width = max(
        map_width,
        round(title_font.getlength(title_text)),
        max(round(body_font.getlength(line)) for line in subtitle_lines),
    )

    canvas_width = content_width + 2 * pad
    canvas_height = title_height + subtitle_height + map_height + legend_height + 2 * pad

    img = Image.new("RGB", (canvas_width, canvas_height), FRAME_COLOR)
    map_img = Image.new("RGB", (map_width, map_height), MAP_BACKGROUND_COLOR)
    map_pixels = map_img.load()

    for x, z, _fn, mtime in main_cluster:
        color = _age_color(mtime, min_mtime, max_mtime)
        px = (x - min_x) * scale
        pz = (z - min_z) * scale
        for dx in range(scale):
            for dz in range(scale):
                map_pixels[px + dx, pz + dz] = color

    map_offset = (pad, pad + title_height + subtitle_height)
    img.paste(map_img, map_offset)

    draw = ImageDraw.Draw(img)
    # Thin outline so the map area reads as a distinct "window" in the frame.
    outline_width = max(1, round(ui_scale))
    draw.rectangle(
        [map_offset[0] - outline_width, map_offset[1] - outline_width, map_offset[0] + map_width, map_offset[1] + map_height],
        outline=(110, 113, 122),
        width=outline_width,
    )

    # Spawn marker (block 0,0), if within the mapped bounds.
    if min_x <= 0 <= max_x and min_z <= 0 <= max_z:
        spawn_px = map_offset[0] + (0 - min_x) * scale
        spawn_py = map_offset[1] + (0 - min_z) * scale
        r = max(scale, 4)
        draw.ellipse([spawn_px - r, spawn_py - r, spawn_px + r, spawn_py + r], fill=SPAWN_COLOR, outline=(0, 0, 0))

    draw.text((pad, pad - round(2 * ui_scale)), title_text, font=title_font, fill=(255, 255, 255))
    for i, line in enumerate(subtitle_lines):
        draw.text((pad, pad + title_height + i * subtitle_line_height), line, font=body_font, fill=(190, 190, 190))

    # Age legend: a horizontal gradient bar with oldest/newest dates.
    legend_bar_height = round(14 * ui_scale)
    legend_y = pad + title_height + subtitle_height + map_height + round(8 * ui_scale)
    legend_width = min(round(240 * ui_scale), content_width)
    for i in range(legend_width):
        color = _lerp_color(OLD_COLOR, NEW_COLOR, i / max(1, legend_width - 1))
        draw.line([(pad + i, legend_y), (pad + i, legend_y + legend_bar_height)], fill=color)
    oldest_label = datetime.datetime.fromtimestamp(min_mtime).strftime("%Y-%m-%d")
    newest_label = datetime.datetime.fromtimestamp(max_mtime).strftime("%Y-%m-%d")
    draw.text((pad, legend_y + legend_bar_height + round(4 * ui_scale)), f"oldest: {oldest_label}", font=body_font, fill=(190, 190, 190))
    draw.text((pad, legend_y + legend_bar_height + round(18 * ui_scale)), f"newest: {newest_label}", font=body_font, fill=(190, 190, 190))

    img.save(output_path)
    # Layout in image-pixel space, kept internal (never published as-is) -
    # login_logger.py uses this to turn a live player's raw block x/z into
    # an approximate on-image percentage position without ever writing the
    # exact coordinates anywhere public. Blocks-per-region is fixed at
    # 32 chunks x 16 blocks = 512.
    layout = {
        "min_x_region": min_x,
        "min_z_region": min_z,
        "blocks_per_region": 512,
        "scale": scale,
        "map_offset_x": map_offset[0],
        "map_offset_y": map_offset[1],
        "canvas_width": canvas_width,
        "canvas_height": canvas_height,
    }
    return width_regions, height_regions, layout


def generate(label, region_subpath):
    region_dir = os.path.join(config.WORLD_DIR, region_subpath)
    if not os.path.isdir(region_dir):
        print(f"{label}: no region folder at {region_dir}, skipping")
        return None

    coords = parse_region_coords(region_dir)
    if not coords:
        print(f"{label}: no region files found")
        return None

    main_cluster, outliers = split_outliers(coords)
    os.makedirs(config.MAP_OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(config.MAP_OUTPUT_DIR, f"{label}.png")
    width_regions, height_regions, layout = render_map(label, main_cluster, output_path, outlier_count=len(outliers))

    print(
        f"{label}: {len(main_cluster)} regions mapped ({width_regions}x{height_regions} regions) -> {output_path}"
    )
    if outliers:
        print(f"{label}: {len(outliers)} outlier region(s) excluded from the map:")
        for x, z, fn, _mtime in outliers:
            print(f"    {fn} (x={x}, z={z})")

    return layout


def main():
    layouts = {}
    for label, region_subpath in config.MAP_DIMENSIONS:
        layout = generate(label, region_subpath)
        if layout is not None:
            layouts[label] = layout
    return layouts


if __name__ == "__main__":
    main()
