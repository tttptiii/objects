"""Publish pieces to Instagram (@pylonscape) through the official Graph API.

The API takes a public image URL, JPEG only. So a post is: full render (PNG) ->
JPEG (PowerShell System.Drawing — this pipeline runs on Windows) -> GitHub release
asset on the public repo (free hosting; the repo itself keeps renders untracked) ->
POST /media -> POST /media_publish -> check the piece off in queue.md.

Secrets live in .env (gitignored): IG_TOKEN, IG_USER_ID, IG_TOKEN_DATE (managed).
The long-lived token lasts 60 days; any run refreshes it automatically once it is
over 40 days old.

Usage:
  python scripts/post_instagram.py --check       # token + publishing quota
  python scripts/post_instagram.py --post 2      # publish piece 002
  python scripts/post_instagram.py --next        # publish first unchecked in queue.md
  python scripts/post_instagram.py --refresh     # force token refresh
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(ROOT, ".env")
IG_DIR = os.path.join(ROOT, "outputs", "pylon-series", "instagram")
API = "https://graph.instagram.com/v23.0"
RELEASE_TAG = "ig-posts"
REFRESH_AFTER_DAYS = 40


def load_env():
    env = {}
    with open(ENV, encoding="utf-8") as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                env[k.strip()] = v.strip()
    if not env.get("IG_TOKEN") or not env.get("IG_USER_ID"):
        sys.exit("[post] .env needs IG_TOKEN and IG_USER_ID")
    return env


def save_env(env):
    with open(ENV, "w", encoding="utf-8") as f:
        for k, v in env.items():
            f.write(f"{k}={v}\n")


def api(method, path, **params):
    url = f"{API}/{path}"
    data = urllib.parse.urlencode(params).encode()
    if method == "GET":
        url += "?" + data.decode()
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        sys.exit(f"[post] API error {e.code} on {path}: {body}")


def maybe_refresh(env, force=False):
    stamp = env.get("IG_TOKEN_DATE")
    age = ((datetime.date.today() - datetime.date.fromisoformat(stamp)).days
           if stamp else None)
    if force or age is None or age > REFRESH_AFTER_DAYS:
        r = api("GET", "refresh_access_token", grant_type="ig_refresh_token",
                access_token=env["IG_TOKEN"])
        env["IG_TOKEN"] = r["access_token"]
        env["IG_TOKEN_DATE"] = datetime.date.today().isoformat()
        save_env(env)
        print(f"[post] token refreshed (valid ~{r.get('expires_in', 0) // 86400} days)")
    return env


def to_jpeg(n):
    """Full render PNG -> JPEG via PowerShell System.Drawing (no extra deps)."""
    src = os.path.join(ROOT, "outputs", "pylon-series", "full", f"{n:03d}.png")
    if not os.path.exists(src):
        sys.exit(f"[post] no full render for {n:03d} — "
                 f"python scripts/render_pylons.py --pieces {n} --full")
    out_dir = os.path.join(IG_DIR, "jpg")
    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, f"{n:03d}.jpg")
    ps = (
        "Add-Type -AssemblyName System.Drawing; "
        f"$img = [System.Drawing.Image]::FromFile('{src}'); "
        "$codec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() "
        "| Where-Object { $_.MimeType -eq 'image/jpeg' }; "
        "$p = New-Object System.Drawing.Imaging.EncoderParameters(1); "
        "$p.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter("
        "[System.Drawing.Imaging.Encoder]::Quality, [long]92); "
        f"$img.Save('{dst}', $codec, $p); $img.Dispose()"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(dst):
        sys.exit(f"[post] JPEG conversion failed: {(r.stderr or r.stdout)[:300]}")
    return dst


def host(jpg_path):
    """Upload as a release asset on the public repo; returns the public URL."""
    tags = subprocess.run(["gh", "release", "list", "--json", "tagName",
                           "-q", ".[].tagName"], capture_output=True, text=True,
                          cwd=ROOT).stdout.split()
    if RELEASE_TAG not in tags:
        subprocess.run(["gh", "release", "create", RELEASE_TAG,
                        "--title", "instagram posts",
                        "--notes", "JPEG copies serving as image_url sources for "
                        "API publishing. The images are the pieces; specs remain "
                        "the units of reproduction.", "--latest=false"],
                       check=True, capture_output=True, cwd=ROOT)
    subprocess.run(["gh", "release", "upload", RELEASE_TAG, jpg_path, "--clobber"],
                   check=True, capture_output=True, cwd=ROOT)
    repo = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner",
                           "-q", ".nameWithOwner"], capture_output=True, text=True,
                          cwd=ROOT).stdout.strip()
    name = os.path.basename(jpg_path)
    return f"https://github.com/{repo}/releases/download/{RELEASE_TAG}/{name}"


def caption_for(n):
    path = os.path.join(IG_DIR, f"{n:03d}.txt")
    if not os.path.exists(path):
        sys.exit(f"[post] no caption file for {n:03d} — run scripts/export_instagram.py")
    text = open(path, encoding="utf-8").read()
    # the ALT paragraph is for manual posting; the API has no alt-text field here
    return re.sub(r"\n*ALT:.*$", "", text, flags=re.DOTALL).strip()


def next_unposted():
    queue = os.path.join(IG_DIR, "queue.md")
    for line in open(queue, encoding="utf-8"):
        m = re.match(r"- \[ \] (\d{3})", line)
        if m:
            return int(m.group(1))
    sys.exit("[post] queue.md has no unposted pieces")


def check_off(n):
    queue = os.path.join(IG_DIR, "queue.md")
    text = open(queue, encoding="utf-8").read()
    text = text.replace(f"- [ ] {n:03d}", f"- [x] {n:03d}", 1)
    open(queue, "w", encoding="utf-8").write(text)


def publish(env, n):
    jpg = to_jpeg(n)
    url = host(jpg)
    caption = caption_for(n)
    print(f"[post] hosting: {url}")
    container = api("POST", f"{env['IG_USER_ID']}/media",
                    image_url=url, caption=caption, access_token=env["IG_TOKEN"])
    cid = container["id"]
    for _ in range(12):
        s = api("GET", cid, fields="status_code", access_token=env["IG_TOKEN"])
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") == "ERROR":
            sys.exit(f"[post] container failed: {s}")
        time.sleep(5)
    r = api("POST", f"{env['IG_USER_ID']}/media_publish",
            creation_id=cid, access_token=env["IG_TOKEN"])
    check_off(n)
    print(f"[post] published {n:03d} — media id {r['id']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--post", type=int)
    ap.add_argument("--next", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    env = load_env()
    if args.refresh:
        maybe_refresh(env, force=True)
        return
    if args.check:
        me = api("GET", "me", fields="user_id,username,account_type",
                 access_token=env["IG_TOKEN"])
        quota = api("GET", f"{env['IG_USER_ID']}/content_publishing_limit",
                    access_token=env["IG_TOKEN"])
        used = quota.get("data", [{}])[0].get("quota_usage", "?")
        print(f"[post] token OK: @{me.get('username')} ({me.get('account_type')}) "
              f"— api id {me.get('user_id')} / env id {env['IG_USER_ID']} — "
              f"quota used {used}/100 per 24h")
        return
    if args.post is not None or args.next:
        env = maybe_refresh(env)
        publish(env, args.post if args.post is not None else next_unposted())
        return
    ap.error("pass --check, --post N, --next, or --refresh")


if __name__ == "__main__":
    main()
