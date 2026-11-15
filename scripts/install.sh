#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
project_root="$(cd -- "$script_dir/.." && pwd)"
environment_path="$project_root/.writune-venv"
python_command=""
use_uv=false
with_dev=false
while (($#)); do
    case "$1" in
        --uv) use_uv=true ;;
        --dev) with_dev=true ;;
        --venv) environment_path="${2:?--venv needs a new absolute directory}"; shift ;;
        --python) python_command="${2:?--python needs an interpreter path}"; shift ;;
        -h|--help)
            printf '%s\n' 'Usage: bash scripts/install.sh [--uv] [--dev] [--venv PATH] [--python PATH]'
            exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
    esac
    shift
done
[[ "$environment_path" = /* ]] || { printf '%s\n' '--venv must be an absolute path.' >&2; exit 2; }
owner_path="$environment_path/.writune-owner"
venv_python="$environment_path/bin/python"
if [[ -e "$environment_path" ]]; then
    if [[ ! -f "$owner_path" ]] || [[ "$(<"$owner_path")" != "$project_root" ]]; then
        printf 'Refusing to modify an existing unowned environment: %s. Choose a new --venv.\n' "$environment_path" >&2
        exit 1
    fi
    [[ -x "$venv_python" ]] || { printf '%s\n' 'The Writune environment is incomplete; choose a new --venv.' >&2; exit 1; }
fi
# Settings apply to this process, never to shell profiles.
export PYTHONNOUSERSITE=1 PIP_REQUIRE_VIRTUALENV=true
export UV_PYTHON_INSTALL_DIR="$project_root/.writune-runtime/python"
export UV_PYTHON_BIN_DIR="$project_root/.writune-runtime/bin"
export UV_CACHE_DIR="${TMPDIR:-/tmp}/writune-uv-cache"
export UV_PYTHON_INSTALL_REGISTRY=false UV_PYTHON_NO_REGISTRY=true
export UV_PYTHON_INSTALL_BIN=false UV_NO_MODIFY_PATH=true
package_spec="$project_root"
[[ "$with_dev" == false ]] || package_spec+='[dev]'
if [[ "$use_uv" == true ]]; then
    command -v uv >/dev/null 2>&1 || { printf '%s\n' 'uv was not found; provide a standalone uv executable on this process PATH or use Python 3.11+.' >&2; exit 1; }
    if [[ ! -x "$venv_python" ]]; then
        uv --no-config venv --managed-python --python 3.12 "$environment_path"
        printf '%s\n' "$project_root" > "$owner_path"
    fi
    uv --no-config pip install --python "$venv_python" --editable "$package_spec"
else
    if [[ ! -x "$venv_python" ]]; then
        if [[ -z "$python_command" ]]; then
            python_command=python3
            command -v python3 >/dev/null 2>&1 || python_command=python
        fi
        "$python_command" -I -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)'
        "$python_command" -I -m venv "$environment_path"
        printf '%s\n' "$project_root" > "$owner_path"
    fi
    "$venv_python" -I -c 'import sys; assert sys.prefix != sys.base_prefix, "An isolated venv is required"'
    "$venv_python" -I -m pip --isolated install --editable "$package_spec"
fi
printf 'Installed in: %s\nInitialize: %s -m writune init\n' "$environment_path" "$venv_python"
printf '%s\n' 'Existing Python installations, environments, PATH and registry are unchanged.'
