//! Supervisión de los procesos que la app levanta y de los que depende.
//!
//! Mnemosyne necesita tres piezas corriendo: el vector store (Qdrant), el
//! runtime de inferencia (Ollama) y la API de Python que las une. La app las
//! arranca como procesos hijos —salvo Ollama, que se instala aparte por su
//! tamaño— y las espera hasta que responden, en vez de asumir que ya están.
//!
//! Cada servicio escucha en un puerto pedido libre al sistema en vez de uno
//! fijo: los de por defecto pueden estar tomados por otro Qdrant o por un
//! `mnemosyne serve` lanzado a mano, y dos ventanas abiertas a la vez
//! chocarían entre sí.

use std::io::{BufRead, BufReader};
use std::net::TcpListener;
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

/// Intervalo entre sondeos mientras se espera que un servicio arranque.
const POLL_INTERVAL: Duration = Duration::from_millis(200);

/// Handle de un job object de Windows configurado para matar a sus miembros
/// cuando se cierra. Se queda vivo dentro del `Service`: cerrarlo antes de
/// tiempo mataría al hijo que se supone que protege.
#[cfg(windows)]
struct KillOnCloseJob(windows_sys::Win32::Foundation::HANDLE);

// HANDLE es un puntero crudo, que por defecto no es Send ni Sync. Es seguro
// acá: nadie más lo desreferencia, solo se pasa a las llamadas de Win32 que lo
// esperan, y AppState necesita Send + Sync para vivir dentro de tauri::State.
#[cfg(windows)]
unsafe impl Send for KillOnCloseJob {}
#[cfg(windows)]
unsafe impl Sync for KillOnCloseJob {}

#[cfg(windows)]
impl Drop for KillOnCloseJob {
    fn drop(&mut self) {
        unsafe { windows_sys::Win32::Foundation::CloseHandle(self.0) };
    }
}

/// Un proceso hijo supervisado, junto con la URL donde quedó escuchando.
pub struct Service {
    name: &'static str,
    child: Mutex<Option<Child>>,
    /// Para hablarle desde la capa nativa.
    pub base_url: String,
    /// El mismo servicio por nombre de host, para cargarlo en la ventana.
    pub local_url: String,
    /// Ver `KillOnCloseJob`. Vive hasta que este `Service` se destruye, es
    /// decir, hasta que el proceso de la app termina.
    #[cfg(windows)]
    _job: Option<KillOnCloseJob>,
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

        // Si la app muere sin poder ejecutar su apagado —un cierre forzado, un
        // fallo—, el sistema operativo se encarga de matar al hijo. Sin esto
        // los procesos sobreviven, y el siguiente arranque encuentra el
        // almacenamiento de Qdrant bloqueado por el anterior.
        #[cfg(target_os = "linux")]
        unsafe {
            use std::os::unix::process::CommandExt;
            command.pre_exec(|| {
                // SIGKILL al proceso actual cuando muera su padre.
                if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) == -1 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }

        let mut child = command
            .spawn()
            .map_err(|e| format!("no se pudo lanzar {name} ({}): {e}", exe.display()))?;

        // Windows no tiene un equivalente a PR_SET_PDEATHSIG que se pueda pedir
        // antes de lanzar el proceso: hay que armarlo después, metiendo al hijo
        // en un job object con JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE. El kernel
        // mata a todo lo que quede en el job en cuanto se cierra su último
        // handle, y eso pasa solo al terminar el proceso de la app —incluido un
        // cierre forzado desde el Administrador de tareas—, porque Windows
        // cierra los handles abiertos de un proceso al matarlo.
        #[cfg(windows)]
        let job = match create_kill_on_close_job() {
            Ok(job) => {
                use std::os::windows::io::AsRawHandle;
                let handle = child.as_raw_handle() as windows_sys::Win32::Foundation::HANDLE;
                if unsafe { windows_sys::Win32::System::JobObjects::AssignProcessToJobObject(job.0, handle) } == 0 {
                    log::warn!(
                        "no se pudo asociar {name} a su job object; si la app se cierra \
                         de golpe el proceso podría quedar huérfano: {}",
                        std::io::Error::last_os_error()
                    );
                    None
                } else {
                    Some(job)
                }
            }
            Err(e) => {
                log::warn!("{e}");
                None
            }
        };

        // La salida del hijo se reenvía al log de la app. Sin esto, un fallo de
        // arranque sería invisible: el proceso muere y lo único que se ve es el
        // timeout.
        forward_output(name, &mut child);

        let service = Service {
            name,
            child: Mutex::new(Some(child)),
            base_url: format!("http://127.0.0.1:{port}"),
            // Mismo destino, distinto nombre de host, y la diferencia importa
            // para la ventana: Tauri no reconoce una dirección IP en el patrón
            // de orígenes remotos con acceso a comandos (tauri-apps/tauri#7009),
            // así que la interfaz tiene que cargarse por "localhost".
            local_url: format!("http://localhost:{port}"),
            #[cfg(windows)]
            _job: job,
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

/// Crea un job object configurado para matar a todos sus miembros en cuanto
/// se cierre su último handle. Ver el comentario en `Service::spawn`.
#[cfg(windows)]
fn create_kill_on_close_job() -> Result<KillOnCloseJob, String> {
    use windows_sys::Win32::System::JobObjects::{
        CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            return Err(format!(
                "no se pudo crear el job object: {}",
                std::io::Error::last_os_error()
            ));
        }

        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;

        let ok = SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const _,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        if ok == 0 {
            let err = std::io::Error::last_os_error();
            windows_sys::Win32::Foundation::CloseHandle(job);
            return Err(format!("no se pudo configurar el job object: {err}"));
        }

        Ok(KillOnCloseJob(job))
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
