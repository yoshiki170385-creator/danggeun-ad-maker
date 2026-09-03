from flask import Flask, render_template, request, send_from_directory
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
import os, uuid, shutil, re, zipfile

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024
OUT="output"; UP="uploads"
os.makedirs(OUT, exist_ok=True); os.makedirs(UP, exist_ok=True)
W=1080; H=1080

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

def short_product(p,n=18):
    p=safe_text(p,50)
    return p if len(p)<=n else p[:n-1]+"…"

def make_titles(product):
    p=short_product(product)
    return [f"{p} 조건 확인하기"[:30],
            f"{p} 알아보고 있다면?"[:30],
            f"{p} 비교 전 확인하세요"[:30],
            f"{p} 주요 정보 알아보기"[:30],
            f"{p} 자세히 확인하기"[:30]]

def fit_inside(im,max_w,max_h):
    im=im.convert("RGB")
    s=min(max_w/im.width,max_h/im.height)
    return im.resize((max(1,int(im.width*s)),max(1,int(im.height*s))),Image.Resampling.LANCZOS)

def cover(im,w,h):
    im=im.convert("RGB")
    s=max(w/im.width,h/im.height)
    r=im.resize((int(im.width*s),int(im.height*s)),Image.Resampling.LANCZOS)
    return r.crop(((r.width-w)//2,(r.height-h)//2,(r.width-w)//2+w,(r.height-h)//2+h))

def measure(d,t,f):
    b=d.textbbox((0,0),t,font=f)
    return b[2]-b[0]

def wrap_text(d,text,max_w,max_lines=2):
    words=safe_text(text,90).split()
    for size in [58,54,50,46,42]:
        f=get_font(size); lines=[]; cur=""
        for word in words:
            test=word if not cur else cur+" "+word
            if measure(d,test,f)<=max_w: cur=test
            else:
                if cur: lines.append(cur)
                cur=word
        if cur: lines.append(cur)
        if len(lines)<=max_lines: return lines,f
    return lines[:max_lines],get_font(42)

def add_badge(d):
    d.rounded_rectangle((32,28,230,90),radius=26,fill=(0,0,0,155))
    d.text((55,43),"광고 · 제휴",font=get_font(28),fill="white")

def base_canvas(src):
    bg=cover(src,W,H).filter(ImageFilter.GaussianBlur(28))
    bg=ImageEnhance.Brightness(bg).enhance(.78).convert("RGBA")
    fg=fit_inside(src,W-90,H-150)
    fx=(W-fg.width)//2; fy=(H-fg.height)//2
    bg.alpha_composite(fg.convert("RGBA"),(fx,fy))
    return bg

def panel(canvas,headline,sub=None,layout="bottom",strong=False):
    d=ImageDraw.Draw(canvas,"RGBA"); add_badge(d)
    if layout=="top": top,bottom=120,400
    elif layout=="center": top,bottom=360,720
    else: top,bottom=720,1010
    d.rounded_rectangle((54,top,W-54,bottom),radius=38,fill=(0,0,0,185 if strong else 155))
    lines,f=wrap_text(d,headline,W-150,2)
    lh=int(getattr(f,"size",48)*1.35)
    y=top+34 if sub else top+max(35,(bottom-top-lh*len(lines))//2)
    for line in lines:
        tw=measure(d,line,f); d.text(((W-tw)//2,y),line,font=f,fill="white"); y+=lh
    if sub:
        sf=get_font(32); sy=bottom-105
        for line in wrap_text(d,sub,W-160,2)[0]:
            tw=measure(d,line,sf); d.text(((W-tw)//2,sy),line,font=sf,fill=(245,245,245,255)); sy+=42

def make_variant(src,product,variant):
    p=short_product(product,20)
    canvas=base_canvas(src)
    if variant==0:
        d=ImageDraw.Draw(canvas,"RGBA"); add_badge(d)
        d.rounded_rectangle((60,H-180,W-60,H-65),radius=30,fill=(0,0,0,140))
        text="조건과 상세 내용을 확인해보세요"; f=get_font(40)
        d.text(((W-measure(d,text,f))//2,H-145),text,font=f,fill="white")
    elif variant==1:
        panel(canvas,f"{p}, 알아보고 있다면?","선택 전 주요 내용을 확인해보세요","top",True)
    elif variant==2:
        panel(canvas,"조건부터 확인해보세요",f"{p} 관련 내용을 비교해보세요","bottom")
    elif variant==3:
        panel(canvas,"비교하고 결정하세요",f"{p} 주요 정보 한눈에 확인","center")
    else:
        panel(canvas,"자세한 내용 확인하기",f"{p} 관련 정보는 바로가기에서","bottom",True)
    return canvas.convert("RGB")

@app.after_request
def no_cache(resp):
    resp.headers["Cache-Control"]="no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"]="no-cache"; resp.headers["Expires"]="0"
    return resp

@app.route("/")
def home(): return render_template("index.html")

@app.route("/healthz")
def health(): return "ok",200

@app.route("/make-images",methods=["POST"])
def make_images():
    product=safe_text(request.form.get("product"),50)
    brand=safe_text(request.form.get("brand"),30)
    url=request.form.get("url","")
    f=request.files.get("image")
    if not product: return render_template("result.html",error="상품명을 입력하세요."),400
    if not validate_exact_url(url): return render_template("result.html",error="DBsense 원본 링크를 수정 없이 입력하세요. 앞뒤 공백도 허용하지 않습니다."),400
    if not f or not f.filename: return render_template("result.html",error="DBsense/광고주 사용 허가 이미지를 올려주세요."),400

    job=uuid.uuid4().hex[:10]
    jobdir=os.path.join(UP,job); os.makedirs(jobdir)
    try:
        src=Image.open(f.stream).convert("RGB")
        titles=make_titles(product)
        labels=["A 원본보존형","B 후킹형","C 조건확인형","D 비교형","E CTA형"]
        outputs=[]
        for i,title in enumerate(titles):
            name=f"danggeun_ad_{job}_{chr(65+i)}.jpg"
            make_variant(src,product,i).save(os.path.join(OUT,name),quality=94,optimize=True)
            outputs.append({"label":labels[i],"title":title,"filename":name})
        zip_name=f"danggeun_ad_set_{job}.zip"
        with zipfile.ZipFile(os.path.join(OUT,zip_name),"w",zipfile.ZIP_DEFLATED) as z:
            for item in outputs:
                z.write(os.path.join(OUT,item["filename"]),item["filename"])
            info="추천 1순위 제목: "+titles[0]+"\nDBsense 원본 홍보링크: "+url+"\n\n"
            info+="\n".join([labels[i]+": "+titles[i] for i in range(5)])
            info+="\n\n[광고/제휴 링크]\n"+url
            z.writestr("광고제목_및_링크.txt",info)
        return render_template("result.html",product=product,brand=brand,url=url,outputs=outputs,recommended=titles[0],zip_name=zip_name)
    finally:
        shutil.rmtree(jobdir,ignore_errors=True)

@app.route("/output/<path:name>")
def output(name): return send_from_directory(OUT,name,as_attachment=False,conditional=False)

@app.route("/download/<path:name>")
def download(name): return send_from_directory(OUT,name,as_attachment=True,download_name=name,conditional=False)

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","10000")))
