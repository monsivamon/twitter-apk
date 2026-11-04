import os
import re
import sys
import glob
import shutil
import subprocess

APKSIGNER_PATH = None

class DualLogger:
    def __init__(self, filename="build_log.txt"):
        self.terminal = sys.stdout
        self.log = open(filename, "w", encoding="utf-8")
        self.cwd = os.getcwd()
        self.home = os.path.expanduser("~")
        
    def sanitize(self, text):
        if not isinstance(text, str):
            return text
        cwd_b = self.cwd
        cwd_f = self.cwd.replace("\\", "/")
        home_b = self.home
        home_f = self.home.replace("\\", "/")

        if os.name == 'nt':
            text = re.sub(re.escape(cwd_b), ".", text, flags=re.IGNORECASE)
            text = re.sub(re.escape(cwd_f), ".", text, flags=re.IGNORECASE)
            text = re.sub(re.escape(home_b), "~", text, flags=re.IGNORECASE)
            text = re.sub(re.escape(home_f), "~", text, flags=re.IGNORECASE)
        else:
            text = text.replace(cwd_b, ".").replace(cwd_f, ".")
            text = text.replace(home_b, "~").replace(home_f, "~")
        return text

    def write(self, message):
        clean_message = self.sanitize(message)
        self.terminal.write(clean_message)
        self.log.write(clean_message)
        self.log.flush()

    def flush(self):
        self.terminal.flush()
        self.log.flush()

def setup_environment():
    # Initialize the log file if it exists
    if os.path.exists("build_log.txt"):
        try: os.remove("build_log.txt")
        except Exception: pass
    
    sys.stdout = DualLogger()
    sys.stderr = sys.stdout

    os.environ["_JAVA_OPTIONS"] = "-Xmx4g"

    # Check for Java path
    java_home = os.environ.get("JAVA_HOME")
    if java_home and not os.path.exists(java_home):
        print(f"  -> [WARNING] Invalid JAVA_HOME detected: {java_home}")
        print(f"  -> [WARNING] Unsetting JAVA_HOME for this session to prevent apksigner crashes.")
        del os.environ["JAVA_HOME"]

    if not shutil.which("java") and os.name == 'nt':
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
                break
        else:
            print("  -> [WARNING] Could not automatically find Java in standard directories.")

    # Check for apksigner path
    global APKSIGNER_PATH
    existing = shutil.which("apksigner")
    if existing:
        APKSIGNER_PATH = existing
        print(f"  -> [DEBUG] apksigner found in system PATH: {existing}")
    elif os.name == 'nt':
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        sdk_path = os.path.join(local_appdata, "Android", "Sdk", "build-tools")
        if os.path.isdir(sdk_path):
            versions = sorted(os.listdir(sdk_path), reverse=True)
            for ver in versions:
                apk_path = os.path.join(sdk_path, ver, "apksigner.bat")
                if os.path.exists(apk_path):
                    APKSIGNER_PATH = apk_path
                    print(f"  -> [INFO] Found apksigner at {apk_path}")
                    break
    
    if not APKSIGNER_PATH:
        print("  -> [ERROR] 'apksigner' not found. Please install Android SDK Build Tools and add it to PATH.")
        sys.exit(1)

    # Patch subprocess.run encoding issues
    original_run = subprocess.run
    def patched_run(*args, **kwargs):
        if kwargs.get("text") is True or kwargs.get("capture_output") is True:
            kwargs["encoding"] = "utf-8"
            kwargs["errors"] = "replace"
            kwargs["text"] = True
        return original_run(*args, **kwargs)
    subprocess.run = patched_run

def get_apksigner_path():
    return APKSIGNER_PATH
