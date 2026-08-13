"""Publish pieces to Instagram (@pylonscape) through the official Graph API.

The API takes a public image URL, JPEG only. JPEG copies are hosted as assets on
the repo's `ig-posts` release (free, public; the repo itself keeps renders
untracked), so publishing is: pick a piece -> POST /media -> POST /media_publish.

**State lives in the feed, not in a file.** `--auto` reads the account's own posts,
parses the piece number out of each caption, and publishes the lowest-numbered
piece that has a hosted JPEG and has not been posted yet. Nothing to sync, nothing
to corrupt, and a re-run after a failure cannot double-post. When every hosted
piece has been published it exits quietly — the series stops until new work lands.

Secrets come from .env locally (gitignored) or the environment in CI:
IG_TOKEN, IG_USER_ID, IG_TOKEN_DATE (managed). The long-lived token lasts 60 days
and is refreshed automatically past REFRESH_AFTER_DAYS; under GitHub Actions the
refreshed value is written back to the repository secret.

Usage:
  python scripts/post_instagram.py --check         # token, quota, what's next
  python scripts/post_instagram.py --auto          # publish the next unposted piece
  python scripts/post_instagram.py --auto --dry-run  # rehearse: fetch + validate only
  python scripts/post_instagram.py --post 2        # publish one piece by number
  python scripts/post_instagram.py --upload        # convert + upload JPEGs (Windows)
  python scripts/post_instagram.py --refresh       # force a token refresh
"""

import argparse
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from export_instagram import caption as build_caption  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(ROOT, ".env")
SCENES = os.path.join(ROOT, "scenes", "pylon-series")
IG_DIR = os.path.join(ROOT, "outputs", "pylon-series", "instagram")
API = "https://graph.instagram.com/v23.0"
RELEASE_TAG = "ig-posts"
REFRESH_AFTER_DAYS = 40


# --- environment -----------------------------------------------------------

def load_env():
    env = {}
    if os.path.exists(ENV):
        with open(ENV, encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    env[k.strip()] = v.strip()
    for k in ("IG_TOKEN", "IG_USER_ID", "IG_TOKEN_DATE"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    if not env.get("IG_TOKEN") or not env.get("IG_USER_ID"):
        sys.exit("[post] need IG_TOKEN and IG_USER_ID (.env locally, secrets in CI)")
    return env


def set_secret(name, value):
    """`gh secret set`, with the value kept out of any failure message.

    check=True would raise CalledProcessError, whose text embeds the whole argv —
    token included — straight into a public repository's Actions log. A token minted
    seconds earlier by refresh_access_token is not yet a registered secret, so the
    runner's log masking would not catch it either."""
    r = subprocess.run(["gh", "secret", "set", name, "--body", value],
                       capture_output=True, text=True, cwd=ROOT)
    if r.returncode != 0:
        detail = (r.stderr or "").replace(value, "***").strip()[:200]
        sys.exit(f"[post] could not write the {name} secret (gh exit "
                 f"{r.returncode}): {detail}")


def save_env(env):
    """Persist the refreshed token: .env locally, the repo secret under Actions."""
    if os.environ.get("GITHUB_ACTIONS"):
        set_secret("IG_TOKEN", env["IG_TOKEN"])
        set_secret("IG_TOKEN_DATE", env["IG_TOKEN_DATE"])
        print("[post] repository secrets updated")
        return
    with open(ENV, "w", encoding="utf-8") as f:
        for k, v in env.items():
            f.write(f"{k}={v}\n")


# --- API -------------------------------------------------------------------

def api(method, path, **params):
    url = f"{API}/{path}"
    data = urllib.parse.urlencode(params).encode()
    if method == "GET":
        req = urllib.request.Request(url + "?" + data.decode())
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
    if not (force or age is None or age > REFRESH_AFTER_DAYS):
        return env
    r = api("GET", "refresh_access_token", grant_type="ig_refresh_token",
            access_token=env["IG_TOKEN"])
    env["IG_TOKEN"] = r["access_token"]
    env["IG_TOKEN_DATE"] = datetime.date.today().isoformat()
    save_env(env)
    print(f"[post] token refreshed (valid ~{r.get('expires_in', 0) // 86400} days)")
    return env


def posted_pieces(env):
    """Piece numbers already on the feed, read back out of the captions."""
    done, path = set(), f"{env['IG_USER_ID']}/media"
    params = {"fields": "caption", "limit": 100, "access_token": env["IG_TOKEN"]}
    url = f"{API}/{path}?" + urllib.parse.urlencode(params)
    while url:
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                page = json.load(r)
        except urllib.error.HTTPError as e:
            sys.exit(f"[post] could not read the feed: {e.read()[:300]}")
        for item in page.get("data", []):
            m = re.search(r"pylon-series (\d{3})", item.get("caption") or "")
            if m:
                done.add(int(m.group(1)))
        url = (page.get("paging") or {}).get("next")
    return done


# --- hosted images ---------------------------------------------------------

def repo_slug():
    return subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner",
                           "-q", ".nameWithOwner"], capture_output=True, text=True,
                          check=True, cwd=ROOT).stdout.strip()


def hosted_pieces():
    """Piece numbers with a JPEG asset on the release — the publishable inventory."""
    r = subprocess.run(["gh", "release", "view", RELEASE_TAG, "--json", "assets",
                        "-q", ".assets[].name"], capture_output=True, text=True,
                       cwd=ROOT)
    if r.returncode != 0:
        # An absent release means an empty inventory; anything else — no auth, no
        # network, rate limit — must not read as one, or --check reports "the series
        # has caught up" when it means "gh failed" and the operator stops looking.
        if "not found" in (r.stderr or "").lower():
            return set()
        sys.exit(f"[post] could not read the release inventory (gh exit "
                 f"{r.returncode}): {(r.stderr or '').strip()[:200]}")
    return {int(m.group(1)) for m in
            (re.fullmatch(r"(\d{3})\.jpg", n) for n in r.stdout.split()) if m}


def image_url(n):
    return (f"https://github.com/{repo_slug()}/releases/download/"
            f"{RELEASE_TAG}/{n:03d}.jpg")


def to_jpeg(n):
    """Full render PNG -> JPEG via PowerShell System.Drawing (Windows-only; the
    renders live on the workstation, so conversion belongs there, not in CI)."""
    src = os.path.join(ROOT, "outputs", "pylon-series", "full", f"{n:03d}.png")
    if not os.path.exists(src):
        return None
    out_dir = os.path.join(IG_DIR, "jpg")
    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, f"{n:03d}.jpg")
    ps = ("Add-Type -AssemblyName System.Drawing; "
          f"$i = [System.Drawing.Image]::FromFile('{src}'); "
          "$c = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | "
          "Where-Object { $_.MimeType -eq 'image/jpeg' }; "
          "$p = New-Object System.Drawing.Imaging.EncoderParameters(1); "
          "$p.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter("
          "[System.Drawing.Imaging.Encoder]::Quality, [long]92); "
          f"$i.Save('{dst}', $c, $p); $i.Dispose()")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(dst):
        sys.exit(f"[post] JPEG conversion failed for {n:03d}: "
                 f"{(r.stderr or r.stdout)[:300]}")
    return dst


def cmd_upload():
    """Convert every full render to JPEG and put it on the release."""
    tags = subprocess.run(["gh", "release", "list", "--json", "tagName",
                           "-q", ".[].tagName"], capture_output=True, text=True,
                          cwd=ROOT).stdout.split()
    if RELEASE_TAG not in tags:
        subprocess.run(["gh", "release", "create", RELEASE_TAG, "--latest=false",
                        "--title", "instagram posts", "--notes",
                        "JPEG copies serving as image_url sources for API "
                        "publishing. The specs remain the units of reproduction."],
                       check=True, capture_output=True, cwd=ROOT)
    have = hosted_pieces()
    todo = []
    for path in sorted(glob.glob(os.path.join(SCENES, "[0-9]*.json"))):
        n = int(os.path.basename(path)[:3])
        if n in have:
            continue
        jpg = to_jpeg(n)
        if jpg:
            todo.append(jpg)
        else:
            print(f"[post] no full render for {n:03d} — skipped")
    for i in range(0, len(todo), 10):
        subprocess.run(["gh", "release", "upload", RELEASE_TAG, *todo[i:i + 10],
                        "--clobber"], check=True, capture_output=True, cwd=ROOT)
    print(f"[post] uploaded {len(todo)} new JPEG(s); "
          f"{len(hosted_pieces())} pieces now hosted")


# --- publishing ------------------------------------------------------------

def caption_for(n):
    with open(os.path.join(SCENES, f"{n:03d}.json"), encoding="utf-8") as f:
        spec = json.load(f)
    # the ALT paragraph belongs to manual posting; the API has no alt field here
    return re.sub(r"\n*ALT:.*$", "", build_caption(n, spec), flags=re.DOTALL).strip()


def next_piece(env):
    available = hosted_pieces()
    if not available:
        sys.exit("[post] no hosted JPEGs — run --upload on the workstation first")
    remaining = sorted(available - posted_pieces(env))
    return remaining[0] if remaining else None


def publish(env, n, dry_run=False):
    url = image_url(n)
    print(f"[post] piece {n:03d} <- {url}")
    container = api("POST", f"{env['IG_USER_ID']}/media", image_url=url,
                    caption=caption_for(n), access_token=env["IG_TOKEN"])
    cid = container["id"]
    for _ in range(20):
        s = api("GET", cid, fields="status_code", access_token=env["IG_TOKEN"])
        code = s.get("status_code")
        if code == "FINISHED":
            break
        if code == "ERROR":
            sys.exit(f"[post] Instagram could not process the image: {s}")
        time.sleep(5)
    else:
        sys.exit("[post] container never finished processing")
    if dry_run:
        print(f"[post] DRY RUN — container {cid} validated and left unpublished "
              "(it expires in 24h)")
        return
    r = api("POST", f"{env['IG_USER_ID']}/media_publish", creation_id=cid,
            access_token=env["IG_TOKEN"])
    print(f"[post] published {n:03d} — media id {r['id']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--post", type=int)
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.upload:
        cmd_upload()
        return
    env = load_env()
    if args.refresh:
        maybe_refresh(env, force=True)
        return
    if args.check:
        me = api("GET", "me", fields="username,account_type",
                 access_token=env["IG_TOKEN"])
        quota = api("GET", f"{env['IG_USER_ID']}/content_publishing_limit",
                    access_token=env["IG_TOKEN"])
        used = (quota.get("data") or [{}])[0].get("quota_usage", "?")
        hosted, done = hosted_pieces(), posted_pieces(env)
        nxt = sorted(hosted - done)
        print(f"[post] @{me.get('username')} ({me.get('account_type')}) — "
              f"quota {used}/100 per 24h")
        print(f"[post] hosted {len(hosted)} · posted {len(done)} · "
              f"remaining {len(nxt)} · next "
              + (f"{nxt[0]:03d}" if nxt else "none (the series has caught up)"))
        return
    if args.auto or args.post is not None:
        env = maybe_refresh(env)
        n = args.post if args.post is not None else next_piece(env)
        if n is None:
            print("[post] nothing new to publish — every hosted piece is on the "
                  "feed. Sample and render a new batch to continue.")
            return
        publish(env, n, dry_run=args.dry_run)
        return
    ap.error("pass --check, --auto, --post N, --upload, or --refresh")


if __name__ == "__main__":
    main()
