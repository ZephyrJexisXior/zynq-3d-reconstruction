"""Preflight desktop/receiver dependencies without CUDA loading or hardware IO."""
import importlib
import json
import sys

REQUIRED = ('numpy', 'cv2', 'PIL.Image', 'serial', 'tkinter')


def inspect_runtime(importer=importlib.import_module):
    loaded, errors = {}, {}
    for name in REQUIRED:
        try:
            module = importer(name)
            loaded[name] = getattr(module, '__file__', None)
        except Exception as exc:
            errors[name] = str(exc)
    return dict(ok=not errors, python=sys.executable, prefix=sys.prefix,
                isolated=bool(sys.flags.isolated), modules=loaded, errors=errors)


if __name__ == '__main__':
    result = inspect_runtime()
    print(json.dumps(result, ensure_ascii=True))
    raise SystemExit(0 if result['ok'] else 1)
