#!/bin/zsh
set -euo pipefail

repo_root="${0:A:h:h}"
package_root="$repo_root/native/MacBotApp"
output_root="$repo_root/build/native"
app="$output_root/MacBot.app"
cli="$repo_root/.venv/bin/macbot"

if [[ ! -x "$cli" ]]; then
  echo "MacBot CLI is not installed in the project environment. Run: uv sync --frozen --all-extras" >&2
  exit 1
fi

swift build --package-path "$package_root" -c release
python3 - "$app" <<'PY'
import pathlib, shutil, sys
path = pathlib.Path(sys.argv[1])
if path.exists():
    shutil.rmtree(path)
(path / "Contents/MacOS").mkdir(parents=True)
(path / "Contents/Resources").mkdir(parents=True)
PY
cp "$package_root/.build/release/MacBotApp" "$app/Contents/MacOS/MacBotApp"
python3 - "$package_root/Info.plist.in" "$app/Contents/Info.plist" "$cli" "$repo_root/src" <<'PY'
import pathlib, sys
template, output, cli, source = map(pathlib.Path, sys.argv[1:])
text = template.read_text().replace("__MACBOT_CLI__", str(cli)).replace("__MACBOT_SOURCE__", str(source))
output.write_text(text)
PY
# Cloud-synchronized workspaces can attach Finder metadata while the bundle is
# assembled. Apple codesign rejects those extended attributes.
xattr -cr "$app"
codesign --force --sign - --entitlements "$package_root/MacBot.entitlements" "$app"
codesign --verify --strict --verbose=2 "$app"

if [[ "${1:-}" == "--install" ]]; then
  destination="$HOME/Applications/MacBot.app"
  mkdir -p "$HOME/Applications"
  python3 - "$app" "$destination" <<'PY'
import pathlib, shutil, sys
source, destination = map(pathlib.Path, sys.argv[1:])
if destination.exists():
    shutil.rmtree(destination)
shutil.copytree(source, destination, symlinks=True)
PY
  codesign --verify --strict --verbose=2 "$destination"
  echo "$destination"
else
  echo "$app"
fi
