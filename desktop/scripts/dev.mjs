/**
 * Lanza la app en desarrollo (`npm run dev`), en Linux, Windows y macOS.
 *
 * Sobre `npx tauri dev` directo aporta una sola cosa, pero necesaria en Linux:
 * limpiar el entorno de snap. Si el editor está instalado como snap —VS Code lo
 * está por defecto en Ubuntu—, exporta rutas de /snap/core20 y el enlazador
 * carga desde ahí una libpthread incompatible con la glibc del sistema. La app
 * muere antes de abrir la ventana:
 *
 *     symbol lookup error: /snap/core20/.../libpthread.so.0:
 *     undefined symbol: __libc_pthread_init, version GLIBC_PRIVATE
 *
 * En Windows y macOS no hay nada que limpiar y el script solo delega.
 */

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const DESKTOP_DIR = dirname(dirname(fileURLToPath(import.meta.url)));

/** Variables del snap que desvían la carga de librerías y de módulos de GTK. */
const SNAP_VARS = [
  "SNAP", "SNAP_NAME", "SNAP_REVISION", "SNAP_VERSION", "SNAP_ARCH",
  "SNAP_LIBRARY_PATH", "SNAP_USER_DATA", "SNAP_USER_COMMON", "SNAP_DATA",
  "SNAP_COMMON", "SNAP_REAL_HOME", "SNAP_INSTANCE_NAME", "SNAP_INSTANCE_KEY",
  "SNAP_CONTEXT", "SNAP_COOKIE",
  "GTK_PATH", "GTK_EXE_PREFIX", "GTK_IM_MODULE_FILE",
  "GIO_MODULE_DIR", "GSETTINGS_SCHEMA_DIR", "LOCPATH",
  "GDK_PIXBUF_MODULE_FILE", "GDK_PIXBUF_MODULEDIR",
  "LD_LIBRARY_PATH",
];

const env = { ...process.env };

if (process.platform === "linux") {
  for (const name of SNAP_VARS) {
    delete env[name];
  }

  // El snap redirige los directorios XDG a su propia jaula; devolverlos a los
  // del usuario mantiene el directorio de datos de la app en su sitio real.
  const home = env.HOME ?? homedir();
  env.XDG_DATA_HOME = join(home, ".local", "share");
  env.XDG_CONFIG_HOME = join(home, ".config");
  env.XDG_CACHE_HOME = join(home, ".cache");

  // rustup añade esta ruta desde el perfil del shell, que la terminal del
  // editor no siempre carga.
  const cargoBin = join(home, ".cargo", "bin");
  if (existsSync(cargoBin) && !(env.PATH ?? "").includes(cargoBin)) {
    env.PATH = `${cargoBin}:${env.PATH ?? ""}`;
  }
}

const child = spawn("npx", ["tauri", "dev", ...process.argv.slice(2)], {
  cwd: DESKTOP_DIR,
  env,
  stdio: "inherit",
  // En Windows, npx es un .cmd y solo se resuelve a través del shell.
  shell: process.platform === "win32",
});

child.on("exit", (code, signal) => {
  process.exit(signal ? 1 : (code ?? 0));
});
