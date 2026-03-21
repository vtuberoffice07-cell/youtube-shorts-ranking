"""
TikTok VTuber バズランキング取得ツール
Apify の TikTok Scraper を使用してデータ取得
"""

import csv
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

from apify_client import ApifyClient
from dotenv import load_dotenv

# Windows cp932 で出力エラーを防ぐ
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

load_dotenv()

APIFY_TOKEN = os.getenv("APIFY_API_TOKEN")
if not APIFY_TOKEN:
    print("エラー: .env ファイルに APIFY_API_TOKEN を設定してください。")
    sys.exit(1)

client = ApifyClient(APIFY_TOKEN)

# --- フィルタ条件 ---
SEARCH_HASHTAGS = ["vtuber", "新人vtuber", "個人vtuber", "vtuber準備中"]
MIN_FOLLOWERS = 500
MAX_FOLLOWERS = 100000
VIEW_MULTIPLIER = 2  # 再生数 >= フォロワー数 × この値
MIN_COMMENTS = 10
SEARCH_DAYS = 30  # 直近何日間

# --- NGキーワード ---
NG_KEYWORDS = [
    "切り抜き", "まとめ", "速報", "手書き", "反応",
    "ホロライブ", "hololive", "にじさんじ", "nijisanji",
    "ぶいすぽ", "ネオポルテ",
]

HISTORY_FILE = "tiktok_history.json"
CSV_FILE = "tiktok_output.csv"


def search_tiktok_hashtag(hashtag, max_results=100):
    """Apify TikTok Scraper でハッシュタグ検索"""
    print(f"  検索中: #{hashtag} (最大{max_results}件)...")

    try:
        run_input = {
            "hashtags": [hashtag],
            "resultsPerPage": max_results,
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
            "shouldDownloadSubtitles": False,
            "shouldDownloadSlideshowImages": False,
        }

        # clockworks/free-tiktok-scraper を使用
        run = client.actor("clockworks/free-tiktok-scraper").call(
            run_input=run_input,
            timeout_secs=300,
        )

        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        print(f"    → {len(items)}件取得")
        return items

    except Exception as e:
        print(f"    ⚠ エラー: {e}")
        return []


def extract_video_data(items):
    """Apifyの生データから必要情報を抽出"""
    videos = {}
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

            # カバー画像
            cover = item.get("videoMeta", {}).get("coverUrl", "") or item.get("covers", {}).get("default", "") or ""
            if not cover:
                cover = item.get("video", {}).get("cover", "") if isinstance(item.get("video"), dict) else ""

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
            }

        except Exception as e:
            continue

    return list(videos.values())


def is_ng(video):
    """ブラックリストチェック"""
    # チャンネル名に「切り抜き」が含まれていたら除外
    if "切り抜き" in video.get("author", ""):
        return True

    # タイトル・アカウント名・ハッシュタグにNGキーワードが含まれていたら除外
    check_text = " ".join([
        video.get("title", ""),
        video.get("author", ""),
        video.get("username", ""),
        " ".join(video.get("hashtags", [])),
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
        # ブラックリスト除外
        if is_ng(v):
            ng_count += 1
            continue

        # フォロワー数チェック
        if v["followers"] < MIN_FOLLOWERS or v["followers"] > MAX_FOLLOWERS:
            continue

        # 再生数チェック
        if v["views"] < v["followers"] * VIEW_MULTIPLIER:
            continue

        # コメント数チェック
        if v["comments"] < MIN_COMMENTS:
            continue

        # バズ倍率
        v["growth_rate"] = round(v["views"] / max(v["followers"], 1), 1)
        filtered.append(v)

    print(f"  → ブラックリスト除外: {ng_count}件")
    print(f"  → 条件クリア: {len(filtered)}件")

    # バズ倍率でソート
    filtered.sort(key=lambda x: x["growth_rate"], reverse=True)

    return filtered


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


def main():
    print("TikTok VTuber バズランキングツール")
    print("=" * 50)
    print(f"検索ハッシュタグ: {SEARCH_HASHTAGS}")
    print(f"検索期間: 直近{SEARCH_DAYS}日間")
    print(f"除外キーワード: {NG_KEYWORDS}")

    # 1. ハッシュタグ検索
    print(f"\n[1/3] TikTok動画を検索中...")
    all_items = []
    for tag in SEARCH_HASHTAGS:
        items = search_tiktok_hashtag(tag, max_results=100)
        all_items.extend(items)

    print(f"\n  合計取得: {len(all_items)}件（重複含む）")

    # 2. データ整形
    print(f"\n[2/3] 動画データを整形中...")
    videos = extract_video_data(all_items)
    print(f"  → ユニーク動画数: {len(videos)}件（期間内）")

    # 3. フィルタ＆ランキング
    print(f"\n[3/3] フィルタリング & ランキング生成中...")
    results = filter_and_rank(videos)

    # 出力
    display_results(results)
    save_csv(results)
    save_history(results)


if __name__ == "__main__":
    main()
