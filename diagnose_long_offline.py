"""指定された8件の横動画について、改修後フィルタを再評価する（API不要・オフライン判定）。

前セッションで取得済みの YouTube API レスポンス値を埋め込み、改修1〜4 + 追加修正
（"まとめ" を NG_KEYWORDS から除外）を適用後にどの動画が PASS するか論理的に確認する。

使い方: python diagnose_long_offline.py
"""
import io
import sys
from datetime import datetime, timezone, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ----- 改修後パラメータ -----
MIN_SUBSCRIBERS = 500
MAX_SUBSCRIBERS = 100_000
VIEW_MULTIPLIER = 0.3
MIN_COMMENTS = 5
MIN_DURATION_SEC = 4 * 60
MAX_DURATION_SEC = 30 * 60
SEARCH_DAYS = 14
NG_KEYWORDS = [
    "切り抜き", "速報", "手書き", "反応",
    "ホロライブ", "hololive", "にじさんじ", "nijisanji",
    "ぶいすぽ", "ネオポルテ",
]


def has_kana(s):
    if not s:
        return False
    return any("぀" <= ch <= "ゟ" or "゠" <= ch <= "ヿ" for ch in s)


def contains_ng(text):
    if not text:
        return False
    t = text.lower()
    return any(ng.lower() in t for ng in NG_KEYWORDS)


# ----- 前セッション 2026-04-27 22:43Z 取得時点の API データ -----
VIDEOS = [
    {
        "id": "kwIQeqx-_SA",
        "title": "非エンジニアVtuberがAIで作ったアプリ📝リリース一週間でアクティブユーザー数4000人突破して",
        "channel": "びそtube",
        "channel_id": "UCXViF649Tnn9kwv0aXNCO2g",
        "subscribers": 3200,
        "published": "2026-04-22T15:03:52Z",
        "duration_sec": 1032,
        "views": 15380,
        "comments": 55,
        "in_db": False,
    },
    {
        "id": "eJ1sZ5ryDtw",
        "title": "嘘だらけのまとめサイトにまとめられる新人Vtuber【いかがでしたか？】",
        "channel": "してはる ",
        "channel_id": "UCm9fknC7ObBFKCtEXN-6Hgg",
        "subscribers": 65300,
        "published": "2026-04-16T12:07:38Z",
        "duration_sec": 517,
        "views": 66916,
        "comments": 684,
        "in_db": True,
    },
    {
        "id": "xILmEWV1Q4w",
        "title": "【最強】兼業VTuberが気づいた人生の攻略法",
        "channel": "天菜はかせの専業VTuber目指しチャンネル",
        "channel_id": "UCt9MnCGIZNDUsJ2ijwI_D7g",
        "subscribers": 7880,
        "published": "2026-04-23T11:01:28Z",
        "duration_sec": 351,
        "views": 15679,
        "comments": 35,
        "in_db": False,
    },
    {
        "id": "IGcpH-9nXWs",
        "title": "【投資/貯金】元貯金0の浪費家が｢新NISA｣と株式投資を始めたらコンビニに行く回数が減ったゆるーい",
        "channel": "豹矢りいす / 日常ch",
        "channel_id": "UCl8bSkB41JhyziKIcFmVYOQ",
        "subscribers": 56700,
        "published": "2026-04-24T10:00:06Z",
        "duration_sec": 784,
        "views": 39845,
        "comments": 167,
        "in_db": False,
    },
    {
        "id": "gI5Ql4GHkek",
        "title": "【マシュマロ回答】チャンネルBANされたら？/好きを言語化する方法/学生時代の部活/複数推しOKです",
        "channel": "何者にもなれないちゃん",
        "channel_id": "UCIUc1gP8EwTk81D9-4oGCig",
        "subscribers": 23900,
        "published": "2026-04-19T16:57:20Z",
        "duration_sec": 896,
        "views": 2092,
        "comments": 40,
        "in_db": False,
    },
    {
        "id": "mLbH4gOrJpc",
        "title": "【Poshi Log】#2 鴨川デルタ",
        "channel": "咲鐘 星架",
        "channel_id": "UCQvm0E8dQ9tSyTtq6mnHaCQ",
        "subscribers": 41400,
        "published": "2026-04-17T15:01:33Z",
        "duration_sec": 339,
        "views": 19673,
        "comments": 90,
        "in_db": False,
    },
    {
        "id": "dsLiWin6x0s",
        "title": "初投稿",
        "channel": "松岡",
        "channel_id": "UCtCf6pYWnmx3Gsmk16H854A",
        "subscribers": 45500,
        "published": "2026-04-26T08:30:17Z",
        "duration_sec": 53,
        "views": 69158,
        "comments": 1000,
        "in_db": False,
    },
    {
        "id": "7tCW8DC4VYw",
        "title": "キョン！食うわよ！散歩もするわよ！【vlog/不夜ロクヤ】",
        "channel": "不夜ロクヤ",
        "channel_id": "UCL3tyxnPmEAhiSFnKwfzg0w",
        "subscribers": 20900,
        "published": "2026-04-16T11:00:00Z",
        "duration_sec": 651,
        "views": 12399,
        "comments": 115,
        "in_db": False,
    },
]


def main():
    # 評価基準日: ユーザー提供時点 (2026-04-27 22:43Z) を「現在」とする。
    # SEARCH_DAYS=14 の cutoff を計算
    now = datetime(2026, 4, 27, 22, 43, 0, tzinfo=timezone.utc)
    cutoff = (now - timedelta(days=SEARCH_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")

    print("=" * 80)
    print(f"改修後フィルタによる8動画の判定結果（基準日: {now.isoformat()}）")
    print(f"パラメータ: VIEW_MULTIPLIER={VIEW_MULTIPLIER}, SEARCH_DAYS={SEARCH_DAYS}日")
    print(f"NG_KEYWORDS = {NG_KEYWORDS}")
    print(f"NG判定スコープ: title + channel_name のみ（description/tags は除外）")
    print(f"cutoff = {cutoff}")
    print("=" * 80)
    print()

    pass_count = 0
    fail_summary = []

    for v in VIDEOS:
        title = v["title"]
        ch = v["channel"]
        sub = v["subscribers"]
        published = v["published"]
        dur = v["duration_sec"]
        views = v["views"]
        comments = v["comments"]

        # 改修後フィルタチェック
        in_period = published >= cutoff
        ok_duration = MIN_DURATION_SEC <= dur <= MAX_DURATION_SEC
        ok_sub = MIN_SUBSCRIBERS <= sub <= MAX_SUBSCRIBERS
        ok_views = views >= sub * VIEW_MULTIPLIER
        ok_comments = comments >= MIN_COMMENTS
        is_jp = has_kana(title) or has_kana(ch)
        # 改修1: NG判定は title + channel のみ
        is_ng = contains_ng(title) or contains_ng(ch) or "切り抜き" in ch

        all_pass = ok_duration and ok_sub and ok_views and ok_comments and is_jp and not is_ng and in_period

        # FAIL理由を集約
        reasons = []
        if not in_period:
            reasons.append(f"期間外({published[:10]})")
        if not ok_duration:
            reasons.append(f"動画長{dur}秒")
        if not ok_sub:
            reasons.append(f"登録者{sub:,}")
        if not ok_views:
            reasons.append(f"再生{views:,}<要{int(sub*VIEW_MULTIPLIER):,}")
        if not ok_comments:
            reasons.append(f"コメ{comments}<5")
        if not is_jp:
            reasons.append("日本語なし")
        if is_ng:
            reasons.append("NGワード該当")

        mark = "✅PASS" if all_pass else "❌FAIL"
        print(f"{mark} {v['id']} | {ch[:18]:<18} | 登録{sub:>6,} 再生{views:>7,}/{int(sub*VIEW_MULTIPLIER):>7,} 比{views/sub:>4.2f}x | {published[:10]} {dur//60:>2}:{dur%60:02d}")
        print(f"       {title[:60]}")
        if not all_pass:
            print(f"       FAIL理由: {', '.join(reasons)}")
            fail_summary.append((v["id"], ch, reasons))
        else:
            pass_count += 1
            # 取得経路の判定: DB登録済 → 巡回、未登録 → search.list 発掘必要
            if v["in_db"]:
                print(f"       取得経路: ✅ playlistItems巡回でDB既存チャンネルから取得可")
            else:
                print(f"       取得経路: ⚠ DB未登録 → search.list 検索で発掘される必要あり")
        print()

    print("=" * 80)
    print(f"結果: {pass_count}/{len(VIDEOS)} 件がフィルタ通過")
    print("=" * 80)
    if fail_summary:
        print()
        print("【FAIL動画の理由】")
        for vid, ch, reasons in fail_summary:
            print(f"  - {vid} ({ch}): {', '.join(reasons)}")
    print()
    print("【取得経路の補足】")
    print("  - 巡回(playlistItems): vtuber_channels に登録済みのチャンネルのみ対象")
    print("  - 検索発掘(search.list): 曜日別2クエリ × 直近7日 × order=viewCount × videoDuration=medium")
    print("    → 改修2の曜日ローテで個人VTuberクラスタを継続的に拡充し、いずれDBに取り込まれる")


if __name__ == "__main__":
    main()
