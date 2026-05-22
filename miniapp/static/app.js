(function () {
  const tg = window.Telegram?.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
  }

  const state = {
    me: null,
    stack: [],
    ctx: {},
    selected: null,
    answered: false,
  };

  function initData() {
    return tg?.initData || "";
  }

  async function api(path, options = {}) {
    const headers = {
      "Content-Type": "application/json",
      "X-Telegram-Init-Data": initData(),
      ...(options.headers || {}),
    };
    const res = await fetch(path, { ...options, headers });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }
    if (res.headers.get("content-type")?.includes("application/json")) {
      return res.json();
    }
    return res;
  }

  const $ = (sel) => document.querySelector(sel);
  const main = $("#main");
  const titleEl = $("#page-title");
  const btnBack = $("#btn-back");

  function t(ru, en, ar) {
    const lang = state.me?.ui_lang || "ru";
    if (lang === "ru") return ru;
    if (lang === "ar") return ar || en;
    return en;
  }

  function toast(msg, type) {
    const el = $("#toast");
    el.textContent = msg;
    el.className = "toast" + (type ? " " + type : "");
    el.classList.remove("hidden");
    setTimeout(() => el.classList.add("hidden"), 2500);
  }

  function pushView(name, data) {
    state.stack.push({ name, data: { ...state.ctx } });
    state.ctx = { ...state.ctx, ...data };
    updateBack();
    render();
  }

  function popView() {
    if (state.stack.length === 0) return;
    const prev = state.stack.pop();
    state.ctx = prev.data || {};
    state.selected = null;
    state.answered = false;
    updateBack();
    render();
  }

  function updateBack() {
    btnBack.classList.toggle("hidden", state.stack.length === 0);
  }

  btnBack.addEventListener("click", popView);

  async function loadMe() {
    state.me = await api("/api/me");
  }

  function renderMenu() {
    titleEl.textContent = "CSCA";
    main.innerHTML = `<div class="loading">${t("Загрузка…", "Loading…", "جاري التحميل…")}</div>`;
    api("/api/menu")
      .then((data) => {
        let html = `<div class="menu-list">`;
        (data.items || []).forEach((item) => {
          html += `<button type="button" class="card" data-menu="${item.id}" data-topic="${item.topic || ""}">${item.title}</button>`;
        });
        if (state.me?.continue?.length) {
          html += `<p style="color:var(--hint);margin:12px 0 6px">${t("Продолжить", "Continue", "متابعة")}</p>`;
          state.me.continue.forEach((c) => {
            html += `<button type="button" class="card" data-continue="${c.topic}" data-index="${c.last_index}">
              ▶ ${c.title} (${c.last_index}/${c.total})
            </button>`;
          });
        }
        html += `</div>`;
        main.innerHTML = html;

        main.querySelectorAll("[data-menu]").forEach((btn) => {
          btn.addEventListener("click", () => {
            const id = btn.dataset.menu;
            const topic = btn.dataset.topic;
            if (id === "topics") pushView("menu", { view: "topics" });
            else if (id === "physics" || id === "chemistry")
              startTopic(topic, null, parseInt(btn.dataset.index || "0", 10) || 0);
            else if (id === "exams") pushView("menu", { view: "exams" });
            else if (id === "stats") pushView("menu", { view: "stats" });
          });
        });
        main.querySelectorAll("[data-continue]").forEach((btn) => {
          btn.addEventListener("click", () => {
            startTopic(btn.dataset.continue, null, parseInt(btn.dataset.index, 10));
          });
        });
      })
      .catch(showError);
  }

  function renderTopics() {
    titleEl.textContent = t("Темы", "Topics", "المواضيع");
    main.innerHTML = `<div class="loading">…</div>`;
    api("/api/topics")
      .then((data) => {
        let html = `<div class="topic-list">`;
        (data.topics || []).forEach((top) => {
          html += `<button type="button" class="card" data-topic="${top.key}">
            ${top.title}<small>${top.count} ${t("задач", "tasks", "مسائل")}</small>
          </button>`;
        });
        html += `</div>`;
        main.innerHTML = html;
        main.querySelectorAll("[data-topic]").forEach((btn) => {
          btn.addEventListener("click", () => {
            pushView("topics", { topic: btn.dataset.topic, view: "subtopics" });
          });
        });
      })
      .catch(showError);
  }

  function renderSubtopics() {
    const topic = state.ctx.topic;
    titleEl.textContent = state.me?.continue?.find((c) => c.topic === topic)?.title || topic;
    api(`/api/topics/${encodeURIComponent(topic)}/subtopics`)
      .then((data) => {
        let html = `<div class="topic-list">`;
        html += `<button type="button" class="card" data-all="1">
          ${t("Все задачи темы", "All topic tasks", "كل مسائل الموضوع")}
        </button>`;
        (data.subtopics || []).forEach((s) => {
          html += `<button type="button" class="card" data-sub="${encodeURIComponent(s.key)}">
            ${s.title}<small>${s.count}</small>
          </button>`;
        });
        html += `</div>`;
        main.innerHTML = html;
        main.querySelector("[data-all]")?.addEventListener("click", () => {
          startTopic(topic, null, 0);
        });
        main.querySelectorAll("[data-sub]").forEach((btn) => {
          btn.addEventListener("click", () => {
            startTopic(topic, decodeURIComponent(btn.dataset.sub), 0);
          });
        });
      })
      .catch(showError);
  }

  function startTopic(topic, subtopic, index) {
    state.ctx = { mode: "topic", topic, subtopic, index: index || 0 };
    state.selected = null;
    state.answered = false;
    pushView("question", {});
  }

  function startExam(examKey, pos) {
    state.ctx = { mode: "exam", exam_key: examKey, exam_pos: pos || 0 };
    state.selected = null;
    state.answered = false;
    pushView("exam_q", {});
  }

  async function renderQuestion() {
    const { mode, topic, subtopic, index, exam_key, exam_pos } = state.ctx;
    state.selected = null;
    state.answered = false;

    let payload;
    if (mode === "exam") {
      payload = await api(`/api/exams/${exam_key}/question?pos=${exam_pos}`);
      titleEl.textContent = payload.title + ` (${payload.position + 1}/${payload.total})`;
      state.ctx.topic = payload.topic;
      state.ctx.q_index = payload.index;
      renderQuestionUI(payload.question, payload.position, payload.total, true);
      return;
    }

    let url = `/api/question?topic=${encodeURIComponent(topic)}&index=${index}`;
    if (subtopic) url += `&subtopic=${encodeURIComponent(subtopic)}`;
    payload = await api(url);
    titleEl.textContent = `${payload.position + 1} / ${payload.total}`;
    state.ctx.q_index = payload.question.index;
    renderQuestionUI(payload.question, payload.position, payload.total, false);
  }

  function renderQuestionUI(q, pos, total, isExam) {
    const topic = state.ctx.topic;
    const qIndex = q.index;
    let html = "";
    if (q.has_image) {
      html += `<img class="question-img" src="/api/image?topic=${encodeURIComponent(topic)}&index=${qIndex}" alt="" />`;
    }
    if (q.stars) html += `<p>${q.stars} n=${q.exam_n || ""}</p>`;
    html += `<div class="question-text">${escapeHtml(q.text)}</div>`;
    html += `<div class="options">`;
    (q.options || []).forEach((opt) => {
      html += `<button type="button" class="opt-btn" data-idx="${opt.index}">${escapeHtml(opt.label)}</button>`;
    });
    html += `</div><div class="actions">`;
    if (q.has_hint) {
      html += `<button type="button" class="btn secondary" id="btn-hint">${t("Подсказка", "Hint", "تلميح")}</button>`;
    }
    if (q.has_solution) {
      html += `<button type="button" class="btn secondary" id="btn-sol">${t("Решение", "Solution", "الحل")}</button>`;
    }
    html += `<button type="button" class="btn" id="btn-submit" disabled>${t("Ответить", "Submit", "إرسال")}</button>`;
    html += `</div><div id="sol-box"></div><div id="hint-box"></div>`;
    main.innerHTML = html;

    main.querySelectorAll(".opt-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (state.answered) return;
        main.querySelectorAll(".opt-btn").forEach((b) => b.classList.remove("selected"));
        btn.classList.add("selected");
        state.selected = parseInt(btn.dataset.idx, 10);
        $("#btn-submit").disabled = false;
      });
    });

    $("#btn-submit")?.addEventListener("click", () => submitAnswer(isExam, pos, total));
    $("#btn-hint")?.addEventListener("click", () => showHint(topic, qIndex));
    $("#btn-sol")?.addEventListener("click", () => showSolution(topic, qIndex));
  }

  async function submitAnswer(isExam, pos, total) {
    if (state.selected === null) return;
    const body = {
      topic: state.ctx.topic,
      index: state.ctx.subtopic ? pos : state.ctx.q_index,
      chosen_index: state.selected,
      mode: isExam ? "exam" : "topic",
      subtopic: state.ctx.subtopic || null,
      exam_key: isExam ? state.ctx.exam_key : null,
      exam_pos: isExam ? pos : null,
    };
    try {
      const res = await api("/api/answer", {
        method: "POST",
        body: JSON.stringify(body),
      });
      state.answered = true;
      toast(res.message, res.correct ? "ok" : "bad");
      main.querySelectorAll(".opt-btn").forEach((b) => {
        b.disabled = true;
        if (parseInt(b.dataset.idx, 10) === state.selected) {
          b.classList.add(res.correct ? "correct" : "wrong");
        }
      });
      $("#btn-submit").disabled = true;

      setTimeout(() => {
        if (isExam) {
          if (res.finished) {
            toast(t("Экзамен завершён", "Exam finished", "اكتمل الامتحان"), "ok");
            state.stack = [];
            state.ctx = { view: "exams" };
            updateBack();
            render();
          } else {
            state.ctx.exam_pos = res.next_pos;
            renderQuestion();
          }
        } else if (res.finished) {
          toast(t("Тема завершена", "Topic done", "اكتمل الموضوع"), "ok");
          popView();
          popView();
        } else {
          state.ctx.index = res.next_index;
          renderQuestion();
        }
      }, 1200);
    } catch (e) {
      toast(e.message, "bad");
    }
  }

  async function showSolution(topic, index) {
    const box = $("#sol-box");
    try {
      const res = await api(`/api/solution?topic=${encodeURIComponent(topic)}&index=${index}`);
      box.innerHTML = `<div class="solution-box">${escapeHtml(res.text)}</div>`;
    } catch (e) {
      box.innerHTML = `<p>${escapeHtml(e.message)}</p>`;
    }
  }

  function showHint(topic, index) {
    const box = $("#hint-box");
    box.innerHTML = `<img class="hint-img" src="/api/hint?topic=${encodeURIComponent(topic)}&index=${index}&t=${Date.now()}" alt="hint" />`;
  }

  function renderExams() {
    titleEl.textContent = t("Экзамены", "Exams", "الامتحانات");
    api("/api/exams")
      .then((data) => {
        let html = `<div class="topic-list">`;
        (data.exams || []).forEach((ex) => {
          html += `<button type="button" class="card" data-exam="${ex.key}">
            ${ex.title}<small>${ex.count} ${t("задач", "tasks", "مسائل")}</small>
          </button>`;
        });
        html += `</div>`;
        main.innerHTML = html;
        main.querySelectorAll("[data-exam]").forEach((btn) => {
          btn.addEventListener("click", () => startExam(btn.dataset.exam, 0));
        });
      })
      .catch(showError);
  }

  function renderStats() {
    titleEl.textContent = t("Статистика", "Statistics", "الإحصائيات");
    api("/api/stats")
      .then((s) => {
        const pct = s.accuracy_percent ?? 0;
        main.innerHTML = `
          <div class="stats-grid">
            <div class="stat-card">
              <div>${t("Всего ответов", "Total answers", "إجمالي الإجابات")}</div>
              <strong>${s.total_answered ?? 0}</strong>
            </div>
            <div class="stat-card">
              <div>${t("Верных", "Correct", "صحيح")}</div>
              <strong>${s.total_correct ?? 0}</strong>
              <div class="progress-bar"><span style="width:${pct}%"></span></div>
              <small>${pct}%</small>
            </div>
            <div class="stat-card">
              <div>${t("Тем с прогрессом", "Topics with progress", "مواضيع مع تقدم")}</div>
              <strong>${s.topics_count ?? 0}</strong>
            </div>
          </div>`;
      })
      .catch(showError);
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function showError(e) {
    main.innerHTML = `<p style="color:var(--bad)">${escapeHtml(e.message)}</p>
      <p>${t("Откройте приложение из Telegram-бота.", "Open from Telegram bot.", "افتح من بوت تيليجرام.")}</p>`;
  }

  function render() {
    const view = state.ctx.view || "home";
    if (view === "home") renderMenu();
    else if (view === "topics") renderTopics();
    else if (view === "subtopics") renderSubtopics();
    else if (view === "exams") renderExams();
    else if (view === "stats") renderStats();
    else if (state.ctx.mode === "topic" || state.ctx.mode === "exam") renderQuestion().catch(showError);
  }

  async function init() {
    if (!initData()) {
      showError(new Error(t("Нет данных Telegram", "No Telegram data", "لا بيانات تيليجرام")));
      return;
    }
    try {
      await loadMe();
      state.ctx = { view: "home" };
      render();
    } catch (e) {
      showError(e);
    }
  }

  init();
})();
