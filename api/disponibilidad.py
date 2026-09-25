import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from zoneinfo import ZoneInfo

try:
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2 import service_account

    ERROR_DEPENDENCIA = None
except ImportError as error:
    # Sin esto, una dependencia ausente tumba la función entera y Vercel
    # solo muestra FUNCTION_INVOCATION_FAILED, sin decir qué faltó.
    ERROR_DEPENDENCIA = str(error)

GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
GOOGLE_SERVICE_ACCOUNT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "manosvivas-calendar-a2e0e23082e2.json",
)
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "manosvivascl@gmail.com")
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

ZONA_HORARIA = ZoneInfo("America/Santiago")
HORA_INICIO = int(os.environ.get("DISPONIBILIDAD_HORA_INICIO", "9"))
HORA_FIN = int(os.environ.get("DISPONIBILIDAD_HORA_FIN", "20"))
PASO_SLOT_MINUTOS = 30
DURACION_POR_DEFECTO_MINUTOS = 60
# Tiempo libre obligatorio entre una sesión y cualquier otro evento de la
# agenda (traslado, armado, descanso). Se exige antes y después.
MARGEN_ENTRE_SESIONES = timedelta(minutes=45)


def obtener_credenciales():
    """El JSON de la cuenta de servicio nunca se sube a git (ver .gitignore).
    En producción vive en la variable de entorno GOOGLE_SERVICE_ACCOUNT_JSON;
    el archivo local en la raíz del proyecto solo sirve para pruebas locales.
    """
    if ERROR_DEPENDENCIA:
        raise RuntimeError(
            f"Falta la dependencia google-auth en el despliegue ({ERROR_DEPENDENCIA}). "
            "Revisa que requirements.txt se haya instalado en el build."
        )

    if GOOGLE_SERVICE_ACCOUNT_JSON:
        info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    if os.path.exists(GOOGLE_SERVICE_ACCOUNT_FILE):
        return service_account.Credentials.from_service_account_file(
            GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
    raise RuntimeError(
        "Falta la credencial de Google Calendar: define GOOGLE_SERVICE_ACCOUNT_JSON "
        "como variable de entorno, o coloca el archivo de la cuenta de servicio "
        "en la raíz del proyecto para pruebas locales."
    )


def obtener_token_acceso():
    credenciales = obtener_credenciales()
    credenciales.refresh(GoogleAuthRequest())
    return credenciales.token


def consultar_bloques_ocupados(token, inicio_utc_iso, fin_utc_iso):
    body = {
        "timeMin": inicio_utc_iso,
        "timeMax": fin_utc_iso,
        "items": [{"id": GOOGLE_CALENDAR_ID}],
    }
    request = urllib.request.Request(
        "https://www.googleapis.com/calendar/v3/freeBusy",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request) as response:
        data = json.loads(response.read())
    calendario = data["calendars"].get(GOOGLE_CALENDAR_ID, {})
    if calendario.get("errors"):
        raise RuntimeError(f"Google Calendar devolvió errores: {calendario['errors']}")
    return calendario.get("busy", [])


def generar_slots(fecha, duracion_minutos):
    slots = []
    cursor = datetime.combine(fecha, time(hour=HORA_INICIO), tzinfo=ZONA_HORARIA)
    fin_dia = datetime.combine(fecha, time(hour=HORA_FIN), tzinfo=ZONA_HORARIA)
    paso = timedelta(minutes=PASO_SLOT_MINUTOS)
    duracion = timedelta(minutes=duracion_minutos)
    while cursor + duracion <= fin_dia:
        slots.append(cursor)
        cursor += paso
    return slots


def choca_con_margen(inicio_slot, fin_slot, bloques_ocupados):
    """Un horario sirve solo si empieza al menos 45 min después del fin de
    cada evento ocupado y termina al menos 45 min antes de su inicio."""
    return any(
        inicio_slot < ocupado_fin + MARGEN_ENTRE_SESIONES
        and fin_slot + MARGEN_ENTRE_SESIONES > ocupado_inicio
        for ocupado_inicio, ocupado_fin in bloques_ocupados
    )


def calcular_horarios_disponibles(fecha, duracion_minutos):
    token = obtener_token_acceso()

    inicio_dia = datetime.combine(fecha, time.min, tzinfo=ZONA_HORARIA)
    fin_dia = inicio_dia + timedelta(days=1)
    bloques_ocupados_utc = consultar_bloques_ocupados(
        token,
        inicio_dia.astimezone(timezone.utc).isoformat(),
        fin_dia.astimezone(timezone.utc).isoformat(),
    )
    bloques_ocupados = [
        (
            datetime.fromisoformat(bloque["start"].replace("Z", "+00:00")).astimezone(ZONA_HORARIA),
            datetime.fromisoformat(bloque["end"].replace("Z", "+00:00")).astimezone(ZONA_HORARIA),
        )
        for bloque in bloques_ocupados_utc
    ]

    ahora = datetime.now(ZONA_HORARIA)
    disponibles = []
    for inicio_slot in generar_slots(fecha, duracion_minutos):
        if inicio_slot < ahora:
            continue
        fin_slot = inicio_slot + timedelta(minutes=duracion_minutos)
        if not choca_con_margen(inicio_slot, fin_slot, bloques_ocupados):
            disponibles.append(inicio_slot.strftime("%H:%M"))

    return disponibles


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            fecha_str = query.get("fecha", [None])[0]
            duracion_str = query.get("duracion", [str(DURACION_POR_DEFECTO_MINUTOS)])[0]

            if not fecha_str:
                self._responder(400, {"error": "Falta el parámetro 'fecha' (formato YYYY-MM-DD)."})
                return

            try:
                fecha = date.fromisoformat(fecha_str)
                duracion_minutos = int(duracion_str)
                if duracion_minutos <= 0:
                    raise ValueError
            except ValueError:
                self._responder(400, {"error": "Parámetros inválidos: 'fecha' debe ser YYYY-MM-DD y 'duracion' un entero positivo de minutos."})
                return

            horarios = calcular_horarios_disponibles(fecha, duracion_minutos)
            self._responder(200, {
                "fecha": fecha_str,
                "duracion_minutos": duracion_minutos,
                "horarios_disponibles": horarios,
            })
        except RuntimeError as error:
            self._responder(500, {"error": str(error)})
        except urllib.error.HTTPError as error:
            self._responder(502, {
                "error": "Error al consultar Google Calendar.",
                "detalle": error.read().decode("utf-8", "ignore"),
            })
        except Exception as error:
            self._responder(500, {"error": "Error inesperado.", "detalle": str(error)})

    def _responder(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
