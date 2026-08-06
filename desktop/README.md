# Mnemosyne para escritorio

Envoltorio nativo (Tauri) sobre el motor de `core/` y la API de `api/`.

Esta capa **no contiene lógica del motor**: lanza la API de Python como proceso
hijo, espera a que responda `/health`, y apunta la ventana a la interfaz que esa
misma API sirve. El código de `core/` y `api/` no se modifica — la app es otra
forma de arrancar lo mismo que `mnemosyne serve`.

## Estructura

```
desktop/
├── api_entry.py       punto de entrada de la API (puerto vía entorno)
├── build_api.py       empaqueta la API con PyInstaller → binaries/
├── src/               marcador de posición; la UI real la sirve la API
└── src-tauri/
    ├── src/lib.rs     arranque: lanza la API, crea la ventana
    ├── src/backend.rs supervisión del proceso hijo
    └── tauri.conf.json
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
python build_api.py         # genera binaries/mnemosyne-api/
npm install                 # una sola vez
npm run dev
```

`build_api.py` se vuelve a correr solo cuando cambia el código de Python.
Para iterar sobre el diseño de la interfaz basta con editar
`api/static/index.html` y recargar la ventana: la sirve la API, no el bundle.

## Instaladores

```bash
python build_api.py
npm run build
```

Deja los paquetes en `src-tauri/target/release/bundle/`: `.AppImage` y `.deb` en
Linux, `.msi` y el instalador NSIS en Windows.

## Estado

Paso 1 de 8: la app arranca la API y muestra su interfaz. Todavía **asume que
Qdrant y Ollama ya están corriendo** — los pasos siguientes incluyen Qdrant en
el instalador y añaden el asistente que detecta e instala Ollama y los modelos.
