#!/bin/zsh
set -euo pipefail

repo_root="${0:A:h:h}"
package_root="$repo_root/native/MacBotApp"
# Signed bundles are assembled outside cloud-synchronized source checkouts.
# File Provider can otherwise reattach Finder metadata between xattr cleanup
# and codesign, invalidating an otherwise identical build.
output_root="$HOME/Library/Caches/MacBot/native-build"
app="$output_root/MacBot.app"
cli="$repo_root/.venv/bin/macbot"
plist_cli="$cli"
source_root="$repo_root/src"
install=false
release_root=""
version="$(python3 - "$repo_root/pyproject.toml" <<'PY'
import pathlib, sys, tomllib
print(tomllib.loads(pathlib.Path(sys.argv[1]).read_text())["project"]["version"])
PY
)"

if [[ "${1:-}" == "--install" ]]; then
  install=true
  generation="$version-$(date +%Y%m%d-%H%M%S)"
  release_root="$HOME/Library/Application Support/MacBot/releases/$generation"
  mkdir -p "$release_root"
  chmod 700 "$release_root"
  # Assemble release artifacts outside cloud-synchronized workspaces so Finder
  # metadata cannot race the strict signing boundary.
  output_root="$release_root/build"
  app="$output_root/MacBot.app"
  runtime_stage="$($repo_root/scripts/install_runtime.sh --stage "$release_root/runtime")"
  cli="$runtime_stage/bin/macbot"
  plist_cli="$HOME/Library/Application Support/MacBot/runtime/bin/macbot"
  source_root=""
fi

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
resource_bundle="$package_root/.build/release/MacBotApp_MacBotApp.bundle"
if [[ ! -d "$resource_bundle" ]]; then
  echo "SwiftPM did not produce the required MacBotApp resource bundle" >&2
  exit 1
fi
cp -R "$resource_bundle" "$app/Contents/Resources/"
python3 - "$app/Contents/Resources/MacBotApp_MacBotApp.bundle/Contents/Resources/task_protocol_v3.json" <<'PY'
import json, pathlib, sys
contract = pathlib.Path(sys.argv[1])
if not contract.is_file():
    raise SystemExit("The assembled app is missing task_protocol_v3.json")
payload = json.loads(contract.read_text())
if payload.get("protocol_version") != 3:
    raise SystemExit("The assembled app does not contain native protocol version 3")
PY
python3 - "$package_root/Info.plist.in" "$app/Contents/Info.plist" "$plist_cli" "$source_root" "$version" <<'PY'
import pathlib, sys
template, output, cli = map(pathlib.Path, sys.argv[1:4])
source, version = sys.argv[4:6]
text = (template.read_text()
    .replace("__MACBOT_CLI__", str(cli))
    .replace("__MACBOT_SOURCE__", source)
    .replace("__MACBOT_VERSION__", version)
    .replace("__MACBOT_BUILD__", version.replace(".", "")))
output.write_text(text)
PY
# Cloud-synchronized workspaces can attach Finder metadata while the bundle is
# assembled. Apple codesign rejects those extended attributes.
xattr -cr "$app"
# Finder may reattach this root attribute while a cloud-backed directory is
# active even after the recursive clear; remove it immediately at the signing boundary.
xattr -d com.apple.FinderInfo "$app" 2>/dev/null || true
codesign --force --sign - --entitlements "$package_root/MacBot.entitlements" "$app"
codesign --verify --strict --verbose=2 "$app"

if $install; then
  destination="$HOME/Applications/MacBot.app"
  mkdir -p "$HOME/Applications"
  staged_app="$release_root/app/MacBot.app"
  python3 - "$app" "$staged_app" "$release_root/release-manifest.json" "$runtime_stage" "$version" "$repo_root" <<'PY'
import hashlib, json, os, pathlib, shutil, subprocess, sys, time
source, destination, manifest, runtime, version, repo = map(pathlib.Path, sys.argv[1:])
destination.parent.mkdir(parents=True, exist_ok=True)
shutil.copytree(source, destination, symlinks=True)

def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

revision = subprocess.run(
    ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
).stdout.strip()
dirty = bool(subprocess.run(
    ["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True
).stdout.strip())
payload = {
    "schema_version": 1,
    "generation": manifest.parent.name,
    "version": version.name,
    "source_revision": revision,
    "source_dirty": dirty,
    "protocol_version": 3,
    "protocol_resource_sha256": digest(
        destination / "Contents/Resources/MacBotApp_MacBotApp.bundle/Contents/Resources/task_protocol_v3.json"
    ),
    "configuration_schema_sha256": digest(repo / "src/macbot/defaults/config.yaml"),
    "created_ns": time.time_ns(),
    "app_executable_sha256": digest(destination / "Contents/MacOS/MacBotApp"),
    "runtime_python_sha256": digest(runtime / "bin/python3"),
}
selection = json.loads((repo / "src/macbot/defaults/release_models.json").read_text())
catalog = json.loads((repo / "src/macbot/defaults/models.json").read_text())
payload["selected_models"] = {
    role: {
        "artifact": entries[0]["artifact"],
        "revision": catalog[entries[0]["artifact"]]["revision"],
        "files": {
            item["name"]: item["sha256"]
            for item in catalog[entries[0]["artifact"]]["files"]
        },
    }
    for role, entries in selection["roles"].items()
}
manifest.write_text(json.dumps(payload, indent=2) + "\n")
os.chmod(manifest, 0o600)
PY
  codesign --verify --strict --verbose=2 "$staged_app"
  "$runtime_stage/bin/macbot" --help >/dev/null
  data_root="$HOME/Library/Application Support/MacBot"
  stable_cli="$data_root/runtime/bin/macbot"
  current_pointer="$data_root/current"
  rollback_pointer="$data_root/rollback"
  activation_receipt="$release_root/activation-receipt.json"
  was_running=false
  app_was_running=false
  activated=false

  runtime_ready() {
    "$stable_cli" status 2>/dev/null | python3 -c 'import json,sys; raise SystemExit(0 if json.load(sys.stdin).get("ready") else 1)' 2>/dev/null
  }

  rollback_activation() {
    activation_exit=$?
    trap - ERR
    set +e
    if $app_was_running && pgrep -x MacBotApp >/dev/null 2>&1; then
      osascript -e 'tell application id "local.macbot.app" to quit' >/dev/null 2>&1
      for _ in {1..100}; do
        pgrep -x MacBotApp >/dev/null 2>&1 || break
        sleep 0.1
      done
    fi
    if $activated; then
      "$stable_cli" stop >/dev/null 2>&1
      "$runtime_stage/bin/python3" -m macbot.release_activation restore \
        "$activation_receipt" "$current_pointer" "$rollback_pointer" \
        --data-dir "$data_root"
      restore_exit=$?
      if [[ "$restore_exit" -ne 0 ]]; then
        echo "MacBot activation failed and rollback was blocked; inspect current and rollback pointers before starting MacBot" >&2
        exit "$restore_exit"
      fi
    fi
    if $app_was_running; then
      open "$destination" >/dev/null 2>&1
    elif $was_running && ! runtime_ready; then
      "$stable_cli" start --background >/dev/null 2>&1
    fi
    echo "MacBot activation failed; the previous paired generation was restored" >&2
    exit "$activation_exit"
  }
  trap rollback_activation ERR

  if [[ -x "$stable_cli" ]] && "$stable_cli" status >/dev/null 2>&1; then
    was_running=true
  fi
  if pgrep -x MacBotApp >/dev/null 2>&1; then
    app_was_running=true
    osascript -e 'tell application id "local.macbot.app" to quit' >/dev/null
    for _ in {1..100}; do
      pgrep -x MacBotApp >/dev/null 2>&1 || break
      sleep 0.1
    done
    if pgrep -x MacBotApp >/dev/null 2>&1; then
      echo "MacBot.app did not quit before release activation" >&2
      false
    fi
  fi
  if $was_running && "$stable_cli" status >/dev/null 2>&1; then
    "$stable_cli" stop >/dev/null
  fi

  "$runtime_stage/bin/python3" -m macbot.release_activation activate \
    "$release_root" "$destination" "$data_root/runtime" \
    "$current_pointer" "$rollback_pointer" --data-dir "$data_root" \
    > "$activation_receipt"
  activated=true
  codesign --verify --strict --verbose=2 "$destination"
  "$stable_cli" --help >/dev/null
  if $app_was_running; then
    open "$destination"
    for _ in {1..600}; do
      runtime_ready && break
      sleep 0.25
    done
    runtime_ready
  elif $was_running; then
    for _ in {1..20}; do
      runtime_ready && break
      sleep 0.25
    done
    if ! runtime_ready; then
      "$stable_cli" start --background >/dev/null
    fi
    for _ in {1..600}; do
      runtime_ready && break
      sleep 0.25
    done
    runtime_ready
  fi
  trap - ERR
  echo "$destination"
else
  echo "$app"
fi
