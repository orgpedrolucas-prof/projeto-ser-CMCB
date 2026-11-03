document.addEventListener('click', (event) => {
  const btn = event.target.closest('[data-toggle]');
  if (btn) {
    const target = document.getElementById(btn.dataset.toggle);
    if (target) target.classList.toggle('hidden');
  }
  if (document.body.classList.contains('menu-open') && event.target.closest('.navlink')) {
    document.body.classList.remove('menu-open');
  }
});
document.querySelectorAll('[data-search]').forEach((input) => {
  input.addEventListener('input', () => {
    const table = document.getElementById(input.dataset.search);
    if (!table) return;
    const term = input.value.toLocaleLowerCase('pt-BR').trim();
    table.querySelectorAll('tbody > tr:not(.editing-row)').forEach((row) => {
      const match = row.textContent.toLocaleLowerCase('pt-BR').includes(term);
      row.style.display = match ? '' : 'none';
      if (!match) {
        const next = row.nextElementSibling;
        if (next && next.classList.contains('editing-row')) next.classList.add('hidden');
      }
    });
  });
});

const startSelect = document.getElementById('start-tri');
const endSelect = document.getElementById('end-tri');
if (startSelect && endSelect) {
  const update = () => {
    const start = Number(startSelect.value);
    const end = Number(endSelect.value);
    if (end <= start) endSelect.value = String(start + 1);
    ['pdf','xlsx'].forEach((kind) => {
      const element = document.getElementById(`progress-${kind}`);
      if (element) {
        const url = new URL(element.href);
        url.searchParams.set('inicio',startSelect.value);
        url.searchParams.set('fim',endSelect.value);
        element.href = url.href;
      }
    });
  };
  startSelect.addEventListener('change',update);
  endSelect.addEventListener('change',update);
}


// Controla a obrigatoriedade de dias de suspensão nos formulários de medidas.
document.querySelectorAll('[data-disciplinary-form]').forEach((form) => {
  const kind = form.querySelector('[name="action_type"]');
  const days = form.querySelector('[name="suspension_days"]');
  if (!kind || !days) return;
  const refresh = () => {
    const isSuspension = kind.value === 'suspensao';
    days.required = isSuspension;
    days.disabled = !isSuspension;
    days.closest('label').style.opacity = isSuspension ? '1' : '0.5';
  };
  kind.addEventListener('change', refresh);
  refresh();
});

// SIMR 5: navegação móvel acessível.
const mobileToggle = document.querySelector('[data-open-menu]');
const closeMenu = () => {
  document.body.classList.remove('menu-open');
  mobileToggle?.setAttribute('aria-expanded', 'false');
};
mobileToggle?.addEventListener('click', () => {
  const willOpen = !document.body.classList.contains('menu-open');
  document.body.classList.toggle('menu-open', willOpen);
  mobileToggle.setAttribute('aria-expanded', String(willOpen));
});
document.querySelector('[data-close-menu]')?.addEventListener('click', closeMenu);
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeMenu();
});
