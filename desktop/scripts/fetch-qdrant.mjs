/**
 * Descarga el binario de Qdrant que la app empaqueta (`npm run fetch:qdrant`).
 *
 * Qdrant se distribuye como un ejecutable suelto sin dependencias, así que la
 * app puede lanzarlo como proceso hijo y Docker deja de hacer falta. Este
 * script lo trae desde las releases oficiales y lo deja en `binaries/qdrant/`,
 * que es donde lo buscan tanto el modo desarrollo como el empaquetado.
 *
 * El proyecto no publica checksums en sus releases, así que los de más abajo se
 * calcularon sobre las descargas verificadas y se fijan acá: sin ellos, una
 * descarga alterada pasaría inadvertida. Al subir de versión hay que
 * recalcularlos.
 */

import { createHash } from "node:crypto";
import { existsSync, mkdirSync, rmSync } from "node:fs";
import { chmod, readFile, writeFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/** Debe coincidir con la imagen del docker-compose: el cliente avisa cuando la
 *  diferencia de versión menor con el servidor es mayor que uno. */
const VERSION = "1.19.0";

const DESKTOP_DIR = dirname(dirname(fileURLToPath(import.meta.url)));
const TARGET_DIR = join(DESKTOP_DIR, "binaries", "qdrant");

/** Descarga por plataforma, con el sha256 de cada archivo. */
const RELEASES = {
  "linux-x64": {
    asset: "qdrant-x86_64-unknown-linux-musl.tar.gz",
    sha256: "9ec667456443463eee390e43cd36988af6b730c6db807b4e39f57c303d0264a3",
    executable: "qdrant",
  },
  "win32-x64": {
    asset: "qdrant-x86_64-pc-windows-msvc.zip",
    sha256: "980cb2e1ae771155cf211da8c0a8a9206b6482bd4effdc4db994d3adb707b087",
    executable: "qdrant.exe",
  },
  "darwin-arm64": {
    asset: "qdrant-aarch64-apple-darwin.tar.gz",
    sha256: "4e279a80cc1ebe73e859318ff86375af54c123887dd7ae46605c0eb6cb7c44e8",
    executable: "qdrant",
  },
};

const key = `${process.platform}-${process.arch}`;
const release = RELEASES[key];
if (!release) {
  console.error(
    `error: no hay un binario de Qdrant configurado para ${key}.\n` +
      `Plataformas disponibles: ${Object.keys(RELEASES).join(", ")}`,
  );
  process.exit(1);
}

const target = join(TARGET_DIR, release.executable);

// En Linux lo que queda guardado es el comprimido; ver el comentario del final.
const stored = process.platform === "linux" ? `${target}.gz` : target;
if (existsSync(stored)) {
  console.log(`Qdrant ${VERSION} ya está en ${stored}`);
  process.exit(0);
}

const url =
  `https://github.com/qdrant/qdrant/releases/download/v${VERSION}/${release.asset}`;

console.log(`Descargando Qdrant ${VERSION} para ${key} ...`);
const response = await fetch(url);
if (!response.ok) {
  console.error(`error: la descarga falló (HTTP ${response.status}): ${url}`);
  process.exit(1);
}

const archive = Buffer.from(await response.arrayBuffer());

const digest = createHash("sha256").update(archive).digest("hex");
if (digest !== release.sha256) {
  console.error(
    "error: el archivo descargado no coincide con el checksum esperado.\n" +
      `  esperado: ${release.sha256}\n` +
      `  obtenido: ${digest}\n` +
      "No se instala nada. Si subiste la versión, actualizá el checksum.",
  );
  process.exit(1);
}

rmSync(TARGET_DIR, { recursive: true, force: true });
mkdirSync(TARGET_DIR, { recursive: true });

const archivePath = join(TARGET_DIR, release.asset);
await writeFile(archivePath, archive);

// tar está en Windows 10+ y en todos los Unix; unzip no siempre, así que los
// .zip se extraen con PowerShell, que sí viene de serie.
const extract = release.asset.endsWith(".zip")
  ? spawnSync(
      "powershell",
      [
        "-NoProfile",
        "-Command",
        `Expand-Archive -LiteralPath '${archivePath}' -DestinationPath '${TARGET_DIR}' -Force`,
      ],
      { stdio: "inherit" },
    )
  : spawnSync("tar", ["xzf", archivePath, "-C", TARGET_DIR], { stdio: "inherit" });

if (extract.status !== 0) {
  console.error("error: no se pudo extraer el archivo descargado");
  process.exit(1);
}

rmSync(archivePath, { force: true });

if (!existsSync(target)) {
  console.error(`error: el archivo no contenía ${release.executable}`);
  process.exit(1);
}

if (process.platform !== "win32") {
  await chmod(target, 0o755);
}

// En Linux el binario se guarda además comprimido, y la app lo descomprime al
// arrancar. Es un rodeo, pero necesario: al armar el AppImage, linuxdeploy
// recorre los ELF que encuentra y les pasa patchelf para reescribir sus rutas
// de librerías. Qdrant viene enlazado estáticamente (static-pie), y ese parcheo
// le inyecta un RUNPATH que no debería tener y lo deja muerto con SIGSEGV,
// antes de emitir una sola línea de log. Tauri no ofrece forma de excluir un
// recurso del recorrido (tauri-apps/tauri#11898), y quitarle el permiso de
// ejecución no alcanza: lo detecta igual. Comprimido no lo reconoce como ELF y
// lo copia intacto.
if (process.platform === "linux") {
  const { gzipSync } = await import("node:zlib");
  const compressed = gzipSync(await readFile(target), { level: 9 });
  await writeFile(`${target}.gz`, compressed);
  rmSync(target, { force: true });
  console.log(
    `Listo: ${target}.gz  (${(compressed.length / 1e6).toFixed(0)} MB comprimido)`,
  );
} else {
  const size = (await readFile(target)).length / 1e6;
  console.log(`Listo: ${target}  (${size.toFixed(0)} MB)`);
}
