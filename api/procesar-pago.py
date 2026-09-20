import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler

MERCADOPAGO_ACCESS_TOKEN = os.environ.get("MERCADOPAGO_ACCESS_TOKEN")
MERCADOPAGO_PAGOS_URL = "https://api.mercadopago.com/v1/payments"

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

SITE_URL = os.environ.get("SITE_URL", "https://manosvivas.vercel.app")

ESTADOS_RESPUESTA = {
    "approved": "aprobado",
    "in_process": "pendiente",
    "pending": "pendiente",
    "authorized": "pendiente",
    "rejected": "rechazado",
    "cancelled": "rechazado",
}


class ErrorSolicitud(Exception):
    def __init__(self, status, mensaje):
        super().__init__(mensaje)
        self.status = status
        self.mensaje = mensaje


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
    if not resultado:
        raise ErrorSolicitud(404, "La reserva no existe.")
    return resultado[0]


def actualizar_reserva(reserva_id, cambios):
    fila_escapada = urllib.parse.quote(str(reserva_id), safe="")
    _supabase_request(
        "PATCH",
        f"reservas?id=eq.{fila_escapada}",
        cuerpo=cambios,
        headers_extra={"Prefer": "return=minimal"},
    )


def crear_pago(reserva, datos_brick):
    """El monto lo manda Supabase, nunca el navegador: el formulario del
    Brick solo aporta el token de la tarjeta y el medio de pago."""
    payload = {
        "transaction_amount": float(reserva["monto_total"]),
        "token": datos_brick.get("token"),
        "installments": datos_brick.get("installments") or 1,
        "payment_method_id": datos_brick.get("payment_method_id"),
        "issuer_id": datos_brick.get("issuer_id"),
        "description": reserva.get("servicio") or "Sesión Manos Vivas",
        "external_reference": str(reserva["id"]),
        "notification_url": f"{SITE_URL}/api/webhook-pago",
        "payer": {
            "email": (datos_brick.get("payer") or {}).get("email") or reserva.get("email_cliente"),
        },
    }

    identificacion = (datos_brick.get("payer") or {}).get("identification")
    if identificacion and identificacion.get("number"):
        payload["payer"]["identification"] = identificacion

    request = urllib.request.Request(
        MERCADOPAGO_PAGOS_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {MERCADOPAGO_ACCESS_TOKEN}",
            "Content-Type": "application/json",
            "X-Idempotency-Key": str(uuid.uuid4()),
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        detalle = error.read().decode("utf-8", "ignore")
        raise RuntimeError(f"Error de Mercado Pago ({error.code}): {detalle}")


def procesar_solicitud(datos):
    reserva_id = datos.get("reserva_id")
    datos_brick = datos.get("pago") or {}

    if not reserva_id:
        raise ErrorSolicitud(400, "Falta 'reserva_id'.")
    if not datos_brick.get("token"):
        raise ErrorSolicitud(400, "Faltan los datos de la tarjeta.")

    reserva = obtener_reserva(reserva_id)
    if reserva.get("estado") == "confirmada":
        return {"estado": "aprobado", "motivo": "La reserva ya estaba pagada."}

    pago = crear_pago(reserva, datos_brick)
    estado_mp = pago.get("status")
    estado_respuesta = ESTADOS_RESPUESTA.get(estado_mp, "pendiente")

    # La confirmación final y el evento en Calendar los hace webhook-pago.py,
    # que es la única fuente de verdad del estado del pago.
    actualizar_reserva(
        reserva_id,
        {
            "mp_payment_id": str(pago.get("id")),
            "estado": "pago_rechazado" if estado_respuesta == "rechazado" else "pago_pendiente",
        },
    )

    return {
        "estado": estado_respuesta,
        "payment_id": pago.get("id"),
        "detalle": pago.get("status_detail"),
    }


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            if not all([MERCADOPAGO_ACCESS_TOKEN, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY]):
                raise RuntimeError(
                    "Faltan variables de entorno: MERCADOPAGO_ACCESS_TOKEN, SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY."
                )

            largo_contenido = int(self.headers.get("Content-Length", 0))
            cuerpo_crudo = self.rfile.read(largo_contenido) if largo_contenido else b"{}"
            try:
                datos = json.loads(cuerpo_crudo)
            except json.JSONDecodeError:
                self._responder(400, {"error": "El cuerpo debe ser JSON válido."})
                return

            self._responder(200, procesar_solicitud(datos))
        except ErrorSolicitud as error:
            self._responder(error.status, {"error": error.mensaje})
        except RuntimeError as error:
            self._responder(502, {"error": str(error)})
        except Exception as error:
            self._responder(500, {"error": "Error inesperado.", "detalle": str(error)})

    def _responder(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
