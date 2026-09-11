#!/usr/bin/env python3
"""mdapi_retrieve.py — retrieve metadata components via the SOAP Metadata API
retrieve() call using a live access token (sandbox-safe; never touches ~/.sfdx).
Writes the unzipped members under --out-dir (mdapi layout).

Usage:
  python scripts/mdapi_retrieve.py --auth-json .build/orgauth.json \
      --type CustomObject --members TI_Fnt_Foo__c,TI_Fnt_Bar__c --out-dir .build/retrieve
"""
from __future__ import annotations
import argparse, base64, io, json, os, sys, time, urllib.request, urllib.error, zipfile, html

ENV = "http://schemas.xmlsoap.org/soap/envelope/"
MET = "http://soap.sforce.com/2006/04/metadata"


def _post(inst, ver, tok, body):
    env = (f'<soapenv:Envelope xmlns:soapenv="{ENV}" xmlns:met="{MET}">'
           f'<soapenv:Header><met:SessionHeader><met:sessionId>{tok}</met:sessionId>'
           f'</met:SessionHeader></soapenv:Header><soapenv:Body>{body}</soapenv:Body></soapenv:Envelope>')
    req = urllib.request.Request(f"{inst}/services/Soap/m/{ver}", data=env.encode(),
                                 headers={"Content-Type": "text/xml; charset=UTF-8", "SOAPAction": '""'})
    try:
        return urllib.request.urlopen(req, timeout=180).read().decode()
    except urllib.error.HTTPError as e:
        sys.exit(f"❌ HTTP {e.code}: {e.read().decode()[:400]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--auth-json", default=".build/orgauth.json")
    ap.add_argument("--type", required=True)
    ap.add_argument("--members", required=True, help="comma-separated fullNames")
    ap.add_argument("--out-dir", default=".build/retrieve")
    a = ap.parse_args()
    auth = json.load(open(a.auth_json))["result"]
    inst = auth["instanceUrl"].rstrip("/"); tok = auth["accessToken"]; ver = auth["apiVersion"]

    mems = "".join(f"<met:members>{m.strip()}</met:members>" for m in a.members.split(","))
    body = (f'<met:retrieve><met:retrieveRequest><met:apiVersion>{ver}</met:apiVersion>'
            f'<met:singlePackage>true</met:singlePackage><met:unpackaged>'
            f'<met:types>{mems}<met:name>{a.type}</met:name></met:types>'
            f'<met:version>{ver}</met:version></met:unpackaged></met:retrieveRequest></met:retrieve>')
    resp = _post(inst, ver, tok, body)
    aid = resp.split("<id>", 1)[1].split("</id>", 1)[0]
    print(f"retrieve async id {aid} — polling…")
    while True:
        time.sleep(3)
        st = _post(inst, ver, tok, f'<met:checkRetrieveStatus><met:asyncProcessId>{aid}</met:asyncProcessId>'
                                   f'<met:includeZip>true</met:includeZip></met:checkRetrieveStatus>')
        if "<done>true</done>" in st:
            break
        print("  … InProgress")
    if "<zipFile>" not in st:
        sys.exit(f"❌ no zipFile in response: {st[:400]}")
    zdata = st.split("<zipFile>", 1)[1].split("</zipFile>", 1)[0]
    zf = zipfile.ZipFile(io.BytesIO(base64.b64decode(zdata)))
    os.makedirs(a.out_dir, exist_ok=True)
    for n in zf.namelist():
        dest = os.path.join(a.out_dir, n)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(zf.read(n))
        print(f"  wrote {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
