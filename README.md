# 🏈 NFL 観戦ガイド

見るべき NFL の試合を**優先度つき**で並べる Web ページ。ESPN の公開データ(APIキー不要・無料)から
各試合を **⭐️1〜5** で自動採点し、**勝敗・スコアを一切表示しない**ネタバレなしの短評(映画レビュー風)を付けます。
更新は **GitHub Actions で完全無料・自動**(土日月火 15:00 JST)。

## 特徴

- **優先度スコア**: プレイオフ影響度 / 地区順位への影響度 / 得点 / 接戦度 / 逆転数 の5要素を重み付け合成して ⭐️ を算出
- **ネタバレなし**: 勝者・最終スコアは非表示。「接戦」「撃ち合い」「劇的な巻き返し」等、結果が判らない特徴だけをタグと短評で表示
- **49ers 固定**: SF の試合は評価に関わらず各セクションの最上段に固定。それ以外は ⭐️(スコア)順
- **シーズン構成**: 進行中シーズンをトップ、過去シーズンは折りたたみ。preseason 各week / regular 各week / wildcard / divisional / conference / superbowl で分割表示

## ローカルで試す

追加ライブラリ不要(Python 標準ライブラリのみ)。

```bash
python3 generate.py                 # 今季 + 前季を生成
python3 generate.py --seasons 2025  # 特定シーズンだけ生成
```

`index.html` が出力されます。ブラウザで開けばそのまま閲覧できます。
確定した試合は `data/games_<年>.json` にキャッシュされ、次回以降は再取得しません(未確定・新規の試合のみ取得)。

---

## GitHub で自動更新をセットアップ(無料)

### 1. リポジトリを作る
1. [github.com](https://github.com) にログイン(アカウントが無ければ無料作成)
2. 右上「+」→ **New repository**
3. 名前を入力(例: `nfl-watch-guide`)。**Public** を選択(Public なら Pages と Actions が無料)
4. **Create repository**

### 2. このフォルダを push する

**事前確認**: `git --version` でバージョンが出ればOK。出なければ macOS は `xcode-select --install` で導入。

**名前とメールの初期設定**(初回のみ・任意):
```bash
git config --global user.name  "あなたの名前"
git config --global user.email "you@example.com"
```

このフォルダに移動してコミット & push:
```bash
cd "/Users/taijiro/Downloads/cloud code/nfl-watch-guide"
git init
git add .
git commit -m "初回コミット"
git branch -M main
git remote add origin https://github.com/<あなたのユーザー名>/nfl-watch-guide.git
git push -u origin main
```

**認証について**: `git push` すると GitHub のユーザー名とパスワードを聞かれますが、
**パスワードの代わりに「Personal Access Token(PAT)」が必要**です(通常のパスワードは不可)。

1. GitHub右上のアイコン → **Settings → Developer settings**(左メニュー最下部)
2. **Personal access tokens → Tokens (classic)** → **Generate new token (classic)**
3. **Note** に名前、**Expiration** を任意、**Select scopes** で **`repo`** にチェック
4. **Generate token** → 表示された文字列(`ghp_...`)をコピー(**一度しか表示されません**)
5. `git push` のパスワード入力欄にこの PAT を貼り付け

macOS ならキーチェーンに保存され、次回以降は聞かれません。
> コマンドラインが不慣れなら **GitHub Desktop**(公式アプリ)でも同じことができます。
> リポジトリをこのフォルダとして追加 → Commit → Publish、で完了です。

### 3. Actions に書き込み権限を与える(重要)
自動更新は生成した `index.html` をリポジトリに書き戻します。そのため:

- リポジトリの **Settings → Actions → General**
- 一番下 **Workflow permissions** で **「Read and write permissions」** を選び **Save**

### 4. GitHub Pages を有効化
- **Settings → Pages**
- **Source** を **「Deploy from a branch」**
- **Branch** を **`main`** / フォルダ **`/ (root)`** にして **Save**
- 数分後、ページが公開されます: `https://<あなたのユーザー名>.github.io/nfl-watch-guide/`

このURLをブックマークすれば、どの端末のブラウザからも閲覧できます。

### 5. 動作確認(任意)
- **Actions** タブ →「NFL 観戦ガイド更新」→ **Run workflow** で手動実行し、成功すればOK。

---

## 更新スケジュール

`.github/workflows/update.yml` の cron で制御:

```
0 5 * * *   # = 毎日 14:00 JST
```

GitHub の cron は **UTC 基準**。14:00 JST = 05:00 UTC。
時刻を変えたいときはこの行を編集してください（例: 毎日18:00 JST なら `0 9 * * *`）。
※GitHub の定期実行は混雑時に数分ずれることがあります(仕様)。

## ⭐️ の重み調整

`generate.py` 冒頭の `WEIGHTS` / `EXC_WEIGHTS` を編集すると採点基準を変えられます。

```python
WEIGHTS = {"excitement": 0.55, "importance": 0.30, "scoring": 0.15}
```

- `excitement`: 接戦度・リード変動・逆転(見て面白いか)
- `importance`: プレイオフ / 地区順位への影響度(重要度)
- `scoring`: 得点の多さ

## 対象シーズンを増やす

既定は「今季 + 前季」。過去をもっと遡りたい場合は `.github/workflows/update.yml` の実行コマンドを
`python generate.py --seasons 2026,2025,2024` のように変更してください。

## データについて

- 出典: ESPN の公開エンドポイント(`site.api.espn.com`)。非公式・無保証のため、仕様変更で取得できなくなる可能性があります。
- 重要度(プレイオフ / 地区順位への影響)は、ラウンド・週の後半度・地区対決かどうかから**近似**しています(試合時点の順位表そのものではありません)。
