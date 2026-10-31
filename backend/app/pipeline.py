import hashlib, re, json
from pathlib import Path
from bs4 import BeautifulSoup
import fitz
from .config import CHUNK_SIZE, CHUNK_OVERLAP

def extract_file(path: Path):
    if path.suffix.lower()==".pdf":
        with fitz.open(path) as pdf:
            return [(i+1, p.get_text("text")) for i,p in enumerate(pdf)]
    soup=BeautifulSoup(path.read_bytes(),"html.parser")
    for tag in soup(["script","style","ix:header"]): tag.decompose()
    text=soup.get_text("\n",strip=True)
    # HTML filings do not have stable pages; represent as one logical page.
    return [(1,text)]

def clean(text):
    text=text.replace("\u00a0"," ").replace("\x00", " ")
    return re.sub(r"[ \t]+"," ",re.sub(r"\n{3,}","\n\n",text)).strip()

def split_chunks(pages, doc_id):
    result=[]
    for page,text in pages:
        text=clean(text)
        if not text: continue
        paragraphs=[p.strip() for p in re.split(r"\n\s*\n",text) if p.strip()]
        joined="\n".join(paragraphs)
        start=0
        while start<len(joined):
            end=min(start+CHUNK_SIZE,len(joined))
            if end<len(joined):
                boundary=joined.rfind(" ",start+CHUNK_SIZE*2//3,end)
                if boundary>start: end=boundary
            body=joined[start:end].strip()
            if len(body)>60:
                digest=hashlib.sha1(f"{doc_id}:{page}:{start}".encode()).hexdigest()[:18]
                result.append({"id":digest,"page":page,"section":None,"text":body,"embedding":None})
            if end>=len(joined): break
            start=max(end-CHUNK_OVERLAP,start+1)
    return result

def digest(text): return hashlib.sha256(text.encode()).hexdigest()


class QualityError(ValueError):
    pass

def quality_report(pages, chunks, raw_bytes):
    page_count=len(pages)
    text="\n".join(text for _,text in pages)
    chars=len(text.strip())
    chunk_count=len(chunks)
    if page_count == 0: raise QualityError("No pages or HTML body were extracted")
    if chars < 100: raise QualityError(f"Only {chars} text characters were extracted; document may be empty, scanned, or unreadable")
    if chunk_count == 0: raise QualityError("No usable text chunks were produced")
    unique=len({c["text"].strip() for c in chunks})
    duplicate_ratio=1-(unique/chunk_count) if chunk_count else 0
    avg_chars=round(sum(len(c["text"]) for c in chunks)/chunk_count,1)
    warnings=[]
    if chars < 1200: warnings.append("Low extracted text volume; verify this is a complete report.")
    if duplicate_ratio >= 0.35: warnings.append("Many chunks repeat; review extraction and chunk boundaries.")
    alpha=sum(ch.isalpha() for ch in text)
    alpha_ratio=alpha/max(chars,1)
    if alpha_ratio < 0.35: warnings.append("Low alphabetic text ratio; the source may be scanned or poorly encoded.")
    if raw_bytes and chars/raw_bytes < 0.01: warnings.append("Very little text was recovered relative to file size; OCR may be needed.")
    return {"page_count":page_count,"extracted_chars":chars,"chunk_count":chunk_count,"average_chunk_chars":avg_chars,"duplicate_chunk_ratio":round(duplicate_ratio,3),"alphabetic_ratio":round(alpha_ratio,3),"raw_bytes":raw_bytes,"warnings":warnings}
