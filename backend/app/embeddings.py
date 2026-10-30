import json
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from .config import EMBEDDING_MODEL

_model=None
def vectors(texts):
    global _model
    if EMBEDDING_MODEL.lower() in {"off","none","tfidf"}: return [None]*len(texts)
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model=SentenceTransformer(EMBEDDING_MODEL)
    return _model.encode(texts,normalize_embeddings=True).tolist()

def rank(query, chunks, top_k):
    if not chunks: return []
    docs=[x["text"] for x in chunks]
    lexical=TfidfVectorizer(stop_words="english",ngram_range=(1,2),max_features=100000).fit_transform(docs+[query])
    lex=cosine_similarity(lexical[-1],lexical[:-1]).ravel()
    semantic=np.zeros(len(chunks))
    if EMBEDDING_MODEL.lower() not in {"off","none","tfidf"}:
        try:
            q=vectors([query])[0]
            eligible=[]
            for i,x in enumerate(chunks):
                emb=json.loads(x["embedding"] or "null")
                if emb is not None: eligible.append((i,emb))
            if eligible:
                import faiss
                matrix=np.asarray([item[1] for item in eligible],dtype="float32")
                index=faiss.IndexFlatIP(matrix.shape[1])
                index.add(matrix)
                similarities,indices=index.search(np.asarray([q],dtype="float32"),len(eligible))
                for similarity,position in zip(similarities[0],indices[0]):
                    semantic[eligible[int(position)][0]]=float(similarity)
        except Exception:
            semantic=lex
    scores=.62*semantic+.38*lex if semantic.max()>0 else lex
    order=np.argsort(scores)[::-1][:top_k]
    return [(chunks[int(i)],float(scores[int(i)])) for i in order if scores[int(i)]>0]
