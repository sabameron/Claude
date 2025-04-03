import os
import re
import sys
import shutil
import subprocess
from pathlib import Path

# スクリプトのバージョン
VERSION = "1.0.0"

class ProcedureValidator:
    """手順書のフォーマット検証クラス"""
    
    def __init__(self, content):
        self.content = content
        self.errors = []
    
    def validate(self):
        """手順書のフォーマットを検証する"""
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
            
            if file_id in file_ids:
                self.errors.append(f"ファイルID {file_id} が重複しています")
            file_ids.add(file_id)
            
            # 新規・修正の場合はコード管理番号をチェック
            if action in ["新規", "修正"]:
                # ファイルセクションの内容を取得
                file_end_pattern = r'### (新規|修正|削除),\d{5},[^\n]+(?:\nコミット内容：[^\n]+)?\n(.*?)(?=### |\Z)'
                file_content_match = re.search(file_end_pattern, self.content[match.start():], re.DOTALL)
                
                if file_content_match:
                    file_content = file_content_match.group(2)
                    
                    # コード管理番号のチェック
                    if action == "新規":
                        # 新規ファイルの場合
                        code_numbers = re.findall(r'(?://|#|<!--|/\*) #(\d{5})', file_content)
                        if not code_numbers:
                            self.errors.append(f"ファイルID {file_id} のコード管理番号が見つかりません")
                        
                        # 連番かつ一意のチェック
                        unique_numbers = set(code_numbers)
                        if len(code_numbers) != len(unique_numbers):
                            self.errors.append(f"ファイルID {file_id} のコード管理番号に重複があります")
                    
                    elif action == "修正":
                        # 修正区間のチェック
                        modification_sections = re.finditer(r'#### #(\d{5})-#(\d{5})\n```[a-z]*\n(.*?)```', file_content, re.DOTALL)
                        for mod_match in modification_sections:
                            start_code = mod_match.group(1)
                            end_code = mod_match.group(2)
                            mod_content = mod_match.group(3)
                            
                            # 修正区間内にコード管理番号があるかチェック
                            section_code_numbers = re.findall(r'(?://|#|<!--|/\*) #(\d{5})', mod_content)
                            if not section_code_numbers:
                                self.errors.append(f"ファイルID {file_id} の修正区間 #{start_code}-#{end_code} にコード管理番号が見つかりません")
                            
                            # 修正区間の開始と終了コードが含まれているかチェック
                            if start_code not in section_code_numbers:
                                self.errors.append(f"ファイルID {file_id} の修正区間 #{start_code}-#{end_code} に開始コード #{start_code} が含まれていません")
                            if end_code not in section_code_numbers:
                                self.errors.append(f"ファイルID {file_id} の修正区間 #{start_code}-#{end_code} に終了コード #{end_code} が含まれていません")
        
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
        except Exception as e:
            print(f"エラー: 手順書の読み込みに失敗しました: {e}")
            sys.exit(1)
        
        # バリデーション
        validator = ProcedureValidator(self.procedure_content)
        if not validator.validate():
            print("エラー: 手順書のフォーマットが不正です:")
            for error in validator.get_errors():
                print(f"- {error}")
            sys.exit(1)
        
        # バージョンの取得と照合
        self.version = validator.get_version()
        version_without_v = self.version[1:] if self.version and self.version.startswith('v') else ""
        if version_without_v != VERSION:
            print(f"警告: スクリプトのバージョン({VERSION})と手順書の準拠形式バージョン({version_without_v})が一致しません")
            response = input("続行しますか？ (y/n): ")
            if response.lower() != 'y':
                sys.exit(0)
        
        # タイトルの取得
        title_match = re.search(r'# ([^\n]+)', self.procedure_content)
        if title_match:
            self.app_name = title_match.group(1)
        
        # 概要の取得
        overview_match = re.search(r'## 概要\n(.*?)(?=##)', self.procedure_content, re.DOTALL)
        if overview_match:
            self.overview = overview_match.group(1).strip()
        
        # アプリ実行コマンドの取得
        commands_match = re.search(r'## アプリ実行コマンド\n```bash\n(.*?)```', self.procedure_content, re.DOTALL)
        if commands_match:
            self.run_commands = commands_match.group(1).strip().split('\n')
        
        # 必要ファイル一覧の取得
        file_list_match = re.search(r'## 必要ファイル一覧\n(.*?)(?=##)', self.procedure_content, re.DOTALL)
        if file_list_match:
            file_list_content = file_list_match.group(1).strip()
            file_lines = [line.strip() for line in file_list_content.split('\n') if line.strip()]
            
            for line in file_lines:
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
        
        # ファイルの中身とコミットメッセージの取得
        file_section_pattern = r'### (新規|修正|削除),(\d{5}),([^\n]+)\n(コミット内容：([^\n]+)\n)?'
        file_sections = re.finditer(file_section_pattern, self.procedure_content)
        
        for match in file_sections:
            action = match.group(1)
            file_id = match.group(2)
            file_path = match.group(3)
            commit_msg = match.group(5) if match.group(5) else f"{action} {file_path}"
            
            # ファイルの内容を取得
            file_end_pattern = r'### (新規|修正|削除),\d{5},[^\n]+(?:\nコミット内容：[^\n]+)?\n(.*?)(?=### |\Z)'
            file_content_match = re.search(file_end_pattern, self.procedure_content[match.start():], re.DOTALL)
            
            if file_content_match:
                file_content = file_content_match.group(2).strip()
                key = f"{file_id},{file_path}"
                
                if action == "新規":
                    # 新規ファイルの場合、コードブロックの内容を抽出
                    code_block_match = re.search(r'```[a-z]*\n(.*?)```', file_content, re.DOTALL)
                    if code_block_match:
                        self.file_contents[key] = code_block_match.group(1)
                
                elif action == "修正":
                    # 修正ファイルの場合、修正区間を抽出
                    self.file_modifications[key] = []
                    
                    modification_sections = re.finditer(r'#### #(\d{5})-#(\d{5})\n```[a-z]*\n(.*?)```', file_content, re.DOTALL)
                    for mod_match in modification_sections:
                        start_code = mod_match.group(1)
                        end_code = mod_match.group(2)
                        mod_content = mod_match.group(3)
                        
                        self.file_modifications[key].append({
                            "start": start_code,
                            "end": end_code,
                            "content": mod_content
                        })
                
                # コミットメッセージを保存
                self.commit_messages[key] = commit_msg
        
        # 備考の取得
        notes_match = re.search(r'## 備考\n(.*?)(?=$)', self.procedure_content, re.DOTALL)
        if notes_match:
            self.notes = notes_match.group(1).strip()
        
        return self
    
    def create_project_structure(self, base_dir):
        """解析した手順書に基づいてプロジェクト構造を作成する"""
        if not os.path.exists(base_dir):
            os.makedirs(base_dir)
            print(f"ディレクトリ作成: {base_dir}")
        
        # ファイル操作
        for file_entry in self.file_list:
            try:
                action = file_entry["type"]
                file_id = file_entry["id"]
                file_path = file_entry["path"]
                full_path = os.path.join(base_dir, file_path)
                dir_path = os.path.dirname(full_path)
                
                # ディレクトリがなければ作成
                if dir_path and not os.path.exists(dir_path):
                    os.makedirs(dir_path)
                    print(f"ディレクトリ作成: {dir_path}")
                
                key = f"{file_id},{file_path}"
                
                if action == "delete":
                    # ファイル削除
                    if os.path.exists(full_path):
                        os.remove(full_path)
                        print(f"ファイル削除: {full_path}")
                    else:
                        print(f"警告: 削除対象ファイル {full_path} が見つかりません")
                
                elif action == "new":
                    # 新規ファイル作成
                    if key in self.file_contents:
                        with open(full_path, 'w', encoding='utf-8') as f:
                            f.write(self.file_contents[key])
                        print(f"ファイル作成: {full_path}")
                    else:
                        print(f"警告: ファイル {file_path} の内容が見つかりません")
                
                elif action == "modify":
                    # ファイル修正
                    if key in self.file_modifications and os.path.exists(full_path):
                        self._modify_file(full_path, self.file_modifications[key])
                        print(f"ファイル更新: {full_path}")
                    else:
                        print(f"警告: ファイル {file_path} の修正情報が見つからないか、ファイルが存在しません")
            
            except Exception as e:
                print(f"エラー: ファイル {file_path} の処理に失敗しました: {e}")
        
        # 実行コマンドをbat/shファイルとして保存
        if self.run_commands:
            if os.name == 'nt':  # Windows
                script_path = os.path.join(base_dir, "run.bat")
                with open(script_path, 'w', encoding='utf-8') as f:
                    f.write("@echo off\n")
                    for cmd in self.run_commands:
                        f.write(f"{cmd}\n")
                print(f"実行スクリプト作成: {script_path}")
            else:  # Unix/Linux/Mac
                script_path = os.path.join(base_dir, "run.sh")
                with open(script_path, 'w', encoding='utf-8') as f:
                    f.write("#!/bin/bash\n")
                    for cmd in self.run_commands:
                        f.write(f"{cmd}\n")
                os.chmod(script_path, 0o755)  # 実行権限を付与
                print(f"実行スクリプト作成: {script_path}")
    
    def _modify_file(self, file_path, modifications):
        """ファイルの特定範囲を修正する"""
        try:
            # ファイル内容の読み込み
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # バックアップの作成
            backup_path = file_path + ".bak"
            shutil.copy2(file_path, backup_path)
            
            # 各修正区間を処理（順序に依存する可能性があるため、変更位置が大きい順に処理）
            mods_sorted = sorted(modifications, key=lambda m: int(m["start"]), reverse=True)
            
            for mod in mods_sorted:
                start_code = mod["start"]
                end_code = mod["end"]
                new_content = mod["content"]
                
                # コード管理番号のパターン（コメント記号とスペース、インデントなどを考慮）
                code_pattern = r'([^\n]*?)(?://|#|<!--|/\*)[^\n]*?#(%s)\b'
                
                # 開始コードと終了コードの位置を検索
                start_match = re.search(code_pattern % re.escape(start_code), content)
                end_match = re.search(code_pattern % re.escape(end_code), content)
                
                if start_match and end_match:
                    # 行の先頭のインデントを取得
                    start_indent = start_match.group(1)
                    
                    # 開始コードの行の開始位置を取得
                    start_line_start = content.rfind('\n', 0, start_match.start()) + 1
                    if start_line_start <= 0:
                        start_line_start = 0
                    
                    # 終了コードの行の終了位置を取得
                    end_line_end = content.find('\n', end_match.end())
                    if end_line_end == -1:
                        end_line_end = len(content)
                    
                    # 次のコード管理番号の位置を取得
                    next_code_match = re.search(r'(?://|#|<!--|/\*)[^\n]*#\d{5}\b', content[end_line_end:])
                    next_code_pos = end_line_end + next_code_match.start() if next_code_match else len(content)
                    
                    # 各行にインデントを適用
                    indented_content = ""
                    for line in new_content.split('\n'):
                        # 既にインデントがある行はそのまま
                        if not line.strip() or line.lstrip() != line:
                            indented_content += line + '\n'
                        else:
                            # インデントがない行にはインデントを追加
                            indented_content += start_indent + line + '\n'
                    
                    # 最後の改行を削除
                    if indented_content.endswith('\n'):
                        indented_content = indented_content[:-1]
                    
                    # 内容を置き換え
                    content = content[:start_line_start] + indented_content + content[next_code_pos:]
                else:
                    print(f"警告: コード管理番号 #{start_code} や #{end_code} がファイル {file_path} 内で見つかりません")
            
            # 修正内容をファイルに書き込む
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # バックアップを削除
            os.remove(backup_path)
            
        except Exception as e:
            # エラーが発生した場合はバックアップから復元
            if os.path.exists(backup_path):
                shutil.copy2(backup_path, file_path)
                os.remove(backup_path)
            print(f"ファイル修正中にエラーが発生しました: {e}")
            raise e
    
    def perform_git_operations(self, base_dir):
        """Git操作を実行する"""
        try:
            # カレントディレクトリを変更
            original_dir = os.getcwd()
            os.chdir(base_dir)
            
            # git add
            subprocess.run(["git", "add", "."], check=True)
            print("Git: ファイルを追加しました")
            
            # コミットメッセージを決定
            # 最初のファイルのコミットメッセージを使用
            if self.file_list and len(self.file_list) > 0:
                first_file = self.file_list[0]
                file_id = first_file["id"]
                file_path = first_file["path"]
                key = f"{file_id},{file_path}"
                
                commit_message = self.commit_messages.get(key, f"{self.app_name} の更新")
                
                # git commit
                subprocess.run(["git", "commit", "-m", commit_message], check=True)
                print(f"Git: コミット完了 - {commit_message}")
            else:
                print("警告: コミットするファイルがありません")
            
            # カレントディレクトリを戻す
            os.chdir(original_dir)
            
        except subprocess.CalledProcessError as e:
            print(f"Git操作中にエラーが発生しました: {e}")
        except Exception as e:
            print(f"エラー: {e}")
    
    def generate_summary(self):
        """解析した内容のサマリーを表示する"""
        print("\n===== 手順書解析サマリー =====")
        print(f"アプリ名: {self.app_name}")
        print(f"準拠形式バージョン: {self.version}")
        print(f"ファイル数: {len(self.file_list)}")
        print("  新規:", len([f for f in self.file_list if f["type"] == "new"]))
        print("  修正:", len([f for f in self.file_list if f["type"] == "modify"]))
        print("  削除:", len([f for f in self.file_list if f["type"] == "delete"]))
        print("実行コマンド:")
        for cmd in self.run_commands:
            print(f"  {cmd}")
        print("========================\n")


def save_procedure_copy(procedure_content, howto_dir):
    """手順書をHowToBookフォルダに保存する"""
    try:
        if not os.path.exists(howto_dir):
            os.makedirs(howto_dir)
            print(f"HowToBookディレクトリを作成しました: {howto_dir}")
        
        # 最新の番号を取得
        files = os.listdir(howto_dir)
        procedure_files = [f for f in files if re.match(r'^\d{5}\.md$', f)]
        
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
        
        print(f"手順書を保存しました: {new_filename}")
        return new_filename
    
    except Exception as e:
        print(f"手順書の保存に失敗しました: {e}")
        return None


def main():
    if len(sys.argv) < 3:
        print(f"使用方法: python procedure_parser.py <手順書ファイルパス> <出力ディレクトリ>")
        print(f"バージョン: {VERSION}")
        sys.exit(1)
    
    procedure_file = sys.argv[1]
    output_dir = sys.argv[2]
    
    # HowToBookディレクトリのパス
    howto_dir = os.path.join(os.path.dirname(output_dir), "HowToBook")
    
    # パーサーの初期化と実行
    parser = ProcedureParser(procedure_file)
    parser.parse()
    
    # 手順書コピーの保存
    with open(procedure_file, 'r', encoding='utf-8') as f:
        procedure_content = f.read()
    
    save_procedure_copy(procedure_content, howto_dir)
    
    # サマリー表示
    parser.generate_summary()
    
    # プロジェクト構造の作成
    parser.create_project_structure(output_dir)
    
    # Git操作の実行
    parser.perform_git_operations(output_dir)
    
    print(f"\n環境構築が完了しました。出力先: {output_dir}")
    print("実行コマンドを実行するには、生成された実行スクリプトを使用してください。")

if __name__ == "__main__":
    main()
