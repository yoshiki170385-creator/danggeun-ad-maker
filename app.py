
from flask import Flask, render_template, request, jsonify, send_file
from pathlib import Path
from PIL import Image, ImageOps, ImageDraw, ImageFont
import tempfile, subprocess, uuid, os, shutil
import imageio_ffmpeg

app=Flask(__name__)
app.config["MAX_CONTENT_LENGTH"]=60*1024*1024
BASE=Path(__file__).resolve().parent
OUT=BASE/"output"; OUT.mkdir(exist_ok=True)

def font(sz):
    for p in [r"C:\Windows\Fonts\malgunbd.ttf","/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc","/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        if os.path.exists(p): return ImageFont.truetype(p,sz)
    return ImageFont.load_default()

def valid_url(u):
    if not u:return False,"디비센스 원본 링크를 입력해주세요."
    if u!=u.strip():return False,"링크 앞뒤 공백을 제거하지 말고 원본 링크를 다시 붙여넣어 주세요."
    if not (u.startswith("http://") or u.startswith("https://")):return False,"http:// 또는 https:// 링크만 사용할 수 있습니다."
    return True,""

def titles(p):
    xs=[f"{p} 조건 확인하기",f"{p} 알아보고 있다면?",f"{p} 비교 전 확인하세요",f"{p} 주요 조건 알아보기",f"{p} 혜택 확인하기",f"{p} 고민이라면 확인",f"{p} 주요 정보 한눈에",f"{p} 알아볼 때 체크할 점",f"{p} 신청 전 조건 확인",f"{p} 자세히 알아보기"]
    return [x[:30] for x in xs]

def scene(src,text,i,out):
    W,H=1080,1920
    img=Image.open(src).convert("RGB")
    iw,ih=img.size
    ratios=[1.00,1.08,1.16,1.10,1.20]
    z=ratios[i%len(ratios)]
    tw,th=iw/z,(iw/z)/(W/H)
    if th>ih/z: th=ih/z; tw=th*(W/H)
    shifts=[(0,0),(-.08,0),(.08,0),(0,-.05),(0,.05)]
    sx,sy=shifts[i%len(shifts)]
    cx,cy=iw/2+iw*sx,ih/2+ih*sy
    left=max(0,min(iw-tw,cx-tw/2)); top=max(0,min(ih-th,cy-th/2))
    img=img.crop((left,top,left+tw,top+th))
    img=ImageOps.fit(img,(W,H),method=Image.Resampling.LANCZOS).convert("RGBA")
    ov=Image.new("RGBA",(W,H),(0,0,0,0)); d=ImageDraw.Draw(ov)
    d.rectangle((0,0,W,H),fill=(0,0,0,55)); d.rectangle((0,1180,W,H),fill=(0,0,0,105))
    img=Image.alpha_composite(img,ov).convert("RGB"); d=ImageDraw.Draw(img)
    f=font(72); badge=font(32); small=font(34)
    d.rounded_rectangle((45,50,300,118),radius=24,fill=(255,126,54))
    d.text((70,68),"광고/제휴",font=badge,fill="white")
    lines=text.split("\n"); y=1300
    for line in lines:
        b=d.textbbox((0,0),line,font=f); w=b[2]-b[0]
        d.text(((W-w)//2,y),line,font=f,fill="white",stroke_width=3,stroke_fill="black"); y+=95
    footer="자세한 내용은 바로가기에서 확인"
    b=d.textbbox((0,0),footer,font=small); d.text(((W-(b[2]-b[0]))//2,1740),footer,font=small,fill="white",stroke_width=2,stroke_fill="black")
    img.save(out,quality=95)

@app.route("/")
def home(): return render_template("index.html")

@app.route("/api/generate",methods=["POST"])
def gen():
    d=request.get_json(force=True); p=(d.get("product") or "").strip(); b=(d.get("brand") or "").strip(); u=d.get("url") or ""; c=d.get("cta") or "바로가기"
    if not p:return jsonify(ok=False,error="상품명을 입력해주세요."),400
    ok,msg=valid_url(u)
    if not ok:return jsonify(ok=False,error=msg),400
    ts=titles(p)
    return jsonify(ok=True,titles=ts,recommended=ts[0],registration=[
        ["업체 이름",b or p.split()[0],"실제 브랜드/광고주 이름"],
        ["웹사이트 주소",u,"디비센스 원본 링크 그대로"],
        ["광고 사진/영상","V5에서 만든 이미지형 광고영상","허용된 소재만 사용"],
        ["광고 제목",ts[0],"30자 이내"],
        ["행동 유도 버튼",c,"기본 추천: 바로가기"],
        ["가격","검증된 경우에만 입력","미확인 시 비우기"],
        ["심의필 번호","필요하고 확인된 경우에만 입력","임의 생성 금지"]
    ])

@app.route("/api/video",methods=["POST"])
def video():
    p=(request.form.get("product") or "").strip(); title=(request.form.get("title") or "").strip(); sec=int(request.form.get("seconds") or 15)
    if sec not in (15,30):sec=15
    if not p:return jsonify(ok=False,error="상품명이 없습니다."),400
    fs=[f for f in request.files.getlist("images") if f.filename]
    if not fs:return jsonify(ok=False,error="디비센스 이미지를 1장 이상 넣어주세요."),400
    tmp=Path(tempfile.mkdtemp(prefix="dg_v5_"))
    try:
        imgs=[]
        for i,f in enumerate(fs[:8]):
            ext=Path(f.filename).suffix.lower()
            if ext not in [".jpg",".jpeg",".png",".webp"]:continue
            x=tmp/f"img{i}{ext}"; f.save(x); imgs.append(x)
        if not imgs:return jsonify(ok=False,error="지원되는 이미지가 없습니다."),400
        bgm=None; bf=request.files.get("bgm")
        if bf and bf.filename and Path(bf.filename).suffix.lower() in [".mp3",".wav",".m4a",".aac"]:
            bgm=tmp/("bgm"+Path(bf.filename).suffix.lower()); bf.save(bgm)
        if sec==15:
            texts=[title or f"{p} 알아보는 중이라면?","신청 전\n주요 조건부터 확인","상품 정보와\n세부 내용 체크","나에게 맞는지\n비교해보세요","자세한 내용\n바로가기"]; ds=[3]*5
        else:
            texts=[title or f"{p} 알아보는 중이라면?","신청 전\n주요 조건부터 확인","혜택과 조건은\n꼼꼼하게 체크","상품 정보와\n세부 내용 확인","나에게 맞는지\n비교해보세요","자세한 내용\n바로가기"]; ds=[5]*6
        scenes=[]
        for i,t in enumerate(texts):
            o=tmp/f"s{i}.jpg"; scene(imgs[i%len(imgs)],t,i,o); scenes.append(o)
        concat=tmp/"list.txt"; lines=[]
        for s,dur in zip(scenes,ds):lines += [f"file '{s.as_posix()}'",f"duration {dur}"]
        lines.append(f"file '{scenes[-1].as_posix()}'"); concat.write_text("\n".join(lines),encoding="utf-8")
        ff=imageio_ffmpeg.get_ffmpeg_exe(); base=tmp/"base.mp4"
        subprocess.run([ff,"-y","-f","concat","-safe","0","-i",str(concat),"-vf","scale=1080:1920,format=yuv420p","-r","30","-c:v","libx264","-pix_fmt","yuv420p","-movflags","+faststart",str(base)],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        out=OUT/f"danggeun_ad_{uuid.uuid4().hex[:8]}_{sec}s.mp4"
        if bgm:
            subprocess.run([ff,"-y","-i",str(base),"-stream_loop","-1","-i",str(bgm),"-filter_complex","[1:a]volume=0.13[a]","-map","0:v","-map","[a]","-shortest","-c:v","copy","-c:a","aac","-b:a","128k","-movflags","+faststart",str(out)],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        else: shutil.copy2(base,out)
        return jsonify(ok=True,download=f"/download/{out.name}",note="이미지 1장이어도 장면별 확대·크롭 위치를 바꿔 구성했습니다.")
    except Exception:
        return jsonify(ok=False,error="영상 생성에 실패했습니다."),500
    finally: shutil.rmtree(tmp,ignore_errors=True)

@app.route("/download/<name>")
def dl(name):
    p=OUT/Path(name).name
    return send_file(p,as_attachment=True) if p.exists() else ("Not found",404)

@app.route("/manifest.webmanifest")
def manifest(): return send_file(BASE/"static"/"manifest.webmanifest",mimetype="application/manifest+json")
@app.route("/sw.js")
def sw(): return send_file(BASE/"static"/"sw.js",mimetype="application/javascript")
@app.route("/healthz")
def health(): return "ok",200
if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)))
