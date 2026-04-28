"""
動画が伸びた理由を多角的に解析する。

設計方針:
- ルールベースで複数の検出器を並列実行し、それぞれが「factors dict」の 1 セクションを返す
- 検出器は独立: コンテンツ評価 / タイトル / 投稿時刻 / エンゲージメント /
  ゲームトレンド / チャンネル文脈 / 外部流入（Twitter/TikTok）/ 拡散パターン
- LLM API は使用しない（既存データだけで解析）
- 既存の `vtuber_common.analyze_comments` から ANALYSIS_CATEGORIES と analyze_comments
  を本モジュールに移管。後方互換のため re-export する

主な公開 API:
    analyze_video_holistic(video_info, comments, **contexts) -> dict (factors)
    format_holistic_analysis(factors) -> str  (人間向けテキスト)
    analyze_comments(comments, video_info, growth_thresholds) -> str  (後方互換)

contexts:
    long_db_path : str  - youtube_long.db のパス（DB クロスリファレンス用）
    tweet_history: dict - tweet_history.json の中身
    tiktok_history: dict - tiktok_history.json の中身
    growth_thresholds: tuple - (高,中,低) 伸び率しきい値 (default Shorts: 50,15,5 / Long: 5,2,1)
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping, Optional

# =============================================================================
# 辞書類
# =============================================================================

# コンテンツ評価カテゴリ（コメント本文のキーワード集計）
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

# カテゴリ → 人間向け説明文
_CATEGORY_DESCRIPTIONS = {
    "面白さ・笑い": "笑いやユーモアが視聴者に刺さった",
    "かわいい・推し": "かわいさや推しポイントが視聴者の心を掴んだ",
    "すごい・才能": "スキルやクオリティの高さに驚きの声が多数",
    "共感・あるある": "あるある感や共感性の高い内容が拡散を後押し",
    "応援・期待": "視聴者からの応援・期待の声が多く伸びに繋がった",
    "驚き・衝撃": "意外性や衝撃的な内容で注目を集めた",
    "声・ビジュアル": "声やビジュアルの魅力が評価された",
    "編集・クオリティ": "編集のクオリティやテンポの良さが好評",
}

# タイトル感情語（クリックベイトシグナル）
# 注: 「ガチ」単独は「ガチャ」「ガチ勢」等と誤マッチするため、具体形（ガチギレ等）のみ採用
TITLE_EMOTION_KEYWORDS = [
    "神回", "衝撃", "事件", "大事件", "本気", "限界", "閲覧注意",
    "最強", "最高", "最後", "最終", "感動", "号泣", "悲報", "朗報",
    "炎上", "禁断", "やばい", "ヤバい", "ガチギレ", "ガチで", "本音",
]


# =============================================================================
# Phase 1: 基礎検出器（コメント / タイトル / 時刻 / エンゲージメント）
# =============================================================================

def detect_content_signals(comments, growth_thresholds=(50, 15, 5), video_info=None):
    """コメント解析でコンテンツ評価カテゴリと注目度を抽出する。

    Returns
    -------
    dict
        {"top_categories": [...], "category_scores": {...},
         "popular_comments": [...], "growth_note": str | None,
         "description": str}
    """
    video_info = video_info or {}
    if not comments:
        return {
            "top_categories": [],
            "category_scores": {},
            "popular_comments": [],
            "growth_note": None,
            "description": "コメントを取得できませんでした",
        }

    all_text = " ".join([c.get("text", "") for c in comments])
    top_comments = sorted(comments, key=lambda c: c.get("likes", 0), reverse=True)[:5]

    # カテゴリ別スコア
    scores = {}
    for category, keywords in ANALYSIS_CATEGORIES.items():
        count = 0
        text_lower = all_text.lower()
        for kw in keywords:
            count += text_lower.count(kw.lower())
        if count > 0:
            scores[category] = count

    sorted_categories = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top3 = [c for c, _ in sorted_categories[:3]]

    if top3:
        reason_texts = [_CATEGORY_DESCRIPTIONS.get(c) for c in top3]
        reason_texts = [t for t in reason_texts if t]
        description = "。".join(reason_texts) + "。"
    else:
        description = "コメントの傾向から明確なバズ要因を特定中。"

    # 注目度（伸び率）に関する補足
    growth = video_info.get("growth_rate", 0) or 0
    subs = video_info.get("subscribers", 0) or video_info.get("subscriber_count", 0) or 0
    th_high, th_mid, th_low = growth_thresholds
    growth_note = None
    if growth >= th_high:
        growth_note = f"登録者{subs:,}人に対し伸び率{growth}xは驚異的。非フォロワーへの大規模な拡散が発生"
    elif growth >= th_mid:
        growth_note = f"伸び率{growth}xは高水準。おすすめフィードでの露出が拡散に寄与した可能性が高い"
    elif growth >= th_low:
        growth_note = f"伸び率{growth}xは堅調。既存ファン以外にもリーチが広がっている"

    return {
        "top_categories": top3,
        "category_scores": scores,
        "popular_comments": [
            {"text": (c.get("text", "") or "").replace("\n", " ")[:100], "likes": c.get("likes", 0)}
            for c in top_comments
        ],
        "growth_note": growth_note,
        "description": description,
    }


def detect_title_patterns(title):
    """タイトルの構造的特徴を検出する（クリックベイトシグナル）。"""
    if not title:
        return {"emotion_words": [], "click_score": 0, "description": "タイトル空"}

    has_brackets = bool(re.search(r'【|】|\[|\]', title))
    # 数字パターン: 数字 + 限定的な単位（\w* の貪欲マッチで日本語を巻き込まないよう厳密に）
    number_matches = re.findall(
        r'\d+(?:万円|万|千|百|連|時間|日間|日|週間|週|ヶ月|円|本|戦|回|人|問|連|連勝)',
        title,
    )

    emotion_words = [kw for kw in TITLE_EMOTION_KEYWORDS if kw in title]

    # 釣り度スコア (0-10)
    click_score = 0
    if has_brackets:
        click_score += 2
    if number_matches:
        click_score += 1 + min(len(number_matches), 2)
    if emotion_words:
        click_score += min(len(emotion_words) * 2, 5)
    if len(title) > 50:
        click_score -= 1
    if len(title) < 10:
        click_score -= 1
    click_score = max(0, min(10, click_score))

    # 説明文構築
    parts = []
    if emotion_words:
        parts.append(f"感情語「{', '.join(emotion_words)}」を含む")
    if number_matches:
        parts.append(f"数字使用: {', '.join(number_matches[:3])}")
    if click_score >= 7:
        parts.append("高クリック率タイトル")

    return {
        "has_brackets": has_brackets,
        "has_numbers": bool(number_matches),
        "number_matches": number_matches,
        "emotion_words": emotion_words,
        "click_score": click_score,
        "description": "、".join(parts) if parts else "標準的なタイトル構造",
    }


def detect_timing_factors(published):
    """published (ISO8601 or YYYY-MM-DD) から投稿タイミングを評価。"""
    if not published:
        return {"description": "投稿時刻不明"}
    try:
        if "T" in published:
            dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            jst = dt.astimezone(timezone(timedelta(hours=9)))
        else:
            # YYYY-MM-DD のみの場合は時刻情報なし
            return {
                "weekday": ["月", "火", "水", "木", "金", "土", "日"][
                    datetime.strptime(published[:10], "%Y-%m-%d").weekday()
                ],
                "hour": None,
                "is_golden_time": False,
                "is_weekend": False,
                "description": "日付のみ取得（時刻情報なし）",
            }
    except (ValueError, TypeError):
        return {"description": "投稿時刻のパース失敗"}

    weekday_jp = ["月", "火", "水", "木", "金", "土", "日"][jst.weekday()]
    hour = jst.hour
    is_golden_time = 19 <= hour <= 22  # 推奨どおり 19-22 時固定
    is_weekend = jst.weekday() >= 4  # 金土日

    parts = []
    if is_golden_time:
        parts.append(f"{hour}時投稿（ゴールデンタイム 19-22時）")
    elif 6 <= hour <= 9:
        parts.append(f"{hour}時投稿（朝枠）")
    elif 12 <= hour <= 14:
        parts.append(f"{hour}時投稿（お昼枠）")
    elif 22 < hour or hour < 6:
        parts.append(f"{hour}時投稿（深夜枠）")
    if is_weekend:
        parts.append(f"{weekday_jp}曜日（週末枠）")

    return {
        "weekday": weekday_jp,
        "hour": hour,
        "is_golden_time": is_golden_time,
        "is_weekend": is_weekend,
        "description": "・".join(parts) if parts else f"{weekday_jp}曜 {hour}時投稿",
    }


def detect_engagement_quality(video_info):
    """エンゲージメント率（コメント率）を評価。"""
    if not video_info:
        return {"description": "データ不足"}
    views = video_info.get("views", 0) or video_info.get("view_count", 0) or 0
    comments = video_info.get("comments", 0) or video_info.get("comment_count", 0) or 0

    if views < 100:
        return {"description": "視聴回数が少なくエンゲージメント評価不可"}

    comment_rate = comments / views if views > 0 else 0
    is_high = comment_rate >= 0.005  # 0.5% 超で高水準

    desc = (
        f"コメント率 {comment_rate*100:.2f}% は高水準（推薦アルゴリズム獲得しやすい）"
        if is_high
        else f"コメント率 {comment_rate*100:.2f}%"
    )
    return {
        "comment_rate": round(comment_rate * 100, 3),
        "is_high_engagement": is_high,
        "description": desc,
    }


# =============================================================================
# 統合エントリーポイント（Phase 1 版）
# =============================================================================

def analyze_video_holistic(
    video_info,
    comments=None,
    *,
    growth_thresholds=(50, 15, 5),
    long_db_path=None,
    tweet_history=None,
    tiktok_history=None,
    ranking_history=None,
):
    """全要因を統合解析する。Returns factors dict.

    Phase 1 では content / title_patterns / timing / engagement のみ実装。
    Phase 2 で trend / channel_context / viral_pattern を追加。
    Phase 3 で amplification を追加。
    """
    factors = {}
    comments = comments or []
    video_info = video_info or {}

    factors["content"] = detect_content_signals(comments, growth_thresholds, video_info)
    factors["title_patterns"] = detect_title_patterns(video_info.get("title", ""))
    factors["timing"] = detect_timing_factors(video_info.get("published", ""))
    factors["engagement"] = detect_engagement_quality(video_info)

    # 人気コメント（content 内にも含まれているが、トップレベルに別出ししておく）
    if comments:
        top = sorted(comments, key=lambda c: c.get("likes", 0), reverse=True)[:3]
        factors["popular_comments"] = [
            {"text": (c.get("text", "") or "").replace("\n", " ")[:100], "likes": c.get("likes", 0)}
            for c in top
        ]

    return factors


def format_holistic_analysis(factors):
    """factors dict を人間向けの整形済みテキストに変換する。

    既存 viewer.html の formatAnalysis() の【...】マーカーで装飾されるため、
    各セクションは「【ラベル】本文」のフォーマットで出力する。
    強シグナル（高クリック率タイトル / 注目度 / 高エンゲージメント等）のみ
    表示し、平凡なセクションは省略する。
    """
    if not factors:
        return "解析データなし"

    parts = []

    # 1. コンテンツ評価
    content = factors.get("content", {})
    if content.get("description"):
        parts.append(f"【バズ要因】{content['description']}")

    # 2. 注目度（伸び率）
    if content.get("growth_note"):
        parts.append(f"【注目度】{content['growth_note']}")

    # 3. タイトル特徴（強シグナルのみ）
    title_p = factors.get("title_patterns", {})
    if title_p.get("emotion_words") or (title_p.get("click_score", 0) >= 7):
        parts.append(f"【タイトル特徴】{title_p['description']}")

    # 4. 投稿タイミング（強シグナルのみ）
    timing = factors.get("timing", {})
    if timing.get("is_golden_time") or timing.get("is_weekend"):
        parts.append(f"【投稿タイミング】{timing['description']}")

    # 5. エンゲージメント（高水準のみ）
    eng = factors.get("engagement", {})
    if eng.get("is_high_engagement"):
        parts.append(f"【エンゲージメント】{eng['description']}")

    # 6. 人気コメント
    pop = factors.get("popular_comments", [])
    if pop:
        best = pop[0]
        parts.append(f"【人気コメント】「{best['text']}」（いいね{best['likes']}件）")

    return "\n".join(parts) if parts else "解析データなし"


# =============================================================================
# 後方互換: 既存の analyze_comments 呼出しが動くように関数を残す
# =============================================================================

def analyze_comments(comments, video_info, growth_thresholds=(50, 15, 5)):
    """後方互換: detect_content_signals + 既存の出力フォーマットを再現する。

    旧 vtuber_common.analyze_comments と同じシグネチャ・出力。
    既存の main.py / main_all.py / main_long.py のコードを変えずに動かす。
    """
    if not comments:
        return "コメントを取得できませんでした"

    signals = detect_content_signals(comments, growth_thresholds, video_info or {})
    parts = []
    parts.append(f"【バズ要因】{signals['description']}")
    if signals.get("growth_note"):
        parts.append(f"【注目度】{signals['growth_note']}")
    if signals.get("popular_comments"):
        best = signals["popular_comments"][0]
        parts.append(f"【人気コメント】「{best['text']}」（いいね{best['likes']}件）")
    return "\n".join(parts)
