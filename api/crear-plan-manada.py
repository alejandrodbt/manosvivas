import json
import os
import sys
import urllib.request
import urllib.error

MERCADOPAGO_API_URL = "https://api.mercadopago.com/preapproval_plan"

PLAN = {
    "reason": "Manada · Para los que ya saben que esto es continuo",
    "external_reference": "plan-manada",
    "auto_recurring": {
        "frequency": 1,
        "frequency_type": "months",
        "transaction_amount": 350000,
        "currency_id": "CLP",
    },
    "back_url": "https://manosvivas.cl",
}


def _pedir(url, access_token, cuerpo=None):
    request = urllib.request.Request(
        url,
        data=json.dumps(cuerpo).encode("utf-8") if cuerpo else None,
        method="POST" if cuerpo else "GET",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def buscar_plan(access_token):
    """Evita crear un plan duplicado: los planes se cobran de verdad y no se
    pueden borrar desde la API."""
    return _pedir(
        f"{MERCADOPAGO_API_URL}/search?external_reference={PLAN['external_reference']}",
        access_token,
    )


def crear_plan(access_token):
    return _pedir(MERCADOPAGO_API_URL, access_token, PLAN)


def leer_token():
    token = os.environ.get("MERCADOPAGO_ACCESS_TOKEN")
    if token:
        return token

    # Alternativa para no escribir el token en la línea de comandos.
    ruta_env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(ruta_env):
        for linea in open(ruta_env, encoding="utf-8"):
            if linea.strip().startswith("MERCADOPAGO_ACCESS_TOKEN="):
                return linea.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def mostrar(plan):
    print(f"  id: {plan.get('id')}")
    print(f"  monto: {plan.get('auto_recurring', {}).get('transaction_amount')} "
          f"{plan.get('auto_recurring', {}).get('currency_id')}")
    print(f"  estado: {plan.get('status')}")
    print(f"  LINK DE SUSCRIPCIÓN: {plan.get('init_point')}")


if __name__ == "__main__":
    accion = sys.argv[1] if len(sys.argv) > 1 else "revisar"

    access_token = leer_token()
    if not access_token:
        print("Falta MERCADOPAGO_ACCESS_TOKEN (variable de entorno o archivo .env en la raíz).")
        sys.exit(1)

    modo = "PRUEBA" if access_token.startswith("TEST-") else "PRODUCCIÓN (cobra dinero real)"
    print(f"Credenciales en modo: {modo}\n")

    status, body = buscar_plan(access_token)
    existentes = body.get("results", []) if status == 200 else []

    if existentes:
        print(f"El plan '{PLAN['external_reference']}' YA EXISTE ({len(existentes)}):")
        for plan in existentes:
            mostrar(plan)
        print("\nNo se creó nada. Usa el link de arriba.")
        sys.exit(0)

    if accion != "crear":
        print(f"No existe todavía un plan '{PLAN['external_reference']}'.")
        print(f"Para crearlo: python3 {os.path.basename(__file__)} crear")
        sys.exit(0)

    status, body = crear_plan(access_token)
    if status == 201:
        print("Plan creado:")
        mostrar(body)
    else:
        print(f"Error {status}:")
        print(json.dumps(body, indent=2, ensure_ascii=False))
        sys.exit(1)
