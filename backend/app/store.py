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
        """)

def add_document(doc, chunks):
    with db() as c:
        c.execute("INSERT OR IGNORE INTO documents(id,ticker,company,form,filing_date,year,source,filename) VALUES(?,?,?,?,?,?,?,?)",
          (doc["id"],doc.get("ticker"),doc.get("company"),doc.get("form"),doc.get("filing_date"),doc.get("year"),doc.get("source"),doc.get("filename")))
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
