import hashlib
import hmac
import html
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

BREVO_API_KEY = os.environ.get("BREVO_API_KEY")
# Remitente verificado en Brevo. Con una dirección @gmail.com, Gmail puede
# mandar el correo a spam; lo ideal es una dirección del dominio propio.
CORREO_REMITENTE = os.environ.get("CORREO_REMITENTE", "manosvivascl@gmail.com")
SITE_URL = os.environ.get("SITE_URL", "https://manosvivas.cl")
WHATSAPP_URL = "https://wa.me/56995742775"

# Desde estos estados una reserva puede pasar a confirmarse.
ESTADOS_RECLAMABLES = ("pendiente_pago", "pago_pendiente", "pago_rechazado")

ESTADOS_MERCADOPAGO = {
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

    descripcion_lineas.append(f"Correo: {reserva['email_cliente']}")

    # Sin invitados a propósito: una cuenta de servicio no puede invitar
    # asistentes sin delegación a nivel de dominio (403
    # forbiddenForServiceAccounts), y este calendario es una cuenta Gmail
    # personal, donde esa delegación no existe. El correo del cliente va en
    # la descripción, y la confirmación se le envía aparte.
    evento = {
        "summary": f"{reserva.get('servicio', 'Sesión')} — {reserva.get('nombre_cliente', '')}",
        "description": "\n".join(descripcion_lineas),
        "start": {"dateTime": inicio.isoformat(), "timeZone": "America/Santiago"},
        "end": {"dateTime": fin.isoformat(), "timeZone": "America/Santiago"},
    }
    if reserva.get("direccion"):
        evento["location"] = str(reserva["direccion"])

    request = urllib.request.Request(
        f"https://www.googleapis.com/calendar/v3/calendars/{urllib.parse.quote(GOOGLE_CALENDAR_ID, safe='')}/events",
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


# ---------- Correo de confirmación ----------

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def formato_clp(valor):
    return "$" + f"{int(round(float(valor))):,}".replace(",", ".")


def datos_correo(reserva):
    """Todo lo que viene de la reserva lo escribió el cliente: se escapa
    antes de meterlo en el HTML."""
    inicio = parsear_fecha_hora(reserva["fecha_hora_solicitada"])
    minutos = (reserva.get("duracion") or DURACION_POR_DEFECTO_MINUTOS) + minutos_de_complementos(
        reserva.get("complementos")
    )
    nombre = (reserva.get("nombre_cliente") or "").strip()
    servicio = reserva.get("servicio") or "Sesión Manos Vivas"
    return {
        "nombre": nombre.split(" ")[0] if nombre else "",
        "servicio": servicio,
        "fecha": f"{DIAS[inicio.weekday()]} {inicio.day} de {MESES[inicio.month - 1]}",
        "hora": inicio.strftime("%H:%M"),
        "duracion": f"{minutos} min",
        "complementos": reserva.get("complementos") or "",
        "direccion": reserva.get("direccion") or "",
        "estacionamiento": reserva.get("estacionamiento") or "",
        "monto": formato_clp(reserva.get("monto_total") or 0),
        # Rituales y lanzamiento incluyen varias sesiones: esta es la primera.
        "varias_sesiones": "ritual" in servicio.lower(),
    }


def html_confirmacion(reserva):
    d = {k: html.escape(v) if isinstance(v, str) else v for k, v in datos_correo(reserva).items()}

    def fila(etiqueta, valor):
        if not valor:
            return ""
        return (
            '<tr><td style="padding:10px 0;border-bottom:1px solid #e6dbc8;'
            "font-family:'Plus Jakarta Sans',Helvetica,Arial,sans-serif;font-size:13px;"
            f'color:#6b5a4c;width:38%;vertical-align:top;">{etiqueta}</td>'
            '<td style="padding:10px 0;border-bottom:1px solid #e6dbc8;'
            "font-family:'Plus Jakarta Sans',Helvetica,Arial,sans-serif;font-size:15px;"
            f'color:#2A1D14;vertical-align:top;">{valor}</td></tr>'
        )

    detalle = "".join([
        fila("Servicio", d["servicio"]),
        fila("Día", d["fecha"].capitalize()),
        fila("Hora", f'{d["hora"]} <span style="color:#6b5a4c;font-size:13px;">(hora de Chile)</span>'),
        fila("Duración", d["duracion"]),
        fila("Complementos", d["complementos"]),
        fila("Dirección", d["direccion"]),
        fila("Estacionamiento", d["estacionamiento"]),
    ])

    nota_sesiones = (
        '<p style="margin:0 0 16px;font-family:\'Plus Jakarta Sans\',Helvetica,Arial,sans-serif;'
        'font-size:15px;line-height:1.6;color:#2A1D14;">Esta es la primera sesión de tu ritual. '
        "Las siguientes las coordinamos juntos.</p>"
        if d["varias_sesiones"] else ""
    )
    saludo = f"Hola {d['nombre']}," if d["nombre"] else "Hola,"

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>Tu sesión está confirmada</title>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@600&family=Plus+Jakarta+Sans:wght@400;600&display=swap" rel="stylesheet">
</head>
<body style="margin:0;padding:0;background:#f5f0e8;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">Tu sesión del {d['fecha']} a las {d['hora']} está confirmada.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f5f0e8;">
<tr><td align="center" style="padding:32px 16px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;">

    <tr><td style="padding:0 0 24px;">
      <table role="presentation" cellpadding="0" cellspacing="0"><tr>
        <td style="vertical-align:middle;padding-right:10px;">
          <img src="{SITE_URL}/assets/logo/manos-vivas-favicon-180.png" width="36" height="36" alt="" style="display:block;border:0;">
        </td>
        <td style="vertical-align:middle;font-family:'Cormorant Garamond',Georgia,serif;font-size:22px;font-weight:600;color:#2A1D14;">Manos Vivas</td>
      </tr></table>
    </td></tr>

    <tr><td style="background:#fffdf9;border:1px solid #e6dbc8;border-radius:20px;padding:36px 32px;">
      <h1 style="margin:0 0 20px;font-family:'Cormorant Garamond',Georgia,serif;font-size:32px;line-height:1.15;font-weight:600;color:#2A1D14;">Tu sesión está confirmada</h1>

      <p style="margin:0 0 16px;font-family:'Plus Jakarta Sans',Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:#2A1D14;">{saludo}</p>
      <p style="margin:0 0 24px;font-family:'Plus Jakarta Sans',Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:#2A1D14;">Recibí tu pago y tu hora quedó reservada. Llego con camilla, toallas y todo lo necesario.</p>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #e6dbc8;margin:0 0 20px;">
        {detalle}
        <tr>
          <td style="padding:14px 0 0;font-family:'Plus Jakarta Sans',Helvetica,Arial,sans-serif;font-size:13px;color:#6b5a4c;">Pagado</td>
          <td style="padding:14px 0 0;font-family:'Courier New',monospace;font-size:18px;font-weight:600;color:#964C18;">{d['monto']}</td>
        </tr>
      </table>

      {nota_sesiones}
      <p style="margin:0 0 24px;font-family:'Plus Jakarta Sans',Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:#2A1D14;">Si necesitas cambiar la hora, escríbeme por WhatsApp con anticipación.</p>

      <table role="presentation" cellpadding="0" cellspacing="0"><tr>
        <td style="background:#B25F2D;border-radius:999px;">
          <a href="{WHATSAPP_URL}" style="display:inline-block;padding:13px 26px;font-family:'Plus Jakarta Sans',Helvetica,Arial,sans-serif;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;">Escribir por WhatsApp</a>
        </td>
      </tr></table>

      <p style="margin:32px 0 0;font-family:'Cormorant Garamond',Georgia,serif;font-size:22px;font-style:italic;font-weight:600;color:#2A1D14;">Alejandro Bermudez</p>
      <p style="margin:2px 0 0;font-family:'Plus Jakarta Sans',Helvetica,Arial,sans-serif;font-size:13px;color:#6b5a4c;">Manos Vivas · masajes a domicilio</p>
    </td></tr>

    <tr><td align="center" style="padding:24px 0 0;font-family:'Plus Jakarta Sans',Helvetica,Arial,sans-serif;font-size:12px;color:#6b5a4c;">
      <a href="{SITE_URL}" style="color:#6b5a4c;">manosvivas.cl</a> · Santiago de Chile
    </td></tr>

  </table>
</td></tr>
</table>
</body>
</html>"""


def texto_confirmacion(reserva):
    """Versión en texto plano: la leen clientes sin HTML y los filtros de spam."""
    d = datos_correo(reserva)
    lineas = [
        f"Hola {d['nombre']}," if d["nombre"] else "Hola,",
        "",
        "Tu sesión está confirmada. Recibí tu pago y tu hora quedó reservada.",
        "",
        f"Servicio: {d['servicio']}",
        f"Día: {d['fecha']}",
        f"Hora: {d['hora']} (hora de Chile)",
        f"Duración: {d['duracion']}",
    ]
    if d["complementos"]:
        lineas.append(f"Complementos: {d['complementos']}")
    if d["direccion"]:
        lineas.append(f"Dirección: {d['direccion']}")
    if d["estacionamiento"]:
        lineas.append(f"Estacionamiento: {d['estacionamiento']}")
    lineas += [f"Pagado: {d['monto']}", ""]
    if d["varias_sesiones"]:
        lineas += ["Esta es la primera sesión de tu ritual. Las siguientes las coordinamos juntos.", ""]
    lineas += [
        "Llego con camilla, toallas y todo lo necesario.",
        f"Si necesitas cambiar la hora, escríbeme por WhatsApp: {WHATSAPP_URL}",
        "",
        "Alejandro Bermudez",
        "Manos Vivas · masajes a domicilio",
    ]
    return "\n".join(lineas)


def enviar_confirmacion(reserva, destinatario=None):
    if not BREVO_API_KEY:
        raise RuntimeError("Falta BREVO_API_KEY: no se puede enviar el correo de confirmación.")

    d = datos_correo(reserva)
    payload = {
        "sender": {"name": "Manos Vivas", "email": CORREO_REMITENTE},
        "replyTo": {"email": CORREO_REMITENTE, "name": "Alejandro · Manos Vivas"},
        "to": [{"email": destinatario or reserva["email_cliente"], "name": reserva.get("nombre_cliente") or ""}],
        "subject": f"Tu sesión está confirmada · {d['fecha']}, {d['hora']}",
        "htmlContent": html_confirmacion(reserva),
        "textContent": texto_confirmacion(reserva),
    }
    request = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        detalle = error.read().decode("utf-8", "ignore")
        raise RuntimeError(f"Error de Brevo al enviar la confirmación ({error.code}): {detalle}")


# ---------- Lógica principal ----------

class ReservaEnProceso(Exception):
    """Otra notificación del mismo pago está confirmando la reserva ahora."""


def reclamar_reserva(reserva_id, payment_id):
    """Pasa la reserva a 'confirmando' solo si todavía no la tomó nadie.

    Es un UPDATE condicional atómico en Postgres: si llegan dos
    notificaciones del mismo pago al mismo tiempo, solo una recibe la fila
    de vuelta. La otra no crea evento ni manda correo."""
    filtro = ",".join(ESTADOS_RECLAMABLES)
    filas = _supabase_request(
        "PATCH",
        f"reservas?id=eq.{urllib.parse.quote(str(reserva_id), safe='')}&estado=in.({filtro})",
        cuerpo={"estado": "confirmando", "mp_payment_id": str(payment_id)},
        headers_extra={"Prefer": "return=representation"},
    )
    return filas[0] if filas else None


def confirmar_reserva(reserva):
    """Crea el evento (una sola vez), manda el correo y recién ahí marca la
    reserva como confirmada. Si algo falla, la libera para que el reintento
    de Mercado Pago lo vuelva a intentar sin duplicar el evento."""
    reserva_id = reserva["id"]
    try:
        if not reserva.get("calendar_event_id"):
            evento = crear_evento_calendar(reserva)
            actualizar_reserva(reserva_id, {"calendar_event_id": evento.get("id")})
            reserva["calendar_event_id"] = evento.get("id")

        enviar_confirmacion(reserva)
        actualizar_reserva(reserva_id, {"estado": "confirmada"})
    except Exception:
        actualizar_reserva(reserva_id, {"estado": "pago_pendiente"})
        raise


def procesar_notificacion(tipo, payment_id):
    if tipo != "payment" or not payment_id:
        return {"ignorado": True}

    pago = obtener_pago(payment_id)
    reserva_id = pago.get("external_reference")
    estado_mp = pago.get("status")

    if not reserva_id:
        return {"ignorado": True, "motivo": "Pago sin external_reference."}

    if estado_mp != "approved":
        # Un pago rechazado o pendiente nunca pisa una reserva ya confirmada
        # (puede ser un intento anterior de la misma reserva).
        _supabase_request(
            "PATCH",
            f"reservas?id=eq.{urllib.parse.quote(str(reserva_id), safe='')}&estado=not.in.(confirmada,confirmando)",
            cuerpo={"estado": ESTADOS_MERCADOPAGO.get(estado_mp, "pago_pendiente"), "mp_payment_id": str(payment_id)},
            headers_extra={"Prefer": "return=minimal"},
        )
        return {"ok": True, "reserva_id": reserva_id, "estado_pago": estado_mp}

    reserva = reclamar_reserva(reserva_id, payment_id)
    if reserva is None:
        actual = obtener_reserva(reserva_id)
        if not actual:
            return {"ignorado": True, "motivo": f"No existe la reserva {reserva_id}."}
        if actual.get("estado") == "confirmada":
            return {"ok": True, "motivo": "La reserva ya estaba confirmada (notificación repetida)."}
        if actual.get("estado") == "confirmando":
            raise ReservaEnProceso(f"La reserva {reserva_id} se está confirmando en otra notificación.")
        return {"ignorado": True, "motivo": f"La reserva está en estado {actual.get('estado')}."}

    confirmar_reserva(reserva)
    return {"ok": True, "reserva_id": reserva_id, "estado": "confirmada"}


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

        except ReservaEnProceso as error:
            # Duplicado simultáneo: se responde con error para que Mercado
            # Pago reintente más tarde y encuentre la reserva ya confirmada.
            self._responder(409, {"ok": False, "error": str(error)})

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
