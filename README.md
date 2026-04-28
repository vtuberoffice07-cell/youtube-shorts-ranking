# VTuber Buzz Ranking

個人 VTuber のバズ動向を観察するためのランキング集計ツール。
YouTube Shorts / 中尺動画 (4-30分) / TikTok / Twitter(X) の4プラットフォームを対象に、毎日自動でランキングを更新します。

🌐 **公開ページ**: https://vtuberoffice07-cell.github.io/youtube-shorts-ranking/

> **Note**: このリポジトリは個人運営の非営利プロジェクトです。広告表示や有料プランはありません。
> YouTube・TikTok・Twitter(X) からの公式サービスではなく、各社による承認・後援を受けたものではありません。

---

## 📋 目次

- [機能概要](#-機能概要)
- [アーキテクチャ](#-アーキテクチャ)
- [クイックスタート](#-クイックスタート)
- [GitHub Actions ワークフロー](#-github-actions-ワークフロー)
- [API クォータ](#-api-クォータ)
- [トラブルシュート](#-トラブルシュート)
- [プライバシー・規約](#-プライバシー規約)
- [ライセンス](#-ライセンス)

---

## 🎯 機能概要

| ランキング | 対象 | スクリプト | データ |
|---|---|---|---|
| YouTube Shorts (個人VTuber) | 60秒以下の縦動画 | `main.py` | `ranking_history.json` |
| YouTube Shorts (全体) | 同上、企業勢含む | `main_all.py` | `ranking_all_history.json` |
| YouTube 中尺動画 | 4-30分の横動画 | `main_long.py` | `long_history.json` + `youtube_long.db` |
| TikTok | ハッシュタグ検索 | `tiktok_ranking.py` | `tiktok_history.json` + `tiktok.db` |
| Twitter(X) | キーワード検索 | `tweet_ranking.py` | `tweet_history.json` + `tweets.db` |

各ランキングは **登録者数に対する再生数倍率（伸び率）** を主指標として並び替えており、登録者数の多寡に依存しないバズの検出を狙っています。

---

## 🏗 アーキテクチャ

```
┌─────────────────────┐    cron 1日2回    ┌──────────────────┐
│ GitHub Actions      │ ─────────────────▶ │ Python Scripts   │
│ (daily-ranking.yml) │                    │ (main*.py 等)    │
└─────────────────────┘                    └────────┬─────────┘
                                                    │
                              ┌─────────────────────┼─────────────────┐
                              ▼                     ▼                 ▼
                    ┌──────────────────┐  ┌──────────────────┐  ┌─────────┐
                    │ YouTube Data API │  │ Apify (TikTok/X) │  │ SQLite  │
                    │      v3          │  │                  │  │  + JSON │
                    └──────────────────┘  └──────────────────┘  └────┬────┘
                                                                     │
                                                                     ▼ commit
                                                            ┌──────────────────┐
                                                            │  GitHub Pages    │
                                                            │  (viewer.html)   │
                                                            └──────────────────┘
```

- **データ収集**: GitHub Actions の cron が毎日 JST 7:00 と 19:00 にスクリプトを実行
- **データ保存**: SQLite に正規化、JSON に履歴アーカイブ
- **配信**: GitHub Pages で `viewer.html` を静的配信（クライアントが JSON を fetch）

---

## 🚀 クイックスタート

### 必要なもの

- Python 3.12
- [YouTube Data API v3](https://console.cloud.google.com/apis/library/youtube.googleapis.com) のキー
- [Apify](https://apify.com/) のアカウントとトークン (TikTok / Twitter 用)

### ローカル実行

```bash
# 1. 依存ライブラリをインストール
pip install -r requirements.txt

# 2. 環境変数を設定
cp .env.example .env
# .env を編集して YOUTUBE_API_KEY と APIFY_API_TOKEN を記入

# 3. スクリプトを個別実行
python main.py        # YouTube Shorts (個人VTuber)
python main_all.py    # YouTube Shorts (全体)
python main_long.py   # YouTube 中尺動画
python tiktok_ranking.py
python tweet_ranking.py

# 4. ブラウザで結果を確認
# viewer.html を任意の HTTP サーバーで配信（例: python -m http.server 8000）
```

### 環境変数

| 変数 | 用途 | 取得方法 |
|---|---|---|
| `YOUTUBE_API_KEY` | YouTube Data API v3 | [Cloud Console](https://console.cloud.google.com/apis/credentials) |
| `APIFY_API_TOKEN` | Apify (TikTok / Twitter スクレイピング) | [Apify Console](https://console.apify.com/account/integrations) |

> **重要**: `.env` は git で除外されています。本番では GitHub Actions の Repository Secrets に登録してください。

---

## ⚙️ GitHub Actions ワークフロー

### スケジュール

`.github/workflows/daily-ranking.yml` で定義：

- **JST 7:00** (UTC 22:00): メイン実行
- **JST 19:00** (UTC 10:00): バックアップ実行（朝のジョブが GitHub の都合でスキップされた場合の保険）
- **手動実行**: GitHub Actions タブから `Run workflow` ボタンで dispatch 可能

### 構成

1. YouTube ランキング × 3 種類（Shorts 個人 / Shorts 全体 / 中尺）
2. TikTok ランキング（失敗時は continue）
3. Twitter ランキング（失敗時は continue）
4. ヒントレポート生成（API 不要）
5. 診断ステップ（ファイル更新状況をログ出力）
6. 自動コミット & プッシュ
7. **致命的失敗時の Issue 自動作成**（YouTube 個人/全体が失敗した場合のみ）

### タイムアウト

ジョブ全体: 30分。各ステップ: 5〜10分。これにより API ハング時のジョブ暴走と GitHub Actions 無料枠の浪費を防止しています。

### 失敗通知

YouTube ランキング (個人 / 全体) のいずれかが失敗した場合、自動で GitHub Issue が作成されます。同名の open Issue があれば重複作成されません。

---

## 📊 API クォータ

YouTube Data API v3 はデフォルトで **10,000 unit/日** の無料枠が割り当てられています（PT 0:00 = JST 16:00 リセット）。

各スクリプトの 1 回あたりの消費量目安:

| スクリプト | 1回の消費 | 主な API メソッド |
|---|---|---|
| `main.py` | 約 200 unit | search.list (100 × 2) |
| `main_all.py` | 約 200 unit | search.list (100 × 2) |
| `main_long.py` | 約 470 unit | playlistItems.list (1) × 多数 + search.list (100) × 2 |

> **クォータ最適化のポイント**: `main_long.py` は登録者上位チャンネルの uploads playlist を巡回する設計で、search.list (100 unit) ではなく playlistItems.list (1 unit) を主に使うことで大幅にコストを削減しています。

クォータ増加申請の手順は [`SETUP_QUOTA_REQUEST.md`](./SETUP_QUOTA_REQUEST.md) を参照してください。

---

## 🚀 パフォーマンス設計

### JSON 分割読込み（latest + 全期間）

`viewer.html` の初期表示を高速化するため、各 history JSON は 2 段階で生成・読込みされる:

| ファイル | サイズ | 内容 | 読込みタイミング |
|---|---|---|---|
| `*_latest.json` | ~1MB | 直近 30 日 | **初回 fetch（即時表示）** |
| `*_history.json` | 全期間 | 全データ | latest 表示後にバックグラウンド遅延 fetch |

これにより初期表示は数百ms〜1秒で完了し、ユーザーが古い日付に切り替えた時点で全期間データが既にロード済みになる。

`vtuber_common.py` の `write_latest_snapshot()` が各 main_*.py / tweet_ranking.py から呼ばれて latest を生成する。

### .git リポジトリサイズ

JSON 履歴ファイルを毎日コミットしているため、.git は徐々に肥大化する（執筆時点で約 57MB、年 +13MB ペース見込み）。

**現状の方針**: 個人運営の小規模リポジトリのため許容範囲（GitHub の上限 1GB に対して十分小さい）。`.gitattributes` で JSON のデルタ圧縮を最適化済み。

**将来の選択肢**（必要になった時点で検討）:
- **A 案**: `gh-pages` ブランチに JSON を分離。main はコード専用に。**過去履歴は触らない**ので 57MB は残るが今後増えない
- **B 案**: BFG Repo-Cleaner で過去 JSON を履歴から削除。.git ~5MB に縮むが**強制 push 必須・既存クローン無効化**
- **C 案**: 現状維持。アーカイブ運用は将来検討

---

## 🛠 トラブルシュート

### ❌ ワークフローが失敗した

1. GitHub Issue が自動作成されているか確認
2. Workflow run のログを確認（Actions タブ → 該当 run → 失敗ステップを展開）
3. 主な原因候補:
   - **`quotaExceeded`**: YouTube API クォータ超過。JST 16:00 のリセットを待つか、増加申請を検討
   - **`HttpError 403`**: API キー失効・権限不足。Repository Secrets を確認
   - **`HttpError 429`**: レート制限。短時間の連続実行を避ける
   - **Apify 関連エラー**: Apify Console でアクター実行状況を確認

### ❌ ローカルで動かない

```bash
# 環境変数が設定されているか確認
python -c "import os; print('YT:', bool(os.getenv('YOUTUBE_API_KEY')), 'APIFY:', bool(os.getenv('APIFY_API_TOKEN')))"

# .env が読み込まれない場合は python-dotenv を確認
python -c "from dotenv import load_dotenv; load_dotenv(); import os; print(os.getenv('YOUTUBE_API_KEY'))"
```

### ❌ viewer.html でデータが表示されない

- ブラウザの開発者ツール → Console でエラー確認
- `*_history.json` ファイルが存在するか確認
- AUTH_REQUIRED フラグの状態を確認 (`viewer.html` 上部の定数)

### 📊 診断スクリプト

```bash
python diagnose_long_offline.py    # 中尺動画フィルタの動作を検証
python diagnose_long.py            # 実 API でフィルタを検証（クォータ消費注意）
```

---

## 📜 プライバシー・規約

- [プライバシーポリシー](./privacy-policy.html)
- [利用規約](./terms-of-service.html)

このサービスは [YouTube API Services](https://developers.google.com/youtube/terms/api-services-terms-of-service) を利用しています。利用者は [YouTube 利用規約](https://www.youtube.com/t/terms) と [Google プライバシーポリシー](https://policies.google.com/privacy) に同意したものとみなされます。

### 削除依頼

動画作成者・チャンネル所有者から本サービスへの自身の情報削除依頼があった場合、原則として 7 日以内に対応します。

📧 連絡先: amtokyo713@gmail.com

---

## 📂 プロジェクト構造

```
.
├── .github/
│   ├── workflows/
│   │   └── daily-ranking.yml        # 毎日のランキング更新ワークフロー
│   └── dependabot.yml               # 依存ライブラリの自動更新
├── main.py                          # YouTube Shorts (個人VTuber)
├── main_all.py                      # YouTube Shorts (全体)
├── main_long.py                     # YouTube 中尺 (4-30分)
├── tiktok_ranking.py                # TikTok ランキング
├── tweet_ranking.py                 # Twitter(X) ランキング
├── generate_hints.py                # ヒントレポート生成
├── diagnose_long.py                 # 中尺フィルタ診断（実API）
├── diagnose_long_offline.py         # 中尺フィルタ診断（オフライン）
├── viewer.html                      # メイン表示ページ
├── privacy-policy.html              # プライバシーポリシー
├── terms-of-service.html            # 利用規約
├── robots.txt                       # 検索エンジンインデックス拒否
├── *.db                             # SQLite データベース
├── *_history.json                   # ランキング履歴
└── tasks/                           # 開発メモ（公開対象外）
```

---

## 📝 ライセンス

個人運営の非営利プロジェクトです。ソースコードの参照は自由ですが、以下を禁止します:

- 取得データの商用目的での再販・再配布
- 本サービスのデータを用いた第三者へのスパム・嫌がらせ
- ソースコードを無断改変して商用サービスとして提供する行為

詳細は [利用規約 第4条](./terms-of-service.html) を参照してください。

---

## 🤝 運営者

**カイン** (個人運営、ハンドル名)
📧 amtokyo713@gmail.com
