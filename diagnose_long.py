"""指定された8件の横動画について、フィルタ通過の可否と未取得理由を診断する"""
import os
import sqlite3
import sys
import io
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from googleapiclient.discovery import build

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

load_dotenv()
youtube = build("youtube", "v3", developerKey=os.getenv("YOUTUBE_API_KEY"))

# main_long.py の現行フィルタ条件（2026-04-28 改修1〜4 反映後）
MIN_SUBSCRIBERS = 500
MAX_SUBSCRIBERS = 100_000
VIEW_MULTIPLIER = 0.3        # 改修4: 0.5→0.3
MIN_COMMENTS = 5
MIN_DURATION_SEC = 4 * 60
MAX_DURATION_SEC = 30 * 60
SEARCH_DAYS = 14             # 改修3: 7→14
NG_KEYWORDS = [
    "切り抜き", "まとめ", "速報", "手書き", "反応",
    "ホロライブ", "hololive", "にじさんじ", "nijisanji",
    "ぶいすぽ", "ネオポルテ",
]


def parse_duration(s):
    import re
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m: return 0
    return int(m.group(1) or 0)*3600 + int(m.group(2) or 0)*60 + int(m.group(3) or 0)


def has_kana(s):
    if not s: return False
    return any("぀" <= ch <= "ゟ" or "゠" <= ch <= "ヿ" for ch in s)


def contains_ng(text):
    if not text: return False
    t = text.lower()
    return any(ng.lower() in t for ng in NG_KEYWORDS)


VIDEO_IDS = [
    "kwIQeqx-_SA", "eJ1sZ5ryDtw", "xILmEWV1Q4w", "IGcpH-9nXWs",
    "gI5Ql4GHkek", "mLbH4gOrJpc", "dsLiWin6x0s", "7tCW8DC4VYw",
]


def main():
    # 1. videos.list で詳細取得
    resp = youtube.videos().list(
        part="snippet,contentDetails,statistics,status",
        id=",".join(VIDEO_IDS),
    ).execute()
    items = resp.get("items", [])
    print(f"取得件数: {len(items)} / {len(VIDEO_IDS)}")
    print()

    # 2. 関連 channel の登録者を取得
    channel_ids = list({(it["snippet"] or {}).get("channelId", "") for it in items})
    ch_resp = youtube.channels().list(
        part="snippet,statistics,contentDetails",
        id=",".join(channel_ids),
    ).execute()
    channels = {}
    for c in ch_resp.get("items", []):
        st = c.get("statistics") or {}
        sub = int(st.get("subscriberCount", 0)) if not st.get("hiddenSubscriberCount") else 0
        channels[c["id"]] = {
            "title": (c.get("snippet") or {}).get("title", ""),
            "subscriber_count": sub,
            "uploads_playlist_id": (c.get("contentDetails") or {}).get("relatedPlaylists", {}).get("uploads", ""),
        }

    # 3. DB のチャンネル登録状況
    conn = sqlite3.connect("youtube_long.db")
    db_channels = {row[0]: row[1] for row in conn.execute("SELECT channel_id, subscriber_count FROM vtuber_channels").fetchall()}
    print(f"DBに登録済みのチャンネル数: {len(db_channels)}")
    print()

    cutoff = (datetime.now(timezone.utc) - timedelta(days=SEARCH_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"取得期間 cutoff (直近{SEARCH_DAYS}日): {cutoff}")
    print()

    # 4. 各動画について判定
    for v in items:
        vid = v["id"]
        snip = v.get("snippet") or {}
        cd = v.get("contentDetails") or {}
        st = v.get("statistics") or {}
        ch_id = snip.get("channelId", "")
        ch = channels.get(ch_id, {})
        title = snip.get("title", "")
        ch_title = snip.get("channelTitle", "") or ch.get("title", "")
        sub = ch.get("subscriber_count", 0)
        published = snip.get("publishedAt", "")
        duration = parse_duration(cd.get("duration", ""))
        views = int(st.get("viewCount", 0))
        comments = int(st.get("commentCount", 0))
        desc = snip.get("description", "")
        tags = snip.get("tags", []) or []

        # 各条件チェック
        in_db = ch_id in db_channels
        is_top200 = False
        if in_db:
            sorted_chs = sorted(db_channels.items(), key=lambda x: x[1], reverse=True)[:200]
            top200_ids = {c[0] for c in sorted_chs}
            is_top200 = ch_id in top200_ids

        is_jp = has_kana(title) or has_kana(ch_title) or has_kana(desc)
        # 改修1: NG判定は title + channel_title のみ（description / tags は除外）
        is_ng = contains_ng(title) or contains_ng(ch_title) or "切り抜き" in ch_title
        in_period = published >= cutoff
        ok_duration = MIN_DURATION_SEC <= duration <= MAX_DURATION_SEC
        ok_sub = MIN_SUBSCRIBERS <= sub <= MAX_SUBSCRIBERS
        ok_views = views >= sub * VIEW_MULTIPLIER
        ok_comments = comments >= MIN_COMMENTS

        print(f"=== {vid} ===")
        print(f"  タイトル: {title[:50]}")
        print(f"  チャンネル: {ch_title} (id={ch_id}, 登録者={sub:,})")
        print(f"  投稿: {published} / 期間内: {'OK' if in_period else 'NG (古すぎ)'}")
        print(f"  動画長: {duration}秒 ({duration//60}:{duration%60:02d}) / 4-30分: {'OK' if ok_duration else f'NG (範囲外)'}")
        print(f"  再生: {views:,} / コメント: {comments} / いいね={st.get('likeCount','?')}")
        print(f"  登録者範囲: {'OK' if ok_sub else f'NG (登録者={sub:,})'}")
        print(f"  再生≧登録者×0.5: {'OK' if ok_views else f'NG (要={sub*0.5:.0f}, 実={views})'}")
        print(f"  コメント≧5: {'OK' if ok_comments else f'NG (実={comments})'}")
        print(f"  日本語: {'OK' if is_jp else 'NG'}")
        print(f"  NGワード: {'NG (該当)' if is_ng else 'OK'}")
        print(f"  DBにチャンネル登録: {'OK' if in_db else 'NG (vtuber_channels未登録)'}")
        print(f"  巡回TOP200に含まれる: {'OK' if is_top200 else 'NG (登録者順位低い)'}")

        # 総合判定
        all_pass = ok_duration and ok_sub and ok_views and ok_comments and is_jp and not is_ng and in_period
        crawled = in_db and is_top200 and in_period
        print(f"  → フィルタ条件: {'PASS' if all_pass else 'FAIL'}")
        print(f"  → 巡回で拾える: {'YES' if crawled else 'NO'}")
        print()

    conn.close()


if __name__ == "__main__":
    main()
