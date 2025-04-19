# Obsidian-memo-summary

## システム概要の要約

Obsidian(マークダウン)に記録したノートを自動要約しメール送信するスクリプトです。

システムは、以下の仕組みで機能します：

1. 直近更新されたノートで特定タグ（#要約対象など）の付いたノートを自動検出
2. OpenAI APIを使用してノート内容を要約
3. 要約結果を設定したメールアドレスに送信

## セットアップ手順

### 1. 仮想環境のセットアップ
```bash
# 仮想環境の作成
python -m venv venv

# 仮想環境の有効化
## Windows
venv\Scripts\activate
## macOS/Linux
# source venv/bin/activate

# パッケージのインストール
pip install -r requirements.txt
```

### 2. 設定ファイルの準備
1. `config.sample.yaml`を`config.yaml`にコピー
2. `config.yaml`を編集して以下の項目を設定：
   ```yaml
   # Obsidianのvaultパス
   vault_path: "C:\\SynologyDrive\\Obsidian\\VaultName"  # Windowsの場合

   # 要約対象のタグ
   target_tag: "要約対象"

   # メール設定
   email:
     from: "your_email@gmail.com"
     # 配列形式で複数の送信先を指定する場合
     to:
       - "recipient1@example.com"
       - "recipient2@example.com"
     # または、カンマ区切りの文字列で指定することも可能
     # to: "recipient1@example.com, recipient2@example.com"
     smtp_server: "smtp.gmail.com"
     smtp_port: 587
     password: "your_app_password"  # Gmailの場合はアプリパスワードを使用

   # OpenAI API設定
   openai:
     api_key: "your_openai_api_key"
     model: "gpt-4o-mini"
     max_tokens: 800  # 日本語400文字の要約に対応
     additional_prompt: |
       以下の指示に従って要約を作成してください：
       1. 各ノートの重要なポイントを3点にまとめる
       2. 技術的な用語は可能な限り平易な言葉で説明する
       3. アクションアイテムがある場合は、末尾に箇条書きでまとめる

   # 検索対象期間の設定
   search_period:
     days: 1  # 検索対象とする日数（1なら当日のみ、2なら前日も含む）
     start_time: "00:00"  # 検索開始時刻（オプション）
     end_time: "23:59"   # 検索終了時刻（オプション）

   # ログ設定
   logging:
     retention_days: 7
     directory: "logs"
   ```

### 3. 動作確認
```bash
python obsidian_summary.py
```

### 4. Docker環境での実行

1. ボリュームマウントの設定
docker-compose.ymlの`volumes`セクションでObsidianのvaultパスを指定します：
```yaml
volumes:
  - /path/to/your/vault:/app/vault  # Obsidianのvaultパスをマウント
  - ./config.yaml:/app/config.yaml   # 設定ファイル
  - ./logs:/app/logs                 # ログディレクトリ
```

2. config.yamlの設定
Docker環境では、vault_pathをコンテナ内のマウントポイントに合わせて設定します：
```yaml
vault_path: "/app/vault"  # Dockerコンテナ内のパス
```

3. Docker Composeでの実行
```bash
docker-compose up --build
```

注意点：
- Windowsでは、vault_pathのマウント設定で適切なドライブパスを指定（例：`c:/Users/...:/app/vault`）
- config.yamlのvault_pathは常に`/app/vault`を使用（コンテナ内のパス）
- タイムゾーンはDocker Composeファイルで`TZ=Asia/Tokyo`として設定済み

## セキュリティについて

- `config.yaml`はGit管理対象外（.gitignoreに設定済み）
- APIキーやメール認証情報は`config.yaml`で管理
- ログファイル（*.log）もGit管理対象外

## ログについて

- ログファイル: `logs/obsidian_summary_YYYY-MM-DD.log`
- ログレベル: INFO
- 記録内容:
  - タスクの開始・終了
  - タグ付きノートの検出
  - 要約処理の状況
  - メール送信の結果
  - エラー情報（発生時）

## トラブルシューティング

1. メール送信エラー
   - Gmailの場合、通常のパスワードではなく「アプリパスワード」が必要
   - SMTPサーバーとポート番号の確認
   - メールアカウントの認証設定の確認

2. ノート検出エラー
   - vault_pathの設定確認
   - Windowsの場合、パスの区切りは`\\`を使用
   - ファイルのエンコーディング確認（UTF-8推奨）

3. OpenAI APIエラー
   - APIキーの有効性確認
   - APIの利用制限・クォータの確認
   - ネットワーク接続の確認
