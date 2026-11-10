"""Opt-in YouTube uploader with an at-most-once attempt ledger on GitHub.

Upload outcomes after network errors can be unknown. A ledger is created BEFORE
upload, and failures require manual reconciliation rather than automatic retry.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request as HttpRequest, urlopen
from urllib.error import HTTPError


def github_api(method: str, path: str, data: dict | None = None):
    repo=os.environ["GITHUB_REPOSITORY"]
    token=os.environ["GITHUB_TOKEN"]
    url="https://api.github.com/repos/"+repo+"/contents/"+path
    headers={"Authorization":"Bearer "+token,"Accept":"application/vnd.github+json",
             "X-GitHub-Api-Version":"2022-11-28","User-Agent":"ShortsStudio"}
    payload=json.dumps(data).encode() if data is not None else None
    with urlopen(HttpRequest(url,data=payload,method=method,headers=headers),timeout=35) as r:
        return json.loads(r.read().decode())


def write_ledger(path: str, state: dict, sha: str | None = None):
    content=base64.b64encode(json.dumps(state).encode()).decode()
    payload={"message":"Record Shorts Studio publish attempt","content":content,
             "branch":os.environ.get("GITHUB_REF_NAME","main")}
    if sha:
        payload["sha"]=sha
    return github_api("PUT",path,payload)


def main():
    if len(sys.argv)!=3:
        raise SystemExit("Usage: cloud_publish.py video.mp4 metadata.json")
    video=Path(sys.argv[1])
    meta=json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    privacy=os.getenv("SHORTS_PRIVACY","private")
    if privacy not in ("private","unlisted","public"):
        raise ValueError("Unsupported YouTube visibility")
    oauth_json=os.getenv("YOUTUBE_OAUTH_JSON","")
    if not oauth_json:
        raise RuntimeError("Configure the YOUTUBE_OAUTH_JSON GitHub secret first")
    day=datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path="shorts-studio/ledger/"+day+".json"
    try:
        existing=github_api("GET",path)
        raise RuntimeError("A publish attempt already exists. Inspect this receipt: "+str(existing.get("html_url","")))
    except HTTPError as exc:
        if exc.code!=404:
            raise
    state={"status":"upload_started","created_at":datetime.now(timezone.utc).isoformat(),
           "plan_title":meta["plan"]["title"],"privacy":privacy}
    receipt=write_ledger(path,state)
    sha=receipt["content"]["sha"]
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        scopes=["https://www.googleapis.com/auth/youtube.upload"]
        credentials=Credentials.from_authorized_user_info(json.loads(oauth_json),scopes)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        if not credentials.valid:
            raise RuntimeError("YouTube OAuth token needs reconnection")
        youtube=build("youtube","v3",credentials=credentials,cache_discovery=False)
        plan=meta["plan"]
        request=youtube.videos().insert(part="snippet,status",body={
            "snippet":{"title":plan["title"][:100],
                       "description":(plan.get("description","")+"\n#Shorts")[:4900],
                       "categoryId":"27"},
            "status":{"privacyStatus":privacy,"selfDeclaredMadeForKids":False},
        },media_body=MediaFileUpload(str(video),mimetype="video/mp4",resumable=True))
        result=None
        while result is None:
            _,result=request.next_chunk()
        state["status"]="posted"
        state["url"]="https://www.youtube.com/shorts/"+result["id"]
        write_ledger(path,state,sha)
        print("Published "+state["url"])
    except Exception:
        print("Upload outcome unknown: check YouTube Studio and the GitHub receipt.",
              file=sys.stderr)
        raise


if __name__=="__main__":
    main()
