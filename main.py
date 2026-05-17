import os
import re
import time
import json
import urllib.request
import subprocess
import argparse
import shutil
import apkmirror
import github
from functools import cmp_to_key

from apkmirror import Version, Variant
from build_variants import build_apks
from download_bins import download_apkeditor, download_morphe_cli, download_release_asset
from utils import panic, merge_apk, publish_release, patch_apk

# バージョン文字列を数値的に比較し、v1がv2より新しい場合にTrueを返す
def version_greater(v1: str | None, v2: str | None) -> bool:
    if not v1: return False
    if not v2: return True
    def normalize(v: str):
        v = v.replace("piko-", "").replace("piko ", "").lstrip('v')
        parts = v.split('-', 1)
        main_part = parts[0]
        prerelease_part = parts[1] if len(parts) > 1 else ""

        main_nums = re.findall(r'\d+', main_part)
        main_nums = [int(n) for n in main_nums[:3]]
        while len(main_nums) < 3:
            main_nums.append(0)

        pre_parts = []
        if prerelease_part:
            for part in re.split(r'(\d+)', prerelease_part):
                if part == '': continue
                if part.isdigit(): pre_parts.append(int(part))
                else: pre_parts.append(part)
        return main_nums, pre_parts

    nums1, pre1 = normalize(v1)
    nums2, pre2 = normalize(v2)

    for i in range(3):
        if nums1[i] != nums2[i]: return nums1[i] > nums2[i]

    if not pre1 and pre2: return True
    if pre1 and not pre2: return False

    for p1, p2 in zip(pre1, pre2):
        if p1 != p2:
            if type(p1) == type(p2): return p1 > p2
            else: return str(p1) > str(p2)

    return len(pre1) > len(pre2)

# GitHubリポジトリからリリース一覧を取得し、StableとPreの最新版を返す
def get_latest_releases(repo: str, is_my_repo: bool = False) -> dict:
    cmd = ["gh", "api", f"repos/{repo}/releases?per_page=30"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        releases = json.loads(result.stdout)
    except Exception as e:
        print(f"  -> [WARNING] Failed to fetch releases for {repo}: {e}")
        return {"stable": None, "pre": None}
        
    valid_stable = []
    valid_pre = []

    for r in releases:
        tag = r.get("tag_name")
        is_pre = r.get("prerelease", False)
        
        if is_my_repo and not tag.startswith("piko"):
            continue

        if is_pre or "dev" in tag.lower() or "alpha" in tag.lower() or "beta" in tag.lower():
            valid_pre.append(tag)
        else:
            valid_stable.append(tag)

    def cmp_versions(v1, v2):
        if v1 == v2: return 0
        return 1 if version_greater(v1, v2) else -1

    if valid_stable:
        valid_stable.sort(key=cmp_to_key(cmp_versions), reverse=True)
    if valid_pre:
        valid_pre.sort(key=cmp_to_key(cmp_versions), reverse=True)

    return {
        "stable": valid_stable[0] if valid_stable else None,
        "pre": valid_pre[0] if valid_pre else None
    }

# patches-list.json を取得する
def fetch_patches_json(is_pre: bool) -> list:
    branch = "dev" if is_pre else "main"
    url = f"https://raw.githubusercontent.com/crimera/piko/refs/heads/{branch}/patches-list.json"
    print(f"  -> Fetching {url}...")
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode('utf-8'))
            return data.get("patches", []) if isinstance(data, dict) else data
    except Exception as e:
        print(f"  -> [ERROR] Failed to load patches-list.json: {e}")
        return []

# 指定APKバージョンと互換性のある全パッチを抽出する（Instagram用などに使用）
def get_patches_for_version(patches_list: list, package_name: str, target_version: str) -> list:
    patches = []
    for patch in patches_list:
        patch_name = patch.get("name")
        compat = patch.get("compatiblePackages")

        supports_version = False
        if not compat: 
            supports_version = True
        elif isinstance(compat, list):
            for pkg in compat:
                if isinstance(pkg, dict) and pkg.get("packageName") == package_name:
                    extracted_versions = set()
                    if pkg.get("versions"): extracted_versions.update(pkg.get("versions"))
                    if pkg.get("targets"):
                        for t in pkg.get("targets"):
                            ver = t.get("version")
                            if ver and not t.get("isExperimental", False):
                                extracted_versions.add(ver)
                    if not extracted_versions or target_version in extracted_versions:
                        supports_version = True
                    break
        elif isinstance(compat, dict) and package_name in compat:
            versions = compat[package_name]
            if not versions or target_version in versions:
                supports_version = True

        if supports_version:
            patches.append(patch_name)

    return patches

# PikoのJSONからTwitterの対象バージョン（Beta/Alpha除外）を抽出する
def get_twitter_target_version(patches_list: list) -> str | None:
    versions_set = set()
    for patch in patches_list:
        compat = patch.get("compatiblePackages")
        if isinstance(compat, list):
            for pkg in compat:
                if isinstance(pkg, dict) and pkg.get("packageName") == "com.twitter.android":
                    targets = pkg.get("targets", [])
                    for t in targets:
                        ver = t.get("version")
                        is_exp = t.get("isExperimental", False)
                        if ver and not is_exp:
                            vl = ver.lower()
                            if "alpha" not in vl and "beta" not in vl:
                                versions_set.add(ver)
    
    if versions_set:
        release_versions = [v for v in versions_set if "-release" in v.lower()]
        target_list = release_versions if release_versions else list(versions_set)
        
        def parse_ver(v):
            return [int(x) for x in re.findall(r'\d+', v)]
        
        target_twitter_version = sorted(target_list, key=parse_ver)[-1]
        print(f"  -> Targeted Twitter Version: {target_twitter_version}")
        return target_twitter_version
    else:
        print("  -> [WARNING] No valid stable Twitter target found.")
        return None

# Twitter用のAPKダウンロードからパッチ適用、GitHubリリースまでのパイプラインを実行する
def process(latest_version: Version, pikoRelease, download_link: Variant, release_tag: str, release_title: str):
    print("\n[STEP] Downloading APK and tools...")
    
    print(f"  -> Downloading {latest_version.version} bundle from APKMirror...")
    apkmirror.download_apk(download_link)
    target_file = "big_file.apkm"

    if not os.path.exists(target_file):
        panic("  -> [ERROR] Failed to download APK from APKMirror.")

    print("  -> Downloading APKEditor...")
    download_apkeditor()
    if not os.path.exists("big_file_merged.apk"):
        print(f"  -> Merging APK ({target_file} -> big_file_merged.apk)...")
        merge_apk(target_file)
    else:
        print("  -> Merged APK already exists. Skipping merge.")

    print("\n[STEP] Preparing Morphe CLI...")
    download_morphe_cli()
    
    message: str = f"""
Changelogs:
[piko-{pikoRelease["tag_name"]}]({pikoRelease["html_url"]})
"""

    print(f"\n[STEP] Building patched APKs (Target: {latest_version.version})...")
    build_apks(latest_version)

    print("\n[STEP] Publishing release to GitHub...")
    # ここで引数を明確に分離：タグ名(スペースなし), タイトル(スペースあり)
    publish_release(
        release_tag,
        [
            f"x-piko-v{latest_version.version}.apk",
            f"x-piko-material-you-v{latest_version.version}.apk",
            f"twitter-piko-v{latest_version.version}.apk",
            f"twitter-piko-material-you-v{latest_version.version}.apk",
        ],
        message,
        release_title
    )
    print("  -> [DONE] Release successfully published.")

# Instagram用の対象バージョン判定、APK取得、パッチ適用、リリース追記を実行する
def check_and_build_instagram(release_tag: str, release_title: str, pikoRelease: dict, patches_list: list, force: bool = False):
    print(f"\n=======================================================")
    print(f"[STEP 8] INITIATING INSTAGRAM BUILD PIPELINE")
    print(f"=======================================================")
    
    print("\n[STEP 8.1] Verifying existing release assets...")
    if not force:
        cmd_check = ["gh", "release", "view", release_tag, "--json", "assets"]
        try:
            res = subprocess.run(cmd_check, capture_output=True, text=True, check=True)
            assets = json.loads(res.stdout).get("assets", [])
            has_insta = any("instagram" in a["name"].lower() for a in assets)
            if has_insta:
                print("  -> [SKIP] Instagram APK is already present in this release.")
                return
        except subprocess.CalledProcessError:
            print(f"  -> [WARNING] Release {release_tag} does not exist yet. It will be created.")
    else:
        print("  -> [FORCE] Instagram build forced by CLI argument or Piko update.")

    print("\n[STEP 8.3] Resolving supported Instagram versions (New JSON Format)...")
    versions_set = set()
    for patch in patches_list:
        compat = patch.get("compatiblePackages")
        if isinstance(compat, dict) and "com.instagram.android" in compat:
            if compat["com.instagram.android"]: versions_set.update(compat["com.instagram.android"])
        elif isinstance(compat, list):
            for pkg in compat:
                if isinstance(pkg, dict) and pkg.get("packageName") == "com.instagram.android":
                    if pkg.get("versions"): versions_set.update(pkg.get("versions"))
                    if pkg.get("targets"):
                        for t in pkg.get("targets"):
                            ver = t.get("version")
                            if ver and not t.get("isExperimental", False):
                                versions_set.add(ver)
    
    if not versions_set:
        print("  -> [ERROR] No supported versions found for Instagram.")
        return
        
    supported_versions = sorted(list(versions_set), key=lambda s: [int(u) for u in s.split('.')])[-5:]
    print(f"  -> Found {len(supported_versions)} recent compatible versions.")
    
    print("\n[STEP 8.4] Downloading base APK from APKMirror (Direct Sniper Mode)...")
    
    final_insta_ver = None
    insta_base_apk_to_patch = None
    
    for version in reversed(supported_versions):
        print(f"\n  -> [FALLBACK ROUTINE] Trying to fetch Instagram v{version}...")
        
        slug = version.replace('.', '-')
        target_url = f"https://www.apkmirror.com/apk/instagram/instagram-instagram/instagram-{slug}-release/"
        
        variants = []
        print(f"  -> Targeting Exact URL: {target_url}")
        time.sleep(2) 
        
        try:
            tv = Version(version=version, link=target_url)
            variants = apkmirror.get_variants(tv)
            if variants:
                print(f"  -> [SUCCESS] Hit direct URL and found {len(variants)} variants!")
        except Exception as e:
            print(f"  -> [FAILED] {e}")
            time.sleep(3) 
        
        if not variants:
            print(f"  -> [SKIP] No variants found for v{version}.")
            continue
            
        target_variant = None
        for v in variants:
            if getattr(v, 'is_bundle', False):
                arch = getattr(v, 'architecture', '').lower()
                dpi = getattr(v, 'screen_dpi', '').lower()
                if "arm64" in arch or "universal" in arch or "nodpi" in arch or "120-640" in dpi:
                    target_variant = v
                    break
        if not target_variant:
            for v in variants:
                if not getattr(v, 'is_bundle', False):
                    arch = getattr(v, 'architecture', '').lower()
                    dpi = getattr(v, 'screen_dpi', '').lower()
                    if "arm64" in arch or "universal" in arch or "nodpi" in arch or "120-640" in dpi:
                        target_variant = v
                        break
        if not target_variant:
            target_variant = variants[0]

        ext = ".apkm" if getattr(target_variant, 'is_bundle', False) else ".apk"
        filepath = f"insta_base{ext}"
        merged_filepath = "insta_base_merged.apk"

        if os.path.exists(filepath): os.remove(filepath)
        if os.path.exists(merged_filepath): os.remove(merged_filepath)

        download_apkeditor()

        try:
            apkmirror.download_apk(target_variant, path=filepath)
            if os.path.exists(filepath):
                print(f"  -> [SUCCESS] Downloaded Instagram base APK for v{version}")
                if getattr(target_variant, 'is_bundle', False):
                    print("  -> Merging Instagram APKM bundle...")
                    merge_apk(filepath)
                    insta_base_apk_to_patch = merged_filepath
                else:
                    insta_base_apk_to_patch = filepath
                final_insta_ver = version
                break
        except Exception as e:
            print(f"  -> [BLOCKED] Download failed: {e}")
            if os.path.exists(filepath): os.remove(filepath)
            print("  -> Retrying with an older version...")
            time.sleep(3)

    if not final_insta_ver or not insta_base_apk_to_patch or not os.path.exists(insta_base_apk_to_patch):
        print("  -> [FATAL] All fallback attempts failed for Instagram.")
        return

    print("\n[STEP 8.5] Selecting compatible patches...")
    insta_patches = get_patches_for_version(patches_list, "com.instagram.android", final_insta_ver)
    print(f"  -> Extracted {len(insta_patches)} applicable patches.")

    print("\n[STEP 8.6] Building patched APK with Morphe CLI...")
    download_morphe_cli()
    cli = "bins/morphe-cli.jar"
    patches_mpp = "bins/patches.mpp"
    output_apk = f"instagram-piko-v{final_insta_ver}.apk"
    
    print(f"  -> Building {output_apk}...")
    patch_apk(cli, patches_mpp, insta_base_apk_to_patch, includes=insta_patches, excludes=[], out=output_apk)
    
    print("\n[STEP 8.7] Uploading Instagram APK to GitHub...")
    if os.path.exists(output_apk):
        print(f"  -> Uploading {output_apk} to release '{release_tag}'...")
        try:
            subprocess.run(["gh", "release", "upload", release_tag, output_apk, "--clobber"], check=True)
            print("  -> [SUCCESS] Instagram APK successfully published.")
        except subprocess.CalledProcessError:
            print(f"  -> [WARNING] Failed to upload. Release might not exist. Creating release '{release_tag}'...")
            message = f"Changelogs:\n[piko-{pikoRelease['tag_name']}]({pikoRelease['html_url']})"
            try:
                # ここもタイトル(release_title)とタグ(release_tag)を分離
                subprocess.run(["gh", "release", "create", "--latest", release_tag, output_apk, "--notes", message, "--title", release_title], check=True)
                print("  -> [SUCCESS] Created release and published Instagram APK.")
            except subprocess.CalledProcessError as e:
                print(f"  -> [ERROR] Failed to create release: {e}")
    else:
        print("  -> [ERROR] Failed to build Instagram APK.")

# 引数を解釈し、更新の有無を確認した上で各アプリのビルド処理を制御する
def main():
    parser = argparse.ArgumentParser(description="Piko Auto Builder")
    parser.add_argument("--app", choices=["twitter", "instagram", "all"], default="all", help="Which app to build")
    args = parser.parse_args()

    repo_url = "monsivamon/twitter-apk"
    upstream_repo = "crimera/piko"

    print("\n[STEP 1] Fetching release history for upstream and my repo...")
    upstream = get_latest_releases(upstream_repo)
    my_repo = get_latest_releases(repo_url, is_my_repo=True)
    
    print("\n--- VERSION STATUS ---")
    print(f"Upstream Stable: {upstream['stable']}")
    print(f"Upstream Pre   : {upstream['pre']}")
    print(f"My Repo  Stable: {my_repo['stable']}")
    print(f"My Repo  Pre   : {my_repo['pre']}")
    print("----------------------\n")

    print("[STEP 2] Verifying build history for updates...")
    build_targets = []
    
    if upstream["stable"] and version_greater(upstream["stable"], my_repo["stable"]):
        build_targets.append({"tag": upstream["stable"], "is_pre": False})
        
    if upstream["pre"] and version_greater(upstream["pre"], my_repo["pre"]):
        build_targets.append({"tag": upstream["pre"], "is_pre": True})

    if not build_targets:
        print("  -> [EXIT] No new updates found. Skipping build.")
        return

    print(f"  -> [RESULT] Found {len(build_targets)} pending update(s).")
    
    for target in build_targets:
        piko_tag = target["tag"]
        is_pre = target["is_pre"]
        
        # 内部タグ用（スペースなし）と表示タイトル用（スペースあり）を生成
        clean_tag = piko_tag.replace("piko-", "").replace("piko ", "").lstrip("v")
        release_tag = f"piko-v{clean_tag}"
        release_title = f"piko v{clean_tag}"

        print(f"\n=======================================================")
        print(f"INITIATING BUILD PIPELINE FOR: {release_title} ({args.app.upper()})")
        print(f"=======================================================")

        print("\n[STEP 3] Fetching the latest Piko patches from GitHub...")
        pikoRelease = download_release_asset(
            upstream_repo,
            r".*\.mpp$",
            "bins",
            "patches.mpp",
            include_prereleases=is_pre,
            version=piko_tag
        )
        
        patches_list = fetch_patches_json(is_pre)

        if args.app in ["twitter", "all"]:
            print("\n[STEP 3.5] Resolving supported Twitter versions from Piko JSON...")
            target_twitter_version = get_twitter_target_version(patches_list)
            
            if target_twitter_version:
                print(f"\n[STEP 4] Fetching Twitter base APK for v{target_twitter_version} (Direct Sniper Mode)...")
                slug1 = target_twitter_version.replace('.', '-')
                slug2 = target_twitter_version.split('-')[0].replace('.', '-')
                
                urls_to_try = [
                    f"https://www.apkmirror.com/apk/x-corp/twitter/x-{slug1}-release/",
                    f"https://www.apkmirror.com/apk/x-corp/twitter/x-{slug2}-release/"
                ]

                target_variant = None
                tv = None
                for url in urls_to_try:
                    print(f"  -> Targeting URL: {url}")
                    tv = Version(version=target_twitter_version, link=url)
                    try:
                        variants = apkmirror.get_variants(tv)
                        if variants:
                            for v in variants:
                                if getattr(v, 'is_bundle', False):
                                    arch = getattr(v, 'architecture', '').lower()
                                    if "universal" in arch or "arm64" in arch or "nodpi" in arch:
                                        target_variant = v
                                        break
                            if target_variant:
                                print(f"  -> [SUCCESS] Found universal bundle variant!")
                                break
                    except Exception as e:
                        print(f"  -> [FAILED] {e}")
                    time.sleep(2)

                if target_variant and tv:
                    # タグとタイトルの両方を渡す
                    process(tv, pikoRelease, target_variant, release_tag, release_title)
                else:
                    print("  -> [WARNING] Valid release not found on APKMirror.")
            else:
                print("  -> [WARNING] Could not resolve target Twitter version.")
        
        if args.app in ["instagram", "all"]:
            # タグとタイトルの両方を渡す
            check_and_build_instagram(release_tag, release_title, pikoRelease, patches_list, force=True)

if __name__ == "__main__":
    main()