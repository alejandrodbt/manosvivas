import hashlib
import hmac
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
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

MERCADOPAGO_ACCESS_TOKEN = os.environ.get("MERCADOPAGO_ACCESS_TOKEN")
MERCADOPAGO_WEBHOOK_SECRET = os.environ.get("MERCADOPAGO_WEBHOOK_SECRET")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
GOOGLE_SERVICE_ACCOUNT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "manosvivas-calendar-a2e0e23082e2.json",
)
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "manosvivascl@gmail.com")
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

ZONA_HORARIA = ZoneInfo("America/Santiago")
DURACION_POR_DEFECTO_MINUTOS = 60

ESTADOS_MERCADOPAGO = {
    "approved": "confirmada",
    "rejected": "pago_rechazado",
    "cancelled": "pago_rechazado",
    "refunded": "pago_reembolsado",
    "charged_back": "pago_reembolsado",
    "in_process": "pago_pendiente",
    "pending": "pago_pendiente",
}


# ---------- Supabase ----------

def _supabase_request(metodo, ruta, cuerpo=None, headers_extra=None):
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if headers_extra:
        headers.update(headers_extra)

    data = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
    request = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{ruta}", data=data, method=metodo, headers=headers
    )
    try:
        with urllib.request.urlopen(request) as response:
            crudo = response.read()
            return json.loads(crudo) if crudo else None
    except urllib.error.HTTPError as error:
        detalle = error.read().decode("utf-8", "ignore")
        raise RuntimeError(f"Error de Supabase ({error.code}): {detalle}")


def obtener_reserva(reserva_id):
    fila_escapada = urllib.parse.quote(str(reserva_id), safe="")
    resultado = _supabase_request("GET", f"reservas?id=eq.{fila_escapada}&select=*")
    return resultado[0] if resultado else None


def actualizar_reserva(reserva_id, cambios):
    fila_escapada = urllib.parse.quote(str(reserva_id), safe="")
    _supabase_request(
        "PATCH",
        f"reservas?id=eq.{fila_escapada}",
        cuerpo=cambios,
        headers_extra={"Prefer": "return=minimal"},
    )


# ---------- Mercado Pago ----------

def validar_firma_webhook(headers, query):
    """Ver 'Cómo asegurar el origen de una notificación' en los docs de
    Mercado Pago. Devuelve (valida, motivo): el motivo distingue "no vino
    firma" de "la firma no coincide", que se diagnostican distinto y no
    revelan el secreto.
    """
    if not MERCADOPAGO_WEBHOOK_SECRET:
        return True, "sin secreto configurado: validación omitida"

    firma = headers.get("x-signature", "")
    request_id = headers.get("x-request-id", "")
    data_id = query.get("data.id", [""])[0]

    if not firma:
        return False, (
            "la notificación no trae el header x-signature. Suele pasar cuando la URL "
            "no está registrada en el panel de Mercado Pago: sin registrar, las "
            "notificaciones llegan sin firmar."
        )

    partes = dict(par.split("=", 1) for par in firma.split(",") if "=" in par)
    ts = partes.get("ts", "")
    v1_recibido = partes.get("v1", "")
    if not ts or not v1_recibido:
        return False, "el header x-signature no trae ts y v1."

    manifest = f"id:{data_id.lower()};request-id:{request_id};ts:{ts};"
    firma_calculada = hmac.new(
        MERCADOPAGO_WEBHOOK_SECRET.encode("utf-8"), manifest.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(firma_calculada, v1_recibido):
        return False, (
            "la firma no coincide. Revisa que MERCADOPAGO_WEBHOOK_SECRET sea el de la "
            "misma aplicación y el mismo modo (prueba o producción) que envía la notificación."
        )

    return True, "firma válida"


def obtener_pago(payment_id):
    request = urllib.request.Request(
        f"https://api.mercadopago.com/v1/payments/{payment_id}",
        headers={"Authorization": f"Bearer {MERCADOPAGO_ACCESS_TOKEN}"},
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


# ---------- Google Calendar ----------

def obtener_credenciales_calendar():
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
        "Falta la credencial de Google Calendar: define GOOGLE_SERVICE_ACCOUNT_JSON."
    )


def minutos_de_complementos(complementos_texto):
    """La columna 'complementos' guarda el texto que escribe
    crear-preferencia.py: 'Reflexología · 10 min, Drenaje Linfático · 30 min'.
    El evento tiene que bloquear la sesión más esos minutos."""
    return sum(int(m) for m in re.findall(r"(\d+)\s*min", str(complementos_texto or "")))


def parsear_fecha_hora(valor):
    """Supabase devuelve timestamptz en UTC. El offset puede venir como
    '+00:00' o como '+00', y esta última forma solo la entiende
    fromisoformat desde Python 3.11: se normaliza antes de parsear."""
    texto = str(valor).strip().replace("Z", "+00:00")
    texto = re.sub(r"([+-]\d{2})$", r"\1:00", texto)

    dt = datetime.fromisoformat(texto)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ZONA_HORARIA)
    return dt.astimezone(ZONA_HORARIA)


def crear_evento_calendar(reserva):
    credenciales = obtener_credenciales_calendar()
    credenciales.refresh(GoogleAuthRequest())

    duracion_minutos = (reserva.get("duracion") or DURACION_POR_DEFECTO_MINUTOS) + minutos_de_complementos(
        reserva.get("complementos")
    )

    inicio = parsear_fecha_hora(reserva["fecha_hora_solicitada"])
    fin = inicio + timedelta(minutes=duracion_minutos)

    descripcion_lineas = [f"Reserva #{reserva['id']} — Manos Vivas"]
    if reserva.get("telefono_cliente"):
        descripcion_lineas.append(f"Teléfono: {reserva['telefono_cliente']}")
    if reserva.get("complementos"):
        descripcion_lineas.append(f"Complementos: {reserva['complementos']}")
    if reserva.get("estacionamiento"):
        descripcion_lineas.append(f"Estacionamiento: {reserva['estacionamiento']}")

    evento = {
        "summary": f"{reserva.get('servicio', 'Sesión')} — {reserva.get('nombre_cliente', '')}",
        "description": "\n".join(descripcion_lineas),
        "start": {"dateTime": inicio.isoformat(), "timeZone": "America/Santiago"},
        "end": {"dateTime": fin.isoformat(), "timeZone": "America/Santiago"},
        "attendees": [{"email": reserva["email_cliente"], "displayName": reserva.get("nombre_cliente")}],
    }
    if reserva.get("direccion"):
        evento["location"] = str(reserva["direccion"])

    request = urllib.request.Request(
        f"https://www.googleapis.com/calendar/v3/calendars/{urllib.parse.quote(GOOGLE_CALENDAR_ID, safe='')}/events?sendUpdates=all",
        data=json.dumps(evento).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {credenciales.token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        detalle = error.read().decode("utf-8", "ignore")
        raise RuntimeError(f"Error al crear evento en Calendar ({error.code}): {detalle}")


# ---------- Lógica principal ----------

def procesar_notificacion(tipo, payment_id):
    if tipo != "payment" or not payment_id:
        return {"ignorado": True}

    pago = obtener_pago(payment_id)
    reserva_id = pago.get("external_reference")
    estado_mp = pago.get("status")

    if not reserva_id:
        return {"ignorado": True, "motivo": "Pago sin external_reference."}

    reserva = obtener_reserva(reserva_id)
    if not reserva:
        return {"ignorado": True, "motivo": f"No existe la reserva {reserva_id}."}

    if reserva.get("estado") == "confirmada":
        return {"ok": True, "motivo": "La reserva ya estaba confirmada (notificación repetida)."}

    nuevo_estado = ESTADOS_MERCADOPAGO.get(estado_mp, "pago_pendiente")
    cambios = {
        "estado": nuevo_estado,
        "mp_payment_id": str(payment_id),
    }

    if nuevo_estado == "confirmada":
        evento = crear_evento_calendar(reserva)
        cambios["calendar_event_id"] = evento.get("id")

    actualizar_reserva(reserva_id, cambios)
    return {"ok": True, "reserva_id": reserva_id, "estado": nuevo_estado}


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            if not all([MERCADOPAGO_ACCESS_TOKEN, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY]):
                raise RuntimeError(
                    "Faltan variables de entorno: MERCADOPAGO_ACCESS_TOKEN, SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY."
                )

            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

            valida, motivo = validar_firma_webhook(self.headers, query)
            if not valida:
                print(f"webhook-pago: notificación rechazada — {motivo}")
                self._responder(401, {"error": "Firma de webhook inválida.", "motivo": motivo})
                return

            largo_contenido = int(self.headers.get("Content-Length", 0))
            cuerpo_crudo = self.rfile.read(largo_contenido) if largo_contenido else b"{}"
            try:
                cuerpo = json.loads(cuerpo_crudo) if cuerpo_crudo else {}
            except json.JSONDecodeError:
                cuerpo = {}

            tipo = cuerpo.get("type") or query.get("type", [None])[0]
            payment_id = (cuerpo.get("data") or {}).get("id") or query.get("data.id", [None])[0]

            resultado = procesar_notificacion(tipo, payment_id)
            self._responder(200, resultado)

        except Exception as error:
            # Un fallo interno responde 500 a propósito. Antes respondía 200
            # para evitar reintentos, pero eso hacía que Mercado Pago marcara
            # la notificación como entregada y no volviera a intentar: el
            # error quedaba invisible en los dos lados. Con 500, el panel lo
            # muestra en rojo y reintenta, que es lo que debe pasar cuando
            # una reserva pagada no alcanzó a confirmarse.
            print(f"webhook-pago ERROR: {error}")
            self._responder(500, {"ok": False, "error": str(error)})

    def do_GET(self):
        # Mercado Pago puede probar la URL del webhook con un GET al guardarla.
        self._responder(200, {"ok": True})

    def _responder(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
