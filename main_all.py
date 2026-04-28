"""
YouTube Shorts VTuber バズランキング（全体版）
事務所系NGワードを除外しない版。その他のフィルタ条件はmain.pyと同一。
出力: ranking_all_history.json / ranking_all_output.csv
"""
import csv
import io
import json
import os
import re
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

API_KEY = os.getenv("YOUTUBE_API_KEY")
if not API_KEY or API_KEY == "YOUR_API_KEY_HERE":
    print("エラー: .env ファイルに YOUTUBE_API_KEY を設定してください。")
    sys.exit(1)

youtube = build("youtube", "v3", developerKey=API_KEY)

# クォータロガー用カウンタ
_QUOTA_COUNTS = {"search_list": 0, "videos_list": 0, "channels_list": 0}

# --- フィルタ条件（main.pyと同一） ---
SEARCH_QUERIES = [
    "#vtuber", "新人Vtuber", "個人Vtuber", "Vtuber準備中",
    "VTuber shorts", "個人勢VTuber", "#新人vtuber", "VTuberデビュー",
]
MIN_SUBSCRIBERS = 500
MAX_SUBSCRIBERS = 100000
VIEW_MULTIPLIER = 3
MIN_COMMENTS = 10
MIN_DURATION_SEC = 5
MAX_DURATION_SEC = 60
SEARCH_DAYS = 2  # 横動画追加に伴うクォータ節約のため 3→2 に短縮
DAYS_PER_CHUNK = 1
MAX_RESULTS_PER_QUERY = 50

# --- NGキーワード（事務所系を除外 = 切り抜き・まとめ系のみ） ---
NG_KEYWORDS = [
    "切り抜き", "まとめ", "速報", "手書き", "反応",
]


def contains_ng_keyword(text):
    """このスクリプトの NG_KEYWORDS で判定する薄いラッパー。"""
    return _common_contains_ng_keyword(text, NG_KEYWORDS)


def search_shorts():
    seen = set()
    video_ids = []
    now = datetime.now(timezone.utc)
    api_calls = 0

    chunks = []
    for start_offset in range(SEARCH_DAYS, 0, -DAYS_PER_CHUNK):
        end_offset = max(start_offset - DAYS_PER_CHUNK, 0)
        chunk_start = now - timedelta(days=start_offset)
        chunk_end = now - timedelta(days=end_offset)
        chunks.append((chunk_start, chunk_end))

    total_tasks = len(SEARCH_QUERIES) * len(chunks)
    completed = 0

    for query in SEARCH_QUERIES:
        for chunk_start, chunk_end in chunks:
            published_after = chunk_start.strftime("%Y-%m-%dT%H:%M:%SZ")
            published_before = chunk_end.strftime("%Y-%m-%dT%H:%M:%SZ")
            completed += 1
            progress = f"[{completed}/{total_tasks}]"

            try:
                request = youtube.search().list(
                    q=query,
                    type="video",
                    videoDuration="short",
                    publishedAfter=published_after,
                    publishedBefore=published_before,
                    order="viewCount",
                    part="id",
                    maxResults=MAX_RESULTS_PER_QUERY,
                )
                response = request.execute()
                api_calls += 1
                _QUOTA_COUNTS["search_list"] += 1

                new_count = 0
                for item in response.get("items", []):
                    vid = item["id"]["videoId"]
                    if vid not in seen:
                        seen.add(vid)
                        video_ids.append(vid)
                        new_count += 1

                if new_count > 0:
                    date_range = f"{chunk_start.strftime('%m/%d')}~{chunk_end.strftime('%m/%d')}"
                    print(f"  {progress} \"{query}\" {date_range}: +{new_count}件 (累計{len(video_ids)})")

            except Exception as e:
                error_msg = str(e)
                if "quotaExceeded" in error_msg:
                    print(f"\n⚠ APIクォータ上限に達しました。取得済み {len(video_ids)} 件で続行します。")
                    return video_ids
                print(f"  {progress} エラー: {e}")

    print(f"\n検索完了: {len(video_ids)} 件の動画を取得 (API呼び出し: {api_calls}回)")
    return video_ids


def get_video_details(video_ids):
    videos = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        request = youtube.videos().list(
            part="snippet,contentDetails,statistics",
            id=",".join(batch),
        )
        response = request.execute()
        _QUOTA_COUNTS["videos_list"] += 1
        videos.extend(response.get("items", []))
    return videos


def get_channel_details(channel_ids):
    channels = {}
    unique_ids = list(set(channel_ids))
    for i in range(0, len(unique_ids), 50):
        batch = unique_ids[i : i + 50]
        request = youtube.channels().list(
            part="statistics,snippet",
            id=",".join(batch),
        )
        response = request.execute()
        _QUOTA_COUNTS["channels_list"] += 1
        for item in response.get("items", []):
            sub_count = int(item["statistics"].get("subscriberCount", 0))
            if item["statistics"].get("hiddenSubscriberCount", False):
                sub_count = 0
            channels[item["id"]] = {
                "subscriberCount": sub_count,
                "title": item["snippet"]["title"],
            }
    return channels


def is_blacklisted(video, channel_name):
    if "切り抜き" in channel_name:
        return True
    title = video["snippet"].get("title", "")
    description = video["snippet"].get("description", "")
    tags = video["snippet"].get("tags", [])
    tags_text = " ".join(tags)
    for text in [title, channel_name, description, tags_text]:
        if contains_ng_keyword(text):
            return True
    return False


def filter_and_rank(videos, channels):
    results = []
    blacklisted_count = 0

    for video in videos:
        channel_id = video["snippet"]["channelId"]
        channel_info = channels.get(channel_id)
        if not channel_info:
            continue

        if is_blacklisted(video, channel_info["title"]):
            blacklisted_count += 1
            continue

        if not is_japanese_vtuber(video, channel_info["title"]):
            continue

        sub_count = channel_info["subscriberCount"]
        view_count = int(video["statistics"].get("viewCount", 0))
        comment_count = int(video["statistics"].get("commentCount", 0))
        # ライブ配信中・地域制限・削除済み動画は contentDetails.duration が欠落することがあるためスキップ
        duration_iso = (video.get("contentDetails") or {}).get("duration")
        if not duration_iso:
            continue
        duration_sec = parse_iso8601_duration(duration_iso)

        if not (MIN_SUBSCRIBERS <= sub_count <= MAX_SUBSCRIBERS):
            continue
        if view_count < sub_count * VIEW_MULTIPLIER:
            continue
        if comment_count < MIN_COMMENTS:
            continue
        if not (MIN_DURATION_SEC <= duration_sec <= MAX_DURATION_SEC):
            continue

        growth_rate = view_count / sub_count if sub_count > 0 else 0

        results.append({
            "title": video["snippet"]["title"],
            "channel": channel_info["title"],
            "channel_id": channel_id,  # main_long.py のチャンネル巡回用に保存
            "subscribers": sub_count,
            "views": view_count,
            "growth_rate": round(growth_rate, 1),
            "comments": comment_count,
            "duration": duration_sec,
            "published": video["snippet"]["publishedAt"][:10],
            "url": f"https://www.youtube.com/shorts/{video['id']}",
        })

    print(f"  → ブラックリスト除外: {blacklisted_count}件")
    results.sort(key=lambda x: x["growth_rate"], reverse=True)
    return results


# --- コメント分析 ---
ANALYSIS_CATEGORIES = {
    "面白さ・笑い": ["面白", "笑", "草", "ｗ", "www", "ww", "ﾜﾛ", "ワロ", "ウケ", "爆笑", "吹いた", "吹き出", "ツボ", "腹筋"],
    "かわいい・推し": ["かわい", "カワイ", "可愛", "推し", "推せ", "尊い", "てぇてぇ", "好き", "大好き", "すこ", "萌え", "きゅん", "癒し", "癒さ"],
    "すごい・才能": ["すご", "スゴ", "凄", "上手", "うま", "ウマ", "天才", "才能", "プロ", "神", "最高", "やば", "ヤバ", "えぐ", "半端"],
    "共感・あるある": ["わかる", "分かる", "あるある", "それな", "共感", "同じ", "わかりみ", "まさに", "ほんと", "リアル"],
    "応援・期待": ["頑張", "がんば", "応援", "期待", "楽しみ", "待って", "登録", "チャンネル", "伸び", "もっと", "これから"],
    "驚き・衝撃": ["えっ", "えぇ", "まじ", "マジ", "嘘", "ウソ", "衝撃", "びっくり", "驚", "初めて", "知らな", "そうなん"],
    "声・ビジュアル": ["声", "イケボ", "かっこい", "カッコ", "イケメン", "美", "綺麗", "キレイ", "ビジュアル", "見た目", "顔"],
    "編集・クオリティ": ["編集", "クオリティ", "完成度", "センス", "構成", "テンポ", "見やすい", "作り", "演出"],
}


def fetch_comments(video_id, max_results=30):
    try:
        response = youtube.commentThreads().list(
            part="snippet",
            videoId=video_id,
            maxResults=max_results,
            order="relevance",
            textFormat="plainText",
        ).execute()
        comments = []
        for item in response.get("items", []):
            text = item["snippet"]["topLevelComment"]["snippet"]["textDisplay"]
            likes = item["snippet"]["topLevelComment"]["snippet"].get("likeCount", 0)
            comments.append({"text": text, "likes": likes})
        return comments
    except Exception:
        return []


def analyze_comments(comments, video_info):
    if not comments:
        return "コメントを取得できませんでした"

    all_text = " ".join([c["text"] for c in comments])
    top_comments = sorted(comments, key=lambda c: c["likes"], reverse=True)[:5]

    scores = {}
    for category, keywords in ANALYSIS_CATEGORIES.items():
        count = 0
        for kw in keywords:
            count += all_text.lower().count(kw.lower())
        if count > 0:
            scores[category] = count

    sorted_categories = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    parts = []
    if sorted_categories:
        top_reasons = sorted_categories[:3]
        reason_texts = []
        for cat, score in top_reasons:
            if cat == "面白さ・笑い":
                reason_texts.append("笑いやユーモアが視聴者に刺さった")
            elif cat == "かわいい・推し":
                reason_texts.append("かわいさや推しポイントが視聴者の心を掴んだ")
            elif cat == "すごい・才能":
                reason_texts.append("スキルやクオリティの高さに驚きの声が多数")
            elif cat == "共感・あるある":
                reason_texts.append("あるある感や共感性の高い内容が拡散を後押し")
            elif cat == "応援・期待":
                reason_texts.append("視聴者からの応援・期待の声が多く伸びに繋がった")
            elif cat == "驚き・衝撃":
                reason_texts.append("意外性や衝撃的な内容で注目を集めた")
            elif cat == "声・ビジュアル":
                reason_texts.append("声やビジュアルの魅力が評価された")
            elif cat == "編集・クオリティ":
                reason_texts.append("編集のクオリティやテンポの良さが好評")
        parts.append("【バズ要因】" + "。".join(reason_texts) + "。")
    else:
        parts.append("【バズ要因】コメントの傾向から明確なバズ要因を特定中。")

    growth = video_info.get("growth_rate", 0)
    subs = video_info.get("subscribers", 0)
    if growth >= 50:
        parts.append(f"【注目度】登録者{subs:,}人に対し伸び率{growth}xは驚異的。非フォロワーへの大規模な拡散が発生。")
    elif growth >= 15:
        parts.append(f"【注目度】伸び率{growth}xは高水準。おすすめフィードでの露出が拡散に寄与した可能性が高い。")
    elif growth >= 5:
        parts.append(f"【注目度】伸び率{growth}xは堅調。既存ファン以外にもリーチが広がっている。")

    if top_comments:
        best = top_comments[0]
        quote = best["text"].replace("\n", " ")[:80]
        parts.append(f"【人気コメント】「{quote}」（いいね{best['likes']}件）")

    return "\n".join(parts)


def fetch_and_analyze_all(results):
    print(f"\n[5/5] コメント分析中...")
    total = len(results)
    for i, r in enumerate(results):
        video_id = r["url"].split("/shorts/")[-1] if "/shorts/" in r["url"] else ""
        if not video_id:
            r["analysis"] = "動画IDを取得できませんでした"
            continue
        print(f"  [{i+1}/{total}] {r['channel'][:20]}...")
        comments = fetch_comments(video_id)
        r["analysis"] = analyze_comments(comments, r)
    print(f"  → {total}件の分析完了")


HISTORY_FILE = "ranking_all_history.json"


def save_csv(results, filename="ranking_all_output.csv"):
    if not results:
        return
    fieldnames = [
        "順位", "タイトル", "チャンネル名", "登録者数", "再生数",
        "伸び率", "コメント数", "動画秒数", "投稿日", "URL",
    ]
    try:
        f = open(filename, "w", newline="", encoding="utf-8-sig")
    except PermissionError:
        base, ext = os.path.splitext(filename)
        filename = f"{base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
        f = open(filename, "w", newline="", encoding="utf-8-sig")
    with f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for i, r in enumerate(results, 1):
            writer.writerow({
                "順位": i,
                "タイトル": r["title"],
                "チャンネル名": r["channel"],
                "登録者数": r["subscribers"],
                "再生数": r["views"],
                "伸び率": f"{r['growth_rate']}x",
                "コメント数": r["comments"],
                "動画秒数": r["duration"],
                "投稿日": r["published"],
                "URL": f'=HYPERLINK("{r["url"]}","動画を開く")',
            })
    print(f"\nCSV出力: {filename}")


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
            "channel": r["channel"],
            "channel_id": r.get("channel_id", ""),
            "subscribers": r["subscribers"],
            "views": r["views"],
            "growth_rate": r["growth_rate"],
            "comments": r["comments"],
            "duration": r["duration"],
            "published": r["published"],
            "url": r["url"],
            "analysis": r.get("analysis", ""),
        }
        for i, r in enumerate(results, 1)
    ]

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    total_days = len(history)
    print(f"履歴保存: {HISTORY_FILE} ({today}, 累計{total_days}日分)")


def main():
    print("YouTube Shorts VTuber バズランキング【全体版】")
    print("=" * 50)
    print(f"検索クエリ: {SEARCH_QUERIES}")
    print(f"検索期間: 直近{SEARCH_DAYS}日間")
    print(f"除外キーワード: {NG_KEYWORDS}")
    print(f"※ 事務所系VTuber（ホロライブ・にじさんじ等）も含む")

    num_chunks = -(-SEARCH_DAYS // DAYS_PER_CHUNK)
    estimated_calls = len(SEARCH_QUERIES) * num_chunks
    estimated_quota = estimated_calls * 100
    print(f"\n⚠ 推定APIクォータ消費: 約{estimated_quota:,} units")

    print(f"\n[1/5] ショート動画を検索中...")
    video_ids = search_shorts()
    if not video_ids:
        print("動画が見つかりませんでした。")
        return

    print(f"[2/5] 動画の詳細情報を取得中...")
    videos = get_video_details(video_ids)
    print(f"  → {len(videos)} 件の動画情報を取得")

    print("[3/5] チャンネル情報を取得中...")
    channel_ids = [v["snippet"]["channelId"] for v in videos]
    channels = get_channel_details(channel_ids)
    print(f"  → {len(channels)} チャンネルの情報を取得")

    print("[4/5] フィルタリング & ランキング生成中...")
    results = filter_and_rank(videos, channels)

    if results:
        fetch_and_analyze_all(results)

    if results:
        print(f"\n{'='*80}")
        print(f"  VTuber Shorts バズランキング【全体版】（上位 {len(results)} 件）")
        print(f"  ※ 事務所系VTuberも含む")
        print(f"{'='*80}")
        for i, r in enumerate(results, 1):
            raw_title = unicodedata.normalize("NFC", r["title"])
            title = raw_title[:38] + ".." if len(raw_title) > 40 else raw_title
            print(f"  {i:>3}. {title:<40} {r['growth_rate']:>6.1f}x  {r['views']:>10,}再生")

    save_csv(results)
    save_history(results)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ 致命的エラー: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        log_quota_run("main_all.py", _QUOTA_COUNTS)
