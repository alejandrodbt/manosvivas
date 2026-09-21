import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from zoneinfo import ZoneInfo

ZONA_HORARIA = ZoneInfo("America/Santiago")

MERCADOPAGO_ACCESS_TOKEN = os.environ.get("MERCADOPAGO_ACCESS_TOKEN")
MERCADOPAGO_PREFERENCIAS_URL = "https://api.mercadopago.com/checkout/preferences"

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

SITE_URL = os.environ.get("SITE_URL", "https://manosvivas.cl")

DURACIONES_VALIDAS = (60, 90)


class ErrorSolicitud(Exception):
    def __init__(self, status, mensaje):
        super().__init__(mensaje)
        self.status = status
        self.mensaje = mensaje


def _validar_configuracion():
    faltantes = [
        nombre
        for nombre, valor in (
            ("MERCADOPAGO_ACCESS_TOKEN", MERCADOPAGO_ACCESS_TOKEN),
            ("SUPABASE_URL", SUPABASE_URL),
            ("SUPABASE_SERVICE_ROLE_KEY", SUPABASE_SERVICE_ROLE_KEY),
        )
        if not valor
    ]
    if faltantes:
        raise RuntimeError(f"Faltan variables de entorno: {', '.join(faltantes)}")


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


def obtener_producto(producto_id):
    fila_escapada = urllib.parse.quote(str(producto_id), safe="")
    resultado = _supabase_request("GET", f"catalogo?id=eq.{fila_escapada}&select=*")
    if not resultado:
        raise ErrorSolicitud(404, "El producto solicitado no existe.")
    producto = resultado[0]
    if producto.get("activo") is False:
        raise ErrorSolicitud(409, "Este producto ya no está disponible.")
    if producto.get("tipo") == "suscripcion":
        raise ErrorSolicitud(400, "Las suscripciones no se procesan por este endpoint.")
    return producto


def obtener_complementos(ids_complementos):
    """Cada fila de 'complementos' ya es una combinación específica de
    nombre + duracion_min + precio (ej. Reflexología 10 min y Reflexología
    15 min son filas distintas) — el frontend manda el id de la variante
    exacta que el cliente eligió, no hace falta cruzar por duración aquí.
    """
    if not ids_complementos:
        return []
    lista = ",".join(urllib.parse.quote(str(i), safe="") for i in ids_complementos)
    resultado = _supabase_request("GET", f"complementos?id=in.({lista})&select=*")
    return resultado or []


def calcular_precio_producto(producto, duracion_minutos):
    tiene_precio_por_duracion = (
        producto.get("precio_60") is not None or producto.get("precio_90") is not None
    )
    if tiene_precio_por_duracion:
        if duracion_minutos not in DURACIONES_VALIDAS:
            raise ErrorSolicitud(
                400, f"'duracion' debe ser uno de {DURACIONES_VALIDAS} para este producto."
            )
        campo = f"precio_{duracion_minutos}"
        precio = producto.get(campo)
        if precio is None:
            raise ErrorSolicitud(400, f"Este producto no tiene precio definido para {duracion_minutos} minutos.")
        return precio

    precio = producto.get("precio")
    if precio is None:
        raise ErrorSolicitud(500, "El producto no tiene un precio configurado en el catálogo.")
    return precio


def fecha_hora_con_zona(valor):
    """'fecha_hora_solicitada' es timestamptz. Si se manda la hora sin zona,
    Postgres la toma como UTC y la reserva queda corrida varias horas: acá
    se fija explícitamente la hora de Chile, que es la que se muestra."""
    momento = datetime.fromisoformat(valor)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=ZONA_HORARIA)
    return momento.isoformat()


def crear_reserva_pendiente(datos, producto, complementos, monto_total):
    fila = {
        "servicio": producto.get("nombre"),
        "duracion": datos.get("duracion"),
        # La columna es text: se guarda legible, para que la reserva se
        # entienda leyendo la tabla sin cruzarla con otra.
        "complementos": ", ".join(f"{c['nombre']} · {c['duracion_min']} min" for c in complementos),
        "monto_total": monto_total,
        "nombre_cliente": datos["cliente"]["nombre"],
        "email_cliente": datos["cliente"]["email"],
        "telefono_cliente": datos["cliente"].get("telefono"),
        "direccion": datos.get("direccion"),
        "estacionamiento": datos.get("estacionamiento"),
        "fecha_hora_solicitada": fecha_hora_con_zona(datos["fecha_hora"]),
        "estado": "pendiente_pago",
    }
    resultado = _supabase_request(
        "POST", "reservas", cuerpo=fila, headers_extra={"Prefer": "return=representation"}
    )
    if not resultado:
        raise RuntimeError("Supabase no devolvió la reserva creada.")
    return resultado[0]


def eliminar_reserva(reserva_id):
    try:
        _supabase_request("DELETE", f"reservas?id=eq.{urllib.parse.quote(str(reserva_id), safe='')}")
    except RuntimeError:
        pass


def crear_preferencia_mercadopago(reserva, producto, complementos, datos):
    items = [
        {
            "title": producto.get("nombre", "Servicio Manos Vivas"),
            "quantity": 1,
            "currency_id": "CLP",
            "unit_price": float(calcular_precio_producto(producto, datos.get("duracion"))),
        }
    ]
    for complemento in complementos:
        duracion = complemento.get("duracion_min")
        titulo = complemento.get("nombre", "Complemento")
        if duracion:
            titulo = f"{titulo} ({duracion} min)"
        items.append(
            {
                "title": titulo,
                "quantity": 1,
                "currency_id": "CLP",
                "unit_price": float(complemento.get("precio", 0)),
            }
        )

    payload = {
        "items": items,
        "payer": {
            "name": datos["cliente"]["nombre"],
            "email": datos["cliente"]["email"],
        },
        "external_reference": str(reserva["id"]),
        "notification_url": f"{SITE_URL}/api/webhook-pago",
        "back_urls": {
            "success": f"{SITE_URL}/?reserva=confirmada&id={reserva['id']}",
            "failure": f"{SITE_URL}/?reserva=fallida&id={reserva['id']}",
            "pending": f"{SITE_URL}/?reserva=pendiente&id={reserva['id']}",
        },
        "auto_return": "approved",
        "statement_descriptor": "MANOS VIVAS",
    }

    request = urllib.request.Request(
        MERCADOPAGO_PREFERENCIAS_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {MERCADOPAGO_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        detalle = error.read().decode("utf-8", "ignore")
        raise RuntimeError(f"Error de Mercado Pago ({error.code}): {detalle}")


def procesar_solicitud(datos):
    if not isinstance(datos, dict):
        raise ErrorSolicitud(400, "Cuerpo de la solicitud inválido.")

    producto_id = datos.get("producto_id")
    cliente = datos.get("cliente") or {}
    fecha_hora = datos.get("fecha_hora")
    if not producto_id:
        raise ErrorSolicitud(400, "Falta 'producto_id'.")
    if not cliente.get("nombre") or not cliente.get("email"):
        raise ErrorSolicitud(400, "Falta 'cliente.nombre' o 'cliente.email'.")
    if not fecha_hora:
        raise ErrorSolicitud(400, "Falta 'fecha_hora' (formato ISO 8601, ej. 2026-09-20T15:00:00).")
    try:
        datetime.fromisoformat(fecha_hora)
    except ValueError:
        raise ErrorSolicitud(400, "'fecha_hora' debe tener formato ISO 8601, ej. 2026-09-20T15:00:00.")

    producto = obtener_producto(producto_id)
    complementos = obtener_complementos(datos.get("complementos"))
    monto_total = calcular_precio_producto(producto, datos.get("duracion")) + sum(
        float(c.get("precio", 0)) for c in complementos
    )

    reserva = crear_reserva_pendiente(datos, producto, complementos, monto_total)

    try:
        preferencia = crear_preferencia_mercadopago(reserva, producto, complementos, datos)
    except RuntimeError:
        eliminar_reserva(reserva["id"])
        raise

    return {
        "reserva_id": reserva["id"],
        "preference_id": preferencia["id"],
        "init_point": preferencia.get("init_point"),
        "monto_total": monto_total,
    }


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            _validar_configuracion()

            largo_contenido = int(self.headers.get("Content-Length", 0))
            cuerpo_crudo = self.rfile.read(largo_contenido) if largo_contenido else b"{}"
            try:
                datos = json.loads(cuerpo_crudo)
            except json.JSONDecodeError:
                self._responder(400, {"error": "El cuerpo debe ser JSON válido."})
                return

            resultado = procesar_solicitud(datos)
            self._responder(201, resultado)
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
