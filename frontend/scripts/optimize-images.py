"""
Builds the small, right-sized WebP variants the landing page actually serves.

The originals in public/ are full shoot exports (1.4-1.7k px wide, two of them
2.5 MB PNGs) — fine as sources, far too heavy to send to a phone. This writes:

  public/hero/<slug>-m.webp     phone hero — the exact window the phone shows
                                (the hero is a tall 390x540 frame over a wide photo,
                                object-cover at the slide's focus point), so we
                                crop to it instead of shipping the whole photo
  public/hero/<slug>-1600.webp  tablet / desktop hero
  public/img/<name>-640.webp    service cards / popup

Re-run after replacing a source photo:   python3 scripts/optimize-images.py
(needs `pip install pillow`).
"""
from pathlib import Path

from PIL import Image

PUBLIC = Path(__file__).resolve().parent.parent / "public"

# slug -> (source file in public/, horizontal focus point %, same values as the
# heroImagePosition map in LandingSections.tsx)
HERO = {
    "home": ("wash-image.png", 68),
    "star-wash": ("car-wash.png", 68),
    "deep-cleaning": ("hero-img3.webp", 64),
    "waterless": ("hero-img4.webp", 62),
    "jet-wash": ("service-jet.webp", 62),
    "bike-wash": ("service-bike.webp", 66),
}
PHONE_FRAME = 390 / 540  # width / height of the phone hero frame
CARDS = ["service-jet", "service-star", "service-deepclean", "service-4", "service-5", "service-bike", "service-waterless"]


def save(src: Path, dest: Path, width: int, quality: int) -> None:
    im = Image.open(src).convert("RGB")
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest, "WEBP", quality=quality, method=6)
    print(f"{dest.relative_to(PUBLIC)!s:40s} {im.width}x{im.height}  {dest.stat().st_size // 1024} KB")


def save_phone_crop(src: Path, dest: Path, focus_x: int, quality: int) -> None:
    im = Image.open(src).convert("RGB")
    window = min(im.width, round(im.height * PHONE_FRAME))
    left = round((im.width - window) * focus_x / 100)
    im = im.crop((left, 0, left + window, im.height))
    if im.height > 1000:
        im = im.resize((round(im.width * 1000 / im.height), 1000), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest, "WEBP", quality=quality, method=6)
    print(f"{dest.relative_to(PUBLIC)!s:40s} {im.width}x{im.height}  {dest.stat().st_size // 1024} KB")


for slug, (source, focus_x) in HERO.items():
    save_phone_crop(PUBLIC / source, PUBLIC / "hero" / f"{slug}-m.webp", focus_x, 72)
    save(PUBLIC / source, PUBLIC / "hero" / f"{slug}-1600.webp", 1600, 68)
for name in CARDS:
    save(PUBLIC / f"{name}.webp", PUBLIC / "img" / f"{name}-640.webp", 640, 74)

# The wordmark: 64 KB PNG -> a few KB WebP (it is shown ~28-140 px wide, keeps alpha).
logo = Image.open(PUBLIC / "blussit-logo.png").convert("RGBA")
logo = logo.resize((480, round(logo.height * 480 / logo.width)), Image.LANCZOS)
logo.save(PUBLIC / "img" / "blussit-logo-480.webp", "WEBP", quality=90, method=6)
print(f"img/blussit-logo-480.webp                {logo.width}x{logo.height}  {(PUBLIC / 'img' / 'blussit-logo-480.webp').stat().st_size // 1024} KB")
