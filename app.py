from flask import Flask, render_template, request, send_from_directory
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
import os, uuid, subprocess, logging, time, shutil, re, math

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024
OUT="output"; UP="uploads"
os.makedirs(OUT, exist_ok=True); os.makedirs(UP, exist_ok=True)
logging.basicConfig(level=logging.INFO)

W,H,FPS=720,1280,15

def font_path():
    for p in ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        if os.path.exists(p): return p
    return None
FONT_PATH=font_path()

def get_font(size):
    return ImageFont.truetype(FONT_PATH,size) if FONT_PATH else ImageFont.load_default()

def safe_text(s,n=100):
    return (s or "").replace("\n"," ").strip()[:n]

def validate_exact_url(u):
    return bool(u and u==u.strip() and re.match(r"^https?://\S+$",u))

def auto_copy(product,brand=""):
    p=safe_text(product,50)
    return {
        "title": f"{p} 확인하기",
        "hook": f"{p}, 먼저 확인해보세요",
        "point1": "조건부터 확인",
        "point2": "나에게 맞는지 체크",
        "cta": "자세한 내용 확인하기"
    }

def cover_crop(src, zoom=1.0, focus_x=.5, focus_y=.5):
    src=src.convert("RGB")
    scale=max(W/src.width,H/src.height)*zoom
    rw,rh=max(W,int(src.width*scale)),max(H,int(src.height*scale))
    im=src.resize((rw,rh),Image.Resampling.LANCZOS)
    maxx=max(0,rw-W); maxy=max(0,rh-H)
    x=int(maxx*min(1,max(0,focus_x))); y=int(maxy*min(1,max(0,focus_y)))
    return im.crop((x,y,x+W,y+H))

def preserved_scene(src, zoom=1.0, focus_x=.5, focus_y=.5):
    # V9: 광고 원본을 보존하되 배경과 전경의 크기/위치를 장면마다 다르게 구성
    bg=cover_crop(src,1.05,focus_x,focus_y).filter(ImageFilter.GaussianBlur(24))
    bg=ImageEnhance.Brightness(bg).enhance(.72).convert("RGBA")
    maxw=int((W-54)*zoom); maxh=int((H-210)*zoom)
    ratio=min(maxw/src.width,maxh/src.height)
    fg=src.resize((max(1,int(src.width*ratio)),max(1,int(src.height*ratio))),Image.Resampling.LANCZOS)
    canvas=bg
    fx=(W-fg.width)//2
    # slight vertical scene variation without cutting the original ad
    available=max(0,H-fg.height-150)
    fy=70+int(available*focus_y*.55)
    shadow=Image.new("RGBA",(fg.width+28,fg.height+28),(0,0,0,0))
    sd=ImageDraw.Draw(shadow)
    sd.rounded_rectangle((14,14,fg.width+14,fg.height+14),radius=20,fill=(0,0,0,95))
    shadow=shadow.filter(ImageFilter.GaussianBlur(12))
    canvas.alpha_composite(shadow,(fx-14,fy-14))
    canvas.alpha_composite(fg.convert("RGBA"),(fx,fy))
    return canvas.convert("RGB")

def make_scene(src_path,out_path,idx,total):
    src=Image.open(src_path).convert("RGB")
    # five virtual camera framings for a single DBsense image
    presets=[
        (0.94,.50,.18), # establish
        (1.03,.48,.30), # upper information
        (1.08,.52,.50), # center emphasis
        (1.03,.50,.70), # lower information
        (0.96,.50,.50), # return to whole ad
    ]
    z,fx,fy=presets[idx % len(presets)]
    preserved_scene(src,z,fx,fy).save(out_path,quality=93)

def measure(draw,text,f):
    b=draw.textbbox((0,0),text,font=f); return b[2]-b[0]

def wrap_words(draw,text,maxw,max_lines=2):
    words=safe_text(text,90).split()
    for size in [42,38,34,30]:
        f=get_font(size); lines=[]; cur=""
        for word in words:
            t=word if not cur else cur+" "+word
            if measure(draw,t,f)<=maxw: cur=t
            else:
                if cur: lines.append(cur)
                cur=word
        if cur: lines.append(cur)
        if len(lines)<=max_lines and all(measure(draw,l,f)<=maxw for l in lines):
            return lines,f
    return lines[:max_lines],get_font(30)

def make_caption_overlay(text,out_path,kind="normal"):
    # V9: large black panels removed; compact bottom caption chip
    ov=Image.new("RGBA",(W,H),(0,0,0,0)); d=ImageDraw.Draw(ov,"RGBA")
    lines,f=wrap_words(d,text,W-150,2)
    lh=int(getattr(f,"size",34)*1.3); total=max(lh,lh*len(lines))
    bottom=H-70; top=bottom-total-48
    d.rounded_rectangle((48,top,W-48,bottom),radius=26,fill=(0,0,0,150))
    y=top+24
    for line in lines:
        tw=measure(d,line,f)
        d.text(((W-tw)//2,y),line,font=f,fill="white")
        y+=lh
    d.rounded_rectangle((20,20,176,68),radius=22,fill=(0,0,0,145))
    d.text((36,29),"광고 · 제휴",font=get_font(21),fill="white")
    ov.save(out_path)

def make_motion_clip(base_path,overlay_path,out_path,seconds,style):
    frames=max(1,int(seconds*FPS))
    # gentle motion only: no aggressive crop/zoom
    if style==0:
        z="min(zoom+0.00045,1.035)"; x="iw/2-(iw/zoom/2)"; y="ih/2-(ih/zoom/2)"
    elif style==1:
        z="min(zoom+0.00035,1.03)"; x=f"(iw-iw/zoom)*on/{max(1,frames-1)}"; y="ih/2-(ih/zoom/2)"
    elif style==2:
        z="min(zoom+0.00035,1.03)"; x=f"(iw-iw/zoom)*(1-on/{max(1,frames-1)})"; y="ih/2-(ih/zoom/2)"
    elif style==3:
        z="min(zoom+0.0003,1.025)"; x="iw/2-(iw/zoom/2)"; y=f"(ih-ih/zoom)*on/{max(1,frames-1)}"
    else:
        z="if(eq(on,0),1.03,max(zoom-0.00035,1.00))"; x="iw/2-(iw/zoom/2)"; y="ih/2-(ih/zoom/2)"
    filt=(f"[0:v]scale={W}:{H},zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={FPS}[m];"
          f"[1:v]scale={W}:{H}[o];[m][o]overlay=0:0:format=auto,format=yuv420p")
    cmd=["ffmpeg","-y","-loop","1","-i",base_path,"-loop","1","-i",overlay_path,
         "-filter_complex",filt,"-t",f"{seconds:.3f}","-r",str(FPS),
         "-c:v","libx264","-preset","ultrafast","-crf","26","-pix_fmt","yuv420p",out_path]
    proc=subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=180,text=True)
    if proc.returncode!=0:
        logging.error("FFMPEG_CLIP_ERROR %s",(proc.stderr or "")[-2500:])
        raise RuntimeError("영상 장면 생성에 실패했습니다.")

def concat_clips(clips,out_path,duration):
    list_path=os.path.join(os.path.dirname(clips[0]),"clips.txt")
    with open(list_path,"w",encoding="utf-8") as f:
        for p in clips: f.write(f"file '{os.path.basename(p)}'\n")
    cmd=["ffmpeg","-y","-f","concat","-safe","0","-i",list_path,
         "-vf",f"fps={FPS},format=yuv420p","-c:v","libx264","-preset","ultrafast",
         "-crf","26","-t",str(duration),"-movflags","+faststart",out_path]
    proc=subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=180,text=True)
    if proc.returncode!=0:
        logging.error("FFMPEG_CONCAT_ERROR %s",(proc.stderr or "")[-2500:])
        raise RuntimeError("영상 합치기에 실패했습니다.")

def create_video(form,files):
    t0=time.time()
    product=safe_text(form.get("product"),50); brand=safe_text(form.get("brand"),30)
    url=form.get("url","")
    if not product: raise ValueError("상품명을 입력하세요.")
    if not validate_exact_url(url): raise ValueError("DBsense 원본 링크를 수정 없이 입력하세요. 앞뒤 공백도 허용하지 않습니다.")
    if not files or not files[0].filename: raise ValueError("DBsense/광고주 사용 허가 이미지를 올려주세요.")
    defaults=auto_copy(product,brand)
    title=safe_text(form.get("title"),70) or defaults["title"]
    hook=safe_text(form.get("hook"),60) or defaults["hook"]
    p1=safe_text(form.get("point1"),60) or defaults["point1"]
    p2=safe_text(form.get("point2"),60) or defaults["point2"]
    cta=safe_text(form.get("cta"),60) or defaults["cta"]
    duration=int(form.get("duration","15")); duration=15 if duration not in (15,30) else duration
    job=uuid.uuid4().hex[:10]; jobdir=os.path.join(UP,job); os.makedirs(jobdir)
    try:
        img_paths=[]
        for i,f in enumerate(files[:4]):
            p=os.path.join(jobdir,f"src{i}.jpg")
            Image.open(f.stream).convert("RGB").save(p,quality=94); img_paths.append(p)
        if duration==15:
            captions=[hook,p1,p2,cta]
        else:
            captions=[hook,p1,"핵심 내용을 확인",p2,"비교 후 선택",cta]
        sec=duration/len(captions); clips=[]
        logging.info("VIDEO_START_V9 job=%s images=%s duration=%s",job,len(img_paths),duration)
        for i,caption in enumerate(captions):
            src=img_paths[i % len(img_paths)]
            base=os.path.join(jobdir,f"scene{i}.jpg"); overlay=os.path.join(jobdir,f"overlay{i}.png")
            clip=os.path.join(jobdir,f"clip{i}.mp4")
            make_scene(src,base,i,len(captions))
            make_caption_overlay(caption,overlay,"hook" if i==0 else "normal")
            make_motion_clip(base,overlay,clip,sec,i%5); clips.append(clip)
        out=f"danggeun_v9_{job}_{duration}s.mp4"; outp=os.path.join(OUT,out)
        concat_clips(clips,outp,duration)
        elapsed=round(time.time()-t0,1)
        logging.info("VIDEO_DONE_V9 job=%s sec=%s file=%s",job,elapsed,out)
        return {"filename":out,"seconds":elapsed,"duration":duration,"title":title,"hook":hook,
                "point1":p1,"point2":p2,"cta":cta,"url":url,"product":product,"image_count":len(img_paths)}
    finally:
        shutil.rmtree(jobdir,ignore_errors=True)

@app.after_request
def no_cache(resp):
    resp.headers["Cache-Control"]="no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"]="no-cache"; resp.headers["Expires"]="0"; return resp

@app.route("/")
def home(): return render_template("index.html")

@app.route("/healthz")
def health(): return "ok",200

@app.route("/video-form",methods=["POST"])
def video_form():
    try:
        result=create_video(request.form,request.files.getlist("images"))
        return render_template("result.html",**result)
    except Exception as e:
        logging.exception("VIDEO_FORM_ERROR_V9")
        return render_template("result.html",error=str(e)),500

@app.route("/download/<path:name>")
def download(name): return send_from_directory(OUT,name,as_attachment=True,download_name=name,conditional=False)

@app.route("/output/<path:name>")
def output(name): return send_from_directory(OUT,name,as_attachment=False,conditional=False)

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","10000")))
