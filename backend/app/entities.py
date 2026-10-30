import re

PATTERNS={
 "monetary_value": re.compile(r"(?i)(?:US\$|\$|€|£)\s?\(?\s?\d[\d,]*(?:\.\d+)?\s?(?:billion|million|thousand|bn|mm|m|k)?\)?"),
 "percentage": re.compile(r"\b\d+(?:\.\d+)?\s?%\b"),
 "date": re.compile(r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}\b|\b\d{4}-\d{2}-\d{2}\b"),
 "financial_metric": re.compile(r"(?i)\b(?:revenue|net income|operating income|operating expenses?|gross profit|cash flow|total assets|total debt|long-term debt|earnings per share|EBITDA|capital expenditures?|research and development)\b"),
}
def extract(text):
    out=[]
    for kind,pat in PATTERNS.items():
        for m in pat.finditer(text): out.append({"type":kind,"value":m.group(0),"start":m.start(),"end":m.end()})
    try:
        import spacy
        nlp=spacy.load("en_core_web_sm")
        for ent in nlp(text[:100000]).ents:
            if ent.label_ in {"ORG","PERSON","GPE"}: out.append({"type":ent.label_.lower(),"value":ent.text,"start":ent.start_char,"end":ent.end_char})
    except (ImportError,OSError): pass
    return sorted(out,key=lambda x:x["start"])
