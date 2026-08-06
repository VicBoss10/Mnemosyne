# Mnemosyne para escritorio

Envoltorio nativo (Tauri) sobre el motor de `core/` y la API de `api/`.

Esta capa **no contiene lógica del motor**: lanza la API de Python como proceso
hijo, espera a que responda `/health`, y apunta la ventana a la interfaz que esa
misma API sirve. El código de `core/` y `api/` no se modifica — la app es otra
forma de arrancar lo mismo que `mnemosyne serve`.

## Estructura

```
desktop/
├── api_entry.py         punto de entrada de la API (puerto vía entorno)
├── build_api.py         empaqueta la API con PyInstaller → binaries/
├── scripts/             lanzadores multiplataforma en Node
├── src/                 marcador de posición; la UI real la sirve la API
└── src-tauri/src/
    ├── lib.rs           arranque, ventana y comandos de la interfaz
    ├── service.rs       supervisión genérica de un proceso hijo
    ├── qdrant.rs        arranque del vector store
    ├── api.rs           arranque de la API de Python
    ├── documents.rs     carpeta de documentos e indexación
    └── paths.rs         ubicación de los binarios empaquetados
```

## Requisitos

**Rust** ([rustup.rs](https://rustup.rs)) y **Node 18+**.

En Linux (Ubuntu/Debian), las librerías del WebView:

```bash
sudo apt install libwebkit2gtk-4.1-dev libgtk-3-dev \
  libayatana-appindicator3-dev librsvg2-dev patchelf
```

En Windows: [WebView2](https://developer.microsoft.com/microsoft-edge/webview2/)
(ya viene con Windows 11) y las Build Tools de Visual Studio.

## Desarrollo

La app necesita el ejecutable de la API antes de arrancar:

```bash
pip install pyinstaller     # una sola vez
cd desktop
npm install                 # una sola vez
npm run prepare:deps        # descarga Qdrant y empaqueta la API → binaries/
npm run dev
```

`npm run prepare:deps` se vuelve a correr solo cuando cambia el código de
Python; Qdrant se descarga una única vez.
Para iterar sobre el diseño de la interfaz basta con editar
`api/static/index.html` y recargar la ventana: la sirve la API, no el bundle.

## Instaladores

```bash
npm run build
```

Empaqueta la API por su cuenta —`beforeBuildCommand` la reconstruye antes de
armar el paquete— y la incluye como recurso, así que el instalador la lleva
adentro: no hay que instalar Python en la máquina de destino.

Deja los paquetes en `src-tauri/target/release/bundle/`: `.AppImage` y `.deb` en
Linux (~127 MB y ~69 MB), `.msi` y el instalador NSIS en Windows. Cada sistema
se compila en el suyo: PyInstaller genera un ejecutable nativo, no
multiplataforma.

## Qué levanta la app

Al abrirse arranca dos procesos hijos y los espera hasta que responden:

| Proceso | De dónde sale | Puerto |
|---|---|---|
| Qdrant | incluido en el instalador | libre, elegido al arrancar |
| API de Python | incluida en el instalador | libre, elegido al arrancar |

**Docker ya no hace falta.** El motor sigue hablando con un Qdrant real por
HTTP —`core/store.py` no cambió— pero quien lo levanta es la app.

Los puertos se piden libres al sistema en vez de fijarlos: el 6333 y el 8100
pueden estar ocupados por el `docker compose` del proyecto, y dos ventanas
abiertas a la vez chocarían entre sí.

El índice, la configuración y la carpeta elegida viven en el directorio de datos
del sistema (`~/.local/share/com.mnemosyne.desktop/` en Linux,
`%APPDATA%\com.mnemosyne.desktop\` en Windows), no junto al ejecutable.

## Estado

Pasos 1, 2, 3 y 6 de 8: la app arranca sus servicios, permite elegir la carpeta
de documentos con el selector del sistema, indexa, y los instaladores llevan
todo adentro.

Falta **Ollama**, que por su tamaño no se puede empaquetar: hoy la app asume que
ya está corriendo. Los pasos que quedan añaden el asistente que lo detecta, lo
instala si falta y descarga los modelos mostrando el progreso.
