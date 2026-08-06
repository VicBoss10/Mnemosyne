"""Empaqueta la API de Python como ejecutable para la app de escritorio.

    python desktop/build_api.py

Deja el resultado en `desktop/binaries/`, que es donde la capa nativa lo busca
tanto en desarrollo como al armar el instalador.

Se usa `--onedir` y no `--onefile`: este último se descomprime en un temporal en
cada arranque, lo que suma segundos y, en Windows, choca con los antivirus.
"""

import shutil
import subprocess
import sys
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parent
REPO_ROOT = DESKTOP_DIR.parent
OUTPUT_DIR = DESKTOP_DIR / "binaries"
BUILD_DIR = DESKTOP_DIR / ".build"

#: Dependencias que PyInstaller no descubre siguiendo los imports.
#: qdrant-client carga su capa gRPC dinámicamente, y uvicorn resuelve sus
#: protocolos por nombre en tiempo de ejecución.
COLLECT_ALL = ["qdrant_client", "pypdf", "docx"]
HIDDEN_IMPORTS = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]


def main() -> int:
    if not (REPO_ROOT / "api" / "main.py").is_file():
        print(f"error: no se encontró la API en {REPO_ROOT}", file=sys.stderr)
        return 1

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print(
            "error: falta PyInstaller. Instalalo con:\n"
            "    pip install pyinstaller",
            file=sys.stderr,
        )
        return 1

    # Una compilación anterior dejaría binarios viejos conviviendo con los nuevos.
    shutil.rmtree(OUTPUT_DIR, ignore_errors=True)

    static_dir = REPO_ROOT / "api" / "static"
    separator = ";" if sys.platform == "win32" else ":"

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onedir",
        "--noconfirm",
        "--clean",
        "--name",
        "mnemosyne-api",
        "--distpath",
        str(OUTPUT_DIR),
        "--workpath",
        str(BUILD_DIR / "work"),
        "--specpath",
        str(BUILD_DIR),
        "--paths",
        str(REPO_ROOT),
        # La interfaz web se sirve desde el ejecutable, así que viaja adentro.
        "--add-data",
        f"{static_dir}{separator}api/static",
        # Plantilla de configuración: en el primer arranque se copia al
        # directorio de datos del usuario, que es donde queda editable.
        "--add-data",
        f"{REPO_ROOT / 'config.yaml'}{separator}.",
    ]
    for package in COLLECT_ALL:
        command += ["--collect-all", package]
    for module in HIDDEN_IMPORTS:
        command += ["--hidden-import", module]
    command.append(str(DESKTOP_DIR / "api_entry.py"))

    print("Empaquetando la API con PyInstaller ...\n")
    result = subprocess.run(command, cwd=REPO_ROOT)
    if result.returncode != 0:
        print("\nerror: falló el empaquetado", file=sys.stderr)
        return result.returncode

    # PyInstaller anida el resultado en un subdirectorio con el nombre del
    # programa; la capa nativa lo espera directamente en binaries/.
    nested = OUTPUT_DIR / "mnemosyne-api"
    executable = nested / ("mnemosyne-api.exe" if sys.platform == "win32" else "mnemosyne-api")
    if not executable.is_file():
        print(f"error: no se generó el ejecutable en {executable}", file=sys.stderr)
        return 1

    size_mb = sum(f.stat().st_size for f in nested.rglob("*") if f.is_file()) / 1e6
    print(f"\nListo: {executable}  ({size_mb:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
