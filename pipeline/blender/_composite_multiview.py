"""External compositor — reads a manifest of 4 tile PNGs and produces a
2x2 composite. Run via Blender's bundled Python (which has Pillow).

USAGE:
    python _composite_multiview.py <manifest.txt> <out.png>

manifest.txt lines: "label: /path/to/tile.png"
Order expected: persp, front, right, top.
"""
import sys, os
from PIL import Image, ImageDraw, ImageFont


def composite(manifest_path, out_path):
    tiles = []
    labels = []
    with open(manifest_path) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            label, path = line.split(":", 1)
            labels.append(label.strip())
            tiles.append(path.strip())
    imgs = [Image.open(p) for p in tiles]
    w, h = imgs[0].size
    grid = Image.new("RGB", (w * 2, h * 2), (24, 24, 28))
    grid.paste(imgs[0], (0, 0))
    grid.paste(imgs[1], (w, 0))
    grid.paste(imgs[2], (0, h))
    grid.paste(imgs[3], (w, h))
    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except Exception:
        font = ImageFont.load_default()
    draw = ImageDraw.Draw(grid)
    label_display = ("PERSPECTIVE", "FRONT (-Y)", "RIGHT (+X)", "TOP (+Z)")
    positions = [(8, 8), (w + 8, 8), (8, h + 8), (w + 8, h + 8)]
    for (x, y), lbl in zip(positions, label_display):
        draw.rectangle([x - 2, y - 2, x + 130, y + 22], fill=(0, 0, 0))
        draw.text((x, y), lbl, fill=(255, 255, 255), font=font)
    grid.save(out_path)
    return out_path


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        out = composite(sys.argv[1], sys.argv[2])
        print(out)
    else:
        # If first arg is a directory, composite every manifest in it
        d = sys.argv[1]
        for name in sorted(os.listdir(d)):
            if not name.endswith(".manifest.txt"): continue
            mp = os.path.join(d, name)
            op = mp.replace(".manifest.txt", ".png")
            composite(mp, op)
            print(op)
