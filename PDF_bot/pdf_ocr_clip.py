"""
PDF -> OCR-> 이미지/캡션 -> CLIP 매칭
HTML 뷰어 + 모드 버튼(이미지/번역/요약) + 문서기반 챗봇(GPT-4-mini)
"""

from __future__ import annotations
from pathlib import Path
import json, re, threading, socket, time, sys, platform, shutil, os
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import urllib.request, urllib.error

import fitz 
from PIL import Image, ImageDraw, ImageFont
import numpy as np

# ===================== 1) 설정 =====================
PDF_PATH = Path("./your file.pdf")
OUT_ROOT = Path("./result")
DOC_NAME = PDF_PATH.stem
OUT_DIR  = OUT_ROOT / DOC_NAME

PAGES_DIR  = OUT_DIR / "pages"
IMAGES_DIR = OUT_DIR / "images"
MANIFEST   = OUT_DIR / "manifest.json"
INDEX_JSON = OUT_DIR / "index.json"
HTML_OUT   = OUT_DIR / "viewer_clip.html"

for d in [OUT_DIR, PAGES_DIR, IMAGES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# 매칭/렌더
SIM_THRESHOLD = 0.20
TOP_K = 1
MIN_WORD_LEN = 2
PNG_SCALE = 2.0

# OCR
USE_OCR_ALWAYS = True
OCR_MIN_WORDS_FOR_SKIP = 5
OCR_LANG = "ko"
OCR_ENGINE_ORDER = ["tesseract", "easyocr"]

# 캡션 탐지/합성
CAPTION_SEARCH_HEIGHT   = 10
CAPTION_SIDE_OVERLAP    = 0.10
CAPTION_MAX_WIDTH_RATIO = 0.90
CAPTION_CENTER_TOL      = 40
CAPTION_MIN_TOKENS      = 1
CAPTION_MAX_VERTICAL_GAP = 10
CAPTION_HEAD_RE = re.compile(r'^(fig(?:\.|ure)?|그림|표|table|도)\s*[\.:]?\s*\d+', re.IGNORECASE)

# *_cap.png 로 저장
SAVE_WITH_SUFFIX = True

# 캡션 스타일
CAPTION_WRAP_PX_RATIO = 0.92
CAPTION_PADDING       = 12
CAPTION_LINE_SPACING  = 6
CAPTION_BG            = (255, 255, 255)
CAPTION_FG            = (0, 0, 0)
CAPTION_BORDER        = (220, 220, 220)
CAPTION_BORDER_W      = 1

# 임베딩 융합 (이미지+캡션)
FUSE_WITH_CAPTION = True
FUSE_IMG_WEIGHT   = 0.30

# CLIP 모델
CLIP_MODEL_NAME = "openai/clip-vit-base-patch32"

# ===================== 2) 유틸 =====================
def _to_jsonable(obj):
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8")
        except Exception:
            import base64 as _b64
            return "base64:" + _b64.b64encode(obj).decode("ascii")
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_to_jsonable(v) for v in obj]
    if hasattr(obj, "__dict__") and obj.__class__.__name__ == "Rect":
        return [float(obj.x0), float(obj.y0), float(obj.x1), float(obj.y1)]
    return obj

def save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_to_jsonable(obj), f, ensure_ascii=False, indent=2)

def _overlap_ratio(ax0, ax1, bx0, bx1):
    inter = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    aw = max(1e-6, ax1 - ax0)
    bw = max(1e-6, bx1 - bx0)
    return inter / min(aw, bw)

def _px2pt(bbox_px, scale: float):
    x0,y0,x1,y1 = bbox_px
    return [x0/scale, y0/scale, x1/scale, y1/scale]

# ===================== 3) OCR (Tesseract 우선, EasyOCR 폴백) =====================
_EASY = None
def _easy_reader():
    global _EASY
    if _EASY is None:
        import easyocr
        langs = ["ko","en"] if OCR_LANG.startswith(("ko","kor")) else ["en"]
        model_dir = (OUT_DIR / ".easyocr").resolve()
        model_dir.mkdir(parents=True, exist_ok=True)
        use_gpu = False
        try:
            import torch
            use_gpu = torch.cuda.is_available()
        except Exception:
            pass
        _EASY = easyocr.Reader(
            langs, gpu=use_gpu,
            verbose=False, model_storage_directory=str(model_dir),
            download_enabled=True
        )
        try:
            tmp = OUT_DIR / "_warmup.png"
            if not tmp.exists():
                Image.new("RGB", (32, 16), (255,255,255)).save(tmp)
            _EASY.readtext(str(tmp))
        except Exception:
            pass
    return _EASY

def _ellipsize_single_line(draw, text, font, max_px):
    if not text:
        return ""
    if draw.textbbox((0,0), text, font=font)[2] <= max_px:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        trial = text[:mid] + ell
        w = draw.textbbox((0,0), trial, font=font)[2]
        if w <= max_px:
            best = trial
            lo = mid + 1
        else:
            hi = mid - 1
    return best or ell

def run_ocr_on_png(png_path: Path):
    if "tesseract" in OCR_ENGINE_ORDER:
        try:
            import pytesseract
            img = Image.open(png_path).convert("RGB")
            data = pytesseract.image_to_data(img, lang="kor+eng", output_type=pytesseract.Output.DICT)
            n = len(data["text"])
            words = []
            for i in range(n):
                txt = (data["text"][i] or "").strip()
                if not txt:
                    continue
                x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
                conf = float(data.get("conf", [0]*n)[i] if data.get("conf") else 0)
                words.append({"text": txt, "bbox_px": [float(x), float(y), float(x+w), float(y+h)], "conf": conf})
            if words:
                return words
        except Exception as e2:
            print("[OCR] Tesseract 우선 실패:", e2)

    if "easyocr" in OCR_ENGINE_ORDER:
        try:
            reader = _easy_reader()
            res = reader.readtext(str(png_path), detail=1)
            words = []
            for bbox, txt, conf in res:
                if not txt or not txt.strip():
                    continue
                xs = [p[0] for p in bbox]; ys = [p[1] for p in bbox]
                x0, y0, x1, y1 = float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))
                tokens = [t for t in txt.strip().split() if t]
                if len(tokens) <= 1:
                    words.append({"text": txt.strip(), "bbox_px": [x0,y0,x1,y1], "conf": float(conf)})
                else:
                    total = sum(len(t) for t in tokens)
                    cur = x0
                    for t in tokens:
                        w_ratio = len(t) / total if total > 0 else 0
                        seg_w = (x1 - x0) * w_ratio
                        seg_x1 = cur + seg_w
                        words.append({"text": t, "bbox_px": [cur, y0, seg_x1, y1], "conf": float(conf)})
                        cur = seg_x1
            return words
        except Exception as e:
            print("[OCR] EasyOCR 실패:", e)

    return []

# 경량 숫자 OCR (축 틱 검출용)
def _ocr_words_from_pil(img: Image.Image):
    words = []
    try:
        import pytesseract
        data = pytesseract.image_to_data(
            img, lang="eng", output_type=pytesseract.Output.DICT,
            config="--psm 6 -c tessedit_char_whitelist=0123456789.,-+"
        )
        n = len(data["text"])
        for i in range(n):
            txt = (data["text"][i] or "").strip()
            if not txt:
                continue
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            words.append({"text": txt, "bbox_px": [float(x), float(y), float(x+w), float(y+h)]})
        if words:
            return words
    except Exception:
        pass
    try:
        reader = _easy_reader()
        res = reader.readtext(np.array(img), detail=1)
        for bbox, txt, _conf in res:
            if not txt or not txt.strip():
                continue
            xs = [p[0] for p in bbox]; ys = [p[1] for p in bbox]
            x0, y0, x1, y1 = float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))
            words.append({"text": txt.strip(), "bbox_px": [x0,y0,x1,y1]})
    except Exception:
        pass
    return words

# ===================== 4) PDF 파싱 (OCR 통합) =====================
def parse_pdf(pdf_path: Path):
    doc = fitz.open(pdf_path)
    meta = {"page_count": len(doc), "pages": []}

    for pno, page in enumerate(doc):
        width, height = page.rect.width, page.rect.height

        words_pdf = [{"text": w[4], "bbox": [w[0], w[1], w[2], w[3]]}
                     for w in page.get_text("words")]
        plain_pdf = page.get_text("text")

        image_items = []
        page_xrefs = [info[0] for info in page.get_images(full=True)]
        seen = set()
        for xref in page_xrefs:
            rects = []
            try:
                rects = list(page.get_image_rects(xref))
            except Exception:
                rects = []
            if not rects:
                try:
                    r = page.get_image_bbox(xref)
                    if r is not None:
                        rects = [r]
                except Exception:
                    rects = []
            for r in rects:
                bb = [float(r.x0), float(r.y0), float(r.x1), float(r.y1)]
                key = (xref, round(bb[0],2), round(bb[1],2), round(bb[2],2), round(bb[3],2))
                if key in seen:
                    continue
                seen.add(key)
                image_items.append({"xref": xref, "bbox": bb})

        pix = page.get_pixmap(matrix=fitz.Matrix(PNG_SCALE, PNG_SCALE))
        png_path = PAGES_DIR / f"page_{pno+1:04d}.png"
        pix.save(str(png_path))

        need_ocr = USE_OCR_ALWAYS or (len(words_pdf) < OCR_MIN_WORDS_FOR_SKIP)
        words_final = words_pdf
        plain_final = plain_pdf

        if need_ocr:
            ocr_words = run_ocr_on_png(png_path)
            if ocr_words:
                tmp = []
                for ow in ocr_words:
                    pt = _px2pt(ow["bbox_px"], PNG_SCALE)
                    tmp.append({"text": ow["text"], "bbox": pt})
                words_final = sorted(tmp, key=lambda w: ((w["bbox"][1]+w["bbox"][3])/2.0, w["bbox"][0]))
                plain_final = " ".join([w["text"] for w in words_final])

        txt_path = PAGES_DIR / f"page_{pno+1:04d}.txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(plain_final or "")

        meta["pages"].append({
            "page": pno, "width": width, "height": height,
            "words": words_final, "images": image_items,
            "png": str(png_path), "txt": str(txt_path),
            "used_ocr": bool(need_ocr and len(words_final) > 0)
        })

    doc.close()
    return meta

# ===================== 5) 원본 이미지 추출 =====================
def export_images(pdf_path: Path):
    doc = fitz.open(pdf_path)
    xref_to_path = {}
    for _pno, page in enumerate(doc):
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            if xref in xref_to_path:
                continue
            pix = fitz.Pixmap(doc, xref)
            if pix.n >= 5:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            if pix.width <= 220 or pix.height <= 220:
                continue
            img_path = IMAGES_DIR / f"img_{xref}.png"
            with open(img_path, "wb") as f:
                f.write(pix.tobytes("png"))
            xref_to_path[xref] = str(img_path)
    doc.close()
    return xref_to_path

# ===================== 6) 캡션 탐지/합성 + 차트 판정 =====================
def _lines_with_bbox(words, y_tol=3.5):
    lines = []
    for w in sorted(words, key=lambda x: ((x["bbox"][1]+x["bbox"][3])/2.0, x["bbox"][0])):
        x0,y0,x1,y1 = w["bbox"]; cy = (y0+y1)/2.0
        placed = False
        for L in lines:
            if abs(L["_cy"] - cy) <= y_tol:
                L["items"].append(w)
                L["_cy"] = (L["_cy"]*L["_n"] + cy)/(L["_n"]+1); L["_n"] += 1
                placed = True; break
        if not placed:
            lines.append({"_cy": cy, "_n":1, "items":[w]})
    out=[]
    for L in sorted(lines, key=lambda z: z["_cy"]):
        its = sorted(L["items"], key=lambda it: it["bbox"][0])
        text = " ".join([(it["text"] or "").strip() for it in its if (it.get("text") or "").strip()])
        x0 = min(it["bbox"][0] for it in its); y0 = min(it["bbox"][1] for it in its)
        x1 = max(it["bbox"][2] for it in its); y1 = max(it["bbox"][3] for it in its)
        out.append({"text": text, "bbox":[x0,y0,x1,y1]})
    return out

def extract_caption_for_image(page_dict: dict, img_bbox):
    x0, y0, x1, y1 = img_bbox
    img_cx = (x0 + x1) / 2.0
    img_w  = max(1e-6, x1 - x0)

    below, above = [], []
    for w in page_dict.get("words", []):
        wb = w["bbox"]; t = (w.get("text") or "").strip()
        if not t: continue
        if y1 <= wb[1] <= y1 + CAPTION_SEARCH_HEIGHT: below.append(w)
        if y0 - CAPTION_SEARCH_HEIGHT <= wb[3] <= y0: above.append(w)

    def pick(lines, anchor, direction):
        if not lines: return ""
        cand=[]
        for ln in lines:
            lx0,ly0,lx1,ly1 = ln["bbox"]
            lw=max(1e-6, lx1-lx0); lcx=(lx0+lx1)/2.0
            overlap=_overlap_ratio(x0,x1,lx0,lx1)
            center_ok=abs(lcx-img_cx)<=CAPTION_CENTER_TOL
            width_ok=(lw/img_w) <= CAPTION_MAX_WIDTH_RATIO
            vgap = (ly0 - y1) if direction=='below' else (y0 - ly1)
            if vgap < 0: vgap = 0
            vertical_ok = vgap <= CAPTION_MAX_VERTICAL_GAP
            if (overlap>=CAPTION_SIDE_OVERLAP or center_ok or width_ok) and vertical_ok:
                cand.append(ln)
        if not cand: return ""
        for ln in cand:
            if CAPTION_HEAD_RE.match(ln["text"].strip()): return ln["text"].strip()
        cand.sort(key=lambda ln: abs(((ln["bbox"][1]+ln["bbox"][3])/2.0) - anchor))
        return cand[0]["text"].strip()

    cap = pick(_lines_with_bbox(below), y1, 'below') or pick(_lines_with_bbox(above), y0, 'above')
    return cap if len(cap.split()) >= CAPTION_MIN_TOKENS else ""

# ---- 차트 판정(축 눈금 기반) ----
CHART_KWS = {
    "accuracy","acc","epoch","iter","iteration","precision","recall","f1","auc","validation","val","legend","xlabel","ylabel","x-axis","y-axis",
}
NUM_RE = re.compile(r"^[+-]?\d+(?:,\d{3})*(?:\.\d+)?$")

def is_chart_caption(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    return any(kw in t for kw in CHART_KWS) or " vs. " in t or " vs " in t

def gather_neighbor_text(page_dict: dict, bbox, margin=35):
    x0,y0,x1,y1 = bbox
    mx0, my0, mx1, my1 = x0-margin, y0-margin, x1+margin, y1+margin
    words = []
    for w in page_dict.get("words", []):
        wx0,wy0,wx1,wy1 = w["bbox"]
        if (wx1 < mx0) or (wx0 > mx1) or (wy1 < my0) or (wy0 > my1):
            continue
        t = (w.get("text") or "").strip().lower()
        if t: words.append((t, (wx0,wy0,wx1,wy1)))
    return words

def looks_like_axis_ticks_from_page(bbox, words, horiz_band_ratio=0.15, vert_band_ratio=0.15,
                                    min_ticks=4, min_monotonic_ratio=0.7):
    x0,y0,x1,y1 = bbox
    w, h = max(1e-6, x1-x0), max(1e-6, y1-y0)
    bottom_band = (x0, y1 - h*horiz_band_ratio, x1, y1 + 1)
    left_band   = (x0 - 1, y0, x0 + w*vert_band_ratio, y1)

    def in_box(bb, band):
        bx0,by0,bx1,by1 = bb; X0,Y0,X1,Y1 = band
        return not (bx1 < X0 or bx0 > X1 or by1 < Y0 or by0 > Y1)

    def parse_num(t):
        try: return float(t.replace(",", ""))
        except: return None

    bottom = [(parse_num(t), (bb[0]+bb[2])/2.0) for t,bb in words if NUM_RE.match(t) and in_box(bb, bottom_band)]
    bottom = [(v,cx) for v,cx in bottom if v is not None]
    bottom.sort(key=lambda x: x[1])

    left = [(parse_num(t), (bb[1]+bb[3])/2.0) for t,bb in words if NUM_RE.match(t) and in_box(bb, left_band)]
    left  = [(v,cy) for v,cy in left if v is not None]
    left.sort(key=lambda x: x[1])

    def monotonic_ok(vals):
        if len(vals) < 2: return False
        inc = sum(1 for i in range(1,len(vals)) if vals[i] >= vals[i-1])
        dec = sum(1 for i in range(1,len(vals)) if vals[i] <= vals[i-1])
        return max(inc, dec) / (len(vals)-1) >= min_monotonic_ratio

    bottom_ok = len(bottom) >= min_ticks and monotonic_ok([v for v,_ in bottom])
    left_ok   = len(left)   >= min_ticks and monotonic_ok([v for v,_ in left])
    return bottom_ok or left_ok

def looks_like_axis_ticks_from_image(img_path: str,
                                     bottom_ratio=0.22, left_ratio=0.22,
                                     min_ticks=4, min_monotonic_ratio=0.7):
    try:
        im = Image.open(img_path).convert("L")
    except Exception:
        return False
    W, H = im.size
    bottom = im.crop((0, int(H*(1-bottom_ratio)), W, H))
    left   = im.crop((0, 0, int(W*left_ratio), H))

    def extract_numbers(crop: Image.Image, axis="x"):
        words = _ocr_words_from_pil(crop)
        out = []
        for w in words:
            t = (w["text"] or "").strip()
            if not NUM_RE.match(t):
                continue
            x0,y0,x1,y1 = w["bbox_px"]
            cx, cy = (x0+x1)/2.0, (y0+y1)/2.0
            try:
                v = float(t.replace(",", ""))
            except:
                continue
            out.append((v, cx if axis=="x" else cy))
        out.sort(key=lambda x: x[1])
        return [v for v,_ in out]

    xs = extract_numbers(bottom, "x")
    ys = extract_numbers(left, "y")

    def monotonic_ok(vals):
        if len(vals) < 2: return False
        inc = sum(1 for i in range(1,len(vals)) if vals[i] >= vals[i-1])
        dec = sum(1 for i in range(1,len(vals)) if vals[i] <= vals[i-1])
        return max(inc, dec) / (len(vals)-1) >= min_monotonic_ratio

    bottom_ok = len(xs) >= min_ticks and monotonic_ok(xs)
    left_ok   = len(ys) >= min_ticks and monotonic_ok(ys)
    return bottom_ok or left_ok

def is_chart_image(page_dict: dict, img_bbox, caption_text: str, img_path: str | None) -> bool:
    if is_chart_caption(caption_text):
        return True
    neigh = gather_neighbor_text(page_dict, img_bbox, margin=35)
    if looks_like_axis_ticks_from_page(img_bbox, neigh):
        return True
    if img_path and looks_like_axis_ticks_from_image(img_path):
        return True
    return False

def _wrap_by_width(draw, text, font, max_px):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        bb = draw.textbbox((0,0), test, font=font)
        if (bb[2]-bb[0]) <= max_px or not cur: cur = test
        else: lines.append(cur); cur = w
    if cur: lines.append(cur)
    return lines

def draw_caption_on_image(img_path: Path, caption: str, placeholder: str | None = None, force_single_line: bool = False):
    abs_path = Path(img_path).resolve()
    try:
        im = Image.open(abs_path).convert("RGB")
    except Exception as e:
        print(f"[warn] open failed: {abs_path} :: {e}")
        return False

    text = caption.strip() if caption and caption.strip() else (placeholder or "").strip()
    if not text:
        return False

    W, H = im.size
    try:
        font = ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    PAD = CAPTION_PADDING; LSP = CAPTION_LINE_SPACING
    BG, BD, BDW, FG = CAPTION_BG, CAPTION_BORDER, CAPTION_BORDER_W, CAPTION_FG

    wrap_width_px = int(W * CAPTION_WRAP_PX_RATIO)
    draw_tmp = ImageDraw.Draw(im)

    if force_single_line:
        lines = [_ellipsize_single_line(draw_tmp, text, font, wrap_width_px)]
    else:
        lines = _wrap_by_width(draw_tmp, text, font, wrap_width_px)

    line_h = []
    for ln in lines:
        bb = draw_tmp.textbbox((0,0), ln, font=font)
        line_h.append(bb[3] - bb[1])
    text_h = sum(line_h) + (LSP * (len(lines) - 1 if len(lines) > 0 else 0))
    box_h  = text_h + PAD*2

    new_im = Image.new("RGB", (W, H + box_h), BG)
    new_im.paste(im, (0,0))
    draw = ImageDraw.Draw(new_im)
    draw.rectangle([0, H, W-1, H + box_h - 1], outline=BD, width=BDW)

    y = H + PAD
    for i, ln in enumerate(lines):
        tw = draw.textbbox((0,0), ln, font=font)[2]
        x  = (W - tw)//2
        draw.text((x, y), ln, fill=FG, font=font)
        y += line_h[i] + LSP

    out_path = abs_path.with_name(abs_path.stem + "_cap" + abs_path.suffix) if SAVE_WITH_SUFFIX else abs_path
    new_im.save(out_path)
    print(f"[cap] saved: {out_path}")
    return True

# ===================== 7) CLIP 임베딩 =====================
import torch
from transformers import CLIPModel, CLIPProcessor
_device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
clip_model = CLIPModel.from_pretrained(CLIP_MODEL_NAME).to(_device).eval()
clip_proc  = CLIPProcessor.from_pretrained(CLIP_MODEL_NAME)

@torch.no_grad()
def clip_text_embed(texts):
    if isinstance(texts, str): texts=[texts]
    if not texts: return np.zeros((0, 512), dtype=np.float32)
    batch = clip_proc(text=texts, padding=True, truncation=True, return_tensors="pt")
    batch = {k: v.to(_device) for k, v in batch.items()}
    feats = clip_model.get_text_features(**batch)
    feats = torch.nn.functional.normalize(feats, p=2, dim=1)
    return feats.detach().cpu().numpy().astype(np.float32)

@torch.no_grad()
def clip_image_embed(paths, batch_size=16):
    feats = []
    cur_imgs, cur_idx = [], []
    def _flush(imgs):
        if not imgs: return None
        batch = clip_proc(images=imgs, return_tensors="pt")
        batch = {k: v.to(_device) for k, v in batch.items()}
        out = clip_model.get_image_features(**batch)
        out = torch.nn.functional.normalize(out, p=2, dim=1)
        return out.detach().cpu().numpy().astype(np.float32)
    for i, p in enumerate(paths):
        try:
            img = Image.open(p).convert("RGB")
        except Exception:
            img = None
        if img is not None:
            cur_imgs.append(img); cur_idx.append(i)
        if len(cur_imgs) == batch_size:
            emb = _flush(cur_imgs); feats.append((cur_idx[:], emb))
            cur_imgs, cur_idx = [], []
    if cur_imgs:
        emb = _flush(cur_imgs); feats.append((cur_idx[:], emb))
    if not feats: return np.zeros((0, 512), dtype=np.float32)
    dim = feats[0][1].shape[1]
    out = np.zeros((len(paths), dim), dtype=np.float32)
    for idxs, emb in feats: out[np.array(idxs)] = emb
    return out

# ===================== 8) OpenAI API 래퍼 =====================
def openai_chat(messages, model=None, temperature=0.2):
    """messages: [{'role':'system'|'user'|'assistant','content':str}, ...]"""
    api_key = "your apikey"
    model = os.environ.get("OPENAI_MODEL", model or "gpt-4o-mini")
    if not api_key:
        return {"ok": False, "error": "OPENAI_API_KEY not set"}

    url = "https://api.openai.com/v1/chat/completions"
    payload = {"model": model, "messages": messages, "temperature": float(temperature)}
    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            js = json.loads(resp.read().decode("utf-8"))
            text = js["choices"][0]["message"]["content"]
            return {"ok": True, "model": model, "text": text}
    except urllib.error.HTTPError as e:
        try:
            err = e.read().decode("utf-8")
        except Exception:
            err = str(e)
        return {"ok": False, "error": err}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ===================== 9) 메인 파이프라인 =====================
def main():
    print("PDF     :", PDF_PATH.resolve())
    print("OUT_DIR :", OUT_DIR.resolve())
    if not PDF_PATH.exists():
        raise FileNotFoundError(f"PDF not found: {PDF_PATH}")

    # 1) PDF + OCR
    doc_meta = parse_pdf(PDF_PATH)
    print("Pages parsed:", doc_meta["page_count"])

    # 2) 이미지 추출
    xref_to_path = export_images(PDF_PATH)
    for p in doc_meta["pages"]:
        for im in p["images"]:
            im["path"] = xref_to_path.get(im["xref"])
    print("Exported images:", len(xref_to_path))

    # 3) 차트 필터 + 캡션 합성(캡션 있는 경우에만)
    path_to_caption = {}
    for p in doc_meta["pages"]:
        for im in p["images"]:
            if not im.get("path"):
                continue
            cap = extract_caption_for_image(p, im["bbox"])
            if is_chart_image(p, im["bbox"], cap, im["path"]):
                try:
                    base = Path(im["path"])
                    if base.exists(): base.unlink()
                    capver = base.with_name(base.stem + "_cap" + base.suffix)
                    if capver.exists(): capver.unlink()
                    print(f"[chart-skip] removed and skipped: {base.name} (cap='{(cap or '')[:60]}')")
                except Exception as e:
                    print("[chart-skip] remove failed:", e)
                im["path"] = None
                im["_skip_chart"] = True
                continue
            if not cap or not cap.strip():
                print(f"[no-caption] skipped: {im['path']}")
                continue
            abs_img_path = Path(im["path"]).resolve()
            if draw_caption_on_image(abs_img_path, cap, None, force_single_line=True):
                path_to_caption[abs_img_path.as_posix()] = cap

    # 4) manifest 저장
    for p in doc_meta["pages"]:
        save_json(PAGES_DIR / f"page_{p['page']+1:04d}.json", p)
    manifest = {
        "doc_name": DOC_NAME, "pdf": str(PDF_PATH), "out_dir": str(OUT_DIR),
        "page_count": doc_meta["page_count"], "pages": [],
        "images_total": sum(len(p["images"]) for p in doc_meta["pages"]),
    }
    for p in doc_meta["pages"]:
        txt_path = PAGES_DIR / f"page_{p['page']+1:04d}.txt"
        try: txt_sz = txt_path.stat().st_size
        except Exception: txt_sz = 0
        needs_ocr = (len(p["words"]) < 5) or (txt_sz < 10)
        manifest["pages"].append({
            "page": p["page"], "png": p["png"],
            "json": str(PAGES_DIR / f"page_{p['page']+1:04d}.json"),
            "txt": str(txt_path), "words": len(p["words"]),
            "images": len(p["images"]), "needs_ocr": bool(needs_ocr),
            "used_ocr": bool(p.get("used_ocr", False)),
        })
    save_json(MANIFEST, manifest)
    print("Manifest saved")

    # 5) 이미지 후보 수집 (캡션 합성본만 사용)
    def _rel_from_images_dir(p: Path) -> str:
        try: return str(p.resolve().relative_to(OUT_DIR.resolve())).replace("\\", "/")
        except Exception:
            s = str(p).replace("\\", "/")
            prefix = str(OUT_DIR).replace("\\", "/").rstrip("/") + "/"
            return s[len(prefix):] if s.startswith(prefix) else s

    all_img_cands = []
    img_files = sorted([p for p in IMAGES_DIR.glob("*")
                        if p.suffix.lower() in (".png",".jpg",".jpeg",".bmp",".webp")])
    for f in img_files:
        if SAVE_WITH_SUFFIX:
            cap_f = f.with_name(f.stem + "_cap" + f.suffix)
            if not cap_f.exists():
                continue
            rel = _rel_from_images_dir(cap_f)
        else:
            rel = _rel_from_images_dir(f)
        try:
            with Image.open(OUT_DIR / rel) as _im:
                w, h = _im.size
            if w <= 220 or h <= 220:
                continue
        except Exception:
            continue
        all_img_cands.append({"path": rel, "bbox": None, "page": None})
    print(f"[info] collected {len(all_img_cands)} images (charts removed, captions only)")

    # 6) 임베딩/매칭
    abs_img_paths = [(OUT_DIR / c["path"]).resolve() for c in all_img_cands]
    img_embs = clip_image_embed(abs_img_paths) if abs_img_paths else np.zeros((0,512), np.float32)

    if FUSE_WITH_CAPTION and path_to_caption:
        captions = [(OUT_DIR / c["path"]).resolve().as_posix() for c in all_img_cands]
        cap_texts = [path_to_caption.get(p, "") for p in captions]
        if any(bool(x.strip()) for x in cap_texts):
            txt_feats = clip_text_embed([cap if cap else "" for cap in cap_texts])
            if txt_feats.shape[0] == img_embs.shape[0]:
                a = float(FUSE_IMG_WEIGHT); b = 1.0 - a
                fused = a * img_embs + b * txt_feats
                norms = np.linalg.norm(fused, axis=1, keepdims=True) + 1e-9
                img_embs = (fused / norms).astype(np.float32)

    pages_compact = []
    for p in doc_meta["pages"]:
        words = [w for w in p.get("words", []) if len((w.get("text") or "")) >= MIN_WORD_LEN]
        q_texts = [w["text"] for w in words]
        dim = img_embs.shape[1] if img_embs.size else 512
        q_clip = clip_text_embed(q_texts) if q_texts else np.zeros((0, dim), dtype=np.float32)

        img_matches_per_word = [[] for _ in range(len(words))]
        if q_clip.size > 0 and img_embs.size > 0:
            sims = (q_clip @ img_embs.T).astype(np.float32)
            for i in range(sims.shape[0]):
                row = sims[i]
                idxs = [j for j, s in enumerate(row) if s >= SIM_THRESHOLD]
                pairs = [(float(row[j]), j) for j in idxs]
                pairs.sort(key=lambda x: x[0], reverse=True)
                top = pairs[:TOP_K]
                img_matches_per_word[i] = [
                    {"score": s, "path": all_img_cands[j]["path"], "bbox": None, "page": None}
                    for (s, j) in top
                ]

        pages_compact.append({
            "page": p["page"],
            "png": str(Path(p["png"]).resolve().relative_to(OUT_DIR.resolve())).replace("\\", "/"),
            "txt": str(Path(PAGES_DIR / f"page_{p['page']+1:04d}.txt").resolve().relative_to(OUT_DIR.resolve())).replace("\\","/"),
            "width": p["width"], "height": p["height"],
            "words": [{"text": words[i]["text"], "bbox": words[i]["bbox"], "images": img_matches_per_word[i]}
                      for i in range(len(words))]
        })

    save_json(INDEX_JSON, {"pages": pages_compact})
    print("index.json saved →", INDEX_JSON.resolve())

    # 7) 뷰어 HTML 저장 (+ 모드 버튼 + 챗봇)
    html = """<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<title>CLIP Overlay Viewer + Chat</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
body { margin:0; font-family:system-ui, -apple-system, Segoe UI, Roboto, 'Noto Sans KR', sans-serif; background:#fafafa;}
.wrap { display:flex; height:100vh; }
.left { width:70%; border-right:1px solid #ddd; padding:12px; overflow:auto; box-sizing:border-box; background:#fff;}
.right { flex:1; padding:12px; overflow:auto; box-sizing:border-box; display:flex; flex-direction:column; gap:12px;}
.canvas { position:relative; background-size:100% 100%; background-repeat:no-repeat; background-position:top left; }
.wbtn { position:absolute; background: transparent; border:1px solid transparent; cursor:pointer; outline:none; user-select:none; -webkit-tap-highlight-color: transparent; }
.wbtn:hover { border-color: rgba(0,0,0,0.25); box-shadow:0 0 0 2px rgba(0,0,0,0.06) inset; }
.selbox { position:absolute; border:1px dashed #8aa; background:rgba(120,160,255,.15); pointer-events:none; }
.placeholder { color:#888; }
img { max-width:100%; height:auto; display:block; }
.card { border:1px solid #eee; border-radius:10px; padding:10px; margin-bottom:10px; box-shadow:0 4px 16px rgba(0,0,0,.06); background:#fff; }
.score { color:#444; font-size:12px; margin-bottom:6px; }
.toolbar { display:flex; gap:8px; align-items:center; margin:0 0 0 0; }
.btn { appearance:none; border:1px solid #ddd; background:#f7f7f7; border-radius:8px; padding:6px 10px; font-size:13px; cursor:pointer; }
.btn:hover { background:#efefef; }
.btn.active { background:#dfe9ff; border-color:#bcd; }
.actions { display:flex; gap:8px; margin-top:8px; }
.hgroup { display:flex; gap:8px; align-items:center; }
h3 { margin:0; font-size:16px;}
.section { background:#fff; border:1px solid #eee; border-radius:10px; padding:10px;}
#results { min-height:120px; }
.chat { display:flex; flex-direction:column; gap:8px;}
.chatlog { max-height:40vh; overflow:auto; border:1px solid #eee; border-radius:10px; padding:10px; background:#fff;}
.msg { margin:8px 0; }
.msg.me { text-align:right; }
.msg .bubble { display:inline-block; padding:8px 10px; border-radius:10px; background:#eef; }
.msg.me .bubble { background:#e9f7e9; }
.chatctrl { display:flex; gap:8px; align-items:flex-start; }
.chatctrl textarea { flex:1; min-height:60px; resize:vertical; font:inherit; padding:8px; border-radius:8px; border:1px solid #ddd; }
.small { font-size:12px; color:#666; }
</style>
</head>
<body>
<div class="wrap">
  <div class="left">
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;">
      <label for="pageSel">Page:</label>
      <select id="pageSel"></select>
    </div>
    <div id="stage"></div>
  </div>

  <div class="right">
    <div class="section">
      <div class="toolbar">
        <div class="hgroup" style="flex:1">
          <button class="btn modebtn active" data-mode="images">이미지</button>
          <button class="btn modebtn" data-mode="translate">번역</button>
          <button class="btn modebtn" data-mode="summarize">요약</button>
        </div>
        <button id="clearBtn" class="btn" title="오른쪽 결과 영역 초기화">초기화</button>
      </div>
      <div id="results"><p class='placeholder'>모드를 선택하고, 캔버스에서 단어를 클릭(이미지) 또는 드래그(번역/요약) 해보세요.</p></div>
    </div>

    <div class="section chat">
      <div style="display:flex; align-items:center; gap:8px;">
        <h3 style="flex:1;">문서 기반 챗봇</h3>
        <label class="small"><input type="checkbox" id="usePageContext" checked/> 현재 페이지 텍스트 사용</label>
      </div>
      <div id="chatlog" class="chatlog"></div>
      <div class="chatctrl">
        <textarea id="chatText" placeholder="질문을 입력하세요 (예: 이 논문의 핵심 기여는?)"></textarea>
        <button id="chatSend" class="btn">보내기</button>
      </div>
      <div id="chatHint" class="small">OpenAI 모델: <code>gpt-4o-mini</code> (환경변수 <code>OPENAI_MODEL</code>로 변경 가능)</div>
    </div>
  </div>
</div>

<script>
let DATA=null;
let CURRENT_MODE="images"; // images | translate | summarize
const sel=document.getElementById('pageSel');
const stage=document.getElementById('stage');
const results=document.getElementById('results');
const clearBtn=document.getElementById('clearBtn');
const chatlog=document.getElementById('chatlog');
const chatText=document.getElementById('chatText');
const chatSend=document.getElementById('chatSend');
const usePageContext=document.getElementById('usePageContext');

function getParam(name, def){const u=new URL(location.href);return u.searchParams.get(name)??def;}
const JSON_URL=getParam('json','index.json');

function setMode(m){
  CURRENT_MODE = m;
  document.querySelectorAll('.modebtn').forEach(b=>{
    b.classList.toggle('active', b.dataset.mode===m);
  });
  if(m==='images'){
    if(!results.querySelector('.card')) results.innerHTML="<p class='placeholder'>단어를 클릭하면 관련 이미지가 쌓입니다.</p>";
  }else{
    if(!results.querySelector('.card')) results.innerHTML="<p class='placeholder'>드래그로 텍스트 영역을 선택하세요.</p>";
  }
}

async function boot(){
  try{
    const res=await fetch(JSON_URL,{cache:'no-store'});
    DATA=await res.json();
    init();
  }catch(e){
    console.error(e);
    stage.innerHTML='<p class="placeholder">index.json 로드 실패</p>';
  }
}
function init(){
  const frag=document.createDocumentFragment();
  for(const p of (DATA.pages||[])){
    const opt=document.createElement('option');
    opt.value=String(p.page);
    opt.textContent='Page '+(p.page+1);
    frag.appendChild(opt);
  }
  sel.appendChild(frag);
  clearBtn.addEventListener('click',()=>results.innerHTML="<p class='placeholder'>결과가 여기에 표시됩니다.</p>");
  sel.addEventListener('change',()=>renderPage(parseInt(sel.value)));
  document.querySelectorAll('.modebtn').forEach(b=>{
    b.addEventListener('click',()=>setMode(b.dataset.mode));
  });
  chatSend.addEventListener('click', onChatSend);

  if(DATA.pages?.length) renderPage(DATA.pages[0].page);
  else stage.innerHTML='<p class="placeholder">No pages</p>';
}

function makeOverlayHTML(p){
  if(!(p.png&&p.width&&p.height)) return '<p class="placeholder">No PNG available.</p>';
  return `<div class="canvas" id="canvas" data-page="${p.page}" style="background-image:url('${p.png}');"></div>`;
}

function placeButtons(p, canvas, pngW, pngH){
  const sx=pngW/p.width, sy=pngH/p.height;
  let html='';
  for(const it of p.words){
    const b=it.bbox;
    const l=Math.max(0,Math.round(b[0]*sx));
    const t=Math.max(0,Math.round(b[1]*sy));
    const wpx=Math.max(1,Math.round((b[2]-b[0])*sx));
    const hpx=Math.max(1,Math.round((b[3]-b[1])*sy));
    const payload=encodeURIComponent(JSON.stringify({word:it.text,images:it.images}));
    html+=`<button class="wbtn" style="left:${l}px;top:${t}px;width:${wpx}px;height:${hpx}px"
             data-payload="${payload}" data-text="${it.text}" title="${it.text}"></button>`;
  }
  const canvasEl=document.getElementById('canvas');
  canvasEl.innerHTML=html;
  canvasEl.querySelectorAll('.wbtn').forEach(btn=>{
    btn.addEventListener('click',()=>{
      if(CURRENT_MODE!=='images') return;
      const payload=JSON.parse(decodeURIComponent(btn.dataset.payload));
      appendMatches(payload);
    });
  });
  addDragSelector(canvasEl, p); // 드래그 선택(번역/요약)
}

function basename(p){if(!p)return'image.png';const parts=p.split('?')[0].split('#')[0].split('/');return parts[parts.length-1]||'image.png';}
async function triggerDownload(url,filename){
  try{
    const res=await fetch(url);const blob=await res.blob();
    const objectUrl=URL.createObjectURL(blob);
    const a=document.createElement('a');a.href=objectUrl;a.download=filename||basename(url);
    document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(objectUrl);
  }catch(e){window.open(url,'_blank');}
}
function appendMatches(payload){
  const imgs=(payload.images||[]);
  if(imgs.length===0){
    if(!results.querySelector('.card')) results.innerHTML="<p class='placeholder'>관련 이미지 없음 (threshold 미만)</p>";
    else{
      const emptyCard=document.createElement('div'); emptyCard.className='card';
      emptyCard.innerHTML="<div class='score'>관련 이미지 없음 (threshold 미만)</div>";
      results.appendChild(emptyCard);
    }
    return;
  }
  const placeholder=results.querySelector('.placeholder'); if(placeholder) results.innerHTML='';
  const frag=document.createDocumentFragment();
  imgs.forEach(im=>{
    const fname=basename(im.path);
    const card=document.createElement('div'); card.className='card';
    card.innerHTML=`
      <div class="score">score: ${Number(im.score||0).toFixed(3)}</div>
      <img src="${im.path}" alt="${fname}"/>
      <div class="actions">
        <button class="btn savebtn" data-src="${im.path}" data-fn="${fname}">이미지 저장</button>
      </div>`;
    frag.appendChild(card);
  });
  results.appendChild(frag);
  results.querySelectorAll('.savebtn').forEach(b=>{
    if(b.dataset._wired) return; b.dataset._wired='1';
    b.addEventListener('click',()=>{
      const src=b.getAttribute('data-src'); const fn=b.getAttribute('data-fn')||basename(src);
      triggerDownload(src,fn);
    });
  });
}

// ===== 선택→번역/요약 =====
function addDragSelector(canvasEl, pageMeta){
  let selecting=false, sx=0, sy=0, box=null;

  function getRelPos(e){
    const r=canvasEl.getBoundingClientRect();
    return {x:e.clientX - r.left, y:e.clientY - r.top};
  }
  function rect(a,b){ return {x0:Math.min(a.x,b.x), y0:Math.min(a.y,b.y),
                              x1:Math.max(a.x,b.x), y1:Math.max(a.y,b.y)}; }
  function intersects(a, b){
    return !(b.left > a.x1 || b.right < a.x0 || b.top > a.y1 || b.bottom < a.y0);
  }
  function toTextInReadingOrder(btns){
    const items = btns.map(b => ({
      t: b.getAttribute('data-text') || b.title || '',
      x: b.offsetLeft, y: b.offsetTop
    })).filter(x => x.t && x.t.trim());
    items.sort((p,q)=> (p.y - q.y) || (p.x - q.x));
    return items.map(i=>i.t).join(' ').replace(/\s+/g,' ').trim();
  }

  canvasEl.addEventListener('mousedown', (e)=>{
    if(CURRENT_MODE==='images') return; // 이미지 모드에서는 드래그 선택 비활성
    selecting=true;
    const p=getRelPos(e);
    sx=p.x; sy=p.y;
    box=document.createElement('div');
    box.className='selbox';
    box.style.left = sx+'px'; box.style.top = sy+'px';
    box.style.width='0px'; box.style.height='0px';
    canvasEl.appendChild(box);
    e.preventDefault();
  });

  window.addEventListener('mousemove', (e)=>{
    if(!selecting) return;
    const p=getRelPos(e);
    const r=rect({x:sx,y:sy}, p);
    box.style.left=r.x0+'px'; box.style.top=r.y0+'px';
    box.style.width=(r.x1-r.x0)+'px'; box.style.height=(r.y1-r.y0)+'px';
  });

  window.addEventListener('mouseup', async (e)=>{
    if(!selecting) return;
    selecting=false;
    const p=getRelPos(e);
    const r=rect({x:sx,y:sy}, p);
    if(box){ box.remove(); box=null; }
    if(Math.abs(r.x1-r.x0) < 3 && Math.abs(r.y1-r.y0) < 3) return;

    // 선택 영역과 교차하는 단어 버튼 수집
    const btns = Array.from(canvasEl.querySelectorAll('.wbtn')).filter(b=>{
      const bb = {left:b.offsetLeft, top:b.offsetTop,
                  right:b.offsetLeft + b.offsetWidth, bottom:b.offsetTop + b.offsetHeight};
      return intersects(r, bb);
    });

    const text = toTextInReadingOrder(btns);
    if(!text) return;

    if(CURRENT_MODE==='translate'){
      showLLMCard("번역", text, "ko-translate");
      const out = await callOpenAI({mode:"translate", text});
      updateLastLLMCard(out);
    }else if(CURRENT_MODE==='summarize'){
      showLLMCard("요약", text, "ko-summary");
      const out = await callOpenAI({mode:"summarize", text});
      updateLastLLMCard(out);
    }
  });
}

function showLLMCard(kind, src, cls){
  const placeholder=results.querySelector('.placeholder'); if(placeholder) results.innerHTML='';
  const card=document.createElement('div'); card.className='card';
  card.innerHTML = `
    <div class="small">${kind}</div>
    <div style="white-space:pre-wrap; margin:.4rem 0"><b>선택 텍스트</b><br>${src}</div>
    <div style="white-space:pre-wrap"><b>결과</b><br><span class="${cls}">처리 중...</span></div>`;
  results.prepend(card);
}
function updateLastLLMCard(out){
  const el = results.querySelector('.card .ko-translate, .card .ko-summary');
  if(!el) return;
  if(!out || !out.ok) el.textContent = "(요청 실패: " + (out && out.error ? out.error : "unknown") + ")";
  else el.textContent = out.text || "(빈 응답)";
}

// ---- OpenAI 호출(서버 프록시) ----
async function callOpenAI(payload){
  try{
    const res = await fetch('/api/openai', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    return await res.json();
  }catch(e){
    return {ok:false, error:String(e)};
  }
}

// ============ Chatbot ============
let CHAT_HISTORY=[];
function pushChat(role, text){
  CHAT_HISTORY.push({role, content:text});
  const div=document.createElement('div'); div.className='msg '+(role==='user'?'me':'');
  div.innerHTML=`<span class="bubble">${text.replace(/</g,'&lt;')}</span>`;
  chatlog.appendChild(div);
  chatlog.scrollTop = chatlog.scrollHeight;
}
async function onChatSend(){
  const q = chatText.value.trim();
  if(!q) return;
  chatText.value='';
  pushChat('user', q);

  let context = '';
  if(usePageContext.checked){
    const pno = parseInt(sel.value||'0');
    const p = (DATA.pages||[]).find(x=>x.page==pno);
    if(p?.txt){
      try{
        const t = await (await fetch(p.txt, {cache:'no-store'})).text();
        context = t.slice(0, 12000); // 과도 보호
      }catch(e){}
    }
  }

  const res = await callOpenAI({mode:'chat', history:CHAT_HISTORY, context});
  const text = (res && res.ok) ? res.text : '(실패: ' + (res && res.error ? res.error : 'unknown') + ')';
  pushChat('assistant', text);
}

function renderPage(pno){
  const p=(DATA.pages||[]).find(x=>x.page==pno);
  if(!p){ stage.innerHTML='<p class="placeholder">No page</p>'; return; }
  stage.innerHTML=makeOverlayHTML(p);
  const canvas=document.getElementById('canvas');
  const img=new Image();
  img.onload=function(){
    canvas.style.width=img.naturalWidth+'px';
    canvas.style.height=img.naturalHeight+'px';
    placeButtons(p, canvas, img.naturalWidth, img.naturalHeight);
  };
  img.src=p.png;
}

boot();
</script>
</body>
</html>"""
    with open(HTML_OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print("Viewer saved →", HTML_OUT.resolve())

    # 8) 로컬 서버 자동 오픈
    serve_and_open(OUT_DIR, HTML_OUT.name)

# ===================== 10) 로컬 서버(+ API) =====================
def free_port(preferred=8000, max_tries=20):
    p = preferred
    for _ in range(max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", p))
                return p
            except OSError:
                p += 1
    raise RuntimeError("no free port")

def serve_and_open(root: Path, entry_html: str):
    class RootHandler(SimpleHTTPRequestHandler):
        def translate_path(self, path):
            from urllib.parse import unquote, urlsplit
            path = urlsplit(path).path
            path = unquote(path)
            p = (root / path.lstrip("/")).resolve()
            return str(p)

        def _read_json(self):
            try:
                ln = int(self.headers.get("Content-Length","0"))
                body = self.rfile.read(ln) if ln>0 else b"{}"
                return json.loads(body.decode("utf-8"))
            except Exception:
                return {}

        # GET: 정적 + (필요시 다른 API 추가 가능)
        # def do_GET(self):
        #     return super().do_GET()

        # POST: /api/openai
        def do_POST(self):
            from urllib.parse import urlparse
            u = urlparse(self.path)
            if u.path == "/api/openai":
                req = self._read_json()
                mode = req.get("mode","chat")
                if mode == "translate":
                    text = (req.get("text") or "").strip()
                    messages = [
                        {"role":"system","content":"You are a translation engine. Translate the user's English text into natural Korean. Output only the translation."},
                        {"role":"user","content": text}
                    ]
                    out = openai_chat(messages, temperature=0.2)
                elif mode == "summarize":
                    text = (req.get("text") or "").strip()
                    messages = [
                        {"role":"system","content":"You summarize the given passage in concise Korean (3-6 sentences)."},
                        {"role":"user","content": text}
                    ]
                    out = openai_chat(messages, temperature=0.2)
                else:  # chat
                    hist = req.get("history") or []
                    ctx  = (req.get("context") or "").strip()
                    sys_prompt = "You are a helpful assistant that answers in Korean. If context is provided, ground your answers in it and say when the answer is not in the context."
                    messages = [{"role":"system","content": sys_prompt}]
                    if ctx:
                        messages.append({"role":"system","content": "문서 컨텍스트:\n" + ctx})
                    # sanitize history
                    for m in hist:
                        role = m.get("role") in ("user","assistant","system") and m["role"] or "user"
                        content = str(m.get("content",""))[:12000]
                        messages.append({"role": role, "content": content})
                    out = openai_chat(messages, temperature=0.2)

                data = json.dumps(out, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

            return super().do_POST()

    port = free_port(8000)
    httpd = ThreadingHTTPServer(("0.0.0.0", port), RootHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    url = f"http://localhost:{port}/{entry_html}"
    print(f"\n[Local Server] {url}")
    print("Press Ctrl+C to stop server.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()

if __name__ == "__main__":
    main()
