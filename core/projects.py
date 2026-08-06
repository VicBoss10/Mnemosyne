"""Registro de los proyectos: qué corpus conoce el motor y dónde vive cada uno.

Un proyecto es una carpeta de documentos más su índice. El aislamiento ya lo da
el vector store —cada proyecto tiene su propia colección, ver
`Settings.collection_name`— así que esto no separa nada por sí mismo: solo
recuerda cuáles existen, para poder listarlos y cambiar de uno a otro sin
reiniciar.

Se guarda como un JSON en el directorio de datos y no en el vector store, aunque
allí ya estén las colecciones: hace falta conocer la carpeta de origen y la fecha
de la última indexación, que Qdrant no almacena, y un archivo legible permite
recuperar el registro a mano si se corrompe.
"""

import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

#: Nombre del archivo dentro del directorio de datos.
REGISTRY_FILE = "projects.json"

#: Caracteres admitidos en el identificador. El nombre da nombre a la colección
#: de Qdrant, que solo acepta este juego.
SLUG_PATTERN = re.compile(r"[^a-z0-9_-]+")


@dataclass
class Project:
    """Un corpus registrado."""

    #: Identificador estable: nombra la colección y no cambia al renombrar.
    slug: str
    #: Nombre visible, elegido por la persona.
    name: str
    #: Carpeta de documentos. Absoluta: el registro no depende del directorio
    #: de trabajo de quien lo lea.
    docs_path: str
    #: Cuándo se indexó por última vez, en ISO 8601 UTC. None si nunca.
    indexed_at: str | None = None
    #: Resultado de esa indexación, para mostrarlo sin consultar a Qdrant.
    documents: int = 0
    chunks: int = 0
    #: Última vez que se abrió. Ordena el listado: lo último usado primero.
    opened_at: str | None = None

    @property
    def path(self) -> Path:
        return Path(self.docs_path)

    @property
    def exists(self) -> bool:
        """Si la carpeta sigue estando donde el registro dice.

        Puede haberse movido o borrado desde fuera de la app. El chat sigue
        funcionando —el índice vive en el vector store, no en la carpeta— pero
        no se puede volver a indexar, así que conviene avisar antes de que la
        acción falle.
        """
        return self.path.is_dir()


def slugify(name: str) -> str:
    """Convierte un nombre visible en un identificador válido para Qdrant.

    Solo minúsculas, dígitos, guion y guion bajo: es lo que admite el nombre de
    una colección.
    """
    slug = SLUG_PATTERN.sub("-", name.strip().lower()).strip("-")
    return slug or "proyecto"


class ProjectRegistry:
    """Los proyectos conocidos, persistidos en un JSON.

    Cada operación relee y reescribe el archivo. Es más lento que mantenerlo en
    memoria, pero el registro es diminuto y así dos procesos —la app y una CLI
    abierta a la vez— no se pisan tan fácil.
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / REGISTRY_FILE

    def all(self) -> list[Project]:
        """Los proyectos, del usado más recientemente al más antiguo.

        Ese orden es el que espera quien abre la app: lo que estaba haciendo
        ayer arriba, sin tener que buscarlo.
        """
        if not self.path.is_file():
            return []

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            # Un registro ilegible no debe impedir arrancar: se avisa y se
            # sigue con la lista vacía, que el usuario puede volver a poblar.
            logger.warning("No se pudo leer %s: %s", self.path, exc)
            return []

        projects = [Project(**item) for item in raw.get("projects", [])]
        # Los que nunca se abrieron van al final, no al principio.
        return sorted(projects, key=lambda p: p.opened_at or "", reverse=True)

    def get(self, slug: str) -> Project | None:
        return next((p for p in self.all() if p.slug == slug), None)

    def create(self, name: str, docs_path: Path) -> Project:
        """Registra un proyecto nuevo. No indexa nada: eso es un paso aparte."""
        docs_path = Path(docs_path).expanduser().resolve()
        if not docs_path.is_dir():
            raise ValueError(f"La carpeta no existe: {docs_path}")

        existing = {p.slug for p in self.all()}
        slug = base = slugify(name)
        # Dos proyectos pueden llamarse igual de cara al usuario, pero su
        # identificador nombra una colección y tiene que ser único.
        suffix = 2
        while slug in existing:
            slug = f"{base}-{suffix}"
            suffix += 1

        project = Project(slug=slug, name=name.strip(), docs_path=str(docs_path))
        self._save(self.all() + [project])
        logger.info("Proyecto '%s' registrado en %s", slug, docs_path)
        return project

    def update(self, slug: str, **changes: object) -> Project:
        """Modifica los campos indicados de un proyecto."""
        projects = self.all()
        for index, project in enumerate(projects):
            if project.slug != slug:
                continue
            for key, value in changes.items():
                if not hasattr(project, key):
                    raise ValueError(f"Campo desconocido: {key}")
                setattr(project, key, value)
            projects[index] = project
            self._save(projects)
            return project
        raise KeyError(f"No hay ningún proyecto '{slug}'")

    def mark_indexed(self, slug: str, documents: int, chunks: int) -> Project:
        """Anota el resultado de una indexación."""
        return self.update(slug, indexed_at=_now(), documents=documents, chunks=chunks)

    def mark_opened(self, slug: str) -> Project:
        """Anota que se abrió, que es lo que ordena el listado."""
        return self.update(slug, opened_at=_now())

    def delete(self, slug: str) -> None:
        """Quita el proyecto del registro.

        No toca ni los documentos ni el índice: los primeros son del usuario y
        el segundo lo borra quien tenga acceso al vector store.
        """
        projects = [p for p in self.all() if p.slug != slug]
        self._save(projects)
        logger.info("Proyecto '%s' eliminado del registro", slug)

    def _save(self, projects: list[Project]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {"projects": [asdict(p) for p in projects]}
        # Se escribe aparte y se renombra: si el proceso muere a mitad, el
        # registro anterior queda intacto en vez de truncado.
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)


def _now() -> str:
    """Marca de tiempo con microsegundos.

    La precisión importa porque estas marcas ordenan el listado y las
    comparaciones son entre cadenas: dos aperturas dentro del mismo segundo
    quedarían empatadas y el orden lo decidiría el azar.
    """
    return datetime.now(UTC).isoformat()
