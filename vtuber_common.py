"""VTuberバズランキング共通ユーティリティ。

main.py / main_all.py / main_long.py の3スクリプトで重複していた基礎関数を
ここに集約する。NG_KEYWORDS など各スクリプトで設定値が異なるものは
引数で受け取る形にして、ロジックの重複だけを排除する。

抽出方針:
- 100%同一の純粋関数 → そのまま移動
- ロジック同一・データ違い → ロジックを移動し、データは引数で受ける
- スクリプト固有のフィルタ判定 (is_blacklisted 等) は移動しない
  （main.py vs main_long.py で対象フィールドが違うため）

Notion 要件定義: https://www.notion.so/34d4806b96df811c88aff310ad2161c7
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Iterable, Mapping, Optional


def parse_iso8601_duration(duration_str: Optional[str]) -> int:
    """ISO 8601 duration (PT1M30S 等) を秒数に変換する。

    None や空文字、不正フォーマットの場合は 0 を返す（呼び出し側で
    duration の有無による分岐を不要にするため）。
    """
    if not duration_str:
        return 0
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def contains_ng_keyword(text: Optional[str], ng_keywords: Iterable[str]) -> bool:
    """text に ng_keywords のいずれかが（大文字小文字を区別せず）含まれていれば True。

    ng_keywords はスクリプトごとに異なるため引数で受ける。
    main.py: 切り抜き / まとめ / ホロライブ 等
    main_long.py: 上記 + 歌ってみた / cover 等（2026-04-28 改修で追加）
    """
    if not text:
        return False
    text_lower = text.lower()
    for ng in ng_keywords:
        if ng.lower() in text_lower:
            return True
    return False


def has_japanese_kana(text: Optional[str]) -> bool:
    """text にひらがな or カタカナが含まれていれば True。

    漢字のみ（中国語・台湾語）の動画を VTuber 判定から除外するために使う。
    Unicode 範囲: ひらがな U+3040〜U+309F、カタカナ U+30A0〜U+30FF
    """
    if not text:
        return False
    for ch in text:
        if "぀" <= ch <= "ゟ" or "゠" <= ch <= "ヿ":
            return True
    return False


def is_japanese_vtuber(video: Mapping, channel_name: Optional[str]) -> bool:
    """動画の title / channel name / description のいずれかに
    ひらがな・カタカナが含まれれば True を返す（=日本語圏 VTuber と推定）。
    """
    snippet = video.get("snippet") or {}
    title = snippet.get("title", "")
    description = snippet.get("description", "")
    return (
        has_japanese_kana(title)
        or has_japanese_kana(channel_name or "")
        or has_japanese_kana(description)
    )


# =============================================================================
# コメント分析（バズ要因の解析）
# =============================================================================
# 各 main_*.py 共通で使う。コメント本文に含まれるキーワードでカテゴリ別スコアを
# 集計し、上位カテゴリから人間向けの説明文を組み立てる。

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

# カテゴリ → 人間向け説明文のマップ
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


def analyze_comments(
    comments,
    video_info,
    growth_thresholds=(50, 15, 5),
):
    """コメント配列とビデオ情報からバズ要因の説明文を生成する。

    Parameters
    ----------
    comments : list[dict]
        各 dict は {"text": str, "likes": int} を持つ
    video_info : dict
        最低限 "growth_rate" / "subscribers" を持つ。表示に使う
    growth_thresholds : tuple[int, int, int]
        伸び率の (驚異的, 高水準, 堅調) のしきい値。
        Shorts は (50, 15, 5)、横動画は伸び率の値域が異なるため (5, 2, 1) など
        format ごとに渡す。

    Returns
    -------
    str
        改行区切りの分析テキスト（【バズ要因】【注目度】【人気コメント】）。
        コメント無しの時は固定文言を返す。
    """
    if not comments:
        return "コメントを取得できませんでした"

    all_text = " ".join([c.get("text", "") for c in comments])
    top_comments = sorted(comments, key=lambda c: c.get("likes", 0), reverse=True)[:5]

    # カテゴリごとのスコア
    scores = {}
    for category, keywords in ANALYSIS_CATEGORIES.items():
        count = 0
        for kw in keywords:
            count += all_text.lower().count(kw.lower())
        if count > 0:
            scores[category] = count

    sorted_categories = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    parts = []
    # メイン: 上位3カテゴリで「バズ要因」を構成
    if sorted_categories:
        top_reasons = sorted_categories[:3]
        reason_texts = [_CATEGORY_DESCRIPTIONS.get(cat) for cat, _ in top_reasons]
        reason_texts = [t for t in reason_texts if t]
        parts.append("【バズ要因】" + "。".join(reason_texts) + "。")
    else:
        parts.append("【バズ要因】コメントの傾向から明確なバズ要因を特定中。")

    # 伸び率による注目度
    growth = video_info.get("growth_rate", 0)
    subs = video_info.get("subscribers", 0)
    th_high, th_mid, th_low = growth_thresholds
    if growth >= th_high:
        parts.append(f"【注目度】登録者{subs:,}人に対し伸び率{growth}xは驚異的。非フォロワーへの大規模な拡散が発生。")
    elif growth >= th_mid:
        parts.append(f"【注目度】伸び率{growth}xは高水準。おすすめフィードでの露出が拡散に寄与した可能性が高い。")
    elif growth >= th_low:
        parts.append(f"【注目度】伸び率{growth}xは堅調。既存ファン以外にもリーチが広がっている。")

    # 人気コメントの引用
    if top_comments:
        best = top_comments[0]
        quote = best.get("text", "").replace("\n", " ")[:80]
        likes = best.get("likes", 0)
        parts.append(f"【人気コメント】「{quote}」（いいね{likes}件）")

    return "\n".join(parts)


def write_latest_snapshot(history: Mapping[str, object], out_path: str, days: int = 30) -> int:
    """history dict (キー = "YYYY-MM-DD" の日付) から直近 N 日分だけ抽出した
    軽量版 JSON を out_path に書き出す。

    フロントエンド (viewer.html) の初期表示で大きな history.json 全量を読まず、
    まず軽量な latest を fetch して即座に直近データを表示するための補助。
    過去日付の閲覧時には呼び出し側 (viewer.html) で full history を遅延読込する。

    Returns: 抽出された日付の件数。

    Notes
    -----
    - history のキーが日付フォーマット "YYYY-MM-DD" であることを前提。
    - 日付パース失敗 / フォーマット不一致のキーは無視する。
    - 出力ファイルは indent=2 で UTF-8 書き出し。
    """
    if not isinstance(history, Mapping):
        raise TypeError(f"history must be Mapping, got {type(history).__name__}")

    today = datetime.utcnow().date()
    cutoff = today - timedelta(days=days)
    latest = {}
    for date_key, value in history.items():
        try:
            d = datetime.strptime(date_key, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if d >= cutoff:
            latest[date_key] = value

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False, indent=2)

    return len(latest)
