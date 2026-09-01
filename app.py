
from flask import Flask, render_template, request, send_from_directory
from PIL import Image, ImageDraw, ImageFont, ImageFilter
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

def auto_copy(product, brand=""):
    p = safe_text(product, 50)
    return {
        "title": f"{p} 자세히 알아보기",
        "hook": f"{p}, 먼저 확인해보세요",
        "point1": "핵심 내용을 확인해보세요",
        "point2": "조건과 상세 내용을 확인해보세요",
        "cta": "자세한 내용 확인하기",
    }

def fit_inside(im, max_w, max_h):
    scale = min(max_w / im.width, max_h / im.height)
    return im.resize(
        (max(1, int(im.width * scale)), max(1, int(im.height * scale))),
        Image.Resampling.LANCZOS,
    )

def make_master_image(src_path, out_path):
    src = Image.open(src_path).convert("RGB")

    # blurred vertical background
    scale = max(W / src.width, H / src.height)
    bg = src.resize(
        (int(src.width * scale), int(src.height * scale)),
        Image.Resampling.LANCZOS,
    )
    x = max(0, (bg.width - W) // 2)
    y = max(0, (bg.height - H) // 2)
    bg = bg.crop((x, y, x + W, y + H)).filter(ImageFilter.GaussianBlur(24)).convert("RGBA")
    bg.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, 38)))

    # keep the original ad readable
    fg = fit_inside(src, W - 58, H - 190)
    fx = (W - fg.width) // 2
    fy = (H - fg.height) // 2 - 10
    fy = max(72, min(H - fg.height - 72, fy))

    shadow = Image.new("RGBA", (fg.width + 26, fg.height + 26), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((13, 13, fg.width + 13, fg.height + 13), radius=20, fill=(0, 0, 0, 80))
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    bg.alpha_composite(shadow, (fx - 13, fy - 13))
    bg.alpha_composite(fg.convert("RGBA"), (fx, fy))

    # disclosure badge only. Scene captions are added by ffmpeg drawtext once.
    d = ImageDraw.Draw(bg, "RGBA")
    d.rounded_rectangle((20, 20, 178, 68), radius=20, fill=(0, 0, 0, 150))
    d.text((36, 29), "광고 · 제휴", font=get_font(20), fill="white")

    bg.convert("RGB").save(out_path, quality=92)

def esc_drawtext(s):
    # Escape characters that are meaningful to ffmpeg drawtext.
    s = safe_text(s, 90)
    return (
        s.replace("\\", r"\\")
         .replace(":", r"\:")
         .replace("'", r"\'")
         .replace("%", r"\%")
    )

def create_video(form, files):
    t0 = time.time()
    product = safe_text(form.get("product"), 50)
    brand = safe_text(form.get("brand"), 30)
    url = form.get("url", "")

    if not product:
        raise ValueError("상품명을 입력하세요.")
    if not validate_exact_url(url):
        raise ValueError("DBsense 원본 링크를 수정 없이 입력하세요. 앞뒤 공백도 허용하지 않습니다.")
    if not files or not files[0].filename:
        raise ValueError("DBsense/광고주 사용 허가 이미지를 올려주세요.")

    defaults = auto_copy(product, brand)
    title = safe_text(form.get("title"), 70) or defaults["title"]
    hook = safe_text(form.get("hook"), 65) or defaults["hook"]
    p1 = safe_text(form.get("point1"), 65) or defaults["point1"]
    p2 = safe_text(form.get("point2"), 65) or defaults["point2"]
    cta = safe_text(form.get("cta"), 65) or defaults["cta"]

    duration = int(form.get("duration", "15"))
    duration = 15 if duration not in (15, 30) else duration

    job = uuid.uuid4().hex[:10]
    jobdir = os.path.join(UP, job)
    os.makedirs(jobdir)

    try:
        src = os.path.join(jobdir, "src.jpg")
        Image.open(files[0].stream).convert("RGB").save(src, quality=94)

        master = os.path.join(jobdir, "master.jpg")
        make_master_image(src, master)

        out = f"danggeun_v10_light_{job}_{duration}s.mp4"
        outp = os.path.join(OUT, out)

        logging.info("VIDEO_START_V10_LIGHT job=%s duration=%s", job, duration)

        # One ffmpeg invocation only.
        # Gentle continuous zoom + four short caption windows.
        q1 = duration * 0.25
        q2 = duration * 0.50
        q3 = duration * 0.75

        filters = [
            f"scale={W}:{H}",
            f"zoompan=z='min(zoom+0.00028,1.028)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={FPS}",
        ]

        fontopt = f":fontfile='{FONT_PATH}'" if FONT_PATH else ""
        captions = [
            (0, q1, hook),
            (q1, q2, p1),
            (q2, q3, p2),
            (q3, duration, cta),
        ]

        for start, end, text in captions:
            txt = esc_drawtext(text)
            filters.append(
                "drawbox="
                "x=44:y=h-235:w=w-88:h=145:"
                "color=black@0.48:t=fill:"
                f"enable='between(t,{start:.3f},{end:.3f})'"
            )
            filters.append(
                "drawtext="
                f"text='{txt}'{fontopt}:"
                "fontcolor=white:fontsize=34:"
                "x=(w-text_w)/2:y=h-190:"
                "borderw=1:bordercolor=black@0.55:"
                f"enable='between(t,{start:.3f},{end:.3f})'"
            )

        vf = ",".join(filters)

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", master,
            "-vf", vf,
            "-t", str(duration),
            "-r", str(FPS),
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "27",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            outp,
        ]

        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=150,
            text=True,
        )
        if proc.returncode != 0:
            logging.error("FFMPEG_ERROR_V10_LIGHT job=%s stderr=%s", job, (proc.stderr or "")[-3000:])
            raise RuntimeError("영상 생성에 실패했습니다.")

        elapsed = round(time.time() - t0, 1)
        logging.info("VIDEO_DONE_V10_LIGHT job=%s sec=%s file=%s", job, elapsed, out)

        return {
            "filename": out,
            "seconds": elapsed,
            "duration": duration,
            "title": title,
            "hook": hook,
            "point1": p1,
            "point2": p2,
            "cta": cta,
            "url": url,
            "product": product,
            "image_count": 1,
            "mode": "V10 경량",
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
        logging.exception("VIDEO_FORM_ERROR_V10_LIGHT")
        return render_template("result.html", error=str(e)), 500

@app.route("/download/<path:name>")
def download(name):
    return send_from_directory(OUT, name, as_attachment=True, download_name=name, conditional=False)

@app.route("/output/<path:name>")
def output(name):
    return send_from_directory(OUT, name, as_attachment=False, conditional=False)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
