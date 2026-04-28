"""
VTuber バズツイートランキング取得ツール
Apify の Tweet Scraper を使用してデータ取得 → SQLite に UPSERT 保存

使い方:
  python tweet_ranking.py            # 通常実行
  python tweet_ranking.py --dry      # Apify を呼ばず DB の既存データでランキング表示
  python tweet_ranking.py --debug    # 通常実行 + 生データを tweet_raw_debug.json に保存
"""

import csv
import io
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

from apify_client import ApifyClient
from dotenv import load_dotenv

from vtuber_common import write_latest_snapshot

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
SEARCH_QUERY = "(#VTuber OR #新人Vtuber OR #個人Vtuber OR 個人勢 OR Vtuber準備中) -ホロライブ -にじさんじ -ぶいすぽ -hololive -nijisanji -vspo -あおぎり -ネオポルテ -ななしいんく min_faves:500 lang:ja -filter:replies"
# 取得上限: 50件固定（$0.00025/tweet × 50 = $0.0125/回。無料枠$5/月で毎日実行可能）
ABSOLUTE_MAX_ITEMS = 50
ACTOR_ID = "kaitoeasyapi/twitter-x-data-tweet-scraper-pay-per-result-cheapest"
ACTOR_TIMEOUT_SECS = 300  # Actor 実行タイムアウト（5分）

# デバッグ: 生データを保存して構造を確認できるようにする
DEBUG_RAW_FILE = "tweet_raw_debug.json"
SAVE_RAW_DEBUG = "--debug" in sys.argv

DB_FILE = "tweets.db"
HISTORY_FILE = "tweet_history.json"
LATEST_FILE = "tweet_latest.json"  # viewer.html の初期表示用（直近30日分）
LATEST_DAYS = 30
CSV_FILE = "tweet_output.csv"


# =============================================================================
# SQLite データベース操作
# =============================================================================

def init_db():
    """SQLite データベースとテーブルを初期化"""
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tweets (
            id              TEXT PRIMARY KEY,
            text            TEXT NOT NULL DEFAULT '',
            author_name     TEXT NOT NULL DEFAULT '',
            author_username TEXT NOT NULL DEFAULT '',
            author_icon_url TEXT NOT NULL DEFAULT '',
            like_count      INTEGER NOT NULL DEFAULT 0,
            retweet_count   INTEGER NOT NULL DEFAULT 0,
            reply_count     INTEGER NOT NULL DEFAULT 0,
            quote_count     INTEGER NOT NULL DEFAULT 0,
            bookmark_count  INTEGER NOT NULL DEFAULT 0,
            impression_count INTEGER NOT NULL DEFAULT 0,
            posted_at       TEXT NOT NULL DEFAULT '',
            tweet_url       TEXT NOT NULL DEFAULT '',
            media_urls      TEXT NOT NULL DEFAULT '[]',
            fetched_at      TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tweets_like_count ON tweets(like_count DESC)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tweets_posted_at ON tweets(posted_at DESC)
    """)
    # 既存DBへ quoted_json カラム追加（引用元ツイートのJSON保存用、未追加なら追加）
    cols = {row[1] for row in conn.execute("PRAGMA table_info(tweets)").fetchall()}
    if "quoted_json" not in cols:
        conn.execute("ALTER TABLE tweets ADD COLUMN quoted_json TEXT NOT NULL DEFAULT ''")
    conn.commit()
    return conn


def upsert_tweet(conn, tweet):
    """ツイートを UPSERT（存在すれば更新、なければ挿入）"""
    quoted_obj = tweet.get("quoted") or None
    quoted_json = json.dumps(quoted_obj, ensure_ascii=False) if quoted_obj else ""
    conn.execute("""
        INSERT INTO tweets (
            id, text, author_name, author_username, author_icon_url,
            like_count, retweet_count, reply_count, quote_count,
            bookmark_count, impression_count,
            posted_at, tweet_url, media_urls, fetched_at, quoted_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            text = excluded.text,
            author_name = excluded.author_name,
            author_username = excluded.author_username,
            author_icon_url = excluded.author_icon_url,
            like_count = excluded.like_count,
            retweet_count = excluded.retweet_count,
            reply_count = excluded.reply_count,
            quote_count = excluded.quote_count,
            bookmark_count = excluded.bookmark_count,
            impression_count = excluded.impression_count,
            posted_at = excluded.posted_at,
            tweet_url = excluded.tweet_url,
            media_urls = excluded.media_urls,
            fetched_at = excluded.fetched_at,
            quoted_json = excluded.quoted_json
    """, (
        tweet["id"],
        tweet["text"],
        tweet["author_name"],
        tweet["author_username"],
        tweet["author_icon_url"],
        tweet["like_count"],
        tweet["retweet_count"],
        tweet["reply_count"],
        tweet["quote_count"],
        tweet["bookmark_count"],
        tweet["impression_count"],
        tweet["posted_at"],
        tweet["tweet_url"],
        json.dumps(tweet["media_urls"], ensure_ascii=False),
        tweet["fetched_at"],
        quoted_json,
    ))


def upsert_tweets(conn, tweets):
    """複数ツイートを一括 UPSERT"""
    for tweet in tweets:
        upsert_tweet(conn, tweet)
    conn.commit()
    print(f"  → DB保存完了: {len(tweets)}件を tweets テーブルに UPSERT")


# =============================================================================
# Apify データ取得
# =============================================================================

def fetch_tweets_from_apify():
    """Apify Actor (kaitoeasyapi/twitter-x-data-tweet-scraper) を実行してツイートを取得

    【課金事故防止】
    - maxItems は ABSOLUTE_MAX_ITEMS (50) に固定
    - twitterContent は定数 SEARCH_QUERY のみ（動的に変更不可）
    - タイムアウトも ACTOR_TIMEOUT_SECS で制限
    - コスト: $0.00025/tweet × 50件 = $0.0125/回（無料枠 $5/月 で毎日実行可能）
    """
    print(f"  Apify Actor: {ACTOR_ID}")
    print(f"  検索クエリ: {SEARCH_QUERY}")
    print(f"  取得件数上限: {ABSOLUTE_MAX_ITEMS}（ハードリミット）")

    if client is None:
        print("  ⚠ Apify クライアント未初期化（トークン未設定）")
        return []

    try:
        # ─── kaitoeasyapi 用リクエストパラメータ（値を変更しないこと） ───
        # queryType="Latest": 検索クエリに合致する最新ツイートを取得
        # min_faves:500 はクエリ文字列側で効くので、最新かつ人気のツイートが取れる
        # 1日2回の実行で直近24時間の投稿を確実にDBへ蓄積し、日次ランキング(get_top_tweets_last_24h)を機能させる
        run_input = {
            "twitterContent": SEARCH_QUERY,
            "maxItems": ABSOLUTE_MAX_ITEMS,   # 50件固定
            "queryType": "Latest",            # 最新順（直近の人気ツイートをDBへ蓄積するため）
            "lang": "ja",
            "min_faves": 500,                 # いいね500以上（個人勢向けに閾値を下げる）
        }

        # 念のため実行直前に上限を再確認
        assert run_input["maxItems"] <= 50, "maxItems が50を超えています！"

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

        return items

    except Exception as e:
        print(f"  ⚠ Apify実行エラー: {e}")
        return []


# =============================================================================
# データ整形
# =============================================================================

def _safe_int(value):
    """安全に整数変換（None, 空文字, 文字列すべて対応）"""
    if value is None:
        return 0
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def _parse_date(raw_date):
    """さまざまな日時フォーマットをISO形式に変換"""
    if not raw_date:
        return ""
    if isinstance(raw_date, (int, float)):
        return datetime.fromtimestamp(raw_date, tz=timezone.utc).isoformat()
    raw_date = str(raw_date)
    # Twitter API形式: "Wed Oct 10 20:19:24 +0000 2018"
    try:
        return datetime.strptime(raw_date, "%a %b %d %H:%M:%S %z %Y").isoformat()
    except ValueError:
        pass
    # ISO形式
    try:
        return datetime.fromisoformat(raw_date.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return raw_date


def _extract_media_urls(item):
    """ツイートからメディアURLリストを取り出す（kaitoeasyapi形式とフォールバック対応）"""
    media_urls = []
    ext_entities = item.get("extendedEntities") or {}
    media_list = ext_entities.get("media") or []
    if not media_list:
        media_list = item.get("media") or []
    for m in media_list:
        if isinstance(m, dict):
            u = m.get("media_url_https", "") or m.get("url", "")
            if u:
                media_urls.append(u)
        elif isinstance(m, str):
            media_urls.append(m)
    return media_urls


def _extract_quoted(quoted_obj):
    """quoted_tweet 構造から表示に必要なフィールドだけを取り出す。
    quoted が無ければ None を返す。"""
    if not isinstance(quoted_obj, dict):
        return None
    q_author = quoted_obj.get("author") or {}
    q_id = str(quoted_obj.get("id", "") or "")
    q_text = quoted_obj.get("text", "") or ""
    q_author_name = q_author.get("name", "") or ""
    q_author_username = q_author.get("userName", "") or ""
    q_author_icon = q_author.get("profilePicture", "") or ""
    q_url = (
        quoted_obj.get("url")
        or quoted_obj.get("twitterUrl")
        or (f"https://x.com/{q_author_username}/status/{q_id}" if q_id and q_author_username else "")
    )
    if not (q_text or q_author_username):
        return None
    return {
        "id": q_id,
        "text": q_text,
        "author_name": q_author_name,
        "author_username": q_author_username,
        "author_icon_url": q_author_icon,
        "url": q_url,
        "media_urls": _extract_media_urls(quoted_obj),
    }


def extract_tweet_data(items):
    """Apify (kaitoeasyapi/twitter-x-data-tweet-scraper) の生データを整形

    出力フィールド:
      id, url, text, createdAt（Twitter形式）,
      likeCount, retweetCount, replyCount, quoteCount, viewCount, bookmarkCount,
      author: { name, userName, profilePicture, ... }
      media: [ { media_url_https, type, ... } ]（オプション）
    """
    tweets = {}
    now_iso = datetime.now(timezone.utc).isoformat()
    skipped = 0

    for item in items:
        try:
            # noResults / エラーレスポンスはスキップ
            if item.get("noResults") or item.get("type") not in (None, "tweet"):
                if item.get("type") not in (None, "tweet"):
                    skipped += 1
                    continue

            # --- ツイートID ---
            tweet_id = str(item.get("id", ""))
            if not tweet_id or tweet_id in tweets:
                continue

            # --- テキスト ---
            text = item.get("text", "") or ""

            # --- ユーザー情報（author ネスト） ---
            author = item.get("author", {}) or {}

            author_name = author.get("name", "") or ""
            author_username = author.get("userName", "") or ""
            author_icon = author.get("profilePicture", "") or ""

            # --- エンゲージメント（キャメルケース） ---
            like_count = _safe_int(item.get("likeCount", 0))
            retweet_count = _safe_int(item.get("retweetCount", 0))
            reply_count = _safe_int(item.get("replyCount", 0))
            quote_count = _safe_int(item.get("quoteCount", 0))
            bookmark_count = _safe_int(item.get("bookmarkCount", 0))
            impression_count = _safe_int(item.get("viewCount", 0))

            # --- 投稿日時 ---
            posted_at = _parse_date(item.get("createdAt", ""))

            # --- ツイートURL ---
            tweet_url = item.get("url", "") or ""
            if not tweet_url and author_username and tweet_id:
                tweet_url = f"https://x.com/{author_username}/status/{tweet_id}"

            # --- メディアURL ---
            # kaitoeasyapi形式: extendedEntities.media[].media_url_https
            media_urls = _extract_media_urls(item)

            # --- 引用ツイート（quoted_tweet があれば抽出） ---
            quoted_data = _extract_quoted(item.get("quoted_tweet"))

            tweets[tweet_id] = {
                "id": tweet_id,
                "text": text,
                "author_name": author_name,
                "author_username": author_username,
                "author_icon_url": author_icon,
                "like_count": like_count,
                "retweet_count": retweet_count,
                "reply_count": reply_count,
                "quote_count": quote_count,
                "bookmark_count": bookmark_count,
                "impression_count": impression_count,
                "posted_at": posted_at,
                "tweet_url": tweet_url,
                "media_urls": media_urls,
                "quoted": quoted_data,
                "fetched_at": now_iso,
            }

        except Exception as e:
            skipped += 1
            continue

    if skipped:
        print(f"  → スキップ: {skipped}件（無効データ）")

    return list(tweets.values())


# =============================================================================
# ランキング・出力
# =============================================================================

def rank_tweets(tweets):
    """いいね数でソートしてランキング"""
    tweets.sort(key=lambda x: x["like_count"], reverse=True)
    return tweets


def save_csv(tweets):
    """CSV出力"""
    if not tweets:
        return

    with open(CSV_FILE, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "順位", "投稿者名", "@ユーザー名", "ツイート本文",
            "いいね数", "RT数", "引用数", "リプライ数",
            "インプレッション数", "投稿日時", "URL",
        ])

        for i, t in enumerate(tweets, 1):
            url_formula = f'=HYPERLINK("{t["tweet_url"]}", "リンク")'
            text_short = t["text"][:100].replace("\n", " ")
            writer.writerow([
                i,
                t["author_name"],
                f'@{t["author_username"]}',
                text_short,
                t["like_count"],
                t["retweet_count"],
                t["quote_count"],
                t["reply_count"],
                t["impression_count"],
                t["posted_at"][:10] if t["posted_at"] else "",
                url_formula,
            ])

    print(f"CSV出力: {CSV_FILE}")


def save_history(tweets):
    """履歴JSON保存"""
    if not tweets:
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
            "text": t["text"],   # 全文を保存（過去の[:200]切り詰めをやめる）
            "author_name": t["author_name"],
            "author_username": t["author_username"],
            "author_icon_url": t["author_icon_url"],
            "like_count": t["like_count"],
            "retweet_count": t["retweet_count"],
            "reply_count": t["reply_count"],
            "quote_count": t["quote_count"],
            "impression_count": t["impression_count"],
            "posted_at": t["posted_at"],
            "url": t["tweet_url"],
            "media_urls": t["media_urls"],
            "quoted": t.get("quoted"),  # 引用元ツイート（無ければ null）
        }
        for i, t in enumerate(tweets, 1)
    ]

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    total_days = len(history)
    print(f"履歴保存: {HISTORY_FILE} ({today}, 累計{total_days}日分)")

    # 軽量版 (latest) を生成。viewer.html の初期表示高速化用
    latest_count = write_latest_snapshot(history, LATEST_FILE, days=LATEST_DAYS)
    print(f"  → {LATEST_FILE} に直近{LATEST_DAYS}日分を出力 ({latest_count}日分)")


def display_results(tweets):
    """コンソール表示"""
    if not tweets:
        print("\n条件を満たすツイートが見つかりませんでした。")
        return

    print(f"\n{'='*120}")
    print(f"  VTuber バズツイートランキング（上位 {len(tweets)} 件）")
    print(f"  条件: いいね500以上 / 個人勢・新人特化 / 大手事務所除外 / リプライ除外")
    print(f"{'='*120}")
    print(f"{'順位':>4}  {'投稿者':<20}  {'ツイート本文':<50}  "
          f"{'いいね':>8}  {'RT':>8}  {'インプ':>10}  {'投稿日':<10}")
    print("-" * 120)

    for i, t in enumerate(tweets, 1):
        author = t["author_name"][:18]
        text = t["text"][:48].replace("\n", " ")
        posted = t["posted_at"][:10] if t["posted_at"] else "N/A"
        print(
            f"{i:>4}  {author:<20}  {text:<50}  "
            f"{t['like_count']:>8,}  {t['retweet_count']:>8,}  "
            f"{t['impression_count']:>10,}  {posted:<10}"
        )

    print(f"\n各ツイートのURL:")
    for i, t in enumerate(tweets, 1):
        print(f"  {i}. {t['tweet_url']}")


# =============================================================================
# メイン
# =============================================================================

def get_db_stats(conn):
    """DB内のツイート件数と最新取得日時を返す"""
    row = conn.execute("SELECT COUNT(*), MAX(fetched_at) FROM tweets").fetchone()
    return row[0] or 0, row[1] or "N/A"


def get_top_tweets_last_24h(conn, limit=ABSOLUTE_MAX_ITEMS):
    """直近24時間に投稿されたツイートを like_count 降順で取得する。
    日次のスナップショットランキングを生成するための関数。
    posted_at は ISO8601（UTC）で保存されているので文字列比較で時系列判定可能。"""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    rows = conn.execute(
        "SELECT id, text, author_name, author_username, author_icon_url, "
        "like_count, retweet_count, reply_count, quote_count, "
        "bookmark_count, impression_count, posted_at, tweet_url, "
        "media_urls, fetched_at, quoted_json FROM tweets "
        "WHERE posted_at >= ? "
        "ORDER BY like_count DESC LIMIT ?",
        (cutoff, limit),
    ).fetchall()
    result = []
    for r in rows:
        quoted = None
        if r[15]:
            try:
                quoted = json.loads(r[15])
            except (json.JSONDecodeError, TypeError):
                quoted = None
        result.append({
            "id": r[0], "text": r[1], "author_name": r[2],
            "author_username": r[3], "author_icon_url": r[4],
            "like_count": r[5], "retweet_count": r[6],
            "reply_count": r[7], "quote_count": r[8],
            "bookmark_count": r[9], "impression_count": r[10],
            "posted_at": r[11], "tweet_url": r[12],
            "media_urls": json.loads(r[13]), "fetched_at": r[14],
            "quoted": quoted,
        })
    return result


def main():
    print("VTuber バズツイートランキングツール")
    print("=" * 50)
    print(f"検索クエリ: {SEARCH_QUERY}")
    print(f"取得上限 : {ABSOLUTE_MAX_ITEMS}件（ハードリミット）")

    if DRY_RUN:
        print("\n[DRY RUN] Apify は呼び出さず、DB の既存データを表示します。")

    # 1. DB初期化
    print(f"\n[1/5] データベース初期化中...")
    conn = init_db()
    total, last_fetch = get_db_stats(conn)
    print(f"  → {DB_FILE} 準備完了（既存 {total}件, 最終取得: {last_fetch}）")

    if not DRY_RUN:
        # 2. Apify実行
        print(f"\n[2/5] Apifyでツイートを取得中...")
        raw_items = fetch_tweets_from_apify()

        if raw_items:
            # 3. データ整形
            print(f"\n[3/5] データを整形中...")
            tweets = extract_tweet_data(raw_items)
            print(f"  → ユニークツイート数: {len(tweets)}件")

            # 4. DB保存（UPSERT: 既存ツイートはいいね数・RT数等を最新値に更新）
            print(f"\n[4/5] データベースに保存中...")
            upsert_tweets(conn, tweets)

            # 保存後の統計
            total_after, _ = get_db_stats(conn)
            new_count = total_after - total
            updated_count = len(tweets) - new_count
            print(f"  → 新規追加: {new_count}件 / 既存更新: {updated_count}件")
        else:
            print("\n[2/5] 取得データが0件でした（DB既存データで日次ランキング生成します）")
            print("\n[3/5] スキップ（新規データなし）")
            print("\n[4/5] スキップ（新規データなし）")
    else:
        print(f"\n[2/5] スキップ（DRY RUN）")
        print(f"\n[3/5] スキップ（DRY RUN）")
        print(f"\n[4/5] スキップ（DRY RUN）")

    # 5. 直近24時間に投稿されたツイートのみで日次ランキングを生成
    print(f"\n[5/5] 直近24時間の日次ランキングを生成中...")
    ranked = get_top_tweets_last_24h(conn)
    print(f"  → 直近24h投稿の対象: {len(ranked)}件（上位 {ABSOLUTE_MAX_ITEMS} 件まで）")

    conn.close()

    # ランキング表示 & ファイル出力（直近24時間のスナップショット）
    display_results(ranked)
    save_csv(ranked)
    save_history(ranked)

    print(f"\n完了!")


if __name__ == "__main__":
    main()
