//! Supervisión de los procesos que la app levanta y de los que depende.
//!
//! Mnemosyne necesita tres piezas corriendo: el vector store (Qdrant), el
//! runtime de inferencia (Ollama) y la API de Python que las une. La app las
//! arranca como procesos hijos —salvo Ollama, que se instala aparte por su
//! tamaño— y las espera hasta que responden, en vez de asumir que ya están.
//!
//! Cada servicio escucha en un puerto pedido libre al sistema en vez de uno
//! fijo: el 6333 y el 8100 pueden estar tomados por el `docker compose` del
//! propio proyecto, y dos ventanas abiertas a la vez chocarían entre sí.

use std::io::{BufRead, BufReader};
use std::net::TcpListener;
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

/// Intervalo entre sondeos mientras se espera que un servicio arranque.
const POLL_INTERVAL: Duration = Duration::from_millis(200);

/// Un proceso hijo supervisado, junto con la URL donde quedó escuchando.
pub struct Service {
    name: &'static str,
    child: Mutex<Option<Child>>,
    pub base_url: String,
}

impl Service {
    /// Lanza un proceso y espera a que su sondeo de salud responda.
    ///
    /// `configure` recibe el comando a medio armar y el puerto elegido, para que
    /// cada servicio añada sus propias variables de entorno y argumentos.
    pub fn spawn<F>(
        name: &'static str,
        exe: &Path,
        health_path: &str,
        timeout: Duration,
        configure: F,
    ) -> Result<Self, String>
    where
        F: FnOnce(&mut Command, u16),
    {
        let port = free_port()?;

        let mut command = Command::new(exe);
        command.stdout(Stdio::piped()).stderr(Stdio::piped());
        configure(&mut command, port);

        // En Windows, evita que se abra una consola detrás de la ventana.
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            command.creation_flags(CREATE_NO_WINDOW);
        }

        let mut child = command
            .spawn()
            .map_err(|e| format!("no se pudo lanzar {name} ({}): {e}", exe.display()))?;

        // La salida del hijo se reenvía al log de la app. Sin esto, un fallo de
        // arranque sería invisible: el proceso muere y lo único que se ve es el
        // timeout.
        forward_output(name, &mut child);

        let service = Service {
            name,
            child: Mutex::new(Some(child)),
            base_url: format!("http://127.0.0.1:{port}"),
        };

        service.wait_until_ready(health_path, timeout)?;
        Ok(service)
    }

    /// Sondea hasta que el servicio responde, el proceso muere, o vence el plazo.
    fn wait_until_ready(&self, health_path: &str, timeout: Duration) -> Result<(), String> {
        let deadline = Instant::now() + timeout;
        let health = format!("{}{health_path}", self.base_url);

        while Instant::now() < deadline {
            // Si el proceso ya terminó, seguir sondeando solo gasta el timeout.
            if let Ok(mut guard) = self.child.lock() {
                if let Some(child) = guard.as_mut() {
                    if let Ok(Some(status)) = child.try_wait() {
                        return Err(format!(
                            "{} terminó durante el arranque (código {status}); \
                             revisá el log para ver el error",
                            self.name
                        ));
                    }
                }
            }

            if ureq::get(&health)
                .timeout(Duration::from_secs(2))
                .call()
                .is_ok()
            {
                log::info!("{} listo en {}", self.name, self.base_url);
                return Ok(());
            }

            std::thread::sleep(POLL_INTERVAL);
        }

        Err(format!(
            "{} no respondió en {} segundos",
            self.name,
            timeout.as_secs()
        ))
    }

    /// Termina el proceso hijo. Idempotente.
    ///
    /// Se llama al cerrar la ventana: sin esto quedan procesos huérfanos
    /// sosteniendo sus puertos, y el siguiente arranque encuentra el
    /// almacenamiento bloqueado.
    pub fn shutdown(&self) {
        let Ok(mut guard) = self.child.lock() else {
            return;
        };
        let Some(mut child) = guard.take() else {
            return;
        };

        // Terminación inmediata. Alcanza al proceso lanzado, no a una
        // descendencia suya: basta porque ni uvicorn —que recibe la aplicación
        // ya importada, sin workers ni recargador— ni Qdrant se ramifican.
        if let Err(e) = child.kill() {
            log::warn!("no se pudo terminar {}: {e}", self.name);
        }
        let _ = child.wait();
        log::info!("{} detenido", self.name);
    }
}

impl Drop for Service {
    fn drop(&mut self) {
        self.shutdown();
    }
}

/// Pide al sistema operativo un puerto libre.
///
/// Se cierra el listener y se reusa el número. Hay una ventana de carrera
/// teórica entre cerrar y que el servicio haga bind, pero es la forma estándar
/// de resolverlo y en la práctica no colisiona en localhost.
fn free_port() -> Result<u16, String> {
    let listener =
        TcpListener::bind("127.0.0.1:0").map_err(|e| format!("no hay puertos disponibles: {e}"))?;
    listener
        .local_addr()
        .map(|addr| addr.port())
        .map_err(|e| format!("no se pudo leer el puerto asignado: {e}"))
}

/// Reenvía stdout y stderr del hijo al log de la app, línea por línea.
fn forward_output(name: &'static str, child: &mut Child) {
    for stream in [
        child.stdout.take().map(StreamKind::Out),
        child.stderr.take().map(StreamKind::Err),
    ]
    .into_iter()
    .flatten()
    {
        std::thread::spawn(move || match stream {
            // uvicorn escribe su log normal en stderr, así que no se distingue
            // por nivel: ambos flujos van a info.
            StreamKind::Out(pipe) => log_lines(name, pipe),
            StreamKind::Err(pipe) => log_lines(name, pipe),
        });
    }
}

enum StreamKind {
    Out(std::process::ChildStdout),
    Err(std::process::ChildStderr),
}

fn log_lines<R: std::io::Read>(name: &str, pipe: R) {
    for line in BufReader::new(pipe).lines().map_while(Result::ok) {
        log::info!("[{name}] {line}");
    }
}
