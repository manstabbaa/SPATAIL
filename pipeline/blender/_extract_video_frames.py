import cv2, os, sys

video_path = sys.argv[1]
out_dir = sys.argv[2]
n_samples = int(sys.argv[3]) if len(sys.argv) > 3 else 8

os.makedirs(out_dir, exist_ok=True)
cap = cv2.VideoCapture(video_path)
total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
fps = cap.get(cv2.CAP_PROP_FPS)
w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
print(f"total={total} fps={fps} res={w}x{h}")

sample_frames = [int(i * (total - 1) / max(1, n_samples - 1)) for i in range(n_samples)]
for f in sample_frames:
    cap.set(cv2.CAP_PROP_POS_FRAMES, f)
    ok, img = cap.read()
    if not ok:
        continue
    new_w = 640
    new_h = int(h * 640 / w)
    img_small = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    out_path = os.path.join(out_dir, f"frame_{f:05d}.png").replace("\\", "/")
    cv2.imwrite(out_path, img_small)
    print(out_path)
cap.release()
