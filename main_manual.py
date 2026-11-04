import os
import glob
import shutil
import subprocess
import sys

# Run initialization first
import env_setup
env_setup.setup_environment()

from download_bins import download_apkeditor, download_morphe_cli, download_release_asset
from utils import merge_apk
import gh_utils
from build_utils import (
    is_version_greater_than, is_version_less_than,
    get_latest_piko_tag, fetch_x_shim, apply_shim,
    extract_version_from_filename, get_target_patches,
    run_morphe_and_extract
)

BASE_APK_DIR = ".base_apk"
OUTPUT_DIR = "output_apks"
BINS_DIR = "bins"
GITHUB_REPO = gh_utils.GITHUB_REPO
LOG_FILENAME = "build_log.txt"

def cleanup_workspace(clean_bins=False, clean_outputs=False):
    print("\n[CLEANUP] Removing temporary files...")
    targets = [
        "morphe-temporary-files", "__pycache__", "big_file_merged",
        "insta_base_merged", "big_file*", "insta_base*", "temp.jks",
        os.path.join(OUTPUT_DIR, "*.idsig")
    ]
    if clean_bins: targets.append(BINS_DIR)
    if clean_outputs: targets.append(OUTPUT_DIR)

    for target in targets:
        paths = glob.glob(target) if any(c in target for c in "*?[") else [target]
        for path in paths:
            if os.path.exists(path):
                try:
                    if os.path.isdir(path): shutil.rmtree(path, ignore_errors=True)
                    else: os.remove(path)
                except Exception: pass

    if clean_bins: os.makedirs(BINS_DIR, exist_ok=True)
    if clean_outputs: os.makedirs(OUTPUT_DIR, exist_ok=True)
    print("  -> Workspace clean.")

def main():
    print("\n=======================================================")
    print(" PIKO AUTOMATED LOCAL BUILDER")
    print("=======================================================\n")

    if not gh_utils.check_github_repo_access(GITHUB_REPO):
        print("\n[!] Pre-check failed. Aborting build process.")
        return

    print("Select start point:")
    print("  [1] Full Build   (Clean ALL, Download tools, Build, Sign, Upload)")
    print("  [2] Debug Build  (Clean ALL, Download tools, Build, Sign, NO Upload)")
    print("  [3] Sign Only    (Sign existing APKs from 'output_apks')")
    print("  [4] Upload Only  (Upload existing APKs directly from 'output_apks')")
    mode = input("Enter your choice (1, 2, 3, or 4): ").strip()
    
    if mode not in ["1", "2", "3", "4"]:
        print("\n[!] Invalid choice. Exiting.")
        return

    is_pre = False
    piko_tag = "v1.0.0"
    target_upload_tag = ""
    final_upload_tag = ""

    if mode in ["1", "2"]:
        print("\nSelect target Piko branch:")
        print("  [1] Stable (Default)")
        print("  [2] Pre-release (Beta)")
        choice = input("Enter your choice (1 or 2): ").strip()
        is_pre = (choice == "2")
    elif mode in ["3", "4"]:
        mode_name = "Sign Only" if mode == "3" else "Upload Only"
        print(f"\n[Optional] GitHub Upload for '{mode_name}' mode")
        recent_tags = gh_utils.get_recent_github_releases(GITHUB_REPO, 10)
        
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
            target_upload_tag = input("Enter target tag manually (e.g., v3.5.0) or leave empty to skip: ").strip()

    cleanup_workspace(clean_bins=(mode in ["1", "2"]), clean_outputs=(mode in ["1", "2"]))

    if not os.path.exists(BASE_APK_DIR): os.makedirs(BASE_APK_DIR)
    if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)

    if mode in ["1", "2"]:
        print("\n[STEP 1] Fetching Piko resources...")
        piko_tag = get_latest_piko_tag(is_pre)
        print(f"  -> [INFO] Downloading Piko patches Version: {piko_tag}")
        download_release_asset("crimera/piko", r".*\.mpp$", "bins", "patches.mpp", include_prereleases=is_pre, version=piko_tag)
        
        print("\n[STEP 2] Preparing build tools...")
        print("  -> [INFO] Downloading external build tools (APKEditor, Morphe CLI)...")
        download_apkeditor()
        download_morphe_cli()

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
            version_str = extract_version_from_filename(filename)
            
            if "twitter" in filename or "x-" in filename or "x_" in filename:
                print(f"\n[STEP 3] Processing Twitter/X: {filename} (Detected v{version_str})")
                is_apkm = filename.endswith((".apkm", ".apks", ".xapk"))
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
                    
                    if os.path.exists(target_merged):
                        base_name = os.path.splitext(filename)[0]
                        merged_out = os.path.join(OUTPUT_DIR, f"{base_name}_merged_unpatched.apk")
                        shutil.copy(target_merged, merged_out)
                else:
                    if working_file != target_merged: shutil.move(working_file, target_merged)
                
                if os.path.exists(target_merged):
                    if is_version_greater_than(version_str, "11.88") and is_version_less_than(version_str, "12.5"):
                        print(f"  -> [INFO] Twitter v{version_str} requires x-shim. Applying x-shim first...")
                        shim_mpp = fetch_x_shim()
                        if shim_mpp: target_merged = apply_shim(cli_jar, shim_mpp, target_merged)

                    print("  -> Building Twitter/X variants...")
                    common_includes = get_target_patches(cli_jar, patch_mpp, "com.twitter.android", excludes=["Bring back twitter", "Dynamic color"])
                    
                    twitter_variants = [
                        {"name": "X (Material You)", "output": f"x-piko-material-you-v{version_str}.apk", "includes": ["Dynamic color"], "excludes": []},
                        {"name": "X (Standard)", "output": f"x-piko-v{version_str}.apk", "includes": [], "excludes": ["Dynamic color"]},
                        {"name": "Twitter (Material You)", "output": f"twitter-piko-material-you-v{version_str}.apk", "includes": ["Bring back twitter", "Dynamic color"], "excludes": []},
                        {"name": "Twitter (Standard)", "output": f"twitter-piko-v{version_str}.apk", "includes": ["Bring back twitter"], "excludes": ["Dynamic color"]}
                    ]

                    for variant in twitter_variants:
                        final_includes = common_includes + variant["includes"]
                        run_morphe_and_extract(cli_jar, patch_mpp, target_merged, variant["output"], final_includes, variant["excludes"])
                    print(f"  -> [SUCCESS] Twitter/X variants saved to '{OUTPUT_DIR}'.")

            elif "insta" in filename:
                print(f"\n[STEP 3] Processing Instagram: {filename} (Detected v{version_str})")
                insta_merged = "insta_base_merged.apk"
                
                for f in glob.glob("insta_base*"):
                    if os.path.isdir(f): shutil.rmtree(f, ignore_errors=True)
                    else:
                        try: os.remove(f)
                        except: pass

                is_apkm = filename.endswith((".apkm", ".apks", ".xapk"))
                working_file = "insta_base.apkm" if is_apkm else "insta_base_merged.apk"
                shutil.copy(file_path, working_file)

                if is_apkm:
                    print("  -> Merging App Bundle (APKM/APKS/XAPK)...")
                    merge_apk(working_file)
                    if os.path.exists(working_file): os.remove(working_file)
                    
                    if os.path.exists(insta_merged):
                        base_name = os.path.splitext(filename)[0]
                        merged_out = os.path.join(OUTPUT_DIR, f"{base_name}_merged_unpatched.apk")
                        shutil.copy(insta_merged, merged_out)
                else:
                    if working_file != insta_merged: shutil.move(working_file, insta_merged)
                
                if os.path.exists(insta_merged):
                    insta_patches = get_target_patches(cli_jar, patch_mpp, "com.instagram.android")
                    if insta_patches:
                        run_morphe_and_extract(cli_jar, patch_mpp, insta_merged, f"instagram-piko-{version_str}.apk", insta_patches, [])
                        print(f"  -> [SUCCESS] Instagram variant saved to '{OUTPUT_DIR}'.")

    # Signature process
    signed_assets = []
    target_apks = glob.glob(os.path.join(OUTPUT_DIR, "*.apk"))
    apksigner_path = env_setup.get_apksigner_path()
    
    if mode in ["1", "2", "3"]:
        print("\n[STEP 4] Aligning and Signing APKs in 'output_apks' folder...")
        if target_apks:
            ZIPALIGN_PATH = apksigner_path.replace("apksigner.bat", "zipalign.exe")
            if not os.path.exists(ZIPALIGN_PATH): ZIPALIGN_PATH = "zipalign"
            
            keystore_path = "ks_pkcs12.keystore"
            if not os.path.exists(keystore_path):
                print(f"  -> [ERROR] Keystore file not found at {keystore_path}")
                sys.exit(1)
                
            for apk_path in target_apks:
                if apk_path.endswith((".clean.apk", ".aligned.apk", ".signed.apk")): continue
                print(f"\n  -> Processing {os.path.basename(apk_path)} ...")
                
                print(f"     [1/2] Zipaligning...")
                temp_aligned = apk_path + ".aligned.apk"
                try:
                    res_align = subprocess.run([ZIPALIGN_PATH, "-p", "-f", "4", apk_path, temp_aligned], capture_output=True, text=True)
                    if res_align.returncode == 0 and os.path.exists(temp_aligned):
                        os.replace(temp_aligned, apk_path)
                    else:
                        if os.path.exists(temp_aligned): os.remove(temp_aligned)
                except Exception: pass

                print(f"     [2/2] Signing with apksigner...")
                temp_signed = apk_path + ".signed.apk"
                cmd = [apksigner_path, "sign", "--ks", keystore_path, "--ks-pass", "pass:123456789", "--ks-key-alias", "jhc", "--key-pass", "pass:123456789", "--out", temp_signed, apk_path]
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode != 0:
                    print(f"     [ERROR] apksigner failed!\n{res.stderr}")
                    sys.exit(1)
                
                os.replace(temp_signed, apk_path)
                signed_assets.append(apk_path)
                print(f"     [SUCCESS] Finished processing {os.path.basename(apk_path)}!")
            
            for idsig_file in glob.glob(os.path.join(OUTPUT_DIR, "*.idsig")):
                try: os.remove(idsig_file)
                except: pass
        else:
            print(f"  -> [INFO] No APK files found in '{OUTPUT_DIR}' to sign.")
            
    elif mode == "4":
        print("\n[STEP 4] Skipped Signing. Collecting APKs from 'output_apks' folder...")
        if target_apks:
            for apk_path in target_apks:
                if not apk_path.endswith((".clean.apk", ".aligned.apk", ".signed.apk")):
                    signed_assets.append(apk_path)
            print(f"  -> Collected {len(signed_assets)} APK(s) for upload.")

    # Cleanup at the end (always cleans bins)
    cleanup_workspace(clean_bins=True, clean_outputs=False)
    assets_to_upload = [apk for apk in signed_assets if "_merged_unpatched" not in apk]

    if mode == "1" and signed_assets:
        if assets_to_upload:
            gh_utils.upload_to_github_release(piko_tag, assets_to_upload, is_pre)
            final_upload_tag = piko_tag if piko_tag.startswith("piko-") else f"piko-{piko_tag}"
            
    elif mode in ["3", "4"] and signed_assets and target_upload_tag:
        if assets_to_upload:
            gh_utils.upload_to_github_release(target_upload_tag, assets_to_upload, is_pre=False, exact_tag=True)
            final_upload_tag = target_upload_tag

    print("\n=======================================================")
    print(" BUILD PIPELINE COMPLETED SUCCESSFULLY")
    print("=======================================================\n")

    # Upload build log to GitHub
    if final_upload_tag and subprocess.run(["where", "gh"] if os.name == 'nt' else ["which", "gh"], capture_output=True).returncode == 0:
        print(f"  -> Uploading build_log.txt to release {final_upload_tag}...")
        sys.stdout.flush()
        subprocess.run(["gh", "release", "upload", final_upload_tag, LOG_FILENAME, "--clobber", "--repo", GITHUB_REPO], capture_output=True)
        print("  -> [SUCCESS] Log file uploaded.")

if __name__ == "__main__":
    main()
