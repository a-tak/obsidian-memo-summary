import os
import glob
import re
import yaml
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import requests


def setup_logging(config):
    """ロギングの設定とログローテーションの実装"""
    log_dir = config.get('logging', {}).get('directory', 'logs')
    os.makedirs(log_dir, exist_ok=True)

    # 日付付きのログファイル名を生成
    current_date = datetime.now().strftime('%Y-%m-%d')
    log_file = os.path.join(log_dir, f'obsidian_summary_{current_date}.log')

    # ロギングの設定
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(levelname)s - %(message)s',
                        handlers=[
                            logging.FileHandler(log_file, encoding='utf-8'),
                            logging.StreamHandler()
                        ])

    # 古いログファイルの削除
    retention_days = config.get('logging', {}).get('retention_days', 7)
    cleanup_old_logs(log_dir, retention_days)


def cleanup_old_logs(log_dir, retention_days):
    """指定日数より古いログファイルを削除"""
    current_date = datetime.now()
    for filename in os.listdir(log_dir):
        if not filename.endswith('.log'):
            continue

        file_path = os.path.join(log_dir, filename)
        file_date = datetime.fromtimestamp(os.path.getctime(file_path))

        if (current_date - file_date).days > retention_days:
            try:
                os.remove(file_path)
                logging.info(f"古いログファイルを削除しました: {filename}")
            except Exception as e:
                logging.error(f"ログファイルの削除に失敗: {filename} - {e}")


class ObsidianSummary:

    def __init__(self, config_path='config.yaml'):
        """設定の初期化"""
        self.logger = logging.getLogger(__name__)
        self.load_config(config_path)
        self.validate_vault_path()

    def _convert_to_unc_path(self, path):
        """Windowsの場合のみUNCパスに変換"""
        if os.name == 'nt' and not path.startswith('\\\\'):
            return '\\\\?\\' + path
        return path

    def validate_vault_path(self):
        """vaultの場所を確認し、存在しない場合は例外を発生させる"""
        vault_path = self.config['vault_path']
        self.logger.info(f"Vault location: {vault_path}")

        # OSに応じてパスを変換
        vault_path = self._convert_to_unc_path(vault_path)

        if not os.path.exists(vault_path):
            error_msg = f"Vault path does not exist: {vault_path}"
            self.logger.error(error_msg)
            raise ValueError(error_msg)

    def load_config(self, config_path):
        """設定ファイルの読み込み"""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                self.config = yaml.safe_load(f)
        except Exception as e:
            self.logger.error(f"設定ファイルの読み込みに失敗: {e}")
            raise

    def _get_search_period(self):
        """検索対象期間の開始時刻と終了時刻を計算"""
        now = datetime.now()
        search_period = self.config.get('search_period', {})

        # 日数の取得（デフォルトは1日）
        days = search_period.get('days', 1)
        if not isinstance(days, int) or days < 1:
            self.logger.warning("無効な日数指定。デフォルトの1日を使用します。")
            days = 1

        # 開始・終了時刻の取得
        try:
            start_time = datetime.strptime(
                search_period.get('start_time', '00:00'), '%H:%M').time()
            end_time = datetime.strptime(
                search_period.get('end_time', '23:59'), '%H:%M').time()
        except ValueError as e:
            self.logger.warning(f"時刻形式が無効です: {e}. デフォルト値を使用します。")
            start_time = datetime.strptime('00:00', '%H:%M').time()
            end_time = datetime.strptime('23:59', '%H:%M').time()

        # 期間の計算
        end_datetime = datetime.combine(now.date(), end_time)
        start_datetime = datetime.combine(
            now.date() - timedelta(days=days - 1), start_time)

        return start_datetime, end_datetime

    def _process_frontmatter(self, content, filepath):
        """フロントマターを処理し、テンプレート構文を処理可能な形に変換"""
        if not content.startswith('---'):
            return {}, content

        frontmatter_end = content.find('---', 3)
        if frontmatter_end == -1:
            return {}, content

        frontmatter_str = content[3:frontmatter_end]
        # テンプレート構文を一時的な値に置き換え
        frontmatter_str = re.sub(r'\{\{[^}]+\}\}', 'TEMPLATE_VALUE',
                                 frontmatter_str)

        try:
            frontmatter = yaml.safe_load(frontmatter_str)
            self.logger.info(f"フロントマター処理成功: {filepath}")
            return frontmatter, content[frontmatter_end + 3:]
        except yaml.YAMLError as e:
            self.logger.warning(
                f"フロントマターの解析に失敗しましたが、処理を継続します: {filepath} - {e}")
            return {}, content

    def find_tagged_notes(self):
        """指定したタグを持つノートファイルを検索"""
        notes = []
        try:
            vault_path = self._convert_to_unc_path(self.config['vault_path'])
            pattern = os.path.join(vault_path, '**/*.md')

            # 検索対象期間の取得
            start_datetime, end_datetime = self._get_search_period()
            self.logger.info(f"検索対象期間: {start_datetime} から {end_datetime} まで")

            for filepath in glob.glob(pattern, recursive=True):
                # ファイルの更新日時を取得
                try:
                    last_modified = datetime.fromtimestamp(
                        os.path.getmtime(filepath))
                except FileNotFoundError as e:
                    self.logger.error(f"ファイルが見つかりません: {filepath} - {e}")
                    continue

                # 指定された期間内かどうか確認
                if start_datetime <= last_modified <= end_datetime:
                    with open(filepath, 'r', encoding='utf-8') as file:
                        content = file.read()
                        original_content = content  # オリジナルのコンテンツを保持
                        # フロントマターの抽出と処理
                        frontmatter, content = self._process_frontmatter(
                            content, filepath)

                        # タグの確認（フロントマター内）
                        tags = frontmatter.get('tags', [])
                        if tags is None:
                            tags = []
                        elif isinstance(tags, str):
                            tags = [tags]  # 文字列の場合、リストに変換

                        # タグの処理
                        if self.config['target_tag'] in tags:
                            # フロントマターにタグがある場合、ノート全体を対象とする
                            notes.append((filepath, original_content))
                            self.logger.info(f"フロントマターにタグ付きノートを検出: {filepath}")
                        else:
                            # 本文中のタグ付き箇条書きブロックを抽出
                            target_tag_str = self.config['target_tag']
                            # タグが単独の単語として存在するか確認するための正規表現
                            search_tag_regex = re.compile(rf'#{target_tag_str}(?:$|\s|[^\w#])')

                            # まず、コンテンツ全体にタグが含まれているか大まかに確認
                            if not search_tag_regex.search(content):
                                self.logger.info(f"コンテンツにタグ '{target_tag_str}' が見つかりません。スキップ: {filepath}")
                            else:
                                lines = content.splitlines()
                                extracted_tagged_blocks = []
                                
                                i = 0
                                while i < len(lines):
                                    line = lines[i]
                                    match_list_item_start = re.match(r'^(\s*)-\s+', line) # リストアイテムの開始を検出

                                    if match_list_item_start:
                                        current_block_lines = [line]
                                        base_indent_len = len(match_list_item_start.group(1)) # リストアイテム開始時のインデント
                                        
                                        # 同じリストアイテムに属する後続の行を収集
                                        j = i + 1
                                        while j < len(lines):
                                            next_line = lines[j]
                                            next_line_leading_space_len = len(re.match(r'^(\s*)', next_line).group(1))
                                            is_next_line_new_list_item = bool(re.match(r'^\s*-\s+', next_line))

                                            # 現在のブロックを継続する条件:
                                            # 1. 次の行が空行である。
                                            # 2. 次の行が現在のリストアイテムよりも深くインデントされている。
                                            # 3. 次の行が新しいリストアイテムではなく、かつ現在のリストアイテム以上のインデントを持つ (アイテム内の複数行テキストに対応)。
                                            if not next_line.strip(): # 条件1: 空行
                                                current_block_lines.append(next_line)
                                            elif next_line_leading_space_len > base_indent_len: # 条件2: より深くインデント
                                                current_block_lines.append(next_line)
                                            elif not is_next_line_new_list_item and next_line_leading_space_len >= base_indent_len: # 条件3
                                                current_block_lines.append(next_line)
                                            else:
                                                # 上記条件に合致しない場合、現在のブロックは終了
                                                break 
                                            j += 1
                                        
                                        # 現在のブロック (lines[i...j-1]) が完成
                                        block_content = "\n".join(current_block_lines)
                                        if search_tag_regex.search(block_content):
                                            extracted_tagged_blocks.append(block_content)
                                            self.logger.info(f"タグ付き箇条書きブロックを検出:\n{block_content}")
                                        
                                        i = j # メインイテレータを次のブロックの開始位置へ移動
                                    else:
                                        # 行がリストアイテムを開始しない場合は、単に進む
                                        i += 1
                                
                                if extracted_tagged_blocks:
                                    notes.append((filepath, "\n\n".join(extracted_tagged_blocks))) # 同じファイル内の複数ブロックは改行2つで結合
                                    self.logger.info(f"コンテンツ内のタグ付きブロックを検出: {filepath}")
            return notes
        except Exception as e:
            self.logger.error(f"ノート検索中にエラー: {e}")
            raise

    def clean_content(self, content):
        """マークダウンコンテンツのクリーニング"""
        # フロントマターの削除
        content = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL)
        # タグの削除
        content = re.sub(r'#\w+', '', content)
        return content.strip()

    def summarize_with_ai(self, notes):
        """OpenAI APIを使用して複数のノートをまとめて要約"""
        combined_content = []

        for filepath, content in notes:
            filename = os.path.basename(filepath)

            # フロントマターの処理
            frontmatter, _ = self._process_frontmatter(content, filepath)

            # タグの確認（フロントマター内）
            tags = frontmatter.get('tags', [])
            if tags is None:
                tags = []
            elif isinstance(tags, str):
                tags = [tags]

            # ファイル名から拡張子を除去してタイトルとして使用
            title = os.path.splitext(filename)[0]

            # フロントマターと本文から対象箇条書きまたは全体内容を取得
            # コンテンツ本文（フロントマター除去後）を使用
            body_content = content

            if self.config['target_tag'] in tags:
                # フロントマターにタグがある場合は全文を要約
                target_raw = body_content
            else:
                # 本文中のタグ付き箇条書きを抽出
                target_tag_with_hash = '#' + self.config['target_tag']
                target_raw = body_content

            # タグ行を削除して余分な空白をトリム
            target_content = re.sub(r'#\w+', '', target_raw).strip()

            # タグ付きコンテンツがない場合はスキップ
            if not target_content:
                continue
            # タイトルを追加
            combined_content.append(f"【{title}】\n{target_content}")

        # コンテンツが空の場合は要約をスキップ
        if not combined_content:
            message = "要約対象のノートが見つかりませんでした。AI要約をスキップします。"
            self.logger.info(message)
            return message

        # 全てのノートの内容を結合
        all_content = "\n\n---\n\n".join(combined_content)

        # AIへ送信するノート内容をログに記録
        self.logger.info("=== AIへ送信するノート内容 ===")
        self.logger.info(all_content)

        # 基本のシステムプロンプト
        system_prompt = "あなたは文章を要約する専門家です。各ノートは【タイトル】で区切られています。タイトルが付いているノートは、そのタイトルの文脈を考慮して要約してください。"

        # 設定から追加のプロンプトを取得
        additional_prompt = self.config['openai'].get('additional_prompt', '')
        if additional_prompt:
            system_prompt = f"{system_prompt} {additional_prompt}"

        # システムプロンプトをログに記録
        self.logger.info("=== システムプロンプト ===")
        self.logger.info(system_prompt)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config['openai']['api_key']}"
        }

        data = {
            "model":
            self.config['openai']['model'],
            "messages": [{
                "role": "developer",
                "content": [{
                    "type": "text",
                    "text": system_prompt
                }]
            }, {
                "role": "user",
                "content": [{
                    "type": "text",
                    "text": all_content
                }]
            }],
            "reasoning_effort":
            "medium",
            "max_completion_tokens":
            self.config['openai']['max_tokens'],
            "store":
            True
        }

        try:
            response = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=data)
            response.raise_for_status()
            summary = response.json()['choices'][0]['message']['content']
            return summary
        except Exception as e:
            self.logger.error(f"AI要約エラー: {e}")
            return f"要約エラー: {str(e)[:100]}..."

    def _validate_email(self, email):
        """メールアドレスの簡易バリデーション"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(pattern, email))

    def send_email(self, notes_summary):
        """複数の宛先にメール送信"""
        # 要約内容を常にログに記録
        self.logger.info("=== 要約内容 ===")
        self.logger.info(notes_summary)
        # メール送信が無効な場合はスキップ
        if not self.config['email'].get('enabled', True):
            self.logger.info("メール送信がスキップされました（設定で無効化されています）")
            return

        # 送信先アドレスのリストを取得
        to_addresses = self.config['email'].get('to', [])
        if isinstance(to_addresses, str):
            # カンマ区切りの文字列の場合、リストに変換
            to_addresses = [addr.strip() for addr in to_addresses.split(',')]
        elif not isinstance(to_addresses, list):
            to_addresses = [str(to_addresses)]

        # 無効なメールアドレスをフィルタリング
        valid_addresses = []
        for addr in to_addresses:
            if self._validate_email(addr):
                valid_addresses.append(addr)
            else:
                self.logger.warning(f"無効なメールアドレス: {addr}")

        if not valid_addresses:
            error_msg = "有効なメールアドレスが指定されていません"
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        # メール送信の準備
        msg = MIMEMultipart()
        from_addr = self.config['email']['from']
        msg['From'] = from_addr
        msg['To'] = from_addr  # 送信者自身をToに設定
        msg['Bcc'] = ', '.join(valid_addresses)  # 全送信先をBccに設定
        # 検索対象期間の取得
        start_datetime, end_datetime = self._get_search_period()

        # 期間に応じて件名と本文を変更
        if start_datetime.date() == end_datetime.date():
            date_str = start_datetime.strftime('%Y-%m-%d')
            subject = f"Obsidianノート要約 {date_str}"
            body_prefix = "本日の要約対象ノート"
        else:
            date_str = f"{start_datetime.strftime('%Y-%m-%d')}から{end_datetime.strftime('%Y-%m-%d')}"
            subject = f"Obsidianノート要約 {date_str}"
            body_prefix = "期間内の要約対象ノート"

        msg['Subject'] = subject
        body = f"{body_prefix}:\n\n" + notes_summary
        msg.attach(MIMEText(body, 'plain'))

        # SMTP接続と送信
        try:
            server = smtplib.SMTP(self.config['email']['smtp_server'],
                                  self.config['email']['smtp_port'])
            server.starttls()
            server.login(from_addr, self.config['email']['password'])

            try:
                server.send_message(msg)
                self.logger.info(f"メール送信成功 - 送信先数: {len(valid_addresses)}")
                for addr in valid_addresses:
                    self.logger.info(f"送信先: {addr}")
            except Exception as e:
                self.logger.error(f"メール送信エラー: {e}")
                raise

            server.quit()

        except Exception as e:
            self.logger.error(f"SMTP接続/認証エラー: {e}")
            raise

    def run(self):
        """メインタスク実行"""
        try:
            self.logger.info("Obsidian要約タスク開始")
            tagged_notes = self.find_tagged_notes()

            if not tagged_notes:
                message = "要約対象のノートが見つかりませんでした。AI要約をスキップします。"
                self.logger.info(message)
                return  # 要約対象が見つからない場合はここで終了

            # skip_summaryオプションの確認
            if self.config['openai'].get('skip_summary', False):
                message = f"AI要約がスキップされました（設定でスキップが有効）\n\n対象ノート数: {len(tagged_notes)}件"
                self.logger.info("AI要約スキップ（設定で無効化されています）")
                all_summaries = message
            else:
                self.logger.info(f"ノート要約開始: {len(tagged_notes)}件のノートを処理")
                all_summaries = self.summarize_with_ai(tagged_notes)

            # メール送信の実行（無効の場合は内部でスキップ）
            self.send_email(all_summaries)

            # メール送信の状態に応じたログ出力
            if self.config['email'].get('enabled', True):
                self.logger.info("タスク完了（メール送信実行）")
            else:
                self.logger.info("タスク完了（メール送信スキップ）")

        except Exception as e:
            self.logger.error(f"タスク実行エラー: {e}")
            raise


def main():
    """メイン実行関数"""
    try:
        # 設定を読み込んでロギングを初期化
        config_path = 'config.yaml'
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        setup_logging(config)  # ロギング設定を先に初期化

        # ObsidianSummaryを初期化して実行
        summarizer = ObsidianSummary(config_path)
        summarizer.run()
    except Exception as e:
        logging.error(f"プログラム実行エラー: {e}")
        raise


if __name__ == "__main__":
    main()
