
from flask import Flask, render_template, request, send_from_directory
from PIL import Image, ImageDraw, ImageFont
import os, uuid, subprocess, logging, time, shutil, re

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024

OUT = "output"
UP = "uploads"
os.makedirs(OUT, exist_ok=True)
os.makedirs(UP, exist_ok=True)
logging.basicConfig(level=logging.INFO)

W, H, FPS = 720, 1280, 15

def font_path():
    for p in [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]:
        if os.path.exists(p):
            return p
    return None

FONT_PATH = font_path()

def get_font(size):
    if FONT_PATH:
        return ImageFont.truetype(FONT_PATH, size)
    return ImageFont.load_default()

def safe_text(s, n=100):
    return (s or "").replace("\n", " ").strip()[:n]

def validate_exact_url(u):
    if not u or u != u.strip():
        return False
    return bool(re.match(r"^https?://\S+$", u))

def normalize_product(product):
    return safe_text(product, 50)

def auto_copy(product, brand=""):
    p = normalize_product(product)
    b = safe_text(brand, 30)

    # 과장/최저가/보장 표현 없이 비교·확인 중심
    title = f"{p} 조건 확인하기"
    hook = f"{p}, 선택 전에 이것부터 확인하세요"
    point1 = "조건과 이용 내용을 먼저 비교해보세요"
    point2 = "내 상황에 맞는 선택인지 확인해보세요"
    cta = "자세한 내용은 바로가기에서 확인"
    brand_line = f"{b} 관련 정보 확인" if b else ""
    return {
        "title": title,
        "hook": hook,
        "point1": point1,
        "point2": point2,
        "cta": cta,
        "brand_line": brand_line,
    }

def fit_cover(im, zoom=1.0, dx=0, dy=0):
    im = im.convert("RGB")
    scale = max(W / im.width, H / im.height) * zoom
    nw, nh = int(im.width * scale), int(im.height * scale)
    im = im.resize((nw, nh), Image.Resampling.LANCZOS)
    x = max(0, min(nw - W, (nw - W)//2 + dx))
    y = max(0, min(nh - H, (nh - H)//2 + dy))
    return im.crop((x, y, x + W, y + H))

def measure(draw, text, f):
    box = draw.textbbox((0, 0), text, font=f)
    return box[2] - box[0]

def wrap_korean(draw, text, max_width, max_lines=3):
    """한국어 단어 단위 우선 줄바꿈. 단어가 너무 길 때만 글자 단위 분할."""
    text = safe_text(text, 120)
    if not text:
        return [""]
    words = text.split()
    lines = []
    current = ""

    # dynamic font size
    for size in [52, 48, 44, 40, 36]:
        f = get_font(size)
        lines = []
        current = ""
        ok = True
        for word in words:
            test = word if not current else current + " " + word
            if measure(draw, test, f) <= max_width:
                current = test
            else:
                if current:
                    lines.append(current)
                    current = ""
                # 긴 단어만 글자 단위 분할
                if measure(draw, word, f) > max_width:
                    part = ""
                    for ch in word:
                        t = part + ch
                        if measure(draw, t, f) <= max_width:
                            part = t
                        else:
                            if part:
                                lines.append(part)
                            part = ch
                    current = part
                else:
                    current = word
            if len(lines) >= max_lines:
                ok = False
                break
        if current and len(lines) < max_lines:
            lines.append(current)

        if ok and len(lines) <= max_lines:
            return lines, f

    return lines[:max_lines], get_font(36)

def draw_caption_card(im, caption, idx):
    d = ImageDraw.Draw(im, "RGBA")

    # 상단 광고표기
    d.rounded_rectangle((22, 22, 210, 82), radius=24, fill=(0,0,0,150))
    d.text((42, 36), "광고 · 제휴", font=get_font(26), fill="white")

    # 하단 자막 카드
    card_top = H - 355
    d.rounded_rectangle((28, card_top, W-28, H-92), radius=30, fill=(0,0,0,170))

    lines, f = wrap_korean(d, caption, W-110, max_lines=3)
    line_h = int(f.size * 1.35) if hasattr(f, "size") else 58
    total_h = line_h * len(lines)
    y = card_top + max(26, ((H-92-card_top) - total_h)//2)

    for line in lines:
        tw = measure(d, line, f)
        d.text(((W-tw)//2, y), line, font=f, fill="white")
        y += line_h

    return im

def build_scene(img_path, caption, idx):
    # 여러 장이면 순환, 한 장이면 확대/위치 변화
    zooms = [1.00, 1.08, 1.14, 1.05, 1.12, 1.16]
    dxs = [0, 32, -32, 18, -18, 0]
    dys = [0, -24, 24, -12, 12, -28]

    im = Image.open(img_path)
    im = fit_cover(im, zooms[idx % len(zooms)], dxs[idx % len(dxs)], dys[idx % len(dys)])
    return draw_caption_card(im, caption, idx)

def create_video(form, files):
    t0 = time.time()

    product = normalize_product(form.get("product"))
    brand = safe_text(form.get("brand"), 30)
    url = form.get("url", "")
    if not product:
        raise ValueError("상품명을 입력하세요.")
    if not validate_exact_url(url):
        raise ValueError("DBsense 원본 링크를 수정 없이 입력하세요. 앞뒤 공백도 허용하지 않습니다.")
    if not files or not files[0].filename:
        raise ValueError("DBsense/광고주 사용 허가 이미지를 1장 이상 올려주세요.")

    defaults = auto_copy(product, brand)
    title = safe_text(form.get("title"), 70) or defaults["title"]
    hook = safe_text(form.get("hook"), 80) or defaults["hook"]
    point1 = safe_text(form.get("point1"), 80) or defaults["point1"]
    point2 = safe_text(form.get("point2"), 80) or defaults["point2"]
    cta = safe_text(form.get("cta"), 80) or defaults["cta"]

    duration = int(form.get("duration", "15"))
    duration = 15 if duration not in (15, 30) else duration

    job = uuid.uuid4().hex[:10]
    jobdir = os.path.join(UP, job)
    os.makedirs(jobdir)

    try:
        paths = []
        for i, f in enumerate(files[:8]):
            p = os.path.join(jobdir, f"img{i}.jpg")
            Image.open(f.stream).convert("RGB").save(p, quality=92)
            paths.append(p)

        logging.info("VIDEO_START job=%s images=%s duration=%s", job, len(paths), duration)

        if duration == 15:
            captions = [hook, point1, point2, cta]
        else:
            captions = [
                hook,
                point1,
                "핵심 조건을 한 번 더 확인해보세요",
                point2,
                "비교 후 내 상황에 맞게 선택하세요",
                cta
            ]

        scene_count = len(captions)
        seg = duration / scene_count

        scene_files = []
        for i, caption in enumerate(captions):
            scene_path = os.path.join(jobdir, f"scene{i}.jpg")
            build_scene(paths[i % len(paths)], caption, i).save(scene_path, quality=90)
            scene_files.append(scene_path)

        concat = os.path.join(jobdir, "list.txt")
        with open(concat, "w", encoding="utf-8") as f:
            for p in scene_files:
                f.write(f"file '{os.path.basename(p)}'\n")
                f.write(f"duration {seg:.6f}\n")
            f.write(f"file '{os.path.basename(scene_files[-1])}'\n")

        out = f"danggeun_v7_{job}_{duration}s.mp4"
        outp = os.path.join(OUT, out)

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat,
            "-vf", f"fps={FPS},format=yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-crf", "25",
            "-t", str(duration),
            "-movflags", "+faststart",
            outp
        ]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=180,
            text=True
        )
        if proc.returncode != 0:
            logging.error("FFMPEG_ERROR job=%s stderr=%s", job, (proc.stderr or "")[-3000:])
            raise RuntimeError("FFmpeg 영상 생성에 실패했습니다.")

        elapsed = round(time.time() - t0, 1)
        logging.info("VIDEO_DONE job=%s sec=%s file=%s", job, elapsed, out)

        return {
            "filename": out,
            "seconds": elapsed,
            "title": title,
            "hook": hook,
            "point1": point1,
            "point2": point2,
            "cta": cta,
            "product": product,
            "brand": brand,
            "url": url,
            "duration": duration,
        }
    finally:
        shutil.rmtree(jobdir, ignore_errors=True)

@app.after_request
def no_cache(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/healthz")
def health():
    return "ok", 200

@app.route("/video-form", methods=["POST"])
def video_form():
    try:
        result = create_video(request.form, request.files.getlist("images"))
        return render_template("result.html", **result)
    except Exception as e:
        logging.exception("VIDEO_FORM_ERROR")
        return render_template("result.html", error=str(e)), 500

@app.route("/download/<path:name>")
def download(name):
    return send_from_directory(OUT, name, as_attachment=True, download_name=name, conditional=False)

@app.route("/output/<path:name>")
def output(name):
    return send_from_directory(OUT, name, as_attachment=False, conditional=False)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
