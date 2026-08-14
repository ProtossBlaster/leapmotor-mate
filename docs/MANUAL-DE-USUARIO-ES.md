# LeapMotor Mate — Manual de usuario

> **Versión de Mate:** v3.14.0 · **Idioma:** Español
> Este manual está escrito para quien *usa* Mate, no para quien lo desarrolla. Explica cómo
> configurarlo desde cero y qué hace cada página. Para los detalles técnicos internos está `ARCHITECTURE.md`.

---

## Índice

1. [Qué es Mate (y qué no es)](#1-qué-es-mate-y-qué-no-es)
2. [Antes de empezar: los requisitos](#2-antes-de-empezar-los-requisitos)
3. [Instalación](#3-instalación)
4. [Primer arranque: el asistente de configuración](#4-primer-arranque-el-asistente-de-configuración)
5. [Conocer la interfaz](#5-conocer-la-interfaz)
6. [Las páginas, una a una](#6-las-páginas-una-a-una)
   - [Resumen](#resumen) · [Trayectos](#trayectos) · [Mapa](#mapa) · [Cargas](#cargas)
   - [Precios de la carga](#precios-de-la-carga) · [Estadísticas](#estadísticas) · [Informe mensual](#informe-mensual)
   - [Salud de la batería](#salud-de-la-batería) · [Mantenimiento](#mantenimiento) · [Comandos](#comandos)
   - [Programación](#programación) · [Preparar el coche](#preparar-el-coche)
   - [Navegación](#navegación) · [Vehículo](#vehículo) · [Wallbox](#wallbox)
7. [Ajustes](#7-ajustes)
8. [Las integraciones en detalle (Wallbox, ABRP, MQTT)](#8-las-integraciones-en-detalle)
9. [Modo demostración](#9-modo-demostración)
10. [Preguntas frecuentes y resolución de problemas](#10-preguntas-frecuentes-y-resolución-de-problemas)
11. [Glosario](#11-glosario)

---

## 1. Qué es Mate (y qué no es)

**LeapMotor Mate** es una aplicación que instalas tú mismo (autoalojada) y que hace de «compañera»
de tu coche eléctrico Leapmotor. Se conecta a la **nube de Leapmotor** (la misma con la que habla la
app oficial), lee el estado del coche y, a partir de esos datos, reconstruye sola:

- tus **trayectos** (distancia, duración, consumo, recuperación por frenada regenerativa);
- tus **cargas** (energía, potencia, tipo, coste);
- los **costes** y el **consumo** a lo largo del tiempo;
- la **salud de la batería** y los **plazos de mantenimiento**.

Además te permite **enviar comandos a distancia** (cierre, climatización, preparación del vehículo,
programaciones…) y, si quieres, integrar los datos con **Home Assistant** (mediante MQTT), con
**A Better Routeplanner (ABRP)** y con tu **wallbox**.

**Lo que NO hace / límites importantes:**

- **No habla directamente con el coche.** Todo pasa por la nube de Leapmotor. Cuando Mate «consulta»
  la nube (polling) lee el **último estado conocido**: *no* despierta al coche y *no* descarga la
  batería. Es una operación segura y barata.
- **Solo coches 100 % eléctricos (BEV).** Los modelos compatibles son **T03, B05, B10, C10** en sus
  versiones eléctricas. Las versiones **REEV** (con extensor de autonomía de gasolina) **no** están
  soportadas: los cálculos de energía, consumo y coste usarían la capacidad de batería equivocada y
  saldrían distorsionados.
- **Solo la nube europea (Leapmotor International / Stellantis).** Las cuentas registradas en
  servidores de otras regiones (por ejemplo China) no pueden iniciar sesión. Fuera de Europa, hoy por
  hoy Mate no se puede usar.
- **No es una herramienta de contabilidad.** Estima el coste *a partir de la telemetría*; no lleva el
  control de métodos de pago, facturas ni suscripciones a redes de recarga.

---

## 2. Antes de empezar: los requisitos

Para configurar Mate necesitas tres cosas:

1. **Una cuenta de Leapmotor dedicada a Mate.** ⚠️ **Muy importante.** Crea (o reserva) una cuenta de
   Leapmotor que uses **solo** para Mate. Leapmotor permite muy pocas sesiones simultáneas por
   cuenta: si esa misma cuenta está también conectada en la app oficial, en otra integración o en una
   segunda instancia de Mate, los clientes se van «echando» la sesión unos a otros. El resultado es
   una ráfaga de *«Invalid token»* y de reinicios de sesión, el coche que se queda **sin conexión** y
   **datos perdidos** (trayectos y cargas no registrados). Es la causa número uno de los problemas que
   nos llegan. *Solución:* una cuenta secundaria con una **contraseña usada solo en Mate**.

2. **El certificado de la app de Leapmotor** (`app.crt` + `app.key`). Es un certificado que es **el
   mismo para todo el mundo** (pertenece a la app, no a tu cuenta) y hace falta para hablar con la
   nube. Se descarga de un repositorio público — el asistente te da el enlace directo
   ([github.com/markoceri/leapmotor-certs](https://github.com/markoceri/leapmotor-certs)).

3. **Correo, contraseña y el PIN de operaciones de la cuenta.** El **PIN de 4 dígitos** es el mismo
   que usas en la app oficial para autorizar los comandos a distancia (cierre, climatización…).

> 💡 ¿Solo quieres echar un vistazo sin configurar nada? Sáltatelo todo y usa el
> **[modo demostración](#9-modo-demostración)**: Mate arranca con un mes de datos falsos pero
> realistas, sin coche y sin cuenta.

---

## 3. Instalación

Mate funciona igual en tres entornos (la interfaz es idéntica):

- **Como complemento de Home Assistant** — la forma más fácil si ya tienes Home Assistant. Añades el
  repositorio de complementos, instalas «LeapMotor Mate» y lo abres desde el menú lateral de HA
  (ingress). En este caso Mate también puede leer tu **wallbox** directamente desde Home Assistant.
- **Como contenedor Docker independiente** (por ejemplo en un NAS) — con `docker-compose`. En este
  caso se accede a la aplicación desde el navegador por el **puerto 4000**
  (`http://DIRECCIÓN-DE-TU-SERVIDOR:4000`).
- **Como aplicación de escritorio** — [**MateDesktop**](https://github.com/ProtossBlaster/MateDesktop)
  es el mismo Mate empaquetado para **macOS y Windows**, para quien no tiene ni Home Assistant ni
  Docker: lo descargas, lo abres y tienes el mismo asistente de configuración. En Windows se
  distribuye **dentro de un `.zip`** — descomprímelo antes y luego ejecuta el instalador, porque un
  `.exe` suelto descargado de internet todavía no tiene reputación ante SmartScreen y lo bloquean al
  entrar.

Las instrucciones de instalación paso a paso (repositorio, compose, etc.) están en el **README** del
proyecto y en la página de **Docker Hub**. Una vez en marcha, el *primer inicio de sesión* es igual en
ambos casos y se describe más abajo.

> 📱 **En el móvil.** Mate no es una app de móvil y no puede serlo — tiene que consultar durante años,
> y un teléfono suspende lo que corre en segundo plano. Pero puedes ponerlo **en la pantalla de
> inicio**: abre Mate en el navegador del móvil y luego *Compartir → Añadir a pantalla de inicio* en
> iPhone, o *⋮ → Añadir a pantalla de inicio* en Android. Coge el icono propio de Mate y se abre a
> pantalla completa, sin barra de direcciones ni barra de herramientas — unos 110 px de pantalla
> recuperados. Sigue siendo un acceso directo al servidor que tienes tú: con el servidor apagado, no
> abre nada.

> 🔒 **Copia de seguridad.** Todos los datos de Mate viven en una carpeta persistente (`/data`): la
> base de datos, la clave de cifrado de los secretos (`secret.key`) y el certificado. Si haces una
> copia, **guarda la base de datos junto con su `secret.key`** — sin la clave, las contraseñas y los
> tokens guardados ya no se pueden leer. Desde la página de Ajustes puedes descargar una copia de la
> base de datos cuando quieras.
> Si algún día restauras una base de datos **sin** su clave, ahora Mate lo dice en el registro con
> nombre y apellidos — qué secretos no puede leer y qué hacer — en vez de fallar después como si
> fuera un error de acceso. Los trayectos, las cargas y los costes no están cifrados y siempre
> vuelven.

---

## 4. Primer arranque: el asistente de configuración

En el primer inicio de sesión Mate muestra un **asistente** (procedimiento guiado). Arriba puedes
elegir el idioma (🇪🇸 Español). Después:

### Paso 0 — Elige por dónde empezar

Dos botones:

- **▶ Configurar mi coche** — la configuración de verdad (sigue abajo).
- **🧪 Probar la demostración** — entra en el modo demostración con datos falsos. Puedes salir cuando
  quieras.

### Paso 1 — Certificado de la app

Mate te pide el certificado TLS de la app de Leapmotor. Tienes dos maneras:

- **Subir los archivos** `app.crt` y `app.key` (el modo por defecto), o
- **Pegar el texto PEM** de los dos archivos (el botón *«Pegar el texto PEM en su lugar»*).

Descárgalos del enlace que aparece, súbelos y pulsa **Guardar el certificado**. Este paso solo aparece
si el certificado no viene ya dentro de la imagen.

### Paso 2 — Iniciar sesión con la cuenta

Escribe:

- **Correo de la cuenta de Leapmotor**
- **Contraseña**
- **PIN de operaciones** (4 dígitos)

> ⚠️ Aquí Mate te recuerda que uses **una cuenta dedicada solo a Mate** (ver
> [requisitos](#2-antes-de-empezar-los-requisitos)).

Pulsa **🔍 Detectar mi coche**. Mate comprueba las credenciales y lee de la nube el **modelo y el
número de bastidor (VIN)**. Si todo va bien verás una tarjeta «Coche detectado» que muestra
`Leapmotor <modelo> · VIN ···xxxxxx`.

### Paso 3 — Batería

Según el modelo:

- si la versión europea tiene **una sola variante de batería**, Mate la pone sola — hoy solo el T03
  (36,0 kWh);
- si hay **varias variantes** — B10 y B05 (Pro 55,0 kWh / Pro Max 65,0), C10 (RWD 67,0 / AWD 81,9) —
  **eliges la tuya**: la nube no dice cuál lleva tu coche, así que Mate no puede saberlo;
- si la detección falla, puedes **escribir la capacidad a mano** (en kWh).

> La capacidad que se muestra es la **útil/neta** (la que de verdad importa para el consumo y los
> costes) y siempre se puede corregir después, desde Ajustes → Batería.
> A su lado está la **referencia del estado de salud** — la capacidad de nuevo con la que se compara
> la salud de la batería. Mate la captura la primera vez que guardas la capacidad y después no la
> toca, para que adoptar una cifra medida (ya envejecida) nunca pueda devolver tu salud al ~100 % y
> esconder el envejecimiento. Si se capturó a partir del número equivocado, la salud puede salir por
> encima del 100 %: corrígela en el mismo sitio.

> **Si un valor predeterminado de Mate ha quedado desmentido 🆕**, Ajustes → Batería lo dice ahí
> mismo y ofrece la cifra corregida con un botón — nunca reescribe el número a tus espaldas. Hoy
> se trata del **C10 RWD**: 69,9 kWh es el valor nominal, y las cargas reales dan una batería
> utilizable de 67,0.

### Paso 4 — Conectar

Pulsa **Conectar y empezar**. Mate guarda la configuración, se conecta y te lleva al **Resumen**.
A partir de ese momento el «poller» empieza a recoger datos en segundo plano: los primeros trayectos
y las primeras cargas irán apareciendo a medida que conduzcas y cargues.

---

## 5. Conocer la interfaz

La interfaz se compone de:

- **Menú lateral (sidebar)** — la lista de páginas (ver más abajo). En una pantalla pequeña se abre
  con el icono ☰.
- **Cabecera** — el título de la página, el aviso de **actualización disponible** (↑ vX.Y.Z) si lo
  hay, y el botón **🔄 Actualizar**.
- **Botón Actualizar** — fuerza una lectura inmediata del estado del coche sin esperar al ciclo
  automático. Muy útil después de enviar un comando.
- **Franja «nunca configurado» 🆕** — una franja naranja en la parte superior de cada página cuando
  un coche ha llegado a Mate **por su cuenta**, sin pasar por el asistente: le ocurre al **segundo
  coche** añadido a una instalación donde el acceso ya estaba hecho. Mientras nadie responda por él, ese
  coche usa la **batería predeterminada de su modelo**, y eso distorsiona sus kWh, su precio por kWh y
  su consumo. El botón abre el asistente, donde se eligen la batería y el PIN.

Al final del menú están **⚙️ Ajustes** y **🚪 Cerrar sesión**, este último *solo si has puesto una
contraseña de acceso* — y lo único que hace es cerrar esa sesión de contraseña. Si no la has puesto no
aparece, porque no hay nada que cerrar.

**Para cambiar el PIN del coche 🆕** — si lo cambias en el coche, no hace falta desvincular nada: ve a
**Ajustes → Vehículo** y, bajo la dirección de la cuenta, encontrarás **PIN de operaciones**. Se
escribe dos veces, con un ojo para volver a leerlo, y surte efecto al momento — tanto para los
comandos de la página como para los que llegan desde Home Assistant. Lo pidió **@alextchao** (#225).

**Si dos Leapmotor comparten tu cuenta 🆕** — aparece un **selector de coche** en la cabecera, al lado
de la insignia del modelo. Solo está a partir del segundo coche: con un solo Leapmotor no cambia
absolutamente nada. Eliges un coche y todo lo sigue — el Resumen, las Estadísticas, los trayectos, las
cargas, el informe mensual, los comandos que ese coche permite y sus entidades de Home Assistant. Tu
elección se recuerda.

Los ajustes siguen siendo comunes, porque bajo un mismo techo rara vez difieren: precios, moneda, zona
horaria, ubicación de casa. Lo que es del coche se queda con el coche — su capacidad de batería, su
**PIN de operaciones**, su **token de A Better Route Planner**, si es un extensor de autonomía, qué se
le puede mandar hacer y qué sensores tiene de verdad. Los dos coches los lleva **un solo Mate**: un
poller, una base de datos, una sesión contra la nube de Leapmotor, en vez de dos instalaciones
echándose la sesión la una a la otra.

**Para desvincular la cuenta de Leapmotor** — que es una cosa completamente distinta — ve a **Ajustes
→ Vehículo → 🔓 Cerrar sesión**. Eso borra las credenciales guardadas y vuelve a abrir el asistente de
configuración; tu certificado, tus trayectos y tus cargas se quedan (@JoseRMorales, #223, que fue a
buscar lo primero y quería lo segundo).

Muchas páginas **se actualizan solas** más o menos cada 30 segundos, así que los valores «en directo»
(estado, carga en marcha…) se mantienen frescos sin recargar la página.

**El idioma, la moneda y las unidades** se cambian desde *Ajustes → 🌍 Idioma y moneda*:

- **Idioma:** English, Italiano, Français, Deutsch, Polski, Nederlands, Português, Español.
  *(Un manual escrito como este existe en inglés, italiano, francés, alemán y español.)*
- **Moneda:** para los costes (€, £, …).
- **Unidades:** métricas (km, °C) o imperiales RU/EE. UU. (millas, °F). Los datos se guardan siempre en
  km/°C; lo único que cambia es cómo se **muestran**.

---

## 6. Las páginas, una a una

El orden de abajo es el mismo que el del menú lateral.

### Resumen
**(menú: Resumen)** — La portada. Arriba hay una **tarjeta principal** con la imagen del coche y su
estado en directo:

- **estado de carga (SoC)** y autonomía estimada;
- **iconos de estado** que cambian de color: cierre (verde = cerrado, ámbar = abierto), maletero (rojo
  si está abierto), ventanillas (morado si están abiertas), climatización, etc.;
- **comandos rápidos** (abrir/cerrar, localizar el coche…), que ya «saben» cuál es el estado actual;
- cuando el coche está **cargando**, una **animación** muestra el flujo de energía y una etiqueta con
  el tiempo estimado «hasta el X %» (X = el límite de carga que hayas puesto en el coche);
- una etiqueta **«Cable conectado / Carga completada»** cuando el cable está enchufado pero no está
  cargando de verdad. A su lado, si tienes una **carga programada**, aparece la franja del propio
  coche (por ejemplo **«Carga 01:50 – 12:00»**) — la respuesta a «el cable está puesto, ¿por qué no
  carga?».

Cuando el coche está **alimentando un aparato externo con el adaptador V2L (vehicle-to-load)**, el
Resumen muestra un **bloque V2L** con el **estado** (Activo / Inactivo), la **potencia instantánea** en
vatios — indicada **descontando los ~300 W de consumo propio del coche**, para que coincida con lo que
de verdad consume tu aparato — con una barra de 0 a 3500 W, y la **energía consumida en esta sesión**.
Se actualiza cada **10 s** mientras hay una sesión en marcha. Es de **solo lectura**: el V2L se
arranca **en el coche** (marcha en Park + un aparato conectado), no desde Mate. Es fiable a partir de
unos **42 W** (la resolución del propio sensor de corriente del coche — una carga minúscula de ~10 W
queda invisible).

Más abajo encontrarás miniestadísticas y un indicador de **«Respuesta del coche»** (un punto 🟢/🟡/🔴,
⚪ si no hay datos): resume con qué fiabilidad ha respondido el coche a los últimos comandos enviados.

#### Las tres temperaturas: habitáculo, consigna del A/A y batería
No todos los Leapmotor envían las tres. Mate distingue **tres situaciones diferentes**, porque
confundirlas produce números absurdos:

- **el sensor existe pero esta actualización no lo traía** → la fila se queda y muestra **«—»**;
- **el cero es una lectura real** (un paquete de baterías de verdad a 0 °C, en invierno) → Mate
  escribe **0 °C**, porque esa es justo la lectura que más importa;
- **el coche no envía nunca ese sensor** → la fila **no se muestra**, y la entidad correspondiente de
  Home Assistant **se elimina**.

El último caso está **medido, no deducido del modelo**: Mate solo lo afirma tras aproximadamente media
hora de actualizaciones en las que ese valor no llegó ni una sola vez — así que una instalación nueva
enseña todas las filas, y si un sensor empieza a responder la fila (y la entidad) **vuelve sola** en
unas horas.

Si usas la condición de temperatura de **Preparar el coche** («enfría solo por encima de 25 °C»), una
temperatura **desconocida** no dispara la preparación, y lo dice en el registro. Antes contaba como
0 °C, así que en un coche sin sensor de habitáculo la condición «por debajo de 5 °C» se cumplía en
**cada actualización, todo el año**.

### Trayectos
**(menú: Trayectos)** — La lista de tus recorridos, uno por recorrido. De cada trayecto ves
**distancia, duración, consumo (kWh/100 km), energía recuperada** al frenar y el **coste** estimado.

- Al hacer clic en un trayecto se abre el **detalle**, con la **traza GPS** sobre un mapa y los datos
  de ese trayecto concreto.
- **Un calendario y una búsqueda.** Los trayectos se recorren por **mes**; haz clic en un día para ver
  solo los de ese día, o usa la **búsqueda** con un intervalo de fechas, una distancia o una ventana
  de consumo para sacar un conjunto de todo el histórico.
- **Unir trayectos, desde el día que estás mirando.** Una parada lo bastante larga como para cerrar un
  recorrido puede partir un mismo viaje en dos filas. Abre un día y el botón **🔗** que hay junto a la
  fecha te ofrece las parejas de ese día que se pueden unir: un deslizador amplía lo que cuenta como
  una sola parada, ves la ruta combinada antes de confirmar, y es **reversible** en cualquier momento
  (Separar). También puedes **eliminar** un trayecto.
- Las paradas cortas (semáforos, atascos) **no** parten un trayecto: un recorrido sigue siendo una
  sola fila.
- **Un trayecto que la nube abandona termina cuando el coche habló por última vez.** Si el enlace se
  cae mientras conduces, Mate cierra el trayecto solo al cabo de media hora — pero lo fecha en la
  **última noticia real**, no en el momento en que se dio cuenta. Así la duración no contiene media
  hora de silencio y la velocidad media sigue siendo honrada.
- **Los kilómetros recorridos mientras el coche estaba sin contacto no van a ningún trayecto.** Cuando
  el enlace con la nube se cae, el coche sigue moviéndose pero Mate no lo ve; cuando el enlace vuelve,
  lo único que encuentra es un cuentakilómetros más adelantado. Ese salto puede contener el final de
  un recorrido, una parada y el principio de otro, y **nada dice cómo se reparte** — así que Mate no
  se lo atribuye a nadie. Una línea encima del calendario declara los kilómetros, la carga y el coste
  de ese mes, y la página de **Estadísticas** declara el total acumulado: *medidos, pero no
  atribuibles a un trayecto concreto — por eso quedan fuera de distancias, consumos y costes.*
  ⚠️ Por eso el total del propio Mate puede quedar por debajo del cuentakilómetros del coche: la
  diferencia es exactamente esa línea.
- **Altitud y temperatura exterior.** La nube de Leapmotor no da ninguna de las dos, así que unos
  minutos después de terminar un recorrido Mate consulta la traza GPS del trayecto contra
  [Open-Meteo](https://open-meteo.com) (gratis, sin clave, sin cuenta). El detalle gana entonces una
  **línea de altitud bajo el gráfico de SoC y velocidad**, los metros **subidos y bajados**, y la
  temperatura **a la salida y a la llegada** — no una media, para que una subida de valle a puerto
  muestre la caída real. Entre las dos explican buena parte del consumo de un recorrido: subir cuesta
  energía, el frío cuesta autonomía. Los trayectos registrados antes de que esto existiera tienen un
  botón **Calcular la altimetría**, y todo el conjunto se puede desactivar en Ajustes.
- **Consumo oficial desde la nube 🆕** — cuando está disponible, el **consumo, el rendimiento y el
  coste** de un trayecto salen de la **cifra oficial** de Leapmotor (el reparto real entre **marcha /
  climatización / otros**) en vez de solo de la estimación por % de batería. Justo después de un
  recorrido ves la estimación marcada como **⏳ provisional**; en cuanto la nube ha procesado los datos
  (normalmente unas decenas de minutos) se **sustituye sola** por la oficial y el **desglose** aparece
  en el detalle. Los trayectos antiguos tienen un botón **«Convertir con los datos oficiales»**. Si la
  nube no tiene los datos de un trayecto (pasa, en cualquier coche conectado), se queda la
  **estimación** — no es un error. **Siempre activo**, sin configurar nada.
  - **Se cuenta desde que el coche se ENCIENDE, no desde el recorrido 🆕** — la cifra oficial cubre
    todo el **encendido** (de arrancar a apagar), así que puede incluir tiempo con el coche encendido
    antes de que empezaras a moverte. Si **nunca apagas el coche entre dos trayectos** (paras, te
    quedas en Park, vuelves a arrancar), la nube los cuenta como **uno solo** — Mate te dice que
    **unas los dos trayectos** para obtener el consumo combinado real.
- **Tu nota + las etiquetas de conducción 🆕** (#107) — en el detalle de un trayecto puedes escribir
  una **nota libre** (tráfico, tiempo, tipo de carretera, lo que sea) y etiquetar el **modo de
  conducción** (Confort / Normal / Sport) y el **One-Pedal** (activado/desactivado) que usaste. Mate no
  puede leerlos del coche — Leapmotor no los manda a la nube — así que los pones tú a mano; ayudan a
  explicar por qué dos recorridos parecidos consumieron cosas distintas.

### Mapa
**(menú: Mapa)** — Todo lo que has recorrido, en un solo mapa. Está la posición actual del coche (si
los últimos datos de la nube no traen una posición GPS válida, Mate **conserva la última posición
válida** en vez de hacer desaparecer el mapa), y con ella:

- **La ruta de cada trayecto**, dibujada como una línea continua en vez de como puntos sueltos, y
  nunca unida entre dos trayectos distintos.
- **Un puente magenta discontinuo donde se perdió la señal.** Un túnel, una zona sin cobertura, un
  hipo de la nube — cuando el hueco entre dos puntos registrados es mucho mayor que el ritmo de
  muestreo de ese trayecto, Mate dibuja la unión **discontinua** en vez de continua. Una línea
  continua significa *el coche pasó de verdad por aquí*; una discontinua significa *lo perdimos aquí*,
  y la recta entre los dos extremos no es una carretera.
- **Sitios frecuentes**, como burbujas de tamaño proporcional a las veces que paras ahí, y **puntos de
  recarga** que has usado.
- **«Trayectos mostrados»**, una casilla en la fila de la leyenda. Un histórico largo deja el mapa
  convertido en una masa compacta de líneas superpuestas, así que puedes limitarlo a los N trayectos
  más recientes; **0 significa todos**, que es como empieza. Limitarlos hace además que cada ruta
  dibujada se ciña mejor a la carretera real, porque el presupuesto de dibujo se reparte entre menos
  trayectos.

### Cargas
**(menú: Cargas)** — La lista de cargas. De cada una: **energía añadida (kWh)**, **potencia máxima**,
**tipo** y **coste**, con los **€/kWh efectivos** bien a la vista. El tipo se clasifica con una
etiqueta:

- **La franja «por confirmar» te lleva hasta ella 🆕** (#240) — cuando una carga ha terminado sin
  tipo, aparece una franja arriba de la página. **Haz clic**: abre la carga en su propio día del
  calendario y la marca, en vez de dejarte a ti averiguar en qué día está.
- **Cuando una parte de la página no puede cargarse 🆕** — varios bloques de Mate se rellenan solos un
  instante después de abrir la página. Si uno falla, ahora **lo dice debajo de sí mismo**, con el
  error y un **Volver a intentarlo**, en lugar de dejar un hueco vacío sin explicación.
- **En casa** (tu wallbox **o un enchufe doméstico**), **AC** (corriente alterna pública), **DC
  rápida**, **HPC** (carga ultrarrápida) y **✎ Manual**.
- **«En casa» no significa wallbox.** *En casa* es dónde cargaste, no con qué cargaste — un enchufe
  del garaje también es una carga en casa. Importa por lo que se factura: con un contador de wallbox
  asignado (ver *Wallbox* más abajo), la carga se factura sobre la **energía que entregó el
  contador**; sin él, se factura sobre la **energía que llegó a la batería**, exactamente igual que
  una carga pública. Entre las dos está la pérdida en calor del propio cargador, normalmente del 10 al
  15 %.
- **✎ Manual**: para puntos de recarga públicos con tarifas complicadas (suscripciones, coste por
  sesión…) puedes **escribir a mano el total que pagaste de verdad**; ese valor sustituye a la
  estimación automática.
- **Los kWh del propio cargador 🆕** (#222) — en un cargador público Mate **no tiene contador**: lee
  solo lo que entró en la batería, mientras que el cargador te factura lo que salió del suyo. Puedes
  escribir esa cifra: en la tarjeta de la carga, bajo las tres casillas, hay un **✎**; el campo **solo
  se abre si lo abres tú** y **siempre está vacío** — así un clic despistado no cambia nada, y darle a
  Aceptar con el campo vacío lo deja todo como estaba. *Quitar* deshace un número mal puesto. A partir
  de ahí **le pone precio a la carga**, exactamente igual que hace en casa un contador de wallbox, y
  muestra el **rendimiento** (cuánto convirtió en calor el cargador de a bordo). La energía que
  declara Mate sigue siendo la **medida en la batería**.
- **Qué se cuenta y qué no 🆕** — una carga aparece en estas comparaciones solo cuando tiene **las
  dos** cifras, la del contador y la de la batería. Una sesión con una sola llevaría la proporción por
  encima del 100 %, cosa que ningún cargador puede hacer. **Las cargas todavía en marcha quedan
  fuera**: una sesión que aún está llegando no tiene un total que comparar, y se suma a los totales
  cuando termina.
- **El mes dice las dos 🆕** — encima del calendario: *«154,93 kWh entregados · 142,57 en la
  batería»*. La primera es lo que salió de los contadores (el wallbox, o los kWh que escribiste tú); la
  segunda es lo que llegó al paquete. Entre ambas está la pérdida de conversión que pagas.
- Las cargas ocurridas con el coche apagado o sin conexión también se **reconstruyen**, a partir del
  salto del estado de carga.
- **Tu nota 🆕** (#107) — cada carga tiene una **nota libre** (justo encima de *Eliminar la carga*)
  para lo que los números no recogen: dónde estaba el punto, si había sombra o techo, si es fiable,
  cómo está el aparcamiento, el tiempo que hacía, cualquier comentario personal.
- **El cuentakilómetros de la carga 🆕** (#237) — cada sesión lleva ahora **lo que marcaba el
  cuentakilómetros al empezar**. Mate lo escribe en todo lo que ve, y lo recuperó una vez de las
  cargas que ya estaban en el archivo. En una carga que **escribes tú** hay una casilla
  *Cuentakilómetros*: es la única manera de que una sesión anterior a la existencia de Mate lleve
  kilómetros — nada de aquellos días puede aportarlos. Se escribe en **tu** unidad (km o millas).
- **Cuánto anduvo el coche entre dos cargas 🆕** (#237) — bajo la carga: *«🛣 122 km desde la carga
  anterior»*, según el cuentakilómetros del propio coche. Solo aparece donde **las dos** cargas llevan
  una lectura y solo donde el coche se movió de verdad: dos sesiones de la misma tarde no dicen nada
  en vez de escribir un cero.
- **Importar cargas desde una hoja de cálculo (CSV)** — *Importar cargas desde un CSV* te da una
  **plantilla que se explica sola**; rellénala con Excel o Numbers y vuelve a subirla. Solo dos
  columnas son obligatorias, la fecha y la energía; el resto — coste, AC/DC, porcentajes inicial y
  final, hora de fin y el **cuentakilómetros 🆕** — son opcionales. La **exportación** de cargas se
  puede volver a importar tal cual. **Volver a importar el mismo archivo ya no crea duplicados 🆕**
  (#237): una línea que coincide con una sesión ya registrada la **completa** (escribiendo el
  cuentakilómetros) en vez de añadir una segunda copia, y Mate te dice cuántas ha añadido y cuántas ha
  completado. Antes lo duplicaba todo en silencio. ⚠️ En una sesión ya registrada se escribe **solo**
  el cuentakilómetros: un coste que Mate calculó a partir de una curva de carga real no se sobrescribe
  nunca.

### Precios de la carga
**(menú: Precios de la carga)** — Aquí indicas **cuánto pagas por la energía**, para que Mate pueda
calcular los costes. Puedes definir un precio **para cada tipo** de carga (En casa, AC, DC rápida,
HPC) y elegir entre:

- **Tarifa fija** (unos únicos €/kWh), o
- **Franjas horarias (TOU)** — precios distintos según el día de la semana y la franja horaria (por
  ejemplo punta / llano / valle, más barata de noche).

El precio **En casa** es el que alimenta el coste de las cargas domésticas y, a su vez, el coste de
los trayectos (calculado sobre el precio «medio» de la energía que había en la batería en el momento
del trayecto).

> Los cambios de precio valen **solo para las cargas futuras**: los costes ya calculados no cambian.
> Con franjas horarias también puedes elegir *cómo* repartir una sesión entre las franjas — *Reparto
> exacto* (sobre la curva de potencia real) o *Por la hora de inicio* (toda la sesión en la franja en
> la que empezó).

### Estadísticas
**(menú: Estadísticas)** — Tus medias y tus totales a lo largo del tiempo: **distancia de los
trayectos registrados** 🆕 (antes ponía *distancia total*, pero siempre ha sido la suma de los
trayectos terminados — no el cuentakilómetros del coche) y número de trayectos, **distancia media por
trayecto**, **tiempo al volante**, **consumo medio** (ponderado por distancia) y **el mejor**,
**energía consumida y cargada**, **regeneración** total y media, número de **sesiones de carga**, con
sus **evoluciones** correspondientes (consumo y regeneración a lo largo del tiempo). Los totales
incluyen además una tarjeta **Total V2L** con la energía acumulada consumida por V2L en todo el
histórico.

**Coste por 100 km 🆕** — lo que cuesta de verdad recorrer 100 km: **los euros gastados**, divididos
entre **los kilómetros recorridos**. Sin precio por kWh y sin estimaciones — la suma de lo que pagaste
sobre la suma de lo que anduviste, así que incluye los kWh que no movieron el coche a ningún sitio
(climatización, acondicionamiento previo, las pérdidas del propio cargador).

**Los euros y los kilómetros son del mismo periodo 🆕** (#237) — una carga que terminó **antes** del
primer trayecto registrado no tiene kilómetros propios entre los que dividirse, y no entra en la
cifra. Quien hubiera escrito un año de cargas antiguas estaba viendo meses de gasto divididos entre
los kilómetros de una tarde: el número salía decenas de veces más alto. Una carga hecha **después** del
último trayecto sí conserva su dinero — esos kilómetros llegan mañana.

**Y puede dividir por el cuentakilómetros del propio coche 🆕** (#237) — si tus cargas llevan
cuentakilómetros (ver *Cargas*), Mate mide la distancia entre la primera y la última con el contador
del coche en vez de con los trayectos reconstruidos: de lleno a lleno, como se ha medido siempre el
combustible. **Funciona incluso sin ningún trayecto registrado**, que es el caso de quien llevaba un
cuaderno e instala Mate meses después. Mate elige la base que le pone precio a **más de lo que
realmente gastaste** y dice cuál bajo la cifra — *«sobre los 18422 km del cuentakilómetros del coche»*
en lugar de *«sobre los km registrados»*. En un histórico normal ganan los trayectos y no cambia nada.
En un extensor de autonomía la gasolina se añade junto a la electricidad — la gasolina **quemada**,
valorada a lo que te costó el depósito, no el repostaje entero: un depósito que has pagado sigue
mayormente en el depósito, y cargárselo a los kilómetros que todavía no ha recorrido hacía que la
cifra saliera varias veces más alta 🆕. Si alguna carga no tiene precio, la tarjeta lo dice, porque
entonces la cifra real es mayor. Sigue tus unidades: con millas se convierte en «por 100 mi».

Junto al dinero, la tarjeta muestra ahora también **cuántos kWh costaron esos 100 km**, con la
etiqueta *«incluido el tiempo detenido» 🆕*. Se calcula como un balance, no como una suma de
recorridos: la energía cargada dentro de la ventana, menos lo que quedaba en la batería al final y no
estaba al principio. Así cubre todo lo que salió del paquete — marcha, climatización,
acondicionamiento previo, las pérdidas del propio cargador — que es la razón de que sea **más alta que
el consumo de la cabecera de Trayectos**, y de que la etiqueta lo diga. Si una carga de esa ventana no
tiene cifra de energía, la tarjeta también lo dice: el número es entonces un mínimo, no la historia
completa.

**Hasta dónde llegan estos números 🆕** — una línea en la parte de arriba de la página recuerda que
**todos** los totales de Estadísticas son lo que Mate ha registrado desde que se instaló, con la fecha
desde la que empieza, y **no** el total del cuentakilómetros del propio coche.

**Qué cubre cada cifra 🆕** — *Consumo medio* es la media sobre los kilómetros que **sí** tienen una
cifra de consumo, y debajo aparece *«sobre 452 km de 509 km»* cuando esos son menos que el total —
nombra los dos números, así que de un vistazo se ve si la cifra cubre casi toda la ventana o solo un
rincón. *Energía consumida* suma únicamente los trayectos cuya energía conoce Mate: un trayecto sin
ella queda **fuera** en vez de contar como cero, y la casilla dice de cuántos trayectos habla. En un
coche donde cada trayecto lleva su propio consumo — que es casi siempre — nada de esto llega a
aparecer.

### Informe mensual
**(menú: Informe mensual)** — Un resumen **mes a mes**: cuánto condujiste, cuánta energía consumiste y
cargaste, cuánto gastaste. Útil para vigilar la tendencia. Lleva también las tarjetas de **consumo
oficial** (Hoy / Esta semana / Este mes) de la nube.

Se abre siempre en el **mes en que estás**, incluso el día 1 sin haber conducido todavía — un mes
vacío lo dice en vez de enseñarte calladamente el anterior, y un mes en el que no hay nada no muestra
comparación con el precedente (todas las cifras darían −100 %, lo cual describe el calendario y no tu
conducción).

**De dónde sale la cifra de consumo, y cuándo Mate la corrige.** *Consumo medio* y *Energía consumida*
salen normalmente del total oficial del propio coche para ese mes. Ese total solo es tan completo como
lo fuera la conexión de tu coche: si el coche no pudo llegar a la nube durante un recorrido, ese
recorrido entero falta. Cuando el total vuelve muy por debajo de lo que suman los trayectos del propio
Mate para ese mismo mes, Mate muestra **su propia cifra en su lugar** — la misma que enseña la página
Trayectos — y lo dice bajo la casilla. El reparto Marcha / Climatización / Otros sigue siendo el del
coche, con una línea que aclara que cubre solo la parte que llegó a la nube.

### Salud de la batería
**(menú: Salud de la batería)** — Una **estimación del estado de salud (SoH)** de la batería: cuánta
capacidad útil queda respecto a la de nueva. En cada carga Mate divide la energía que **midió**
entrando en el paquete (tensión × corriente, integrado a lo largo de la sesión) entre el porcentaje que
esa carga añadió. Esa proporción es una estimación de la capacidad de todo el paquete, y su evolución
en el tiempo — o con los kilómetros, tú eliges — es el aspecto que tiene el envejecimiento.

Tres cosas sobre cómo se calcula, porque cambian lo que significa el número.

- **Un tramo callado ya no envejece la batería 🆕** (#241) — la capacidad se mide como energía frente
  al SoC que subió. Donde el coche deja de informar durante más de un cuarto de hora, esa energía se
  deja fuera a propósito (nadie sabe qué hizo el cargador mientras tanto), y **ahora el SoC de ese
  mismo tramo también se deja fuera**. Antes, una carga con una hora de silencio dentro podía dar
  81 % donde el paquete estaba al 100 %.
- **Con una conexión normal no cambia nada.** Donde tu coche informa como siempre, las cifras son
  idénticas hasta la décima; solo se mueven las cargas que tenían huecos de verdad — hacia arriba,
  hasta donde les tocaba.
- **Se para en el 95 %.** En un paquete LFP la tensión apenas cambia en la parte central del rango, así
  que el BMS **cuenta** la carga en vez de leerla, y se va desviando; cerca de arriba la curva por fin
  sube y el BMS **se reancla** — añadiendo puntos de porcentaje que ninguna energía pagó. Contar esos
  puntos haría parecer el paquete más pequeño, y sobre todo en una recarga corta hasta el 100 %, donde
  son casi toda la subida. Así que la cuenta se para en el 95 %: la carga en sí sigue contando, solo se
  deja fuera su último tramo.
- **Las cargas grandes pesan más, en proporción.** La cifra principal junta la energía y el porcentaje
  de las cargas recientes en vez de promediarlas una a una, así que una carga que abarcó 50 puntos pesa
  unas cuatro veces más que una que abarcó 13. No se descarta nada para conseguirlo.
- **Las cargas en frío se muestran pero quedan fuera** — una LFP marca de menos cuando está fría —
  igual que las cargas que empezaron casi vacías o en las que se ve saltar al BMS.

**La cifra viene con un ± , y esa es la parte honrada.** Es la **dispersión** de las cargas que hay
detrás, no una precisión: la energía está medida, pero el porcentaje entre el que se divide es un
número que contó el BMS, y ese número se desvía. Una banda estrecha significa que tus cargas coinciden
entre sí, no que el paquete sea con seguridad de ese tamaño. Con una sola carga no se muestra ningún ±,
porque una única medición no tiene dispersión que declarar.

Es una **estimación**, entonces — no un diagnóstico de laboratorio — y se va asentando a medida que se
acumulan cargas.

### Mantenimiento
**(menú: Mantenimiento)** — Los **plazos de mantenimiento** de tu coche, según el **plan oficial de tu
modelo** (T03, B05, B10, C10). De cada operación (revisión, líquido de frenos, filtro de habitáculo,
neumáticos…) ves dos barras de progreso: una de los **kilómetros** y otra del **tiempo**, porque lo que
toca es lo que llegue primero.

- Puedes **registrar una operación** («hecha hoy a X km») directamente desde la página: el siguiente
  plazo se recalcula.
- Para un **coche nuevo** que todavía no tiene histórico, puedes fijar una **fecha y un
  cuentakilómetros de referencia** para que los plazos partan de la entrega («primera revisión
  dentro de…») en vez de aparecer como «nunca hecha».
- La **fecha de matriculación o entrega ahora se puede editar**: haz clic en el **✏️** que hay junto a
  la fecha guardada para corregir un error (el valor nuevo sustituye al anterior).
- Las distancias respetan la unidad elegida (km o millas).

### Comandos
**(menú: Comandos)** — Los **comandos a distancia**. Desde aquí puedes:

- **abrir y cerrar**, abrir el **maletero**, **localizar el coche** (claxon/luces);
- gestionar la **climatización**: frío, calor, desempañado, ventilación, **apagar**;
- activar la **calefacción de los asientos**, el **volante** y los **retrovisores calefactados** (donde
  estén disponibles);
- gestionar el **límite de carga**.

La **tarjeta de climatización** tiene ahora un **deslizador de temperatura, uno de ventilador y un
interruptor de recirculación** (aire exterior / recirculación). Cada casilla de climatización — **A/A
AUTO · Frío · Calor · Ventilación · Desempañado** — se enciende según el **modo real** del coche, con
exactamente una encendida cada vez (igual que la app oficial). En los tres modos **manuales** (**Frío /
Calor / Ventilación**) puedes fijar la temperatura de consigna y la velocidad del ventilador: el coche
se queda en ese modo y recuerda el valor. En **AUTO** el coche gestiona solo el ventilador y la
recirculación, así que esos dos controles muestran el valor actual pero son de **solo lectura** — la
temperatura sigue siendo ajustable. La **ventilación rápida** ahora activa de forma fiable la
ventilación de verdad (solo aire, sin frío ni calor) desde cualquier estado.

Cuando envías un comando, Mate actualiza la interfaz al momento de forma «optimista» y luego lo
confirma en la siguiente lectura. Si la nube lo acepta pero el coche no confirma en unos segundos,
verás un aviso **ámbar** («enviado, puede que haya funcionado») — no es un error: el comando muchas
veces pasa igualmente (depende de la cobertura o del reposo del coche).

### Programación
**(menú: Programación)** — Las **programaciones** del coche:

- **Carga programada** (y el **límite de carga**);
- **Climatización programada** — 5 modos preestablecidos (frío / calor / ventilar / desempañar / auto)
  con una hora de inicio futura; puedes crearlas, editarlas y cancelarlas.

### Preparar el coche
**(menú: Preparar el coche)** — La función de «**acondiciona tu coche con un toque**»: lleva el
habitáculo a la temperatura que quieras (y las funciones asociadas) **ahora mismo** o a una **hora
programada**. También puedes apagarlo todo.

**🆕 Automático al encender** — En vez de darle al botón cada vez, puedes dejar que Mate ejecute la
preparación **solo, en cuanto el coche pasa a READY** (encendido). Activa **Automático al encender**,
elige una vez qué debe hacer — modo de climatización y temperatura de consigna, cuánto abrir las
ventanillas, **calefacción o ventilación** de los asientos del conductor y del acompañante, volante y
retrovisores calefactados — y guarda.

Puedes añadir una **condición opcional sobre la temperatura interior**: ejecutar la preparación **solo
cuando el habitáculo esté por encima** de un valor (por ejemplo, enfriar solo cuando pase de 25 °C) **o
solo cuando esté por debajo** de otro (por ejemplo, calentar solo cuando baje de 5 °C). **Deja la
condición desactivada y se ejecutará en cada encendido**, con la temperatura que sea. Dos cosas que
conviene saber de la condición: mira la temperatura **interior** (el coche no informa de la exterior), y
se decide **una sola vez, en el instante en que enciendes el coche** — así que si el habitáculo cambia
después durante el recorrido, no se disparará una segunda vez.

Se ejecuta **una vez por encendido** (no se repite mientras sigas encendido, ni en un recorrido
posterior del mismo encendido), ignora los cortes breves de señal y nunca se vuelve a disparar solo
porque Mate se haya reiniciado.

### Navegación
**(menú: Navegación)** — *Enviar un destino al navegador del coche* y **buscar puntos de recarga
cercanos**. La página tiene tres partes:

- **Destino** — escribe una **dirección** (y, si hace falta, la **ciudad**) y pulsa **Buscar**: el
  destino aparece en el mapa y con **🧭 Enviar al coche** lo mandas al navegador de a bordo.
  *Buscar por dirección requiere una clave de geocodificación* (ver [Ajustes → Búsqueda de
  direcciones](#7-ajustes)).
- **⚡ Puntos de recarga — «Buscar puntos de recarga»** — busca **puntos de recarga públicos alrededor
  del coche** (usando su posición GPS actual). Puedes fijar:
  - **Distancia máxima** — 500 m, 1, 2, **5 km** (por defecto) o 10 km;
  - **Resultados por página** — 25, 50 o 100;
  - **Red / operador** (opcional) — para filtrar un proveedor concreto (por ejemplo Iberdrola, Ionity,
    Endesa X, Zunder, Wenea, Repsol, Tesla…).

  Los resultados aparecen tanto como **chinchetas ⚡ en el mapa** como en una **lista** debajo, con
  **nombre, distancia** y, donde esté disponible, la **disponibilidad en tiempo real** (🟢/🔴 «libre
  ahora»). Toca un punto de la lista para **verlo en el mapa**, y con un clic puedes **usarlo como
  destino** y luego enviarlo al coche. Si no hay nada dentro del radio elegido, Mate lo amplía y muestra
  **los más cercanos**.

  > La búsqueda de puntos **no requiere ninguna clave** (usa mapas abiertos + una base de datos pública
  > de puntos de recarga); las claves opcionales de *Ajustes → ⚡ Puntos de recarga* (Open Charge Map,
  > TomTom) la enriquecen. Lo que sí necesita el coche es una **posición GPS** conocida.
- **Posición actual del coche** — la dirección del coche y un mapa con su chincheta 🚗.

### Vehículo
**(menú: Vehículo)** — La tarjeta de **estado completo** del coche: todos los sensores disponibles en tu
modelo (carga, autonomía, temperatura interior, marcha, puertas, ventanillas, neumáticos, cierres,
estado de la carga…), y ahora también el **detalle de la climatización**: **nivel del ventilador** (1–7),
**recirculación del aire** (exterior / recirculación) y el **modo de climatización activo** (AUTO /
Frío / Calor / Ventilación). Mate muestra **solo lo que tu coche informa de verdad** (algunos modelos no
exponen ciertos datos).

### Wallbox
**(menú: Wallbox)** — Si has conectado un wallbox (ver
[Integraciones](#8-las-integraciones-en-detalle)), aquí ves sus datos **en directo** (potencia,
energía), el **resumen** y la lista de **sesiones**, y en su caso los **controles** (por ejemplo la
corriente máxima) si tu wallbox los expone a través de Home Assistant.


Cuando tu coche **no está enchufado**, la tarjeta lo dice por su nombre — *«El C10 no está conectado»* —
porque en la wallbox puede haber otro coche, y esas cifras en vivo no serían las tuyas. La casilla del
coste se llama **Última carga en casa**: una carga recibe precio solo cuando termina, así que ese
número nunca es la sesión en curso.

> En Mate «casa» significa **wallbox o enchufe doméstico**: una carga puede llevar esa etiqueta sin que
> tu wallbox tenga nada que ver.

---

## 7. Ajustes

**(menú: ⚙️ Ajustes)** — La página está organizada en **tarjetas plegables**: se abre una cada vez. Está
dividida en tres columnas.

**Columna 1 — Vehículo y conducción**

- **🌍 Idioma y moneda** — el idioma de la interfaz, la moneda para los costes y las **unidades**
  (métricas/imperiales).
- **Vehículo** — el modelo de tu coche, su VIN y **con qué cuenta de Leapmotor inicia sesión esta
  instancia**. La cuenta importa si tienes Mate más de una vez — una segunda instancia, una de
  pruebas, una por coche: el modelo y el VIN describen el *coche*, así que dos instancias vigilando el
  mismo coche parecían idénticas por dentro. Aquí tienes también el botón **🔓 Cerrar sesión** para
  vincular otra cuenta: borra *solo* las credenciales guardadas, **no** tus trayectos y cargas ni el
  certificado.
- **Batería** — la **capacidad** en kWh que se usa en todos los cálculos; corregible. Si Mate tiene una
  estimación «medida» a partir de tus datos, te la ofrece.
- **Frecuencia de consulta** — cada cuánto lee Mate el estado de la nube, con dos deslizadores:
  **aparcado** (10 s–5 min, por defecto 30 s) y **en marcha** (10–60 s, por defecto 10 s). Leer más a
  menudo no descarga el coche, pero genera más tráfico hacia la nube.
- **Detección de carga** — el **umbral de corriente** (en amperios) por encima del cual Mate considera
  que hay «carga en marcha». Bájalo solo si tienes cargas muy lentas que se quedan sin detectar.

**Columna 2 — Integraciones**

- **ABRP** — envío de telemetría a A Better Routeplanner (ver [§8](#8-las-integraciones-en-detalle)).
- **Búsqueda de direcciones** — el servicio que traduce direcciones ↔ coordenadas en la página de
  Navegación (Geoapify *recomendado*, LocationIQ, TomTom). Requiere una **clave** gratuita del servicio
  elegido.
- **⚡ Puntos de recarga** — activa los **nombres de los puntos** en las cargas (📍) y acepta claves
  opcionales (Open Charge Map, TomTom) para enriquecer la búsqueda. Viene **desactivado**.
- **Wallbox** — conecta tu wallbox para tener **costes reales** y los controles que haya (ver
  [§8](#8-las-integraciones-en-detalle)).
- **MQTT → Home Assistant** — publica los datos del coche como entidades en Home Assistant (ver
  [§8](#8-las-integraciones-en-detalle)).

**Columna 3 — Datos y mantenimiento**

- **🔐 Acceso** *(solo en Docker independiente — con el complemento de Home Assistant, el ingress ya
  autentica cada petición y la tarjeta no se muestra)* — una contraseña para abrir Mate. Merece la pena
  ponerla: sin ella, cualquier cosa que esté en tu red puede abrir Mate, y Mate puede abrir tu coche.

  Se escribe **dos veces**, porque después no hay ningún sitio donde volver a leerla — se guarda como un
  hash con sal, nunca en claro. **Si la pierdes**, no te quedas fuera para siempre: el campo *Contraseña
  nueva* no pide la anterior, así que desde cualquier dispositivo con la sesión todavía abierta puedes
  poner una nueva sin más. Si ya no queda ningún dispositivo con la sesión abierta, la variable de
  entorno `MATE_AUTH_PASSWORD` tiene prioridad sobre lo que haya guardado.

- **Base de datos** — el tamaño de la BD y la **retención del GPS**: puedes conservar los puntos GPS
  «para siempre» (por defecto) o borrar los de más de 6/12/18/24 meses para ahorrar espacio. *Solo se
  limpian las posiciones*: los trayectos, las cargas y las curvas de carga se quedan.
- **Exportar / Copia de seguridad** — descargar **trayectos (CSV)**, **cargas (CSV)** y una **copia de
  la base de datos**.
- **🩺 Diagnóstico** — una foto del sistema (versión, modelo, recuentos, última consulta, integraciones
  activas), la posibilidad de **ver los registros** (poller/web) y, sobre todo, de **descargar un
  paquete de diagnóstico** marcando las partes que quieras (información, registro del poller, registro
  de la web, **señales en bruto**). El paquete viene **ya limpio** de datos sensibles: **GPS eliminado**
  y VIN y secretos ocultos, así que se puede adjuntar sin problema cuando pidas ayuda. La línea de
  integraciones informa por separado del **interruptor del wallbox** y de **Home Assistant**: el primero
  dice si tienes la función marcada, el segundo solo si Mate puede llegar a HA. Hay también una
  **búsqueda de cargas no vistas** ocurridas mientras el coche
  estaba dormido.

  🆕 **Los deslizadores que cambian el comportamiento de Mate ahora necesitan que pulses Guardar.** La
  frecuencia de consulta, la detección de carga, los umbrales avanzados: antes se guardaban en cuanto
  soltabas el deslizador, así que un dedo que lo arrastraba al hacer scroll en el móvil lo cambiaba sin
  preguntar. El deslizador se sigue moviendo libremente; no se escribe nada hasta que pulsas Guardar.
  **Y cada cambio de estos queda registrado** — cuándo, de qué valor, a qué valor — y sale en el
  paquete de diagnóstico, así que «se ha cambiado solo» se puede comprobar.

  🆕 El paquete lleva ahora también **las propias filas** — las cargas y los trayectos de las últimas
  dos semanas, directamente de la base de datos — y una sección que lista **cada vez que la batería se
  llenó con el coche aparcado** junto con lo que Mate podía ver en ese momento: si el cable se
  declaraba, si Mate concluyó que estaba cargando, la corriente, y si los datos llegaban frescos o la
  nube estaba repitiendo una lectura antigua. Nada de eso es información nueva sobre ti: es lo que Mate
  ya registraba, por fin escrito donde el soporte puede leerlo. Sigue sin haber posiciones.
- **⚙️ Avanzado** — parámetros finos para usuarios expertos: el umbral mínimo para **reconstruir** una
  carga no vista, el umbral de la **descarga pasiva**, el umbral en kW para distinguir la **CC**, y la
  temperatura mínima para el cálculo de la **salud de la batería**. Hay un botón para **restablecer los
  valores por defecto**.

> 🆕 Cuando llega una función nueva, su tarjeta puede mostrar una insignia **Nuevo** hasta que la abras
> por primera vez.

---

## 8. Las integraciones en detalle

Todas las integraciones son **opcionales** y vienen **desactivadas**. Se configuran desde **Ajustes**.

### Wallbox (para los costes reales de carga)
Conectando tu wallbox, Mate usa la **energía realmente entregada** (del lado de corriente alterna) para
calcular el coste de las cargas en casa, en vez de estimarlo a partir del cambio de porcentaje.

Mate lee el wallbox **a través de Home Assistant**:

1. En *Ajustes → Wallbox*, activa **Tengo wallbox**.
2. **Si usas el complemento de Home Assistant**, Mate puede llegar a HA por su cuenta: no hace falta
   escribir ninguna dirección ni ningún token.
3. **Si usas Mate como Docker independiente**, escribe la **URL de Home Assistant** (por ejemplo
   `http://192.168.1.10:8123`) y un **token de acceso de larga duración** de HA, y pulsa **Probar**.
4. Con las **palabras clave** puedes ayudar a Mate a reconocer las entidades correctas de tu wallbox
   (por ejemplo `wallbox, charger, evse, keba, pulsar`). Algunos wallbox conocidos (por ejemplo V2C
   Trydan) se reconocen solos; las entidades «trampa» (solar/casa) quedan excluidas.
5. Abre la lista de entidades para comprobar que Mate se ha agarrado a los sensores de **energía y
   potencia** correctos.
6. Opción **«asignar sola la carga en casa»**: pone automáticamente la etiqueta **En casa** a las cargas
   hechas en tu wallbox.

### ABRP (A Better Routeplanner)
Envía la telemetría del coche a ABRP para planificar rutas en tiempo real.

1. En *Ajustes → ABRP*, activa **Activado**.
2. Pega tu **token** de ABRP (lo encuentras en los ajustes «generic»/telemetría de tu cuenta de ABRP).
3. Guarda. El estado de la integración aparece en la cabecera de la tarjeta.

### MQTT → Home Assistant
Publica el estado del coche (carga, autonomía, posición, puertas, estado de la carga…) como
**entidades en Home Assistant**, con **descubrimiento automático**. También puedes **mandar** al coche
desde las entidades de HA — incluidos un número **Límite de carga** escribible para fijar el SoC
objetivo, una entidad de texto **Programación de carga** escribible que acepta un plan en JSON para
automatizaciones (`{"start":"23:00","soc":90}` — cada clave es opcional, y lo que omitas conserva su
valor actual), un número **Nivel del ventilador** escribible (1–7) y un interruptor **Recirculación**
escribible, más un sensor **Modo de climatización** (AUTO / Frío / Calor / Ventilación). Las entidades
publicadas incluyen además tres de V2L de solo lectura: **`V2L Active`** (sensor binario), **`V2L
Power`** (W) y **`V2L Session Energy`** (Wh), y un sensor binario **`Ready`** que se enciende en cuanto
el coche se pone en marcha — antes de que se mueva, que es cuando a una automatización todavía le da
tiempo a actuar.

Las entidades que **tu** coche no soporta no se te quedan en las manos: las que el modelo no tiene
(asientos calefactados, volante…) no se crean nunca, y una **entidad de temperatura** cuyo sensor el
coche no ha informado jamás **se elimina** — no se queda en `unknown` para siempre. La eliminación
llega cuando llega la prueba (más o menos media hora de actualizaciones), sin necesidad de reiniciar, y
si el sensor empieza a responder la entidad **vuelve**.

1. Ten preparado un **broker MQTT** (normalmente el complemento *Mosquitto* de Home Assistant).
2. En *Ajustes → MQTT*, activa **Activado** y rellena:
   - **Broker** (por ejemplo `192.168.1.10` o `core-mosquitto`) y **Puerto** (por defecto `1883`);
   - el **Usuario** y la **Contraseña** del broker;
   - el **Prefijo** de temas (por defecto `leapmotor`);
   - opciones: **Descubrimiento** (recomendado), **TLS** y **TLS sin verificar** si usas certificados
     autofirmados.
3. Pulsa **Probar la conexión** para comprobar el enlace y luego **Guardar**. En unos segundos las
   entidades aparecen en Home Assistant.

> Para los comandos por MQTT el coche sigue pidiendo el PIN: Mate lo usa automáticamente con las
> credenciales guardadas.

---

**Si tienes más de un Mate contra el mismo broker 🆕** — el complemento normal y el de BetaTester, por
ejemplo — dale a cada uno un **prefijo de temas distinto** (*Ajustes → MQTT*). Con el mismo prefijo, y
vigilando el mismo coche, para Home Assistant son **un solo dispositivo**: el segundo parece no
funcionar y, peor todavía, **cada comando se ejecuta dos veces**. Ahora Mate se da cuenta y lo dice; la
compilación BetaTester se mueve sola a un prefijo propio, y la normal no se mueve nunca.

## 9. Modo demostración

El modo **demostración** te deja probar Mate sin coche y sin cuenta: arranca con **un mes de datos
falsos pero realistas**. Se puede activar de dos maneras:

- desde el asistente del primer arranque, con el botón **🧪 Probar la demostración**;
- o arrancando el contenedor con la variable `MATE_DEMO=1`.

En la demostración: los datos son abiertamente ficticios (una insignia **DEMO**), los comandos están
**simulados** (no se contacta con ningún coche) y una franja arriba se queda visible todo el rato con el
botón para **salir**. Al salir, Mate vuelve a la configuración normal.

---

## 10. Preguntas frecuentes y resolución de problemas

**El coche se queda «sin conexión» a menudo / veo constantemente «Invalid token».**
Casi siempre es porque **la misma cuenta de Leapmotor se está usando en otro sitio** (la app oficial,
otra integración, una segunda instancia de Mate). Usa una **cuenta dedicada solo a Mate** y **cámbiale
la contraseña**, usándola únicamente aquí (así el otro cliente queda fuera y no puede volver a entrar).
Ver [requisitos](#2-antes-de-empezar-los-requisitos).

**Un comando da «timeout» o un aviso ámbar.**
(Normalmente) no es un problema de Mate. Los comandos son *en tiempo real* y dependen de que el **coche
sea alcanzable** (cobertura, reposo). Mate reintenta y muchas veces el comando pasa igualmente. El
indicador **«Respuesta del coche»** del Resumen te da una idea de la situación.

**Faltan algunos trayectos o kilómetros después de un rato sin conexión.**
Cuando el coche estuvo inalcanzable, puede que algunos datos no se registraran. Las cargas ocurridas
«mientras dormía» normalmente se **reconstruyen** a partir del salto de carga; los kilómetros perdidos
no siempre se pueden recuperar. La **búsqueda de cargas no vistas** (Ajustes → Diagnóstico) ayuda a
encontrar cargas que no se registraron.

**Veo una carga rara / un coste absurdo.**
Mate tiene protecciones contra valores imposibles (por ejemplo, contadores de wallbox que informan del
total histórico). El caso contrario también está cubierto: si el contador del wallbox **se para** a
media carga mientras el coche sigue consumiendo, Mate deja de fiarse de su total para esa sesión y
factura sobre la energía que llegó a la batería — la cifra del contador se quedaría corta en todo lo que
se perdió mientras estuvo congelado.
Si una carga pública tiene una tarifa complicada, usa el tipo **✎ Manual** y escribe el total pagado.

**El gráfico de descarga pasiva está vacío.**
Hace falta al menos una **parada larga** con una caída de carga medible en los últimos días. Si el coche
está siempre cargando o duerme mientras está aparcado, puede que no haya material suficiente. Mate
también capta la caída que solo «se revela» al despertar.
Otra causa frecuente es el **umbral de la descarga pasiva** de *Ajustes → Avanzado*: si lo subiste por
encima de las caídas reales de tu coche, el gráfico no dibuja nada. Devuélvelo hacia **0,2** (o pulsa
**Restablecer**) y los periodos reaparecen. Desde la **v1.22.4** la página te lo dice explícitamente —
sigue mostrando el valor típico y un aviso de «por debajo de tu umbral» en vez de parecer vacía.
Desde la **v3.10.5** al gráfico le sigue además **la última parada descartada**, con su duración, su
caída y el motivo — así, un gráfico que lleva días sin crecer ya no parece roto. Lo más habitual es que
el motivo sea que el coche perdió un **0,1 %**, un único escalón de su sensor de carga: por debajo de
ahí una caída no se puede distinguir del ruido, y Mate prefiere no dibujar nada antes que un número
inventado.

**Tengo un Leapmotor REEV (híbrido con extensor de autonomía).**
No está soportado: los cálculos de energía usarían la capacidad de batería de la versión BEV y saldrían
mal. Mate es **solo para las versiones 100 % eléctricas**.

**No estoy en Europa.**
Por ahora Mate solo funciona con la nube **europea** de Leapmotor. Las cuentas alojadas en servidores de
otras regiones no pueden iniciar sesión.

**¿Cómo hago una copia de seguridad?**
Desde *Ajustes → Exportar/Copia de seguridad* descargas la base de datos (y los CSV). Guarda la BD
**junto con su `secret.key`**.

---

## 11. Glosario

- **SoC** (*State of Charge*) — el estado de carga de la batería, en porcentaje.
- **SoH** (*State of Health*) — el estado de salud de la batería: capacidad que queda respecto a la de
  nueva.
- **CA / CC** — corriente alterna (carga lenta, desde casa o puntos de CA) / corriente continua (carga
  rápida y ultrarrápida).
- **En casa / AC / DC rápida / HPC / Manual** — los tipos de carga que Mate reconoce o que puedes
  asignar tú; «HPC» es la carga de potencia muy alta.
- **TOU** (*Time-of-Use*) — una tarifa por **franjas horarias** (precios distintos según el día y la
  hora).
- **Regeneración** — energía **recuperada** al frenar o al levantar el pie y devuelta a la batería.
- **Descarga pasiva** — lo que consume el coche estando **completamente apagado**, medido desde que se
  apaga hasta el siguiente encendido. **Incluye la climatización a distancia hecha con el coche
  apagado** (a propósito — coche apagado → cuenta como descarga). El consumo en reposo con el coche
  *encendido* (aparcado, con la climatización en marcha) no se cuenta aquí.
- **Polling** — la lectura periódica del estado del coche desde la nube (no descarga el coche).
- **Wallbox** — tu punto de recarga doméstico.
- **Poller / Web** — los dos componentes internos de Mate: el *poller* recoge los datos, la *web*
  muestra la interfaz. Para ti, como usuario, es un detalle: trabajan juntos.
- **VIN** — el número de bastidor del coche; identifica tu vehículo de forma única.
- **PIN de operaciones** — el PIN de 4 dígitos de la cuenta, necesario para autorizar los comandos a
  distancia.

---

> 📌 **Nota de mantenimiento del manual.** Este documento describe la versión **v3.11.0**. Cuando cambie
> algo visible para el usuario (una página nueva, una opción, un flujo), actualiza la sección
> correspondiente y la línea de versión de arriba. Está pensado como base para las traducciones
> (EN/IT/FR/DE): la estructura es deliberadamente la misma que la de la interfaz.
