"""Точка входа для gunicorn / production WSGI."""
import importlib.util
import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


def _load_main_module():
    """Загружает app.py по явному пути.

    Защита от конфликта имён: если рядом с app.py окажется папка app/
    (лишние файлы на сервере), обычный `import app` подхватил бы пакет app
    и упал с ImportError. Здесь модуль грузится из конкретного файла.
    """
    path = os.path.join(APP_DIR, "app.py")
    spec = importlib.util.spec_from_file_location("auto_price_app", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["auto_price_app"] = module
    spec.loader.exec_module(module)
    return module


_main = _load_main_module()
app = _main.app
ensure_admin = _main.ensure_admin
ensure_runtime_data = _main.ensure_runtime_data
DATA_DIR = _main.DATA_DIR

ensure_runtime_data()
ensure_admin()

if __name__ == "__main__":
    print(f"DATA_DIR={DATA_DIR}")
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "80")),
        debug=False,
    )
