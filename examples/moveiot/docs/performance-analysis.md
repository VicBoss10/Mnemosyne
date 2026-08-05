# Análisis de Rendimiento — MOVE Dashboard

Métricas medidas con Chrome DevTools MCP (Performance Trace) sobre `https://moveiot.online`.
Sin throttling de CPU ni red (condiciones óptimas de laboratorio).

**Umbrales Core Web Vitals de referencia:**
- LCP: ✅ Good < 2.5s | ⚠️ Needs improvement 2.5–4s | ❌ Poor > 4s
- CLS: ✅ Good < 0.1 | ⚠️ Needs improvement 0.1–0.25 | ❌ Poor > 0.25

---

## Dashboard Principal `/dashboard`

| Métrica | Valor | Estado |
|---|---|---|
| LCP | 1,462ms | ✅ Good |
| CLS | — | — |

### Hallazgos

**Bottleneck principal — Google Maps (cadena de dependencias de red):**
La carga de Google Maps genera una cadena de peticiones en serie que bloquea el render del elemento LCP. El mapa es el componente más pesado del dashboard.

**ForcedReflow por Google Maps controls.js:**
El script `controls.js` de Google Maps provoca un reflow forzado de ~36ms en el hilo principal (layout thrashing). Ocurre porque el script lee propiedades de layout (`offsetWidth`, `clientHeight`) y luego escribe estilos sin separar las operaciones.

### Cambios sugeridos

| # | Cambio | Impacto en LCP | Prioridad |
|---|---|---|---|
| 1 | Cargar Google Maps con `loading="lazy"` o diferir su inicialización hasta que el mapa sea visible (Intersection Observer) | Alto — elimina el mapa de la ruta crítica | Media |
| 2 | Añadir `<link rel="preconnect" href="https://maps.googleapis.com">` en el `index.html` | Medio — reduce TTFB de las peticiones de Maps | Baja |

> **Nota:** El LCP de 1,462ms sigue siendo "Good" según Google. Estos cambios son de optimización, no corrección urgente.

---

## Módulo Ambiente

### Resultados por página

| Página | LCP | CLS | Estado general |
|---|---|---|---|
| `/environment/humidity` | 544ms | 0.08 | ✅ Muy bueno |
| `/environment/co2` | 558ms | 0.02 | ✅ Excelente |
| `/environment/particles` | 575ms | **0.26** | ⚠️→❌ CLS crítico |
| `/environment/temperature` | 597ms | 0.08 | ✅ Bueno |
| `/environment/history` | 608ms | 0.08 | ✅ Bueno |
| `/environment/gases` | 614ms | **0.24** | ⚠️ CLS problemático |

### Hallazgos

**LCP:** Todas las páginas por debajo de 650ms — excelente en todas sin excepción. El patrón es consistente: TTFB ~150ms + render delay ~430ms. El render delay es el tiempo que tarda Angular en inicializar el componente y Chart.js en pintar el primer frame.

**CLS — problema en Gases y Partículas:**
Las páginas con más charts o charts de mayor tamaño (Gases, Partículas) sufren layout shifts significativos durante la carga. El shift ocurre porque los contenedores de Chart.js no tienen altura reservada: el DOM los inserta con altura 0 y Chart.js los expande al recibir los datos de la API, desplazando el contenido inferior.

Las páginas CO₂, Temperatura, Humedad e Histórico tienen el mismo patrón de charts pero CLS mucho menor, lo que indica que sus contenedores son más pequeños o tienen menos charts simultáneos.

### Cambios sugeridos

| # | Cambio | Impacto en CLS | Prioridad |
|---|---|---|---|
| 1 | Añadir `min-height` fijo a los contenedores de chart en los componentes `gases` y `particles` | Alto — elimina el layout shift al reservar espacio antes de que Chart.js pinte | **Alta** (valor 0.26 roza "Poor") |
| 2 | Aplicar el mismo `min-height` preventivo a Temperatura, Histórico y Humedad (CLS 0.08) | Medio — los lleva a CLS ~0 | Baja |

**Implementación para Gases y Partículas** (cambio de ~2 líneas de CSS):
```css
/* En el CSS del componente o en styles.scss */
.chart-container {
  min-height: 300px; /* ajustar a la altura real del chart */
}
```

> **Contexto de severidad:** Al ser un dashboard interno con usuarios autenticados, el impacto en SEO es nulo. El CLS de 0.26 es un problema de pulido visual, no funcional. Recomendado aplicar si hay tiempo, no es bloqueante.

---

## Histórico Ambiental — Análisis funcional con filtros activos

> Análisis realizado con filtro de fecha 11/05/2026 (inicio y fin), alternando todos los parámetros manualmente.

### Comportamiento del gráfico por parámetro (tab buttons)

El cambio de parámetro mediante los botones de tab (CO₂, Temperatura, Humedad, PM2.5, PM10, Gases) es **instantáneo** — no lanza una nueva petición al backend. Los datos de todos los parámetros se cargan en una sola llamada al aplicar el filtro y se mantienen en memoria. Buen diseño.

| Parámetro | Promedio | Máximo | Mínimo | Desv. Est. | Observación |
|---|---|---|---|---|---|
| CO₂ | 515.56 ppm | 832 | 343.3 | 108 | Alta variabilidad intradiaria |
| Temperatura | 20.86 °C | 21.3 | 14.1 | 0.64 | Muy estable — sensor funcionando bien |
| Humedad | 64.55 % | 74.8 | 41.4 | 3.38 | Normal |
| PM2.5 | 12.23 µg/m³ | 35 | 2 | 9.66 | Desv/Prom ratio: 0.79 — muy variable |
| PM10 | 13.62 µg/m³ | 42.3 | 2 | 11.31 | Desv/Prom ratio: 0.83 — muy variable |
| Gases | 19.61 ppb | 87 | 0 | 17.74 | Desv/Prom ratio: 0.90 — altísima variabilidad, mínimo en 0 |

### Bug encontrado — Desincronización entre dropdown de parámetro y tab del gráfico

**Descripción:** Al seleccionar un parámetro concreto en el dropdown de filtros (ej. "CO₂") y pulsar "Aplicar Filtros", el gráfico no cambia su tab activo para reflejar el parámetro seleccionado. El tab del gráfico mantiene el último parámetro que el usuario pulsó manualmente, aunque el rango de valores sí se actualiza correctamente al rango del parámetro del dropdown.

**Comportamiento observado:**
- Dropdown: CO₂ seleccionado → Rango cambia a 300–2000 ✅
- Tab del gráfico: sigue en "Gases" (verde) ❌
- Métricas mostradas: siguen siendo las de Gases ❌

**Impacto:** Confusión para el usuario — el dropdown y el gráfico muestran parámetros distintos simultáneamente.

**Causa probable:** El componente del gráfico tiene su propio estado local para el tab activo (`selectedTab`) que no se sincroniza con el valor del dropdown al aplicar filtros. El handler de "Aplicar Filtros" actualiza los datos y el rango pero no llama al método que cambia el tab activo.

**Corrección sugerida:** Al aplicar filtros con un parámetro específico (distinto de "Todos"), forzar el tab del gráfico al parámetro correspondiente. Prioridad: **Media** — es un bug de UX, no bloquea el uso pero genera confusión.

### Tabla de datos — observaciones

- La tabla muestra **100 registros por carga** con botón "Ver más registros" — paginación lazy correcta.
- Los datos más recientes aparecen primero (orden descendente por fecha). Correcto.
- La tabla muestra **todos los parámetros** en columnas simultáneamente independientemente del parámetro seleccionado en el filtro — la tabla ignora el filtro de parámetro del dropdown, solo respeta el rango de fechas y el rango de valores. Esto puede ser intencional (vista completa) o puede confundir si el usuario espera que la tabla también filtre por parámetro.

### Valores llamativos en los datos del 11/05/2026

- CO₂ llega a **832 ppm** (referencia: exterior limpio ~420 ppm, interior normal <1000 ppm — valor elevado pero no alarmante)
- PM2.5 llega a **35 µg/m³** (OMS recomienda <15 µg/m³ como media anual — picos intradiarios pueden ser normales)
- Gases (CO) llega a **147 ppm** en tabla y mínimo 0 — rango muy amplio sugiere posibles lecturas ruidosas del sensor o eventos puntuales

---

## Patrón común a todo el módulo Ambiente

El **render delay de ~400–440ms** es consistente en todas las páginas. Este tiempo corresponde a:
1. Angular resolviendo las rutas lazy-loaded del módulo Ambiente
2. Petición a la API del backend para obtener los datos del sensor
3. Chart.js procesando y pintando los datos

No es optimizable fácilmente sin cambios arquitecturales (SSR, caché de datos en el cliente entre navegaciones). Para el contexto actual es aceptable.

---

---

## Módulo Vehículos

### Resultados por página

| Página | LCP | CLS | Estado |
|---|---|---|---|
| `/vehicles/detected` | 616ms | 0.08 | ✅ Bueno |
| `/vehicles/stats` | **1,398ms** | 0.00 | ⚠️ LCP elevado |

**Vehículos Detectados** sigue el patrón habitual del dashboard (TTFB 172ms + render delay 444ms).

**Estadísticas** tiene un render delay de **1,257ms** — el más alto de todos los módulos excepto el Dashboard principal con Google Maps. Probable causa: múltiples peticiones al backend en serie para construir las estadísticas agregadas (por tipo, por hora, por día) antes de poder pintar el contenido. CLS perfecto (0.00) porque el contenido no aparece hasta que todos los datos están listos.

### Cambio sugerido — Estadísticas

| # | Cambio | Impacto en LCP | Prioridad |
|---|---|---|---|
| 1 | Mostrar skeleton/placeholder mientras cargan las estadísticas, y lanzar las peticiones en paralelo (`forkJoin`) si no lo están ya | Alto — reduce render delay percibido y puede bajar LCP si alguna petición es independiente | Media |

---

## Módulo Vehículos — Vehículos Detectados `/dashboard/vehicles/detected`

> Nota: se realizó también inspección funcional con filtros activos.

### Distribución de detecciones del 11/05/2026

| Tipo | Detecciones | % | Observación |
|---|---|---|---|
| Auto | 3839 | 96% | Dominante absoluto |
| Camión | 111 | 3% | Segundo más detectado |
| Bus | 37 | 1% | Bajo volumen |
| Moto | 5 | 0% | Casi insignificante |
| Bicicleta | 0 | 0% | Sin detecciones ese día |

El tráfico se concentra en un pico de 4–5 horas (Hora -5 a Hora -1), con la hora de máximo tráfico siendo Hora -4 con 1466 detecciones de Auto.

### Comportamiento de filtros

**Fecha + Tipo de vehículo:** Funciona correctamente. Cada tipo filtra sus datos y actualiza métricas y gráfico. El estado vacío (Bicicleta = 0 detecciones) se maneja bien con mensaje y icono apropiados.

**Ubicación (Plaza 2):** El filtro de ubicación funciona pero solo existe una ubicación activa ("Plaza 2") — los 3992 resultados son idénticos filtrando por "Todas las ubicaciones" o por "Plaza 2" explícitamente. Esto es esperable dado que solo hay un punto de monitoreo desplegado.

### Hallazgos y bugs

**1. Leyenda del gráfico no se adapta al filtro de tipo — UX menor**
Al filtrar por un tipo concreto (ej. "Bus"), la leyenda del gráfico sigue mostrando todos los tipos (Auto, Bus, Moto, Bicicleta, Camión) aunque solo haya datos de uno. No es un bug funcional pero puede confundir. Prioridad: **Baja**.

**2. "Tasa de Detección (por día)" = Total de Detecciones — métrica redundante**
La tarjeta "Tasa de Detección (por día)" siempre muestra el mismo número que "Total de Detecciones" (ej. 3992 = 3992). Esto es porque el rango de fechas es de un solo día. Si el rango fuera multiday, la tasa debería ser Total/días. Hay que verificar si el cálculo es correcto con rangos de más de un día o si es un alias directo. Prioridad: **Media** — puede ser un error de cálculo o de diseño de la métrica.

**3. Nomenclatura inconsistente — "Camion" vs "Camión"**
En la tabla de detecciones el badge muestra "Camion" (sin tilde) mientras que en el dropdown y la leyenda aparece "Camión" (con tilde). Inconsistencia de datos entre el valor almacenado en BD y el label de la UI. Prioridad: **Baja** — cosmético pero indica que los valores del enum en BD no están normalizados.

### Cambios sugeridos

| # | Cambio | Prioridad |
|---|---|---|
| 1 | Verificar el cálculo de "Tasa de Detección (por día)" con rangos multiday | Media |
| 2 | Ocultar de la leyenda del gráfico los tipos sin datos cuando hay filtro activo | Baja |
| 3 | Normalizar el valor "Camion" → "Camión" en la base de datos o en el mapeo del backend | Baja |

---

## Módulo Cámara

### Resultados por página

| Página | LCP | CLS | Estado |
|---|---|---|---|
| `/cameras/model-status` | 583ms | 0.01 | ✅ Excelente |
| `/cameras/streaming` | 598ms | 0.00 | ✅ Excelente |

Ambas páginas siguen el patrón habitual: TTFB ~144ms + render delay ~440ms. CLS prácticamente nulo en las dos. Sin anomalías de rendimiento.

### Funcionamiento — Streaming

El flujo de streaming funciona correctamente de extremo a extremo:

1. Seleccionar cámara en la lista → aparece panel con badges (YOUTUBE, ACTIVE), SESSION ID y botón "Ver Stream"; "DETECCIÓN ACTIVA" cambia a SÍ
2. Pulsar "Ver Stream" → el área principal carga el feed MJPEG vía `<img src="https://api.moveiot.online/streams/feed/<session-id>">` con la imagen en vivo de la cámara y la línea de detección superpuesta
3. El feed muestra timestamp en tiempo real y overlay de detección activo

**Estado del Modelo** muestra correctamente: modelo En Ejecución, framework activo, lista de cámaras con toggle Activar/Desactivar funcional. Sin hallazgos de rendimiento ni funcionales relevantes en este módulo.

---

---

## Módulo Ubicaciones

### Resultados por página

| Página | LCP | CLS | Estado |
|---|---|---|---|
| `/locations/register-location` | 1,157ms | 0.01 | ✅ Good |
| `/locations/monitoring` | 1,237ms | 0.05 | ✅ Good |
| `/locations/history` | 627ms | 0.00 | ✅ Excelente |

**Registrar Ubicación y Puntos de Monitoreo** tienen LCP más alto que el resto del dashboard (~1,1–1,2s) pero siguen en rango "Good". El desglose muestra un **Load delay** de ~700–800ms — el tiempo que tarda Google Maps en ser descubierto y cargado antes de poder pintar el elemento LCP (que es el mapa en ambos casos). El patrón es idéntico al Dashboard principal.

**Historial por Ubicación** sigue el patrón habitual del módulo sin mapa: 627ms ✅.

### Funcionamiento — Flujo de registro

El flujo Registrar → Verificar en Monitoring es claro e intuitivo:

1. Campo de descripción con placeholder y límite de caracteres visible (3-255)
2. Instrucción "Clickea en el mapa para seleccionar la ubicación" clara y directa
3. Al hacer clic en el mapa aparece pin rojo y las coordenadas debajo del mapa ("Ubicación seleccionada: Lat / Lon") — feedback inmediato
4. Botón "Registrar Ubicación" permanece `disabled` hasta que hay descripción + coordenadas — validación correcta
5. Al registrar: toast "Success" + redirección automática a Puntos de Monitoreo
6. La nueva ubicación aparece en mapa y tabla inmediatamente
7. Eliminación con diálogo de confirmación modal y actualización instantánea del mapa

### Cambio sugerido — Rendimiento

| # | Cambio | Impacto en LCP | Prioridad |
|---|---|---|---|
| 1 | Aplicar `loading="lazy"` o Intersection Observer al mapa de Google Maps en Registrar Ubicación y Puntos de Monitoreo (mismo fix del Dashboard) | Medio — reduce Load delay ~700ms | Baja (LCP sigue en "Good") |

---

## Módulo Dispositivos

### Resultados por página

| Página | LCP | CLS | Estado |
|---|---|---|---|
| `/devices/register-device` | 567ms | 0.00 | ✅ Excelente |
| `/devices/device-status` | 561ms | 0.00 | ✅ Excelente |

Ambas páginas siguen el patrón habitual: TTFB ~138ms + render delay ~425ms. CLS perfecto (0.00) en las dos. Sin anomalías de rendimiento.

### Funcionamiento — Flujo de registro (Cámara/Video)

1. Seleccionar tipo "Cámara/Video" despliega el formulario con campos: Nombre (3-255 chars), Ubicación (dropdown), Estado Inicial (Activo/Inactivo/Fallando), Tipo de Streaming (RTSP/HTTP/YouTube), URL del Stream
2. Botón "Registrar Dispositivo" permanece `disabled` hasta completar todos los campos — validación correcta
3. Al registrar: toast "Éxito: Cámara registrado(a) exitosamente. Para iniciar la detección dirígete a Cámara → Streaming." — claro e informativo con CTA de siguiente paso ✅
4. Redirección automática a `/devices/device-status` con el nuevo dispositivo visible inmediatamente ✅
5. Modal Editar carga todos los datos pre-poblados correctamente ✅
6. Eliminación con modal de confirmación que muestra el nombre del dispositivo en negrita + advertencia "Esta acción no se puede deshacer" + toast de confirmación ✅

### Hallazgos

**"Camara Autopista Ejemplo" sin tilde** — el nombre del dispositivo preexistente aparece sin tilde ("Camara" en lugar de "Cámara"). Consistente con el hallazgo en Vehículos ("Camion" sin tilde). Indica que estos dispositivos se registraron antes sin normalización de tildes. Prioridad: **Baja** — cosmético.

---

## Módulo Análisis

### Resultados por página

| Página | LCP | CLS | Estado |
|---|---|---|---|
| `/analysis/time-series` | 628ms | 0.00 | ✅ Bueno |
| `/analysis/correlation-matrix` | 576ms | 0.01 | ✅ Bueno |
| `/analysis/lags` | 471ms | 0.01 | ✅ Excelente |
| `/analysis/by-location` | 473ms | 0.01 | ✅ Excelente |
| `/analysis/export-data` | 609ms | 0.00 | ✅ Bueno |

**Time Series** sigue patrón habitual (TTFB 137ms + render delay 490ms).

**Correlation Matrix, Lags y By Location** tienen un patrón diferente: TTFB ~139–256ms + Load delay ~288–303ms + Render delay ~27–29ms. El Load delay es un bottleneck común pero LCP sigue en rango "Good" (<2.5s).

**Export Data** sigue patrón habitual (TTFB 139ms + render delay 469ms).

CLS excelente en todas (máx 0.01). Sin anomalías de rendimiento críticas.

---

## Módulo Configuración

### Resultados por página

| Página | LCP | CLS | Estado |
|---|---|---|---|
| `/configuration/users` | 802ms | 0.01 | ✅ Good |
| `/configuration/alert-thresholds` | 617ms | 0.00 | ✅ Bueno |
| `/configuration/delete-data` | 599ms | 0.00 | ✅ Bueno |

**Usuarios** tiene LCP más elevado (802ms, TTFB 139ms + render delay 663ms) — render delay incrementado, probable por más datos en tabla o lógica adicional en el componente. Aún dentro de "Good" (<2.5s).

**Alert Thresholds y Delete Data** siguen patrón habitual (TTFB 139–144ms + render delay 455–476ms).

CLS excelente en todas (máx 0.01). Sin anomalías críticas.

---

## Módulo Logs del Sistema

### Resultados por página

| Página | LCP | CLS | Estado |
|---|---|---|---|
| `/system-logs/events` | 477ms | 0.01 | ✅ Excelente |
| `/system-logs/errors` | 481ms | 0.01 | ✅ Excelente |

Ambas páginas: TTFB ~140–147ms + Load delay ~289–293ms + Render delay ~41–42ms. Load delay es el bottleneck pero LCP sigue excelente (<2.5s).

CLS excelente (0.01). Sin anomalías de rendimiento.

---

## Resumen General — Módulos Restantes

Todos los módulos analizados (Dispositivos, Análisis, Configuración, Logs del Sistema) exhiben un rendimiento consistentemente bueno:

- **LCP:** Rango 471–802ms — todos en "Good" (<2.5s)
- **CLS:** Máximo 0.01 — excelente en todos
- **Patrón principal:** TTFB ~137–147ms + (Render delay 27–663ms) o (Load delay 288–303ms)

**Hallazgos clave:**
1. El Load delay en páginas de Análisis (Correlation Matrix, Lags, By Location) y Logs sugiere peticiones a API asincrónicas antes del render
2. El Render delay elevado en `/configuration/users` (663ms) podría beneficiarse de skeleton loaders o lazy rendering
3. No hay problemas críticos de CLS — layouts bien reservados

---

*Última actualización: 2026-05-11*
*Herramienta: Chrome DevTools MCP + Performance Trace + inspección interactiva (sin throttling)*
