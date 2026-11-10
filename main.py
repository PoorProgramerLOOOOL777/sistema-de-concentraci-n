#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VIGILANTE DE PANTALLA
=====================
Usa tu webcam para:
  - Saber si estás mirando hacia la pantalla (giro de la cabeza + hacia dónde miran tus ojos).
  - Detectar tus manos y tu cara y marcarlas con cuadros ROJOS.
  - GRITARTE (sirena + voz) si dejas de mirar la pantalla o desapareces de la cámara.

Instalación (una sola vez):
    pip install mediapipe opencv-python numpy

Uso:
    python vigilante_pantalla.py

- La primera vez descarga dos modelos pequeños de Google (~10 MB) a la carpeta "modelos".
- Al arrancar, mira a la pantalla unos segundos: así aprende cuál es tu posición "normal".

Teclas (con la ventana del video activa):
    C        recalibrar (mira a la pantalla y pulsa C)
    M        silenciar / activar los gritos
    Q o ESC  salir

Para un grito propio, pon un archivo "grito.wav" junto a este script.
"""

import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave

os.environ.setdefault("GLOG_minloglevel", "2")        # menos ruido en la consola
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

try:
    import cv2
    import mediapipe as mp
    import numpy as np
except ImportError as error:
    sys.exit(f"Falta una librería ({error}).\n"
             "Instálala con:  pip install mediapipe opencv-python numpy")

# =============================== CONFIGURACIÓN ===============================
CAMARA = 0                   # 0 = cámara por defecto (prueba 1, 2... si tienes varias)
ESPEJO = True                # True = la imagen se ve como un espejo
ANCHO, ALTO = 960, 540       # resolución que se le pide a la cámara

SEG_CALIBRACION = 3.0        # segundos mirando la pantalla para calibrar (al iniciar y con C)
SEG_TOLERANCIA = 1.5         # segundos "distraído" antes de que te griten
SEG_ENTRE_GRITOS = 4.0       # pausa mínima entre un grito y el siguiente

GIRO_MAX = 28                # grados que puedes girar la cabeza (izq/der) y seguir "mirando"
INCLINACION_MAX = 20         # grados que puedes subir/bajar la cabeza
USAR_OJOS = True             # también vigila hacia dónde miran tus ojos
MIRADA_MAX = 0.16            # desvío tolerado de los ojos (0 = nada, ~0.3 = extremo)

COLOR_MANO = (0, 0, 255)     # BGR: rojo
COLOR_CARA = (0, 0, 255)     # BGR: rojo
PUNTOS_EN_MANOS = False      # True = además dibuja los 21 puntos de cada mano

VOLUMEN_SIRENA = 0.8         # 0.0 a 1.0 (cuidado si usas audífonos)
ARCHIVO_GRITO = "grito.wav"  # si existe junto al script, suena este en vez de la sirena

FRASES_DESVIADO = [
    "¡Eh! ¡Mira la pantalla!",
    "¡Oye! ¡Te estás distrayendo!",
    "¡Vuelve al trabajo ahora mismo!",
    "¡Concéntrate, por favor!",
    "¡La pantalla está aquí, no allá!",
]
FRASES_AUSENTE = [
    "¡No te veo! ¡Vuelve a mirar la pantalla!",
    "¡Dónde estás! ¡Vuelve a la computadora!",
]
# ============================================================================

CARPETA = os.path.dirname(os.path.abspath(__file__))
CARPETA_MODELOS = os.path.join(CARPETA, "modelos")
VENTANA = "Vigilante de pantalla"
FUENTE = cv2.FONT_HERSHEY_SIMPLEX

_BASE = "https://storage.googleapis.com/mediapipe-models"
URLS_MODELOS = {
    "face_landmarker.task": [
        f"{_BASE}/face_landmarker/face_landmarker/float16/latest/face_landmarker.task",
        f"{_BASE}/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
    ],
    "hand_landmarker.task": [
        f"{_BASE}/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task",
        f"{_BASE}/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
    ],
}

# Puntos "rígidos" de la malla facial (frente, nariz, ojos, mejillas): no se mueven al hablar.
PUNTOS_RIGIDOS = [10, 67, 109, 338, 297, 234, 454, 127, 356,
                  33, 133, 362, 263, 1, 4, 6, 168, 197, 195, 5,
                  50, 280, 205, 425]
# (centro del iris, esquina izquierda del ojo, esquina derecha del ojo) en la imagen
OJOS = ((468, 33, 133), (473, 362, 263))


# ------------------------------------------------------------------ modelos
def asegurar_modelo(nombre):
    """Devuelve la ruta del modelo; lo descarga la primera vez."""
    ruta = os.path.join(CARPETA_MODELOS, nombre)
    if os.path.isfile(ruta) and os.path.getsize(ruta) > 100_000:
        return ruta
    os.makedirs(CARPETA_MODELOS, exist_ok=True)
    for url in URLS_MODELOS[nombre]:
        try:
            print(f"Descargando {nombre} (solo la primera vez)...")
            urllib.request.urlretrieve(url, ruta + ".tmp")
            os.replace(ruta + ".tmp", ruta)
            return ruta
        except Exception as error:
            print(f"  no se pudo con {url}: {error}")
    sys.exit(f"\nNo pude descargar {nombre}. Descárgalo a mano de:\n  {URLS_MODELOS[nombre][0]}\n"
             f"y guárdalo en:\n  {ruta}")


def leer_modelo(nombre):
    # Se pasa como bytes (no como ruta) para que funcione aunque tu usuario de Windows tenga tildes o ñ.
    with open(asegurar_modelo(nombre), "rb") as archivo:
        return archivo.read()


# ------------------------------------------------------------------ sonido
def crear_sirena_wav(ruta, volumen=VOLUMEN_SIRENA, seg=1.3, sr=44100):
    """Genera una sirena chillona: frecuencia que sube y baja muy rápido + distorsión."""
    t = np.arange(int(sr * seg)) / sr
    freq = 1200 + 600 * np.sin(2 * np.pi * 7 * t) + 250 * np.sin(2 * np.pi * 1.1 * t)
    fase = 2 * np.pi * np.cumsum(freq) / sr
    onda = np.sin(fase) + 0.6 * np.sin(2 * fase) + 0.4 * np.sin(3 * fase)
    onda = np.tanh(2.5 * onda)                                   # distorsión = más chillido
    onda = onda * np.minimum(1.0, np.minimum(t, seg - t) / 0.02)  # entrada/salida suave (sin "clic")
    onda = onda / np.max(np.abs(onda)) * max(0.0, min(1.0, volumen))
    with wave.open(ruta, "wb") as archivo:
        archivo.setnchannels(1)
        archivo.setsampwidth(2)
        archivo.setframerate(sr)
        archivo.writeframes((onda * 32767).astype(np.int16).tobytes())


def reproducir_wav(ruta):
    """Reproduce un .wav y espera a que termine (se llama desde un hilo)."""
    if sys.platform.startswith("win"):
        import winsound
        winsound.PlaySound(ruta, winsound.SND_FILENAME)
    elif sys.platform == "darwin":
        subprocess.run(["afplay", ruta], check=False)
    else:
        for programa in (["paplay"], ["aplay", "-q"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]):
            if shutil.which(programa[0]):
                subprocess.run(programa + [ruta], check=False)
                return
        print("\a", end="", flush=True)


def hablar(frase):
    """Dice la frase en voz alta con la voz del sistema (sin instalar nada extra)."""
    if sys.platform.startswith("win"):
        limpia = frase.replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$v = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$v.Volume = 100; $v.Rate = 2; "
            "$es = $v.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'es*' } "
            "| Select-Object -First 1; "
            "if ($es) { $v.SelectVoice($es.VoiceInfo.Name) }; "
            f"$v.Speak('{limpia}')"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    elif sys.platform == "darwin":
        resultado = subprocess.run(["say", "-v", "Paulina", frase], check=False, capture_output=True)
        if resultado.returncode != 0:
            subprocess.run(["say", frase], check=False)
    else:
        for programa in (["espeak-ng", "-v", "es", "-a", "200"], ["espeak", "-v", "es", "-a", "200"],
                         ["spd-say", "-l", "es"]):
            if shutil.which(programa[0]):
                subprocess.run(programa + [frase], check=False)
                return


class Gritador:
    """Sirena + voz en un hilo aparte, para que el video no se congele."""

    def __init__(self):
        propio = os.path.join(CARPETA, ARCHIVO_GRITO)
        if os.path.isfile(propio):
            self.wav = propio
        else:
            self.wav = os.path.join(tempfile.gettempdir(), "vigilante_sirena.wav")
            crear_sirena_wav(self.wav)
        self.ocupado = False

    def gritar(self, frase):
        """Devuelve True si empezó a gritar (False si todavía está sonando el grito anterior)."""
        if self.ocupado:
            return False
        self.ocupado = True
        threading.Thread(target=self._trabajo, args=(frase,), daemon=True).start()
        return True

    def _trabajo(self, frase):
        try:
            reproducir_wav(self.wav)
            hablar(frase)
        except Exception as error:
            print(f"[aviso] No pude reproducir el grito: {error}")
        finally:
            self.ocupado = False


# ------------------------------------------------------------------ geometría
def puntos_cara(lms, w, h):
    """Puntos rígidos de la cara en 3D (z viene en la misma escala que x)."""
    return np.array([(lms[i].x * w, lms[i].y * h, (lms[i].z or 0.0) * w) for i in PUNTOS_RIGIDOS],
                    dtype=np.float64)


def razones_ojos(lms, w):
    """Posición del iris entre las dos esquinas de cada ojo (~0.5 = mirando al frente)."""
    razones = []
    for iris, esquina_a, esquina_b in OJOS:
        xa, xb, xi = lms[esquina_a].x * w, lms[esquina_b].x * w, lms[iris].x * w
        ancho = xb - xa
        razones.append((xi - xa) / ancho if abs(ancho) > 1.0 else 0.5)
    return np.array(razones)


def rotacion_cabeza(ref, act):
    """Giro (izq/der) e inclinación (arriba/abajo), en grados, de la cabeza actual respecto a la calibrada.

    Alinea las dos nubes de puntos 3D con el método de Kabsch (SVD) y saca los ángulos de la rotación.
    """
    a = ref - ref.mean(axis=0)
    b = act - act.mean(axis=0)
    u, _, vt = np.linalg.svd(a.T @ b)
    d = 1.0 if np.linalg.det(vt.T @ u.T) >= 0 else -1.0
    r = vt.T @ np.diag([1.0, 1.0, d]) @ u.T          # b ≈ r · a
    giro = math.degrees(math.atan2(r[0, 2], r[2, 2]))
    inclinacion = math.degrees(math.asin(max(-1.0, min(1.0, -r[1, 2]))))
    return giro, inclinacion


class Vigilante:
    """Decide si estás mirando a la pantalla comparando tu pose actual con la calibrada."""

    def __init__(self):
        self.giro = self.inclinacion = self.desvio_ojos = 0.0
        self.reiniciar_calibracion()

    def reiniciar_calibracion(self):
        self.calibrando = True
        self.inicio_calib = None
        self.muestras_cara = []
        self.muestras_ojos = []
        self.ref_cara = None
        self.ref_ojos = None

    def evaluar(self, lms, w, h, ahora):
        """Devuelve 'calibrando', 'ok', 'desviado' o 'ausente'."""
        if self.inicio_calib is None:
            self.inicio_calib = ahora

        pts = ojos = None
        if lms is not None:
            pts = puntos_cara(lms, w, h)
            if USAR_OJOS and len(lms) >= 478:
                ojos = razones_ojos(lms, w)

        if self.calibrando:
            if pts is not None:
                self.muestras_cara.append(pts)
                if ojos is not None:
                    self.muestras_ojos.append(ojos)
            if ahora - self.inicio_calib >= SEG_CALIBRACION:
                if len(self.muestras_cara) >= 8:
                    self.ref_cara = np.mean(self.muestras_cara, axis=0)
                    self.ref_ojos = np.mean(self.muestras_ojos, axis=0) if self.muestras_ojos else None
                    self.calibrando = False
                    self.giro = self.inclinacion = self.desvio_ojos = 0.0
                    print("Calibrado. Desde ahora te vigilo...")
                else:  # casi no te vio la cámara: empezar de nuevo
                    self.inicio_calib = ahora
                    self.muestras_cara.clear()
                    self.muestras_ojos.clear()
            return "calibrando"

        if pts is None:
            return "ausente"

        giro, inclinacion = rotacion_cabeza(self.ref_cara, pts)
        self.giro = 0.6 * self.giro + 0.4 * giro                    # suavizado
        self.inclinacion = 0.6 * self.inclinacion + 0.4 * inclinacion
        if ojos is not None and self.ref_ojos is not None:
            desvio = float(np.mean(np.abs(ojos - self.ref_ojos)))
            self.desvio_ojos = 0.6 * self.desvio_ojos + 0.4 * desvio

        fuera = (abs(self.giro) > GIRO_MAX
                 or abs(self.inclinacion) > INCLINACION_MAX
                 or (USAR_OJOS and self.desvio_ojos > MIRADA_MAX))
        return "desviado" if fuera else "ok"


# ------------------------------------------------------------------ dibujo
def texto(frame, msg, pos, escala=0.6, color=(255, 255, 255), grosor=2):
    cv2.putText(frame, msg, pos, FUENTE, escala, (0, 0, 0), grosor + 3, cv2.LINE_AA)
    cv2.putText(frame, msg, pos, FUENTE, escala, color, grosor, cv2.LINE_AA)


def dibujar_caja(frame, landmarks, color, etiqueta, margen=0.12):
    """Cuadro alrededor de un conjunto de puntos (una mano o una cara) con su etiqueta."""
    h, w = frame.shape[:2]
    xs = [p.x for p in landmarks]
    ys = [p.y for p in landmarks]
    x1, x2 = min(xs) * w, max(xs) * w
    y1, y2 = min(ys) * h, max(ys) * h
    mx, my = (x2 - x1) * margen, (y2 - y1) * margen
    x1, y1 = int(max(0, x1 - mx)), int(max(0, y1 - my))
    x2, y2 = int(min(w - 1, x2 + mx)), int(min(h - 1, y2 + my))
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3)
    (tw, th), _ = cv2.getTextSize(etiqueta, FUENTE, 0.6, 2)
    y_texto = y1 - 6 if y1 - th - 12 >= 0 else y1 + th + 8   # fuera del cuadro, o dentro si no cabe
    cv2.rectangle(frame, (x1, y_texto - th - 6), (x1 + tw + 8, y_texto + 6), color, -1)
    cv2.putText(frame, etiqueta, (x1 + 4, y_texto), FUENTE, 0.6, (255, 255, 255), 2, cv2.LINE_AA)


def abrir_camara():
    if sys.platform.startswith("win"):
        cap = cv2.VideoCapture(CAMARA, cv2.CAP_DSHOW)
    else:
        cap = cv2.VideoCapture(CAMARA)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, ANCHO)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, ALTO)
    if not cap.isOpened():
        sys.exit("No pude abrir la cámara. Cierra otras apps que la usen (Zoom, Teams, OBS...) "
                 "o cambia CAMARA = 1 en la configuración.")
    return cap


# ------------------------------------------------------------------ programa principal
def main():
    BaseOptions = mp.tasks.BaseOptions
    RunningMode = mp.tasks.vision.RunningMode
    FaceLandmarker = mp.tasks.vision.FaceLandmarker
    FaceLandmarkerOptions = mp.tasks.vision.FaceLandmarkerOptions
    HandLandmarker = mp.tasks.vision.HandLandmarker
    HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions

    opciones_cara = FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_buffer=leer_modelo("face_landmarker.task")),
        running_mode=RunningMode.VIDEO,
        num_faces=1,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    opciones_manos = HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_buffer=leer_modelo("hand_landmarker.task")),
        running_mode=RunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=0.5,
        min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    cap = abrir_camara()
    gritador = Gritador()
    vigilante = Vigilante()
    silencio = False
    desde = None                 # desde cuándo estás distraído
    ultimo_grito = 0.0
    t0 = t_prev = time.time()
    ultimo_ts = -1
    fps = 0.0
    fallos = 0

    print("Vigilante de pantalla iniciado.")
    print("Mira a la pantalla unos segundos para calibrar.")
    print("Teclas: C = recalibrar | M = silenciar | Q o ESC = salir")

    try:
        with FaceLandmarker.create_from_options(opciones_cara) as detector_cara, \
                HandLandmarker.create_from_options(opciones_manos) as detector_manos:
            while True:
                ok, frame = cap.read()
                if not ok:
                    fallos += 1
                    if fallos > 50:
                        print("La cámara dejó de enviar imagen.")
                        break
                    time.sleep(0.02)
                    continue
                fallos = 0
                if ESPEJO:
                    frame = cv2.flip(frame, 1)
                h, w = frame.shape[:2]
                ahora = time.time()

                # --- detección (cara + manos) ---
                ts_ms = int((ahora - t0) * 1000)
                if ts_ms <= ultimo_ts:
                    ts_ms = ultimo_ts + 1
                ultimo_ts = ts_ms
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                imagen = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                res_cara = detector_cara.detect_for_video(imagen, ts_ms)
                res_manos = detector_manos.detect_for_video(imagen, ts_ms)

                lms = res_cara.face_landmarks[0] if res_cara.face_landmarks else None
                estado = vigilante.evaluar(lms, w, h, ahora)

                # --- cuadros rojos ---
                if lms is not None:
                    etiqueta = "CARA" if estado in ("ok", "calibrando") else "CARA (DESVIADA)"
                    dibujar_caja(frame, lms, COLOR_CARA, etiqueta)
                for mano in res_manos.hand_landmarks:
                    dibujar_caja(frame, mano, COLOR_MANO, "MANO")
                    if PUNTOS_EN_MANOS:
                        for p in mano:
                            cv2.circle(frame, (int(p.x * w), int(p.y * h)), 3, COLOR_MANO, -1)

                # --- ¿hay que gritar? ---
                espera = 0.0
                if estado in ("ok", "calibrando"):
                    desde = None
                else:
                    if desde is None:
                        desde = ahora
                    espera = ahora - desde
                alerta = espera >= SEG_TOLERANCIA
                if alerta and not silencio and ahora - ultimo_grito >= SEG_ENTRE_GRITOS:
                    frases = FRASES_AUSENTE if estado == "ausente" else FRASES_DESVIADO
                    if gritador.gritar(random.choice(frases)):
                        ultimo_grito = ahora

                # --- avisos en pantalla (solo ASCII: OpenCV no dibuja tildes) ---
                if alerta:
                    if int(ahora * 4) % 2 == 0:
                        cv2.rectangle(frame, (12, 12), (w - 13, h - 13), (0, 0, 255), 24)
                    mensaje = "MIRA LA PANTALLA!"
                    (tw, _), _ = cv2.getTextSize(mensaje, FUENTE, 1.6, 4)
                    texto(frame, mensaje, (max(10, (w - tw) // 2), h // 2), 1.6, (0, 0, 255), 4)

                if estado == "calibrando":
                    inicio = vigilante.inicio_calib or ahora
                    resta = max(0.0, SEG_CALIBRACION - (ahora - inicio))
                    color, msg = (0, 140, 255), f"CALIBRANDO... mira a la pantalla ({resta:.0f}s)"
                elif estado == "ok":
                    color, msg = (0, 150, 0), "ATENTO"
                else:
                    nombre = "NO TE VEO" if estado == "ausente" else "DISTRAIDO"
                    color, msg = (0, 0, 200), f"{nombre}  {espera:.1f}s"
                cv2.rectangle(frame, (0, 0), (w, 38), color, -1)
                texto(frame, msg, (10, 26), 0.8)

                dt = ahora - t_prev
                t_prev = ahora
                if dt > 0:
                    fps = 0.9 * fps + 0.1 / dt
                texto(frame, f"giro {vigilante.giro:+.0f}  incl {vigilante.inclinacion:+.0f}  "
                             f"ojos {vigilante.desvio_ojos:.2f}  FPS {fps:.0f}", (10, h - 38), 0.5)
                texto(frame, "C: calibrar   M: " + ("activar sonido" if silencio else "silenciar")
                      + "   Q: salir", (10, h - 12), 0.5)

                cv2.imshow(VENTANA, frame)
                tecla = cv2.waitKey(1) & 0xFF
                if tecla in (27, ord("q"), ord("Q")):
                    break
                if tecla in (ord("c"), ord("C")):
                    vigilante.reiniciar_calibracion()
                    desde = None
                    print("Recalibrando: mira a la pantalla...")
                if tecla in (ord("m"), ord("M")):
                    silencio = not silencio
                    print("Gritos silenciados." if silencio else "Gritos activados.")
                if cv2.getWindowProperty(VENTANA, cv2.WND_PROP_VISIBLE) < 1:
                    break
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VIGILANTE DE PANTALLA
=====================
Usa tu webcam para:
  - Saber si estás mirando hacia la pantalla (giro de la cabeza + hacia dónde miran tus ojos).
  - Detectar tus manos y tu cara y marcarlas con cuadros ROJOS.
  - GRITARTE (sirena + voz) si dejas de mirar la pantalla o desapareces de la cámara.

Instalación (una sola vez):
    pip install mediapipe opencv-python numpy

Uso:
    python vigilante_pantalla.py

- La primera vez descarga dos modelos pequeños de Google (~10 MB) a la carpeta "modelos".
- Al arrancar, mira a la pantalla unos segundos: así aprende cuál es tu posición "normal".

Teclas (con la ventana del video activa):
    C        recalibrar (mira a la pantalla y pulsa C)
    M        silenciar / activar los gritos
    Q o ESC  salir

Para un grito propio, pon un archivo "grito.wav" junto a este script.
"""

import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave

os.environ.setdefault("GLOG_minloglevel", "2")        # menos ruido en la consola
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

try:
    import cv2
    import mediapipe as mp
    import numpy as np
except ImportError as error:
    sys.exit(f"Falta una librería ({error}).\n"
             "Instálala con:  pip install mediapipe opencv-python numpy")

# =============================== CONFIGURACIÓN ===============================
CAMARA = 0                   # 0 = cámara por defecto (prueba 1, 2... si tienes varias)
ESPEJO = True                # True = la imagen se ve como un espejo
ANCHO, ALTO = 960, 540       # resolución que se le pide a la cámara

SEG_CALIBRACION = 3.0        # segundos mirando la pantalla para calibrar (al iniciar y con C)
SEG_TOLERANCIA = 1.5         # segundos "distraído" antes de que te griten
SEG_ENTRE_GRITOS = 4.0       # pausa mínima entre un grito y el siguiente

GIRO_MAX = 28                # grados que puedes girar la cabeza (izq/der) y seguir "mirando"
INCLINACION_MAX = 20         # grados que puedes subir/bajar la cabeza
USAR_OJOS = True             # también vigila hacia dónde miran tus ojos
MIRADA_MAX = 0.16            # desvío tolerado de los ojos (0 = nada, ~0.3 = extremo)

COLOR_MANO = (0, 0, 255)     # BGR: rojo
COLOR_CARA = (0, 0, 255)     # BGR: rojo
PUNTOS_EN_MANOS = False      # True = además dibuja los 21 puntos de cada mano

VOLUMEN_SIRENA = 0.8         # 0.0 a 1.0 (cuidado si usas audífonos)
ARCHIVO_GRITO = "grito.wav"  # si existe junto al script, suena este en vez de la sirena

FRASES_DESVIADO = [
    "¡Eh! ¡Mira la pantalla!",
    "¡Oye! ¡Te estás distrayendo!",
    "¡Vuelve al trabajo ahora mismo!",
    "¡Concéntrate, por favor!",
    "¡La pantalla está aquí, no allá!",
]
FRASES_AUSENTE = [
    "¡No te veo! ¡Vuelve a mirar la pantalla!",
    "¡Dónde estás! ¡Vuelve a la computadora!",
]
# ============================================================================

CARPETA = os.path.dirname(os.path.abspath(__file__))
CARPETA_MODELOS = os.path.join(CARPETA, "modelos")
VENTANA = "Vigilante de pantalla"
FUENTE = cv2.FONT_HERSHEY_SIMPLEX

_BASE = "https://storage.googleapis.com/mediapipe-models"
URLS_MODELOS = {
    "face_landmarker.task": [
        f"{_BASE}/face_landmarker/face_landmarker/float16/latest/face_landmarker.task",
        f"{_BASE}/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
    ],
    "hand_landmarker.task": [
        f"{_BASE}/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task",
        f"{_BASE}/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
    ],
}

# Puntos "rígidos" de la malla facial (frente, nariz, ojos, mejillas): no se mueven al hablar.
PUNTOS_RIGIDOS = [10, 67, 109, 338, 297, 234, 454, 127, 356,
                  33, 133, 362, 263, 1, 4, 6, 168, 197, 195, 5,
                  50, 280, 205, 425]
# (centro del iris, esquina izquierda del ojo, esquina derecha del ojo) en la imagen
OJOS = ((468, 33, 133), (473, 362, 263))


# ------------------------------------------------------------------ modelos
def asegurar_modelo(nombre):
    """Devuelve la ruta del modelo; lo descarga la primera vez."""
    ruta = os.path.join(CARPETA_MODELOS, nombre)
    if os.path.isfile(ruta) and os.path.getsize(ruta) > 100_000:
        return ruta
    os.makedirs(CARPETA_MODELOS, exist_ok=True)
    for url in URLS_MODELOS[nombre]:
        try:
            print(f"Descargando {nombre} (solo la primera vez)...")
            urllib.request.urlretrieve(url, ruta + ".tmp")
            os.replace(ruta + ".tmp", ruta)
            return ruta
        except Exception as error:
            print(f"  no se pudo con {url}: {error}")
    sys.exit(f"\nNo pude descargar {nombre}. Descárgalo a mano de:\n  {URLS_MODELOS[nombre][0]}\n"
             f"y guárdalo en:\n  {ruta}")


def leer_modelo(nombre):
    # Se pasa como bytes (no como ruta) para que funcione aunque tu usuario de Windows tenga tildes o ñ.
    with open(asegurar_modelo(nombre), "rb") as archivo:
        return archivo.read()


# ------------------------------------------------------------------ sonido
def crear_sirena_wav(ruta, volumen=VOLUMEN_SIRENA, seg=1.3, sr=44100):
    """Genera una sirena chillona: frecuencia que sube y baja muy rápido + distorsión."""
    t = np.arange(int(sr * seg)) / sr
    freq = 1200 + 600 * np.sin(2 * np.pi * 7 * t) + 250 * np.sin(2 * np.pi * 1.1 * t)
    fase = 2 * np.pi * np.cumsum(freq) / sr
    onda = np.sin(fase) + 0.6 * np.sin(2 * fase) + 0.4 * np.sin(3 * fase)
    onda = np.tanh(2.5 * onda)                                   # distorsión = más chillido
    onda = onda * np.minimum(1.0, np.minimum(t, seg - t) / 0.02)  # entrada/salida suave (sin "clic")
    onda = onda / np.max(np.abs(onda)) * max(0.0, min(1.0, volumen))
    with wave.open(ruta, "wb") as archivo:
        archivo.setnchannels(1)
        archivo.setsampwidth(2)
        archivo.setframerate(sr)
        archivo.writeframes((onda * 32767).astype(np.int16).tobytes())


def reproducir_wav(ruta):
    """Reproduce un .wav y espera a que termine (se llama desde un hilo)."""
    if sys.platform.startswith("win"):
        import winsound
        winsound.PlaySound(ruta, winsound.SND_FILENAME)
    elif sys.platform == "darwin":
        subprocess.run(["afplay", ruta], check=False)
    else:
        for programa in (["paplay"], ["aplay", "-q"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]):
            if shutil.which(programa[0]):
                subprocess.run(programa + [ruta], check=False)
                return
        print("\a", end="", flush=True)


def hablar(frase):
    """Dice la frase en voz alta con la voz del sistema (sin instalar nada extra)."""
    if sys.platform.startswith("win"):
        limpia = frase.replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$v = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$v.Volume = 100; $v.Rate = 2; "
            "$es = $v.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'es*' } "
            "| Select-Object -First 1; "
            "if ($es) { $v.SelectVoice($es.VoiceInfo.Name) }; "
            f"$v.Speak('{limpia}')"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    elif sys.platform == "darwin":
        resultado = subprocess.run(["say", "-v", "Paulina", frase], check=False, capture_output=True)
        if resultado.returncode != 0:
            subprocess.run(["say", frase], check=False)
    else:
        for programa in (["espeak-ng", "-v", "es", "-a", "200"], ["espeak", "-v", "es", "-a", "200"],
                         ["spd-say", "-l", "es"]):
            if shutil.which(programa[0]):
                subprocess.run(programa + [frase], check=False)
                return


class Gritador:
    """Sirena + voz en un hilo aparte, para que el video no se congele."""

    def __init__(self):
        propio = os.path.join(CARPETA, ARCHIVO_GRITO)
        if os.path.isfile(propio):
            self.wav = propio
        else:
            self.wav = os.path.join(tempfile.gettempdir(), "vigilante_sirena.wav")
            crear_sirena_wav(self.wav)
        self.ocupado = False

    def gritar(self, frase):
        """Devuelve True si empezó a gritar (False si todavía está sonando el grito anterior)."""
        if self.ocupado:
            return False
        self.ocupado = True
        threading.Thread(target=self._trabajo, args=(frase,), daemon=True).start()
        return True

    def _trabajo(self, frase):
        try:
            reproducir_wav(self.wav)
            hablar(frase)
        except Exception as error:
            print(f"[aviso] No pude reproducir el grito: {error}")
        finally:
            self.ocupado = False


# ------------------------------------------------------------------ geometría
def puntos_cara(lms, w, h):
    """Puntos rígidos de la cara en 3D (z viene en la misma escala que x)."""
    return np.array([(lms[i].x * w, lms[i].y * h, (lms[i].z or 0.0) * w) for i in PUNTOS_RIGIDOS],
                    dtype=np.float64)


def razones_ojos(lms, w):
    """Posición del iris entre las dos esquinas de cada ojo (~0.5 = mirando al frente)."""
    razones = []
    for iris, esquina_a, esquina_b in OJOS:
        xa, xb, xi = lms[esquina_a].x * w, lms[esquina_b].x * w, lms[iris].x * w
        ancho = xb - xa
        razones.append((xi - xa) / ancho if abs(ancho) > 1.0 else 0.5)
    return np.array(razones)


def rotacion_cabeza(ref, act):
    """Giro (izq/der) e inclinación (arriba/abajo), en grados, de la cabeza actual respecto a la calibrada.

    Alinea las dos nubes de puntos 3D con el método de Kabsch (SVD) y saca los ángulos de la rotación.
    """
    a = ref - ref.mean(axis=0)
    b = act - act.mean(axis=0)
    u, _, vt = np.linalg.svd(a.T @ b)
    d = 1.0 if np.linalg.det(vt.T @ u.T) >= 0 else -1.0
    r = vt.T @ np.diag([1.0, 1.0, d]) @ u.T          # b ≈ r · a
    giro = math.degrees(math.atan2(r[0, 2], r[2, 2]))
    inclinacion = math.degrees(math.asin(max(-1.0, min(1.0, -r[1, 2]))))
    return giro, inclinacion


class Vigilante:
    """Decide si estás mirando a la pantalla comparando tu pose actual con la calibrada."""

    def __init__(self):
        self.giro = self.inclinacion = self.desvio_ojos = 0.0
        self.reiniciar_calibracion()

    def reiniciar_calibracion(self):
        self.calibrando = True
        self.inicio_calib = None
        self.muestras_cara = []
        self.muestras_ojos = []
        self.ref_cara = None
        self.ref_ojos = None

    def evaluar(self, lms, w, h, ahora):
        """Devuelve 'calibrando', 'ok', 'desviado' o 'ausente'."""
        if self.inicio_calib is None:
            self.inicio_calib = ahora

        pts = ojos = None
        if lms is not None:
            pts = puntos_cara(lms, w, h)
            if USAR_OJOS and len(lms) >= 478:
                ojos = razones_ojos(lms, w)

        if self.calibrando:
            if pts is not None:
                self.muestras_cara.append(pts)
                if ojos is not None:
                    self.muestras_ojos.append(ojos)
            if ahora - self.inicio_calib >= SEG_CALIBRACION:
                if len(self.muestras_cara) >= 8:
                    self.ref_cara = np.mean(self.muestras_cara, axis=0)
                    self.ref_ojos = np.mean(self.muestras_ojos, axis=0) if self.muestras_ojos else None
                    self.calibrando = False
                    self.giro = self.inclinacion = self.desvio_ojos = 0.0
                    print("Calibrado. Desde ahora te vigilo...")
                else:  # casi no te vio la cámara: empezar de nuevo
                    self.inicio_calib = ahora
                    self.muestras_cara.clear()
                    self.muestras_ojos.clear()
            return "calibrando"

        if pts is None:
            return "ausente"

        giro, inclinacion = rotacion_cabeza(self.ref_cara, pts)
        self.giro = 0.6 * self.giro + 0.4 * giro                    # suavizado
        self.inclinacion = 0.6 * self.inclinacion + 0.4 * inclinacion
        if ojos is not None and self.ref_ojos is not None:
            desvio = float(np.mean(np.abs(ojos - self.ref_ojos)))
            self.desvio_ojos = 0.6 * self.desvio_ojos + 0.4 * desvio

        fuera = (abs(self.giro) > GIRO_MAX
                 or abs(self.inclinacion) > INCLINACION_MAX
                 or (USAR_OJOS and self.desvio_ojos > MIRADA_MAX))
        return "desviado" if fuera else "ok"


# ------------------------------------------------------------------ dibujo
def texto(frame, msg, pos, escala=0.6, color=(255, 255, 255), grosor=2):
    cv2.putText(frame, msg, pos, FUENTE, escala, (0, 0, 0), grosor + 3, cv2.LINE_AA)
    cv2.putText(frame, msg, pos, FUENTE, escala, color, grosor, cv2.LINE_AA)


def dibujar_caja(frame, landmarks, color, etiqueta, margen=0.12):
    """Cuadro alrededor de un conjunto de puntos (una mano o una cara) con su etiqueta."""
    h, w = frame.shape[:2]
    xs = [p.x for p in landmarks]
    ys = [p.y for p in landmarks]
    x1, x2 = min(xs) * w, max(xs) * w
    y1, y2 = min(ys) * h, max(ys) * h
    mx, my = (x2 - x1) * margen, (y2 - y1) * margen
    x1, y1 = int(max(0, x1 - mx)), int(max(0, y1 - my))
    x2, y2 = int(min(w - 1, x2 + mx)), int(min(h - 1, y2 + my))
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3)
    (tw, th), _ = cv2.getTextSize(etiqueta, FUENTE, 0.6, 2)
    y_texto = y1 - 6 if y1 - th - 12 >= 0 else y1 + th + 8   # fuera del cuadro, o dentro si no cabe
    cv2.rectangle(frame, (x1, y_texto - th - 6), (x1 + tw + 8, y_texto + 6), color, -1)
    cv2.putText(frame, etiqueta, (x1 + 4, y_texto), FUENTE, 0.6, (255, 255, 255), 2, cv2.LINE_AA)


def abrir_camara():
    if sys.platform.startswith("win"):
        cap = cv2.VideoCapture(CAMARA, cv2.CAP_DSHOW)
    else:
        cap = cv2.VideoCapture(CAMARA)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, ANCHO)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, ALTO)
    if not cap.isOpened():
        sys.exit("No pude abrir la cámara. Cierra otras apps que la usen (Zoom, Teams, OBS...) "
                 "o cambia CAMARA = 1 en la configuración.")
    return cap


# ------------------------------------------------------------------ programa principal
def main():
    BaseOptions = mp.tasks.BaseOptions
    RunningMode = mp.tasks.vision.RunningMode
    FaceLandmarker = mp.tasks.vision.FaceLandmarker
    FaceLandmarkerOptions = mp.tasks.vision.FaceLandmarkerOptions
    HandLandmarker = mp.tasks.vision.HandLandmarker
    HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions

    opciones_cara = FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_buffer=leer_modelo("face_landmarker.task")),
        running_mode=RunningMode.VIDEO,
        num_faces=1,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    opciones_manos = HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_buffer=leer_modelo("hand_landmarker.task")),
        running_mode=RunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=0.5,
        min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    cap = abrir_camara()
    gritador = Gritador()
    vigilante = Vigilante()
    silencio = False
    desde = None                 # desde cuándo estás distraído
    ultimo_grito = 0.0
    t0 = t_prev = time.time()
    ultimo_ts = -1
    fps = 0.0
    fallos = 0

    print("Vigilante de pantalla iniciado.")
    print("Mira a la pantalla unos segundos para calibrar.")
    print("Teclas: C = recalibrar | M = silenciar | Q o ESC = salir")

    try:
        with FaceLandmarker.create_from_options(opciones_cara) as detector_cara, \
                HandLandmarker.create_from_options(opciones_manos) as detector_manos:
            while True:
                ok, frame = cap.read()
                if not ok:
                    fallos += 1
                    if fallos > 50:
                        print("La cámara dejó de enviar imagen.")
                        break
                    time.sleep(0.02)
                    continue
                fallos = 0
                if ESPEJO:
                    frame = cv2.flip(frame, 1)
                h, w = frame.shape[:2]
                ahora = time.time()

                # --- detección (cara + manos) ---
                ts_ms = int((ahora - t0) * 1000)
                if ts_ms <= ultimo_ts:
                    ts_ms = ultimo_ts + 1
                ultimo_ts = ts_ms
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                imagen = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                res_cara = detector_cara.detect_for_video(imagen, ts_ms)
                res_manos = detector_manos.detect_for_video(imagen, ts_ms)

                lms = res_cara.face_landmarks[0] if res_cara.face_landmarks else None
                estado = vigilante.evaluar(lms, w, h, ahora)

                # --- cuadros rojos ---
                if lms is not None:
                    etiqueta = "CARA" if estado in ("ok", "calibrando") else "CARA (DESVIADA)"
                    dibujar_caja(frame, lms, COLOR_CARA, etiqueta)
                for mano in res_manos.hand_landmarks:
                    dibujar_caja(frame, mano, COLOR_MANO, "MANO")
                    if PUNTOS_EN_MANOS:
                        for p in mano:
                            cv2.circle(frame, (int(p.x * w), int(p.y * h)), 3, COLOR_MANO, -1)

                # --- ¿hay que gritar? ---
                espera = 0.0
                if estado in ("ok", "calibrando"):
                    desde = None
                else:
                    if desde is None:
                        desde = ahora
                    espera = ahora - desde
                alerta = espera >= SEG_TOLERANCIA
                if alerta and not silencio and ahora - ultimo_grito >= SEG_ENTRE_GRITOS:
                    frases = FRASES_AUSENTE if estado == "ausente" else FRASES_DESVIADO
                    if gritador.gritar(random.choice(frases)):
                        ultimo_grito = ahora

                # --- avisos en pantalla (solo ASCII: OpenCV no dibuja tildes) ---
                if alerta:
                    if int(ahora * 4) % 2 == 0:
                        cv2.rectangle(frame, (12, 12), (w - 13, h - 13), (0, 0, 255), 24)
                    mensaje = "MIRA LA PANTALLA!"
                    (tw, _), _ = cv2.getTextSize(mensaje, FUENTE, 1.6, 4)
                    texto(frame, mensaje, (max(10, (w - tw) // 2), h // 2), 1.6, (0, 0, 255), 4)

                if estado == "calibrando":
                    inicio = vigilante.inicio_calib or ahora
                    resta = max(0.0, SEG_CALIBRACION - (ahora - inicio))
                    color, msg = (0, 140, 255), f"CALIBRANDO... mira a la pantalla ({resta:.0f}s)"
                elif estado == "ok":
                    color, msg = (0, 150, 0), "ATENTO"
                else:
                    nombre = "NO TE VEO" if estado == "ausente" else "DISTRAIDO"
                    color, msg = (0, 0, 200), f"{nombre}  {espera:.1f}s"
                cv2.rectangle(frame, (0, 0), (w, 38), color, -1)
                texto(frame, msg, (10, 26), 0.8)

                dt = ahora - t_prev
                t_prev = ahora
                if dt > 0:
                    fps = 0.9 * fps + 0.1 / dt
                texto(frame, f"giro {vigilante.giro:+.0f}  incl {vigilante.inclinacion:+.0f}  "
                             f"ojos {vigilante.desvio_ojos:.2f}  FPS {fps:.0f}", (10, h - 38), 0.5)
                texto(frame, "C: calibrar   M: " + ("activar sonido" if silencio else "silenciar")
                      + "   Q: salir", (10, h - 12), 0.5)

                cv2.imshow(VENTANA, frame)
                tecla = cv2.waitKey(1) & 0xFF
                if tecla in (27, ord("q"), ord("Q")):
                    break
                if tecla in (ord("c"), ord("C")):
                    vigilante.reiniciar_calibracion()
                    desde = None
                    print("Recalibrando: mira a la pantalla...")
                if tecla in (ord("m"), ord("M")):
                    silencio = not silencio
                    print("Gritos silenciados." if silencio else "Gritos activados.")
                if cv2.getWindowProperty(VENTANA, cv2.WND_PROP_VISIBLE) < 1:
                    break
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass