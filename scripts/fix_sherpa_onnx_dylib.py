"""Workaround for a broken sherpa-onnx wheel on macOS arm64 (see docs/decisions/
0004-diarization.md): its compiled extension expects a bundled libonnxruntime.dylib
that the PyPI release doesn't actually include, so `import sherpa_onnx` fails outright. Copies
the onnxruntime package's own dylib into the path sherpa_onnx's extension looks for.

Run once after `uv sync` installs/updates dependencies (CI and packaging/build.sh both run this
too) -- `uv run python scripts/fix_sherpa_onnx_dylib.py`. A no-op if sherpa_onnx already imports
cleanly, so it's safe to run unconditionally.
"""

import importlib.util
import shutil
import sys
from pathlib import Path


def main() -> None:
    try:
        import sherpa_onnx  # noqa: F401

        print("sherpa_onnx already imports cleanly; nothing to do.")
        return
    except ImportError:
        pass

    import onnxruntime

    onnxruntime_dir = Path(onnxruntime.__file__).resolve().parent / "capi"
    dylibs = sorted(onnxruntime_dir.glob("libonnxruntime.*.dylib"))
    if not dylibs:
        print(f"no onnxruntime dylib found under {onnxruntime_dir}", file=sys.stderr)
        raise SystemExit(1)
    source = dylibs[-1]

    # find_spec locates the package without executing its (currently-broken) __init__.py.
    spec = importlib.util.find_spec("sherpa_onnx")
    if spec is None or not spec.submodule_search_locations:
        print("sherpa_onnx is not installed", file=sys.stderr)
        raise SystemExit(1)
    sherpa_onnx_dir = Path(next(iter(spec.submodule_search_locations)))
    dest = sherpa_onnx_dir / "lib" / "libonnxruntime.dylib"
    shutil.copy2(source, dest)
    print(f"copied {source} -> {dest}")

    import sherpa_onnx  # noqa: F401

    print("sherpa_onnx now imports cleanly.")


if __name__ == "__main__":
    main()
