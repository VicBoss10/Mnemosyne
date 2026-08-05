# Inventario de servicios — MOVE

Resumen generado a partir de `docker-compose.yml`, `backend/dockerfile`, `frontend/dockerfile` y `vehicle-detection-service/requirements.txt`.

## Servicios (docker-compose)

- **backend** (container: `move-backend`)
  - Build: `./backend/dockerfile` (Maven build, Java 21)
  - Puertos: `8080:8080`
  - Depende de: `move_db`, `keycloak`
  - Variables importantes:
    - `SPRING_DATASOURCE_URL`, `SPRING_DATASOURCE_USERNAME`, `SPRING_DATASOURCE_PASSWORD`
    - `PYTHON_SERVICE_URL` (por defecto `http://vehicle-detection:5000`)
    - `KEYCLOAK_*` (issuer, jwk, server url)
  - Nota: espera un servicio de detección de vehículos accesible como `vehicle-detection:5000`.

- **move_db** (container: `move_db`)
  - Imagen: `postgres:17`
  - Puertos: `5432:5432`
  - Volumen: `move_db_data`

- **keycloak_db** (container: `keycloak_db`)
  - Imagen: `postgres:17`
  - Puertos: `5433:5432`
  - Volumen: `keycloak_db_data`

- **keycloak** (container: `move-keycloak`)
  - Imagen: `keycloak/keycloak:latest`
  - Puertos: `8081:8080`
  - Volumen: `./keycloak/move-realm-import.json` → import realm
  - Depende de: `keycloak_db` (con healthcheck)

- **frontend** (container: `move-frontend`) — perfil `frontend`
  - Build: `./frontend/dockerfile` (Node 20, build Angular, serve static via Nginx)
  - Puertos: `80:80`
  - Depende de: `backend`
  - Entrypoint: `docker-entrypoint.sh` para inyectar variables runtime

- **pgadmin** (perfil `tools`)
  - Imagen: `dpage/pgadmin4:latest`
  - Puertos: `5050:80`

- **cloudflare-tunnel** (perfil `tunnel`)
  - Imagen: `cloudflare/cloudflared:latest`
  - `network_mode: host` (expuesto fuera de la red Docker)

## Observaciones clave

- El `backend` referencia `vehicle-detection` en `PYTHON_SERVICE_URL`, pero **no existe** un servicio `vehicle-detection` en `docker-compose.yml`.
  - Dado tu comentario, `vehicle-detection-service` se ejecuta localmente (fuera de Docker). Ajustes recomendados:
    - Opción A: arrancar `vehicle-detection` en un contenedor y añadir al compose como `vehicle-detection`.
    - Opción B: configurar `PYTHON_SERVICE_URL` para usar `host.docker.internal` o la URL pública cuando se ejecute el backend en Docker.

- El `frontend` produce una build de Angular en `/app/dist/ng-tailadmin/browser` y la sirve con Nginx. `docker-entrypoint.sh` inyecta variables en runtime.

- `backend/dockerfile` usa Java 21 y empaqueta `move-0.0.1-SNAPSHOT.jar`. Requiere Maven y tiempo de build local; la imagen final es `eclipse-temurin:21-jdk`.

- `frontend/dockerfile` usa `node:20-alpine` para build y `nginx:alpine` para servir.

- `vehicle-detection-service/requirements.txt` incluye frameworks ML pesados: `torch`, `torchvision`, `ultralytics`, `opencv-python-headless`, `flask`, `gunicorn`, etc.
  - Recomendación: mantener este servicio local por ahora o crear Dockerfile con CUDA/capacidades apropiadas si se desea contenerizar.

## Archivos referenciados

- Compose: [docker-compose.yml](docker-compose.yml#L1-L400)
- Backend Dockerfile: [backend/dockerfile](backend/dockerfile#L1-L100)
- Frontend Dockerfile: [frontend/dockerfile](frontend/dockerfile#L1-L200)
- Vehicle detection requirements: [vehicle-detection-service/requirements.txt](vehicle-detection-service/requirements.txt#L1-L200)

## Siguientes pasos sugeridos

1. Confirmar si quieres contenerizar `vehicle-detection` o exponerlo a Docker (host.docker.internal).  
2. Generar un diagrama rápido de arquitectura (puedo hacerlo).  
3. Crear checklist por servicio para: documentación, linting, tests, deprecaciones y actualización de dependencias.

---
Generado automáticamente por el asistente.
