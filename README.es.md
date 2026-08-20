[Українська](README.md) · [English](README.en.md) · **Español** · [Português](README.pt.md) · [Deutsch](README.de.md) · [Français](README.fr.md) · [Polski](README.pl.md) · [Türkçe](README.tr.md) · [简体中文](README.zh.md)

# TwitchDropFarm

Consigue **timed drops** en Twitch sin un navegador abierto ni un directo en
pantalla. El programa lee tu inventario, decide qué merece la pena farmear,
encuentra un canal adecuado y entrega tiempo de visionado a Twitch — y muestra
las recompensas obtenidas en su ventana, en la bandeja del sistema y en Telegram.

Un único `.exe`, sin runtimes al lado: ni Node.js, ni Playwright, ni un navegador
incluido. Para iniciar sesión usa el navegador que ya tengas instalado (Edge o
Chrome).

## Qué hace

- **Avisa cuando no llega a tiempo**: si queda menos tiempo que los minutos de
  visionado que aún faltan, lo dice con antelación.
- **Elige por sí mismo.** Cuatro modos: por lista de prioridad, por fecha límite
  más cercana, por mejor encaje (para completar tantas campañas como sea
  posible) o solo aquello a lo que tu cuenta está vinculada y donde se entrega un
  objeto real.
- **Vigila hasta 198 canales** mediante PubSub y cambia cuando un directo se
  apaga.
- **Reclama los drops automáticamente** y pasa de inmediato al siguiente.
- **Una ventana** con cuatro pestañas: Minado, Canales, Inventario, Ajustes.
- **Bandeja del sistema**: minimizar, notificaciones, arranque en segundo plano.
- **Bot de Telegram**: estado, inventario, campañas, pausar/reanudar, cambio de
  canal, gestión de prioridades, reinicio completo — con botones o comandos.
- **Sobrevive a los fallos**: caída de red, DNS que desaparece, suspensión del
  equipo, errores transitorios de Twitch. En el peor caso se reinicia solo.
- **Detecta el estancamiento**: si los minutos dejan de acumularse (por ejemplo,
  porque esa misma cuenta está viendo Twitch manualmente en otro sitio), lo avisa
  en lugar de callar.
- **Idiomas de la interfaz** (Ajustes): ucraniano por defecto, también English,
  Español, Português, Deutsch, Français, Polski, Türkçe, 简体中文. No hay ruso.

## Requisitos

- Windows 10/11
- Python 3.10+ — solo para ejecutar desde el código o compilar el `.exe`
- Edge o Chrome — solo para el primer inicio de sesión

## Ejecución

Desde el código fuente:

```bash
python -m venv env
env\Scripts\pip install -r requirements.txt
env\Scripts\python main.py
```

El `.exe` compilado:

```bash
dist\TwitchDropFarm.exe
```

En el primer arranque el programa abre una página de Twitch con un código de
confirmación. Tras iniciar sesión, el token se guarda y no vuelve a pedirse.

### Argumentos

| Argumento | Qué hace |
|---|---|
| `--console` | sin ventana, solo consola — para un servidor o el arranque automático |
| `--tray` | arrancar minimizado en la bandeja |
| `--log` | escribir `log.txt` |
| `-v`, `-vv`, `-vvv` | más detalle en los registros (repetible) |
| `--auth-only` | autenticarse y salir |
| `--dump-inventory` | mostrar todas las campañas y drops, y salir |
| `--test-telegram` | enviar un mensaje de prueba y salir |
| `--version` | versión |

## Configuración

`settings.json` vive en el directorio de estado (más abajo) y se crea solo en el
primer arranque. Ejemplo:
[`settings.example.json`](settings.example.json).

| Clave | Significado |
|---|---|
| `farm_mode` | `0` — lista de prioridad, `1` — fecha límite más cercana, `2` — mejor encaje, `3` — solo campañas vinculadas |
| `priority` | juegos por orden de preferencia |
| `exclude` | juegos que no tocar |
| `farm_cosmetics` | aceptar campañas que solo dan insignias y emotes |
| `verify_channel_drops` | comprobar en cada canal si los drops están realmente activos (más lento, más fiable) |
| `start_in_tray` | arrancar minimizado |
| `tray_notifications` | notificaciones emergentes |
| `dark_theme` | tema oscuro de la ventana |
| `drop_images` | descargar las imágenes de las recompensas y mostrarlas en la lista (caché ~6 MB) |
| `image_size` | tamaño de la imagen en la lista, 16–96 |
| `inventory_view` | `list` — lista densa, `tiles` — tarjetas con imágenes grandes |
| `browser_path` | ruta al navegador si la detección automática falla |
| `proxy` | proxy para las peticiones |

El modo y la prioridad son más cómodos de cambiar en la pestaña de ajustes; el
resto, a mano en el archivo. Los cambios en el archivo se aplican tras reiniciar.

### Telegram

1. Crea un bot con [@BotFather](https://t.me/BotFather) y copia el token.
2. Escribe cualquier cosa a tu bot para que vea tu `chat_id`.
3. En `settings.json`:

```json
"telegram": {
    "enabled": true,
    "bot_token": "TU_TOKEN_AQUI",
    "chat_ids": [TU_CHAT_ID],
    "allow_control": true,
    "notify_critical": true,
    "notify_rewards": true,
    "notify_routine": false,
    "report_every_hours": 6
}
```

4. Comprueba: `main.py --test-telegram`

`chat_ids` es una lista blanca. Todo lo que llegue de otro sitio se ignora, así
que un desconocido que encuentre el bot no podrá controlar el miner.

Comandos: `/status`, `/inventory`, `/campaigns`, `/pause`, `/resume`,
`/switch <canal>`, `/priority add|remove <juego>`, `/reload`, `/hide`, `/show`, `/reboot`,
`/menu`, `/help`. Todo salvo los dos que llevan argumentos está disponible como
botón.

## Dónde vive el estado

`%LOCALAPPDATA%\TwitchDropFarm\`

```
auth.json        token de Twitch
cookies.jar      cookies
settings.json    configuración
log.txt          registro (con --log)
lock.file        protección contra dos copias a la vez
browser_profile  perfil del navegador para el inicio de sesión
```

El directorio de estado es uno por usuario y no está junto al programa; de lo
contrario, cada copia nueva pediría iniciar sesión otra vez. Para lo contrario
(un pendrive, un ordenador ajeno), coloca un archivo vacío `portable.txt` junto
al `.exe`: el estado vivirá allí.

## Compilación

```bash
env\Scripts\python.exe -m PyInstaller build.spec --noconfirm
```

Tres formas fáciles de tropezar:

- **Detén el `.exe` en ejecución** antes de compilar, o saldrá `PermissionError`.
- **No interrumpas la compilación.** Un PyInstaller abortado deja un `.exe`
  truncado que muere con `DLL load failed while importing _tkinter`. Parece un
  defecto del código, pero no lo es.
- **No añadas `--clean`** sin motivo: más lento y sin beneficio.

## Comprobaciones

```bash
main.py --dump-inventory     todas las campañas del Twitch real
main.py --test-telegram      el bot
tests\core_check.py          lógica del núcleo (sin red)
tests\bot_check.py           pruebas del bot (sin red)
tests\live_check.py          el núcleo contra el Twitch real
```

## Cómo está montado

```
core/protocol   hechos sobre la API privada de Twitch — no decisiones nuestras
core/config     rutas, intervalos, límites
core/toolbox    utilidades independientes
core/api        red, reintentos, resistencia
core/identity   token y cabeceras
core/model      campañas y drops
core/channels   canales y entrega del visionado
core/pubsub     suscripciones
core/miner      solo lógica de decisión
auth/           inicio de sesión: device flow y control del navegador por CDP
gui/            ventana y bandeja
notify/         Telegram
```

La separación es deliberada: `protocol` describe lo que dicta Twitch (hashes de
persisted queries de GraphQL, el formato del evento `minute-watched`, nombres de
los topics), mientras que `config` guarda lo que decidimos nosotros. Mezclarlos
significa no saber cuál de los dos se puede cambiar.

El control del navegador es un cliente propio del Chrome DevTools Protocol sobre
`aiohttp`. Playwright y Selenium no se usan a propósito: ambos arrastran sus
propios runtimes, y el requisito del proyecto es un único `.exe` autosuficiente.

## Limitaciones

- Solo Windows. Nada en la arquitectura impide portarlo, pero las rutas del
  navegador, la bandeja y el arranque automático están escritos para Windows.
- Twitch no promete que su API privada se mantenga igual. Si cambian los hashes
  de las persisted queries, lo que hay que arreglar es `core/protocol.py`.
- Una cuenta por proceso.

## Advertencia

El programa hace lo mismo que haría un directo abierto en el navegador, solo que
sin una persona delante de la pantalla. Automatizar el visionado puede entrar en
conflicto con las Condiciones de Servicio de Twitch. El riesgo es del usuario; el
autor no asume responsabilidad por las consecuencias para tu cuenta.

## Licencia

MIT — véase [LICENSE](LICENSE).
