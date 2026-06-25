import os
import re
import json
import urllib.request
import shutil
import glob
import subprocess
import sys
import time
import zipfile

# =====================================================================
#  GitHubリポジトリの構成設定（ビルド成果物の展開先）
# =====================================================================
GITHUB_REPO = "monsivamon/twitter-apk"

# =====================================================================
#  apksignerの絶対パスを格納するグローバル変数
# =====================================================================
APKSIGNER_PATH = None

# =====================================================================
#  実行環境の依存関係解決処理（Javaおよびapksignerのパス環境変数への追加）
# =====================================================================

# システムのPATH環境変数にJavaが存在するか検証し、未検出の場合は既知のディレクトリを走査して追加する。
def ensure_java_in_path():
    if shutil.which("java"):
        print("  -> [DEBUG] Java is already in system PATH.")
        return
    if os.name == 'nt':
        search_patterns = [
            r"C:\Program Files\Eclipse Adoptium\jdk-17*\bin",
            r"C:\Program Files\Eclipse Adoptium\jdk-21*\bin",
            r"C:\Program Files\Java\jdk-17*\bin",
            r"C:\Program Files\Java\jdk-21*\bin",
            r"C:\Program Files\Amazon Corretto\jdk17*\bin"
        ]
        for pattern in search_patterns:
            matches = glob.glob(pattern)
            if matches:
                os.environ["PATH"] = matches[0] + os.pathsep + os.environ["PATH"]
                print(f"  -> [INFO] Injected Java into PATH: {matches[0]}")
                return
        print("  -> [WARNING] Could not automatically find Java in standard directories. Ensure Java is installed and in PATH.")

ensure_java_in_path()

# apksignerの可用性を検証し、未検出の場合はローカルのAndroid SDKディレクトリ内を走査する。
def ensure_apksigner_in_path():
    global APKSIGNER_PATH
    existing = shutil.which("apksigner")
    if existing:
        APKSIGNER_PATH = existing
        print(f"  -> [DEBUG] apksigner found in system PATH: {existing}")
        return
    if os.name == 'nt':
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        sdk_path = os.path.join(local_appdata, "Android", "Sdk", "build-tools")
        if os.path.isdir(sdk_path):
            versions = sorted(os.listdir(sdk_path), reverse=True)
            for ver in versions:
                apk_path = os.path.join(sdk_path, ver, "apksigner.bat")
                if os.path.exists(apk_path):
                    APKSIGNER_PATH = apk_path
                    print(f"  -> [INFO] Found apksigner at {apk_path}")
                    return
    print("  -> [ERROR] 'apksigner' not found. Please install Android SDK Build Tools and add it to PATH.")
    sys.exit(1)

ensure_apksigner_in_path()

# subprocess.run関数のオーバーライドによる、標準出力のエンコーディング例外の回避処理
original_run = subprocess.run
def patched_run(*args, **kwargs):
    if kwargs.get("text") is True or kwargs.get("capture_output") is True:
        kwargs["encoding"] = "utf-8"
        kwargs["errors"] = "replace"
        kwargs["text"] = True
    return original_run(*args, **kwargs)

subprocess.run = patched_run

# =====================================================================
#  外部ユーティリティモジュールのインポート
# =====================================================================
from download_bins import download_apkeditor, download_morphe_cli, download_release_asset
from utils import merge_apk

BASE_APK_DIR = ".base_apk"
OUTPUT_DIR = "output_apks"
BINS_DIR = "bins"

# 指定されたバージョン文字列が、対象バージョンを超過しているかを判定する関数
def is_version_greater_than(ver_str: str, target: str) -> bool:
    try:
        clean_ver = ver_str.split('-')[0]
        v_parts = [int(x) for x in clean_ver.split('.') if x.isdigit()]
        t_parts = [int(x) for x in target.split('.') if x.isdigit()]
        for i in range(max(len(v_parts), len(t_parts))):
            v = v_parts[i] if i < len(v_parts) else 0
            t = t_parts[i] if i < len(t_parts) else 0
            if v > t: return True
            if v < t: return False
        return False
    except Exception:
        return False

# リモートリポジトリ（Piko）より、最新のリリースタグ情報を取得する処理
def get_latest_piko_tag(is_pre: bool) -> str:
    print("  -> Fetching latest Piko release info from GitHub...")
    url = "https://api.github.com/repos/crimera/piko/releases"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            releases = json.loads(response.read().decode('utf-8'))
            for r in releases:
                if is_pre and r.get("prerelease"): return r["tag_name"]
                elif not is_pre and not r.get("prerelease"): return r["tag_name"]
            return releases[0]["tag_name"]
    except Exception as e:
        print(f"  -> [WARNING] Failed to fetch latest release tag: {e}. Falling back to v1.0.0.")
        return "v1.0.0"

# GitLabリポジトリより、中間パッチモジュール（x-shim）を取得する関数
def fetch_x_shim():
    shim_path = os.path.join(BINS_DIR, "x-shim.mpp")
    if os.path.exists(shim_path):
        print("  -> [DEBUG] x-shim.mpp already exists. Skipping download.")
        return shim_path
    
    print("  -> Fetching latest x-shim from GitLab...")
    url = "https://gitlab.com/api/v4/projects/inotia00%2Fx-shim/releases"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            releases = json.loads(response.read().decode('utf-8'))
            if not releases:
                print("  -> [WARNING] No releases found for x-shim.")
                return None
            
            latest = releases[0]
            download_url = None
            for link in latest.get("assets", {}).get("links", []):
                if link.get("url", "").endswith(".mpp"):
                    download_url = link["url"]
                    break
            
            if not download_url:
                print("  -> [WARNING] Could not find .mpp asset in x-shim release.")
                return None
                
            print(f"  -> Downloading x-shim from {download_url} ...")
            urllib.request.urlretrieve(download_url, shim_path)
            print("  -> [SUCCESS] Downloaded x-shim.mpp")
            return shim_path
    except Exception as e:
        print(f"  -> [WARNING] Failed to fetch x-shim: {e}")
        return None

# 取得したx-shimを中間パッチとして対象APKに適用する関数
def apply_shim(cli_jar, shim_mpp, input_apk):
    output_apk = input_apk.replace(".apk", "_shimmed.apk")
    print(f"\n  -> Applying x-shim patch to {input_apk}...")
    
    cmd = [
        "java", "-jar", cli_jar, "patch",
        "-p", shim_mpp,
        "--continue-on-error",
        "--unsigned",
        input_apk
    ]
    
    print(f"  -> [DEBUG] Full Command: {' '.join(cmd)}")
    
    res = subprocess.run(cmd, capture_output=True, text=True)
    
    if res.stdout: print(res.stdout)
    if res.returncode != 0:
        print("--- CLI Error Output ---", file=sys.stderr)
        print(res.stderr, file=sys.stderr)
        print("  -> [ERROR] Shim patching failed!")
        sys.exit(1)
        
    output_text = (res.stdout or "") + "\n" + (res.stderr or "")
    match = re.search(r"Saved to\s+([^\r\n]+)", output_text)
    
    if match:
        cli_output = match.group(1).strip()
        time.sleep(1)
        if os.path.exists(cli_output):
            if os.path.exists(output_apk): os.remove(output_apk)
            shutil.move(cli_output, output_apk)
            print(f"  -> [SUCCESS] Shim successfully applied: {output_apk}")
            return output_apk
            
    print("  -> [FATAL ERROR] Could not find shimmed APK output.")
    sys.exit(1)

# 対象GitHubリポジトリより、直近のリリース履歴を取得する（取得上限: limit件）
def get_recent_github_releases(repo_name: str, limit: int = 10) -> list:
    print(f"  -> Fetching recent releases from {repo_name}...")
    cmd = ["gh", "api", f"repos/{repo_name}/releases?per_page={limit}"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        releases = json.loads(res.stdout)
        tags = [r.get("tag_name") for r in releases if r.get("tag_name")]
        return tags
    except Exception as e:
        print(f"  -> [WARNING] Failed to fetch releases from {repo_name}: {e}")
        return []

# 対象ファイル名より、正規表現を用いてバージョン情報を抽出する処理
def extract_version_from_filename(filename: str) -> str:
    match = re.search(r'(\d+\.\d+\.\d+[-a-zA-Z0-9.]*)', filename)
    if match:
        ver = match.group(1)
        for ext in [".apk", ".apkm", ".apks", ".xapk", "_pairip", "_bypassed", "_ripped"]:
            if ext in ver:
                ver = ver.split(ext)[0]
        parsed = ver.strip("-")
        print(f"  -> [DEBUG] Parsed version '{parsed}' from '{filename}'.")
        return parsed
    print(f"  -> [DEBUG] Could not extract version from '{filename}'. Using 'local'.")
    return "local"

# =====================================================================
#  CLIツールの標準出力を解析し、指定パッケージに適用可能なパッチ群のリストを生成する共通処理
# =====================================================================
def get_target_patches(cli_path: str, mpp_path: str, target_package: str, excludes: list | None = None) -> list:
    if excludes is None:
        excludes = []
        
    print(f"  -> Extracting patch list dynamically for '{target_package}' from {mpp_path} via CLI...")
    
    cmd = ["java", "-jar", cli_path, "list-patches", f"--patches={mpp_path}", "-p"]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        out = result.stdout
    except subprocess.CalledProcessError as e:
        print(f"  -> [FATAL] Failed to extract patches from CLI. Error: {e.stderr}")
        sys.exit(1)
    except Exception as e:
        print(f"  -> [FATAL] Failed to execute CLI command. Error: {e}")
        sys.exit(1)

    patches = []
    current_patch = None

    for line in out.splitlines():
        s_line = line.strip()
        if not s_line:
            continue

        if s_line.startswith('Index:'):
            current_patch = {"name": "", "packages": []}
            patches.append(current_patch)
            
        elif s_line.startswith('Name:') and current_patch is not None:
            current_patch["name"] = s_line[5:].strip()
            
        elif s_line.startswith('Package name:'):
            pkg_name = s_line.split('Package name:', 1)[1].strip()
            if current_patch is not None:
                current_patch["packages"].append(pkg_name)

    applicable_patches = []

    for patch in patches:
        patch_name = patch["name"]
        packages = patch["packages"]
        
        is_target_patch = False
        if not packages:
            is_target_patch = True
        elif target_package in packages:
            is_target_patch = True
            
        if is_target_patch:
            if patch_name not in excludes:
                applicable_patches.append(patch_name)

    return applicable_patches

# 指定された条件に基づき、作業ディレクトリ内の不要な一時ファイルおよびディレクトリを削除する処理
def cleanup_workspace(clean_bins=False, clean_outputs=False):
    print("\n[CLEANUP] Removing temporary files...")

    targets = [
        "morphe-temporary-files",
        "__pycache__",
        "big_file_merged",
        "insta_base_merged",
        "big_file*",
        "insta_base*",
        "temp.jks",
    ]
    if clean_bins:
        targets.append(BINS_DIR)
    if clean_outputs:
        targets.append(OUTPUT_DIR)

    for target in targets:
        paths = glob.glob(target) if any(c in target for c in "*?[") else [target]
        for path in paths:
            if os.path.exists(path):
                try:
                    if os.path.isdir(path):
                        shutil.rmtree(path, ignore_errors=True)
                        print(f"  -> [DEBUG] Removed directory: {path}")
                    else:
                        os.remove(path)
                        print(f"  -> [DEBUG] Removed temporary file: {path}")
                except Exception:
                    pass

    if clean_bins:
        os.makedirs(BINS_DIR, exist_ok=True)
    if clean_outputs:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
    print("  -> Workspace clean.")

# 指定されたGitHubリポジトリに対するアクセス権限および到達性を検証する処理
def check_github_repo_access(repo_name):
    print("\n[PRE-CHECK] Verifying GitHub repository access...")
    if not repo_name or "ここ" in repo_name:
        print("  -> [ERROR] GITHUB_REPO is not configured correctly.")
        return False
    if not shutil.which("gh"):
        print("  -> [WARNING] GitHub CLI ('gh') is not installed. Uploading will be skipped.")
        return True 
    print(f"  -> Pinging repository: {repo_name} ...")
    res = subprocess.run(["gh", "repo", "view", repo_name], capture_output=True, text=True)
    if res.returncode != 0:
        print(f"  -> [ERROR] Cannot access GitHub repository ({repo_name}).")
        return False
    print("  -> [SUCCESS] Repository access confirmed.")
    return True

# GitHub上に新規リリースを作成し、ビルド済みのAPKファイルをアップロードする処理
def upload_to_github_release(piko_tag, file_paths, is_pre, exact_tag=False):
    if not file_paths:
        print("  -> [INFO] No built files to upload.")
        return
    if not shutil.which("gh"):
        return

    print("\n=======================================================")
    print(" GITHUB AUTO RELEASE PIPELINE")
    print("=======================================================")
    
    if exact_tag:
        tag = piko_tag
        clean_tag = tag.replace("piko-", "")
    else:
        tag = piko_tag if piko_tag.startswith("piko-") else f"piko-{piko_tag}"
        clean_tag = piko_tag.replace("piko-", "")
        
    title = f"piko {clean_tag}"
    notes = f"Changelogs:\n[{tag}](https://github.com/crimera/piko/releases/tag/{clean_tag})\n\nThis version was built/signed manually."
    
    print(f"  -> Attempting to create GitHub Release ({tag})...")
    create_cmd = ["gh", "release", "create", tag] + file_paths + ["--title", title, "--notes", notes]
    if is_pre:
        create_cmd.append("--prerelease")
    if GITHUB_REPO:
        create_cmd.extend(["--repo", GITHUB_REPO])
    
    res = subprocess.run(create_cmd, capture_output=True, text=True)
    
    if res.returncode != 0:
        if "already exists" in res.stderr.lower():
            print("  -> [INFO] Release already exists. Uploading files to existing release...")
            upload_cmd = ["gh", "release", "upload", tag] + file_paths + ["--clobber"]
            if GITHUB_REPO:
                upload_cmd.extend(["--repo", GITHUB_REPO])
            res_upload = subprocess.run(upload_cmd, capture_output=True, text=True)
            if res_upload.returncode == 0:
                print(f"  -> [SUCCESS] Assets uploaded to existing Release {tag}.")
            else:
                print(f"  -> [ERROR] Failed to upload assets:\n{res_upload.stderr}")
        else:
            print(f"  -> [ERROR] Failed to create GitHub release:\n{res.stderr}")
    else:
        print(f"  -> [SUCCESS] Created new GitHub Release {tag} and uploaded assets.")

# Morphe CLIを呼び出してパッチ適用処理を実行し、生成されたAPKを指定の出力ディレクトリへ移動する（署名処理は当スコープ外とする）
def run_morphe_and_extract(cli_jar, patch_mpp, input_apk, output_apk_name, includes, excludes):
    cmd = [
        "java", "-jar", cli_jar, "patch",
        "-p", patch_mpp,
        "--continue-on-error",
        "--unsigned"
    ]
    for i in includes: cmd.extend(["-e", i])
    for e in excludes: cmd.extend(["-d", e])
    cmd.append(input_apk)
    
    print(f"\n  -> Patching APK to create {output_apk_name}...")
    print(f"  -> [DEBUG] Full Command: {' '.join(cmd)}")
    
    res = subprocess.run(cmd, capture_output=True, text=True)
    
    if res.stdout: print(res.stdout)
    if res.returncode != 0:
        print("--- CLI Error Output ---", file=sys.stderr)
        print(res.stderr, file=sys.stderr)
        res.check_returncode()
    
    output_text = (res.stdout or "") + "\n" + (res.stderr or "")
    match = re.search(r"Saved to\s+([^\r\n]+)", output_text)
    
    if not match:
        print(f"  -> [FATAL ERROR] Could not parse output path from CLI log.")
        sys.exit(1)
        
    cli_output = match.group(1).strip()
    
    time.sleep(1) 
    
    if os.path.exists(cli_output):
        dest_path = os.path.join(OUTPUT_DIR, output_apk_name)
        print(f"  -> Moving patched APK to '{dest_path}'")
        if os.path.exists(dest_path):
            os.unlink(dest_path)
        shutil.move(cli_output, dest_path)
        return dest_path
    else:
        print(f"  -> [FATAL ERROR] Patched file not found at expected location: {cli_output}")
        sys.exit(1)

def main():
    print("\n=======================================================")
    print(" PIKO AUTOMATED LOCAL BUILDER")
    print("=======================================================\n")

    # GitHubリポジトリのアクセス権を事前確認
    if not check_github_repo_access(GITHUB_REPO):
        print("\n[!] Pre-check failed. Aborting build process.")
        return

    # 実行モードの選択プロンプト
    print("Select start point:")
    print("  [1] Full Build   (Clean ALL, Download tools, Build, Sign)")
    print("  [2] Build & Sign (Keep existing tools in 'bins', Build, Sign)")
    print("  [3] Sign Only    (Sign existing APKs from 'output_apks')")
    print("  [4] Upload Only  (Upload existing APKs directly from 'output_apks')")
    mode = input("Enter your choice (1, 2, 3, or 4): ").strip()
    
    if mode not in ["1", "2", "3", "4"]:
        print("\n[!] Invalid choice. Exiting.")
        return

    is_pre = False
    piko_tag = "v1.0.0"
    target_upload_tag = ""

    # 選択された実行モードに応じた、追加パラメータの入力プロンプト
    if mode in ["1", "2"]:
        print("\nSelect target Piko branch:")
        print("  [1] Stable (Default)")
        print("  [2] Pre-release (Beta)")
        choice = input("Enter your choice (1 or 2): ").strip()
        is_pre = (choice == "2")
    elif mode in ["3", "4"]:
        mode_name = "Sign Only" if mode == "3" else "Upload Only"
        print(f"\n[Optional] GitHub Upload for '{mode_name}' mode")
        recent_tags = get_recent_github_releases(GITHUB_REPO, 10)
        
        if recent_tags:
            print("Select target release tag for upload:")
            for i, tag in enumerate(recent_tags, 1):
                print(f"  [{i}] {tag}")
            print("  [0] Skip upload")
            
            while True:
                choice = input(f"Enter your choice (0-{len(recent_tags)}): ").strip()
                if choice == "0":
                    target_upload_tag = ""
                    break
                elif choice.isdigit() and 1 <= int(choice) <= len(recent_tags):
                    target_upload_tag = recent_tags[int(choice) - 1]
                    break
                else:
                    print("  -> [!] Invalid choice. Please enter a valid number.")
        else:
            # GitHub CLI（gh）コマンド実行失敗時の代替入力処理
            target_upload_tag = input("Enter target tag manually (e.g., v3.5.0) or leave empty to skip: ").strip()

    # 実行モードに応じた、事前の作業領域初期化処理
    cleanup_workspace(clean_bins=(mode == "1"), clean_outputs=(mode in ["1", "2"]))

    if not os.path.exists(BASE_APK_DIR):
        os.makedirs(BASE_APK_DIR)
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    # ---------------------------------------------------------
    # 第1段階および第2段階: 必須ツール群およびパッチファイルの取得処理
    # ---------------------------------------------------------
    if mode == "1":
        print("\n[STEP 1] Fetching Piko resources...")
        piko_tag = get_latest_piko_tag(is_pre)
        print(f"  -> Target Piko Tag: {piko_tag}")
        download_release_asset("crimera/piko", r".*\.mpp$", "bins", "patches.mpp", include_prereleases=is_pre, version=piko_tag)
        
        print("\n[STEP 2] Preparing build tools...")
        download_apkeditor()
        download_morphe_cli()
    elif mode == "2":
        print("\n[STEP 1 & 2] Skipped download. Fetching tag info for release...")
        piko_tag = get_latest_piko_tag(is_pre)
    elif mode in ["3", "4"]:
        next_action = "Signature" if mode == "3" else "Upload"
        print(f"\n[STEP 1-3] Skipped. Proceeding directly to {next_action} processing.")

    # ---------------------------------------------------------
    # 第3段階: 対象APK群に対するパッチ適用およびビルド処理
    # ---------------------------------------------------------
    if mode in ["1", "2"]:
        base_files = glob.glob(os.path.join(BASE_APK_DIR, "*"))
        print(f"\n[DEBUG] Found {len(base_files)} file(s) in {BASE_APK_DIR}.")
        if not base_files:
            print(f"\n[!] No base APK files found in '{BASE_APK_DIR}'. Please place them and run again.")
            return

        cli_jar = "bins/morphe-cli.jar"
        patch_mpp = "bins/patches.mpp"

        for file_path in base_files:
            filename = os.path.basename(file_path).lower()
            print(f"\n  -> [DEBUG] Inspecting file: {filename}")
            version_str = extract_version_from_filename(filename)
            
            # 対象パッケージ: Twitter / X 用のビルドシーケンス
            if "twitter" in filename or "x-" in filename or "x_" in filename:
                print(f"\n[STEP 3] Processing Twitter/X: {filename} (Detected v{version_str})")
                is_apkm = filename.endswith(".apkm") or filename.endswith(".apks") or filename.endswith(".xapk")
                working_file = "big_file.apkm" if is_apkm else "big_file_merged.apk"
                
                for f in glob.glob("big_file*"):
                    if os.path.isdir(f): shutil.rmtree(f, ignore_errors=True)
                    else:
                        try: os.remove(f)
                        except: pass
                        
                shutil.copy(file_path, working_file)
                target_merged = "big_file_merged.apk"
                
                if is_apkm:
                    print("  -> Merging App Bundle (APKM/APKS/XAPK)...")
                    merge_apk(working_file)
                    if os.path.exists(working_file): os.remove(working_file)
                    
                    # 結合済み未パッチAPKのバックアップ保存処理
                    if os.path.exists(target_merged):
                        base_name = os.path.splitext(filename)[0]
                        merged_out = os.path.join(OUTPUT_DIR, f"{base_name}_merged_unpatched.apk")
                        shutil.copy(target_merged, merged_out)
                        print(f"  -> [INFO] Saved unpatched merged APK as '{os.path.basename(merged_out)}'")
                else:
                    if working_file != target_merged: shutil.move(working_file, target_merged)
                
                if os.path.exists(target_merged):
                    
                    # =============== 中間パッチ（Shim）適用シーケンス ===============
                    if is_version_greater_than(version_str, "11.88"):
                        print(f"  -> [INFO] Twitter v{version_str} is > 11.88. Applying x-shim first...")
                        shim_mpp = fetch_x_shim()
                        if shim_mpp:
                            target_merged = apply_shim(cli_jar, shim_mpp, target_merged)
                    # ====================================================

                    print("  -> Building Twitter/X variants...")
                    common_includes = get_target_patches(cli_jar, patch_mpp, "com.twitter.android", excludes=["Bring back twitter", "Dynamic color"])
                    
                    # 生成対象となるバリアント構成の定義リスト
                    twitter_variants = [
                        {
                            "name": "X (Material You)",
                            "output": f"x-piko-material-you-v{version_str}.apk",
                            "includes": ["Dynamic color"],
                            "excludes": []
                        },
                        {
                            "name": "X (Standard)",
                            "output": f"x-piko-v{version_str}.apk",
                            "includes": [],
                            "excludes": ["Dynamic color"]
                        },
                        {
                            "name": "Twitter (Material You)",
                            "output": f"twitter-piko-material-you-v{version_str}.apk",
                            "includes": ["Bring back twitter", "Dynamic color"],
                            "excludes": []
                        },
                        {
                            "name": "Twitter (Standard)",
                            "output": f"twitter-piko-v{version_str}.apk",
                            "includes": ["Bring back twitter"],
                            "excludes": ["Dynamic color"]
                        }
                    ]

                    for variant in twitter_variants:
                        print(f"\n  -> Building Variant: {variant['name']}")
                        final_includes = common_includes + variant["includes"]
                        run_morphe_and_extract(
                            cli_jar, 
                            patch_mpp, 
                            target_merged, 
                            variant["output"], 
                            final_includes, 
                            variant["excludes"]
                        )
                        
                    print(f"  -> [SUCCESS] Twitter/X variants saved to '{OUTPUT_DIR}'.")

            # 対象パッケージ: Instagram 用のビルドシーケンス
            elif "insta" in filename:
                print(f"\n[STEP 3] Processing Instagram: {filename} (Detected v{version_str})")
                insta_merged = "insta_base_merged.apk"
                
                for f in glob.glob("insta_base*"):
                    if os.path.isdir(f): shutil.rmtree(f, ignore_errors=True)
                    else:
                        try: os.remove(f)
                        except: pass

                is_apkm = filename.endswith(".apkm") or filename.endswith(".apks") or filename.endswith(".xapk")
                working_file = "insta_base.apkm" if is_apkm else "insta_base_merged.apk"
                shutil.copy(file_path, working_file)

                if is_apkm:
                    print("  -> Merging App Bundle (APKM/APKS/XAPK)...")
                    merge_apk(working_file)
                    if os.path.exists(working_file): os.remove(working_file)
                    
                    # 結合済み未パッチAPKのバックアップ保存処理
                    if os.path.exists(insta_merged):
                        base_name = os.path.splitext(filename)[0]
                        merged_out = os.path.join(OUTPUT_DIR, f"{base_name}_merged_unpatched.apk")
                        shutil.copy(insta_merged, merged_out)
                        print(f"  -> [INFO] Saved unpatched merged APK as '{os.path.basename(merged_out)}'")
                else:
                    if working_file != insta_merged: shutil.move(working_file, insta_merged)
                
                if os.path.exists(insta_merged):
                    insta_patches = get_target_patches(cli_jar, patch_mpp, "com.instagram.android")
                    if insta_patches:
                        run_morphe_and_extract(cli_jar, patch_mpp, insta_merged, f"instagram-piko-{version_str}.apk", insta_patches, [])
                        print(f"  -> [SUCCESS] Instagram variant saved to '{OUTPUT_DIR}'.")

    # ---------------------------------------------------------
    # 第4段階: 出力ディレクトリ内のAPKに対する構造修復、アライメント最適化、および暗号化署名処理
    # ---------------------------------------------------------
    signed_assets = []
    target_apks = glob.glob(os.path.join(OUTPUT_DIR, "*.apk"))
    
    if mode in ["1", "2", "3"]:
        print("\n[STEP 4] Cleaning, Aligning, and Signing APKs in 'output_apks' folder...")
        if target_apks:
            
            # 静的型解析における型推論エラーを回避するためのアサーション
            assert APKSIGNER_PATH is not None
            
            ZIPALIGN_PATH = APKSIGNER_PATH.replace("apksigner.bat", "zipalign.exe")
            if not os.path.exists(ZIPALIGN_PATH):
                ZIPALIGN_PATH = "zipalign"
            
            keystore_path = os.path.abspath("ks_pkcs12.keystore")
            if not os.path.exists(keystore_path):
                print(f"  -> [ERROR] Keystore file not found at {keystore_path}")
                sys.exit(1)
                
            for apk_path in target_apks:
                # 処理途中で生成された一時ファイル（中間生成物）を除外対象とする
                if apk_path.endswith(".clean.apk") or apk_path.endswith(".aligned.apk") or apk_path.endswith(".signed.apk"):
                    continue
                    
                print(f"\n  -> Processing {os.path.basename(apk_path)} ...")
                
                # 1. ZIPアーカイブ構造の整合性修復（META-INFディレクトリの削除処理）
                print(f"     [1/3] Cleaning ZIP structure (Removing META-INF)...")
                temp_clean = apk_path + ".clean.apk"
                try:
                    with zipfile.ZipFile(apk_path, 'r') as zin:
                        with zipfile.ZipFile(temp_clean, 'w') as zout:
                            for item in zin.infolist():
                                if item.filename.startswith("META-INF/"):
                                    continue
                                zout.writestr(item, zin.read(item.filename))
                    os.replace(temp_clean, apk_path)
                except Exception as e:
                    print(f"     [WARNING] Failed to clean ZIP structure: {e}")
                    if os.path.exists(temp_clean): os.remove(temp_clean)

                # 2. Zipalignによるメモリアライメントの最適化処理
                print(f"     [2/3] Zipaligning...")
                temp_aligned = apk_path + ".aligned.apk"
                align_cmd = [ZIPALIGN_PATH, "-p", "-f", "4", apk_path, temp_aligned]
                try:
                    res_align = subprocess.run(align_cmd, capture_output=True, text=True)
                    if res_align.returncode == 0 and os.path.exists(temp_aligned):
                        os.replace(temp_aligned, apk_path)
                    else:
                        print(f"     [WARNING] Zipalign failed. Installation may fail on newer Android versions.")
                        if os.path.exists(temp_aligned): os.remove(temp_aligned)
                except Exception as e:
                    print(f"     [WARNING] Zipalign execution error: {e}")

                # 3. apksignerを用いたデジタル署名の付与処理
                print(f"     [3/3] Signing with apksigner...")
                temp_signed = apk_path + ".signed.apk"
                cmd = [
                    APKSIGNER_PATH, "sign",
                    "--ks", keystore_path,
                    "--ks-pass", "pass:123456789",
                    "--ks-key-alias", "jhc",
                    "--key-pass", "pass:123456789",
                    "--out", temp_signed,
                    apk_path
                ]
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode != 0:
                    print(f"     [ERROR] apksigner failed:\n{res.stderr}")
                    sys.exit(1)
                
                os.replace(temp_signed, apk_path)
                signed_assets.append(apk_path)
                print(f"     [SUCCESS] Finished processing {os.path.basename(apk_path)}!")
                
            print("\n  -> All APKs in 'output_apks' processed and signed successfully.")
        else:
            print(f"  -> [INFO] No APK files found in '{OUTPUT_DIR}' to sign.")
            
    elif mode == "4":
        print("\n[STEP 4] Skipped Signing. Collecting APKs from 'output_apks' folder...")
        if target_apks:
            for apk_path in target_apks:
                if not (apk_path.endswith(".clean.apk") or apk_path.endswith(".aligned.apk") or apk_path.endswith(".signed.apk")):
                    signed_assets.append(apk_path)
            print(f"  -> Collected {len(signed_assets)} APK(s) for upload.")
        else:
            print(f"  -> [INFO] No APK files found in '{OUTPUT_DIR}' to upload.")

    # 最終終了処理: 作業用の一時ファイルおよびディレクトリの削除
    cleanup_workspace(clean_bins=True, clean_outputs=False)

    # ---------------------------------------------------------
    # 第5段階: GitHubリポジトリへのリリース作成および成果物のアップロード処理
    # ---------------------------------------------------------
    # アップロード対象リストより、結合済み未パッチAPKファイルを除外するフィルタリング処理
    assets_to_upload = [apk for apk in signed_assets if "_merged_unpatched" not in apk]

    if mode in ["1", "2"] and signed_assets:
        if assets_to_upload:
            print(f"  -> [DEBUG] Assets to upload: {assets_to_upload}")
            upload_to_github_release(piko_tag, assets_to_upload, is_pre)
        else:
            print("  -> [INFO] No patched APKs found to upload to GitHub.")
            
    elif mode in ["3", "4"] and signed_assets:
        if target_upload_tag:
            if assets_to_upload:
                print(f"\n  -> [DEBUG] Uploading assets to target release '{target_upload_tag}': {assets_to_upload}")
                upload_to_github_release(target_upload_tag, assets_to_upload, is_pre=False, exact_tag=True)
            else:
                print("\n  -> [INFO] No patched APKs found to upload to GitHub.")
        else:
            mode_name = "Sign Only" if mode == "3" else "Upload Only"
            print(f"\n[INFO] Skipped GitHub upload because '{mode_name}' mode was used and no target tag was specified.")

    print("\n=======================================================")
    print(" BUILD PIPELINE COMPLETED SUCCESSFULLY")
    print("=======================================================\n")

if __name__ == "__main__":
    main()