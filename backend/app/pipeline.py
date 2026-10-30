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
