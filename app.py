
from flask import Flask, render_template, request, jsonify, send_from_directory
from PIL import Image, ImageDraw, ImageFont
import os, uuid, subprocess, logging, time, shutil, re

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024
OUT="output"; UP="uploads"
os.makedirs(OUT, exist_ok=True); os.makedirs(UP, exist_ok=True)
logging.basicConfig(level=logging.INFO)

W,H,FPS=720,1280,15

def font(size):
    for p in ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        if os.path.exists(p):
            return ImageFont.truetype(p,size)
    return ImageFont.load_default()

def validate_url_exact(u):
    if not u or u != u.strip(): return False
    return bool(re.match(r"^https?://\S+$",u))

def safe_text(s, n=80):
    return (s or "").replace("\n"," ").strip()[:n]

def make_copy(product, brand):
    p=safe_text(product,50); b=safe_text(brand,30)
    return [
      f"{p} 조건 확인하기", f"{p} 비교해보기", f"{p} 알아보기",
      f"{p} 선택 전 체크", f"{p} 렌탈 조건 보기",
      f"{b} {p} 정보 확인" if b else f"{p} 정보 확인",
      f"{p} 내게 맞는지 확인", f"{p} 혜택 조건 확인",
      f"{p} 신청 전 확인", f"{p} 자세히 알아보기"
    ]

def fit_cover(im, zoom=1.0, dx=0, dy=0):
    im=im.convert("RGB")
    scale=max(W/im.width,H/im.height)*zoom
    nw,nh=int(im.width*scale),int(im.height*scale)
    im=im.resize((nw,nh),Image.Resampling.LANCZOS)
    x=max(0,min(nw-W,(nw-W)//2+dx)); y=max(0,min(nh-H,(nh-H)//2+dy))
    return im.crop((x,y,x+W,y+H))

def wrap(draw, txt, f, maxw):
    lines=[]; cur=""
    for ch in list(txt):
        t=cur+ch
        if draw.textbbox((0,0),t,font=f)[2] <= maxw: cur=t
        else:
            if cur: lines.append(cur)
            cur=ch
    if cur: lines.append(cur)
    return lines[:3]

def frame(img_path, caption, badge, idx):
    im=Image.open(img_path)
    zoom=[1.00,1.08,1.14,1.05,1.12][idx%5]
    dx=[0,35,-35,20,-20][idx%5]; dy=[0,-30,30,-15,15][idx%5]
    im=fit_cover(im,zoom,dx,dy)
    d=ImageDraw.Draw(im,"RGBA")
    d.rectangle((0,0,W,92), fill=(0,0,0,125))
    d.text((28,25), badge, font=font(28), fill="white")
    d.rounded_rectangle((28,H-360,W-28,H-105), radius=28, fill=(0,0,0,165))
    f=font(48)
    lines=wrap(d,caption,f,W-100)
    y=H-320
    for line in lines:
        box=d.textbbox((0,0),line,font=f); tw=box[2]-box[0]
        d.text(((W-tw)//2,y),line,font=f,fill="white"); y+=66
    return im

@app.route("/")
def home(): return render_template("index.html")

@app.route("/healthz")
def health(): return "ok",200

@app.route("/api/generate", methods=["POST"])
def generate():
    data=request.get_json(force=True)
    product=safe_text(data.get("product"),50); brand=safe_text(data.get("brand"),30)
    url=data.get("url","")
    if not product: return jsonify(error="상품명을 입력하세요."),400
    if not validate_url_exact(url): return jsonify(error="DBsense 원본 링크를 수정 없이 입력하세요. 앞뒤 공백도 허용하지 않습니다."),400
    return jsonify(titles=make_copy(product,brand), brand=brand, url=url,
                   cta=data.get("cta","바로가기"), disclosure="[광고/제휴 링크]")

@app.route("/api/video", methods=["POST"])
def video():
    t0=time.time()
    job=None
    try:
        product=safe_text(request.form.get("product"),50)
        title=safe_text(request.form.get("title"),70)
        hook=safe_text(request.form.get("hook"),70) or f"{product}, 선택 전 확인하세요"
        point1=safe_text(request.form.get("point1"),70) or "조건을 먼저 비교해보세요"
        point2=safe_text(request.form.get("point2"),70) or "내 상황에 맞는지 체크"
        cta=safe_text(request.form.get("cta"),40) or "자세한 조건은 바로가기"
        duration=int(request.form.get("duration","15"))
        duration=15 if duration not in (15,30) else duration
        files=request.files.getlist("images")
        if not files or not files[0].filename:
            return jsonify(error="광고 이미지를 1장 이상 올려주세요."),400

        job=uuid.uuid4().hex[:10]
        jobdir=os.path.join(UP,job)
        os.makedirs(jobdir)
        paths=[]
        for i,f in enumerate(files[:8]):
            p=os.path.join(jobdir,f"img{i}.jpg")
            Image.open(f.stream).convert("RGB").save(p,quality=92)
            paths.append(p)

        logging.info("VIDEO_START job=%s images=%s duration=%s",job,len(paths),duration)

        captions=[hook,point1,point2,cta]
        scene_count=4 if duration==15 else 6
        if duration==30:
            captions=[hook,point1,point2,"핵심 조건을 한 번 더 확인","비교 후 결정하세요",cta]
        seg=duration/scene_count
        concat=os.path.join(jobdir,"list.txt")
        scene_files=[]

        for i in range(scene_count):
            p=os.path.join(jobdir,f"scene{i}.jpg")
            frame(paths[i%len(paths)],captions[i],"광고 · 제휴",i).save(p,quality=90)
            scene_files.append(p)

        # 중요: concat demuxer는 list.txt의 폴더를 기준으로 상대경로를 해석합니다.
        # 따라서 uploads/<job>/scene0.jpg 전체 상대경로가 아니라 파일명만 기록합니다.
        with open(concat,"w",encoding="utf-8") as f:
            for p in scene_files:
                f.write(f"file '{os.path.basename(p)}'\n")
                f.write(f"duration {seg:.6f}\n")
            f.write(f"file '{os.path.basename(scene_files[-1])}'\n")

        out=f"danggeun_v6_1_{job}_{duration}s.mp4"
        outp=os.path.join(OUT,out)
        cmd=["ffmpeg","-y","-f","concat","-safe","0","-i",concat,
             "-vf",f"fps={FPS},format=yuv420p","-c:v","libx264","-preset","ultrafast",
             "-crf","25","-t",str(duration),"-movflags","+faststart",outp]

        try:
            subprocess.run(cmd,check=True,stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE,timeout=180,text=True)
        except subprocess.CalledProcessError as e:
            logging.error("FFMPEG_ERROR job=%s code=%s stderr=%s",
                          job,e.returncode,(e.stderr or "")[-3000:])
            raise

        shutil.rmtree(jobdir,ignore_errors=True)
        logging.info("VIDEO_DONE job=%s sec=%.1f",job,time.time()-t0)
        return jsonify(ok=True,file=f"/output/{out}",seconds=round(time.time()-t0,1))

    except subprocess.TimeoutExpired:
        logging.exception("VIDEO_TIMEOUT job=%s",job)
        return jsonify(error="영상 생성 제한시간을 초과했습니다."),504
    except Exception as e:
        logging.exception("VIDEO_ERROR job=%s",job)
        return jsonify(error=f"영상 생성 오류: {str(e)[:180]}"),500

@app.route("/output/<path:name>")
def output(name): return send_from_directory(OUT,name,as_attachment=False)

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","10000")))
