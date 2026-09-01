
from flask import Flask, render_template, request, send_from_directory
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os, uuid, subprocess, logging, time, shutil, re, math

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024

OUT="output"
UP="uploads"
os.makedirs(OUT, exist_ok=True)
os.makedirs(UP, exist_ok=True)
logging.basicConfig(level=logging.INFO)

W,H,FPS=720,1280,15

def font_path():
    for p in [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]:
        if os.path.exists(p):
            return p
    return None

FONT_PATH=font_path()

def get_font(size):
    if FONT_PATH:
        return ImageFont.truetype(FONT_PATH,size)
    return ImageFont.load_default()

def safe_text(s,n=100):
    return (s or "").replace("\n"," ").strip()[:n]

def validate_exact_url(u):
    if not u or u != u.strip():
        return False
    return bool(re.match(r"^https?://\S+$",u))

def auto_copy(product,brand=""):
    p=safe_text(product,50)
    return {
        "title": f"{p} 조건 확인하기",
        "hook": f"{p}, 선택 전에 이것부터 확인하세요",
        "point1": "조건과 이용 내용을 먼저 비교해보세요",
        "point2": "내 상황에 맞는 선택인지 확인해보세요",
        "cta": "자세한 내용은 바로가기에서 확인"
    }

def fit_inside(im, max_w, max_h):
    im=im.convert("RGB")
    scale=min(max_w/im.width,max_h/im.height)
    nw,nh=max(1,int(im.width*scale)),max(1,int(im.height*scale))
    return im.resize((nw,nh),Image.Resampling.LANCZOS)

def make_vertical_base(src_path,out_path):
    """한 장의 광고 이미지를 최대한 보존하면서 9:16 세로 캔버스 제작.
    배경은 동일 이미지를 확대+블러, 전경은 원본을 온전히 표시."""
    src=Image.open(src_path).convert("RGB")

    # blurred background cover
    scale=max(W/src.width,H/src.height)
    bg=src.resize((int(src.width*scale),int(src.height*scale)),Image.Resampling.LANCZOS)
    x=(bg.width-W)//2
    y=(bg.height-H)//2
    bg=bg.crop((x,y,x+W,y+H)).filter(ImageFilter.GaussianBlur(28))

    # dark veil for caption readability
    veil=Image.new("RGBA",(W,H),(0,0,0,55))
    canvas=bg.convert("RGBA")
    canvas=Image.alpha_composite(canvas,veil)

    # foreground original with margin
    fg=fit_inside(src,W-72,H-250)
    fx=(W-fg.width)//2
    fy=(H-fg.height)//2 - 20

    # soft shadow
    shadow=Image.new("RGBA",(fg.width+28,fg.height+28),(0,0,0,0))
    sd=ImageDraw.Draw(shadow)
    sd.rounded_rectangle((14,14,fg.width+14,fg.height+14),radius=22,fill=(0,0,0,90))
    shadow=shadow.filter(ImageFilter.GaussianBlur(12))
    canvas.alpha_composite(shadow,(fx-14,fy-14))

    fg_rgba=fg.convert("RGBA")
    canvas.alpha_composite(fg_rgba,(fx,fy))
    canvas.convert("RGB").save(out_path,quality=92)

def measure(draw,text,f):
    b=draw.textbbox((0,0),text,font=f)
    return b[2]-b[0]

def wrap_words(draw,text,maxw,max_lines=3):
    words=safe_text(text,120).split()
    for size in [50,46,42,38,34]:
        f=get_font(size)
        lines=[]; cur=""
        for word in words:
            t=word if not cur else cur+" "+word
            if measure(draw,t,f)<=maxw:
                cur=t
            else:
                if cur: lines.append(cur)
                cur=word
        if cur: lines.append(cur)
        if len(lines)<=max_lines and all(measure(draw,l,f)<=maxw for l in lines):
            return lines,f
    return lines[:max_lines],get_font(34)

def make_caption_overlay(text,out_path,kind="normal"):
    ov=Image.new("RGBA",(W,H),(0,0,0,0))
    d=ImageDraw.Draw(ov,"RGBA")
    if kind=="hook":
        top=120; bottom=420
        d.rounded_rectangle((40,top,W-40,bottom),radius=34,fill=(0,0,0,175))
    else:
        top=H-380; bottom=H-120
        d.rounded_rectangle((40,top,W-40,bottom),radius=34,fill=(0,0,0,175))

    lines,f=wrap_words(d,text,W-120,3)
    lh=int(getattr(f,"size",40)*1.35)
    total=lh*len(lines)
    y=top+(bottom-top-total)//2
    for line in lines:
        tw=measure(d,line,f)
        d.text(((W-tw)//2,y),line,font=f,fill="white")
        y+=lh

    # disclosure badge
    d.rounded_rectangle((22,22,205,78),radius=24,fill=(0,0,0,160))
    d.text((40,34),"광고 · 제휴",font=get_font(24),fill="white")
    ov.save(out_path)

def make_motion_clip(base_path, overlay_path, out_path, seconds, style):
    frames=int(seconds*FPS)
    # render-friendly zoompan. Every clip is 720x1280 / 15fps.
    # style changes x/y direction and zoom progression.
    if style==0:   # slow zoom in
        z="min(zoom+0.0009,1.10)"; x="iw/2-(iw/zoom/2)"; y="ih/2-(ih/zoom/2)"
    elif style==1: # pan right + slight zoom
        z="min(zoom+0.0006,1.07)"; x="(iw-iw/zoom)*on/{n}".format(n=max(1,frames-1)); y="ih/2-(ih/zoom/2)"
    elif style==2: # pan left + slight zoom
        z="min(zoom+0.0006,1.07)"; x="(iw-iw/zoom)*(1-on/{n})".format(n=max(1,frames-1)); y="ih/2-(ih/zoom/2)"
    else:          # gentle zoom out simulation
        z="if(eq(on,0),1.08,max(zoom-0.0007,1.00))"; x="iw/2-(iw/zoom/2)"; y="ih/2-(ih/zoom/2)"

    filt=(
        f"[0:v]scale={W}:{H},zoompan=z='{z}':x='{x}':y='{y}':"
        f"d=1:s={W}x{H}:fps={FPS}[m];"
        f"[1:v]scale={W}:{H}[o];"
        f"[m][o]overlay=0:0:format=auto,format=yuv420p"
    )
    cmd=[
        "ffmpeg","-y",
        "-loop","1","-i",base_path,
        "-loop","1","-i",overlay_path,
        "-filter_complex",filt,
        "-t",f"{seconds:.3f}",
        "-r",str(FPS),
        "-c:v","libx264","-preset","ultrafast","-crf","26",
        "-pix_fmt","yuv420p",
        out_path
    ]
    proc=subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=180,text=True)
    if proc.returncode!=0:
        logging.error("FFMPEG_CLIP_ERROR stderr=%s",(proc.stderr or "")[-2500:])
        raise RuntimeError("영상 움직임 생성에 실패했습니다.")

def concat_clips(clips,out_path,duration):
    list_path=os.path.join(os.path.dirname(clips[0]),"clips.txt")
    with open(list_path,"w",encoding="utf-8") as f:
        for p in clips:
            f.write(f"file '{os.path.basename(p)}'\n")

    cmd=[
        "ffmpeg","-y","-f","concat","-safe","0","-i",list_path,
        "-c","copy","-t",str(duration),"-movflags","+faststart",out_path
    ]
    proc=subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=180,text=True)
    if proc.returncode!=0:
        # fallback re-encode
        cmd=[
            "ffmpeg","-y","-f","concat","-safe","0","-i",list_path,
            "-vf",f"fps={FPS},format=yuv420p",
            "-c:v","libx264","-preset","ultrafast","-crf","26",
            "-t",str(duration),"-movflags","+faststart",out_path
        ]
        proc=subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=180,text=True)
        if proc.returncode!=0:
            logging.error("FFMPEG_CONCAT_ERROR stderr=%s",(proc.stderr or "")[-2500:])
            raise RuntimeError("영상 합치기에 실패했습니다.")

def create_video(form,files):
    t0=time.time()
    product=safe_text(form.get("product"),50)
    brand=safe_text(form.get("brand"),30)
    url=form.get("url","")
    if not product:
        raise ValueError("상품명을 입력하세요.")
    if not validate_exact_url(url):
        raise ValueError("DBsense 원본 링크를 수정 없이 입력하세요. 앞뒤 공백도 허용하지 않습니다.")
    if not files or not files[0].filename:
        raise ValueError("DBsense/광고주 사용 허가 이미지를 올려주세요.")

    defaults=auto_copy(product,brand)
    title=safe_text(form.get("title"),70) or defaults["title"]
    hook=safe_text(form.get("hook"),80) or defaults["hook"]
    p1=safe_text(form.get("point1"),80) or defaults["point1"]
    p2=safe_text(form.get("point2"),80) or defaults["point2"]
    cta=safe_text(form.get("cta"),80) or defaults["cta"]
    duration=int(form.get("duration","15"))
    duration=15 if duration not in (15,30) else duration

    job=uuid.uuid4().hex[:10]
    jobdir=os.path.join(UP,job)
    os.makedirs(jobdir)

    try:
        # V8 핵심: DBsense 이미지 1장 전용. 여러 장이면 첫 4장까지 각 장면에 사용.
        img_paths=[]
        for i,f in enumerate(files[:4]):
            p=os.path.join(jobdir,f"src{i}.jpg")
            Image.open(f.stream).convert("RGB").save(p,quality=94)
            img_paths.append(p)

        if duration==15:
            captions=[hook,p1,p2,cta]
        else:
            captions=[hook,p1,"핵심 조건을 한 번 더 확인해보세요",p2,"비교 후 결정하세요",cta]

        sec=duration/len(captions)
        clips=[]
        logging.info("VIDEO_START_V8 job=%s images=%s duration=%s",job,len(img_paths),duration)

        for i,caption in enumerate(captions):
            src=img_paths[i % len(img_paths)]
            base=os.path.join(jobdir,f"base{i}.jpg")
            overlay=os.path.join(jobdir,f"overlay{i}.png")
            clip=os.path.join(jobdir,f"clip{i}.mp4")
            make_vertical_base(src,base)
            make_caption_overlay(caption,overlay,"hook" if i==0 else "normal")
            make_motion_clip(base,overlay,clip,sec,i%4)
            clips.append(clip)

        out=f"danggeun_v8_{job}_{duration}s.mp4"
        outp=os.path.join(OUT,out)
        concat_clips(clips,outp,duration)

        elapsed=round(time.time()-t0,1)
        logging.info("VIDEO_DONE_V8 job=%s sec=%s file=%s",job,elapsed,out)
        return {
            "filename":out,"seconds":elapsed,"duration":duration,
            "title":title,"hook":hook,"point1":p1,"point2":p2,"cta":cta,
            "url":url,"product":product,"image_count":len(img_paths)
        }
    finally:
        shutil.rmtree(jobdir,ignore_errors=True)

@app.after_request
def no_cache(resp):
    resp.headers["Cache-Control"]="no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"]="no-cache"
    resp.headers["Expires"]="0"
    return resp

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/healthz")
def health():
    return "ok",200

@app.route("/video-form",methods=["POST"])
def video_form():
    try:
        result=create_video(request.form,request.files.getlist("images"))
        return render_template("result.html",**result)
    except Exception as e:
        logging.exception("VIDEO_FORM_ERROR_V8")
        return render_template("result.html",error=str(e)),500

@app.route("/download/<path:name>")
def download(name):
    return send_from_directory(OUT,name,as_attachment=True,download_name=name,conditional=False)

@app.route("/output/<path:name>")
def output(name):
    return send_from_directory(OUT,name,as_attachment=False,conditional=False)

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","10000")))
