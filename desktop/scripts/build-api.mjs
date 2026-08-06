/**
 * Empaqueta la API de Python (`npm run build:api`), en Linux, Windows y macOS.
 *
 * Toda la lógica del empaquetado vive en build_api.py; esto solo resuelve con
 * qué intérprete llamarlo, que es lo que cambia entre sistemas: en Windows el
 * comando `python` a menudo no existe —la instalación oficial deja `py`, y la
 * de la Microsoft Store deja un alias que abre la tienda— mientras que en Linux
 * y macOS `python` puede apuntar a Python 2 o directamente faltar.
 *
 * Se prefiere el intérprete del entorno virtual del repositorio cuando existe:
 * es el que tiene instaladas las dependencias del motor.
 */

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const DESKTOP_DIR = dirname(dirname(fileURLToPath(import.meta.url)));
const REPO_ROOT = dirname(DESKTOP_DIR);
const isWindows = process.platform === "win32";

/** Intérpretes a probar, en orden de preferencia. */
function candidates() {
  const venv = isWindows
    ? join(REPO_ROOT, ".venv", "Scripts", "python.exe")
    : join(REPO_ROOT, ".venv", "bin", "python");

  const found = [];
  if (existsSync(venv)) {
    found.push(venv);
  }
  return found.concat(isWindows ? ["py", "python", "python3"] : ["python3", "python"]);
}

const script = join(DESKTOP_DIR, "build_api.py");

for (const interpreter of candidates()) {
  const result = spawnSync(interpreter, [script], {
    cwd: DESKTOP_DIR,
    stdio: "inherit",
    shell: isWindows,
  });

  // El intérprete no existe: se prueba el siguiente. Cualquier otro código de
  // salida viene del script y hay que respetarlo.
  if (result.error?.code === "ENOENT" || result.status === 9009) {
    continue;
  }
  process.exit(result.status ?? 1);
}

console.error(
  "error: no se encontró un intérprete de Python.\n" +
    "Instalá Python 3.11 o superior, o creá el entorno virtual del repositorio.",
);
process.exit(1);
