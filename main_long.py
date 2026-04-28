"""
VTuber 横動画(4-30分 medium)バズランキング取得ツール

設計方針:
- search.list (100 quota/call) は新規発掘1クエリだけに絞り、メインは
  playlistItems.list (1 quota/call) によるチャンネル巡回でクォータを節約。
- 既存 ranking_history.json / ranking_all_history.json から個人VTuberの
  channelId を抽出して vtuber_channels に蓄積し、上位N件のチャンネルから
  最新動画を取りに行く。

主な使い方:
  python main_long.py            # 通常実行（API呼び出しあり）
  python main_long.py --dry      # API を呼ばず DB の既存データでランキング表示

要件定義: https://www.notion.so/34d4806b96df811c88aff310ad2161c7
"""

import csv
import io
import json
import os
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from googleapiclient.discovery import build

from quota_logger import log_quota_run
from vtuber_common import (
    parse_iso8601_duration,
    contains_ng_keyword as _common_contains_ng_keyword,
    has_japanese_kana,
    is_japanese_vtuber,
)

# Windows cp932 で出力エラーを防ぐ
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

load_dotenv()

DRY_RUN = "--dry" in sys.argv

API_KEY = os.getenv("YOUTUBE_API_KEY")
if not API_KEY or API_KEY == "YOUR_API_KEY_HERE":
    if not DRY_RUN:
        print("エラー: .env ファイルに YOUTUBE_API_KEY を設定してください。")
        sys.exit(1)

youtube = build("youtube", "v3", developerKey=API_KEY) if API_KEY else None

# ---------------------------------------------------------------------------
# フィルタ条件（要件定義 2026-04-28 改修: 倍率緩和・期間延長）
# ---------------------------------------------------------------------------
MIN_SUBSCRIBERS = 500
MAX_SUBSCRIBERS = 100_000
VIEW_MULTIPLIER = 0.3            # 再生数 >= 登録者数 × この値（0.5→0.3 に緩和）
MIN_COMMENTS = 5
MIN_DURATION_SEC = 4 * 60        # 4分
MAX_DURATION_SEC = 30 * 60       # 30分
SEARCH_DAYS = 14                 # 直近14日（7→14 に延長）

# ---------------------------------------------------------------------------
# クォータ節約パラメータ
# ---------------------------------------------------------------------------
CRAWL_TOP_N_CHANNELS = 200       # 1回の巡回で参照する上位チャンネル数
PLAYLIST_ITEMS_PER_CHANNEL = 30  # 各チャンネルから取得する最新動画数（20→30 で14日分カバー）
DISCOVER_DAYS = 7                # 新規発掘検索の対象期間（1→7 日）
DISCOVER_MAX_RESULTS = 50

# 曜日別検索クエリローテーション（月=0 ... 日=6、各日2クエリ = 200 quota/run）
# 2026-04-28: 水曜の「VTuber 歌ってみた」と日曜の「Vsinger」は歌動画を発掘してしまうため
# 「VTuber マシュマロ」「個人VTuber トーク」に差し替え（歌系は集計対象外と決定）
DISCOVER_QUERY_ROTATION = {
    0: ["個人VTuber", "個人勢 雑談"],            # 月
    1: ["新人VTuber", "VTuber ゲーム実況"],       # 火
    2: ["個人勢VTuber", "VTuber マシュマロ"],     # 水
    3: ["個人VTuber 配信", "VTuber 解説"],        # 木
    4: ["個人勢 ASMR", "VTuber 雑談"],            # 金
    5: ["VTuber 新人", "個人VTuber 配信"],        # 土
    6: ["個人VTuber", "個人VTuber トーク"],       # 日
}

# ---------------------------------------------------------------------------
# NGキーワード / 事務所ブラックリスト（main.py 継承）
# 2026-04-28 改修1+: "まとめ" は本人動画のタイトルにも一般語として頻出するため除外
# （例: 「嘘だらけのまとめサイトにまとめられる新人Vtuber」など）。
# 切り抜き系は "切り抜き" "反応" "速報" "手書き" でほぼカバーできる。
# 2026-04-28 改修2: 歌ってみた/カバー曲は集計対象外と決定（ユーザー指示）
# ---------------------------------------------------------------------------
NG_KEYWORDS = [
    "切り抜き", "速報", "手書き", "反応",
    "歌ってみた", "歌みた", "歌う", "cover", "カバー",
    "ホロライブ", "hololive", "にじさんじ", "nijisanji",
    "ぶいすぽ", "ネオポルテ",
]

DB_FILE = "youtube_long.db"
HISTORY_FILE = "long_history.json"
CSV_FILE = "long_output.csv"


# =============================================================================
# 共通ユーティリティ（main.py 由来）
# =============================================================================

def contains_ng_keyword(text):
    """このスクリプトの NG_KEYWORDS で判定する薄いラッパー。"""
    return _common_contains_ng_keyword(text, NG_KEYWORDS)


# =============================================================================
# SQLite データベース
# =============================================================================

def init_db():
    """SQLite データベースとテーブルを初期化"""
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vtuber_channels (
            channel_id           TEXT PRIMARY KEY,
            title                TEXT NOT NULL DEFAULT '',
            subscriber_count     INTEGER NOT NULL DEFAULT 0,
            uploads_playlist_id  TEXT NOT NULL DEFAULT '',
            source               TEXT NOT NULL DEFAULT '',
            last_scanned         TEXT NOT NULL DEFAULT '',
            added_at             TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS long_videos (
            id              TEXT PRIMARY KEY,
            channel_id      TEXT NOT NULL DEFAULT '',
            title           TEXT NOT NULL DEFAULT '',
            description     TEXT NOT NULL DEFAULT '',
            published       TEXT NOT NULL DEFAULT '',
            duration_sec    INTEGER NOT NULL DEFAULT 0,
            view_count      INTEGER NOT NULL DEFAULT 0,
            comment_count   INTEGER NOT NULL DEFAULT 0,
            subscriber_count INTEGER NOT NULL DEFAULT 0,
            channel_title   TEXT NOT NULL DEFAULT '',
            url             TEXT NOT NULL DEFAULT '',
            tags_json       TEXT NOT NULL DEFAULT '[]',
            fetched_at      TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_long_videos_published ON long_videos(published DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_long_videos_channel ON long_videos(channel_id)")
    conn.commit()
    return conn


def upsert_channels(conn, channels):
    """チャンネルを UPSERT。既存行は subscriber_count / title を更新。

    2026-04-28 バグ修正: bootstrap_channels_via_video_lookup() などが
    subscriber_count=0 で upsert すると、過去に正しく取得済みの値を 0 で
    上書きしてしまい TOP200 から漏れる事象を修正。0 が来た時は既存値を保持する。
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    for ch in channels:
        conn.execute("""
            INSERT INTO vtuber_channels (
                channel_id, title, subscriber_count,
                uploads_playlist_id, source, last_scanned, added_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                title = CASE WHEN excluded.title != '' THEN excluded.title ELSE vtuber_channels.title END,
                subscriber_count = CASE
                    WHEN excluded.subscriber_count > 0 THEN excluded.subscriber_count
                    ELSE vtuber_channels.subscriber_count
                END,
                uploads_playlist_id = CASE
                    WHEN excluded.uploads_playlist_id != '' THEN excluded.uploads_playlist_id
                    ELSE vtuber_channels.uploads_playlist_id
                END
        """, (
            ch["channel_id"],
            ch.get("title", ""),
            ch.get("subscriber_count", 0),
            ch.get("uploads_playlist_id", ""),
            ch.get("source", "shorts_history"),
            "",
            now_iso,
        ))
    conn.commit()


def upsert_video(conn, v):
    conn.execute("""
        INSERT INTO long_videos (
            id, channel_id, title, description, published,
            duration_sec, view_count, comment_count, subscriber_count,
            channel_title, url, tags_json, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title,
            description = excluded.description,
            published = excluded.published,
            duration_sec = excluded.duration_sec,
            view_count = excluded.view_count,
            comment_count = excluded.comment_count,
            subscriber_count = excluded.subscriber_count,
            channel_title = excluded.channel_title,
            url = excluded.url,
            tags_json = excluded.tags_json,
            fetched_at = excluded.fetched_at
    """, (
        v["id"], v["channel_id"], v["title"], v["description"], v["published"],
        v["duration_sec"], v["view_count"], v["comment_count"], v["subscriber_count"],
        v["channel_title"], v["url"], json.dumps(v.get("tags", []), ensure_ascii=False),
        v["fetched_at"],
    ))


def upsert_videos(conn, videos):
    for v in videos:
        upsert_video(conn, v)
    conn.commit()


def get_db_stats(conn):
    n_ch = conn.execute("SELECT COUNT(*) FROM vtuber_channels").fetchone()[0]
    n_vid = conn.execute("SELECT COUNT(*) FROM long_videos").fetchone()[0]
    return n_ch, n_vid


# =============================================================================
# 既存 Shorts ランキング履歴からチャンネル抽出（クォータ消費 0）
# =============================================================================

def seed_channels_from_history(conn):
    """ranking_history.json と ranking_all_history.json から channelId を抽出し、
    vtuber_channels テーブルに種チャンネルとして登録する。
    既存履歴に channel_id が無い場合、URL から videoId を集めて返す（後段で逆引き）。
    戻り値: (登録チャンネル数, 逆引き対象 video_id リスト)"""
    seeds = {}
    pending_video_ids = set()

    for fname in ("ranking_history.json", "ranking_all_history.json"):
        if not os.path.exists(fname):
            continue
        try:
            with open(fname, "r", encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue
        for date_key, entries in history.items():
            if not isinstance(entries, list):
                continue
            for e in entries:
                ch_id = e.get("channel_id") or e.get("channelId") or ""
                ch_name = e.get("channel", "") or e.get("channel_name", "")
                if ch_id:
                    if ch_id not in seeds:
                        seeds[ch_id] = {
                            "channel_id": ch_id,
                            "title": ch_name,
                            "subscriber_count": e.get("subscribers", 0),
                            "uploads_playlist_id": "",
                            "source": "shorts_history",
                        }
                    elif ch_name and not seeds[ch_id]["title"]:
                        seeds[ch_id]["title"] = ch_name
                else:
                    # channel_id が履歴に無い場合は URL から videoId を抽出して後段の逆引きに回す
                    url = e.get("url", "")
                    m = re.search(r"/(?:shorts|watch\?v=)/?([a-zA-Z0-9_-]{11})", url)
                    if m:
                        pending_video_ids.add(m.group(1))

    if seeds:
        upsert_channels(conn, list(seeds.values()))
    return len(seeds), list(pending_video_ids)


def bootstrap_channels_via_video_lookup(conn, video_ids, quota, limit=300):
    """初回ブートストラップ: 既存履歴 URL の videoId から videos.list で channelId を逆引き。
    DB の vtuber_channels に種チャンネルを追加する。
    クォータ消費: ceil(min(len(video_ids), limit) / 50) units（数 quota 程度）"""
    if not video_ids or not youtube:
        return 0
    target = video_ids[:limit]
    print(f"  → {len(target)} 件の動画IDから channelId 逆引き中...")
    found = {}
    for i in range(0, len(target), 50):
        batch = target[i:i + 50]
        try:
            req = youtube.videos().list(part="snippet", id=",".join(batch))
            resp = req.execute()
            quota.videos_calls += 1
            for it in resp.get("items", []):
                snip = it.get("snippet") or {}
                ch_id = snip.get("channelId")
                ch_title = snip.get("channelTitle", "")
                if ch_id and ch_id not in found:
                    found[ch_id] = {
                        "channel_id": ch_id,
                        "title": ch_title,
                        "subscriber_count": 0,
                        "uploads_playlist_id": "",
                        "source": "shorts_history_lookup",
                    }
        except Exception as e:
            err = str(e)
            if "quotaExceeded" in err:
                print(f"  ⚠ 逆引きでクォータ上限。{len(found)} 件まで取得")
                break
    if found:
        upsert_channels(conn, list(found.values()))
    return len(found)


# =============================================================================
# YouTube API ラッパー
# =============================================================================

class QuotaTracker:
    def __init__(self):
        self.search_calls = 0
        self.playlist_items_calls = 0
        self.videos_calls = 0
        self.channels_calls = 0

    def total(self):
        return (
            self.search_calls * 100
            + self.playlist_items_calls * 1
            + self.videos_calls * 1
            + self.channels_calls * 1
        )

    def report(self):
        print(f"  search.list: {self.search_calls}回 = {self.search_calls * 100} quota")
        print(f"  playlistItems.list: {self.playlist_items_calls}回 = {self.playlist_items_calls} quota")
        print(f"  videos.list: {self.videos_calls}回 = {self.videos_calls} quota")
        print(f"  channels.list: {self.channels_calls}回 = {self.channels_calls} quota")
        print(f"  → 合計消費: 約 {self.total()} quota")


def discover_via_search(quota):
    """新規発掘: 曜日別ローテーションのクエリ × 直近DISCOVER_DAYS日 × videoDuration=medium で検索。
    曜日ローテによって週14クエリ分のカバー範囲を獲得しつつ、1日の search.list 呼び出しは
    DISCOVER_QUERY_ROTATION[今日の曜日] のサイズだけに抑える（=200 quota/run）。"""
    if not youtube:
        return []
    now = datetime.now(timezone.utc)
    weekday = datetime.now().weekday()  # JST基準ではないが運用上問題なし（曜日のばらけが目的）
    queries = DISCOVER_QUERY_ROTATION.get(weekday, ["個人VTuber"])
    published_after = (now - timedelta(days=DISCOVER_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    video_ids = []
    seen = set()

    print(f"  → 本日({weekday}曜)のクエリ: {queries}")
    for q in queries:
        try:
            req = youtube.search().list(
                q=q,
                type="video",
                videoDuration="medium",
                publishedAfter=published_after,
                order="viewCount",
                part="id",
                maxResults=DISCOVER_MAX_RESULTS,
                regionCode="JP",
                relevanceLanguage="ja",
            )
            resp = req.execute()
            quota.search_calls += 1
            for it in resp.get("items", []):
                vid = it["id"].get("videoId")
                if vid and vid not in seen:
                    seen.add(vid)
                    video_ids.append(vid)
        except Exception as e:
            err = str(e)
            if "quotaExceeded" in err:
                print(f"  ⚠ search.list でクォータ上限。{quota.search_calls}クエリで打ち切り")
                break
            print(f"  ⚠ 新規発掘検索エラー ({q}): {e}")

    return video_ids


def fetch_uploads_playlist_ids(conn, quota):
    """vtuber_channels で uploads_playlist_id 未取得 or subscriber_count=0 のチャンネルを
    channels.list で取得。2026-04-28: bootstrap で subscriber_count=0 のまま放置されていた
    過去データの修復もこのパスで行う（24.5% が 0 だった）。"""
    rows = conn.execute(
        "SELECT channel_id FROM vtuber_channels "
        "WHERE uploads_playlist_id = '' OR subscriber_count = 0 "
        "LIMIT 500"
    ).fetchall()
    channel_ids = [r[0] for r in rows if r[0]]
    if not channel_ids:
        return 0
    if not youtube:
        return 0
    updated = 0
    for i in range(0, len(channel_ids), 50):
        batch = channel_ids[i:i + 50]
        try:
            req = youtube.channels().list(
                part="contentDetails,statistics,snippet",
                id=",".join(batch),
            )
            resp = req.execute()
            quota.channels_calls += 1
            for item in resp.get("items", []):
                cid = item["id"]
                uploads = (item.get("contentDetails") or {}).get("relatedPlaylists", {}).get("uploads", "")
                stats = item.get("statistics") or {}
                sub_count = int(stats.get("subscriberCount", 0))
                if stats.get("hiddenSubscriberCount", False):
                    sub_count = 0
                snip = item.get("snippet") or {}
                title = snip.get("title", "")
                conn.execute("""
                    UPDATE vtuber_channels
                       SET uploads_playlist_id = ?,
                           subscriber_count = ?,
                           title = CASE WHEN ?!='' THEN ? ELSE title END
                     WHERE channel_id = ?
                """, (uploads, sub_count, title, title, cid))
                updated += 1
        except Exception as e:
            print(f"  ⚠ channels.list エラー: {e}")
    conn.commit()
    return updated


def fetch_recent_video_ids_from_uploads(conn, quota):
    """巡回対象の上位チャンネルから uploads playlist 経由で最新動画IDを取得。
    対象は vtuber_channels から登録者数降順 + uploads_playlist_id がある行を上位 CRAWL_TOP_N_CHANNELS 件。"""
    rows = conn.execute(
        "SELECT channel_id, uploads_playlist_id, title FROM vtuber_channels "
        "WHERE uploads_playlist_id != '' "
        "ORDER BY subscriber_count DESC LIMIT ?",
        (CRAWL_TOP_N_CHANNELS,)
    ).fetchall()
    if not rows or not youtube:
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SEARCH_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    video_ids = []
    for ch_id, uploads, ch_title in rows:
        try:
            req = youtube.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=uploads,
                maxResults=PLAYLIST_ITEMS_PER_CHANNEL,
            )
            resp = req.execute()
            quota.playlist_items_calls += 1
            for it in resp.get("items", []):
                snip = it.get("snippet") or {}
                published = snip.get("publishedAt", "") or (it.get("contentDetails") or {}).get("videoPublishedAt", "")
                if not published:
                    continue
                # 期間外は除外
                if published < cutoff:
                    continue
                vid = (it.get("contentDetails") or {}).get("videoId") or snip.get("resourceId", {}).get("videoId")
                if vid:
                    video_ids.append(vid)
        except Exception as e:
            err = str(e)
            if "quotaExceeded" in err:
                print(f"  ⚠ playlistItems.list でクォータ上限。{quota.playlist_items_calls}件まで巡回")
                break
            # 個別チャンネルの失敗は続行
            continue
    return video_ids


def fetch_video_details(video_ids, quota):
    """videos.list で詳細情報を一括取得"""
    if not youtube or not video_ids:
        return []
    videos = []
    seen = set()
    unique_ids = [v for v in video_ids if not (v in seen or seen.add(v))]
    for i in range(0, len(unique_ids), 50):
        batch = unique_ids[i:i + 50]
        try:
            req = youtube.videos().list(
                part="snippet,contentDetails,statistics",
                id=",".join(batch),
            )
            resp = req.execute()
            quota.videos_calls += 1
            videos.extend(resp.get("items", []))
        except Exception as e:
            err = str(e)
            if "quotaExceeded" in err:
                print(f"  ⚠ videos.list でクォータ上限。取得済み {len(videos)} 件で続行")
                break
    return videos


def fetch_channel_details(channel_ids, quota):
    """channels.list で登録者数等を取得（discover で見つけた新規チャンネル用）"""
    if not youtube or not channel_ids:
        return {}
    channels = {}
    unique_ids = list({c for c in channel_ids if c})
    for i in range(0, len(unique_ids), 50):
        batch = unique_ids[i:i + 50]
        try:
            req = youtube.channels().list(
                part="contentDetails,statistics,snippet",
                id=",".join(batch),
            )
            resp = req.execute()
            quota.channels_calls += 1
            for item in resp.get("items", []):
                stats = item.get("statistics") or {}
                sub_count = int(stats.get("subscriberCount", 0))
                if stats.get("hiddenSubscriberCount", False):
                    sub_count = 0
                uploads = (item.get("contentDetails") or {}).get("relatedPlaylists", {}).get("uploads", "")
                channels[item["id"]] = {
                    "channel_id": item["id"],
                    "title": (item.get("snippet") or {}).get("title", ""),
                    "subscriber_count": sub_count,
                    "uploads_playlist_id": uploads,
                    "source": "search_discover",
                }
        except Exception as e:
            print(f"  ⚠ channels.list エラー: {e}")
    return channels


# =============================================================================
# フィルタリング
# =============================================================================

def is_blacklisted(video, channel_name):
    """NG判定: タイトル + チャンネル名のみを対象にする（description/tags は除外）。

    description/tags にはコラボ相手や検索対策として大手事務所名(ホロライブ等)が
    雑に入っていることが多く、個人VTuber本人の動画でも誤検知してしまうため
    2026-04-28 改修1 で対象から外した。
    """
    if "切り抜き" in (channel_name or ""):
        return True
    title = (video.get("snippet") or {}).get("title", "")
    for text in [title, channel_name or ""]:
        if contains_ng_keyword(text):
            return True
    return False


def filter_videos(videos, channels_info):
    """videos (videos.list レスポンス) をフィルタして整形。
    channels_info: { channel_id: {title, subscriber_count, ...} }"""
    now_iso = datetime.now(timezone.utc).isoformat()
    results = []
    blacklisted = 0
    skipped_reason = {"duration": 0, "subscribers": 0, "views": 0, "comments": 0, "no_jp": 0, "no_channel": 0}

    for v in videos:
        snip = v.get("snippet") or {}
        cd = v.get("contentDetails") or {}
        st = v.get("statistics") or {}
        ch_id = snip.get("channelId", "")
        ch_info = channels_info.get(ch_id)
        if not ch_info:
            skipped_reason["no_channel"] += 1
            continue
        ch_title = ch_info.get("title", "")
        sub_count = ch_info.get("subscriber_count", 0)

        if is_blacklisted(v, ch_title):
            blacklisted += 1
            continue
        if not is_japanese_vtuber(v, ch_title):
            skipped_reason["no_jp"] += 1
            continue

        duration_iso = cd.get("duration")
        if not duration_iso:
            continue
        duration_sec = parse_iso8601_duration(duration_iso)
        if not (MIN_DURATION_SEC <= duration_sec <= MAX_DURATION_SEC):
            skipped_reason["duration"] += 1
            continue

        if not (MIN_SUBSCRIBERS <= sub_count <= MAX_SUBSCRIBERS):
            skipped_reason["subscribers"] += 1
            continue

        view_count = int(st.get("viewCount", 0))
        comment_count = int(st.get("commentCount", 0))
        if view_count < sub_count * VIEW_MULTIPLIER:
            skipped_reason["views"] += 1
            continue
        if comment_count < MIN_COMMENTS:
            skipped_reason["comments"] += 1
            continue

        growth_rate = view_count / sub_count if sub_count > 0 else 0

        results.append({
            "id": v["id"],
            "channel_id": ch_id,
            "title": snip.get("title", ""),
            "description": snip.get("description", "")[:500],
            "published": snip.get("publishedAt", "")[:19],
            "duration_sec": duration_sec,
            "view_count": view_count,
            "comment_count": comment_count,
            "subscriber_count": sub_count,
            "channel_title": ch_title,
            "url": f"https://www.youtube.com/watch?v={v['id']}",
            "tags": snip.get("tags", []) or [],
            "growth_rate": round(growth_rate, 1),
            "fetched_at": now_iso,
        })

    print(f"  → ブラックリスト除外: {blacklisted}件, "
          f"その他スキップ: {skipped_reason}")
    return results


def load_top_videos_from_db(conn, days=SEARCH_DAYS, limit=50):
    """DBから直近days日分のすべての動画を読み出して growth_rate でランキング。

    NG_KEYWORDS が後から変更された場合に既存DBの動画にも遡及適用するため、
    title / channel_title に対して読み出し時に NG チェックをかける。
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    rows = conn.execute(
        "SELECT id, channel_id, title, description, published, duration_sec, "
        "view_count, comment_count, subscriber_count, channel_title, url, tags_json, fetched_at "
        "FROM long_videos WHERE published >= ? ORDER BY published DESC",
        (cutoff,)
    ).fetchall()
    items = []
    skipped_ng = 0
    for r in rows:
        title = r[2] or ""
        channel_title = r[9] or ""
        # 現行の NG_KEYWORDS でフィルタ（遡及適用）
        if contains_ng_keyword(title) or contains_ng_keyword(channel_title) or "切り抜き" in channel_title:
            skipped_ng += 1
            continue
        sub = r[8] or 0
        view = r[6] or 0
        gr = round(view / sub, 1) if sub > 0 else 0
        items.append({
            "id": r[0], "channel_id": r[1], "title": title, "description": r[3],
            "published": r[4], "duration_sec": r[5],
            "view_count": view, "comment_count": r[7], "subscriber_count": sub,
            "channel_title": channel_title, "url": r[10],
            "tags": json.loads(r[11]) if r[11] else [],
            "growth_rate": gr, "fetched_at": r[12],
        })
    if skipped_ng:
        print(f"  → 表示時 NG フィルタで {skipped_ng} 件除外（NG_KEYWORDS の遡及適用）")
    items.sort(key=lambda x: x["growth_rate"], reverse=True)
    return items[:limit]


# =============================================================================
# 出力
# =============================================================================

def display_results(results):
    if not results:
        print("\n条件を満たす動画が見つかりませんでした。")
        return
    print(f"\n{'='*120}")
    print(f"  VTuber 横動画(4-30分) バズランキング（上位 {len(results)} 件）")
    print(f"  条件: 登録者 {MIN_SUBSCRIBERS:,}〜{MAX_SUBSCRIBERS:,}人 / "
          f"再生≧登録者×{VIEW_MULTIPLIER} / コメント≧{MIN_COMMENTS} / "
          f"{MIN_DURATION_SEC//60}〜{MAX_DURATION_SEC//60}分 / 直近{SEARCH_DAYS}日")
    print(f"{'='*120}")
    print(f"{'順位':>4}  {'タイトル':<40}  {'チャンネル':<20}  "
          f"{'登録者':>8}  {'再生数':>10}  {'伸び率':>6}  {'時間':>5}  {'投稿日':<10}")
    print("-" * 120)
    for i, r in enumerate(results, 1):
        title = unicodedata.normalize("NFC", r["title"])[:38]
        ch = unicodedata.normalize("NFC", r["channel_title"])[:18]
        mins = r["duration_sec"] // 60
        secs = r["duration_sec"] % 60
        print(
            f"{i:>4}  {title:<40}  {ch:<20}  "
            f"{r['subscriber_count']:>8,}  {r['view_count']:>10,}  "
            f"{r['growth_rate']:>5.1f}x  {mins:>2}:{secs:02}  {r['published'][:10]:<10}"
        )


def save_csv(results):
    if not results:
        return
    with open(CSV_FILE, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "順位", "タイトル", "チャンネル名", "登録者数", "再生数",
            "伸び率", "コメント数", "動画秒数", "投稿日", "URL",
        ])
        for i, r in enumerate(results, 1):
            writer.writerow([
                i, r["title"], r["channel_title"], r["subscriber_count"],
                r["view_count"], f"{r['growth_rate']}x", r["comment_count"],
                r["duration_sec"], r["published"][:10],
                f'=HYPERLINK("{r["url"]}","動画を開く")',
            ])
    print(f"CSV出力: {CSV_FILE}")


def save_history(results):
    if not results:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    history = {}
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, IOError):
            history = {}
    history[today] = [
        {
            "rank": i,
            "title": r["title"],
            "channel": r["channel_title"],
            "channel_id": r["channel_id"],
            "subscribers": r["subscriber_count"],
            "views": r["view_count"],
            "growth_rate": r["growth_rate"],
            "comments": r["comment_count"],
            "duration": r["duration_sec"],
            "published": r["published"][:10],
            "url": r["url"],
        }
        for i, r in enumerate(results, 1)
    ]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    print(f"履歴保存: {HISTORY_FILE} ({today}, 累計{len(history)}日分)")


# =============================================================================
# メイン
# =============================================================================

def main():
    print("YouTube 横動画 (4-30分 medium) VTuber バズランキング")
    print("=" * 60)
    print(f"フィルタ: 登録者 {MIN_SUBSCRIBERS:,}〜{MAX_SUBSCRIBERS:,} / "
          f"再生≧登録者×{VIEW_MULTIPLIER} / コメント≧{MIN_COMMENTS} / "
          f"{MIN_DURATION_SEC//60}〜{MAX_DURATION_SEC//60}分 / 直近{SEARCH_DAYS}日")
    print(f"巡回: 上位 {CRAWL_TOP_N_CHANNELS} チャンネル × 各最新 {PLAYLIST_ITEMS_PER_CHANNEL} 件")
    weekday = datetime.now().weekday()
    print(f"新規発掘: 曜日ローテ(本日={weekday}) → {DISCOVER_QUERY_ROTATION.get(weekday)} × 直近{DISCOVER_DAYS}日")

    if DRY_RUN:
        print("\n[DRY RUN] API は呼び出さず、DB の既存データでランキング表示します。")

    quota = QuotaTracker()

    # 1. DB初期化
    print(f"\n[1/7] データベース初期化中...")
    conn = init_db()
    n_ch, n_vid = get_db_stats(conn)
    print(f"  → {DB_FILE} 準備完了（既存チャンネル: {n_ch}, 動画: {n_vid}）")

    # 2. 既存 Shorts ランキング履歴からチャンネル種を抽出
    print(f"\n[2/7] Shortsランキング履歴からチャンネル種を抽出中...")
    seeded, pending_video_ids = seed_channels_from_history(conn)
    print(f"  → {seeded} チャンネルを vtuber_channels に追加/更新（逆引き対象 {len(pending_video_ids)} 動画）")

    if not DRY_RUN:
        # 既存履歴に channel_id 欠落分があれば videos.list で逆引き
        if pending_video_ids and seeded < CRAWL_TOP_N_CHANNELS:
            n_boot = bootstrap_channels_via_video_lookup(conn, pending_video_ids, quota)
            print(f"  → 逆引きで {n_boot} チャンネル追加")

        # 3. uploads_playlist_id を埋める
        print(f"\n[3/7] uploads_playlist_id を取得中（channels.list）...")
        updated = fetch_uploads_playlist_ids(conn, quota)
        print(f"  → {updated} チャンネルの uploads_playlist_id を更新")

        # 4. 上位 N チャンネルを巡回して最新動画ID取得
        print(f"\n[4/7] チャンネル巡回中（playlistItems.list）...")
        crawl_video_ids = fetch_recent_video_ids_from_uploads(conn, quota)
        print(f"  → 巡回で {len(crawl_video_ids)} 件の動画ID取得")

        # 5. 新規発掘検索
        print(f"\n[5/7] 新規発掘検索中（search.list）...")
        discover_video_ids = discover_via_search(quota)
        print(f"  → 検索で {len(discover_video_ids)} 件の動画ID取得")

        # 6. 動画詳細＆チャンネル情報を取得→フィルタ→DB保存
        all_ids = list({*crawl_video_ids, *discover_video_ids})
        print(f"\n[6/7] 動画詳細を取得・フィルタリング中（videos.list）...")
        videos = fetch_video_details(all_ids, quota)
        print(f"  → 動画詳細 {len(videos)} 件取得")

        # 新規発掘で見つかった動画のチャンネルが未登録の場合は追加
        ch_ids_in_videos = list({(v.get("snippet") or {}).get("channelId", "") for v in videos})
        ch_info_fresh = fetch_channel_details(ch_ids_in_videos, quota)
        upsert_channels(conn, list(ch_info_fresh.values()))

        # 既存DBの登録者数も統合
        existing_rows = conn.execute(
            "SELECT channel_id, title, subscriber_count FROM vtuber_channels"
        ).fetchall()
        ch_info = {
            r[0]: {"channel_id": r[0], "title": r[1], "subscriber_count": r[2]}
            for r in existing_rows
        }
        # 直前のフレッシュ取得分で上書き
        for cid, info in ch_info_fresh.items():
            ch_info[cid] = info

        # フィルタ
        filtered = filter_videos(videos, ch_info)
        print(f"  → フィルタ通過: {len(filtered)} 件")

        # DB UPSERT
        upsert_videos(conn, filtered)

    # 7. DBから最終ランキング生成
    print(f"\n[7/7] DBから直近{SEARCH_DAYS}日分のランキング生成中...")
    ranked = load_top_videos_from_db(conn, days=SEARCH_DAYS, limit=50)
    conn.close()

    # 出力
    display_results(ranked)
    save_csv(ranked)
    save_history(ranked)

    if not DRY_RUN:
        print(f"\n===== APIクォータ消費 =====")
        quota.report()

    print(f"\n完了!")
    return quota


if __name__ == "__main__":
    quota_result = None
    try:
        quota_result = main()
    except Exception as e:
        print(f"\n❌ 致命的エラー: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        # クォータ消費を JSONL ログに追記（致命的エラー時も部分集計を記録）
        if quota_result is not None:
            log_quota_run("main_long.py", {
                "search_list": quota_result.search_calls,
                "videos_list": quota_result.videos_calls,
                "channels_list": quota_result.channels_calls,
                "playlist_items_list": quota_result.playlist_items_calls,
            })
