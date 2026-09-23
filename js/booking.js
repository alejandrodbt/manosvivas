/* ==========================================================================
   Manos Vivas — Flujo de reserva
   servicio → dirección → fecha/hora → datos → pago → confirmación.
   El precio se calcula acá para mostrarlo en vivo, pero el monto que se
   cobra siempre lo recalcula el backend desde Supabase.
   ========================================================================== */

(function () {
  'use strict';

  const MV = (window.MV = window.MV || {});

  /* ======================= Configuración =======================
     Valores públicos (no son secretos). Los ids deben coincidir con las
     filas reales de Supabase: sin ellos el backend rechaza la reserva. */

  const CONFIG = {
    /* Respaldo de la Public Key. La que se usa de verdad llega desde
       /api/crear-preferencia, para que cambiar entre modo prueba y
       producción sea solo tocar variables de entorno en Vercel: si se
       edita acá, hay que desplegar y acordarse de revertir. */
    publicKeyMercadoPago: 'APP_USR-5ab79e49-c68a-414e-9614-78e3a9886984',

    // tabla catalogo → columna id (bigint), por nombre de servicio.
    // Los nombres son los mismos que guarda Supabase: si cambias uno allá,
    // cámbialo también en el data-servicio de index.html.
    idsCatalogo: {
      'El Relajante de verdad': 2,
      'Exquisito masaje de tejido profundo': 3,
      'Sí, este Descontracturante no te hará llorar': 4,
      'El gran masaje Deportivo': 5,
      'El masaje Mixto la lleva': 6,
      'Para la futura mamá y la reina de todos': 7,
      'El masaje personalizado': 8,
      'Ritual de lanzamiento': 1,
      'Ritual 4 · Esencial': 9,
      'Ritual 6 · Profundo': 10,
      'Ritual 8 · Manada': 11,
    },

    // tabla complementos → una fila por cada duración
    complementos: [
      { id: 1, nombre: 'Reflexología', duracion_min: 10, precio: 10000 },
      { id: 2, nombre: 'Reflexología', duracion_min: 15, precio: 15000 },
      { id: 3, nombre: 'Cérvico Craneal', duracion_min: 10, precio: 10000 },
      { id: 4, nombre: 'Cérvico Craneal', duracion_min: 15, precio: 15000 },
      { id: 5, nombre: 'Drenaje Linfático', duracion_min: 30, precio: 18000 },
      { id: 6, nombre: 'Head Massage', duracion_min: 10, precio: 10000 },
      { id: 7, nombre: 'Head Massage', duracion_min: 15, precio: 15000 },
    ],

    /* Links de autorización de cada plan en Mercado Pago. Las suscripciones
       no pasan por /api/crear-preferencia: el cobro es recurrente y se
       autoriza en Mercado Pago. Mientras un link esté vacío, el botón avisa
       por WhatsApp en vez de llevar a un enlace muerto. */
    linksSuscripcion: {
      'plan-esencial': 'https://mpago.la/1d3pwBF',
      'plan-bienestar': 'https://mpago.la/1R1DQW2',
      'plan-vital': 'https://mpago.la/1jJBAEA',
      'plan-manada': 'https://mpago.la/271YFQ5',
    },
  };

  const TOTAL_PASOS = 5;

  /* ======================= Estado ======================= */

  const estado = {
    paso: 1,
    servicio: '',
    tipo: 'suelto',
    duracion: 60,
    precioBase: 0,
    complementos: [],
    direccion: '',
    estacionamiento: '',
    fecha: '',
    hora: '',
    cliente: { nombre: '', email: '', telefono: '' },
    reservaId: null,
  };

  /* ======================= Referencias del DOM ======================= */

  const overlay = document.querySelector('[data-reserva-overlay]');
  if (!overlay) return;

  const panel = overlay.querySelector('.reserva__panel');
  const botonCerrar = overlay.querySelector('[data-reserva-cerrar]');
  const botonAtras = overlay.querySelector('[data-reserva-atras]');
  const botonSiguiente = overlay.querySelector('[data-reserva-siguiente]');
  const listaServicios = overlay.querySelector('[data-lista-servicios]');
  const bloqueDuracion = overlay.querySelector('[data-bloque-duracion]');
  const listaDuraciones = overlay.querySelector('[data-lista-duraciones]');
  const listaComplementos = overlay.querySelector('[data-lista-complementos]');
  const resumen = overlay.querySelector('[data-resumen]');
  const resumenFinal = overlay.querySelector('[data-resumen-final]');
  const campoFecha = overlay.querySelector('#reserva-fecha');
  const listaHorarios = overlay.querySelector('[data-lista-horarios]');
  const estadoHorarios = overlay.querySelector('[data-estado-horarios]');
  const contenedorBrick = overlay.querySelector('[data-brick-pago]');
  const estadoPago = overlay.querySelector('[data-estado-pago]');
  const pagoCuando = overlay.querySelector('[data-pago-cuando]');

  let ultimoFoco = null;
  let mercadoPago = null;
  let controladorBrick = null;
  // Cada consulta de horarios lleva un número: si el cliente cambia de día
  // o reabre el flujo antes de que responda, la respuesta vieja se descarta.
  let consultaHorarios = 0;
  // Igual para el pago: solo la última preparación puede montar el brick.
  let intentoPago = 0;

  /* ======================= Catálogo leído del DOM ======================= */

  const servicios = Array.from(document.querySelectorAll('.catalogo__lista .fila-acordeon')).map((fila) => ({
    nombre: fila.dataset.servicio,
    precio60: Number(fila.dataset.precio60),
    precio90: Number(fila.dataset.precio90),
  }));

  /* ======================= Apertura y cierre ======================= */

  document.querySelectorAll('[data-reservar]').forEach((disparador) => {
    disparador.addEventListener('click', () => {
      const datos = disparador.dataset;
      const fila = disparador.closest('.fila-acordeon');

      if (datos.tipo === 'pack' || datos.tipo === 'lanzamiento') {
        estado.tipo = datos.tipo;
        estado.servicio = datos.servicio;
        estado.precioBase = Number(datos.precio);
        estado.duracion = Number(datos.duracion) || 60;
        estado.complementos = [];
      } else {
        estado.tipo = 'suelto';
        estado.servicio = (fila && fila.dataset.servicio) || datos.servicio || '';
        estado.duracion = Number(datos.duracion) || 60;
        estado.precioBase = precioDeServicio(estado.servicio, estado.duracion);
        estado.complementos = [];
      }

      reiniciarAgenda();
      abrirReserva();
    });
  });

  /* Cada apertura parte sin fecha ni hora. Si quedaran de una visita
     anterior, el cliente podría pagar un horario que nunca eligió para
     este servicio (así pasó con la reserva #23). */
  function reiniciarAgenda() {
    consultaHorarios++;
    intentoPago++;
    estado.fecha = '';
    estado.hora = '';
    estado.reservaId = null;
    if (campoFecha) {
      campoFecha.value = '';
      campoFecha.min = hoyEnChile();
    }
    listaHorarios.innerHTML = '';
    estadoHorarios.textContent = '';
    estadoHorarios.classList.remove('esta-cargando');
    desmontarBrick();
    estadoPago.textContent = '';
    estadoPago.classList.remove('esta-cargando');
    if (pagoCuando) pagoCuando.textContent = '';
  }

  function abrirReserva() {
    ultimoFoco = document.activeElement;
    overlay.hidden = false;
    document.body.style.overflow = 'hidden';
    requestAnimationFrame(() => overlay.classList.add('esta-visible'));

    renderizarPaso1();
    irAPaso(1);

    if (botonCerrar) botonCerrar.focus();
  }

  function cerrarReserva() {
    overlay.classList.remove('esta-visible');
    document.body.style.overflow = '';
    setTimeout(() => {
      overlay.hidden = true;
    }, 400);
    if (ultimoFoco) ultimoFoco.focus();
  }

  if (botonCerrar) botonCerrar.addEventListener('click', cerrarReserva);

  overlay.addEventListener('click', (evento) => {
    if (evento.target === overlay) cerrarReserva();
  });

  document.addEventListener('keydown', (evento) => {
    if (evento.key === 'Escape' && !overlay.hidden) cerrarReserva();
    if (evento.key === 'Tab' && !overlay.hidden) atraparFoco(evento);
  });

  function atraparFoco(evento) {
    const enfocables = panel.querySelectorAll(
      'button:not([disabled]), input:not([disabled]), select, textarea, a[href]'
    );
    if (!enfocables.length) return;
    const primero = enfocables[0];
    const ultimo = enfocables[enfocables.length - 1];

    if (evento.shiftKey && document.activeElement === primero) {
      evento.preventDefault();
      ultimo.focus();
    } else if (!evento.shiftKey && document.activeElement === ultimo) {
      evento.preventDefault();
      primero.focus();
    }
  }

  /* ======================= Paso 1: sesión ======================= */

  function precioDeServicio(nombre, duracion) {
    const servicio = servicios.find((s) => s.nombre === nombre);
    if (!servicio) return 0;
    return duracion === 90 ? servicio.precio90 : servicio.precio60;
  }

  function renderizarPaso1() {
    const esPaquete = estado.tipo !== 'suelto';

    // Selector de servicio (solo para masajes sueltos)
    listaServicios.innerHTML = '';
    if (esPaquete) {
      listaServicios.innerHTML =
        '<p class="paso-reserva__ayuda">Estás reservando <strong>' +
        MV.escaparHTML(estado.servicio) +
        '</strong>. Agenda acá tu primera sesión; las siguientes las coordinamos después.</p>';
    } else {
      servicios.forEach((servicio) => {
        const boton = document.createElement('button');
        boton.type = 'button';
        boton.className = 'opcion-servicio';
        boton.setAttribute('aria-pressed', String(servicio.nombre === estado.servicio));
        boton.innerHTML =
          '<span>' +
          MV.escaparHTML(servicio.nombre) +
          '</span><span class="opcion-complemento__precio">' +
          MV.formatoCLP(servicio.precio60) +
          '</span>';
        boton.addEventListener('click', () => {
          estado.servicio = servicio.nombre;
          estado.precioBase = precioDeServicio(estado.servicio, estado.duracion);
          renderizarPaso1();
        });
        listaServicios.appendChild(boton);
      });
    }

    // Duración
    bloqueDuracion.hidden = esPaquete;
    listaDuraciones.innerHTML = '';
    if (!esPaquete) {
      [60, 90].forEach((minutos) => {
        const precio = precioDeServicio(estado.servicio, minutos);
        const boton = document.createElement('button');
        boton.type = 'button';
        boton.className = 'horario-slot';
        boton.setAttribute('aria-pressed', String(estado.duracion === minutos));
        boton.setAttribute('aria-label', minutos + ' minutos, ' + MV.formatoCLP(precio));
        boton.textContent = minutos + ' min · ' + MV.formatoCLP(precio);
        boton.addEventListener('click', () => {
          estado.duracion = minutos;
          estado.precioBase = precioDeServicio(estado.servicio, minutos);
          renderizarPaso1();
        });
        listaDuraciones.appendChild(boton);
      });
    }

    // Complementos (se pueden marcar y desmarcar varios)
    listaComplementos.parentElement.hidden = esPaquete;
    listaComplementos.innerHTML = '';
    if (!esPaquete) {
      CONFIG.complementos.forEach((complemento, indice) => {
        const marcado = estado.complementos.some((c) => c.indice === indice);
        const etiqueta = document.createElement('label');
        etiqueta.className = 'opcion-complemento' + (marcado ? ' esta-marcado' : '');
        etiqueta.innerHTML =
          '<span class="opcion-complemento__etiqueta">' +
          '<input type="checkbox"' +
          (marcado ? ' checked' : '') +
          '>' +
          MV.escaparHTML(complemento.nombre) +
          ' · ' +
          complemento.duracion_min +
          ' min</span>' +
          '<span class="opcion-complemento__precio">' +
          MV.formatoCLP(complemento.precio) +
          '</span>';

        etiqueta.querySelector('input').addEventListener('change', (evento) => {
          if (evento.target.checked) {
            estado.complementos.push({ indice: indice, ...complemento });
          } else {
            estado.complementos = estado.complementos.filter((c) => c.indice !== indice);
          }
          renderizarPaso1();
        });

        listaComplementos.appendChild(etiqueta);
      });
    }

    actualizarResumen();
  }

  function montoTotal() {
    return estado.complementos.reduce((suma, c) => suma + c.precio, estado.precioBase);
  }

  function duracionTotal() {
    return estado.complementos.reduce((suma, c) => suma + c.duracion_min, estado.duracion);
  }

  function filasResumen() {
    const filas = [
      '<div class="resumen-reserva__fila"><span>' +
        MV.escaparHTML(estado.servicio || 'Sin elegir') +
        '</span><span>' +
        MV.formatoCLP(estado.precioBase) +
        '</span></div>',
    ];

    if (estado.tipo === 'suelto') {
      filas.push(
        '<div class="resumen-reserva__fila"><span>Duración</span><span>' + estado.duracion + ' min</span></div>'
      );
    }

    estado.complementos.forEach((complemento) => {
      filas.push(
        '<div class="resumen-reserva__fila"><span>' +
          MV.escaparHTML(complemento.nombre) +
          ' · ' +
          complemento.duracion_min +
          ' min</span><span>' +
          MV.formatoCLP(complemento.precio) +
          '</span></div>'
      );
    });

    if (estado.fecha && estado.hora) {
      filas.push(
        '<div class="resumen-reserva__fila"><span>Cuándo</span><span>' +
          MV.escaparHTML(fechaEnPalabras(estado.fecha, estado.hora)) +
          '</span></div>'
      );
    }

    filas.push(
      '<div class="resumen-reserva__total"><span>Total</span><span>' + MV.formatoCLP(montoTotal()) + '</span></div>'
    );

    return filas.join('');
  }

  function actualizarResumen() {
    if (resumen) resumen.innerHTML = filasResumen();
    if (resumenFinal) resumenFinal.innerHTML = filasResumen();
  }

  /* "viernes 25 de septiembre, 18:00". Se arma a mano y en UTC porque
     new Date('2026-09-25') se interpreta como medianoche UTC y en Chile
     mostraría el día anterior. */
  const DIAS = ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado'];
  const MESES = [
    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
  ];

  function fechaEnPalabras(fecha, hora) {
    const [anio, mes, dia] = fecha.split('-').map(Number);
    const diaSemana = new Date(Date.UTC(anio, mes - 1, dia)).getUTCDay();
    return DIAS[diaSemana] + ' ' + dia + ' de ' + MESES[mes - 1] + ', ' + hora;
  }

  // Fecha de hoy en Chile (toISOString daría la de UTC, que después de las
  // 21:00 ya es mañana).
  function hoyEnChile() {
    return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Santiago' }).format(new Date());
  }

  /* ======================= Paso 3: disponibilidad ======================= */

  if (campoFecha) {
    campoFecha.min = hoyEnChile();

    campoFecha.addEventListener('change', () => {
      estado.fecha = campoFecha.value;
      estado.hora = '';
      actualizarResumen();
      cargarHorarios();
    });
  }

  // `aviso` se muestra sobre la lista nueva, por ejemplo cuando el backend
  // rechazó la hora elegida porque alguien la tomó antes.
  async function cargarHorarios(aviso) {
    if (!estado.fecha) return;

    const consulta = ++consultaHorarios;
    listaHorarios.innerHTML = '';
    estadoHorarios.textContent = 'Buscando horarios libres…';
    estadoHorarios.classList.add('esta-cargando');

    try {
      const url = '/api/disponibilidad?fecha=' + encodeURIComponent(estado.fecha) + '&duracion=' + duracionTotal();
      const respuesta = await fetch(url);
      if (!respuesta.ok) throw new Error('Sin respuesta de la agenda.');

      const datos = await respuesta.json();
      if (consulta !== consultaHorarios) return;
      const horarios = datos.horarios_disponibles || [];

      estadoHorarios.classList.remove('esta-cargando');

      if (!horarios.length) {
        estadoHorarios.textContent =
          (aviso ? aviso + ' ' : '') + 'No quedan horarios libres ese día. Prueba con otra fecha.';
        return;
      }

      estadoHorarios.textContent = aviso || '';
      horarios.forEach((hora) => {
        const boton = document.createElement('button');
        boton.type = 'button';
        boton.className = 'horario-slot';
        boton.textContent = hora;
        boton.setAttribute('aria-pressed', 'false');
        boton.addEventListener('click', () => {
          estado.hora = hora;
          listaHorarios.querySelectorAll('.horario-slot').forEach((slot) => {
            slot.setAttribute('aria-pressed', String(slot === boton));
          });
          actualizarResumen();
        });
        listaHorarios.appendChild(boton);
      });
    } catch (error) {
      if (consulta !== consultaHorarios) return;
      estadoHorarios.classList.remove('esta-cargando');
      estadoHorarios.textContent = 'No pudimos leer la agenda ahora. Inténtalo de nuevo en un momento.';
    }
  }

  /* ======================= Navegación entre pasos ======================= */

  function irAPaso(numero) {
    estado.paso = numero;

    overlay.querySelectorAll('.paso-reserva').forEach((seccion) => {
      seccion.classList.toggle('esta-activo', Number(seccion.dataset.paso) === numero);
    });

    overlay.querySelectorAll('[data-paso-indicador]').forEach((indicador) => {
      const paso = Number(indicador.dataset.pasoIndicador);
      indicador.classList.toggle('esta-completo', paso < numero);
      indicador.classList.toggle('esta-activo', paso === numero);
    });

    botonAtras.hidden = numero === 1;
    botonSiguiente.hidden = numero === TOTAL_PASOS;
    botonSiguiente.textContent = numero === TOTAL_PASOS - 1 ? 'Ir a pagar' : 'Continuar';

    if (numero === 4) actualizarResumen();
  }

  function validarPasoActual() {
    MV.limpiarErrores(overlay);

    if (estado.paso === 1) {
      if (!estado.servicio) {
        MV.toast('Elige un masaje para seguir.');
        return false;
      }
      return true;
    }

    if (estado.paso === 2) {
      const direccion = overlay.querySelector('#reserva-direccion');
      if (direccion.value.trim().length < 6) {
        MV.mostrarError('reserva-direccion', 'Necesito la dirección completa para llegar.');
        direccion.focus();
        return false;
      }
      estado.direccion = direccion.value.trim();
      estado.estacionamiento = overlay.querySelector('#reserva-estacionamiento').value.trim();
      return true;
    }

    if (estado.paso === 3) {
      if (!estado.fecha) {
        MV.toast('Elige el día de tu sesión.');
        return false;
      }
      if (!estado.hora) {
        MV.toast('Elige una hora disponible.');
        return false;
      }
      return true;
    }

    if (estado.paso === 4) {
      const nombre = overlay.querySelector('#reserva-nombre');
      const email = overlay.querySelector('#reserva-email');
      const telefono = overlay.querySelector('#reserva-telefono');
      let valido = true;

      if (nombre.value.trim().length < 3) {
        MV.mostrarError('reserva-nombre', 'Escribe tu nombre y apellido.');
        valido = false;
      }
      if (!MV.validarEmail(email.value)) {
        MV.mostrarError('reserva-email', 'Revisa el correo: ahí te llega la confirmación.');
        valido = false;
      }
      if (!MV.validarTelefono(telefono.value)) {
        MV.mostrarError('reserva-telefono', 'Incluye el código de país, por ejemplo +56 9 1234 5678.');
        valido = false;
      }

      if (!valido) return false;

      estado.cliente = {
        nombre: nombre.value.trim(),
        email: email.value.trim(),
        telefono: telefono.value.trim(),
      };
      return true;
    }

    return true;
  }

  botonAtras.addEventListener('click', () => {
    if (estado.paso > 1) irAPaso(estado.paso - 1);
  });

  botonSiguiente.addEventListener('click', async () => {
    if (!validarPasoActual()) return;

    if (estado.paso === 4) {
      await prepararPago();
      return;
    }

    irAPaso(estado.paso + 1);
  });

  /* ======================= Paso 5: pago ======================= */

  async function prepararPago() {
    const intento = ++intentoPago;
    irAPaso(5);
    desmontarBrick();
    if (pagoCuando) pagoCuando.textContent = fechaEnPalabras(estado.fecha, estado.hora);
    estadoPago.textContent = 'Preparando el pago…';
    estadoPago.classList.add('esta-cargando');

    const idProducto = CONFIG.idsCatalogo[estado.servicio];

    try {
      const respuesta = await fetch('/api/crear-preferencia', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          producto_id: idProducto,
          // Duración base del servicio: es la que define el precio en el
          // catálogo. El tiempo extra de los complementos lo suma el backend
          // al crear el evento en Calendar.
          duracion: estado.duracion,
          complementos: estado.complementos.map((c) => c.id),
          fecha_hora: estado.fecha + 'T' + estado.hora + ':00',
          direccion: estado.direccion,
          estacionamiento: estado.estacionamiento,
          cliente: estado.cliente,
        }),
      });

      const datos = await respuesta.json();
      if (intento !== intentoPago) return;

      if (respuesta.status === 409 && datos.codigo === 'horario_no_disponible') {
        volverAElegirHora(datos.error);
        return;
      }
      if (!respuesta.ok) throw new Error(datos.error || 'No pudimos iniciar el pago.');

      estado.reservaId = datos.reserva_id;
      estadoPago.classList.remove('esta-cargando');
      estadoPago.textContent = '';

      if (datos.modo_prueba) {
        // Aviso visible: en modo prueba ningún pago es real, y nadie debería
        // creer que compró algo.
        estadoPago.textContent = 'Modo de prueba: este pago no es real.';
      }

      await montarBrick(datos.preference_id, datos.monto_total, datos.public_key);
    } catch (error) {
      if (intento !== intentoPago) return;
      estadoPago.classList.remove('esta-cargando');
      estadoPago.textContent = error.message + ' Escríbeme por WhatsApp y lo resolvemos.';
    }
  }

  /* El backend revisó el calendario y la hora ya no está libre: no se
     generó cobro. El cliente vuelve al paso 3 con horarios frescos. */
  function volverAElegirHora(mensaje) {
    estadoPago.classList.remove('esta-cargando');
    estadoPago.textContent = '';
    if (pagoCuando) pagoCuando.textContent = '';
    estado.hora = '';
    actualizarResumen();
    irAPaso(3);
    MV.toast(mensaje);
    cargarHorarios(mensaje);
  }

  /* Cada paso por el pago monta un brick nuevo con la preferencia recién
     creada. Reusar uno anterior dejaría el botón de Mercado Pago apuntando
     a una preferencia vieja, con otra hora o monto. */
  function desmontarBrick() {
    if (controladorBrick) {
      try {
        controladorBrick.unmount();
      } catch (error) {
        console.error('[Manos Vivas] No se pudo desmontar el brick:', error);
      }
      controladorBrick = null;
    }
    contenedorBrick.innerHTML = '';
  }

  async function montarBrick(preferenceId, monto, publicKey) {
    if (typeof window.MercadoPago === 'undefined') {
      estadoPago.textContent = 'No pudimos cargar el medio de pago. Revisa tu conexión y vuelve a intentar.';
      return;
    }

    if (!mercadoPago) {
      mercadoPago = new window.MercadoPago(publicKey || CONFIG.publicKeyMercadoPago, { locale: 'es-CL' });
    }
    const bricks = mercadoPago.bricks();

    controladorBrick = await bricks.create('payment', contenedorBrick.id || crearIdBrick(), {
      initialization: {
        amount: monto,
        preferenceId: preferenceId,
      },
      customization: {
        paymentMethods: {
          creditCard: 'all',
          debitCard: 'all',
          // Cuenta Mercado Pago: la clave es 'mercadoPago' y su valor es
          // 'wallet_purchase', no al revés. Requiere el preferenceId que
          // se pasa arriba en initialization.
          mercadoPago: 'wallet_purchase',
        },
      },
      callbacks: {
        // Vacío pero obligatorio: sin onReady el SDK no monta el brick.
        onReady: () => {},
        // El SDK espera una promesa para saber cuándo terminó el cobro:
        // sin devolverla, el brick apaga su spinner de inmediato y el
        // cliente puede pensar que el pago falló y volver a enviarlo.
        onSubmit: (datosPago) => procesarPago(datosPago),
        // El detalle va a la consola porque el SDK avisa acá cuando ignora
        // una opción de customization o cuando un medio de pago no está
        // disponible para el país de la cuenta.
        onError: (error) => {
          console.error('[Manos Vivas] Payment Brick:', error);
          estadoPago.textContent = 'Hubo un problema con el medio de pago. Revisa los datos e inténtalo otra vez.';
        },
      },
    });
  }

  function crearIdBrick() {
    contenedorBrick.id = 'brick-pago';
    return 'brick-pago';
  }

  async function procesarPago(datosPago) {
    estadoPago.textContent = 'Confirmando tu pago…';
    estadoPago.classList.add('esta-cargando');

    try {
      const respuesta = await fetch('/api/procesar-pago', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reserva_id: estado.reservaId, pago: datosPago.formData }),
      });

      const datos = await respuesta.json();
      estadoPago.classList.remove('esta-cargando');

      if (!respuesta.ok || datos.estado === 'rechazado') {
        estadoPago.textContent = 'El pago fue rechazado. Prueba con otra tarjeta o escríbeme por WhatsApp.';
        return;
      }

      mostrarConfirmacion(datos.estado === 'aprobado');
    } catch (error) {
      estadoPago.classList.remove('esta-cargando');
      estadoPago.textContent = 'No pudimos confirmar el pago. Escríbeme por WhatsApp antes de volver a intentar.';
    }
  }

  /* Un pago aprobado NO es una hora agendada: la reserva la confirma
     webhook-pago.py después, y es quien crea el evento en el calendario.
     Prometer "agendada" acá deja al cliente creyendo que tiene una hora
     que puede no existir. */
  function mostrarConfirmacion(pagoAprobado) {
    desmontarBrick();

    estadoPago.innerHTML = pagoAprobado
      ? '<p class="paso-reserva__titulo">Pago recibido.</p>' +
        '<p>Estamos confirmando tu hora. En unos minutos te llega un correo de confirmación a ' +
        MV.escaparHTML(estado.cliente.email) +
        ' con el día, la hora y la dirección.</p><p>Si no te llega, revisa spam o escríbeme por WhatsApp y lo reviso.</p>'
      : '<p class="paso-reserva__titulo">Tu pago está en revisión.</p>' +
        '<p>Apenas Mercado Pago lo resuelva te aviso por correo a ' +
        MV.escaparHTML(estado.cliente.email) +
        '.</p>';

    MV.toast(pagoAprobado ? 'Pago recibido. Te confirmamos la hora por correo.' : 'Pago en revisión.');
  }

  /* ======================= Links de suscripción ======================= */

  document.querySelectorAll('[data-link-suscripcion]').forEach((enlace) => {
    const clave = enlace.dataset.linkSuscripcion;
    const url = CONFIG.linksSuscripcion[clave];

    if (url) {
      enlace.href = url;
      return;
    }

    enlace.addEventListener('click', (evento) => {
      evento.preventDefault();
      MV.toast('Este plan se activa por WhatsApp por ahora. Escríbeme y lo dejamos andando.');
    });
  });
})();
