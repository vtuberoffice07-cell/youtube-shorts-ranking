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
