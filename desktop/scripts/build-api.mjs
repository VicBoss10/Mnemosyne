/**
 * Empaqueta la API de Python (`npm run build:api`), en Linux, Windows y macOS.
 *
 * Toda la lógica del empaquetado vive en build_api.py; esto solo resuelve con
 * qué intérprete llamarlo, que es lo que cambia entre sistemas: en Windows el
 * comando `python` a veces no existe —la instalación oficial deja `py`, y la de
 * la Microsoft Store deja un alias que abre la tienda— mientras que en Linux y
 * macOS `python` puede apuntar a Python 2 o directamente faltar.
 *
 * Cuando hay varios, se elige el que tenga PyInstaller instalado y no el
 * primero que responda: en la misma máquina pueden convivir dos Python y solo
 * uno haber recibido las dependencias del proyecto.
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
  // "python" antes que "py" también en Windows: el lanzador `py` resuelve a la
  // instalación que él considera predeterminada, que no tiene por qué ser la
  // del PATH — la que acaba de recibir las dependencias. En un runner de CI son
  // dos Python distintos y `py` apunta al que no sirve.
  return found.concat(isWindows ? ["python", "python3", "py"] : ["python3", "python"]);
}

/** Si un intérprete tiene instalado lo que build_api.py necesita. */
function isUsable(interpreter) {
  const check = spawnSync(interpreter, ["-c", "import PyInstaller"], {
    stdio: "ignore",
    shell: isWindows,
  });
  return !check.error && check.status === 0;
}

const script = join(DESKTOP_DIR, "build_api.py");
const interpreters = candidates();

// Se elige por lo que el intérprete puede hacer, no por su nombre: varios
// pueden existir a la vez y solo uno tener las dependencias del proyecto.
// Ejecutar el primero que exista fallaría en el que no las tiene sin llegar a
// probar el que sí.
const interpreter = interpreters.find(isUsable);

if (!interpreter) {
  console.error(
    "error: no se encontró un Python con PyInstaller instalado.\n" +
      `Probados: ${interpreters.join(", ")}\n` +
      "Instalalo con:  pip install pyinstaller",
  );
  process.exit(1);
}

const result = spawnSync(interpreter, [script], {
  cwd: DESKTOP_DIR,
  stdio: "inherit",
  shell: isWindows,
});
process.exit(result.status ?? 1);
