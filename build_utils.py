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

# ---------------------------------------------------------------------------
# Patch metadata (Old / NewX categories)
# ---------------------------------------------------------------------------
PATCHES_LIST_URL = "https://raw.githubusercontent.com/crimera/piko/{tag}/patches-list.json"
OLD_CATEGORY = "Old patches"
NEWX_CATEGORY = "NewX patches"
NEWX_NAME_PREFIX = "NewX:"
NEWX_BRANDING_PATCH = "NewX: Restore Twitter branding"
PATCH_FAMILIES = ("old", "newx")

def fetch_patches_list(tag: str):
    """Download Piko's patches-list.json for the given tag (cached in bins/).

    The CLI listing does not expose patch categories, but patches-list.json
    does, which is what lets us keep Old and NewX patches apart.
    """
    os.makedirs(BINS_DIR, exist_ok=True)
    safe_tag = re.sub(r'[^A-Za-z0-9._-]', '_', tag or 'latest')
    cache_path = os.path.join(BINS_DIR, f"patches-list-{safe_tag}.json")

    if os.path.exists(cache_path):
        print(f"  -> [DEBUG] Patch metadata for {tag} already cached.")
    else:
        url = PATCHES_LIST_URL.format(tag=tag)
        print(f"  -> Fetching patch metadata: {url}")
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                data = response.read()
            with open(cache_path, 'wb') as f:
                f.write(data)
            print(f"  -> [SUCCESS] Downloaded patch metadata ({len(data)} bytes).")
        except Exception as e:
            print(f"  -> [WARNING] Failed to fetch patch metadata: {e}")
            return None

    try:
        with open(cache_path, encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"  -> [WARNING] Failed to parse patch metadata: {e}")
        return None

def _version_key(version: str):
    match = re.match(r'(\d+)\.(\d+)(?:\.(\d+))?', version or '')
    if not match:
        return None
    return tuple(int(part) if part is not None else 0 for part in match.groups())

def _version_matches(base_version: str, target_version: str) -> bool:
    base = (base_version or '').split('-')[0]
    target = (target_version or '').split('-')[0]
    if not base or not target:
        return False
    return base == target

def get_patch_catalog(patches_list, target_package: str):
    """Group a package's patches into 'old', 'newx' and 'common' lists.

    'common' holds patches that apply to both UI families (patches without a
    category whose target versions span both families).
    """
    if not patches_list:
        return None

    entries = []
    for patch in patches_list.get("patches", []):
        versions = set()
        applies = False
        for package in (patch.get("compatiblePackages") or []):
            if package.get("packageName") != target_package:
                continue
            applies = True
            for target in (package.get("targets") or []):
                if target.get("version"):
                    versions.add(target["version"])
        if applies:
            entries.append({
                "name": patch.get("name") or "",
                "category": patch.get("category"),
                "versions": versions,
            })

    if not entries:
        return None

    old_versions, newx_versions = set(), set()
    for entry in entries:
        if entry["category"] == OLD_CATEGORY:
            old_versions |= entry["versions"]
        elif entry["category"] == NEWX_CATEGORY:
            newx_versions |= entry["versions"]
    has_categories = bool(old_versions or newx_versions)

    catalog = {
        "old": [], "newx": [], "common": [],
        "versions": {},
        "family_versions": {"old": set(), "newx": set()},
    }

    for entry in entries:
        name, category, versions = entry["name"], entry["category"], entry["versions"]
        if category == NEWX_CATEGORY or name.startswith(NEWX_NAME_PREFIX):
            family = "newx"
        elif category == OLD_CATEGORY:
            family = "old"
        elif has_categories and (versions & old_versions) and (versions & newx_versions):
            family = "common"
        elif has_categories and (versions & newx_versions) and not (versions & old_versions):
            family = "newx"
        else:
            family = "old"

        catalog[family].append(name)
        catalog["versions"][name] = versions
        if family != "common":
            catalog["family_versions"][family] |= versions

    return catalog

def get_patch_family_for_version(catalog, version: str) -> str:
    """Return the patch family ('old' or 'newx') matching a base APK version."""
    if not catalog:
        return "old"

    matches = [
        family for family in PATCH_FAMILIES
        if any(_version_matches(version, v) for v in catalog["family_versions"][family])
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return "newx"

    # No exact match: anything older than the oldest NewX target stays Old,
    # anything newer is treated as NewX.
    base_key = _version_key(version)
    newx_keys = [k for k in (_version_key(v) for v in catalog["family_versions"]["newx"]) if k]
    if base_key and newx_keys and base_key >= min(newx_keys):
        return "newx"
    return "old"

def get_family_patches(catalog, family: str) -> list:
    """Patches to apply for a family, including the shared (common) ones."""
    if not catalog:
        return []
    return list(catalog.get(family, [])) + list(catalog.get("common", []))

def is_family_compatible(catalog, family: str, version: str) -> bool:
    """True when the version is one of the family's declared target versions."""
    if not catalog:
        return False
    return any(_version_matches(version, v) for v in catalog["family_versions"][family])

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