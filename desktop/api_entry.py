"""Punto de entrada de la API cuando corre dentro de la app de escritorio.

Existe por tres motivos que no aplican al despliegue con Docker:

- El puerto lo elige la app, no la configuración: dos ventanas abiertas a la vez
  chocarían por un puerto fijo, y el 8100 puede estar tomado por el
  `docker compose` del propio proyecto.
- PyInstaller necesita un módulo real como objetivo; no puede empaquetar la
  cadena "api.main:app" que uvicorn resuelve por nombre.
- Dentro del ejecutable empaquetado, `core.config` calcula la raíz del
  repositorio a partir de su propio archivo, que vive en un directorio temporal.
  El `config.yaml` que busca ahí no existe, así que hay que indicárselo.

El motor no se toca: se importa la misma aplicación FastAPI que sirve
`mnemosyne serve`.
"""

import json
import os
import shutil
import sys
from pathlib import Path

#: Nombre del archivo de configuración dentro del directorio de datos.
CONFIG_FILENAME = "config.yaml"


def _bundle_dir() -> Path:
    """Directorio donde PyInstaller extrajo los recursos empaquetados.

    Fuera del ejecutable empaquetado —corriendo desde el repositorio— devuelve
    la raíz del proyecto, para que el entry point sirva también en desarrollo.
    """
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parent.parent


def _prepare_data_dir() -> Path | None:
    """Prepara el directorio donde la app guarda documentos y configuración.

    Lo fija la capa nativa (`MNEMOSYNE_DATA_DIR`) al directorio de datos del
    sistema operativo. En Windows el directorio de instalación es de solo
    lectura para el usuario, así que escribir junto al ejecutable no es opción.

    En el primer arranque copia la configuración empaquetada, que a partir de
    ahí es del usuario: las actualizaciones de la app no la pisan.
    """
    raw = os.environ.get("MNEMOSYNE_DATA_DIR")
    if not raw:
        return None

    data_dir = Path(raw)
    (data_dir / "documents").mkdir(parents=True, exist_ok=True)

    config = data_dir / CONFIG_FILENAME
    if not config.exists():
        template = _bundle_dir() / CONFIG_FILENAME
        if template.is_file():
            shutil.copyfile(template, config)

    return data_dir


def _export_config(config: Path, data_dir: Path) -> None:
    """Traduce el YAML del usuario a variables de entorno.

    `core.config` localiza su YAML a partir de la ubicación de su propio módulo,
    que dentro del ejecutable empaquetado es un directorio temporal sin
    `config.yaml`. En vez de modificar el motor para que acepte una ruta —esta
    capa no debe cambiarlo— se leen los valores acá y se exportan con el prefijo
    y el delimitador que pydantic ya entiende (`MNEMOSYNE_`, `__`), que además
    tienen precedencia sobre el YAML.

    Solo se exporta lo que el usuario fijó: lo ausente conserva su valor por
    defecto en el motor.
    """
    import yaml

    data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}

    for section, values in data.items():
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            if value is None:
                continue
            name = f"MNEMOSYNE_{section.upper()}__{key.upper()}"
            # Las listas y los diccionarios viajan como JSON: es el formato que
            # pydantic-settings espera para un campo complejo.
            if isinstance(value, (list, dict)):
                os.environ.setdefault(name, json.dumps(value, ensure_ascii=False))
            else:
                os.environ.setdefault(name, str(value))

    # La carpeta de documentos por defecto es la del directorio de datos, pero
    # solo si el usuario no eligió otra en su YAML — de ahí el setdefault de
    # arriba, que ya la habría fijado.
    docs = os.environ.get("MNEMOSYNE_PROJECT__DOCS_PATH")
    if not docs or not Path(docs).is_absolute():
        os.environ["MNEMOSYNE_PROJECT__DOCS_PATH"] = str(data_dir / "documents")


def main() -> None:
    import uvicorn

    data_dir = _prepare_data_dir()
    if data_dir is not None:
        config = data_dir / CONFIG_FILENAME
        if config.is_file():
            _export_config(config, data_dir)

    host = os.environ.get("MNEMOSYNE_HOST", "127.0.0.1")
    port = int(os.environ.get("MNEMOSYNE_PORT", "8100"))

    # Importado acá, después de fijar el entorno: core.config cachea los ajustes
    # en el primer acceso.
    from api.main import app

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    sys.exit(main())
