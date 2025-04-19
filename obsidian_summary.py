import os
import glob
import re
import yaml
import yaml
import smtplib
import logging
import shutil
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
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )
    
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

    def find_tagged_notes(self):
        """指定したタグを持つノートファイルを検索"""
        notes = []
        try:
            vault_path = self._convert_to_unc_path(self.config['vault_path'])
            pattern = os.path.join(vault_path, '**/*.md')
            for filepath in glob.glob(pattern, recursive=True):
                # ファイルの更新日時を取得
                try:
                    last_modified = datetime.fromtimestamp(os.path.getmtime(filepath))
                except FileNotFoundError as e:
                    self.logger.error(f"ファイルが見つかりません: {filepath} - {e}")
                    continue
                # 今日の日付と一致するか確認
                if last_modified.date() == datetime.now().date():
                    with open(filepath, 'r', encoding='utf-8') as file:
                        content = file.read()
                        original_content = content  # オリジナルのコンテンツを保持
                        # フロントマターの抽出
                        frontmatter = {}
                        if content.startswith('---'):
                            try:
                                frontmatter_end = content.find('---', 3)
                                if frontmatter_end != -1:
                                    frontmatter_str = content[3:frontmatter_end]
                                    frontmatter = yaml.safe_load(frontmatter_str)
                                    self.logger.info(f"フロントマター: {frontmatter}")  # ログを追加
                                    content = content[frontmatter_end + 3:]  # フロントマターを除いたコンテンツ
                            except yaml.YAMLError as e:
                                self.logger.error(f"フロントマターの解析に失敗: {filepath} - {e}")
                                continue

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
                            # フロントマーにタグがない場合のみ、コンテンツ内のタグを確認
                            target_tag_with_hash = '#' + self.config['target_tag']
                            if target_tag_with_hash in content:
                                bullet_lines = []
                                for line in content.split('\n'):
                                    if line.strip().startswith('- ') and target_tag_with_hash in line:
                                        bullet_lines.append(line)
                                if bullet_lines:
                                    notes.append((filepath, '\n'.join(bullet_lines)))
                                    self.logger.info(f"コンテンツ内のタグ付き箇条書きを検出: {filepath}")
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
            
            # フロントマターの確認
            frontmatter = {}
            if content.startswith('---'):
                try:
                    frontmatter_end = content.find('---', 3)
                    if frontmatter_end != -1:
                        frontmatter_str = content[3:frontmatter_end]
                        frontmatter = yaml.safe_load(frontmatter_str)
                except yaml.YAMLError:
                    pass

            # タグの確認（フロントマター内）
            tags = frontmatter.get('tags', [])
            if tags is None:
                tags = []
            elif isinstance(tags, str):
                tags = [tags]

            # ファイル名から拡張子を除去してタイトルとして使用
            title = os.path.splitext(filename)[0]

            # フロントマターにタグがある場合は全文を要約し、タイトルを含める
            if self.config['target_tag'] in tags:
                target_content = f"# {title}\n\n{content}"
            else:
                # タグを含む箇条書きのみを抽出
                target_tag_with_hash = '#' + self.config['target_tag']
                lines = content.splitlines()
                target_content = "\n".join([
                    line for line in lines
                    if line.strip().startswith('- ') and target_tag_with_hash in line
                ])
                
            clean_content = self.clean_content(target_content)
            combined_content.append(f"【{title}】\n{clean_content}")
        
        # 全てのノートの内容を結合
        all_content = "\n\n---\n\n".join(combined_content)
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config['openai']['api_key']}"
        }
        
        # 基本のシステムプロンプト
        system_prompt = "あなたは文章を要約する専門家です。各ノートは【タイトル】で区切られています。タイトルが付いているノートは、そのタイトルの文脈を考慮して要約してください。"
        
        # 設定から追加のプロンプトを取得
        additional_prompt = self.config['openai'].get('additional_prompt', '')
        if additional_prompt:
            system_prompt = f"{system_prompt} {additional_prompt}"

        data = {
            "model": self.config['openai']['model'],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": all_content}
            ],
            "max_tokens": self.config['openai']['max_tokens']
        }
        
        try:
            response = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=data
            )
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
        msg['Subject'] = f"Obsidianノート要約 {datetime.now().strftime('%Y-%m-%d')}"
        body = "本日の要約対象ノート:\n\n" + notes_summary
        msg.attach(MIMEText(body, 'plain'))

        # SMTP接続と送信
        try:
            server = smtplib.SMTP(
                self.config['email']['smtp_server'],
                self.config['email']['smtp_port']
            )
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
                self.logger.info("要約対象のノートが見つかりませんでした")
                return
            
            self.logger.info(f"ノート要約開始: {len(tagged_notes)}件のノートを処理")
            all_summaries = self.summarize_with_ai(tagged_notes)
            
            self.send_email(all_summaries)
            self.logger.info("タスク完了")
            
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
