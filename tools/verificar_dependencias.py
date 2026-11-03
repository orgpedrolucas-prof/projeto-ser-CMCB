"""SER: confirma bibliotecas e Pillow 13+ antes de abrir o navegador."""
import sys
try:
    import fastapi, uvicorn, jinja2, multipart, itsdangerous, reportlab, openpyxl, PIL
    major = int(PIL.__version__.split(".", 1)[0])
    if major < 13 or major >= 14:
        raise RuntimeError("Pillow 13 e necessario para Python 3.15")
except (ImportError, AttributeError, ValueError, RuntimeError) as exc:
    print(f"Bibliotecas incompletas: {exc}")
    sys.exit(1)
