# YouTube Data API v3 クォータ増加申請 — 提出手順書

このファイルは、カインが Google にクォータ増加申請を提出する際の **5ステップの手順書** です。
所要時間: 約 10 分（プロジェクト番号の確認時間を除く）

---

## ✅ 事前確認

このリポジトリには以下が用意されています：

| ファイル | 公開URL（GitHub Pages 反映後） |
|---|---|
| `viewer.html` | https://vtuberoffice07-cell.github.io/youtube-shorts-ranking/viewer.html |
| `privacy-policy.html` | https://vtuberoffice07-cell.github.io/youtube-shorts-ranking/privacy-policy.html |
| `terms-of-service.html` | https://vtuberoffice07-cell.github.io/youtube-shorts-ranking/terms-of-service.html |
| `quota-request-form.md` | （フォーム記入用カンペ、ローカルで参照） |

> **注意**: 本ファイルをコミット&プッシュ後、GitHub Pages のビルドに **約 1〜3 分** かかります。3つの URL がすべて 200 OK で開けることを必ず確認してから、フォーム送信に進んでください。

---

## STEP 1: GitHub Pages 公開を確認（1分）

ブラウザで以下のURLを開き、それぞれエラーなく表示されることを確認：

1. https://vtuberoffice07-cell.github.io/youtube-shorts-ranking/viewer.html
2. https://vtuberoffice07-cell.github.io/youtube-shorts-ranking/privacy-policy.html
3. https://vtuberoffice07-cell.github.io/youtube-shorts-ranking/terms-of-service.html

❗ もし 404 が出る場合は GitHub Actions の Pages ビルドを確認：
https://github.com/vtuberoffice07-cell/youtube-shorts-ranking/actions

---

## STEP 2: Google Cloud プロジェクト番号を確認（2分）

1. https://console.cloud.google.com/ を開く
2. **amtokyo713@gmail.com** でログイン
3. 上部の **プロジェクト選択ドロップダウン**（「プロジェクトの選択」と書かれた部分）をクリック
4. 開かれた一覧から、YouTube Data API を有効化しているプロジェクトを選択
   - 一般的な名前: `My Project`, `My First Project`, `vtuber-ranking` など
   - もしプロジェクトが1つしかない場合はそれが該当
5. 選択後、Cloud Console のホーム画面の右側「**プロジェクト情報**」カードに以下が表示される：
   - **プロジェクト名 (Project Name)**
   - **プロジェクト ID (Project ID)** — 文字列、例 `vtuber-ranking-12345`
   - **プロジェクト番号 (Project Number)** — 数値、例 `123456789012`

📋 **メモする項目**: Project Name / Project ID / Project Number の3つ

---

## STEP 3: 申請フォームを開いてカンペを横に並べる（1分）

申請フォーム URL:
👉 **https://support.google.com/youtube/contact/yt_api_form?hl=ja**

別タブで `quota-request-form.md`（または GitHub 上で
https://github.com/vtuberoffice07-cell/youtube-shorts-ranking/blob/main/quota-request-form.md ）を開き、
両方を画面上に並べておく。

---

## STEP 4: フォームに記入（5分）

`quota-request-form.md` の各セクションに対応する項目を、以下の順でフォームに転記：

| フォームの欄 | quota-request-form.md のセクション |
|---|---|
| Reason for filling out this form | §1 |
| First Name / Email / Country / Website | §2 |
| YouTube-related business | §3（**英語版を使用**） |
| Audited since June 2019? | §4 → No |
| API Client Name / Project Number / Project ID / Public or Private | §5（STEP 2 で確認した値を入力） |
| User base and usage pattern | §6（**英語版を使用**） |
| Reason for quota increase（最重要！詳細記入） | §7（**英語版を必ずコピペ**） |
| Data use / disclosure | §8（**英語版を使用**） |
| Branding compliance（質問されたら） | §9 |

🔑 **重要**: §7「Reason for quota increase」が最も重要。**英語版をそのままコピペ**することを推奨。

---

## STEP 5: 同意チェック → 送信（1分）

1. 利用規約・プライバシーポリシー関連の同意チェックボックスにチェック
2. 一番下の **送信ボタン（Submit）** をクリック
3. 「申請を受け付けました」のメッセージが表示されれば完了
4. 確認メールが amtokyo713@gmail.com に届く

---

## ⏰ 送信後の流れ

| 時期 | 内容 |
|---|---|
| 即時 | 自動返信メール（受領通知） |
| 1日〜2週間 | Google から **英語の追加質問メール** が来る場合あり |
| 1〜4週間 | 承認 or 却下の最終決定メール |
| 承認後 | クォータが自動で増加（管理画面で反映確認） |

### 追加質問が来た場合の対応

英語のメールで「Could you please clarify ...」のような質問が来ることがあります。
内容は §7 の補足が大半。日本語で文面を作るので、メールが来たら教えてください。

### もし却下された場合

却下理由のメールが来ます。よくある理由：
- データ取得目的が不明確 → §3, §6, §7 をより詳細に書き直し再申請
- 利用規約・プライバシーポリシーが不十分 → 該当箇所を補強
- ブランドガイドライン違反の指摘 → §9 を再確認

12ヶ月以内であれば専用フォームで再申請可能です。

---

## 🔒 承認後の運用

1. **クォータが増えた**ことを Google Cloud Console で確認
   - https://console.cloud.google.com/apis/api/youtube.googleapis.com/quotas
   - `Queries per day` が 50,000 に変更されていること
2. （任意）GitHub Pages を非公開化したい場合
   - リポジトリを Private に変更（Settings → General → Change visibility）
   - またはリポジトリは Public のまま、`viewer.html` 等を別ブランチに移動
   - **推奨**: Google が後から URL を再確認することがあるため、できれば公開維持

---

## 🆘 困ったときは

- カンペの文面で不明な点があれば: `quota-request-form.md` の各セクションのコメント参照
- プロジェクト番号がわからない: STEP 2 のスクショを撮って共有してください
- 追加質問のメール対応: 内容を貼り付けて相談してください
