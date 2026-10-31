import hashlib, uuid, time, threading
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from .config import RAW_DIR
from .store import init_db, add_document, documents, get_document, all_chunks, add_benchmark_item, benchmark_items, find_document_by_hash, log_ingestion_event, data_health
from .pipeline import extract_file, split_chunks, digest, quality_report, QualityError
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

_ingest_lock=threading.Lock()

def ingest(path,doc):
    # Keep duplicate detection and indexing atomic between concurrent local uploads.
    with _ingest_lock:
        return _ingest_serial(path,doc)

def _ingest_serial(path,doc):
    raw=path.read_bytes()
    content_hash=hashlib.sha256(raw).hexdigest()
    filename=doc.get("filename") or path.name
    source=doc.get("source") or "upload"
    existing=find_document_by_hash(content_hash)
    if existing:
        chunk_count=len(existing.get("chunks",[])) if "chunks" in existing else next((d["chunk_count"] for d in documents() if d["id"]==existing["id"]),0)
        log_ingestion_event(existing["id"],source,filename,content_hash,"dedupe","duplicate",chunk_count=chunk_count)
        return {"document_id":existing["id"],"chunks":chunk_count,"filename":existing.get("filename"),"status":"already indexed","content_hash":content_hash}
    doc_id=doc.get("id") or content_hash
    doc.update({"id":doc_id,"content_hash":content_hash})
    attempts=3
    for attempt in range(1,attempts+1):
        try:
            log_ingestion_event(doc_id,source,filename,content_hash,"received","started",attempt)
            pages=extract_file(path)
            chunks=split_chunks(pages,doc_id)
            quality=quality_report(pages,chunks,len(raw))
            quality_status="warning" if quality["warnings"] else "ok"
            log_ingestion_event(doc_id,source,filename,content_hash,"extraction","complete",attempt,len(chunks),quality)
            log_ingestion_event(doc_id,source,filename,content_hash,"chunking","complete",attempt,len(chunks),quality)
            embedded=vectors([c["text"] for c in chunks])
            for chunk,embedding in zip(chunks,embedded): chunk["embedding"]=embedding
            log_ingestion_event(doc_id,source,filename,content_hash,"embedding","complete",attempt,len(chunks),quality)
            doc.update({"quality":quality,"quality_status":quality_status,"processing_attempts":attempt})
            add_document(doc,chunks)
            log_ingestion_event(doc_id,source,filename,content_hash,"indexing","complete",attempt,len(chunks),quality)
            return {"document_id":doc_id,"chunks":len(chunks),"filename":filename,"status":"ingested","content_hash":content_hash,"quality":quality}
        except Exception as exc:
            log_ingestion_event(doc_id,source,filename,content_hash,"processing","failed",attempt,error=f"{type(exc).__name__}: {exc}")
            retryable=not isinstance(exc,(QualityError,ValueError))
            if attempt>=attempts or not retryable: raise
            log_ingestion_event(doc_id,source,filename,content_hash,"processing","retrying",attempt,error=f"{type(exc).__name__}: {exc}")
            time.sleep(0.5*(2**(attempt-1)))
    raise RuntimeError("Ingestion exhausted retries")

@app.get("/health")
def health(): return {"status":"ok","documents":len(documents())}

@app.get("/data-health")
def get_data_health(): return data_health()

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
    except QualityError as e: raise HTTPException(422,f"Document failed quality checks: {e}")
    except Exception as e: raise HTTPException(503,f"Could not process filing after retries: {e}")

@app.post("/documents/ingest/sec")
def ingest_sec(request:SecRequest):
    forms=[f for f in request.forms if f in {"10-K","10-Q"}]
    if not forms: raise HTTPException(400,"forms must contain 10-K or 10-Q")
    try: entries=sec.recent_filings(request.ticker,forms,request.limit)
    except Exception as e:
        log_ingestion_event(None,"sec_edgar",request.ticker,None,"sec_lookup","failed",error=f"{type(e).__name__}: {e}")
        raise HTTPException(502,str(e))
    results=[]
    for item in entries:
        doc_id=digest(item["url"])
        indexed=get_document(doc_id)
        if indexed:
            log_ingestion_event(doc_id,item["url"],item["filename"],indexed.get("content_hash"),"dedupe","duplicate",chunk_count=len(indexed.get("chunks",[])))
            results.append({"document_id":doc_id,"chunks":len(indexed.get("chunks",[])),"filename":item["filename"],"status":"already indexed"})
            continue
        try:
            body=sec.download(item["url"])
        except Exception as e:
            log_ingestion_event(doc_id,item["url"],item["filename"],None,"sec_download","failed",attempt=6,error=f"{type(e).__name__}: {e}")
            results.append({"filename":item["filename"],"status":"error","error":str(e)})
            continue
        try:
            suffix=Path(item["filename"]).suffix.lower() or ".html"
            path=RAW_DIR/f"{doc_id}{suffix}"; path.write_bytes(body)
            result=ingest(path,{"id":doc_id,"ticker":item["ticker"],"company":item["company"],"form":item["form"],"filing_date":item["filing_date"],"year":item["year"],"source":item["url"],"filename":item["filename"]})
            results.append(result)
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
