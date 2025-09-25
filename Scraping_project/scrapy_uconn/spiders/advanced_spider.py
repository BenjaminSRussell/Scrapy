import json, hashlib, re, os, resource
from pathlib import Path
from collections import Counter
from urllib.parse import urlparse

import scrapy, spacy
from scrapy.crawler import CrawlerProcess
from scrapy.linkextractors import LinkExtractor
from w3lib.url import canonicalize_url

# ───────────────── CONFIG ────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent
DATA_FILE  = Path("uconn_data.jsonl")

CONCURRENT = 128          # safe default under macOS ulimit 256
TIMEOUT    = 6
DNS_TIMEOUT= 3
MAX_TXT    = 20_000
TOP_KW     = 15
ZERO_LIM   = 0.02         # abort if >2 % zero-byte bodies
AUDIO_RE   = re.compile(r"\.(mp3|wav|ogg|flac)(\?.*)?$", re.I)

TAGS = {
    "admissions","about","research","students","faculty","staff","alumni","athletics","covid",
    "graduate","undergraduate","catalog","courses","registrar","financial-aid","scholarships",
    "library","majors","minors","housing","dining","parking","sustainability","environment",
    "international","global","diversity","inclusion","policy","news","events","calendar","maps",
    "careers","jobs","wellness","health","engineering","law","medicine","business","nursing",
    "education","pharmacy","fine-arts","humanities","psychology","biology","chemistry","physics",
    "geosciences","data","ai","robotics","institute","center",
}

# raise open-file soft limit if possible
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
if soft < 4096:
    try: resource.setrlimit(resource.RLIMIT_NOFILE, (min(4096, hard), hard))
    except Exception: pass

# ───────────────── NLP helpers ───────────────────────────────────────────
NLP            = spacy.load("en_core_web_sm", disable=["lemmatizer","parser"])
ENTITY_LABELS  = set(NLP.pipe_labels["ner"])
sha            = lambda u: hashlib.sha1(u.encode()).hexdigest()

def ents_kws(text: str):
    doc  = NLP(text[:MAX_TXT])
    ents = {e.text.strip() for e in doc.ents if e.label_ in ENTITY_LABELS}
    lem  = [t.lemma_.lower() for t in doc if t.is_alpha and not t.is_stop]
    kws  = [w for w,_ in Counter(lem).most_common(TOP_KW)]
    return sorted(ents), kws

# ───────────────── Load existing JSONL ───────────────────────────────────
GOOD, BAD, CACHE = set(), set(), {}
if DATA_FILE.exists():
    for ln in DATA_FILE.open(encoding="utf-8"):
        try: row = json.loads(ln)
        except Exception: continue
        CACHE[row["url_hash"]] = row
        (GOOD if row.get("status")==200 and row.get("word_count",0)>0 and "text/html" in row.get("content_type","")
         else BAD).add(row["url_hash"])

# ───────────────── Pipeline ──────────────────────────────────────────────
class WritePipe:
    def open_spider(self, sp):
        self.zero = 0
        self.log  = DATA_FILE.open("a", encoding="utf-8")
    def close_spider(self, sp):
        with DATA_FILE.open("w", encoding="utf-8") as f:
            for v in CACHE.values():
                f.write(json.dumps(v, ensure_ascii=False) + "\n")
        self.log.close()
        sp.logger.info(f"[close] wrote {len(CACHE):,} rows → {DATA_FILE}")
    def process_item(self, it, sp):
        def good(i): return i["status"]==200 and i["word_count"]>0 and "text/html" in i["content_type"]
        if not good(it):
            CACHE.setdefault(it["url_hash"], it)
            return it
        if it["size_bytes"]==0:
            self.zero += 1
            if self.zero/ max(1,len(CACHE)) > ZERO_LIM:
                sp.crawler.engine.close_spider(sp, "too_many_zero_byte")
            return it
        self.log.write(json.dumps(it, ensure_ascii=False)+"\n")
        CACHE[it["url_hash"]] = it
        GOOD.add(it["url_hash"])
        return it

class UConnSpider(scrapy.Spider):
    name = "uconn"
    allowed_domains = ["uconn.edu"]
    custom_settings = {
        "CONCURRENT_REQUESTS"          : CONCURRENT,
        "CONCURRENT_REQUESTS_PER_IP"   : CONCURRENT,
        "DOWNLOAD_TIMEOUT"             : TIMEOUT,
        "DNS_TIMEOUT"                  : DNS_TIMEOUT,
        "RETRY_ENABLED"                : False,
        "ROBOTSTXT_OBEY"               : False,
        "USER_AGENT"                   : "UConnAI-One/0.4",
        "LOG_LEVEL"                    : "INFO",
        "HTTPERROR_ALLOW_ALL"          : True,
        "ITEM_PIPELINES"               : {"__main__.WritePipe": 300},
    }

    def start_requests(self):
        if BAD:
            for hsh in BAD:
                if (row:=CACHE.get(hsh)): yield scrapy.Request(row["url"], self.parse)
        else:
            yield scrapy.Request("https://uconn.edu/", self.parse)

    def parse(self, resp):
        ctype = resp.headers.get("Content-Type",b"").decode("utf-8","ignore").lower()
        txt=""; wc=0; ents=[]; kws=[]; pdf=audio=False
        if "text/html" in ctype:
            txt = " ".join(resp.xpath("//body//text()[normalize-space()]").getall())
            wc  = len(txt.split())
            if wc: ents,kws = ents_kws(txt)
            le = LinkExtractor(allow_domains=self.allowed_domains, unique=True,
                               deny_extensions=["jpg","jpeg","png","gif","zip","exe","dmg","mp4","css","js","svg","ico","woff","ttf","pdf","mp3","wav","ogg","flac"])
            links = le.extract_links(resp)
            for ln in links:
                canon = canonicalize_url(ln.url)
                hsh = sha(canon)
                if hsh not in GOOD:
                    GOOD.add(hsh)
                    yield resp.follow(canon, self.parse)
            pdf   = any(l.url.lower().endswith(".pdf") for l in links)
            audio = any(AUDIO_RE.search(l.url) for l in links) or bool(resp.xpath("//audio").get())

        url_norm = canonicalize_url(resp.url)
        yield {
            "url"          : url_norm,
            "url_hash"     : sha(url_norm),
            "status"       : resp.status,
            "content_type" : ctype,
            "size_bytes"   : len(resp.body),
            "word_count"   : wc,
            "pdf_present"  : pdf,
            "audio_present": audio,
            "entities"     : ents,
            "keywords"     : kws,
            "tags"         : sorted({p for p in urlparse(url_norm).path.lower().split('/') if p in TAGS}),
        }

# ───────────────── Runner ────────────────────────────────────────────────
if __name__ == "__main__":
    # ensure model present
    try: _=NLP("test")
    except OSError:
        print("Downloading spaCy model…"); os.system("python -m spacy download en_core_web_sm")
    process = CrawlerProcess()
    process.crawl(UConnSpider)
    process.start()