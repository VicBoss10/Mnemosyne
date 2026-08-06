//! Supervisión del proceso de la API de Python.
//!
//! La app no reimplementa el motor: lanza la misma API que `mnemosyne serve`
//! levanta, la espera hasta que responde `/health`, y carga su interfaz en la
//! ventana. El proceso Python no sabe que corre dentro de una app de escritorio.
//!
//! El puerto se elige libre en tiempo de ejecución en vez de fijarlo: dos
//! instancias abiertas a la vez chocarían por un puerto fijo, y 8100 puede estar
//! ocupado por el `docker compose` del propio proyecto.

use std::io::{BufRead, BufReader};
use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

/// Cuánto se espera a que la API acepte conexiones antes de darla por fallida.
/// Generoso porque el primer arranque importa el runtime completo de Python.
const STARTUP_TIMEOUT: Duration = Duration::from_secs(60);

/// Intervalo entre sondeos a `/health` mientras se espera el arranque.
const POLL_INTERVAL: Duration = Duration::from_millis(200);

/// Proceso hijo de la API, junto con la URL donde quedó escuchando.
pub struct Backend {
    child: Mutex<Option<Child>>,
    pub base_url: String,
}

impl Backend {
    /// Lanza la API en un puerto libre y espera a que responda.
    pub fn spawn(exe: &PathBuf, data_dir: &PathBuf) -> Result<Self, String> {
        let port = free_port()?;

        let mut command = Command::new(exe);
        command
            .env("MNEMOSYNE_HOST", "127.0.0.1")
            .env("MNEMOSYNE_PORT", port.to_string())
            // El índice y la configuración viven en el directorio de datos del
            // sistema operativo, no junto al ejecutable: en Windows el
            // directorio de instalación es de solo lectura para el usuario.
            .env("MNEMOSYNE_DATA_DIR", data_dir)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        // En Windows, evita que se abra una consola detrás de la ventana.
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            command.creation_flags(CREATE_NO_WINDOW);
        }

        let mut child = command
            .spawn()
            .map_err(|e| format!("no se pudo lanzar la API ({}): {e}", exe.display()))?;

        // La salida del hijo se reenvía al log de la app. Sin esto, un fallo de
        // arranque de Python sería invisible: el proceso muere y lo único que se
        // ve es el timeout.
        forward_output(&mut child);

        let base_url = format!("http://127.0.0.1:{port}");
        let backend = Backend {
            child: Mutex::new(Some(child)),
            base_url,
        };

        backend.wait_until_ready()?;
        Ok(backend)
    }

    /// Sondea `/health` hasta que responde, el proceso muere, o vence el plazo.
    fn wait_until_ready(&self) -> Result<(), String> {
        let deadline = Instant::now() + STARTUP_TIMEOUT;
        let health = format!("{}/health", self.base_url);

        while Instant::now() < deadline {
            // Si el proceso ya terminó, seguir sondeando solo gasta el timeout.
            if let Ok(mut guard) = self.child.lock() {
                if let Some(child) = guard.as_mut() {
                    if let Ok(Some(status)) = child.try_wait() {
                        return Err(format!(
                            "la API terminó durante el arranque (código {status}); \
                             revisá el log para ver el error de Python"
                        ));
                    }
                }
            }

            if ureq::get(&health).timeout(Duration::from_secs(2)).call().is_ok() {
                log::info!("API lista en {}", self.base_url);
                return Ok(());
            }

            std::thread::sleep(POLL_INTERVAL);
        }

        Err(format!(
            "la API no respondió en {} segundos",
            STARTUP_TIMEOUT.as_secs()
        ))
    }

    /// Termina el proceso hijo. Idempotente.
    ///
    /// Se llama al cerrar la ventana: sin esto queda un proceso huérfano
    /// sosteniendo el puerto, y el siguiente arranque encuentra un índice
    /// bloqueado.
    pub fn shutdown(&self) {
        let Ok(mut guard) = self.child.lock() else {
            return;
        };
        let Some(mut child) = guard.take() else {
            return;
        };

        // SIGKILL directo: la API no tiene estado que perder al cerrarse — todo
        // lo persistente ya está en Qdrant — y uvicorn no siempre atiende
        // SIGTERM con rapidez.
        if let Err(e) = child.kill() {
            log::warn!("no se pudo terminar la API: {e}");
        }
        let _ = child.wait();
        log::info!("API detenida");
    }
}

impl Drop for Backend {
    fn drop(&mut self) {
        self.shutdown();
    }
}

/// Pide al sistema operativo un puerto libre.
///
/// Se cierra el listener y se reusa el número. Hay una ventana de carrera
/// teórica entre cerrar y que la API haga bind, pero es la forma estándar de
/// resolverlo y en la práctica no colisiona en localhost.
fn free_port() -> Result<u16, String> {
    let listener = TcpListener::bind("127.0.0.1:0")
        .map_err(|e| format!("no hay puertos disponibles: {e}"))?;
    listener
        .local_addr()
        .map(|addr| addr.port())
        .map_err(|e| format!("no se pudo leer el puerto asignado: {e}"))
}

/// Reenvía stdout y stderr del hijo al log de la app, línea por línea.
fn forward_output(child: &mut Child) {
    if let Some(stdout) = child.stdout.take() {
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                log::info!("[api] {line}");
            }
        });
    }
    if let Some(stderr) = child.stderr.take() {
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                // uvicorn escribe su log normal en stderr, así que esto no es
                // necesariamente un error.
                log::info!("[api] {line}");
            }
        });
    }
}
