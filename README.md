# Twitter/X & Instagram APK (Piko Patches) - Local Builder

[![Stable Release](https://img.shields.io/github/v/release/monsivamon/twitter-apk?label=Stable&color=success)](https://github.com/monsivamon/twitter-apk/releases/latest)
[![Pre-Release](https://img.shields.io/github/v/release/monsivamon/twitter-apk?include_prereleases&label=Pre-Release&color=orange)](https://github.com/monsivamon/twitter-apk/releases)

A Python-based local build system for applying [Piko](https://github.com/crimera/piko) patches to Twitter/X and Instagram. 
Due to upstream tooling changes and stability requirements, this project has transitioned from an automated CI pipeline to a **reliable manual/local builder**.

## 📦 About Releases
APKs available on the Releases page of this repository are manually updated and uploaded by the author (monsivamon) by running `main_manual.py` on their local machine. Automated updates via GitHub Actions (CI) are currently suspended.

## 📂 Project Structure

The build system has been refactored into modular components for maintainability:

* `main_manual.py` - The main entry point and control flow (interactive menu).
* `env_setup.py` - Environment variable setup, logger initialization, and Java/PATH configuration.
* `build_utils.py` - Core logic for patching, version comparison, and APK extraction.
* `gh_utils.py` - GitHub CLI wrapper for fetching releases and uploading assets.
* `download_bins.py` - Utility to download required binaries (Morphe CLI, APKEditor).
* `utils.py` - General helper functions (e.g., APK merging).

## 🛠️ How to Build Locally (and Upload to Your Repo)

If you want to apply patches and build on your own local machine, and **upload the release to your own repository**, please follow the steps below.

### Prerequisites
* **Windows OS** (This script is intended and optimized for Windows environments).
* **Python 3.x** installed.
* **Java (JDK 17 or 21)** installed and added to your system's `PATH`.
* **GitHub CLI (`gh`)** installed and authenticated (`gh auth login`) with **your own account**.
* A valid **PKCS12 format** keystore file named **`ks_pkcs12.keystore`** placed in the root directory.
* **`apksigner`** (from Android SDK Build-Tools) installed and accessible in your `PATH` (used for reliably signing massive APKs like Instagram).

### ⚠️ Important Note (Script Modification)
Even if you are authenticated with the GitHub CLI, if the repository settings in the script remain as `monsivamon/twitter-apk`, the release upload will fail with a permission error.
**Before running the build, you MUST change the repository name in `gh_utils.py` (e.g., `GITHUB_REPO = "monsivamon/twitter-apk"`) to your own repository name.**

### Usage
1. Fork this repository to your own account, or clone it locally.
2. **Modify the repository settings in the script to your own, as described in the "Important Note" above.** *(Change `GITHUB_REPO` in `gh_utils.py`)*
3. Create a folder named `.base_apk` in the root directory.
4. Place your unpatched Twitter/X or Instagram APKs (or `.apkm` bundles) inside the `.base_apk` folder.
5. Open Command Prompt or PowerShell and run the builder script:

```cmd
python main_manual.py
```

6. **Choose your desired execution mode from the interactive menu (e.g., `[1] Full Build`, `[4] Upload Only`).**
7. Choose your target branch (Stable/Pre-release) when prompted. The script will automatically handle merging, shim application (if necessary), patching, signing (using `apksigner` and your `ks_pkcs12.keystore`), and creating/uploading the release to your own repository.

## 📥 Download

If you just want the pre-built APKs, choose the version that best suits your needs:

* **[🌟 Stable Version (Recommended)](https://github.com/monsivamon/twitter-apk/releases/latest)**
    * Built with Piko's `main` branch. Safe and stable for daily use.
* **[🧪 Pre-release Version (Beta)](https://github.com/monsivamon/twitter-apk/releases)**
    * Built with Piko's `dev` branch. Try out the newest experimental features! *(Look for the `Pre-release` label in the list).*

## Credits

* [crimera/piko](https://github.com/crimera/piko) - The patch source.
* [inotia00/x-shim](https://gitlab.com/inotia00/x-shim) - Piko compatibility shim for newer X versions.
* [MorpheApp/morphe-cli](https://github.com/MorpheApp/morphe-cli) - Morphe CLI patcher.
* [REAndroid/APKEditor](https://github.com/REAndroid/APKEditor) - APK merging tool.
* [Android SDK build-tools (apksigner)](https://developer.android.com/studio/command-line/apksigner) - Reliable APK signing tool for massive files.
