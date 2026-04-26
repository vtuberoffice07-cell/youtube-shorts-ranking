"""
TikTok VTuber バズランキング取得ツール
Apify の TikTok Scraper を使用してデータ取得 → SQLite に UPSERT 保存

使い方:
  python tiktok_ranking.py            # 通常実行
  python tiktok_ranking.py --dry      # Apify を呼ばず DB の既存データでランキング表示
  python tiktok_ranking.py --debug    # 通常実行 + 生データを tiktok_raw_debug.json に保存
"""

import csv
import io
import json
import os
import re
import sqlite3
import sys
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apify_client import ApifyClient
from dotenv import load_dotenv

# Windows cp932 で出力エラーを防ぐ
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

load_dotenv()

# ---------------------------------------------------------------------------
# 環境変数チェック（--dry モード以外では必須）
# ---------------------------------------------------------------------------
DRY_RUN = "--dry" in sys.argv

APIFY_TOKEN = os.getenv("APIFY_API_TOKEN")
if not APIFY_TOKEN and not DRY_RUN:
    print("=" * 60)
    print("エラー: APIFY_API_TOKEN が設定されていません。")
    print("")
    print("対処法:")
    print("  1. .env ファイルに APIFY_API_TOKEN=your_token を記載")
    print("  2. または環境変数として export APIFY_API_TOKEN=your_token")
    print("")
    print("トークンは https://console.apify.com/account#/integrations")
    print("から取得できます。")
    print("=" * 60)
    sys.exit(1)

client = ApifyClient(APIFY_TOKEN) if APIFY_TOKEN else None

# ---------------------------------------------------------------------------
# 【課金事故防止】 ハードコードされた安全制限
# ※ これらの値は絶対に変更しないでください
# ---------------------------------------------------------------------------
SEARCH_HASHTAGS = ["新人vtuber", "個人vtuber", "vtuber準備中"]
# 日本語ハッシュタグ3つで日本語VTuber動画を直接ターゲット
# 取得上限: resultsPerPage=1 → Actorがタグあたり1件ずつ返す（3タグ計3件）
# 実測: resultsPerPage=10 → 30件取得で$0.15課金（$0.005×30件）
#        resultsPerPage=1  → 3件取得で$0.015課金（Twitter同等）
# 安全上限5件（3件+αの余裕）。DB蓄積により30日で最大90件のユニーク動画が蓄積される
ABSOLUTE_MAX_ITEMS = 5
ACTOR_ID = "clockworks/tiktok-hashtag-scraper"
ACTOR_TIMEOUT_SECS = 300  # Actor 実行タイムアウト（5分）

# --- フィルタ条件 ---
MIN_FOLLOWERS = 100       # 小規模VTuberもカバー
MAX_FOLLOWERS = 100000
VIEW_MULTIPLIER = 1.5     # 再生数 >= フォロワー数 × この値
MIN_COMMENTS = 3          # コメント数の閾値
SEARCH_DAYS = 30          # 直近何日間

# --- NGキーワード ---
NG_KEYWORDS = [
    "切り抜き", "まとめ", "速報", "手書き", "反応",
    "ホロライブ", "hololive", "にじさんじ", "nijisanji",
    "ぶいすぽ", "ネオポルテ",
]

# デバッグ: 生データを保存して構造を確認できるようにする
DEBUG_RAW_FILE = "tiktok_raw_debug.json"
SAVE_RAW_DEBUG = "--debug" in sys.argv

DB_FILE = "tiktok.db"
HISTORY_FILE = "tiktok_history.json"
CSV_FILE = "tiktok_output.csv"
THUMBS_DIR = "tiktok_thumbs"


# =============================================================================
# SQLite データベース操作
# =============================================================================

def init_db():
    """SQLite データベースとテーブルを初期化"""
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tiktok_videos (
            id              TEXT PRIMARY KEY,
            title           TEXT NOT NULL DEFAULT '',
            author          TEXT NOT NULL DEFAULT '',
            username        TEXT NOT NULL DEFAULT '',
            followers       INTEGER NOT NULL DEFAULT 0,
            views           INTEGER NOT NULL DEFAULT 0,
            likes           INTEGER NOT NULL DEFAULT 0,
            comments        INTEGER NOT NULL DEFAULT 0,
            hashtags        TEXT NOT NULL DEFAULT '[]',
            published       TEXT NOT NULL DEFAULT '',
            url             TEXT NOT NULL DEFAULT '',
            cover           TEXT NOT NULL DEFAULT '',
            fetched_at      TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tiktok_views ON tiktok_videos(views DESC)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tiktok_published ON tiktok_videos(published DESC)
    """)
    conn.commit()
    return conn


def upsert_video(conn, video):
    """動画を UPSERT（存在すれば更新、なければ挿入）"""
    conn.execute("""
        INSERT INTO tiktok_videos (
            id, title, author, username, followers,
            views, likes, comments, hashtags,
            published, url, cover, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title,
            author = excluded.author,
            username = excluded.username,
            followers = excluded.followers,
            views = excluded.views,
            likes = excluded.likes,
            comments = excluded.comments,
            hashtags = excluded.hashtags,
            published = excluded.published,
            url = excluded.url,
            cover = excluded.cover,
            fetched_at = excluded.fetched_at
    """, (
        video["id"],
        video["title"],
        video["author"],
        video["username"],
        video["followers"],
        video["views"],
        video["likes"],
        video["comments"],
        json.dumps(video["hashtags"], ensure_ascii=False),
        video["published"],
        video["url"],
        video.get("cover", ""),
        video["fetched_at"],
    ))


def upsert_videos(conn, videos):
    """複数動画を一括 UPSERT"""
    for video in videos:
        upsert_video(conn, video)
    conn.commit()
    print(f"  → DB保存完了: {len(videos)}件を tiktok_videos テーブルに UPSERT")


def get_db_stats(conn):
    """DB内の動画件数と最新取得日時を返す"""
    row = conn.execute("SELECT COUNT(*), MAX(fetched_at) FROM tiktok_videos").fetchone()
    return row[0] or 0, row[1] or "N/A"


def load_videos_from_db(conn):
    """DB から直近 SEARCH_DAYS 日分の動画を読み出し"""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SEARCH_DAYS)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT id, title, author, username, followers, views, likes, comments, "
        "hashtags, published, url, cover, fetched_at "
        "FROM tiktok_videos WHERE published >= ? ORDER BY views DESC",
        (cutoff,)
    ).fetchall()
    videos = []
    for r in rows:
        videos.append({
            "id": r[0],
            "title": r[1],
            "author": r[2],
            "username": r[3],
            "followers": r[4],
            "views": r[5],
            "likes": r[6],
            "comments": r[7],
            "hashtags": json.loads(r[8]),
            "published": r[9],
            "url": r[10],
            "cover": r[11],
            "fetched_at": r[12],
        })
    return videos


# =============================================================================
# Apify データ取得
# =============================================================================

def contains_japanese(text):
    """テキストに日本語（ひらがな・カタカナ・漢字）が含まれているか判定"""
    for ch in text:
        try:
            name = unicodedata.name(ch, "")
        except ValueError:
            continue
        if ("CJK" in name or "HIRAGANA" in name or "KATAKANA" in name):
            return True
    return False


def fetch_tiktok_from_apify():
    """Apify Actor (clockworks/tiktok-hashtag-scraper) を実行して動画を取得

    【課金事故防止】
    - resultsPerPage は ABSOLUTE_MAX_ITEMS (1) に固定
    - ハッシュタグは SEARCH_HASHTAGS のみ（動的に変更不可）
    - タイムアウトも ACTOR_TIMEOUT_SECS で制限
    - コスト: $0.005/動画 × 約3件 = $0.015/回（Twitter同等）
    """
    print(f"  Apify Actor: {ACTOR_ID}")
    print(f"  検索ハッシュタグ: {', '.join('#'+h for h in SEARCH_HASHTAGS)}")
    print(f"  取得件数上限: {ABSOLUTE_MAX_ITEMS}（ハードリミット）")
    print(f"  予想コスト: ${ABSOLUTE_MAX_ITEMS * 0.005:.4f}")

    if client is None:
        print("  ⚠ Apify クライアント未初期化（トークン未設定）")
        return []

    try:
        # resultsPerPage=1 でタグあたり最小取得（3タグ→計3件、$0.015/回）
        run_input = {
            "hashtags": SEARCH_HASHTAGS,
            "resultsPerPage": 1,
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
            "shouldDownloadSubtitles": False,
            "shouldDownloadSlideshowImages": False,
        }

        # 念のため実行直前に上限を再確認
        assert run_input["resultsPerPage"] <= 3, "resultsPerPage が3を超えています！"

        print(f"  Actor実行中（タイムアウト: {ACTOR_TIMEOUT_SECS}秒）...")
        run = client.actor(ACTOR_ID).call(
            run_input=run_input,
            timeout_secs=ACTOR_TIMEOUT_SECS,
        )

        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        print(f"  → {len(items)}件取得")

        # デバッグ: 生データを保存
        if SAVE_RAW_DEBUG and items:
            with open(DEBUG_RAW_FILE, "w", encoding="utf-8") as f:
                json.dump(items[:5], f, ensure_ascii=False, indent=2, default=str)
            print(f"  → デバッグ用生データ保存: {DEBUG_RAW_FILE}（先頭5件）")

        # 万が一 Actor が上限以上を返した場合は切り捨て
        if len(items) > ABSOLUTE_MAX_ITEMS:
            print(f"  ⚠ 上限超過のため {ABSOLUTE_MAX_ITEMS}件に切り捨て")
            items = items[:ABSOLUTE_MAX_ITEMS]

        # コスト確認
        run_info = client.run(run["id"]).get()
        cost = run_info.get("usageTotalUsd", 0)
        print(f"  → 実際の消費コスト: ${cost:.4f}")

        if cost > 0.03:
            print(f"  ⚠ 警告: コストが$0.03を超えました！")

        return items

    except Exception as e:
        print(f"  ⚠ Apify実行エラー: {e}")
        return []


# =============================================================================
# サムネイル ローカル保存
# =============================================================================

_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/125.0.0.0 Safari/537.36",
    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
    "Referer": "https://www.tiktok.com/",
}


def _download_image(url, dest_path):
    """URLから画像をダウンロードして dest_path に保存。成功なら True。"""
    try:
        req = urllib.request.Request(url, headers=_BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        if len(data) > 100:
            dest_path.write_bytes(data)
            return True
    except Exception:
        pass
    return False


def get_oembed_cover_url(video_url):
    """TikTok oEmbed API からサムネイルURLを取得（公開API・認証不要）"""
    if not video_url:
        return ""
    try:
        oembed_url = "https://www.tiktok.com/oembed?url=" + urllib.parse.quote(video_url, safe=":/")
        req = urllib.request.Request(oembed_url, headers={
            "User-Agent": _BROWSER_HEADERS["User-Agent"],
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return data.get("thumbnail_url", "")
    except Exception:
        return ""


def collect_cover_urls(item):
    """Apifyのアイテムから利用可能なカバーURL候補を全て収集する。
    TikTok の Apify Actor は複数キーに同等のCDN URLを返すことが多いため、
    全候補を順次試すことでダウンロード成功率を上げる。"""
    candidates = []

    def _add(url):
        if url and isinstance(url, str) and url.startswith("http") and url not in candidates:
            candidates.append(url)

    vm = item.get("videoMeta") or {}
    if isinstance(vm, dict):
        for key in ("coverUrl", "originalCoverUrl", "dynamicCoverUrl", "cover"):
            _add(vm.get(key))

    cov = item.get("covers") or {}
    if isinstance(cov, dict):
        for key in ("default", "origin", "dynamic"):
            _add(cov.get(key))

    video_obj = item.get("video")
    if isinstance(video_obj, dict):
        for key in ("cover", "originCover", "dynamicCover"):
            _add(video_obj.get(key))

    _add(item.get("coverUrl"))
    _add(item.get("cover"))

    return candidates


def download_cover(video_id, cover_urls, video_url=""):
    """カバー画像をローカルにダウンロードし、相対パスを返す。
    cover_urls は str または list を受け付け、順番に試行する。
    全URL失敗時は oEmbed API で最新URLを取得して再試行。
    最終的に失敗した場合は空文字を返す。"""
    if isinstance(cover_urls, str):
        cover_urls = [cover_urls] if cover_urls else []
    elif cover_urls is None:
        cover_urls = []

    if not cover_urls and not video_url:
        return ""

    thumbs = Path(THUMBS_DIR)
    thumbs.mkdir(exist_ok=True)
    local_path = thumbs / f"{video_id}.jpeg"

    # 既にダウンロード済みならスキップ
    if local_path.exists() and local_path.stat().st_size > 0:
        return local_path.as_posix()

    # Try 1..N: 候補URLを順次試行（CDN URLは時間制限付きトークンで失効しやすいため複数候補があると有効）
    for url in cover_urls:
        if url and _download_image(url, local_path):
            return local_path.as_posix()

    # Try last: oEmbed API で最新のサムネイルURLを取得してダウンロード
    if video_url:
        fresh_url = get_oembed_cover_url(video_url)
        if fresh_url and _download_image(fresh_url, local_path):
            return local_path.as_posix()

    return ""


def download_covers(videos):
    """動画リスト内の cover をローカルファイルに差し替え。
    失敗時は元のCDN URLを保持し、ブラウザ側のフォールバックを生かす。"""
    ok, fail = 0, 0
    for v in videos:
        cdn_url = v.get("cover", "")
        video_url = v.get("url", "")
        candidates = v.get("cover_candidates") or ([cdn_url] if cdn_url else [])
        if not candidates and not video_url:
            continue
        local = download_cover(v["id"], candidates, video_url)
        if local:
            v["cover"] = local
            ok += 1
        else:
            # 失敗時は CDN URL を維持（ブラウザの onerror フォールバック用）
            fail += 1
    print(f"  → サムネイル保存: 成功 {ok}件 / 失敗 {fail}件")


def retry_missing_thumbnails(conn, limit=80):
    """DBに存在するがローカルサムネイルが無い動画について、
    保存済みCDN URLとoEmbed経由で再ダウンロードを試行する。
    過去に失敗した動画でも、後日 oEmbed が新URLを返せば回収できる。"""
    thumbs = Path(THUMBS_DIR)
    thumbs.mkdir(exist_ok=True)

    rows = conn.execute(
        "SELECT id, url, cover FROM tiktok_videos ORDER BY published DESC, fetched_at DESC"
    ).fetchall()

    missing = []
    for vid, url, cover in rows:
        local_path = thumbs / f"{vid}.jpeg"
        if local_path.exists() and local_path.stat().st_size > 0:
            continue
        missing.append((vid, url, cover))

    if not missing:
        print("  → 全ての動画にローカルサムネイルが揃っています")
        return 0

    target = missing[:limit]
    print(f"  → ローカルサムネイル欠損 {len(missing)}件 / 今回試行 {len(target)}件")

    ok = 0
    for vid, url, cover in target:
        local_path = thumbs / f"{vid}.jpeg"
        # 1) 保存済みCDN URLで試す（まだ失効していなければ成功）
        if cover and cover.startswith("http"):
            if _download_image(cover, local_path):
                conn.execute(
                    "UPDATE tiktok_videos SET cover=? WHERE id=?",
                    (local_path.as_posix(), vid),
                )
                ok += 1
                continue
        # 2) oEmbed で新URLを取得して再試行
        if url:
            fresh = get_oembed_cover_url(url)
            if fresh and _download_image(fresh, local_path):
                conn.execute(
                    "UPDATE tiktok_videos SET cover=? WHERE id=?",
                    (local_path.as_posix(), vid),
                )
                ok += 1
                continue
    conn.commit()
    print(f"  → サムネイル復旧: {ok}/{len(target)}件成功")
    return ok


# =============================================================================
# データ整形
# =============================================================================

def extract_video_data(items):
    """Apifyの生データから必要情報を抽出"""
    videos = {}
    now_iso = datetime.now(timezone.utc).isoformat()
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=SEARCH_DAYS)

    for item in items:
        try:
            video_id = item.get("id", "")
            if not video_id or video_id in videos:
                continue

            # 投稿日の解析
            created = item.get("createTimeISO") or item.get("createTime", "")
            if isinstance(created, (int, float)):
                pub_date = datetime.fromtimestamp(created, tz=timezone.utc)
            elif isinstance(created, str) and created:
                try:
                    pub_date = datetime.fromisoformat(created.replace("Z", "+00:00"))
                except ValueError:
                    continue
            else:
                continue

            if pub_date < cutoff_date:
                continue

            # 動画情報
            desc = item.get("text", "") or item.get("desc", "") or ""
            author_info = item.get("authorMeta", {}) or {}
            if not author_info:
                author_info = {
                    "name": item.get("author", {}).get("uniqueId", "")
                        if isinstance(item.get("author"), dict)
                        else item.get("author", ""),
                    "nickName": item.get("author", {}).get("nickname", "")
                        if isinstance(item.get("author"), dict)
                        else "",
                    "fans": item.get("authorStats", {}).get("followerCount", 0)
                        if isinstance(item.get("authorStats"), dict)
                        else 0,
                }

            username = author_info.get("name", "") or author_info.get("uniqueId", "") or ""
            nickname = author_info.get("nickName", "") or author_info.get("nickname", "") or ""
            display_name = nickname if nickname else username
            followers = author_info.get("fans", 0) or author_info.get("followers", 0) or 0

            # stats
            stats = item.get("videoMeta", {}) or {}
            play_count = item.get("playCount", 0) or stats.get("playCount", 0) or item.get("stats", {}).get("playCount", 0) or 0
            like_count = item.get("diggCount", 0) or item.get("likes", 0) or item.get("stats", {}).get("diggCount", 0) or 0
            comment_count = item.get("commentCount", 0) or item.get("comments", 0) or item.get("stats", {}).get("commentCount", 0) or 0

            # ハッシュタグ
            hashtags = []
            for h in item.get("hashtags", []) or []:
                if isinstance(h, dict):
                    hashtags.append(h.get("name", ""))
                elif isinstance(h, str):
                    hashtags.append(h)

            # カバー画像（候補URLを全て収集して後で順次試行）
            cover_candidates = collect_cover_urls(item)
            cover = cover_candidates[0] if cover_candidates else ""

            # URL
            url = item.get("webVideoUrl", "") or f"https://www.tiktok.com/@{username}/video/{video_id}"

            videos[video_id] = {
                "id": video_id,
                "title": desc[:200] if desc else "(説明なし)",
                "author": display_name,
                "username": username,
                "followers": int(followers) if followers else 0,
                "views": int(play_count) if play_count else 0,
                "likes": int(like_count) if like_count else 0,
                "comments": int(comment_count) if comment_count else 0,
                "hashtags": hashtags,
                "published": pub_date.strftime("%Y-%m-%d"),
                "url": url,
                "cover": cover,
                "cover_candidates": cover_candidates,  # 永続化されないが download_covers で使う
                "fetched_at": now_iso,
            }

        except Exception as e:
            continue

    return list(videos.values())


# =============================================================================
# フィルタ・ランキング
# =============================================================================

def is_ng(video):
    """ブラックリストチェック + 日本語フィルタ"""
    title = video.get("title", "")
    # ハッシュタグ部分を除去して本文だけで判定
    title_no_tags = re.sub(r'#\S+', '', title).strip()
    if not contains_japanese(title_no_tags):
        return True

    if "切り抜き" in video.get("author", ""):
        return True

    check_text = " ".join([
        video.get("title", ""),
        video.get("author", ""),
        video.get("username", ""),
        " ".join(video.get("hashtags", [])) if isinstance(video.get("hashtags"), list) else "",
    ]).lower()

    for kw in NG_KEYWORDS:
        if kw.lower() in check_text:
            return True

    return False


def filter_and_rank(videos):
    """条件フィルタ＆ランキング生成"""
    filtered = []
    ng_count = 0

    for v in videos:
        if is_ng(v):
            ng_count += 1
            continue

        if v["followers"] < MIN_FOLLOWERS or v["followers"] > MAX_FOLLOWERS:
            continue

        if v["views"] < v["followers"] * VIEW_MULTIPLIER:
            continue

        if v["comments"] < MIN_COMMENTS:
            continue

        v["growth_rate"] = round(v["views"] / max(v["followers"], 1), 1)
        filtered.append(v)

    print(f"  → ブラックリスト除外: {ng_count}件")
    print(f"  → 条件クリア: {len(filtered)}件")

    filtered.sort(key=lambda x: x["growth_rate"], reverse=True)
    return filtered


# =============================================================================
# 出力
# =============================================================================

def save_csv(results):
    """CSV出力"""
    if not results:
        return

    with open(CSV_FILE, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "順位", "動画説明", "アカウント名", "フォロワー数",
            "再生数", "いいね数", "コメント数", "バズ倍率",
            "投稿日", "動画URL",
        ])

        for i, r in enumerate(results, 1):
            url_formula = f'=HYPERLINK("{r["url"]}", "リンク")'
            writer.writerow([
                i,
                r["title"][:100],
                r["author"],
                r["followers"],
                r["views"],
                r["likes"],
                r["comments"],
                f'{r["growth_rate"]}x',
                r["published"],
                url_formula,
            ])

    print(f"CSV出力: {CSV_FILE}")


def save_history(results):
    """履歴JSON保存"""
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
            "author": r["author"],
            "username": r["username"],
            "followers": r["followers"],
            "views": r["views"],
            "likes": r["likes"],
            "comments": r["comments"],
            "growth_rate": r["growth_rate"],
            "published": r["published"],
            "url": r["url"],
            "cover": r.get("cover", ""),
        }
        for i, r in enumerate(results, 1)
    ]

    # 過去の履歴でCDN URLが残っている場合、ローカル保存を試行して差し替え
    # ダウンロード失敗時は CDN URL を維持し、ブラウザ側 (onerror data-fallback) の救済に委ねる
    for date_key, entries in history.items():
        for entry in entries:
            cover = entry.get("cover", "")
            if cover.startswith("http"):
                video_url = entry.get("url", "")
                m = re.search(r'/video/(\d+)', video_url)
                if m:
                    vid = m.group(1)
                    local = download_cover(vid, cover, video_url)
                    if local:
                        entry["cover"] = local  # 成功時のみローカルパスに差し替え

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    total_days = len(history)
    print(f"履歴保存: {HISTORY_FILE} ({today}, 累計{total_days}日分)")


def display_results(results):
    """コンソール表示"""
    if not results:
        print("\n条件を満たす動画が見つかりませんでした。")
        return

    print(f"\n{'='*110}")
    print(f"  TikTok VTuber バズランキング（上位 {len(results)} 件）")
    print(f"  条件: フォロワー {MIN_FOLLOWERS:,}〜{MAX_FOLLOWERS:,}人 / "
          f"再生数≧フォロワー×{VIEW_MULTIPLIER} / コメント≧{MIN_COMMENTS}")
    print(f"{'='*110}")
    print(f"{'順位':>4}  {'動画説明':<40}  {'アカウント':<20}  "
          f"{'フォロワー':>10}  {'再生数':>12}  {'倍率':>8}  {'コメント':>6}  {'投稿日':<10}")
    print("-" * 110)

    for i, r in enumerate(results, 1):
        title = r["title"][:38].replace("\n", " ")
        author = r["author"][:18]
        print(
            f"{i:>4}  {title:<40}  {author:<20}  "
            f"{r['followers']:>10,}  {r['views']:>12,}  "
            f"{r['growth_rate']:>7.1f}x  {r['comments']:>6,}  {r['published']:<10}"
        )

    print(f"\n各動画のURL:")
    for i, r in enumerate(results, 1):
        print(f"  {i}. {r['url']}")


# =============================================================================
# メイン
# =============================================================================

def main():
    print("TikTok VTuber バズランキングツール")
    print("=" * 50)
    print(f"検索ハッシュタグ: {SEARCH_HASHTAGS}")
    print(f"取得上限 : {ABSOLUTE_MAX_ITEMS}件（ハードリミット）")
    print(f"検索期間: 直近{SEARCH_DAYS}日間")
    print(f"除外キーワード: {NG_KEYWORDS}")

    if DRY_RUN:
        print("\n[DRY RUN] Apify は呼び出さず、DB の既存データを表示します。")

    # 1. DB初期化
    print(f"\n[1/4] データベース初期化中...")
    conn = init_db()
    total, last_fetch = get_db_stats(conn)
    print(f"  → {DB_FILE} 準備完了（既存 {total}件, 最終取得: {last_fetch}）")

    if not DRY_RUN:
        # 2. Apify実行
        print(f"\n[2/4] Apifyで動画を取得中...")
        raw_items = fetch_tiktok_from_apify()

        if raw_items:
            # 3. データ整形 & DB保存
            print(f"\n[3/4] データを整形・DB保存中...")
            videos = extract_video_data(raw_items)
            print(f"  → ユニーク動画数: {len(videos)}件（期間内）")

            if videos:
                # サムネイルをローカルに保存（CDN URLは期限切れになるため）
                print(f"  サムネイルをダウンロード中...")
                download_covers(videos)
                upsert_videos(conn, videos)
                total_after, _ = get_db_stats(conn)
                new_count = total_after - total
                updated_count = len(videos) - new_count
                print(f"  → 新規追加: {new_count}件 / 既存更新: {updated_count}件")
        else:
            print(f"\n[2/4] 取得データが0件でした（DB既存データでランキング生成します）")
            print(f"\n[3/4] スキップ（新規データなし）")

    else:
        print(f"\n[2/4] スキップ（DRY RUN）")
        print(f"\n[3/4] スキップ（DRY RUN）")

    # 4. DB全データからフィルタ & ランキング生成
    print(f"\n[4/4] DB全データからランキング生成中...")
    all_videos = load_videos_from_db(conn)
    print(f"  → DB内の直近{SEARCH_DAYS}日分: {len(all_videos)}件")

    # ローカルサムネイルが無い動画について再取得を試行
    # 1) 保存済みCDN URL → 2) oEmbed API（公開・無料、Apify課金は発生しない）
    print(f"  欠損サムネイルの復旧を試行中...")
    retry_missing_thumbnails(conn)

    # DBの最新状態を再読み込み（cover欄がローカルパスに更新された可能性があるため）
    all_videos = load_videos_from_db(conn)

    conn.close()

    results = filter_and_rank(all_videos)

    # 出力
    display_results(results)
    save_csv(results)
    save_history(results)

    print(f"\n完了!")


if __name__ == "__main__":
    main()
