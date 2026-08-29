#!/bin/zsh
set -euo pipefail

repo_root="${0:A:h:h}"
data_root="$HOME/Library/Application Support/MacBot"
build_root="$data_root/build"
target="$data_root/runtime"
backup_root="$data_root/backups"
runtime_root="$data_root/runtimes"
wheel_root="$build_root/wheel"
requirements="$build_root/runtime-requirements.txt"
stamp="$(date +%Y%m%d-%H%M%S)"
stage="$runtime_root/2.1.0-$stamp"

mkdir -p "$build_root" "$backup_root" "$runtime_root" "$wheel_root"
chmod 700 "$data_root" "$build_root" "$backup_root" "$runtime_root"

cd "$repo_root"
uv build --wheel --out-dir "$wheel_root"
wheel=("$wheel_root"/macbot-*.whl)
if (( ${#wheel} != 1 )); then
  echo "Expected exactly one MacBot wheel in $wheel_root" >&2
  exit 1
fi
uv export --frozen --all-extras --no-dev --no-emit-project --output-file "$requirements" >/dev/null

python3 - "$stage" <<'PY'
import pathlib, shutil, sys
stage = pathlib.Path(sys.argv[1])
if stage.exists():
    shutil.rmtree(stage)
PY
uv venv --python 3.12 "$stage"
uv pip install --python "$stage/bin/python" --requirement "$requirements"
uv pip install --python "$stage/bin/python" --no-deps "${wheel[1]}"

version="$($stage/bin/python -c 'import importlib.metadata; print(importlib.metadata.version("macbot"))')"
if [[ "$version" != "2.1.0" ]]; then
  echo "Installed runtime has unexpected MacBot version: $version" >&2
  exit 1
fi

"$stage/bin/macbot" --help >/dev/null

if [[ -d "$target" && ! -L "$target" ]]; then
  backup="$backup_root/runtime-$stamp"
  mv "$target" "$backup"
  echo "Previous runtime preserved at $backup"
fi
python3 - "$stage" "$target" <<'PY'
import os, pathlib, sys
stage, target = map(pathlib.Path, sys.argv[1:])
link = target.with_name("runtime.next")
link.unlink(missing_ok=True)
link.symlink_to(stage)
os.replace(link, target)
PY
chmod -R go-rwx "$stage"
"$target/bin/macbot" --help >/dev/null
echo "$target"
