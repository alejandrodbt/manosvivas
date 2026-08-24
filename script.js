function toggleMenu() {
  document.getElementById('mobileMenu').classList.toggle('open');
}

function abrirReserva(servicio) {
  const select = document.getElementById('servicio');
  for (const opt of select.options) {
    if (opt.value.startsWith(servicio) || opt.textContent.startsWith(servicio)) {
      select.value = opt.value;
      break;
    }
  }
  document.getElementById('reserva').scrollIntoView({ behavior: 'smooth' });
}

// Countdown a Fiestas Patrias (18 de septiembre)
(function () {
  const now = new Date();
  let year = now.getFullYear();
  let target = new Date(year, 8, 18, 0, 0, 0); // mes 8 = septiembre
  if (target < now) target = new Date(year + 1, 8, 18, 0, 0, 0);

  function update() {
    const diff = target - new Date();
    if (diff <= 0) return;
    const days = Math.floor(diff / (1000 * 60 * 60 * 24));
    const hours = Math.floor((diff / (1000 * 60 * 60)) % 24);
    const mins = Math.floor((diff / (1000 * 60)) % 60);
    const secs = Math.floor((diff / 1000) % 60);
    document.getElementById('cd-days').textContent = days;
    document.getElementById('cd-hours').textContent = String(hours).padStart(2, '0');
    document.getElementById('cd-min').textContent = String(mins).padStart(2, '0');
    document.getElementById('cd-sec').textContent = String(secs).padStart(2, '0');
  }
  update();
  setInterval(update, 1000);
})();

// Nav shadow on scroll
window.addEventListener('scroll', () => {
  const nav = document.querySelector('.nav');
  nav.style.boxShadow = window.scrollY > 40 ? '0 2px 24px rgba(42,47,38,.1)' : 'none';
});

// Animate on scroll
const observer = new IntersectionObserver(entries => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.style.opacity = '1';
      entry.target.style.transform = 'translateY(0)';
    }
  });
}, { threshold: 0.1 });

document.querySelectorAll('.service-card, .pack-card, .plan-card, .event-card').forEach(el => {
  el.style.opacity = '0';
  el.style.transform = 'translateY(16px)';
  el.style.transition = 'opacity .5s ease, transform .5s ease';
  observer.observe(el);
});
