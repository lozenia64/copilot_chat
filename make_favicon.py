"""Generate favicon.ico for copilot_chat — Copilot-style head on blue background."""
from PIL import Image, ImageDraw

SIZES = [16, 32, 48]
BLUE = (9, 105, 218)       # #0969da
WHITE = (255, 255, 255)
DARK = (26, 35, 50)        # near-black for detail


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size

    # --- background: rounded square ---
    r = s * 0.22  # corner radius
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=r, fill=BLUE)

    # --- head outline: rounded rect, centred, upper 60 % ---
    pad = s * 0.14
    head_l = pad
    head_r = s - pad
    head_t = s * 0.10
    head_b = s * 0.68
    hr = s * 0.16
    d.rounded_rectangle([head_l, head_t, head_r, head_b], radius=hr, fill=WHITE)

    # --- inner face cutout (dark) ---
    ip = s * 0.24
    d.rounded_rectangle([ip, s * 0.18, s - ip, s * 0.60], radius=s * 0.10, fill=DARK)

    # --- eyes (white dots on dark) ---
    ew = s * 0.11
    ey = s * 0.30
    # left eye
    lx = s * 0.33
    d.ellipse([lx - ew, ey - ew, lx + ew, ey + ew], fill=WHITE)
    # right eye
    rx = s * 0.67
    d.ellipse([rx - ew, ey - ew, rx + ew, ey + ew], fill=WHITE)

    # --- "antenna" ears: two small rounded bumps on top ---
    bump_w = s * 0.09
    bump_h = s * 0.09
    for bx in (s * 0.32, s * 0.68):
        d.ellipse([bx - bump_w, head_t - bump_h, bx + bump_w, head_t + bump_h * 0.5], fill=WHITE)

    # --- body stub at bottom ---
    body_t = head_b - s * 0.04
    body_b = s - s * 0.08
    body_l = s * 0.28
    body_r = s - s * 0.28
    d.rounded_rectangle([body_l, body_t, body_r, body_b], radius=s * 0.10, fill=WHITE)

    return img


frames = [draw_icon(sz) for sz in SIZES]
out = "static/favicon.ico"
frames[0].save(
    out,
    format="ICO",
    sizes=[(sz, sz) for sz in SIZES],
    append_images=frames[1:],
)
print(f"Saved {out}  ({SIZES})")
