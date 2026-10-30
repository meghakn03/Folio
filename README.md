# Folio — Financial Document Intelligence

Local-first search and analysis for SEC filings. Upload a PDF/HTML filing or fetch recent 10-K/10-Q reports by ticker, then search with source passages, inspect financial entities, and track the indexed corpus.

## Run locally

```bash
cp .env.example .env
# .env.example includes a personal contact email; edit it if needed.
docker compose up --build
```

Open http://localhost:5173. The API docs are at http://localhost:8000/docs. The first document ingestion downloads `all-MiniLM-L6-v2` from Hugging Face; use `EMBEDDING_MODEL=off` for TF-IDF based local retrieval without a model download. Data and indexes persist in `./data`.

## Capabilities

- SEC EDGAR submissions lookup and rate limited 10-K/10-Q filing download.
- PDF and HTML extraction, page-aware chunking, SQLite metadata and persisted embeddings.
- Semantic retrieval with Sentence Transformers, plus lexical score blending and citations.
- Financial entity patterns (currency, percentages, dates and named metrics) with optional spaCy NER.
- FastAPI endpoints for ingestion, listing, search, extraction and retrieval evaluation.
- Human-labeled retrieval benchmark with Precision@K, Recall@K, MRR, Hit Rate@K and mean latency.

SEC automated requests require a declared User-Agent and fair access. This is a personal project, so `SEC_USER_AGENT` identifies Folio and provides a monitored contact email; no organization is needed. Copy `.env.example` to `.env` before starting Docker. The downloader serializes SEC requests and applies a conservative delay.

## API

- `POST /documents/ingest` — multipart PDF/HTML upload
- `POST /documents/ingest/sec` — `{ "ticker": "AAPL", "forms": ["10-K", "10-Q"], "limit": 3 }`
- `GET /documents` and `GET /documents/{id}`
- `POST /search` — `{ "query": "What drove revenue growth?", "top_k": 5 }`
- `POST /entities/extract` — `{ "text": "..." }`
- `POST /evaluation/benchmarks` — save a relevant passage for the current question
- `GET /evaluation/benchmarks` and `POST /evaluation/run?top_k=5`
- `GET /health`

## Project layout

`backend/app` contains the API and pipeline; `frontend` contains the React UI. Uploaded documents, SQLite metadata, passage embeddings and evaluation labels live under `data/`. To evaluate retrieval in the UI, search a question and mark genuinely relevant passages, then run the benchmark. The metrics reflect your labeled examples, so label a varied set of questions and source passages before drawing conclusions.
