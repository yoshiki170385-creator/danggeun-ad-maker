from flask import Flask, render_template, request, send_from_directory
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageStat
import os, uuid, subprocess, logging, time, shutil, re

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024
OUT="output"; UP="uploads"
os.makedirs(OUT, exist_ok=True); os.makedirs(UP, exist_ok=True)
logging.basicConfig(level=logging.INFO)
W,H,FPS=720,1280,15

def font_path():
    for p in [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        if os.path.exists(p): return p
    return None
FONT_PATH=font_path()
def get_font(size): return ImageFont.truetype(FONT_PATH,size) if FONT_PATH else ImageFont.load_default()
def safe_text(s,n=100): return (s or "").replace("\n"," ").strip()[:n]
def validate_exact_url(u):
    if not u or u != u.strip(): return False
    return bool(re.match(r"^https?://\S+$",u))

def auto_copy(product, brand=""):
    p=safe_text(product,50)
    return {
        "title": f"{p} 자세히 알아보기",
        "hook": f"{p}, 이런 내용이 궁금했다면?",
        "point1": "핵심 내용을 한눈에 확인해보세요",
        "point2": "조건과 상세 내용을 확인해보세요",
        "cta": "자세한 내용 확인하기"
    }

def fit_inside(im,max_w,max_h):
    scale=min(max_w/im.width,max_h/im.height)
    return im.resize((max(1,int(im.width*scale)),max(1,int(im.height*scale))),Image.Resampling.LANCZOS)

def cover_crop(im,w,h,focus_y=.5):
    scale=max(w/im.width,h/im.height)
    r=im.resize((int(im.width*scale),int(im.height*scale)),Image.Resampling.LANCZOS)
    x=max(0,(r.width-w)//2)
    max_y=max(0,r.height-h)
    y=int(max_y*min(1,max(0,focus_y)))
    return r.crop((x,y,x+w,y+h))

def make_scene_base(src_path,out_path,scene,text_heavy=True):
    src=Image.open(src_path).convert("RGB")
    bg=cover_crop(src,W,H,.5).filter(ImageFilter.GaussianBlur(30)).convert("RGBA")
    bg=Image.alpha_composite(bg,Image.new("RGBA",(W,H),(0,0,0,42)))

    # V10: 글 많은 DBsense 이미지는 원본 전체를 우선 보존.
    # 장면마다 '카메라가 이미지의 다른 부분을 바라보는 느낌'만 주되 과도한 크롭은 피한다.
    if text_heavy:
        margins=[(58,210),(42,185),(48,190),(66,225)]
        yoffs=[-12,-58,30,-5]
    else:
        margins=[(42,170),(20,130),(20,130),(50,190)]
        yoffs=[0,-80,70,0]
    mw,mh=margins[scene%4]
    fg=fit_inside(src,W-mw,H-mh)
    fx=(W-fg.width)//2
    fy=(H-fg.height)//2+yoffs[scene%4]
    fy=max(86,min(H-fg.height-86,fy))
    shadow=Image.new("RGBA",(fg.width+30,fg.height+30),(0,0,0,0))
    sd=ImageDraw.Draw(shadow); sd.rounded_rectangle((15,15,fg.width+15,fg.height+15),radius=20,fill=(0,0,0,78))
    shadow=shadow.filter(ImageFilter.GaussianBlur(12)); bg.alpha_composite(shadow,(fx-15,fy-15))
    bg.alpha_composite(fg.convert("RGBA"),(fx,fy))
    bg.convert("RGB").save(out_path,quality=92)

def measure(d,t,f):
    b=d.textbbox((0,0),t,font=f); return b[2]-b[0]
def wrap_words(d,text,maxw,max_lines=2):
    words=safe_text(text,100).split()
    for size in [38,35,32,29]:
        f=get_font(size); lines=[]; cur=""
        for word in words:
            t=word if not cur else cur+" "+word
            if measure(d,t,f)<=maxw: cur=t
            else:
                if cur: lines.append(cur)
                cur=word
        if cur: lines.append(cur)
        if len(lines)<=max_lines: return lines,f
    return lines[:max_lines],get_font(29)

def make_overlay(text,out_path,scene,text_heavy=True):
    ov=Image.new("RGBA",(W,H),(0,0,0,0)); d=ImageDraw.Draw(ov,"RGBA")
    # 고정 광고표시
    d.rounded_rectangle((20,20,178,68),radius=20,fill=(0,0,0,150))
    d.text((36,29),"광고 · 제휴",font=get_font(20),fill="white")
    # 글 많은 원본은 자막을 작고 짧게. 첫 장면/마지막 장면 중심.
    if text:
        if text_heavy:
            top,bottom=(H-245,H-92) if scene else (H-245,H-92)
        else:
            top,bottom=(H-300,H-92)
        d.rounded_rectangle((46,top,W-46,bottom),radius=28,fill=(0,0,0,150))
        lines,f=wrap_words(d,text,W-130,2)
        lh=int(getattr(f,"size",32)*1.3); total=lh*len(lines); y=top+(bottom-top-total)//2
        for line in lines:
            tw=measure(d,line,f); d.text(((W-tw)//2,y),line,font=f,fill="white"); y+=lh
    ov.save(out_path)

def make_motion_clip(base,overlay,out,seconds,style):
    frames=max(1,int(seconds*FPS))
    # V10: 움직임 폭을 줄여 '사진이 출렁이는 느낌' 제거
    if style==0:
        z="min(zoom+0.00035,1.035)"; x="iw/2-(iw/zoom/2)"; y="ih/2-(ih/zoom/2)"
    elif style==1:
        z="min(zoom+0.00025,1.025)"; x=f"(iw-iw/zoom)*on/{max(1,frames-1)}"; y="ih/2-(ih/zoom/2)"
    elif style==2:
        z="min(zoom+0.00025,1.025)"; x=f"(iw-iw/zoom)*(1-on/{max(1,frames-1)})"; y="ih/2-(ih/zoom/2)"
    else:
        z="if(eq(on,0),1.035,max(zoom-0.00030,1.00))"; x="iw/2-(iw/zoom/2)"; y="ih/2-(ih/zoom/2)"
    filt=(f"[0:v]scale={W}:{H},zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={FPS}[m];"
          f"[1:v]scale={W}:{H}[o];[m][o]overlay=0:0:format=auto,format=yuv420p")
    cmd=["ffmpeg","-y","-loop","1","-i",base,"-loop","1","-i",overlay,"-filter_complex",filt,
         "-t",f"{seconds:.3f}","-r",str(FPS),"-c:v","libx264","-preset","ultrafast","-crf","26","-pix_fmt","yuv420p",out]
    p=subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=180,text=True)
    if p.returncode!=0:
        logging.error("FFMPEG_CLIP_ERROR %s",(p.stderr or "")[-2500:]); raise RuntimeError("영상 장면 생성에 실패했습니다.")

def concat_clips(clips,out_path,duration):
    lp=os.path.join(os.path.dirname(clips[0]),"clips.txt")
    with open(lp,"w",encoding="utf-8") as f:
        for p in clips: f.write(f"file '{os.path.basename(p)}'\n")
    cmd=["ffmpeg","-y","-f","concat","-safe","0","-i",lp,"-vf",f"fps={FPS},format=yuv420p",
         "-c:v","libx264","-preset","ultrafast","-crf","26","-t",str(duration),"-movflags","+faststart",out_path]
    p=subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=180,text=True)
    if p.returncode!=0:
        logging.error("FFMPEG_CONCAT_ERROR %s",(p.stderr or "")[-2500:]); raise RuntimeError("영상 합치기에 실패했습니다.")

def create_video(form,files):
    t0=time.time(); product=safe_text(form.get("product"),50); brand=safe_text(form.get("brand"),30); url=form.get("url","")
    if not product: raise ValueError("상품명을 입력하세요.")
    if not validate_exact_url(url): raise ValueError("DBsense 원본 링크를 수정 없이 입력하세요. 앞뒤 공백도 허용하지 않습니다.")
    if not files or not files[0].filename: raise ValueError("DBsense/광고주 사용 허가 이미지를 올려주세요.")
    defaults=auto_copy(product,brand)
    title=safe_text(form.get("title"),70) or defaults["title"]
    hook=safe_text(form.get("hook"),65) or defaults["hook"]
    p1=safe_text(form.get("point1"),65) or defaults["point1"]
    p2=safe_text(form.get("point2"),65) or defaults["point2"]
    cta=safe_text(form.get("cta"),65) or defaults["cta"]
    duration=int(form.get("duration","15")); duration=15 if duration not in (15,30) else duration
    text_heavy=form.get("image_mode","text") == "text"
    job=uuid.uuid4().hex[:10]; jobdir=os.path.join(UP,job); os.makedirs(jobdir)
    try:
        img_paths=[]
        for i,f in enumerate(files[:4]):
            p=os.path.join(jobdir,f"src{i}.jpg"); Image.open(f.stream).convert("RGB").save(p,quality=94); img_paths.append(p)
        # 15초: 4장면. 글 많은 이미지에서는 중간 자막을 더 짧게 유지.
        captions=[hook,p1,p2,cta] if duration==15 else [hook,p1,"",p2,"",cta]
        sec=duration/len(captions); clips=[]
        logging.info("VIDEO_START_V10 job=%s images=%s duration=%s mode=%s",job,len(img_paths),duration,"text" if text_heavy else "photo")
        for i,caption in enumerate(captions):
            src=img_paths[i%len(img_paths)]; base=os.path.join(jobdir,f"base{i}.jpg"); overlay=os.path.join(jobdir,f"overlay{i}.png"); clip=os.path.join(jobdir,f"clip{i}.mp4")
            make_scene_base(src,base,i%4,text_heavy); make_overlay(caption,overlay,i,text_heavy); make_motion_clip(base,overlay,clip,sec,i%4); clips.append(clip)
        out=f"danggeun_v10_{job}_{duration}s.mp4"; outp=os.path.join(OUT,out); concat_clips(clips,outp,duration)
        elapsed=round(time.time()-t0,1); logging.info("VIDEO_DONE_V10 job=%s sec=%s file=%s",job,elapsed,out)
        return dict(filename=out,seconds=elapsed,duration=duration,title=title,hook=hook,point1=p1,point2=p2,cta=cta,url=url,product=product,image_count=len(img_paths),mode="원본 문구 보존" if text_heavy else "상품사진")
    finally: shutil.rmtree(jobdir,ignore_errors=True)

@app.after_request
def no_cache(resp):
    resp.headers["Cache-Control"]="no-store, no-cache, must-revalidate, max-age=0"; resp.headers["Pragma"]="no-cache"; resp.headers["Expires"]="0"; return resp
@app.route("/")
def home(): return render_template("index.html")
@app.route("/healthz")
def health(): return "ok",200
@app.route("/video-form",methods=["POST"])
def video_form():
    try: return render_template("result.html",**create_video(request.form,request.files.getlist("images")))
    except Exception as e:
        logging.exception("VIDEO_FORM_ERROR_V10"); return render_template("result.html",error=str(e)),500
@app.route("/download/<path:name>")
def download(name): return send_from_directory(OUT,name,as_attachment=True,download_name=name,conditional=False)
@app.route("/output/<path:name>")
def output(name): return send_from_directory(OUT,name,as_attachment=False,conditional=False)
if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.getenv("PORT","10000")))
