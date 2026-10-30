import hashlib, uuid, time
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from .config import RAW_DIR
from .store import init_db, add_document, documents, get_document, all_chunks, add_benchmark_item, benchmark_items
from .pipeline import extract_file, split_chunks, digest
from .embeddings import vectors, rank
from .entities import extract as extract_entities
from . import sec

app=FastAPI(title="Folio API",version="0.1.0",description="Local-first financial filing search and intelligence")
app.add_middleware(CORSMiddleware,allow_origins=["http://localhost:5173","http://127.0.0.1:5173"],allow_methods=["*"],allow_headers=["*"])
@app.on_event("startup")
def startup(): init_db()

class SearchRequest(BaseModel): query:str=Field(min_length=2,max_length=1000); top_k:int=Field(default=5,ge=1,le=20); ticker:str|None=None; form:str|None=None
class EntityRequest(BaseModel): text:str=Field(min_length=1,max_length=100000)
class BenchmarkRequest(BaseModel): query:str=Field(min_length=2,max_length=1000); relevant_chunk_id:str; ticker:str|None=None; form:str|None=None
class SecRequest(BaseModel): ticker:str=Field(min_length=1,max_length=10); forms:list[str]=["10-K","10-Q"]; limit:int=Field(default=3,ge=1,le=10)

def ingest(path,doc):
    doc_id=doc.get("id") or digest(doc.get("source",str(path)))
    doc["id"]=doc_id
    pages=extract_file(path); chunks=split_chunks(pages,doc_id)
    for chunk,embedding in zip(chunks,vectors([c["text"] for c in chunks])): chunk["embedding"]=embedding
    add_document(doc,chunks)
    return {"document_id":doc_id,"chunks":len(chunks),"filename":doc.get("filename")}

@app.get("/health")
def health(): return {"status":"ok","documents":len(documents())}

@app.get("/documents")
def list_documents(): return documents()

@app.get("/documents/{document_id}")
def document_detail(document_id:str):
    doc=get_document(document_id)
    if not doc: raise HTTPException(404,"Document not found")
    return doc

@app.post("/documents/ingest")
async def ingest_upload(file:UploadFile=File(...),ticker:str|None=None,company:str|None=None,form:str|None=None,filing_date:str|None=None):
    suffix=Path(file.filename or "upload").suffix.lower()
    if suffix not in {".pdf",".html",".htm"}: raise HTTPException(400,"Upload a PDF or HTML filing")
    content=await file.read()
    if len(content)>50_000_000: raise HTTPException(413,"File exceeds 50 MB")
    path=RAW_DIR/f"{uuid.uuid4().hex}{suffix}"; path.write_bytes(content)
    try: return ingest(path,{"ticker":ticker.upper() if ticker else None,"company":company,"form":form,"filing_date":filing_date,"year":filing_date[:4] if filing_date else None,"source":"upload","filename":file.filename})
    except Exception as e: raise HTTPException(422,f"Could not process filing: {e}")

@app.post("/documents/ingest/sec")
def ingest_sec(request:SecRequest):
    forms=[f for f in request.forms if f in {"10-K","10-Q"}]
    if not forms: raise HTTPException(400,"forms must contain 10-K or 10-Q")
    try: entries=sec.recent_filings(request.ticker,forms,request.limit)
    except Exception as e: raise HTTPException(502,str(e))
    results=[]
    for item in entries:
        doc_id=digest(item["url"])
        if get_document(doc_id): results.append({"document_id":doc_id,"chunks":0,"filename":item["filename"],"status":"already indexed"}); continue
        try:
            body=sec.download(item["url"])
            suffix=Path(item["filename"]).suffix.lower() or ".html"
            path=RAW_DIR/f"{doc_id}{suffix}"; path.write_bytes(body)
            result=ingest(path,{"id":doc_id,"ticker":item["ticker"],"company":item["company"],"form":item["form"],"filing_date":item["filing_date"],"year":item["year"],"source":item["url"],"filename":item["filename"]})
            results.append({**result,"status":"ingested"})
        except Exception as e: results.append({"filename":item["filename"],"status":"error","error":str(e)})
    return {"ticker":request.ticker.upper(),"results":results}

@app.post("/search")
def search(request:SearchRequest):
    chunks=all_chunks()
    if request.ticker: chunks=[c for c in chunks if c["ticker"]==request.ticker.upper()]
    if request.form: chunks=[c for c in chunks if c["form"]==request.form]
    ranked=rank(request.query,chunks,request.top_k)
    return {"query":request.query,"results":[{"score":score,"chunk_id":c["id"],"text":c["text"],"page":c["page"],"section":c["section"],"document_id":c["document_id"],"ticker":c["ticker"],"company":c["company"],"form":c["form"],"filing_date":c["filing_date"],"filename":c["filename"],"source":c["source"]} for c,score in ranked]}

@app.post("/entities/extract")
def entities(request:EntityRequest): return {"entities":extract_entities(request.text)}


@app.get("/evaluation/benchmarks")
def list_benchmarks():
    items=benchmark_items()
    return {"count":len(items),"items":items}

@app.post("/evaluation/benchmarks")
def save_benchmark(request:BenchmarkRequest):
    chunks={c["id"]:c for c in all_chunks()}
    chunk=chunks.get(request.relevant_chunk_id)
    if not chunk: raise HTTPException(404,"Relevant passage not found")
    if request.ticker and chunk.get("ticker")!=request.ticker.upper(): raise HTTPException(400,"Passage does not match the selected ticker")
    item_id=add_benchmark_item(request.query,chunk["id"],request.ticker.upper() if request.ticker else None,request.form)
    return {"id":item_id,"status":"saved"}

@app.post("/evaluation/run")
def run_evaluation(top_k:int=5):
    if top_k<1 or top_k>20: raise HTTPException(400,"top_k must be between 1 and 20")
    items=benchmark_items()
    if not items: return {"count":0,"top_k":top_k,"metrics":{"precision_at_k":0,"recall_at_k":0,"mrr":0,"hit_rate_at_k":0,"mean_latency_ms":0},"results":[]}
    chunks=all_chunks(); rows=[]
    for item in items:
        candidates=chunks
        if item["ticker"]: candidates=[c for c in candidates if c["ticker"]==item["ticker"]]
        if item["form"]: candidates=[c for c in candidates if c["form"]==item["form"]]
        started=time.perf_counter(); ranked=rank(item["query"],candidates,top_k); latency=(time.perf_counter()-started)*1000
        predicted=[c["id"] for c,_ in ranked]; relevant=set(item["relevant_chunk_ids"])
        hits=[i+1 for i,cid in enumerate(predicted) if cid in relevant]
        rows.append({"id":item["id"],"query":item["query"],"relevant_count":len(relevant),"retrieved_relevant":len(hits),"precision_at_k":len(hits)/top_k,"recall_at_k":len(hits)/len(relevant) if relevant else 0,"reciprocal_rank":1/min(hits) if hits else 0,"hit":bool(hits),"latency_ms":round(latency,2)})
    n=len(rows)
    metrics={"precision_at_k":round(sum(r["precision_at_k"] for r in rows)/n,4),"recall_at_k":round(sum(r["recall_at_k"] for r in rows)/n,4),"mrr":round(sum(r["reciprocal_rank"] for r in rows)/n,4),"hit_rate_at_k":round(sum(r["hit"] for r in rows)/n,4),"mean_latency_ms":round(sum(r["latency_ms"] for r in rows)/n,2)}
    return {"count":n,"top_k":top_k,"metrics":metrics,"results":rows}
