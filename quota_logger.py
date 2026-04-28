"""YouTube Data API v3 のクォータ消費を JSONL ログに追記するユーティリティ。

usage:
    from quota_logger import log_quota_run
    log_quota_run("main_long.py", {
        "search_list": 2,
        "videos_list": 5,
        "channels_list": 3,
        "playlist_items_list": 200,
    })

JSONL フォーマット (1 行 = 1 実行):
    {"timestamp": "2026-04-28T19:00:00+09:00",
     "script": "main_long.py",
     "search_list": 2,
     "videos_list": 5,
     "channels_list": 3,
     "playlist_items_list": 200,
     "total_units": 408}

GitHub Actions ログは 90 日で消える。このログは git にコミットされ続けるため、
月次・年次でクォータ消費の集計が可能になる。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from typing import Mapping

# YouTube Data API v3 の各メソッドの quota cost
# https://developers.google.com/youtube/v3/determine_quota_cost
QUOTA_COSTS = {
    "search_list": 100,
    "videos_list": 1,
    "channels_list": 1,
    "playlist_items_list": 1,
    "comment_threads_list": 1,
    "captions_list": 50,  # 参考
}

LOG_FILE = "quota_log.jsonl"
JST = timezone(timedelta(hours=9))


def calculate_total_units(counts: Mapping[str, int]) -> int:
    """各メソッドの呼び出し回数から合計クォータ消費を計算する。"""
    return sum(QUOTA_COSTS.get(k, 0) * v for k, v in counts.items())


def log_quota_run(script_name: str, counts: Mapping[str, int], log_path: str = LOG_FILE) -> None:
    """1 回の実行のクォータ消費を JSONL に追記する。

    Parameters
    ----------
    script_name : str
        実行スクリプト名 (例: "main.py")
    counts : Mapping[str, int]
        各 API メソッドの呼び出し回数。キーは QUOTA_COSTS のキーに合わせる。
    log_path : str, optional
        JSONL の出力先。デフォルト "quota_log.jsonl"。
    """
    entry = {
        "timestamp": datetime.now(JST).isoformat(timespec="seconds"),
        "script": script_name,
        **{k: int(v) for k, v in counts.items()},
        "total_units": calculate_total_units(counts),
    }
    # ファイル無ければ作成、追記モード。失敗してもメインの処理には影響させない。
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001  ログ失敗で実行を止めない
        print(f"[quota_logger] ログ書き込み失敗: {e}")


def summarize_log(log_path: str = LOG_FILE) -> dict:
    """ログを集計して概要を返す（CLI / 定期レポート用）。"""
    if not os.path.exists(log_path):
        return {"runs": 0, "total_units": 0, "by_script": {}}
    runs = 0
    total = 0
    by_script: dict[str, dict] = {}
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            runs += 1
            total += e.get("total_units", 0)
            s = e.get("script", "unknown")
            by_script.setdefault(s, {"runs": 0, "total_units": 0})
            by_script[s]["runs"] += 1
            by_script[s]["total_units"] += e.get("total_units", 0)
    return {"runs": runs, "total_units": total, "by_script": by_script}


if __name__ == "__main__":
    # python quota_logger.py で集計レポートを表示
    import pprint
    pprint.pprint(summarize_log())
