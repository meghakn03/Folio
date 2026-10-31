import json
import sqlite3
from contextlib import contextmanager
from .config import DB_PATH

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, ticker TEXT, company TEXT, form TEXT,
          filing_date TEXT, year TEXT, source TEXT, filename TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY, document_id TEXT, page INTEGER, section TEXT,
          text TEXT, embedding TEXT, FOREIGN KEY(document_id) REFERENCES documents(id));
        CREATE INDEX IF NOT EXISTS chunks_document_id ON chunks(document_id);
        CREATE TABLE IF NOT EXISTS benchmark_items(id INTEGER PRIMARY KEY AUTOINCREMENT, query TEXT NOT NULL,
          relevant_chunk_ids TEXT NOT NULL, ticker TEXT, form TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(query,ticker,form));
        CREATE TABLE IF NOT EXISTS ingestion_events(id INTEGER PRIMARY KEY AUTOINCREMENT, document_id TEXT,
          source TEXT, filename TEXT, input_hash TEXT, stage TEXT NOT NULL, status TEXT NOT NULL,
          attempt INTEGER NOT NULL DEFAULT 1, chunk_count INTEGER, quality_json TEXT, error TEXT,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS ingestion_events_created_at ON ingestion_events(created_at DESC);
        """)
        columns={row["name"] for row in c.execute("PRAGMA table_info(documents)")}
        migrations={"content_hash":"TEXT","ingest_status":"TEXT DEFAULT 'complete'","quality_status":"TEXT","quality_json":"TEXT","processing_attempts":"INTEGER DEFAULT 0","last_ingested_at":"TEXT"}
        for name,definition in migrations.items():
            if name not in columns: c.execute(f"ALTER TABLE documents ADD COLUMN {name} {definition}")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS documents_content_hash ON documents(content_hash) WHERE content_hash IS NOT NULL")

def add_document(doc, chunks):
    with db() as c:
        c.execute("INSERT OR IGNORE INTO documents(id,ticker,company,form,filing_date,year,source,filename,content_hash,ingest_status,quality_status,quality_json,processing_attempts,last_ingested_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
          (doc["id"],doc.get("ticker"),doc.get("company"),doc.get("form"),doc.get("filing_date"),doc.get("year"),doc.get("source"),doc.get("filename"),doc.get("content_hash"),"complete",doc.get("quality_status"),json.dumps(doc.get("quality")),doc.get("processing_attempts",1)))
        for ch in chunks:
            c.execute("INSERT OR IGNORE INTO chunks(id,document_id,page,section,text,embedding) VALUES(?,?,?,?,?,?)",
              (ch["id"],doc["id"],ch.get("page"),ch.get("section"),ch["text"],json.dumps(ch.get("embedding"))))

def documents():
    with db() as c:
        rows=c.execute("SELECT d.*, COUNT(ch.id) chunk_count FROM documents d LEFT JOIN chunks ch ON ch.document_id=d.id GROUP BY d.id ORDER BY d.created_at DESC").fetchall()
        return [dict(r) for r in rows]

def get_document(doc_id):
    with db() as c:
        row=c.execute("SELECT * FROM documents WHERE id=?",(doc_id,)).fetchone()
        if not row: return None
        doc=dict(row)
        doc["chunks"]=[dict(r) for r in c.execute("SELECT id,page,section,text FROM chunks WHERE document_id=?",(doc_id,))]
        return doc

def all_chunks():
    with db() as c:
        return [dict(r) for r in c.execute("SELECT ch.*,d.ticker,d.company,d.form,d.filing_date,d.year,d.source,d.filename FROM chunks ch JOIN documents d ON d.id=ch.document_id")]


def add_benchmark_item(query, chunk_id, ticker=None, form=None):
    with db() as c:
        row=c.execute("SELECT id,relevant_chunk_ids FROM benchmark_items WHERE query=? AND ticker IS ? AND form IS ?",(query,ticker,form)).fetchone()
        if row:
            ids=json.loads(row["relevant_chunk_ids"])
            if chunk_id not in ids: ids.append(chunk_id)
            c.execute("UPDATE benchmark_items SET relevant_chunk_ids=? WHERE id=?",(json.dumps(ids),row["id"]))
            return row["id"]
        cursor=c.execute("INSERT INTO benchmark_items(query,relevant_chunk_ids,ticker,form) VALUES(?,?,?,?)",(query,json.dumps([chunk_id]),ticker,form))
        return cursor.lastrowid

def benchmark_items():
    with db() as c:
        return [{**dict(r),"relevant_chunk_ids":json.loads(r["relevant_chunk_ids"])} for r in c.execute("SELECT * FROM benchmark_items ORDER BY created_at DESC,id DESC")]


def find_document_by_hash(content_hash):
    with db() as c:
        row=c.execute("SELECT * FROM documents WHERE content_hash=?",(content_hash,)).fetchone()
        return dict(row) if row else None

def log_ingestion_event(document_id, source, filename, input_hash, stage, status, attempt=1, chunk_count=None, quality=None, error=None):
    with db() as c:
        c.execute("INSERT INTO ingestion_events(document_id,source,filename,input_hash,stage,status,attempt,chunk_count,quality_json,error) VALUES(?,?,?,?,?,?,?,?,?,?)",
          (document_id,source,filename,input_hash,stage,status,attempt,chunk_count,json.dumps(quality) if quality is not None else None,error[:1500] if error else None))

def data_health(limit=40):
    with db() as c:
        totals=dict(c.execute("SELECT COUNT(*) documents, COALESCE(SUM((SELECT COUNT(*) FROM chunks ch WHERE ch.document_id=documents.id)),0) chunks, SUM(CASE WHEN quality_status='warning' THEN 1 ELSE 0 END) warning_documents FROM documents").fetchone())
        totals["warning_documents"]=totals.get("warning_documents") or 0
        totals["failed_attempts"]=c.execute("SELECT COUNT(*) FROM ingestion_events WHERE status='failed'").fetchone()[0]
        totals["retry_events"]=c.execute("SELECT COUNT(*) FROM ingestion_events WHERE status='retrying'").fetchone()[0]
        totals["duplicate_skips"]=c.execute("SELECT COUNT(*) FROM ingestion_events WHERE status='duplicate'").fetchone()[0]
        rows=c.execute("SELECT * FROM ingestion_events ORDER BY id DESC LIMIT ?",(limit,)).fetchall()
        events=[]
        for row in rows:
            item=dict(row)
            item["quality"]=json.loads(item.pop("quality_json")) if item.get("quality_json") else None
            events.append(item)
        return {**totals,"recent_events":events}
