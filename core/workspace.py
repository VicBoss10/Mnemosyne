"""Acceso a los proyectos en caliente: un pipeline por corpus, sin reiniciar.

El motor está construido alrededor de un único proyecto: `Settings` nombra uno,
`Pipeline` abre sus conexiones y la colección sale de ahí. Eso basta para la CLI
—un comando, un proyecto— pero no para una app donde se pasa de un corpus a otro
con un clic.

Este módulo mantiene un pipeline por proyecto y los reutiliza. No sustituye a
`Pipeline`, lo administra: cada uno sigue siendo el mismo objeto de siempre, con
su propia colección y sus propias conexiones.
"""

import logging
import threading
from pathlib import Path

from core.config import Settings, load_settings
from core.pipeline import Pipeline
from core.projects import Project, ProjectRegistry

logger = logging.getLogger(__name__)


class Workspace:
    """Los proyectos del usuario y sus pipelines.

    Un pipeline abre conexiones a Ollama y a Qdrant, así que construirlos en
    cada consulta sería caro; se crean cuando se piden por primera vez y se
    conservan. En un uso normal son unos pocos.
    """

    def __init__(self, data_dir: Path, base_settings: Settings | None = None) -> None:
        self.registry = ProjectRegistry(data_dir)
        #: Configuración de la que salen modelos, umbrales y direcciones. Lo
        #: único que cambia entre proyectos es qué corpus se consulta.
        self.base_settings = base_settings or load_settings()
        self._pipelines: dict[str, Pipeline] = {}
        #: Dos peticiones simultáneas sobre un proyecto recién abierto crearían
        #: dos pipelines y una de las dos quedaría huérfana, con sus conexiones
        #: sin cerrar.
        self._lock = threading.Lock()

    def settings_for(self, project: Project) -> Settings:
        """La configuración base, apuntada al corpus de un proyecto.

        Se copia en vez de mutarse: `base_settings` la comparten todos, y
        cambiarla en sitio le movería el corpus a los demás.
        """
        return self.base_settings.model_copy(
            update={
                "project": self.base_settings.project.model_copy(
                    update={
                        "name": project.slug,
                        "title": project.name,
                        "docs_path": project.docs_path,
                    }
                )
            },
            deep=True,
        )

    def pipeline_for(self, slug: str) -> Pipeline:
        """El pipeline de un proyecto, creándolo la primera vez."""
        project = self.registry.get(slug)
        if project is None:
            raise KeyError(f"No hay ningún proyecto '{slug}'")

        with self._lock:
            pipeline = self._pipelines.get(slug)
            if pipeline is None:
                logger.info("Abriendo el proyecto '%s'", slug)
                pipeline = Pipeline(self.settings_for(project))
                self._pipelines[slug] = pipeline
            return pipeline

    def open(self, slug: str) -> Pipeline:
        """Como `pipeline_for`, anotando además que el proyecto se usó.

        Ese registro es lo que ordena el listado, así que solo lo marcan las
        aperturas deliberadas: consultar el estado de un proyecto no cuenta
        como usarlo.
        """
        pipeline = self.pipeline_for(slug)
        self.registry.mark_opened(slug)
        return pipeline

    def ingest(self, slug: str) -> tuple[int, int]:
        """Indexa el proyecto y anota el resultado en el registro.

        Devuelve los documentos y fragmentos indexados.
        """
        project = self.registry.get(slug)
        if project is None:
            raise KeyError(f"No hay ningún proyecto '{slug}'")
        if not project.exists:
            raise FileNotFoundError(f"La carpeta del proyecto ya no existe: {project.docs_path}")

        result = self.pipeline_for(slug).ingest()
        self.registry.mark_indexed(slug, result.documents, result.chunks)
        return result.documents, result.chunks

    def delete(self, slug: str) -> None:
        """Borra el proyecto: su índice y su entrada en el registro.

        Los documentos no se tocan: son del usuario y viven en su carpeta.
        """
        project = self.registry.get(slug)

        with self._lock:
            pipeline = self._pipelines.pop(slug, None)

        # Si nunca se abrió no hay pipeline en caché, pero su colección puede
        # existir igual —de una indexación anterior, o de una sesión previa— y
        # dejarla atrás iría llenando el vector store de índices sin dueño.
        if pipeline is None and project is not None:
            pipeline = Pipeline(self.settings_for(project))

        if pipeline is not None:
            # Se borra la colección antes de soltar el pipeline, que es quien
            # tiene la conexión abierta al vector store.
            try:
                pipeline.store.drop_collection()
            except Exception:
                logger.exception("No se pudo borrar el índice de '%s'", slug)
            pipeline.close()

        self.registry.delete(slug)

    def close(self) -> None:
        """Cierra todos los pipelines abiertos."""
        with self._lock:
            pipelines = list(self._pipelines.values())
            self._pipelines.clear()
        for pipeline in pipelines:
            pipeline.close()
