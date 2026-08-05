# Checklist por servicio — MOVE

Este checklist está pensado para ejecutar las tareas de documentación, limpieza, detección de deprecaciones, testing y actualización por cada servicio. Crear una rama por servicio: `chore/{service}/cleanup`.

Formato recomendado por ítem: `- [ ] Tarea — responsable — nota breve`

## Backend (service: `backend`)
- [ ] Documentación de endpoints públicos y flujo de autenticación — escribe o actualiza `README.md` del backend.
- [ ] Ejecutar `mvn -q -DskipTests=false test` y corregir fallos (baseline).
- [ ] Ejecutar `mvn -DskipTests=false package` para capturar warnings de compilación.
- [ ] Ejecutar `mvn -X` si aparecen errores extraños y copiar logs para análisis.
- [ ] Revisar `application.properties` para variables obsoletas y centralizar en `config/` si aplica.
- [ ] Ejecutar `spotbugs` / `maven-checkstyle-plugin` y resolver issues críticos/major.
- [ ] Buscar APIs/dependencias deprecadas (compilación + `grep -R "@Deprecated|deprecated" src`) y preparar PRs de corrección.
- [ ] Añadir/actualizar tests unitarios si hay lógicas complejas afectadas.
- [ ] Ejecutar pruebas de integración (usar docker-compose core) y validar integraciones con `vehicle-detection` local.

## Frontend (service: `frontend`)
- [ ] Documentar comandos de desarrollo y build en `README.md` de `frontend`.
- [ ] Ejecutar `npm ci` y `npm test` (ver `package.json`); arreglar tests rotos.
- [ ] Ejecutar `npm run build` y comprobar `dist/` contents.
- [ ] Ejecutar `eslint` y `prettier --check` (instalar si falta). Aplicar `eslint --fix` cuando sea seguro.
- [ ] Revisar uso de APIs obsoletas de Angular y actualizar versiones sugeridas por `ng update` si procede (hacer en branch separado y validar).
- [ ] Verificar `docker-entrypoint.sh` y la inyección runtime de variables (evitar reemplazos frágiles por grep/sed).

## Vehicle Detection (local service)
- [ ] Documentar cómo ejecutar localmente (venv/conda), requisitos y GPU/CPU notes en `vehicle-detection-service/README.md`.
- [ ] Revisar `requirements.txt` y fijar versiones mínimas compatibles (evitar `torch` sin versión; prefer pin o extra index si necesita wheels).
- [ ] Ejecutar `python -m venv .venv && .venv/bin/pip install -r requirements.txt` en un entorno controlado; registrar problemas de instalación (CUDA/wheels).
- [ ] Añadir tests básicos (smoke) que verifiquen endpoints Flask (por ejemplo `/health`, `/detect`) usando `pytest` y `requests`.
- [ ] Considerar contenerización futura: crear `Dockerfile` con instrucciones y notas sobre GPU vs CPU.

## Keycloak & Databases
- [ ] Documentar el proceso de importación del realm (ya hay `keycloak/move-realm-import.json`) y pasos para reset/seed.
- [ ] Añadir notas de backup/restore para Postgres data volumes.

## Operacionales / Herramientas
- [ ] Crear script o comando para ejecutar el stack local mínimo (`docker compose up backend move_db keycloak keycloak_db`) y documentarlo.
- [ ] Añadir `Makefile` o scripts `scripts/start-core.sh`, `scripts/stop-core.sh` para desarrolladores.
- [ ] Añadir `docs/development.md` con checklist de pre-push: `npm test`, `mvn test`, `flake8`/`pylint` para Python.

## Revisión de PRs
- [ ] Cada PR de limpieza debe incluir: descripción, archivos tocados, comandos para reproducir, tests añadidos/actualizados.
- [ ] Marcar PRs que cambien dependencias con etiqueta `dependencies` y pedir revisión de CI antes de merge.

---
Puedo generar PRs automáticos por cada ítem y aplicar cambios mínimos; dime por cuál servicio quieres que empiece.
