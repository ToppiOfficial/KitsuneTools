#! python3

import zipfile, os, re, fnmatch

EXTRA_IGNORE = [
    ".gitignore",
    "pyrightconfig.json",
    "*.md",
    "download_pillow.py",
    ".git"
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
                patterns.append(line)
    return patterns

def is_ignored(path: str, patterns: list) -> bool:
    name = os.path.basename(path)
    for pattern in patterns:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(path, pattern):
            return True
    return False

toml_path = "blender_manifest.toml"

with open(toml_path) as toml_file:
    content = toml_file.read()
    version_match = re.search(r'^version\s*=\s*[\'"]?([0-9.]+)[\'"]?', content, re.MULTILINE)
    
    if not version_match:
        print("Error: version not found in blender_manifest.toml")
        exit(1)
    
    version_str = version_match.group(1)
    
    wheels_section = re.search(r'wheels\s*=\s*\[(.*?)\]', content, re.DOTALL)
    if not wheels_section:
        print("Error: wheels section not found in blender_manifest.toml")
        exit(1)
    
    wheel_lines = re.findall(r'[\'"](.+?)[\'"]', wheels_section.group(1))

platforms = {
    'win_amd64': 'windows',
    'macosx_11_0_arm64': 'macos_arm',
    'macosx_10_10_x86_64': 'macos_intel',
    'manylinux': 'linux'
}

def get_platform_from_wheel(wheel_path):
    for platform_tag, platform_name in platforms.items():
        if platform_tag in wheel_path:
            return platform_name
    return None

wheel_platform_map = {}
for wheel in wheel_lines:
    platform = get_platform_from_wheel(wheel)
    if platform:
        if platform not in wheel_platform_map:
            wheel_platform_map[platform] = []
        wheel_platform_map[platform].append(wheel)

print(f"Creating platform-specific releases for version {version_str}\n")

script_dir = "."
gitignore_patterns = load_gitignore_patterns(script_dir)
all_patterns = gitignore_patterns + EXTRA_IGNORE
this_script = os.path.basename(__file__)

for platform_name, platform_wheels in wheel_platform_map.items():
    zip_name = f"../kitsunetools_{version_str}_{platform_name}.zip"
    print(f"Creating {zip_name}...")
    
    zip_file = zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_BZIP2)
    
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

            if file_path.endswith(".whl"):
                should_include = False
                for platform_wheel in platform_wheels:
                    if file_path.endswith(os.path.basename(platform_wheel)):
                        should_include = True
                        break
                if not should_include:
                    continue
            
            zip_file.write(file_path, relative_path)
    
    zip_file.close()
    zip_size = os.path.getsize(zip_name) / (1024 * 1024)
    print(f"  {zip_name} ({zip_size:.2f} MB)")

print(f"\nAll platform releases created in ../")