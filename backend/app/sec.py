import re, time, threading
from urllib.parse import quote
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from .config import SEC_USER_AGENT
from .store import log_ingestion_event

BASE="https://www.sec.gov"
DATA="https://data.sec.gov"
_last=0.0
_request_lock=threading.Lock()
_session=requests.Session()
_retry=Retry(total=5,connect=5,read=5,status=5,backoff_factor=0.7,status_forcelist=(429,500,502,503,504),allowed_methods=frozenset({"GET"}),respect_retry_after_header=True)
_session.mount("https://",HTTPAdapter(max_retries=_retry))

def _get(url):
    global _last
    if "example.com" in SEC_USER_AGENT or not SEC_USER_AGENT.strip():
        raise ValueError("Configure SEC_USER_AGENT in .env with a project name and monitored contact email before downloading.")
    with _request_lock:
        delay=.12-(time.monotonic()-_last)
        if delay>0: time.sleep(delay)
        response=_session.get(url,headers={"User-Agent":SEC_USER_AGENT,"Accept-Encoding":"gzip, deflate"},timeout=(10,90))
        _last=time.monotonic()
    retry_state=getattr(getattr(response,"raw",None),"retries",None)
    for index,retry in enumerate(getattr(retry_state,"history",()),start=1):
        log_ingestion_event(None,url,None,None,"sec_http","retrying",attempt=index,error=f"Retried SEC request after status {retry.status or 'connection error'}")
    response.raise_for_status()
    return response

def find_cik(ticker):
    data=_get(f"{BASE}/files/company_tickers.json").json()
    ticker=ticker.upper()
    for item in data.values():
        if item["ticker"].upper()==ticker: return str(item["cik_str"]).zfill(10),item["title"]
    raise ValueError(f"Ticker {ticker} was not found in SEC company tickers.")

def recent_filings(ticker, forms, limit):
    cik,company=find_cik(ticker)
    data=_get(f"{DATA}/submissions/CIK{cik}.json").json()
    recent=data["filings"]["recent"]
    entries=[]
    for i,form in enumerate(recent["form"]):
        if form not in forms: continue
        acc=recent["accessionNumber"][i]
        accession=acc.replace("-","")
        primary=recent["primaryDocument"][i]
        url=f"{BASE}/Archives/edgar/data/{int(cik)}/{accession}/{quote(primary)}"
        entries.append({"cik":cik,"ticker":ticker.upper(),"company":company,"form":form,"filing_date":recent["filingDate"][i],"year":recent["reportDate"][i][:4] if recent["reportDate"][i] else None,"accession":acc,"url":url,"filename":primary})
        if len(entries)>=limit: break
    return entries

def download(url): return _get(url).content
