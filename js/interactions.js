/* ==========================================================================
   Manos Vivas — Interacciones
   Scroll, acordeones, toggles, video del hero, carrusel de testimonios.
   Expone utilidades compartidas en window.MV para forms.js y booking.js.
   ========================================================================== */

(function () {
  'use strict';

  const raiz = document.documentElement;
  raiz.classList.add('js-activo');

  /* La intro completa del logo se ve una vez por visita: a quien vuelve en
     la misma sesión se le muestra una versión corta. */
  try {
    if (sessionStorage.getItem('mv-intro') === 'vista') {
      raiz.classList.add('intro-vista');
    } else {
      sessionStorage.setItem('mv-intro', 'vista');
    }
  } catch (error) {
    /* Almacenamiento bloqueado: se muestra la intro completa, sin drama. */
  }

  const prefiereMenosMovimiento = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------------- Utilidades compartidas ---------------- */

  const MV = (window.MV = window.MV || {});

  MV.formatoCLP = function (valor) {
    return '$' + Number(valor || 0).toLocaleString('es-CL');
  };

  const toast = document.querySelector('[data-toast]');
  const toastTexto = document.querySelector('[data-toast-texto]');
  let temporizadorToast = null;

  MV.toast = function (mensaje, duracion) {
    if (!toast || !toastTexto) return;
    toastTexto.textContent = mensaje;
    toast.hidden = false;
    requestAnimationFrame(() => toast.classList.add('esta-visible'));

    clearTimeout(temporizadorToast);
    temporizadorToast = setTimeout(() => {
      toast.classList.remove('esta-visible');
      setTimeout(() => {
        toast.hidden = true;
      }, 500);
    }, duracion || 4200);
  };

  /* ---------------- Barra de progreso de scroll ---------------- */

  const progreso = document.querySelector('[data-progreso-scroll]');

  function actualizarProgreso() {
    if (!progreso) return;
    const alcance = document.documentElement.scrollHeight - window.innerHeight;
    const avance = alcance > 0 ? (window.scrollY / alcance) * 100 : 0;
    progreso.style.width = avance + '%';
  }

  /* ---------------- Nav ---------------- */

  const nav = document.querySelector('[data-nav]');

  function actualizarNav() {
    if (!nav) return;
    nav.classList.toggle('nav--con-fondo', window.scrollY > 40);
    nav.style.setProperty('--nav-alto', nav.offsetHeight + 'px');
  }

  const botonMenuMovil = document.querySelector('[data-accion="menu-movil"]');
  if (botonMenuMovil && nav) {
    botonMenuMovil.addEventListener('click', () => {
      const abierto = nav.classList.toggle('nav--menu-abierto');
      botonMenuMovil.setAttribute('aria-expanded', String(abierto));
      botonMenuMovil.setAttribute('aria-label', abierto ? 'Cerrar menú' : 'Abrir menú');
    });

    nav.querySelectorAll('.nav__links a').forEach((enlace) => {
      enlace.addEventListener('click', () => {
        nav.classList.remove('nav--menu-abierto');
        botonMenuMovil.setAttribute('aria-expanded', 'false');
      });
    });
  }

  /* ---------------- CTA flotante ---------------- */

  const ctaFlotante = document.querySelector('[data-cta-flotante]');

  function actualizarCtaFlotante() {
    if (!ctaFlotante) return;
    ctaFlotante.classList.toggle('esta-visible', window.scrollY > window.innerHeight * 0.6);
  }

  /* ---------------- Scroll (un solo listener) ---------------- */

  let tickPendiente = false;
  window.addEventListener(
    'scroll',
    () => {
      if (tickPendiente) return;
      tickPendiente = true;
      requestAnimationFrame(() => {
        actualizarProgreso();
        actualizarNav();
        actualizarCtaFlotante();
        moverParallaxHero();
        tickPendiente = false;
      });
    },
    { passive: true }
  );

  window.addEventListener('resize', actualizarNav, { passive: true });

  /* ---------------- Revelado por scroll ---------------- */

  const elementosRevelar = document.querySelectorAll('.revelar');

  if ('IntersectionObserver' in window && !prefiereMenosMovimiento) {
    const observador = new IntersectionObserver(
      (entradas) => {
        entradas.forEach((entrada) => {
          if (entrada.isIntersecting) {
            entrada.target.classList.add('esta-visible');
            observador.unobserve(entrada.target);
          }
        });
      },
      { threshold: 0.12, rootMargin: '0px 0px -60px 0px' }
    );
    elementosRevelar.forEach((el) => observador.observe(el));
  } else {
    elementosRevelar.forEach((el) => el.classList.add('esta-visible'));
  }

  /* ---------------- Acordeones (catálogo y FAQ) ---------------- */

  document.querySelectorAll('[data-acordeon]').forEach((grupo) => {
    const cabeceras = grupo.querySelectorAll('.fila-acordeon__cabecera');

    cabeceras.forEach((cabecera) => {
      cabecera.addEventListener('click', () => {
        const panel = document.getElementById(cabecera.getAttribute('aria-controls'));
        const estabaAbierto = cabecera.getAttribute('aria-expanded') === 'true';

        // Una sola fila abierta a la vez dentro de cada grupo.
        cabeceras.forEach((otra) => {
          if (otra === cabecera) return;
          otra.setAttribute('aria-expanded', 'false');
          const otroPanel = document.getElementById(otra.getAttribute('aria-controls'));
          if (otroPanel) otroPanel.classList.remove('esta-abierto');
        });

        cabecera.setAttribute('aria-expanded', String(!estabaAbierto));
        if (panel) panel.classList.toggle('esta-abierto', !estabaAbierto);
      });
    });
  });

  /* ---------------- Confirmación de los controles ----------------
     Sin esto el control parece muerto: varios de sus efectos son sutiles
     o quedan fuera de la parte de la pantalla que la persona mira. */

  function confirmarControl(boton, mensaje) {
    if (boton) {
      boton.classList.remove('acaba-de-cambiar');
      void boton.offsetWidth; // reinicia la animación
      boton.classList.add('acaba-de-cambiar');
      setTimeout(() => boton.classList.remove('acaba-de-cambiar'), 600);
    }
    if (mensaje) MV.toast(mensaje, 2200);
  }

  /* ---------------- Modo día / noche ---------------- */

  const botonTema = document.querySelector('[data-accion="tema"]');
  const temaGuardado = leerAlmacenamiento('mv-tema');

  aplicarTema(temaGuardado || raiz.getAttribute('data-tema') || 'noche');

  function aplicarTema(tema) {
    raiz.setAttribute('data-tema', tema);
    if (botonTema) {
      botonTema.setAttribute('aria-pressed', String(tema === 'dia'));
      botonTema.setAttribute('aria-label', tema === 'dia' ? 'Cambiar a modo noche' : 'Cambiar a modo día');
    }
  }

  if (botonTema) {
    botonTema.addEventListener('click', () => {
      const nuevo = raiz.getAttribute('data-tema') === 'dia' ? 'noche' : 'dia';
      aplicarTema(nuevo);
      guardarAlmacenamiento('mv-tema', nuevo);
      confirmarControl(botonTema, nuevo === 'dia' ? 'Modo día' : 'Modo noche');
    });
  }

  /* ---------------- Tamaño de letra ---------------- */

  const ESCALA_MIN = 0.9;
  const ESCALA_MAX = 1.3;
  let escala = parseFloat(leerAlmacenamiento('mv-escala')) || 1;
  aplicarEscala(escala);

  function aplicarEscala(valor) {
    escala = Math.min(ESCALA_MAX, Math.max(ESCALA_MIN, Math.round(valor * 100) / 100));
    raiz.style.setProperty('--escala-fuente', escala);
    guardarAlmacenamiento('mv-escala', String(escala));
  }

  const botonFuenteMas = document.querySelector('[data-accion="fuente-mas"]');
  const botonFuenteMenos = document.querySelector('[data-accion="fuente-menos"]');

  function ajustarEscala(delta, boton) {
    const antes = escala;
    aplicarEscala(escala + delta);

    if (escala === antes) {
      confirmarControl(boton, delta > 0 ? 'Ya estás en el texto más grande' : 'Ya estás en el texto más chico');
      return;
    }
    confirmarControl(boton, 'Texto al ' + Math.round(escala * 100) + '%');

    // El botón del extremo se apaga cuando ya no queda recorrido.
    if (botonFuenteMas) botonFuenteMas.disabled = escala >= ESCALA_MAX;
    if (botonFuenteMenos) botonFuenteMenos.disabled = escala <= ESCALA_MIN;
  }

  if (botonFuenteMas) botonFuenteMas.addEventListener('click', () => ajustarEscala(0.1, botonFuenteMas));
  if (botonFuenteMenos) botonFuenteMenos.addEventListener('click', () => ajustarEscala(-0.1, botonFuenteMenos));

  /* ---------------- Selector de idioma ----------------
     El contenido traducido todavía no existe: el control cambia el idioma
     declarado y avisa con honestidad en vez de fingir una traducción. */

  const IDIOMAS = [
    { codigo: 'es', etiqueta: 'ES', lang: 'es-CL' },
    { codigo: 'en', etiqueta: 'EN', lang: 'en' },
    { codigo: 'pt', etiqueta: 'PT', lang: 'pt' },
  ];
  const botonIdioma = document.querySelector('[data-accion="idioma"]');
  let indiceIdioma = 0;

  if (botonIdioma) {
    botonIdioma.addEventListener('click', () => {
      indiceIdioma = (indiceIdioma + 1) % IDIOMAS.length;
      const idioma = IDIOMAS[indiceIdioma];
      botonIdioma.textContent = idioma.etiqueta;

      confirmarControl(botonIdioma);

      if (idioma.codigo === 'es') {
        raiz.setAttribute('lang', idioma.lang);
        MV.toast('Sitio en español', 2200);
      } else {
        MV.toast(
          idioma.codigo === 'en'
            ? 'The English version is on its way. For now the site stays in Spanish.'
            : 'A versão em português está a caminho. Por enquanto o site segue em espanhol.'
        );
      }
    });
  }

  /* ---------------- Sonido ambiente ---------------- */

  const botonSonido = document.querySelector('[data-accion="sonido"]');
  let audioAmbiente = null;

  if (botonSonido) {
    botonSonido.addEventListener('click', () => {
      const activo = botonSonido.getAttribute('aria-pressed') === 'true';

      if (activo) {
        if (audioAmbiente) audioAmbiente.pause();
        botonSonido.setAttribute('aria-pressed', 'false');
        botonSonido.setAttribute('aria-label', 'Activar sonido ambiente');
        confirmarControl(botonSonido, 'Sonido ambiente apagado');
        return;
      }

      if (!audioAmbiente) {
        audioAmbiente = new Audio('assets/audio/oleaje.mp3');
        audioAmbiente.loop = true;
        audioAmbiente.volume = 0.35;
      }

      audioAmbiente
        .play()
        .then(() => {
          botonSonido.setAttribute('aria-pressed', 'true');
          botonSonido.setAttribute('aria-label', 'Silenciar sonido ambiente');
          confirmarControl(botonSonido, 'Sonido ambiente encendido');
        })
        .catch(() => {
          confirmarControl(botonSonido, 'El sonido ambiente todavía no está disponible.');
        });
    });
  }

  /* ---------------- Video del hero ---------------- */

  const video = document.querySelector('[data-hero-video]');
  const hero = document.querySelector('.hero');

  function conexionLenta() {
    const conexion = navigator.connection;
    if (!conexion) return false;
    if (conexion.saveData) return true;
    return ['slow-2g', '2g', '3g'].indexOf(conexion.effectiveType) !== -1;
  }

  if (video) {
    if (prefiereMenosMovimiento || conexionLenta()) {
      // Se queda el poster: ni se carga el video.
      video.removeAttribute('autoplay');
    } else {
      const esMovil = window.matchMedia('(max-width: 900px)').matches;
      // Si todavía no existe la versión liviana para móvil, se usa la normal.
      const fuente = (esMovil && video.dataset.srcMovil) || video.dataset.srcEscritorio;

      if (fuente) {
        video.src = fuente;
        video.load();
        video.play().catch(() => {
          /* Sin autoplay disponible: el poster queda visible, no rompe nada. */
        });
      }
    }

    video.addEventListener('error', () => {
      video.style.display = 'none';
    });
  }

  function moverParallaxHero() {
    if (!hero || !video || prefiereMenosMovimiento) return;
    const avance = window.scrollY;
    if (avance > window.innerHeight) return;
    video.style.transform = 'translate3d(0, ' + avance * 0.18 + 'px, 0) scale(1.06)';
  }

  /* ---------------- Carrusel de testimonios ----------------
     Pega aquí las reseñas reales (Google, WhatsApp, etc.). Mientras el
     arreglo esté vacío, la sección muestra un estado honesto en vez de
     testimonios inventados. */

  /* Reseñas publicadas en Google. Los textos van tal cual los escribieron
     sus autores, con sus modismos y sus erratas: corregirlos sería
     reescribir lo que dijo otra persona. */
  const TESTIMONIOS = [
    {
      cita: 'Llegué agotada, de esos días con la cabeza a mil. Alejandro lo notó al tiro, fue muy respetuoso y toda la sesión la adaptó a lo que yo necesitaba. Ame terminar y poder quedarme acostadita en mi cama, sin tener que volver a salir al taco de Santiago. De verdad lo recomiendo, se pasó.',
      autor: 'Valentina Sáez',
      servicio: 'Masaje relajante',
      estrellas: 5,
    },
    {
      cita: 'Trabajo en el computador y vivo con la espalda cargada. Con Alejandro voy a la segura, presión firme pero cuidada. Ya tengo todos mis martes bloqueados con él, amanezco como nueva al otro día.',
      autor: 'Catherine Ruz',
      servicio: 'Masaje descontracturante',
      estrellas: 5,
    },
    {
      cita: 'estuvo muy bkn. Conversamos un poco antes de empezar y Alejandro entendió perfecto dónde necesitaba relajarme y dónde trabajar más. Muy recomendable. compre un pack para mi pareja',
      autor: 'Maximiliano Talandriz',
      servicio: 'Masaje mixto',
      estrellas: 5,
    },
    {
      cita: 'Alejandro me ayudó un montón, Entreno harto en la semana y necesitaba un masaje. mi amiga pia me lo recomendó y si, esta bueno. harta fuerza y escucha. me suscribí para hacerlo habito. lo recomiendo ya. trabaja bien',
      autor: 'Nathaly Infante',
      servicio: 'Masaje deportivo',
      estrellas: 4,
    },
    {
      cita: 'Me había hecho un masaje con Alejandro y fue la raja, así que no lo pensé dos veces en suscribirme. super recomendado. muy buenas manos',
      autor: 'Rodrigo Roselló',
      servicio: 'Ritual profundo',
      estrellas: 5,
    },
    {
      cita: 'Resultado espectacular en una sola sesión. El trato humano es lo mejor. ale es muy divertido y amoroso',
      autor: 'Pamela Quiroga',
      servicio: 'Pack drenaje linfático',
      estrellas: 5,
    },
  ];

  const pista = document.querySelector('[data-carrusel-pista]');
  const controles = document.querySelector('[data-carrusel-controles]');
  const contenedorPuntos = document.querySelector('[data-carrusel-puntos]');

  if (pista) {
    if (!TESTIMONIOS.length) {
      pista.innerHTML =
        '<div class="testimonios__vacio">' +
        '<p>Todavía no hay reseñas publicadas acá.</p>' +
        '<p class="testimonio__autor">Si ya te atendiste, escríbeme por WhatsApp y con gusto la sumo.</p>' +
        '</div>';
    } else {
      let indiceActivo = 0;
      let rotacion = null;

      TESTIMONIOS.forEach((testimonio, indice) => {
        const estrellas = Math.max(0, Math.min(5, testimonio.estrellas || 0));
        const pie = [testimonio.autor, testimonio.servicio].filter(Boolean).map(escaparHTML).join(' · ');

        const slide = document.createElement('figure');
        slide.className = 'testimonio' + (indice === 0 ? ' esta-activo' : '');
        slide.innerHTML =
          (estrellas
            ? '<p class="testimonio__estrellas" aria-label="' +
              estrellas +
              ' de 5 estrellas">' +
              '<span aria-hidden="true">' +
              '★'.repeat(estrellas) +
              '<span class="testimonio__estrella-vacia">' +
              '★'.repeat(5 - estrellas) +
              '</span></span></p>'
            : '') +
          '<blockquote class="testimonio__cita">' +
          escaparHTML(testimonio.cita) +
          '</blockquote><figcaption class="testimonio__autor">' +
          pie +
          '</figcaption>';
        pista.appendChild(slide);

        if (contenedorPuntos) {
          const punto = document.createElement('button');
          punto.type = 'button';
          punto.className = 'testimonios__punto';
          punto.setAttribute('aria-label', 'Ver testimonio ' + (indice + 1));
          punto.setAttribute('aria-current', String(indice === 0));
          punto.addEventListener('click', () => {
            mostrarTestimonio(indice);
            detenerRotacion();
          });
          contenedorPuntos.appendChild(punto);
        }
      });

      const slides = pista.querySelectorAll('.testimonio');
      const puntos = contenedorPuntos ? contenedorPuntos.querySelectorAll('.testimonios__punto') : [];

      function mostrarTestimonio(indice) {
        indiceActivo = (indice + slides.length) % slides.length;
        slides.forEach((slide, i) => slide.classList.toggle('esta-activo', i === indiceActivo));
        puntos.forEach((punto, i) => punto.setAttribute('aria-current', String(i === indiceActivo)));
      }

      function iniciarRotacion() {
        if (slides.length < 2 || prefiereMenosMovimiento) return;
        rotacion = setInterval(() => mostrarTestimonio(indiceActivo + 1), 5500);
      }

      function detenerRotacion() {
        clearInterval(rotacion);
        rotacion = null;
      }

      if (slides.length > 1 && controles) {
        controles.hidden = false;

        const anterior = document.querySelector('[data-carrusel-anterior]');
        const siguiente = document.querySelector('[data-carrusel-siguiente]');

        if (anterior)
          anterior.addEventListener('click', () => {
            mostrarTestimonio(indiceActivo - 1);
            detenerRotacion();
          });
        if (siguiente)
          siguiente.addEventListener('click', () => {
            mostrarTestimonio(indiceActivo + 1);
            detenerRotacion();
          });

        const carrusel = document.querySelector('[data-carrusel]');
        if (carrusel) {
          carrusel.addEventListener('mouseenter', detenerRotacion);
          carrusel.addEventListener('focusin', detenerRotacion);
        }

        iniciarRotacion();
      }
    }
  }

  /* ---------------- Marca del hero, letra por letra ----------------
     El HTML trae el texto normal; acá se parte en spans solo para la
     entrada. Si esto no corre, el texto igual se lee bien. */

  const marca = document.querySelector('[data-marca-animada]');

  if (marca && !prefiereMenosMovimiento) {
    const texto = marca.textContent;
    const retrasoBase = raiz.classList.contains('intro-vista') ? 0.5 : 2.1;

    marca.textContent = '';
    Array.from(texto).forEach((caracter, indice) => {
      const span = document.createElement('span');
      span.className = 'letra';
      span.textContent = caracter;
      span.style.animationDelay = retrasoBase + indice * 0.045 + 's';
      marca.appendChild(span);
    });
    marca.setAttribute('aria-label', texto);
  }

  /* ---------------- Año del footer ---------------- */

  const anio = document.querySelector('[data-anio-actual]');
  if (anio) anio.textContent = String(new Date().getFullYear());

  /* ---------------- Helpers ---------------- */

  function escaparHTML(texto) {
    const div = document.createElement('div');
    div.textContent = texto;
    return div.innerHTML;
  }

  function leerAlmacenamiento(clave) {
    try {
      return localStorage.getItem(clave);
    } catch (error) {
      return null;
    }
  }

  function guardarAlmacenamiento(clave, valor) {
    try {
      localStorage.setItem(clave, valor);
    } catch (error) {
      /* Modo privado o almacenamiento bloqueado: se ignora. */
    }
  }

  MV.escaparHTML = escaparHTML;

  actualizarProgreso();
  actualizarNav();
  actualizarCtaFlotante();
})();
