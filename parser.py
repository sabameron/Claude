import os
import re
import sys
import shutil
import subprocess
import datetime
import argparse
from pathlib import Path
import binascii  # デバッグ出力用に追加

# スクリプトのバージョン
VERSION = "2.1.3"

# 処理から除外するファイル拡張子
EXCLUDED_EXTENSIONS = ['.json', '.env', '.lock', '.md', '.gitignore', '.gitkeep', '.git', '.DS_Store']

def is_excluded_file(file_path):
    """ファイルが処理から除外されるかどうかを判定する"""
    _, ext = os.path.splitext(file_path.lower())
    return ext in EXCLUDED_EXTENSIONS

def print_info(message, always_show=True):
    """情報メッセージを表示する。always_showがTrueまたはデバッグモードが有効な場合のみ表示"""
    if always_show or debug_logger.enabled:
        print(message)

# デバッグ用のログ記録
class DebugLogger:
    def __init__(self, enabled=False):
        self.enabled = enabled
        self.log_dir = None
        self.log_file = None
        
        if enabled:
            # ログディレクトリを作成
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self.log_dir = os.path.join("log", "parser", timestamp)
            os.makedirs(self.log_dir, exist_ok=True)
            
            # ログファイルを作成
            self.log_file = open(os.path.join(self.log_dir, "parser_debug.log"), "w", encoding="utf-8")
            self.log("デバッグログを開始しました")
    
    def log(self, message, also_print=True):
        if not self.enabled:
            return
            
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        log_message = f"[{timestamp}] {message}"
        
        if also_print:
            print_info(f"DEBUG: {message}")
            
        if self.log_file:
            self.log_file.write(log_message + "\n")
            self.log_file.flush()
    
    def log_file_content(self, filename, content):
        if not self.enabled:
            return
            
        # ファイル内容をログフォルダに保存
        if self.log_dir:
            base_name = os.path.basename(filename)
            log_path = os.path.join(self.log_dir, f"content_{base_name}")
            
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(content)
            
            self.log(f"ファイル内容を {log_path} に保存しました", also_print=False)
    
    def close(self):
        if self.log_file:
            self.log("デバッグログを終了します")
            self.log_file.close()
            self.log_file = None

# グローバル変数としてロガーを初期化
debug_logger = DebugLogger(enabled=False)

class ProcedureValidator:
    """手順書のフォーマット検証クラス"""
    
    def __init__(self, content):
        self.content = content
        self.errors = []
    
    def validate(self):
        """手順書のフォーマットを検証する"""
        debug_logger.log("手順書の検証を開始")
        
        # バージョン確認
        if not self._check_version():
            self.errors.append("準拠手順書形式のバージョン情報が見つからないか、フォーマットが不正です")
        
        # 必須セクションの確認
        required_sections = ["## 概要", "## アプリ実行コマンド", "## 必要ファイル一覧", "## ファイルの中身", "## 備考"]
        for section in required_sections:
            if section not in self.content:
                self.errors.append(f"必須セクション「{section}」が見つかりません")
        
        # ファイル一覧のフォーマットチェック
        file_list_match = re.search(r'## 必要ファイル一覧\n(.*?)(?=##)', self.content, re.DOTALL)
        if file_list_match:
            file_list = file_list_match.group(1).strip().split('\n')
            for line in file_list:
                if line.strip() and not re.match(r'^(新規|修正|削除),\d{5},\S+', line.strip()):
                    self.errors.append(f"ファイル一覧のフォーマットが不正です: {line}")
        
        # ファイル内容セクションのフォーマットチェック
        file_sections = re.finditer(r'### (新規|修正|削除),(\d{5}),([^\n]+)\n(コミット内容：([^\n]+)\n)?', self.content, re.DOTALL)
        file_ids = set()
        for match in file_sections:
            action = match.group(1)
            file_id = match.group(2)
            file_path = match.group(3)
            
            debug_logger.log(f"ファイルセクション検出: {action},{file_id},{file_path}")
            
            if file_id in file_ids:
                self.errors.append(f"ファイルID {file_id} が重複しています")
            file_ids.add(file_id)
            
            # 新規・修正の場合はコード管理番号をチェック（除外ファイル以外）
            if action in ["新規", "修正"] and not is_excluded_file(file_path):
                # ファイルセクションの内容を取得
                file_end_pattern = r'### (新規|修正|削除),\d{5},[^\n]+(?:\nコミット内容：[^\n]+)?\n([\s\S]*?)(?=### |\Z)'
                file_content_match = re.search(file_end_pattern, self.content[match.start():], re.DOTALL)
                
                if file_content_match:
                    file_content = file_content_match.group(2)
                    
                    if action == "新規":
                        # 新規ファイルの場合
                        # v2.1.0形式のコード管理番号パターン
                        code_numbers = re.findall(r'(?://|#|<!--|/\*)?\s*#(\d{5}_[a-z]{5})', file_content)
                        
                        if not code_numbers:
                            self.errors.append(f"ファイルID {file_id} のコード管理番号が見つかりません")
                        
                        # 終点マーカーの確認
                        if '99999_zzzzz' not in ''.join(code_numbers):
                            self.errors.append(f"ファイルID {file_id} に終点マーカー #99999_zzzzz が見つかりません")
                        
                        # 連番かつ一意のチェック
                        unique_numbers = set(code_numbers)
                        if len(code_numbers) != len(unique_numbers):
                            self.errors.append(f"ファイルID {file_id} のコード管理番号に重複があります")
                    
                    elif action == "修正":
                        # 修正区間のチェック
                        # v2.1.0形式のコード管理番号パターン
                        modification_sections = re.finditer(r'####\s+#(\d{5}_[a-z]{5})-#(\d{5}_[a-z]{5})\s*\n```[a-z]*\n([\s\S]*?)```', file_content, re.DOTALL)
                        for mod_match in modification_sections:
                            start_code = mod_match.group(1)
                            end_code = mod_match.group(2)
                            mod_content = mod_match.group(3)
                            
                            debug_logger.log(f"修正区間検出: #{start_code}-#{end_code}")
                            
                            # 修正区間内にコード管理番号があるかチェック
                            section_code_numbers = re.findall(r'(?://|#|<!--|/\*)?\s*#(\d{5}_[a-z]{5})', mod_content)
                            
                            if not section_code_numbers:
                                self.errors.append(f"ファイルID {file_id} の修正区間 #{start_code}-#{end_code} にコード管理番号が見つかりません")
                            
                            # 修正区間の開始と終了コードが含まれているかチェック
                            if start_code not in section_code_numbers:
                                self.errors.append(f"ファイルID {file_id} の修正区間 #{start_code}-#{end_code} に開始コード #{start_code} が含まれていません")
                            if end_code not in section_code_numbers:
                                self.errors.append(f"ファイルID {file_id} の修正区間 #{start_code}-#{end_code} に終了コード #{end_code} が含まれていません")
        
        debug_logger.log(f"手順書検証完了。エラー数: {len(self.errors)}")
        return len(self.errors) == 0
    
    def _check_version(self):
        """バージョン情報をチェック"""
        version_match = re.search(r'準拠手順書形式：v(\d+\.\d+\.\d+)', self.content)
        return bool(version_match)
    
    def get_version(self):
        """バージョン情報を取得"""
        version_match = re.search(r'準拠手順書形式：v(\d+\.\d+\.\d+)', self.content)
        if version_match:
            return "v" + version_match.group(1)
        return None
    
    def get_errors(self):
        """検証エラーを取得"""
        return self.errors


class ProcedureParser:
    def __init__(self, procedure_file_path):
        self.procedure_file_path = procedure_file_path
        self.procedure_content = None
        self.app_name = None
        self.version = None
        self.overview = None
        self.run_commands = []
        self.file_list = []  # [{'type': 'new', 'id': '00001', 'path': 'file.txt'}]
        self.file_contents = {}
        self.file_modifications = {}  # {'file_id': [{'start': '00001', 'end': '00002', 'content': '...'}]}
        self.commit_messages = {}
        self.notes = None
        
    def parse(self):
        """手順書の内容を解析する"""
        try:
            with open(self.procedure_file_path, 'r', encoding='utf-8') as f:
                self.procedure_content = f.read()
                debug_logger.log(f"手順書 {self.procedure_file_path} を読み込みました ({len(self.procedure_content)} バイト)")
                # 手順書全体をログに保存
                debug_logger.log_file_content("procedure_full_content.md", self.procedure_content)
        except Exception as e:
            debug_logger.log(f"エラー: 手順書の読み込みに失敗しました: {e}")
            print_info(f"エラー: 手順書の読み込みに失敗しました: {e}")
            sys.exit(1)
        
        # バリデーション
        validator = ProcedureValidator(self.procedure_content)
        if not validator.validate():
            debug_logger.log("手順書のフォーマットが不正です:")
            print_info("エラー: 手順書のフォーマットが不正です:")
            for error in validator.get_errors():
                debug_logger.log(f"- {error}")
                print_info(f"- {error}")
            sys.exit(1)
        
        # バージョンの取得と照合
        self.version = validator.get_version()
        version_without_v = self.version[1:] if self.version and self.version.startswith('v') else ""

        # バージョン比較のロジックを修正（メジャー.マイナーまでの一致を確認）
        current_version_parts = VERSION.split('.')
        procedure_version_parts = version_without_v.split('.')

        # メジャーとマイナーバージョンが一致するかチェック
        version_match = False
        if len(current_version_parts) >= 2 and len(procedure_version_parts) >= 2:
            if current_version_parts[0] == procedure_version_parts[0] and current_version_parts[1] == procedure_version_parts[1]:
                version_match = True

        if not version_match:
            debug_logger.log(f"警告: スクリプトのバージョン({VERSION})と手順書の準拠形式バージョン({version_without_v})が一致しません")
            print_info(f"★警告: スクリプトのバージョン({VERSION})と手順書の準拠形式バージョン({version_without_v})が一致しません")
            response = input("続行しますか？ (y/n): ")
            if response.lower() != 'y':
                sys.exit(0)
        
        # タイトルの取得
        title_match = re.search(r'# ([^\n]+)', self.procedure_content)
        if title_match:
            self.app_name = title_match.group(1)
            debug_logger.log(f"アプリ名: {self.app_name}")
        
        # 概要の取得
        overview_match = re.search(r'## 概要\n(.*?)(?=##)', self.procedure_content, re.DOTALL)
        if overview_match:
            self.overview = overview_match.group(1).strip()
            debug_logger.log(f"概要を取得しました ({len(self.overview)} 文字)")
        
        # アプリ実行コマンドの取得
        commands_match = re.search(r'## アプリ実行コマンド\n```bash\n(.*?)```', self.procedure_content, re.DOTALL)
        if commands_match:
            self.run_commands = commands_match.group(1).strip().split('\n')
            debug_logger.log(f"実行コマンドを取得しました ({len(self.run_commands)} 行)")
        
        # 必要ファイル一覧の取得
        file_list_match = re.search(r'## 必要ファイル一覧\n(.*?)(?=##)', self.procedure_content, re.DOTALL)
        if file_list_match:
            file_list_content = file_list_match.group(1).strip()
            file_lines = [line.strip() for line in file_list_content.split('\n') if line.strip()]
            debug_logger.log(f"ファイル一覧を取得しました ({len(file_lines)} ファイル)")
            
            for line in file_lines:
                debug_logger.log(f"ファイル行: {line}")
                parts = line.split(',', 2)
                if len(parts) == 3:
                    action_type, file_id, file_path = parts
                    
                    # アクションタイプを英語に変換
                    action_map = {"新規": "new", "修正": "modify", "削除": "delete"}
                    action = action_map.get(action_type, "unknown")
                    
                    self.file_list.append({
                        "type": action,
                        "id": file_id,
                        "path": file_path.strip()
                    })
                    debug_logger.log(f"ファイル一覧に追加: {action_type}({action}), {file_id}, {file_path}")
        
        # ファイルの中身とコミットメッセージの取得
        file_section_pattern = r'### (新規|修正|削除),(\d{5}),([^\n]+)(?:\nコミット内容：([^\n]+))?'
        file_sections = re.finditer(file_section_pattern, self.procedure_content)
        
        for match in file_sections:
            action = match.group(1)
            file_id = match.group(2)
            file_path = match.group(3)
            commit_msg = match.group(4) if match.group(4) else f"{action} {file_path}"
            
            debug_logger.log(f"ファイルセクション処理: {action}, {file_id}, {file_path}")
            debug_logger.log(f"コミットメッセージ: {commit_msg}")
            
            # 現在のセクションの開始位置を取得
            section_start = match.start()
            
            # 次のセクションの開始位置を探す
            next_section_match = re.search(r'### (新規|修正|削除),\d{5},', self.procedure_content[section_start + 1:])
            if next_section_match:
                section_end = section_start + 1 + next_section_match.start()
            else:
                # 次のセクションがなければ、備考セクションの開始位置を探す
                notes_match = re.search(r'## 備考', self.procedure_content[section_start:])
                if notes_match:
                    section_end = section_start + notes_match.start()
                else:
                    # 備考セクションもなければ、ファイルの終わりまで
                    section_end = len(self.procedure_content)
            
            # セクションの内容を取得
            section_content = self.procedure_content[section_start:section_end].strip()
            debug_logger.log(f"セクション内容の長さ: {len(section_content)}")
            
            key = f"{file_id},{file_path}"
            
            if action == "新規":
                # 新規ファイルの場合、コードブロックの内容を抽出
                code_block_match = re.search(r'```[a-z]*\n(.*?)```', section_content, re.DOTALL)
                if code_block_match:
                    self.file_contents[key] = code_block_match.group(1)
                    debug_logger.log(f"新規ファイル {file_path} の内容を抽出しました ({len(self.file_contents[key])} バイト)")
            
            elif action == "修正":
                # 修正ファイルの場合、修正区間を抽出
                self.file_modifications[key] = []
                
                # デバッグ出力
                debug_logger.log(f"修正ファイル {file_path} の処理を開始")
                print_info(f"修正ファイル {file_path} の処理を開始")
                
                # 修正区間を検索
                modification_pattern = r'####\s+#(\d+(?:_[a-zA-Z0-9]+)?)-#(\d+(?:_[a-zA-Z0-9]+)?)\s*\n```[a-z]*\n([\s\S]*?)```'
                modification_sections = list(re.finditer(modification_pattern, section_content, re.DOTALL))

                debug_logger.log(f"修正区間検索パターン: {modification_pattern}")
                debug_logger.log(f"修正区間数: {len(modification_sections)}")

                # セクションの内容をデバッグログに出力
                debug_logger.log_file_content(f"{file_id}_{file_path}_section_content.txt", section_content)

                if len(modification_sections) == 0:
                    debug_logger.log("修正区間が見つかりません。代替パターンを試します。")
                    alt_pattern = r'####.*?#(\d+(?:_[a-zA-Z0-9]+)?)-#(\d+(?:_[a-zA-Z0-9]+)?).*?\n```.*?\n([\s\S]*?)```'
                    debug_logger.log(f"代替パターン: {alt_pattern}")
                    modification_sections = list(re.finditer(alt_pattern, section_content, re.DOTALL))
                    debug_logger.log(f"代替パターンによる修正区間数: {len(modification_sections)}")
                
                for mod_match in modification_sections:
                    start_code = mod_match.group(1)
                    end_code = mod_match.group(2)
                    mod_content = mod_match.group(3)
                    
                    # 修正内容をデバッグ出力
                    debug_logger.log(f"修正区間 #{start_code}-#{end_code} を抽出しました")
                    debug_logger.log_file_content(f"{file_id}_{file_path}_mod_{start_code}_{end_code}.txt", mod_content)
                    
                    preview = mod_content[:50] + ("..." if len(mod_content) > 50 else "")
                    debug_logger.log(f"修正内容の先頭部分: {preview}")
                    print_info(f"修正区間 #{start_code}-#{end_code} を抽出しました")
                    print_info(f"修正内容の先頭部分: {preview}")
                    
                    self.file_modifications[key].append({
                        "start": start_code,
                        "end": end_code,
                        "content": mod_content
                    })
                
                if len(self.file_modifications[key]) == 0:
                    debug_logger.log(f"警告: ファイル {file_path} に修正区間が見つかりませんでした")
                    print_info(f"★警告: ファイル {file_path} に修正区間が見つかりませんでした")
            
            # コミットメッセージを保存
            self.commit_messages[key] = commit_msg
        
        # 備考の取得
        notes_match = re.search(r'## 備考\n(.*?)(?=$)', self.procedure_content, re.DOTALL)
        if notes_match:
            self.notes = notes_match.group(1).strip()
            debug_logger.log(f"備考を取得しました ({len(self.notes)} 文字)")
        
        return self
    
    def create_project_structure(self, base_dir):
        """解析した手順書に基づいてプロジェクト構造を作成する"""
        debug_logger.log(f"プロジェクト構造の作成を開始: {base_dir}")
        
        # 除外ファイル拡張子のリストを表示
        print_info(f"注意: 以下の拡張子のファイルは自動処理から除外されます: {', '.join(EXCLUDED_EXTENSIONS)}")
        print_info("これらのファイルは手動で作成または修正してください。")
        
        if not os.path.exists(base_dir):
            os.makedirs(base_dir)
            debug_logger.log(f"ディレクトリ作成: {base_dir}")
            print_info(f"ディレクトリ作成: {base_dir}")
        
        # ファイル操作
        for file_entry in self.file_list:
            try:
                action = file_entry["type"]
                file_id = file_entry["id"]
                file_path = file_entry["path"]
                full_path = os.path.join(base_dir, file_path)
                dir_path = os.path.dirname(full_path)
                
                debug_logger.log(f"ファイル処理: {action}, {file_id}, {file_path}")
                
                # 除外ファイルチェック
                if is_excluded_file(file_path):
                    print_info(f"★注意: {file_path} は除外リストに含まれるため、自動処理されません。手動で{action}してください。")
                    debug_logger.log(f"除外ファイル: {file_path}は処理がスキップされます")
                    continue
                
                # ディレクトリがなければ作成
                if dir_path and not os.path.exists(dir_path):
                    os.makedirs(dir_path)
                    debug_logger.log(f"ディレクトリ作成: {dir_path}")
                    print_info(f"ディレクトリ作成: {dir_path}")
                
                key = f"{file_id},{file_path}"
                
                if action == "delete":
                    # ファイル削除
                    if os.path.exists(full_path):
                        os.remove(full_path)
                        debug_logger.log(f"ファイル削除: {full_path}")
                        print_info(f"ファイル削除: {full_path}")
                    else:
                        debug_logger.log(f"警告: 削除対象ファイル {full_path} が見つかりません")
                        print_info(f"★警告: 削除対象ファイル {full_path} が見つかりません")
                
                elif action == "new":
                    # 新規ファイル作成
                    if key in self.file_contents:
                        with open(full_path, 'w', encoding='utf-8') as f:
                            f.write(self.file_contents[key])
                        debug_logger.log(f"ファイル作成: {full_path}")
                        print_info(f"ファイル作成: {full_path}")
                    else:
                        debug_logger.log(f"警告: ファイル {file_path} の内容が見つかりません")
                        print_info(f"★警告: ファイル {file_path} の内容が見つかりません")
                
                elif action == "modify":
                    # ファイル修正
                    if key in self.file_modifications and os.path.exists(full_path):
                        self._modify_file(full_path, self.file_modifications[key])
                        debug_logger.log(f"ファイル更新: {full_path}")
                        print_info(f"ファイル更新: {full_path}")
                    else:
                        debug_logger.log(f"警告: ファイル {file_path} の修正情報が見つからないか、ファイルが存在しません")
                        print_info(f"★警告: ファイル {file_path} の修正情報が見つからないか、ファイルが存在しません")
            
            except Exception as e:
                debug_logger.log(f"エラー: ファイル {file_path} の処理に失敗しました: {e}")
                print_info(f"エラー: ファイル {file_path} の処理に失敗しました: {e}")
        
        # 実行コマンドをbat/shファイルとして保存
        if self.run_commands:
            if os.name == 'nt':  # Windows
                script_path = os.path.join(base_dir, "run.bat")
                with open(script_path, 'w', encoding='utf-8') as f:
                    f.write("@echo off\n")
                    for cmd in self.run_commands:
                        f.write(f"{cmd}\n")
                debug_logger.log(f"実行スクリプト作成: {script_path}")
                print_info(f"実行スクリプト作成: {script_path}")
            else:  # Unix/Linux/Mac
                script_path = os.path.join(base_dir, "run.sh")
                with open(script_path, 'w', encoding='utf-8') as f:
                    f.write("#!/bin/bash\n")
                    for cmd in self.run_commands:
                        f.write(f"{cmd}\n")
                os.chmod(script_path, 0o755)  # 実行権限を付与
                debug_logger.log(f"実行スクリプト作成: {script_path}")
                print_info(f"実行スクリプト作成: {script_path}")
    
    def _modify_file(self, file_path, modifications):
        """ファイルの特定範囲を修正する"""
        try:
            debug_logger.log(f"ファイル修正: {file_path}")
            
            # 除外ファイルチェック
            if is_excluded_file(file_path):
                debug_logger.log(f"除外ファイル: {file_path}は処理がスキップされます")
                print_info(f"★注意: {file_path} は除外リストに含まれるため、自動処理されません。手動で修正してください。")
                return
            
            # ファイル内容の読み込み
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                original_content = content  # バックアップ用
                debug_logger.log(f"ファイル内容を読み込みました ({len(content)} バイト)")
                debug_logger.log_file_content(f"{file_path}_original.txt", content)
            
            print_info(f"ファイル {file_path} の内容を読み込みました（{len(content)}バイト）")
            
            # バックアップの作成
            backup_path = file_path + ".bak"
            shutil.copy2(file_path, backup_path)
            debug_logger.log(f"バックアップを作成しました: {backup_path}")
            
            # 変更フラグ
            changed = False
            
            # 各修正区間を処理
            for mod in modifications:
                start_code = mod["start"]
                end_code = mod["end"]
                new_content = mod["content"]
                
                debug_logger.log(f"修正処理: コード管理番号 #{start_code}-#{end_code}")
                print_info(f"修正処理: コード管理番号 #{start_code}-#{end_code}")
                
                # ファイル拡張子からファイル種別を判断
                _, ext = os.path.splitext(file_path.lower())
                
                # コード管理番号を検索するパターンを作成
                # HTMLコメントやその他のコメント形式を考慮
                patterns = []
                
                # HTML/XMLファイル用パターン
                if ext in ['.html', '.htm', '.xml', '.svg']:
                    patterns.extend([
                        f"<!-- #{start_code} -->",  # HTMLコメント形式
                        f"#{start_code}",           # 単純な形式（フォールバック）
                    ])
                # CSSファイル用パターン
                elif ext in ['.css']:
                    patterns.extend([
                        f"/* #{start_code} */",     # CSSコメント形式
                        f"#{start_code}",           # 単純な形式（フォールバック）
                    ])
                # PHPやJavaScript等のC系言語用パターン
                elif ext in ['.php', '.js', '.ts', '.java', '.cs', '.cpp', '.c', '.h']:
                    patterns.extend([
                        f"// #{start_code}",        # 単一行コメント形式
                        f"/* #{start_code} */",     # 複数行コメント形式
                        f"#{start_code}",           # 単純な形式（フォールバック）
                    ])
                # Python、Ruby、シェルスクリプト用パターン
                elif ext in ['.py', '.rb', '.sh', '.yml', '.yaml']:
                    patterns.extend([
                        f"# #{start_code}",         # シャープコメント形式
                        f"#{start_code}",           # 単純な形式（フォールバック）
                    ])
                # デフォルトパターン（すべての形式を試す）
                else:
                    patterns.extend([
                        f"<!-- #{start_code} -->",  # HTMLコメント形式
                        f"// #{start_code}",        # 単一行コメント形式
                        f"/* #{start_code} */",     # 複数行コメント形式
                        f"# #{start_code}",         # シャープコメント形式
                        f"#{start_code}",           # 単純な形式（フォールバック）
                    ])
                
                # 開始マーカーを検索
                start_pos = -1
                start_marker_used = None
                
                for pattern in patterns:
                    debug_logger.log(f"パターン '{pattern}' で開始マーカーを検索")
                    pos = content.find(pattern)
                    if pos != -1:
                        start_pos = pos
                        start_marker_used = pattern
                        debug_logger.log(f"開始マーカー '{pattern}' を位置 {pos} で見つけました")
                        break
                
                if start_pos == -1:
                    debug_logger.log(f"開始マーカー '#{start_code}' が見つかりません。この修正はスキップします。")
                    print_info(f"★開始マーカー '#{start_code}' が見つかりません。この修正はスキップします。")
                    continue
                
                debug_logger.log(f"開始マーカー '{start_marker_used}' を位置 {start_pos} で見つけました")
                print_info(f"開始マーカー '{start_marker_used}' を位置 {start_pos} で見つけました")
                
                # 終了マーカーを検索するパターンを作成
                end_patterns = []
                
                # HTML/XMLファイル用パターン
                if ext in ['.html', '.htm', '.xml', '.svg']:
                    end_patterns.extend([
                        f"<!-- #{end_code} -->",    # HTMLコメント形式
                        f"#{end_code}",             # 単純な形式（フォールバック）
                    ])
                # CSSファイル用パターン
                elif ext in ['.css']:
                    end_patterns.extend([
                        f"/* #{end_code} */",       # CSSコメント形式
                        f"#{end_code}",             # 単純な形式（フォールバック）
                    ])
                # PHPやJavaScript等のC系言語用パターン
                elif ext in ['.php', '.js', '.ts', '.java', '.cs', '.cpp', '.c', '.h']:
                    end_patterns.extend([
                        f"// #{end_code}",          # 単一行コメント形式
                        f"/* #{end_code} */",       # 複数行コメント形式
                        f"#{end_code}",             # 単純な形式（フォールバック）
                    ])
                # Python、Ruby、シェルスクリプト用パターン
                elif ext in ['.py', '.rb', '.sh', '.yml', '.yaml']:
                    end_patterns.extend([
                        f"# #{end_code}",           # シャープコメント形式
                        f"#{end_code}",             # 単純な形式（フォールバック）
                    ])
                # デフォルトパターン（すべての形式を試す）
                else:
                    end_patterns.extend([
                        f"<!-- #{end_code} -->",    # HTMLコメント形式
                        f"// #{end_code}",          # 単一行コメント形式
                        f"/* #{end_code} */",       # 複数行コメント形式
                        f"# #{end_code}",           # シャープコメント形式
                        f"#{end_code}",             # 単純な形式（フォールバック）
                    ])
                
                # 終了マーカーを検索 (開始位置以降を検索)
                end_pos = -1
                end_marker_used = None
                
                for pattern in end_patterns:
                    debug_logger.log(f"パターン '{pattern}' で終了マーカーを検索")
                    pos = content.find(pattern, start_pos + len(start_marker_used))
                    if pos != -1:
                        end_pos = pos
                        end_marker_used = pattern
                        debug_logger.log(f"終了マーカー '{pattern}' を位置 {pos} で見つけました")
                        break
                
                if end_pos == -1:
                    debug_logger.log(f"終了マーカー '#{end_code}' が見つかりません。この修正はスキップします。")
                    print_info(f"★終了マーカー '#{end_code}' が見つかりません。この修正はスキップします。")
                    continue
                
                debug_logger.log(f"終了マーカー '{end_marker_used}' を位置 {end_pos} で見つけました")
                print_info(f"終了マーカー '{end_marker_used}' を位置 {end_pos} で見つけました")
                
                # 終了マーカーのサイズを加える
                end_pos += len(end_marker_used)
                
                # この範囲を新しい内容で置き換え
                before = content[start_pos:end_pos]
                debug_logger.log(f"置換前の内容: {before[:200]}...")
                debug_logger.log_file_content(f"{file_path}_replace_before.txt", before)
                print_info(f"置換前の内容: {before[:100]}...")
                
                # 新しい内容を出力
                preview = new_content[:100] + ("..." if len(new_content) > 100 else "")
                debug_logger.log(f"新しい内容: {preview}")
                debug_logger.log_file_content(f"{file_path}_replace_after.txt", new_content)
                print_info(f"新しい内容: {preview}")
                
                # 置換を実行
                content = content[:start_pos] + new_content + content[end_pos:]
                changed = True
                
                debug_logger.log(f"置換が完了しました")
                print_info(f"置換が完了しました")
            
            # 変更があった場合のみファイルを書き込む
            if changed:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                debug_logger.log(f"ファイル {file_path} を更新しました")
                debug_logger.log_file_content(f"{file_path}_updated.txt", content)
                print_info(f"ファイル {file_path} を更新しました")
            else:
                debug_logger.log(f"警告: ファイル {file_path} に変更はありませんでした")
                print_info(f"★警告: ファイル {file_path} に変更はありませんでした")
            
            # バックアップを削除
            os.remove(backup_path)
            debug_logger.log(f"バックアップを削除しました: {backup_path}")
            
        except Exception as e:
            debug_logger.log(f"エラー: ファイル修正中にエラーが発生しました: {e}")
            # エラーが発生した場合はバックアップから復元
            if os.path.exists(backup_path):
                shutil.copy2(backup_path, file_path)
                os.remove(backup_path)
                debug_logger.log(f"バックアップから復元しました: {backup_path}")
            print_info(f"★エラー: ファイル修正中にエラーが発生しました: {e}")
            import traceback
            traceback.print_exc()
            raise e
    
    def _apply_indentation(self, content, base_indent):
        """コンテンツに基本インデントを適用する"""
        debug_logger.log(f"インデント適用: base_indent='{base_indent}'")
        lines = content.split('\n')
        indented_lines = []
        
        for line in lines:
            if not line.strip():
                # 空行はそのまま
                indented_lines.append(line)
            elif line.lstrip() != line:
                # 既にインデントがある行は、相対的なインデント構造を維持
                # ただし、行の先頭のインデントを基本インデントに置き換える
                content_part = line.lstrip()
                indented_lines.append(base_indent + content_part)
            else:
                # インデントがない行には基本インデントを追加
                indented_lines.append(base_indent + line)
        
        return '\n'.join(indented_lines)
        
    def perform_git_operations(self, base_dir, skip_confirmation=False):
        """Git操作を実行する"""
        debug_logger.log(f"Git操作を開始: {base_dir}")
        try:
            # カレントディレクトリを変更
            original_dir = os.getcwd()
            os.chdir(base_dir)
            debug_logger.log(f"カレントディレクトリを変更: {base_dir}")
            
            # git add
            subprocess.run(["git", "add", "."], check=True)
            debug_logger.log("Git: ファイルを追加しました")
            print_info("Git: ファイルを追加しました")
            
            # git status を実行して変更を確認
            status_output = subprocess.run(["git", "status", "--porcelain"], check=True, capture_output=True, text=True).stdout
            debug_logger.log(f"Git status 出力:\n{status_output}")
            
            # コミットメッセージを決定
            # 最初のファイルのコミットメッセージを使用
            if self.file_list and len(self.file_list) > 0:
                first_file = self.file_list[0]
                file_id = first_file["id"]
                file_path = first_file["path"]
                key = f"{file_id},{file_path}"
                
                commit_message = self.commit_messages.get(key, f"{self.app_name} の更新")
                debug_logger.log(f"コミットメッセージ: {commit_message}")
                
                # git commit
                try:
                    # 変更があるかチェック
                    if status_output.strip():
                        # 確認が必要かどうかをチェック
                        commit_confirmed = True
                        if not skip_confirmation:
                            print_info(f"\nGitコミットを実行します。")
                            print_info(f"コミットメッセージ: {commit_message}")
                            print_info(f"変更されたファイル:")
                            print_info(status_output)
                            response = input("コミットしてもよろしいですか？ (y/n): ")
                            commit_confirmed = response.lower() == 'y'
                        
                        if commit_confirmed:
                            # 変更がある場合のみコミット
                            commit_result = subprocess.run(["git", "commit", "-m", commit_message], check=True, capture_output=True, text=True)
                            debug_logger.log(f"Git commit 出力:\n{commit_result.stdout}")
                            debug_logger.log(f"Git: コミット完了 - {commit_message}")
                            print_info(f"Git: コミット完了 - {commit_message}")
                        else:
                            debug_logger.log("Git: ユーザーがコミットをキャンセルしました")
                            print_info("Git: コミットがキャンセルされました")
                    else:
                        debug_logger.log("Git: 変更がないため、コミットはスキップされました")
                        print_info("★Git: 変更がないため、コミットはスキップされました")
                except subprocess.CalledProcessError as e:
                    debug_logger.log(f"Git: コミット中にエラーが発生しました: {e}")
                    debug_logger.log(f"エラー出力: {e.stderr}")
                    print_info(f"★Git: コミット中にエラーが発生しました: {e}")
            else:
                debug_logger.log("警告: コミットするファイルがありません")
                print_info("★警告: コミットするファイルがありません")
            
            # カレントディレクトリを戻す
            os.chdir(original_dir)
            debug_logger.log(f"カレントディレクトリを元に戻しました: {original_dir}")
            
        except subprocess.CalledProcessError as e:
            debug_logger.log(f"Git操作中にエラーが発生しました: {e}")
            debug_logger.log(f"エラー出力: {e.stderr if hasattr(e, 'stderr') else 'なし'}")
            print_info(f"★Git操作中にエラーが発生しました: {e}")
        except Exception as e:
            debug_logger.log(f"エラー: {e}")
            print_info(f"★エラー: {e}")
    
    def generate_summary(self):
        """解析した内容のサマリーを表示する"""
        debug_logger.log("サマリーの生成を開始")
        print_info("\n===== 手順書解析サマリー =====")
        print_info(f"アプリ名: {self.app_name}")
        print_info(f"準拠形式バージョン: {self.version}")
        print_info(f"ファイル数: {len(self.file_list)}")
        print_info("  新規:", len([f for f in self.file_list if f["type"] == "new"]))
        print_info("  修正:", len([f for f in self.file_list if f["type"] == "modify"]))
        print_info("  削除:", len([f for f in self.file_list if f["type"] == "delete"]))
        print_info("実行コマンド:")
        for cmd in self.run_commands:
            print_info(f"  {cmd}")
        print_info("========================\n")
        debug_logger.log("サマリーの生成が完了しました")

def save_procedure_copy(procedure_content, howto_dir):
    """手順書をHowToBookフォルダに保存する"""
    debug_logger.log(f"手順書のコピーを保存: {howto_dir}")
    try:
        if not os.path.exists(howto_dir):
            os.makedirs(howto_dir)
            debug_logger.log(f"HowToBookディレクトリを作成しました: {howto_dir}")
            print_info(f"HowToBookディレクトリを作成しました: {howto_dir}")
        
        # 最新の番号を取得
        files = os.listdir(howto_dir)
        procedure_files = [f for f in files if re.match(r'^\d{5}\.md', f)]
        
        if procedure_files:
            latest_num = max([int(f.split('.')[0]) for f in procedure_files])
            new_num = latest_num + 1
        else:
            new_num = 0
        
        # 新しい手順書ファイル名
        new_filename = f"{new_num:05d}.md"
        
        # 保存
        with open(os.path.join(howto_dir, new_filename), 'w', encoding='utf-8') as f:
            f.write(procedure_content)
        
        debug_logger.log(f"手順書を保存しました: {new_filename}")
        print_info(f"手順書を保存しました: {new_filename}")
        return new_filename
    
    except Exception as e:
        debug_logger.log(f"手順書の保存に失敗しました: {e}")
        print_info(f"★手順書の保存に失敗しました: {e}")
        return None

def main():
    # コマンドライン引数のパース
    parser = argparse.ArgumentParser(description='手順書パーサー v2.1.0')
    parser.add_argument('procedure_file', help='手順書ファイルのパス')
    parser.add_argument('output_dir', help='出力ディレクトリ')
    parser.add_argument('--debug', action='store_true', help='デバッグモードを有効にする')
    parser.add_argument('-y', '--yes', action='store_true', help='確認なしでGitコミットを実行する')
    args = parser.parse_args()
    
    # デバッグモードの設定
    global debug_logger
    if args.debug:
        debug_logger = DebugLogger(enabled=True)
        debug_logger.log("デバッグモードが有効になりました")
    
    procedure_file = args.procedure_file
    output_dir = args.output_dir
    
    debug_logger.log(f"手順書ファイル: {procedure_file}")
    debug_logger.log(f"出力ディレクトリ: {output_dir}")
    
    # 除外ファイル拡張子の表示
    print_info(f"注意: 以下の拡張子のファイルは処理されません（自動処理から除外）: {', '.join(EXCLUDED_EXTENSIONS)}")
    print_info("これらのファイルタイプは新規作成、修正を行いませんので手動で作成するようにしてください。")
    
    # HowToBookディレクトリのパス
    howto_dir = os.path.join(os.path.dirname(output_dir), "HowToBook")
    debug_logger.log(f"HowToBookディレクトリ: {howto_dir}")
    
    # パーサーの初期化と実行
    procedure_parser = ProcedureParser(procedure_file)
    procedure_parser.parse()
    
    # 手順書コピーの保存
    with open(procedure_file, 'r', encoding='utf-8') as f:
        procedure_content = f.read()
    
    save_procedure_copy(procedure_content, howto_dir)
    
    # サマリー表示
    procedure_parser.generate_summary()
    
    # プロジェクト構造の作成
    procedure_parser.create_project_structure(output_dir)
    
    # Git操作の実行（-yオプションに基づいて確認をスキップするかどうかを決定）
    procedure_parser.perform_git_operations(output_dir, skip_confirmation=args.yes)
    
    print_info(f"\n環境構築が完了しました。出力先: {output_dir}")
    print_info("実行コマンドを実行するには、生成された実行スクリプトを使用してください。")
    print_info(f"※注意: {', '.join(EXCLUDED_EXTENSIONS)} 形式のファイルは手動で作成または修正してください。")
    
    # デバッグログを閉じる
    debug_logger.close()


if __name__ == "__main__":
    main()
