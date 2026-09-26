"""Check which company-list and price sources are reachable from this machine.

    python pipeline/probe_sources.py
"""
import io
import sys
import zipfile
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
from nsesig.data import HEADERS  # noqa: E402

BSE_HEADERS = {**HEADERS, "Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com"}
DAY = "20260925"

SOURCES = [
    ("NSE main-board list", "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv", HEADERS),
    ("NSE SME list", "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv", HEADERS),
    ("NSE bhavcopy (all series)", "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_25092026.csv", HEADERS),
    ("NSE UDiFF bhavcopy", f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{DAY}_F_0000.csv.zip", HEADERS),
    ("BSE UDiFF bhavcopy", f"https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{DAY}_F_0000.CSV", BSE_HEADERS),
    ("BSE scrips (active)", "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?Group=&Scripcode=&industry=&segment=Equity&status=Active", BSE_HEADERS),
    ("BSE scrips (suspended)", "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?Group=&Scripcode=&industry=&segment=Equity&status=Suspended", BSE_HEADERS),
]


def text(content: bytes) -> str:
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            content = z.read(z.namelist()[0])
    return content.decode("utf-8", errors="replace")


s = requests.Session()
for host in ("https://www.nseindia.com", "https://www.bseindia.com"):
    try:
        print(host, s.get(host, headers=HEADERS, timeout=20).status_code)
    except Exception as e:
        print(host, "failed:", e)

for name, url, headers in SOURCES:
    print(f"\n=== {name}\n{url}")
    try:
        r = s.get(url, headers=headers, timeout=60)
        print("status", r.status_code, "bytes", len(r.content), "type", r.headers.get("content-type"))
        if r.status_code != 200:
            continue
        body = text(r.content)
        if body.lstrip().startswith(("[", "{")):
            data = r.json()
            rows = data if isinstance(data, list) else data.get("Table", data)
            df = pd.DataFrame(rows)
        else:
            df = pd.read_csv(io.StringIO(body), skipinitialspace=True, low_memory=False)
        df.columns = [str(c).strip() for c in df.columns]
        print("rows", len(df), "columns", list(df.columns)[:30])
        print(df.head(3).to_string()[:1500])
        for col in ("SERIES", "SctySrs", "Status", "Segment", "GROUP", "Group"):
            if col in df.columns:
                print(f"{col} counts:", df[col].astype(str).str.strip().value_counts().head(25).to_dict())
    except Exception as e:
        print("failed:", repr(e))
