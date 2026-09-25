/* ==========================================================================
   Manos Vivas — Formularios
   Validación compartida + newsletter (Brevo vía /api/newsletter).
   ========================================================================== */

(function () {
  'use strict';

  const MV = (window.MV = window.MV || {});

  const PATRON_EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/;

  /* Teléfono internacional: acepta cualquier código de país (+56, +1, +57,
     +58, +61, +34...). Solo exige entre 8 y 15 dígitos, que es el rango de
     E.164, y tolera espacios, guiones y paréntesis. */
  MV.validarTelefono = function (valor) {
    const limpio = String(valor || '').trim();
    if (!/^\+?[\d\s\-().]+$/.test(limpio)) return false;
    const digitos = limpio.replace(/\D/g, '');
    return digitos.length >= 8 && digitos.length <= 15;
  };

  MV.validarEmail = function (valor) {
    return PATRON_EMAIL.test(String(valor || '').trim());
  };

  MV.mostrarError = function (idCampo, mensaje) {
    const contenedor = document.querySelector('[data-error-para="' + idCampo + '"]');
    const campo = document.getElementById(idCampo);
    if (contenedor) contenedor.textContent = mensaje || '';
    if (campo) {
      if (mensaje) {
        campo.setAttribute('aria-invalid', 'true');
      } else {
        campo.removeAttribute('aria-invalid');
      }
    }
  };

  MV.limpiarErrores = function (contenedor) {
    (contenedor || document).querySelectorAll('.campo__error').forEach((el) => {
      el.textContent = '';
    });
    (contenedor || document).querySelectorAll('[aria-invalid]').forEach((el) => {
      el.removeAttribute('aria-invalid');
    });
  };

  /* ---------------- Newsletter ---------------- */

  const formulario = document.querySelector('[data-form-newsletter]');

  if (formulario) {
    const campoEmail = formulario.querySelector('#newsletter-email');
    const campoNombre = formulario.querySelector('#newsletter-nombre');
    const boton = formulario.querySelector('button[type="submit"]');

    campoEmail.addEventListener('input', () => MV.mostrarError('newsletter-email', ''));

    formulario.addEventListener('submit', async (evento) => {
      evento.preventDefault();

      const email = campoEmail.value.trim();
      if (!MV.validarEmail(email)) {
        MV.mostrarError('newsletter-email', 'Revisa el correo: parece que falta algo.');
        campoEmail.focus();
        return;
      }

      const textoOriginal = boton.textContent;
      boton.disabled = true;
      boton.textContent = 'Sumándote…';

      try {
        const respuesta = await fetch('/api/newsletter', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: email, nombre: campoNombre ? campoNombre.value.trim() : '' }),
        });

        if (!respuesta.ok) {
          const datos = await respuesta.json().catch(() => ({}));
          throw new Error(datos.error || 'No pude sumarte.');
        }

        formulario.reset();
        MV.toast('¡Ya eres parte de la manada! Revisa tu correo.');
      } catch (error) {
        MV.toast('No pude sumarte ahora. Inténtalo de nuevo en un momento.');
      } finally {
        boton.disabled = false;
        boton.textContent = textoOriginal;
      }
    });
  }
})();
