# sistema-de-concentraci-n# 🚨 Vigilante de pantalla

Programa en Python que usa tu cámara web para **gritarte cuando dejas de mirar la pantalla**. También detecta tus **manos y tu cara** y las marca con **cuadros rojos** en tiempo real.

## Qué hace

- Detecta si estás mirando al monitor: mide el giro de tu cabeza (izquierda/derecha y arriba/abajo) y hacia dónde miran tus ojos.
- Si te distraes más de 1.5 segundos, o desapareces de la cámara, se activa la alerta: borde rojo parpadeante, el texto `MIRA LA PANTALLA!`, una sirena y una voz que te regaña.
- Marca con cuadros rojos tus manos (`MANO`) y tu cara (`CARA`).
- Se calibra con tu posición normal, así que funciona aunque la cámara no esté centrada frente al monitor.
- Todo se procesa en tu computadora (ver [Privacidad](#privacidad)).

## Requisitos

- **Python 3.10 – 3.13.** Si usas una versión más nueva y `pip install mediapipe` falla, instala una de estas.
- Cámara web y parlantes o audífonos.
- Internet **solo la primera vez**, para descargar unos 10 MB de modelos.
- Pensado para **Windows**. En macOS y Linux está soportado, pero sin probar (ver [Sonido y voz](#sonido-y-voz)).

## Instalación y uso

```bash
pip install mediapipe opencv-python numpy
python vigilante_pantalla.py
```

1. La primera vez descarga los modelos en la carpeta `modelos/` (se crea sola).
2. Se abre una ventana con tu cámara. **Mira a la pantalla unos 3 segundos**: así se calibra tu posición normal.
3. Listo: si dejas de mirar, te grita. Para salir pulsa `Q` o `ESC` (o cierra la ventana).

> **Ojo:** prueba primero con el volumen bajo. La sirena es fuerte, sobre todo con audífonos.

## Teclas

Con la ventana del video activa:

| Tecla | Acción |
|---|---|
| `C` | Recalibrar (mira a la pantalla y pulsa `C`) |
| `M` | Silenciar / activar los gritos (los avisos en pantalla siguen) |
| `Q` o `ESC` | Salir |

## Qué se ve en la ventana

- **Franja superior:** `CALIBRANDO...` (naranja), `ATENTO` (verde), o `DISTRAIDO` / `NO TE VEO` (rojo, con los segundos que llevas así).
- **Cuadros rojos:** `CARA` (o `CARA (DESVIADA)`) y `MANO`.
- **Línea inferior:** `giro`, `incl`, `ojos` y `FPS`. Sirve para ajustar los límites (ver [Configuración](#configuración)).

## Cómo funciona

1. **Detección:** usa [MediaPipe](https://developers.google.com/edge/mediapipe/solutions) (*Face Landmarker*: 478 puntos 3D de la cara; *Hand Landmarker*: 21 puntos por mano) y [OpenCV](https://opencv.org) para la cámara y el dibujo.
2. **Calibración:** durante los primeros 3 segundos guarda cómo se ve tu cara cuando miras a la pantalla (24 puntos que no se mueven al hablar: frente, nariz, esquinas de los ojos y mejillas) y dónde está el iris dentro de cada ojo. Si la cámara no te ve en ese tiempo, reintenta sola.
3. **En cada cuadro:** compara tu cara con la calibrada y calcula cuántos grados giraste (izquierda/derecha) o inclinaste (arriba/abajo) la cabeza. Ladear la cabeza no cuenta. También mide cuánto se desvió el iris de su posición calibrada. Los valores se suavizan para evitar falsas alarmas.
4. **Decisión:** si el giro, la inclinación o la mirada superan sus límites estás `DISTRAIDO`; si no hay cara, `NO TE VEO`. Cuando eso dura más de `SEG_TOLERANCIA`, se activa la alerta, y se repite cada `SEG_ENTRE_GRITOS` mientras sigas así.

## Configuración

Los ajustes están al inicio de `vigilante_pantalla.py`, en el bloque `CONFIGURACIÓN`:

| Variable | Por defecto | Qué hace |
|---|---|---|
| `CAMARA` | `0` | Número de cámara (prueba `1`, `2`... si tienes varias) |
| `ESPEJO` | `True` | Muestra la imagen como un espejo |
| `ANCHO`, `ALTO` | `960`, `540` | Resolución que se le pide a la cámara |
| `SEG_CALIBRACION` | `3.0` | Segundos de calibración |
| `SEG_TOLERANCIA` | `1.5` | Segundos distraído antes del primer grito |
| `SEG_ENTRE_GRITOS` | `4.0` | Pausa mínima entre un grito y el siguiente |
| `GIRO_MAX` | `28` | Grados de giro lateral que se toleran |
| `INCLINACION_MAX` | `20` | Grados de cabeza arriba/abajo que se toleran |
| `USAR_OJOS` | `True` | También vigila hacia dónde miran los ojos |
| `MIRADA_MAX` | `0.16` | Desvío de los ojos que se tolera |
| `COLOR_MANO`, `COLOR_CARA` | `(0, 0, 255)` | Color de los cuadros, en formato BGR (este es rojo) |
| `PUNTOS_EN_MANOS` | `False` | Dibuja también los 21 puntos de cada mano |
| `VOLUMEN_SIRENA` | `0.8` | Volumen de la sirena (de 0.0 a 1.0) |
| `ARCHIVO_GRITO` | `"grito.wav"` | Nombre del archivo de grito propio |
| `FRASES_DESVIADO`, `FRASES_AUSENTE` | listas | Frases que dice la voz al distraerte / al no verte |

Ejemplos de ajuste:

- **Tienes dos monitores:** sube `GIRO_MAX` (por ejemplo a `45`).
- **Te grita sin motivo:** pon `USAR_OJOS = False`, o sube `MIRADA_MAX` o `SEG_TOLERANCIA`.
- **No grita cuando miras el celular:** baja `INCLINACION_MAX` (por ejemplo a `12`).

## Sonido y voz

- **Sirena:** el programa la genera solo, no necesita archivos. `VOLUMEN_SIRENA` afecta únicamente a esta sirena.
- **Un grito propio:** guarda un archivo `grito.wav` (formato WAV) junto al script y sonará en lugar de la sirena. Si lo quieres más fuerte, súbele el volumen con un editor de audio.
- **Voz:** usa lo que ya trae el sistema, sin instalar nada extra.
  - **Windows:** `winsound` (viene con Python) para la sirena y PowerShell con `System.Speech` para la voz, que usa una voz en español si hay una instalada.
  - **macOS:** `afplay` (sirena) y `say` (voz), que ya vienen con el sistema.
  - **Linux:** necesita `espeak-ng` (o `espeak` / `spd-say`) para la voz y un reproductor como `paplay` o `aplay` para la sirena.

## Solución de problemas

**"No pude abrir la cámara"**
Cierra otras apps que la usen (Zoom, Teams, OBS...), prueba `CAMARA = 1` y revisa que Windows permita el acceso a la cámara (Configuración → Privacidad y seguridad → Cámara, incluyendo las apps de escritorio).

**`pip install mediapipe` falla**
Casi siempre es la versión de Python. Usa una entre 3.10 y 3.13.

**No se descargan los modelos**
Descárgalos a mano y guárdalos en la carpeta `modelos/` junto al script, con estos nombres exactos:

```
https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task
https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task
```

**Va lento o se entrecorta**
Baja la resolución (`ANCHO, ALTO = 640, 360`) y cierra otros programas pesados.

**Grita demasiado, o no grita cuando debería**
Mira la línea inferior de la ventana (`giro`, `incl`, `ojos`) mientras haces lo que te distrae, y ajusta los límites en [Configuración](#configuración). Recalibra con `C` mirando al centro de la pantalla y procura tener buena luz de frente.

**No se oye nada**
Revisa el volumen de la computadora y prueba con tu propio `grito.wav`. En Linux instala `espeak-ng` y un reproductor (`paplay` o `aplay`).

**La voz habla en inglés o con acento raro**
Windows no tiene una voz en español instalada. La sirena suena igual; si quieres la voz en español, añade el idioma Español (con su función de texto a voz) en la configuración de Windows.

## Privacidad

- Todo se procesa **en tu computadora**: el programa no guarda ni envía imágenes ni audio.
- Solo usa internet la primera vez, para descargar los modelos.
- La cámara está encendida únicamente mientras el programa corre y se libera al salir.

## Límites conocidos

- Una cámara web no puede saber con exactitud a dónde miras: la mirada es una estimación, y funciona mejor con buena luz y la cámara arriba, al centro del monitor.
- Los lentes oscuros o con mucho reflejo pueden confundir la detección de los ojos (si pasa, pon `USAR_OJOS = False`).
- Vigila una sola cara. Si te tapas la cara con la mano o giras mucho la cabeza, cuenta como `NO TE VEO`.
- Los textos de la ventana van sin tildes ni signos de apertura (¡ ¿) porque OpenCV no los dibuja.

## Archivos

```
vigilante_pantalla.py    el programa
README.md                este archivo
grito.wav                (opcional) tu propio grito
modelos/                 se crea sola la primera vez
  face_landmarker.task
  hand_landmarker.task
```

## Créditos

Hecho con [OpenCV](https://opencv.org), [MediaPipe](https://developers.google.com/edge/mediapipe/solutions) (Google) y [NumPy](https://numpy.org).
