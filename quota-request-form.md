# YouTube Data API v3 — クォータ増加申請フォーム記入カンペ

申請フォーム URL: https://support.google.com/youtube/contact/yt_api_form?hl=ja

> **記入のコツ**: 参考記事 ([Zenn](https://zenn.dev/yossyxp/articles/ad490db5b570ea)) によると、申請後のメールやり取りは英語で進行するため、**最初から英語で記入する方がスムーズ**。本ファイルは各項目に日本語版／英語版の両方を用意しているので、英語版をコピペするのを推奨。

---

## 1. このフォームにご記入いただく理由 / Reason for filling out this form

✅ **「Request to increase API quota」（追加の API 割り当てをリクエスト）** を選択

---

## 2. 組織情報 / Organization Information

| 項目 | 記入内容 |
|---|---|
| **First Name (名)** | Kain |
| **Last Name (姓)** | (空欄でOK / leave blank if asked) |
| **Organization (組織名)** | Individual (Hobby Project) |
| **Email Address** | amtokyo713@gmail.com |
| **Phone Number** | (空欄でOK / leave blank, optional) |
| **Country / Region** | Japan |
| **Address** | (任意。求められたら都道府県名のみで OK 例: Tokyo, Japan) |
| **Website URL** | https://vtuberoffice07-cell.github.io/youtube-shorts-ranking/ |

---

## 3. YouTube に関連する組織業務 / Organization's YouTube-Related Business

> **質問例 (English)**: "Briefly describe your organization's YouTube-related business."

### 日本語版（参考）
```
個人で運営している趣味のオープンソースプロジェクトです。組織として YouTube に関連する商業的業務は行っておらず、個人VTuber（バーチャルYouTuber）の動画動向を観察するためのバズランキング集計ツールを個人開発しています。広告収益や有料サービスはありません。
```

### 英語版（推奨・コピペ用）
```
This is an individually operated, non-commercial open-source hobby project. We do not have any organizational YouTube-related business. The Service ("VTuber Buzz Ranking") is a personal tool I developed to observe the buzz trends of individual VTubers (Virtual YouTubers) on YouTube, TikTok, and Twitter. There are no advertisements, no paid plans, and no monetization of any kind. The full source code is published as open source on GitHub.
```

---

## 4. 過去の監査受審 / Have you been audited since June 2019?

✅ **No** (初回申請のため)

---

## 5. API クライアント情報 / API Client Information

| 項目 | 記入内容 |
|---|---|
| **API Client Name** | VTuber Buzz Ranking |
| **Project Number** | ※ Google Cloud Console で確認（→「プロジェクト番号確認手順」セクション参照） |
| **Project ID** | ※ 同上 |
| **Public or Private** | Public (Open source on GitHub, web viewer publicly accessible) |
| **Repository URL** | https://github.com/vtuberoffice07-cell/youtube-shorts-ranking |

---

## 6. 利用ユーザー数・パターン / User Base and Usage Pattern

> **質問例 (English)**: "How many users does your service have? Describe the usage pattern."

### 英語版（推奨）
```
Estimated users: 1-50 (mainly the operator and a small number of VTuber-watching enthusiasts who view the static HTML viewer hosted on GitHub Pages).

Usage pattern: The Service is a fully automated batch tool. The backend Python scripts run twice per day (07:00 and 19:00 JST) on GitHub Actions, fetching public data from YouTube Data API v3 and storing it in SQLite databases / JSON files. The frontend is a static viewer.html that loads these JSON files; no server-side processing or per-user API calls are performed.

Data refresh frequency: Twice per day (cron-scheduled GitHub Actions, JST 07:00 and 19:00).

Data retention: Historical data is stored indefinitely in the project's GitHub repository as SQLite DB and JSON files for time-series analysis. Removed videos are deleted from our data when noticed.
```

---

## 7. 割り当て増加が必要な理由 / Reason for Quota Increase

> **質問例 (English)**: "Please describe in detail why you need a quota increase. Include expected request volume, calculation, and timeline."

### 英語版（推奨・最重要項目）
```
== Current Daily Quota Consumption (measured) ==

The Service consists of three Python scripts run twice per day on GitHub Actions:

1. main.py (YouTube Shorts ranking, individual VTubers focus)
   - search.list: 8 queries × 2 day-chunks × 2 runs/day = 32 calls = 3,200 quota
   - videos.list: ~28 calls = ~28 quota
   - channels.list: ~12 calls = ~12 quota
   Subtotal: ~3,240 quota/day

2. main_all.py (YouTube Shorts ranking, all VTubers including major agencies)
   - Same structure as main.py
   Subtotal: ~3,240 quota/day

3. main_long.py (YouTube 4-30 minute medium video ranking, channel-crawl based)
   - search.list: 2 weekday-rotated queries × 2 runs/day = 4 calls = 400 quota
   - playlistItems.list: 200 channels × 2 runs/day = 400 calls = 400 quota
   - videos.list: ~120 calls = ~120 quota
   - channels.list: ~18 calls = ~18 quota
   Subtotal: ~938 quota/day (measured 2026-04-28: 469/run)

Current total daily consumption: ~7,418 quota/day
This is already 74% of the 10,000-unit free tier.

== Why we need 50,000 quota/day ==

We have several planned expansions that will push us over the free tier limit:

a. Increasing search query rotation (current: 2 queries/day × 7-day rotation)
   to 4-5 queries/day to better cover the long-tail of niche individual VTubers.
   Estimated additional cost: +2,000 quota/day (search.list × 100 quota/call).

b. Expanding the channel crawl from TOP 200 to TOP 500 individual VTuber channels
   (currently 376 channels in our database, growing).
   Estimated additional cost: +600 quota/day (playlistItems.list × 1 quota/call).

c. Adding a third daily run (current: 2 runs/day, planned: 3 runs/day) to capture
   buzz spikes more accurately.
   Estimated additional cost: +50% of current = +3,700 quota/day.

d. Future feature: live-stream archive ranking with multiple genre keywords
   (gaming, talk, ASMR, art, singing — but excluding cover songs per recent UX decision).
   Estimated additional cost: +5,000 quota/day.

Sum of expansions: ~7,418 (current) + 2,000 + 600 + 3,700 + 5,000 = ~18,718 quota/day.

We are requesting 50,000 quota/day to provide a safety margin (approximately 2.5×
the projected expanded usage), accounting for unexpected spikes (e.g., when a
VTuber goes viral and we need to retry channel-crawls), retries on transient
errors, and reasonable headroom for the next 12 months without re-applying.

== Cost-saving measures already implemented ==

We have specifically designed the Service to minimize quota consumption:

1. Channel-crawl architecture: For the medium-video ranking (main_long.py), we
   use playlistItems.list (1 quota/call) instead of search.list (100 quota/call)
   for ongoing data collection. We only use search.list for new-channel discovery,
   limited to 2 queries/day with weekday rotation.

2. Batch fetching: All videos.list and channels.list calls are batched in groups
   of 50 to minimize the number of API calls.

3. SQLite caching: Channel metadata (subscriber count, uploads playlist ID) is
   cached in a local SQLite database, only re-fetched for new or stale channels.

4. Period-chunk optimization: search.list calls are split with DAYS_PER_CHUNK=1
   to avoid duplicate page-token-based pagination.

5. Strict day-window filter: SEARCH_DAYS=2 for Shorts and SEARCH_DAYS=14 for
   medium videos, no full-history backfill.

6. Quota-exceeded graceful degradation: All scripts catch quotaExceeded errors
   and continue with partial results rather than failing.

Source code is public for verification:
https://github.com/vtuberoffice07-cell/youtube-shorts-ranking

The quota tracker output of a representative run is included as evidence:
- 2026-04-28 main_long.py: search.list=2 (200), playlistItems.list=200 (200),
  videos.list=60 (60), channels.list=9 (9), total=469 quota.
```

---

## 8. データの利用目的 / Data Use

### 英語版（推奨）
```
The retrieved data is used solely for:
1. Displaying buzz rankings of individual VTubers' videos on a static HTML viewer
2. Time-series analysis (historical comparison of view counts, comment trends)
3. Generating monthly trend reports (keyword frequency analysis)

The data is NEVER:
- Sold or transferred to third parties
- Used for advertising or marketing-purpose profiling
- Used for AI/ML model training
- Combined with personal information of viewers (we don't collect any)

When a video or channel is removed from YouTube, we remove the corresponding
data from our service upon notice. We honor data deletion requests from video
creators within 7 days.

Privacy Policy: https://vtuberoffice07-cell.github.io/youtube-shorts-ranking/privacy-policy.html
Terms of Service: https://vtuberoffice07-cell.github.io/youtube-shorts-ranking/terms-of-service.html
```

---

## 9. ブランドガイドライン遵守 / Branding Compliance

### 英語版
```
The Service complies with YouTube's Branding Guidelines:
- All YouTube video links open the original video on youtube.com (no in-app
  playback or re-encoding)
- Where YouTube content is referenced, the YouTube logo and a link to the
  source video are displayed
- We do not modify video titles, descriptions, or thumbnails before display
- We do not aggregate YouTube data with data from competing video platforms
  in a way that would create a derivative ranking
```

---

## 10. 連絡先・運営者情報 / Contact and Operator Information

| 項目 | 内容 |
|---|---|
| **Operator handle** | Kain |
| **Email** | amtokyo713@gmail.com |
| **Website / Service URL** | https://vtuberoffice07-cell.github.io/youtube-shorts-ranking/ |
| **Privacy Policy URL** | https://vtuberoffice07-cell.github.io/youtube-shorts-ranking/privacy-policy.html |
| **Terms of Service URL** | https://vtuberoffice07-cell.github.io/youtube-shorts-ranking/terms-of-service.html |
| **Source Code URL** | https://github.com/vtuberoffice07-cell/youtube-shorts-ranking |

---

## 11. プロジェクト番号 / プロジェクト ID 確認手順

クォータ申請フォームには「Project Number」「Project ID」の入力が必須です。以下の手順で確認してください：

### 手順 A: Google Cloud Console から確認（推奨）

1. https://console.cloud.google.com/ を開く
2. amtokyo713@gmail.com でログイン
3. 上部の **プロジェクト選択ドロップダウン** をクリック
4. プロジェクト一覧が表示されるので、YouTube Data API を有効化したプロジェクトを選択
   - 多くの場合「My Project」「My First Project」のような名前
5. 選択後、Cloud Console のホーム画面に「プロジェクト情報」カードが表示される
   - **プロジェクト名 (Project name)**
   - **プロジェクト ID (Project ID)**: 一意の文字列（例: `my-project-12345`）
   - **プロジェクト番号 (Project number)**: 数値（例: `123456789012`）

### 手順 B: API キーから逆引き

1. https://console.cloud.google.com/apis/credentials を開く
2. プロジェクトドロップダウンから該当プロジェクトを選択
3. API キー一覧から `.env` の `YOUTUBE_API_KEY` と先頭一致するキーを探す
4. そのキーが属するプロジェクトの ID / 番号を上部に表示

### 手順 C: もし複数プロジェクトがあって判別に迷う場合

`.env` の API キーの末尾4文字を私（カイン）にお伝えください。私が API キーを叩いて
プロジェクト情報を取得することもできます（クォータ消費は1未満）。

---

## 12. 申請後の流れ

1. フォーム送信完了
2. 数日〜2週間以内に Google から **英語のメール** で返信
   - 追加質問 ＝ 上記内容を補足する形で英語で返信
   - 承認 ＝ クォータが自動で増加（最大 1,000,000/day まで）
3. 12ヶ月以内に再申請する場合は専用フォーム ([yt_api_audited_developer_requests_form](https://support.google.com/youtube/contact/yt_api_audited_developer_requests_form)) を使用

---

## 13. 一発で承認されるためのチェックリスト

- [x] プライバシーポリシーが公開URLでアクセス可能（GitHub Pages で公開済み）
- [x] 利用規約が公開URLでアクセス可能（同上）
- [x] サービス URL が動作（viewer.html が正しく表示される）
- [x] ソースコードが公開されている（オープンソース、GitHub 上）
- [x] クォータ計算根拠が明確（実測値と将来計画の数値）
- [x] クォータ節約策を具体的に記載（チャンネル巡回方式、バッチ処理など）
- [x] YouTube ブランドガイドライン遵守の明示
- [x] 個人運営・非営利の明記（広告ゼロ）
- [x] データ削除依頼への対応方針の明示（7日以内）
- [x] 英語で記入（メールやり取りが英語のため）

---

> **注**: フォーム送信後、`viewer.html` および policy ページは申請審査中ずっと公開しておく必要があります。承認メール到着後に GitHub Pages を非公開化するかは、運用継続の観点から判断してください（推奨：継続公開、Google が後追い確認することがあります）。
