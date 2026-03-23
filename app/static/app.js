const statsEl = document.getElementById("stats");
const historyEl = document.getElementById("history");
const sourcesEl = document.getElementById("sources");
const docsEl = document.getElementById("docs");
const answerEl = document.getElementById("answer");
const answerStatusEl = document.getElementById("answer-status");
const reindexStatusEl = document.getElementById("reindex-status");

function renderEmpty(container, text) {
  container.innerHTML = `<div class="empty-state">${text}</div>`;
}

function renderStats(stats) {
  statsEl.innerHTML = `
    <div class="stat-card">
      <span>Документы</span>
      <strong>${stats.docs_count}</strong>
    </div>
    <div class="stat-card">
      <span>Чанки</span>
      <strong>${stats.chunks_count}</strong>
    </div>
    <div class="stat-card">
      <span>Запросы</span>
      <strong>${stats.recent_requests}</strong>
    </div>
    <div class="stat-card">
      <span>Статус</span>
      <strong>OK</strong>
    </div>
  `;
}

function renderHistory(items) {
  if (!items.length) {
    renderEmpty(historyEl, "История пока пуста.");
    return;
  }

  historyEl.innerHTML = items
    .map(
      (item) => `
        <article class="history-card">
          <h3>${item.question}</h3>
          <p class="meta">${new Date(item.asked_at).toLocaleString("ru-RU")}</p>
          <p>${item.answer_preview}</p>
        </article>
      `,
    )
    .join("");
}

function renderSources(sources) {
  if (!sources.length) {
    renderEmpty(sourcesEl, "Источники появятся после первого ответа.");
    return;
  }

  sourcesEl.innerHTML = sources
    .map(
      (source) => `
        <article class="source-card">
          <h3>${source.title}</h3>
          <p>${source.description || ""}</p>
          <p class="meta">${source.file_path}</p>
          <p class="meta">Фрагмент #${source.chunk_index}</p>
          <p>${source.snippet}</p>
        </article>
      `,
    )
    .join("");
}

function renderDocs(items) {
  if (!items.length) {
    renderEmpty(docsEl, "Описание документов появится после индексации.");
    return;
  }

  docsEl.innerHTML = items
    .map(
      (item) => `
        <article class="source-card">
          <h3>${item.title}</h3>
          <p class="meta">${item.file_type} • ${item.file_path}</p>
          <p>${item.description}</p>
        </article>
      `,
    )
    .join("");
}

async function loadStats() {
  const response = await fetch("/api/stats");
  const stats = await response.json();
  renderStats(stats);
}

async function loadHistory() {
  const response = await fetch("/api/history");
  const items = await response.json();
  renderHistory(items);
}

async function loadDocs() {
  const response = await fetch("/api/docs");
  const items = await response.json();
  renderDocs(items);
}

async function askQuestion(event) {
  event.preventDefault();
  const question = document.getElementById("question").value.trim();
  const topK = Number(document.getElementById("top-k").value || 8);
  if (!question) {
    return;
  }

  answerStatusEl.textContent = "Ищу";
  answerStatusEl.classList.add("pending");
  answerEl.textContent = "Поиск и формирование ответа...";

  const response = await fetch("/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, top_k: topK }),
  });
  const payload = await response.json();

  answerEl.textContent = payload.answer;
  renderSources(payload.sources || []);
  answerStatusEl.textContent = "Готов";
  answerStatusEl.classList.remove("pending");

  await Promise.all([loadHistory(), loadStats()]);
}

async function reindex() {
  reindexStatusEl.textContent = "Индексирование запущено...";
  const response = await fetch("/index", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  const payload = await response.json();
  reindexStatusEl.textContent =
    `Новых: ${payload.indexed_docs}, обновлено: ${payload.updated_docs}, пропущено: ${payload.skipped_docs}`;
  await Promise.all([loadStats(), loadHistory(), loadDocs()]);
}

document.getElementById("ask-form").addEventListener("submit", askQuestion);
document.getElementById("reindex-button").addEventListener("click", reindex);

renderEmpty(sourcesEl, "Источники появятся после первого ответа.");
renderEmpty(docsEl, "Описание документов появится после индексации.");
loadStats();
loadHistory();
loadDocs();
