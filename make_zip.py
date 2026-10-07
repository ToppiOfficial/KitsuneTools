#! python3

import zipfile, os, re, fnmatch

EXTRA_IGNORE = [
    ".gitignore",
    "pyrightconfig.json",
    "*.md",
    ".git",
    ".github"
]

def load_gitignore_patterns(base_dir: str) -> list:
    gitignore_path = os.path.join(base_dir, ".gitignore")
    if not os.path.exists(gitignore_path):
        return []

    patterns = []
    with open(gitignore_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                patterns.append(line.rstrip("/"))
    return patterns

def is_ignored(path: str, patterns: list) -> bool:
    name = os.path.basename(path)
    for pattern in patterns:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(path, pattern):
            return True
    return False

toml_path = "blender_manifest.toml"

with open(toml_path) as toml_file:
    version_match = re.search(r'^version\s*=\s*[\'"]?([0-9.]+)[\'"]?', toml_file.read(), re.MULTILINE)

    if not version_match:
        print("Error: version not found in blender_manifest.toml")
        exit(1)

    version_str = version_match.group(1)

script_dir = "."
all_patterns = load_gitignore_patterns(script_dir) + EXTRA_IGNORE
this_script = os.path.basename(__file__)

zip_name = f"../kitsunetools_{version_str}.zip"
print(f"Creating {zip_name}...")

with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_BZIP2) as zip_file:
    for path, dirnames, filenames in os.walk(script_dir):
        dirnames[:] = [d for d in dirnames if not is_ignored(d, all_patterns)]

        if path.endswith("__pycache__"):
            continue

        for f in filenames:
            file_path = os.path.join(path, f)
            relative_path = os.path.relpath(file_path, script_dir)

            if f == this_script or f == "make_zip.py":
                continue

            if is_ignored(f, all_patterns) or is_ignored(relative_path, all_patterns):
                continue

            zip_file.write(file_path, relative_path)

zip_size = os.path.getsize(zip_name) / (1024 * 1024)
print(f"  {zip_name} ({zip_size:.2f} MB)")
