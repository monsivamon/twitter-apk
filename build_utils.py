import os
import re
import time
import shutil
import subprocess
import sys
import urllib.request
import json

# Constants for directories
BINS_DIR = "bins"
OUTPUT_DIR = "output_apks"

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

def is_version_less_than(ver_str: str, target: str) -> bool:
    try:
        clean_ver = ver_str.split('-')[0]
        v_parts = [int(x) for x in clean_ver.split('.') if x.isdigit()]
        t_parts = [int(x) for x in target.split('.') if x.isdigit()]
        for i in range(max(len(v_parts), len(t_parts))):
            v = v_parts[i] if i < len(v_parts) else 0
            t = t_parts[i] if i < len(t_parts) else 0
            if v < t: return True
            if v > t: return False
        return False
    except Exception:
        return False

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

def fetch_x_shim():
    shim_path = os.path.join(BINS_DIR, "x-shim.mpp")
    if os.path.exists(shim_path):
        print("  -> [DEBUG] x-shim.mpp already exists. Skipping download.")
        return shim_path
    
    print("  -> Fetching latest x-shim release info from GitLab...")
    url = "https://gitlab.com/api/v4/projects/inotia00%2Fx-shim/releases"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            releases = json.loads(response.read().decode('utf-8'))
            if not releases: return None
            latest = releases[0]
            download_url = next((link["url"] for link in latest.get("assets", {}).get("links", []) if link.get("url", "").endswith(".mpp")), None)
            if not download_url: return None
            print(f"  -> [INFO] Downloading x-shim Version: {latest.get('tag_name', 'Unknown')}")
            urllib.request.urlretrieve(download_url, shim_path)
            print("  -> [SUCCESS] Downloaded x-shim.mpp")
            return shim_path
    except Exception as e:
        print(f"  -> [WARNING] Failed to fetch x-shim: {e}")
        return None

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
    
    # Restore log output
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

def get_target_patches(cli_path: str, mpp_path: str, target_package: str, excludes: list | None = None) -> list:
    if excludes is None: excludes = []
    cmd = ["java", "-jar", cli_path, "list-patches", f"--patches={mpp_path}", "-p"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        out = result.stdout
    except subprocess.CalledProcessError as e:
        sys.exit(1)

    patches, current_patch = [], None
    for line in out.splitlines():
        s_line = line.strip()
        if not s_line: continue
        if s_line.startswith('Index:'):
            current_patch = {"name": "", "packages": []}
            patches.append(current_patch)
        elif s_line.startswith('Name:') and current_patch is not None:
            current_patch["name"] = s_line[5:].strip()
        elif s_line.startswith('Package name:'):
            if current_patch is not None:
                current_patch["packages"].append(s_line.split('Package name:', 1)[1].strip())

    applicable_patches = []
    for patch in patches:
        if (not patch["packages"]) or (target_package in patch["packages"]):
            if patch["name"] not in excludes:
                applicable_patches.append(patch["name"])
    return applicable_patches

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
    
    # Restore debug command output
    print(f"  -> [DEBUG] Full Command: {' '.join(cmd)}")
    
    res = subprocess.run(cmd, capture_output=True, text=True)
    
    # Restore stdout (e.g., patch application status)
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
        if os.path.exists(dest_path): os.unlink(dest_path)
        shutil.move(cli_output, dest_path)
        return dest_path
    else:
        print(f"  -> [FATAL ERROR] Patched file not found at expected location: {cli_output}")
        sys.exit(1)