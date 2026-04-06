"""
月次ヒントレポート生成ツール
ranking_history.json / tweet_history.json / tiktok_history.json を分析し
hints_report.json に集約レポートを出力する

使い方:
  python generate_hints.py            # 全期間のデータからレポート生成
"""

import io
import json
import os
import re
import statistics
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

RANKING_FILE = "ranking_history.json"
RANKING_ALL_FILE = "ranking_all_history.json"
TWEET_FILE = "tweet_history.json"
TIKTOK_FILE = "tiktok_history.json"
OUTPUT_FILE = "hints_report.json"

# ストップワード（viewer.html の hintStopWords + hintTemplatePhrases 相当）
STOP_WORDS = {
    "の", "は", "が", "を", "に", "で", "と", "も", "な", "よ", "か",
    "へ", "から", "まで", "より", "ない", "ある", "いる", "する", "なる",
    "れる", "られる", "せる", "させる", "ます", "です", "した", "して",
    "ている", "ていた", "ました", "でした", "ません", "ですが", "ですね",
    "これ", "それ", "あれ", "この", "その", "あの", "ここ", "そこ",
    "あそこ", "こう", "そう", "ああ", "どう", "こんな", "そんな", "あんな",
    "わたし", "あなた", "かれ", "彼女", "たち", "ほう", "もの", "こと",
    "とき", "ため", "うち", "そう", "やつ", "くん", "ちゃん", "さん",
    "バズ要因", "注目度", "人気コメント", "成長ポテンシャル", "登録者",
    "VTuber", "vtuber", "Vtuber", "ショート", "動画", "チャンネル",
}

TEMPLATE_PHRASES = [
    "笑いやユーモアが視聴者に刺さった",
    "かわいさや推しポイントが視聴者の心を掴んだ",
    "スキルやクオリティの高さに驚きの声が多数",
    "身近なあるあるネタに共感が集まった",
    "新人VTuberとして応援の声が多い",
    "意外性やギャップが視聴者にインパクトを与えた",
]

# ツイート種別判定キーワード
TWEET_CATEGORIES = {
    "お知らせ・告知": ["お知らせ", "告知", "デビュー", "新衣装", "新ビジュアル", "初配信", "記念"],
    "イラスト・ビジュアル": ["イラスト", "描", "立ち絵", "新衣装", "ビジュアル", "ママ"],
    "フォロー祭り・交流": ["フォロー祭", "フォロバ", "繋がり", "相互"],
    "おはよう・日常": ["おはよう", "おはにゃ", "おは", "日常", "雑談"],
    "意見・考察": ["思う", "個人勢", "活動", "考え", "意見"],
    "ファンアート・創作": ["ファンアート", "FA", "踊ってみた", "歌ってみた", "創作"],
}


def load_json(filepath):
    """JSONファイルを読み込み"""
    if not os.path.exists(filepath):
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def extract_katakana(text):
    """カタカナ語（3-12文字）を抽出"""
    return re.findall(r'[\u30A1-\u30F6\u30FC]{3,12}', text)


def extract_kanji(text):
    """漢字語（2-6文字）を抽出"""
    return re.findall(r'[\u4E00-\u9FFF]{2,6}', text)


def extract_keywords(text):
    """テキストからキーワードを抽出（ストップワード除外）"""
    words = extract_katakana(text) + extract_kanji(text)
    return [w for w in words
            if w not in STOP_WORDS
            and not any(tp in w or w in tp for tp in TEMPLATE_PHRASES)]


def median(values):
    """中央値を計算"""
    if not values:
        return 0
    return statistics.median(values)


# =============================================================================
# YouTube Shorts 分析
# =============================================================================

def analyze_youtube(items):
    """YouTube Shorts データを分析"""
    if not items:
        return None

    growth_rates = [d.get("growth_rate", 0) for d in items]
    avg_growth = round(sum(growth_rates) / len(growth_rates), 1) if growth_rates else 0
    med_growth = round(median(growth_rates), 1)

    # TOP10
    top10 = sorted(items, key=lambda x: x.get("growth_rate", 0), reverse=True)[:10]
    avg_top_growth = round(sum(d.get("growth_rate", 0) for d in top10) / len(top10), 1) if top10 else 0
    avg_top_duration = round(sum(d.get("duration", 0) for d in top10) / len(top10)) if top10 else 0
    avg_top_subs = round(sum(d.get("subscribers", 0) for d in top10) / len(top10)) if top10 else 0

    # 動画の長さ分析
    dur_buckets = {"5-15秒": [], "16-25秒": [], "26-35秒": [], "36-45秒": [], "46-60秒": []}
    for d in items:
        dur = d.get("duration", 0)
        gr = d.get("growth_rate", 0)
        if dur <= 15:
            dur_buckets["5-15秒"].append(gr)
        elif dur <= 25:
            dur_buckets["16-25秒"].append(gr)
        elif dur <= 35:
            dur_buckets["26-35秒"].append(gr)
        elif dur <= 45:
            dur_buckets["36-45秒"].append(gr)
        else:
            dur_buckets["46-60秒"].append(gr)

    duration_analysis = {}
    best_dur = None
    best_dur_avg = 0
    for label, rates in dur_buckets.items():
        avg = round(sum(rates) / len(rates), 1) if rates else 0
        duration_analysis[label] = {"count": len(rates), "avg_growth": avg}
        if avg > best_dur_avg and len(rates) > 0:
            best_dur_avg = avg
            best_dur = label

    # 登録者帯分析
    sub_bands = {"500-2,000": [500, 2000], "2,001-5,000": [2001, 5000],
                 "5,001-20,000": [5001, 20000], "20,001-100,000": [20001, 100000]}
    sub_analysis = {}
    best_sub = None
    best_sub_avg = 0
    for label, (lo, hi) in sub_bands.items():
        band_items = [d for d in items if lo <= d.get("subscribers", 0) <= hi]
        rates = [d.get("growth_rate", 0) for d in band_items]
        avg = round(sum(rates) / len(rates), 1) if rates else 0
        sub_analysis[label] = {"count": len(band_items), "avg_growth": avg}
        if avg > best_sub_avg and len(band_items) > 0:
            best_sub_avg = avg
            best_sub = label

    # 曜日分析
    day_names = ["月", "火", "水", "木", "金", "土", "日"]
    day_stats = [{"count": 0, "total_growth": 0} for _ in range(7)]
    for d in items:
        pub = d.get("published", "")
        if pub:
            try:
                dt = datetime.strptime(pub, "%Y-%m-%d")
                wd = dt.weekday()  # 0=Monday
                day_stats[wd]["count"] += 1
                day_stats[wd]["total_growth"] += d.get("growth_rate", 0)
            except ValueError:
                pass

    day_analysis = {}
    best_day = None
    best_day_avg = 0
    for i, name in enumerate(day_names):
        s = day_stats[i]
        avg = round(s["total_growth"] / s["count"], 1) if s["count"] > 0 else 0
        day_analysis[f"{name}曜日"] = {"count": s["count"], "avg_growth": avg}
        if avg > best_day_avg and s["count"] > 0:
            best_day_avg = avg
            best_day = f"{name}曜日"

    # ハッシュタグ分析
    hashtag_counter = Counter()
    for d in items:
        tags = re.findall(r'#[^\s#]+', d.get("title", ""))
        for tag in tags:
            hashtag_counter[tag.lower()] += 1
    top_hashtags = hashtag_counter.most_common(12)

    # タイトル文字数分析
    title_len_buckets = {"1-10字": [], "11-20字": [], "21-30字": [], "31字+": []}
    for d in items:
        title = re.sub(r'#\S+', '', d.get("title", "")).strip()
        tlen = len(title)
        gr = d.get("growth_rate", 0)
        if tlen <= 10:
            title_len_buckets["1-10字"].append(gr)
        elif tlen <= 20:
            title_len_buckets["11-20字"].append(gr)
        elif tlen <= 30:
            title_len_buckets["21-30字"].append(gr)
        else:
            title_len_buckets["31字+"].append(gr)

    title_len_analysis = {}
    for label, rates in title_len_buckets.items():
        avg = round(sum(rates) / len(rates), 1) if rates else 0
        title_len_analysis[label] = {"count": len(rates), "avg_growth": avg}

    # コメント数分析
    comment_buckets = {"10-30": [], "31-50": [], "51-100": [], "100+": []}
    for d in items:
        c = d.get("comments", 0)
        gr = d.get("growth_rate", 0)
        if c <= 30:
            comment_buckets["10-30"].append(gr)
        elif c <= 50:
            comment_buckets["31-50"].append(gr)
        elif c <= 100:
            comment_buckets["51-100"].append(gr)
        else:
            comment_buckets["100+"].append(gr)

    comment_analysis = {}
    for label, rates in comment_buckets.items():
        avg = round(sum(rates) / len(rates), 1) if rates else 0
        comment_analysis[label] = {"count": len(rates), "avg_growth": avg}

    # 伸び率分布
    growth_dist = {"3-5x": 0, "5-10x": 0, "10-20x": 0, "20-50x": 0, "50-100x": 0, "100x+": 0}
    for gr in growth_rates:
        if gr >= 100:
            growth_dist["100x+"] += 1
        elif gr >= 50:
            growth_dist["50-100x"] += 1
        elif gr >= 20:
            growth_dist["20-50x"] += 1
        elif gr >= 10:
            growth_dist["10-20x"] += 1
        elif gr >= 5:
            growth_dist["5-10x"] += 1
        elif gr >= 3:
            growth_dist["3-5x"] += 1

    pct_above_10x = round(sum(1 for gr in growth_rates if gr >= 10) / len(growth_rates) * 100, 1) if growth_rates else 0

    # リピートクリエイター
    channel_counter = Counter(d.get("channel", "") for d in items)
    repeat_creators = [(ch, cnt) for ch, cnt in channel_counter.most_common(10) if cnt >= 2]

    # バズ要因（analysis フィールドから）
    buzz_counter = Counter()
    for d in items:
        analysis = d.get("analysis", "")
        if analysis:
            matches = re.findall(r'【バズ要因】(.+?)(?:。|$)', analysis)
            for m in matches:
                for reason in m.split("。"):
                    reason = reason.strip()
                    if reason:
                        buzz_counter[reason] += 1
    top_buzz_reasons = buzz_counter.most_common(6)

    # トレンドキーワード（analysis フィールドから）
    keyword_counter = Counter()
    for d in items:
        analysis = d.get("analysis", "")
        if analysis:
            comment_matches = re.findall(r'【人気コメント】「(.+?)」', analysis)
            for comment_text in comment_matches:
                keywords = extract_keywords(comment_text)
                keyword_counter.update(keywords)
    top_keywords = [(w, c) for w, c in keyword_counter.most_common(20) if c >= 2]

    return {
        "summary": {
            "total_videos": len(items),
            "avg_growth": avg_growth,
            "median_growth": med_growth,
            "top10_avg_growth": avg_top_growth,
            "top10_avg_duration": avg_top_duration,
            "top10_avg_subscribers": avg_top_subs,
            "pct_above_10x": pct_above_10x,
        },
        "duration_analysis": duration_analysis,
        "best_duration": best_dur,
        "subscriber_analysis": sub_analysis,
        "best_subscriber_band": best_sub,
        "day_analysis": day_analysis,
        "best_day": best_day,
        "top_hashtags": [{"tag": t, "count": c} for t, c in top_hashtags],
        "title_length_analysis": title_len_analysis,
        "comment_analysis": comment_analysis,
        "growth_distribution": growth_dist,
        "repeat_creators": [{"channel": ch, "count": c} for ch, c in repeat_creators],
        "top_buzz_reasons": [{"reason": r, "count": c} for r, c in top_buzz_reasons],
        "top_keywords": [{"word": w, "count": c} for w, c in top_keywords],
    }


# =============================================================================
# Twitter 分析
# =============================================================================

def analyze_tweets(items):
    """Twitter データを分析"""
    if not items:
        return None

    total_likes = sum(t.get("like_count", 0) for t in items)
    total_rt = sum(t.get("retweet_count", 0) for t in items)
    total_imps = sum(t.get("impression_count", 0) for t in items)
    avg_likes = round(total_likes / len(items)) if items else 0
    avg_rt = round(total_rt / len(items)) if items else 0

    # TOP10
    top10 = sorted(items, key=lambda x: x.get("like_count", 0), reverse=True)[:10]
    top10_avg = round(sum(t.get("like_count", 0) for t in top10) / len(top10)) if top10 else 0

    # 文字数分析
    len_buckets = {"1-50字": [], "51-100字": [], "101-140字": [], "141字以上": []}
    for t in items:
        tlen = len(t.get("text", ""))
        likes = t.get("like_count", 0)
        if tlen <= 50:
            len_buckets["1-50字"].append(likes)
        elif tlen <= 100:
            len_buckets["51-100字"].append(likes)
        elif tlen <= 140:
            len_buckets["101-140字"].append(likes)
        else:
            len_buckets["141字以上"].append(likes)

    text_len_analysis = {}
    best_len = None
    best_len_avg = 0
    for label, likes_list in len_buckets.items():
        avg = round(sum(likes_list) / len(likes_list)) if likes_list else 0
        text_len_analysis[label] = {"count": len(likes_list), "avg_likes": avg}
        if avg > best_len_avg and len(likes_list) > 0:
            best_len_avg = avg
            best_len = label

    # ハッシュタグ分析
    hashtag_data = defaultdict(lambda: {"count": 0, "likes": 0})
    for t in items:
        tags = re.findall(r'#[^\s#]+', t.get("text", ""))
        for tag in tags:
            hashtag_data[tag]["count"] += 1
            hashtag_data[tag]["likes"] += t.get("like_count", 0)

    top_hashtags_freq = sorted(hashtag_data.items(), key=lambda x: x[1]["count"], reverse=True)[:15]
    top_hashtags_likes = sorted(
        [(tag, data) for tag, data in hashtag_data.items() if data["count"] >= 2],
        key=lambda x: x[1]["likes"] / x[1]["count"],
        reverse=True
    )[:10]

    # メディア効果
    with_media = [t for t in items if t.get("media_urls") and len(t["media_urls"]) > 0]
    no_media = [t for t in items if not t.get("media_urls") or len(t["media_urls"]) == 0]
    media_avg = round(sum(t.get("like_count", 0) for t in with_media) / len(with_media)) if with_media else 0
    no_media_avg = round(sum(t.get("like_count", 0) for t in no_media) / len(no_media)) if no_media else 0

    # 拡散分析
    rt_ratios = []
    for t in items:
        likes = t.get("like_count", 0)
        if likes > 0:
            ratio = t.get("retweet_count", 0) / likes * 100
            rt_ratios.append({"ratio": round(ratio, 1), "url": t.get("url", ""),
                              "text": t.get("text", "")[:50]})
    avg_rt_ratio = round(sum(r["ratio"] for r in rt_ratios) / len(rt_ratios), 1) if rt_ratios else 0
    high_spread = sorted(rt_ratios, key=lambda x: x["ratio"], reverse=True)[:5]

    # 投稿時間帯分析（UTC→JST）
    hour_data = defaultdict(lambda: {"count": 0, "likes": 0})
    for t in items:
        posted = t.get("posted_at", "")
        if posted:
            try:
                # ISO形式 or 先頭10文字だけの場合
                if "T" in posted:
                    dt = datetime.fromisoformat(posted.replace("Z", "+00:00"))
                    jst_hour = (dt.hour + 9) % 24
                    hour_data[jst_hour]["count"] += 1
                    hour_data[jst_hour]["likes"] += t.get("like_count", 0)
            except (ValueError, AttributeError):
                pass

    time_analysis = {}
    best_hour = None
    best_hour_avg = 0
    for h in range(24):
        data = hour_data[h]
        avg = round(data["likes"] / data["count"]) if data["count"] > 0 else 0
        time_analysis[f"{h}時"] = {"count": data["count"], "avg_likes": avg}
        if avg > best_hour_avg and data["count"] > 0:
            best_hour_avg = avg
            best_hour = f"{h}時"

    # ツイート種別分析
    type_data = {}
    for cat_name, keywords in TWEET_CATEGORIES.items():
        cat_tweets = [t for t in items if any(kw in t.get("text", "") for kw in keywords)]
        cat_likes = [t.get("like_count", 0) for t in cat_tweets]
        avg = round(sum(cat_likes) / len(cat_likes)) if cat_likes else 0
        type_data[cat_name] = {"count": len(cat_tweets), "avg_likes": avg}

    # トレンドキーワード
    keyword_counter = Counter()
    for t in items:
        text = t.get("text", "")
        text = re.sub(r'https?://\S+', '', text)
        text = re.sub(r'#\S+', '', text)
        keywords = extract_keywords(text)
        keyword_counter.update(keywords)
    top_keywords = [(w, c) for w, c in keyword_counter.most_common(20) if c >= 2]

    # リピートアカウント
    author_counter = Counter()
    author_likes = defaultdict(int)
    for t in items:
        uname = t.get("author_username", "")
        if uname:
            author_counter[uname] += 1
            author_likes[uname] += t.get("like_count", 0)
    repeat_authors = [
        {"username": u, "count": c, "total_likes": author_likes[u]}
        for u, c in author_counter.most_common(10) if c >= 2
    ]

    return {
        "summary": {
            "total_tweets": len(items),
            "avg_likes": avg_likes,
            "avg_rt": avg_rt,
            "top10_avg_likes": top10_avg,
        },
        "text_length_analysis": text_len_analysis,
        "best_text_length": best_len,
        "top_hashtags_by_freq": [{"tag": t, "count": d["count"]} for t, d in top_hashtags_freq],
        "top_hashtags_by_likes": [
            {"tag": t, "count": d["count"], "avg_likes": round(d["likes"] / d["count"])}
            for t, d in top_hashtags_likes
        ],
        "media_effect": {
            "with_media_count": len(with_media),
            "with_media_avg_likes": media_avg,
            "no_media_count": len(no_media),
            "no_media_avg_likes": no_media_avg,
        },
        "spread_analysis": {
            "avg_rt_ratio_pct": avg_rt_ratio,
            "high_spread_top5": high_spread,
        },
        "time_analysis": time_analysis,
        "best_posting_hour": best_hour,
        "tweet_type_analysis": type_data,
        "top_keywords": [{"word": w, "count": c} for w, c in top_keywords],
        "repeat_authors": repeat_authors,
    }


# =============================================================================
# TikTok 分析
# =============================================================================

def analyze_tiktok(items):
    """TikTok データを分析"""
    if not items:
        return None

    growth_rates = [d.get("growth_rate", 0) for d in items]
    avg_growth = round(sum(growth_rates) / len(growth_rates), 1) if growth_rates else 0
    med_growth = round(median(growth_rates), 1)

    # TOP10
    top10 = sorted(items, key=lambda x: x.get("growth_rate", 0), reverse=True)[:10]
    top10_avg = round(sum(d.get("growth_rate", 0) for d in top10) / len(top10), 1) if top10 else 0

    # フォロワー帯分析
    follower_bands = {
        "100-500": [100, 500], "501-2,000": [501, 2000],
        "2,001-10,000": [2001, 10000], "10,001-100,000": [10001, 100000]
    }
    follower_analysis = {}
    best_band = None
    best_band_avg = 0
    for label, (lo, hi) in follower_bands.items():
        band_items = [d for d in items if lo <= d.get("followers", 0) <= hi]
        rates = [d.get("growth_rate", 0) for d in band_items]
        avg = round(sum(rates) / len(rates), 1) if rates else 0
        follower_analysis[label] = {"count": len(band_items), "avg_growth": avg}
        if avg > best_band_avg and len(band_items) > 0:
            best_band_avg = avg
            best_band = label

    # ハッシュタグ分析
    hashtag_counter = Counter()
    for d in items:
        hashtags = d.get("hashtags", [])
        if isinstance(hashtags, list):
            for tag in hashtags:
                if isinstance(tag, str) and tag:
                    hashtag_counter[tag.lower()] += 1
        # タイトルからも抽出
        tags = re.findall(r'#([^\s#]+)', d.get("title", ""))
        for tag in tags:
            hashtag_counter[tag.lower()] += 1
    top_hashtags = hashtag_counter.most_common(10)

    # リピートクリエイター
    author_counter = Counter(d.get("author", "") or d.get("username", "") for d in items)
    repeat_creators = [(a, c) for a, c in author_counter.most_common(10) if c >= 2]

    return {
        "summary": {
            "total_videos": len(items),
            "avg_growth": avg_growth,
            "median_growth": med_growth,
            "top10_avg_growth": top10_avg,
        },
        "follower_analysis": follower_analysis,
        "best_follower_band": best_band,
        "top_hashtags": [{"tag": t, "count": c} for t, c in top_hashtags],
        "repeat_creators": [{"author": a, "count": c} for a, c in repeat_creators],
    }


# =============================================================================
# メイン
# =============================================================================

def main():
    print("月次ヒントレポート生成ツール")
    print("=" * 50)

    # 1. データ読み込み
    print("\n[1/5] データ読み込み中...")
    ranking_data = load_json(RANKING_FILE)
    ranking_all_data = load_json(RANKING_ALL_FILE)
    tweet_data = load_json(TWEET_FILE)
    tiktok_data = load_json(TIKTOK_FILE)

    # 全アイテムをフラット化
    kojin_items = []
    for date, items in ranking_data.items():
        for item in items:
            kojin_items.append(item)

    all_items = []
    for date, items in ranking_all_data.items():
        for item in items:
            all_items.append(item)

    tweet_items = []
    for date, items in tweet_data.items():
        for item in items:
            tweet_items.append(item)

    tiktok_items = []
    for date, items in tiktok_data.items():
        for item in items:
            tiktok_items.append(item)

    print(f"  YouTube個人: {len(kojin_items)}件 ({len(ranking_data)}日分)")
    print(f"  YouTube全体: {len(all_items)}件 ({len(ranking_all_data)}日分)")
    print(f"  Twitter: {len(tweet_items)}件 ({len(tweet_data)}日分)")
    print(f"  TikTok: {len(tiktok_items)}件 ({len(tiktok_data)}日分)")

    # 期間
    all_dates = sorted(set(
        list(ranking_data.keys()) + list(ranking_all_data.keys()) +
        list(tweet_data.keys()) + list(tiktok_data.keys())
    ))
    period_start = all_dates[0] if all_dates else "N/A"
    period_end = all_dates[-1] if all_dates else "N/A"

    # 2. YouTube 個人 分析
    print(f"\n[2/5] YouTube個人VTuber分析中...")
    youtube_kojin = analyze_youtube(kojin_items)
    if youtube_kojin:
        print(f"  → 平均伸び率: {youtube_kojin['summary']['avg_growth']}x")
        print(f"  → 最適な動画長: {youtube_kojin['best_duration']}")
        print(f"  → 最適な投稿曜日: {youtube_kojin['best_day']}")

    # 3. YouTube 全体 分析
    print(f"\n[3/5] YouTube全体分析中...")
    youtube_all = analyze_youtube(all_items)
    if youtube_all:
        print(f"  → 平均伸び率: {youtube_all['summary']['avg_growth']}x")

    # 個人vs全体 比較
    comparison = None
    if youtube_kojin and youtube_all:
        comparison = {
            "kojin_avg_growth": youtube_kojin["summary"]["avg_growth"],
            "all_avg_growth": youtube_all["summary"]["avg_growth"],
            "kojin_best_duration": youtube_kojin["best_duration"],
            "all_best_duration": youtube_all["best_duration"],
            "kojin_best_day": youtube_kojin["best_day"],
            "all_best_day": youtube_all["best_day"],
            "kojin_pct_above_10x": youtube_kojin["summary"]["pct_above_10x"],
            "all_pct_above_10x": youtube_all["summary"]["pct_above_10x"],
            "kojin_best_sub_band": youtube_kojin["best_subscriber_band"],
            "all_best_sub_band": youtube_all["best_subscriber_band"],
        }

    # 4. Twitter 分析
    print(f"\n[4/5] ツイート分析中...")
    tweet_hints = analyze_tweets(tweet_items)
    if tweet_hints:
        print(f"  → 平均いいね: {tweet_hints['summary']['avg_likes']}")
        print(f"  → 最適な文字数: {tweet_hints['best_text_length']}")
        print(f"  → 最適な投稿時間: {tweet_hints['best_posting_hour']}")

    # 5. TikTok 分析
    print(f"\n[5/5] TikTok分析中...")
    tiktok_hints = analyze_tiktok(tiktok_items)
    if tiktok_hints:
        print(f"  → 平均バズ倍率: {tiktok_hints['summary']['avg_growth']}x")
        print(f"  → 最適なフォロワー帯: {tiktok_hints['best_follower_band']}")

    # レポート生成
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": f"{period_start} 〜 {period_end}",
        "data_counts": {
            "youtube_kojin_videos": len(kojin_items),
            "youtube_kojin_days": len(ranking_data),
            "youtube_all_videos": len(all_items),
            "youtube_all_days": len(ranking_all_data),
            "tweets": len(tweet_items),
            "tweet_days": len(tweet_data),
            "tiktok_videos": len(tiktok_items),
            "tiktok_days": len(tiktok_data),
        },
        "youtube_hints": youtube_kojin,
        "youtube_all_hints": youtube_all,
        "youtube_comparison": comparison,
        "tweet_hints": tweet_hints,
        "tiktok_hints": tiktok_hints,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\nレポート出力: {OUTPUT_FILE}")
    print(f"期間: {period_start} 〜 {period_end}")
    print(f"\n完了!")


if __name__ == "__main__":
    main()
