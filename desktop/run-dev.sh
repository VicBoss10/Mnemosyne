#!/usr/bin/env bash
# Arranca la app en desarrollo con un entorno gráfico limpio.
#
# Existe por el confinamiento de snap: si el editor —VS Code instalado como
# snap— exporta su propio entorno, la app hereda las rutas de /snap/core20 y el
# enlazador carga desde ahí una libpthread incompatible con la glibc del
# sistema. El síntoma es un fallo al arrancar, antes de que se abra la ventana:
#
#   symbol lookup error: /snap/core20/.../libpthread.so.0:
#   undefined symbol: __libc_pthread_init, version GLIBC_PRIVATE
#
# Lanzada desde una terminal normal la app no necesita este script, pero
# ejecutarlo igual no hace daño: las variables que limpia solo existen dentro
# del snap.

set -euo pipefail

cd "$(dirname "$0")"

# Rutas del snap que desvían la carga de librerías y de módulos de GTK.
unset SNAP SNAP_NAME SNAP_REVISION SNAP_VERSION SNAP_ARCH SNAP_LIBRARY_PATH
unset SNAP_USER_DATA SNAP_USER_COMMON SNAP_DATA SNAP_COMMON SNAP_REAL_HOME
unset SNAP_INSTANCE_NAME SNAP_INSTANCE_KEY SNAP_CONTEXT SNAP_COOKIE
unset GTK_PATH GTK_EXE_PREFIX GTK_IM_MODULE_FILE
unset GIO_MODULE_DIR GSETTINGS_SCHEMA_DIR LOCPATH
unset GDK_PIXBUF_MODULE_FILE GDK_PIXBUF_MODULEDIR
unset LD_LIBRARY_PATH

# El snap redirige los directorios XDG a su propia jaula; devolverlos a los del
# usuario mantiene el directorio de datos de la app en su sitio real.
export XDG_DATA_HOME="${HOME}/.local/share"
export XDG_CONFIG_HOME="${HOME}/.config"
export XDG_CACHE_HOME="${HOME}/.cache"

# rustup instala el toolchain en el home y añade esta ruta desde el perfil del
# shell, que no siempre se carga en la terminal del editor.
if [ -f "${HOME}/.cargo/env" ]; then
    # shellcheck disable=SC1091
    . "${HOME}/.cargo/env"
fi

exec npx tauri dev "$@"
