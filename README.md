# SitePack — Website Static Packager

Webサイトを丸ごとダウンロードして、ローカルで閲覧可能な静的パッケージを作成するWindows向けGUIアプリケーションです。

## 特徴

- **GUI操作**: PySide6ベースの現代的なデスクトップUI
- **同一ホスト巡回**: 開始URLと同じホストのHTMLページのみを再帰的に巡回
- **外部アセット保存**: 外部ドメインの画像・CSS・JS・フォントも保存
- **URL書き換え**: HTML/CSS内のURLをローカル相対パスに自動変換
- **2つの取得モード**: 高速静的取得 / Playwrightブラウザレンダリング取得
- **プロジェクト管理**: 停止・再開・失敗URL再試行
- **ローカルプレビュー**: 簡易HTTPサーバーまたはfile://で閲覧
- **ZIPエクスポート**: 出力フォルダをZIPにまとめて配布

## 必要環境

- Python 3.12以上
- Windows 10/11（Linux/macOSでも動作可能）

## セットアップ

```bash
# リポジトリをクローン
git clone <repository-url>
cd sitepack-mirror

# 仮想環境を作成
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Linux/macOS

# 依存パッケージをインストール
pip install -e ".[dev]"

# Playwright ブラウザをインストール（レンダリング取得モードを使う場合）
playwright install chromium
```

## 実行方法

```bash
# GUIを起動
python -m sitepack

# または
sitepack
```

## 使い方

1. **開始URL**にダウンロードしたいサイトのURLを入力
2. **プロジェクト名**を入力
3. **保存先フォルダ**を選択
4. 必要に応じて設定タブで巡回設定を調整
5. **開始**ボタンをクリック

### 設定項目

| 設定 | 説明 | デフォルト |
|------|------|-----------|
| 同一ホストのみ | 同じホストのHTMLのみ巡回 | ON |
| サブドメイン含む | サブドメインも巡回対象に | OFF |
| 最大深度 | リンクをたどる最大深さ | 10 |
| 同時接続数 | 並列ダウンロード数 | 4 |
| リクエスト間隔 | リクエスト間の待機秒数 | 0.5秒 |
| robots.txt尊重 | robots.txtに従う | ON |
| レンダリング取得 | Playwrightで取得 | OFF |
| 外部アセット取得 | 外部CSSや画像も保存 | ON |

## 出力ディレクトリ構造

```
project_name/
  index.html              # ルートページ
  pages/                  # 同一ホストのHTMLページ
    about/
      index.html
    blog/
      index.html
  assets/                 # 同一ホストのアセット
    css/
      style.css
    js/
      main.js
    images/
      logo.png
  external/               # 外部ドメインのアセット
    cdn.example.com/
      lib.js
    fonts.googleapis.com/
      font.woff2
  _meta/                  # メタデータ
    manifest.json         # プロジェクト情報
    url_map.json          # URL→ローカルパス対応表
    crawl_log.jsonl       # クロールログ
    errors.json           # 失敗URL一覧
    settings.json         # 使用した設定
    summary.json          # 実行サマリー
```

## テスト

```bash
# テストを実行
pytest

# 詳細出力
pytest -v
```

## Windows向けビルド（PyInstaller）

```bash
# PyInstallerでexeを作成
pip install pyinstaller

pyinstaller --name SitePack \
  --onedir \
  --windowed \
  --add-data "src/sitepack;sitepack" \
  --hidden-import PySide6.QtWidgets \
  --hidden-import PySide6.QtCore \
  --hidden-import PySide6.QtGui \
  src/sitepack/__main__.py

# 出力: dist/SitePack/SitePack.exe
```

### ビルド用specファイル

より細かい制御が必要な場合は `sitepack.spec` を作成して使用：

```bash
pyinstaller sitepack.spec
```

## 制約事項

- ログイン必須サイトの完全再現は対象外
- SPAやAPI依存画面は見た目の保存優先（完全動作は保証しない）
- フォーム送信、決済、会員機能、WebSocket再現は非対応
- JS内の文字列URLの書き換えはMVPでは行わない

## 今後の拡張案

- [ ] Cookie/セッション対応の強化
- [ ] SPA対応（ルート列挙 + レンダリング取得の組み合わせ）
- [ ] サイトマップ（sitemap.xml）からのURL抽出
- [ ] ダウンロード済みファイルの差分更新
- [ ] FTPアップロード機能の内蔵
- [ ] マルチプロジェクト管理UI
- [ ] CSS/JSの圧縮オプション
- [ ] 画像の最適化（WebP変換等）
- [ ] カスタムHTTPヘッダー設定
- [ ] プロキシ設定
- [ ] ダウンロード速度制限
- [ ] レポート出力（HTML形式のサマリー）

## ライセンス

MIT License
