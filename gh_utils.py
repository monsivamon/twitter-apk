import os
import json
import subprocess
import pathlib

GITHUB_REPO = "monsivamon/twitter-apk"

def check_github_repo_access(repo_name):
    print("\n[PRE-CHECK] Verifying GitHub repository access...")
    if not repo_name:
        print("  -> [ERROR] GITHUB_REPO is not configured correctly.")
        return False
    if not subprocess.run(["which", "gh"], capture_output=True).returncode == 0 and os.name != 'nt':
        print("  -> [WARNING] GitHub CLI ('gh') is not installed. Uploading will be skipped.")
        return True 
    print(f"  -> Pinging repository: {repo_name} ...")
    res = subprocess.run(["gh", "repo", "view", repo_name], capture_output=True, text=True)
    if res.returncode != 0:
        print(f"  -> [ERROR] Cannot access GitHub repository ({repo_name}).")
        return False
    print("  -> [SUCCESS] Repository access confirmed.")
    return True

def get_recent_github_releases(repo_name: str, limit: int = 10) -> list:
    print(f"  -> Fetching recent releases from {repo_name}...")
    cmd = ["gh", "api", f"repos/{repo_name}/releases?per_page={limit}"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        releases = json.loads(res.stdout)
        return [r.get("tag_name") for r in releases if r.get("tag_name")]
    except Exception as e:
        print(f"  -> [WARNING] Failed to fetch releases from {repo_name}: {e}")
        return []

def upload_to_github_release(piko_tag, file_paths, is_pre, exact_tag=False):
    if not file_paths:
        print("  -> [INFO] No built files to upload.")
        return
    # Check if gh command exists
    if subprocess.run(["where", "gh"] if os.name == 'nt' else ["which", "gh"], capture_output=True).returncode != 0:
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
    
    # Convert to absolute paths with forward slashes to fix Windows backslash issues
    posix_paths = [str(pathlib.Path(p).resolve().as_posix()) for p in file_paths]

    print(f"  -> Attempting to create empty GitHub Release ({tag})...")
    create_cmd = ["gh", "release", "create", tag, "--title", title, "--notes", notes]
    if is_pre: create_cmd.append("--prerelease")
    if GITHUB_REPO: create_cmd.extend(["--repo", GITHUB_REPO])
    
    res = subprocess.run(create_cmd, capture_output=True, text=True)
    if res.returncode != 0 and "already exists" not in res.stderr.lower():
        print(f"  -> [WARNING] Failed to create release: {res.stderr.strip()}")

    print(f"  -> Uploading {len(posix_paths)} asset(s) to Release {tag}...")
    upload_cmd = ["gh", "release", "upload", tag] + posix_paths + ["--clobber"]
    if GITHUB_REPO: upload_cmd.extend(["--repo", GITHUB_REPO])
    
    res_upload = subprocess.run(upload_cmd, capture_output=True, text=True)
    if res_upload.returncode == 0:
        print(f"  -> [SUCCESS] Assets uploaded to Release {tag}.")
    else:
        print(f"  -> [ERROR] Failed to upload assets:\n{res_upload.stderr}")
        print(f"  -> [DEBUG] Command executed: {' '.join(upload_cmd)}")
