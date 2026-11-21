(() => {
  "use strict";

  const state = {
    repositories: [],
    repoId: "",
    status: null,
    documents: [],
    sampleQuestions: [],
    messages: [],
    sources: [],
    topK: 4,
    retrievalMode: "hybrid_rerank",
    retrievalModeInitialized: false,
    retrievalTrace: null,
    busy: new Set(),
    loadingDocuments: true,
    evidenceSummary: "等待检索",
    chunkRequestId: 0,
    pollTimer: null,
    releasePollTimer: null,
    noticeTimer: null,
    releasePayload: null,
    generations: [],
    activeRelease: null,
    pendingCount: 0,
    currentJob: null,
    jobEvents: [],
    reconcileReport: null,
    investigation: null,
    buildIdempotencyKey: null,
    releaseCollapsed: false,
    ragasRuns: [],
    ragasRun: null,
    ragasPollTimer: null,
  };

  const refs = {
    repositorySelect: byId("repositorySelect"),
    serviceState: byId("serviceState"),
    knowledgeBaseName: byId("knowledgeBaseName"),
    documentStatus: byId("documentStatus"),
    chunkStatus: byId("chunkStatus"),
    embeddingStatus: byId("embeddingStatus"),
    queryInstructionStatus: byId("queryInstructionStatus"),
    vectorStatus: byId("vectorStatus"),
    retrievalStatus: byId("retrievalStatus"),
    rerankStatus: byId("rerankStatus"),
    generationStatus: byId("generationStatus"),
    noticeBar: byId("noticeBar"),
    noticeIcon: byId("noticeIcon"),
    noticeText: byId("noticeText"),
    noticeClose: byId("noticeClose"),
    refreshButton: byId("refreshButton"),
    studioLayout: byId("studioLayout"),
    releaseStateBadge: byId("releaseStateBadge"),
    releaseSummary: byId("releaseSummary"),
    releaseCount: byId("releaseCount"),
    releaseAlias: byId("releaseAlias"),
    releaseCollection: byId("releaseCollection"),
    pendingCount: byId("pendingCount"),
    pendingDetail: byId("pendingDetail"),
    pendingDocumentBadge: byId("pendingDocumentBadge"),
    mobileReleaseCount: byId("mobileReleaseCount"),
    releaseToggleButton: byId("releaseToggleButton"),
    reconcileButton: byId("reconcileButton"),
    reconcileSummary: byId("reconcileSummary"),
    buildReleaseButton: byId("buildReleaseButton"),
    buildReleaseLabel: byId("buildReleaseLabel"),
    jobState: byId("jobState"),
    jobProgress: byId("jobProgress"),
    jobProgressBar: byId("jobProgressBar"),
    jobStage: byId("jobStage"),
    jobError: byId("jobError"),
    qualityState: byId("qualityState"),
    qualityMetrics: byId("qualityMetrics"),
    ragasState: byId("ragasState"),
    ragasMetrics: byId("ragasMetrics"),
    ragasSummary: byId("ragasSummary"),
    openRagasDetailsButton: byId("openRagasDetailsButton"),
    runRagasButton: byId("runRagasButton"),
    runRagasLabel: byId("runRagasLabel"),
    generationList: byId("generationList"),
    documentSummary: byId("documentSummary"),
    documentList: byId("documentList"),
    fileInput: byId("fileInput"),
    uploadZone: byId("uploadZone"),
    uploadTitle: byId("uploadTitle"),
    uploadHint: byId("uploadHint"),
    supportedFiles: byId("supportedFiles"),
    seedButton: byId("seedButton"),
    seedButtonLabel: byId("seedButtonLabel"),
    messages: byId("messages"),
    questionForm: byId("questionForm"),
    questionInput: byId("questionInput"),
    searchButton: byId("searchButton"),
    askButton: byId("askButton"),
    askButtonLabel: byId("askButtonLabel"),
    investigateButton: byId("investigateButton"),
    investigateButtonLabel: byId("investigateButtonLabel"),
    clearChatButton: byId("clearChatButton"),
    investigationTrace: byId("investigationTrace"),
    evidenceList: byId("evidenceList"),
    evidenceSummary: byId("evidenceSummary"),
    evidenceCount: byId("evidenceCount"),
    mobileEvidenceCount: byId("mobileEvidenceCount"),
    chunksDialog: byId("chunksDialog"),
    chunksTitle: byId("chunksTitle"),
    chunksMeta: byId("chunksMeta"),
    chunksContent: byId("chunksContent"),
    closeChunksButton: byId("closeChunksButton"),
    ragasDialog: byId("ragasDialog"),
    ragasDetailsMeta: byId("ragasDetailsMeta"),
    ragasDetailsContent: byId("ragasDetailsContent"),
    closeRagasDetailsButton: byId("closeRagasDetailsButton"),
  };

  function byId(id) {
    return document.getElementById(id);
  }

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function icon(name, className = "") {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", `icon${className ? ` ${className}` : ""}`);
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#i-${name}`);
    svg.append(use);
    return svg;
  }

  function replaceIcon(element, name) {
    const use = element.querySelector("use");
    if (use) use.setAttribute("href", `#i-${name}`);
  }

  function clear(element) {
    element.replaceChildren();
  }

  function makeId() {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function numberValue(value, fallback = 0) {
    const result = Number(value);
    return Number.isFinite(result) ? result : fallback;
  }

  function formatCount(value) {
    return new Intl.NumberFormat("zh-CN").format(numberValue(value));
  }

  function formatBytes(value) {
    const bytes = numberValue(value);
    if (bytes <= 0) return "";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} KB`;
    return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  }

  function formatDate(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function formatPercent(value) {
    const score = Number(value);
    return Number.isFinite(score) ? `${(score * 100).toFixed(score >= 0.995 ? 0 : 1)}%` : "--";
  }

  function shortId(value, size = 9) {
    const text = String(value || "");
    return text.length > size ? text.slice(0, size) : text || "--";
  }

  function firstValue(...values) {
    return values.find((value) => value !== undefined && value !== null && value !== "");
  }

  function releaseStateLabel(value) {
    const stateValue = String(value || "").toLowerCase();
    const labels = {
      pending: "排队中",
      queued: "排队中",
      running: "执行中",
      building: "构建中",
      validating: "验收中",
      succeeded: "已完成",
      passed: "已通过",
      active: "Serving",
      partial: "仅硬规则",
      archived: "可回滚",
      failed: "失败",
    };
    return labels[stateValue] || value || "未知";
  }

  function releaseStateTone(value) {
    const stateValue = String(value || "").toLowerCase();
    if (["active", "passed", "succeeded"].includes(stateValue)) return "is-success";
    if (["failed", "error"].includes(stateValue)) return "is-error";
    if (["building", "validating", "running", "pending", "queued"].includes(stateValue)) {
      return "is-processing";
    }
    if (stateValue === "partial") return "is-pending";
    if (stateValue === "archived") return "is-archived";
    return "is-muted";
  }

  function setPill(element, value, label) {
    if (!element) return;
    element.className = `status-pill ${releaseStateTone(value)}`;
    element.textContent = label || releaseStateLabel(value);
  }

  function retrievalModeLabel(mode) {
    if (mode === "dense") return "Dense";
    if (mode === "hybrid") return "Hybrid · RRF";
    if (mode === "hybrid_rerank") return "Hybrid · RRF · Rerank";
    return mode || "未配置";
  }

  function setRetrievalMode(mode) {
    if (!["dense", "hybrid", "hybrid_rerank"].includes(mode)) return;
    state.retrievalMode = mode;
    document.querySelectorAll("[data-retrieval-mode]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.retrievalMode === mode));
    });
    if (refs.retrievalStatus) refs.retrievalStatus.textContent = retrievalModeLabel(mode);
  }

  function retrievalSummary(trace) {
    const value = trace || {};
    const parts = [retrievalModeLabel(value.requested_mode || state.retrievalMode)];
    const rerank = value.rerank || {};
    if (rerank.mode === "fallback_hybrid") parts.push("Rerank 降级");
    else if (rerank.mode === "qwen_api") parts.push(rerank.model || "Qwen");
    else if (rerank.mode === "disabled") parts.push("Rerank 关闭");
    const gate = value.answer_gate || {};
    if (Number.isFinite(Number(gate.accepted_count))) {
      parts.push(`Gate ${gate.accepted_count}/${numberValue(gate.accepted_count) + numberValue(gate.rejected_count)}`);
    }
    return parts.join(" · ");
  }

  function apiDetail(payload, statusText) {
    const detail = payload?.detail ?? payload?.message ?? payload?.error;
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) => {
          if (typeof item === "string") return item;
          if (item && typeof item === "object") {
            const location = Array.isArray(item.loc) ? item.loc.join(".") : "";
            return [location, item.msg].filter(Boolean).join("：");
          }
          return String(item);
        })
        .filter(Boolean);
      if (messages.length) return messages.join("；");
    }
    if (detail && typeof detail === "object") {
      try {
        return JSON.stringify(detail);
      } catch {
        return statusText;
      }
    }
    return String(detail || statusText || "请求失败");
  }

  async function request(path, options = {}) {
    const nextOptions = { ...options };
    const headers = new Headers(nextOptions.headers || {});
    if (
      nextOptions.body &&
      !(nextOptions.body instanceof FormData) &&
      typeof nextOptions.body !== "string"
    ) {
      headers.set("Content-Type", "application/json");
      nextOptions.body = JSON.stringify(nextOptions.body);
    }
    headers.set("Accept", "application/json");
    nextOptions.headers = headers;

    let response;
    try {
      response = await fetch(path, nextOptions);
    } catch (error) {
      throw new Error(`无法连接 RAG 服务：${error instanceof Error ? error.message : String(error)}`);
    }

    const contentType = response.headers.get("content-type") || "";
    let payload = null;
    if (response.status !== 204) {
      if (contentType.includes("application/json")) {
        try {
          payload = await response.json();
        } catch {
          payload = null;
        }
      } else {
        const text = await response.text();
        payload = text ? { detail: text } : null;
      }
    }

    if (!response.ok) {
      throw new Error(apiDetail(payload, `${response.status} ${response.statusText}`));
    }
    return payload;
  }

  function repositoryPath(path) {
    if (!state.repoId) throw new Error("请先选择仓库");
    return `/api/repositories/${encodeURIComponent(state.repoId)}${path}`;
  }

  async function loadRepositories() {
    const repositories = await request("/api/repositories");
    state.repositories = Array.isArray(repositories) ? repositories : [];
    if (!state.repositories.length) throw new Error("当前没有可用仓库");
    if (!state.repositories.some((item) => item.id === state.repoId)) {
      state.repoId = state.repositories[0].id;
    }
    clear(refs.repositorySelect);
    state.repositories.forEach((repository) => {
      const option = node("option", "", repository.full_name || repository.id);
      option.value = repository.id;
      option.selected = repository.id === state.repoId;
      refs.repositorySelect.append(option);
    });
  }

  function setServiceState(mode) {
    refs.serviceState.classList.toggle("is-loading", mode === "loading");
    refs.serviceState.classList.toggle("is-offline", mode === "offline");
    refs.serviceState.lastChild.textContent =
      mode === "online" ? " 服务正常" : mode === "offline" ? " 连接失败" : " 正在连接";
  }

  function setNotice(kind, message) {
    if (state.noticeTimer) {
      window.clearTimeout(state.noticeTimer);
      state.noticeTimer = null;
    }
    refs.noticeBar.hidden = !message;
    refs.noticeBar.classList.toggle("is-error", kind === "error");
    refs.noticeBar.classList.toggle("is-success", kind === "success");
    refs.noticeText.textContent = message || "";
    replaceIcon(
      refs.noticeIcon,
      kind === "error" ? "circle-alert" : kind === "success" ? "circle-check" : "info",
    );
    if (message && kind !== "error") {
      state.noticeTimer = window.setTimeout(() => {
        refs.noticeBar.hidden = true;
      }, kind === "success" ? 5000 : 6500);
    }
  }

  function isBusy(...keys) {
    return keys.some((key) => state.busy.has(key));
  }

  function setBusy(key, value) {
    if (value) state.busy.add(key);
    else state.busy.delete(key);
    renderControls();
  }

  function renderControls() {
    const querying = isBusy("ask", "search", "investigate");
    const releaseJobRunning =
      ["pending", "queued", "running"].includes(
        String(state.currentJob?.state || "").toLowerCase(),
      ) ||
      state.generations.some((item) =>
        ["building", "validating"].includes(
          String(item?.state || item?.status || "").toLowerCase(),
        ),
      );
    const releaseBusy =
      releaseJobRunning ||
      isBusy("build-release", "reconcile") ||
      Array.from(state.busy).some(
        (key) => key.startsWith("promote:") || key.startsWith("rollback:"),
      );
    const hasQuestion = Boolean(refs.questionInput.value.trim());

    refs.refreshButton.disabled = isBusy("refresh");
    refs.refreshButton.classList.toggle("is-spinning", isBusy("refresh"));
    refs.buildReleaseButton.disabled =
      releaseBusy || isBusy("refresh") || !state.documents.length;
    refs.buildReleaseButton.classList.toggle("is-spinning", isBusy("build-release"));
    refs.buildReleaseLabel.textContent = isBusy("build-release")
      ? "正在提交"
      : releaseJobRunning
        ? "构建进行中"
        : "构建并验收";
    refs.reconcileButton.disabled = releaseBusy || !state.activeRelease;
    refs.reconcileButton.classList.toggle("is-spinning", isBusy("reconcile"));
    refs.uploadZone.disabled = isBusy("upload");
    refs.fileInput.disabled = isBusy("upload");
    refs.seedButton.disabled = isBusy("seed", "upload");
    refs.seedButton.classList.toggle("is-spinning", isBusy("seed"));
    refs.seedButtonLabel.textContent = isBusy("seed") ? "正在导入" : "导入示例资料";
    refs.clearChatButton.disabled = querying;
    refs.investigateButton.disabled = querying || !state.activeRelease;
    refs.investigateButton.classList.toggle("is-spinning", isBusy("investigate"));
    refs.investigateButtonLabel.textContent = isBusy("investigate") ? "调查中" : "调查发布冲突";
    const ragasRunning = ["queued", "running"].includes(
      String(state.ragasRun?.status || "").toLowerCase(),
    );
    refs.runRagasButton.disabled = ragasRunning || isBusy("run-ragas") || !state.activeRelease;
    refs.runRagasButton.classList.toggle("is-spinning", ragasRunning || isBusy("run-ragas"));
    refs.runRagasLabel.textContent = ragasRunning ? "评测中" : "运行";
    refs.searchButton.disabled = querying || !state.activeRelease || !hasQuestion;
    refs.askButton.disabled = querying || !state.activeRelease || !hasQuestion;
    document.querySelectorAll("[data-retrieval-mode]").forEach((button) => {
      button.disabled = querying;
    });
    refs.askButton.classList.toggle("is-spinning", isBusy("ask"));
    refs.askButtonLabel.textContent = isBusy("ask") ? "生成中" : "提问";

    if (!isBusy("upload")) {
      refs.uploadTitle.textContent = "加入待发布资料";
      refs.uploadHint.textContent = "点击选择，或把文件拖到这里";
    }
  }

  function renderStatus() {
    const status = state.status;
    if (!status) return;

    refs.knowledgeBaseName.textContent = status.knowledge_base_name || "本地知识库";
    refs.documentStatus.textContent = `${formatCount(status.document_count)} 份`;
    refs.chunkStatus.textContent = `${formatCount(status.chunk_count)} 条`;

    const embedding = status.embedding || {};
    const embeddingParts = [];
    if (embedding.provider) embeddingParts.push(embedding.provider);
    if (embedding.model && embedding.model !== embedding.provider) embeddingParts.push(embedding.model);
    embeddingParts.push(
      embedding.dimensions ? `${embedding.dimensions}d` : "维度待定",
    );
    const embeddingLabel = embeddingParts.join(" · ") || "未配置";
    refs.embeddingStatus.textContent =
      embedding.available === false ? `${embeddingLabel} · 不可用` : embeddingLabel;
    refs.embeddingStatus.title = refs.embeddingStatus.textContent;
    refs.embeddingStatus.classList.toggle("is-unavailable", embedding.available === false);

    const queryInstruction = String(embedding.query_instruction || "").trim();
    refs.queryInstructionStatus.textContent = queryInstruction ? "已启用" : "未启用";
    refs.queryInstructionStatus.title =
      queryInstruction || "查询文本不会添加任务说明";

    const vector = status.vector_store || {};
    const vectorParts = [vector.backend, vector.collection].filter(Boolean);
    const vectorLabel = vectorParts.join(" · ") || "未配置";
    refs.vectorStatus.textContent =
      vector.available === false ? `${vectorLabel} · 不可用` : vectorLabel;
    refs.vectorStatus.title =
      [vector.detail, vector.uri].filter(Boolean).join("\n") ||
      refs.vectorStatus.textContent;
    refs.vectorStatus.classList.toggle("is-unavailable", vector.available === false);

    const retrieval = status.retrieval || {};
    if (!state.retrievalModeInitialized) {
      setRetrievalMode(retrieval.default_mode || state.retrievalMode);
      state.retrievalModeInitialized = true;
    }
    refs.retrievalStatus.textContent = retrievalModeLabel(state.retrievalMode);
    refs.retrievalStatus.title = [
      `Dense candidates: ${retrieval.dense_candidate_k ?? "--"}`,
      `BM25 candidates: ${retrieval.keyword_candidate_k ?? "--"}`,
      retrieval.rrf_k ? `RRF k: ${retrieval.rrf_k}` : "",
    ].filter(Boolean).join("\n");

    const rerank = status.rerank || {};
    const rerankLabel = rerank.model || rerank.provider || "未配置";
    refs.rerankStatus.textContent =
      rerank.available === false ? `${rerankLabel} · 降级` : rerankLabel;
    refs.rerankStatus.title = rerank.detail || refs.rerankStatus.textContent;
    refs.rerankStatus.classList.toggle("is-unavailable", rerank.available === false);

    const generation = status.generation || {};
    const generationLabel =
      generation.model || (generation.available === false ? "本地证据模式" : "已配置");
    refs.generationStatus.textContent =
      generation.available === false && generation.model
        ? `${generationLabel} · 不可用`
        : generationLabel;
    refs.generationStatus.title = refs.generationStatus.textContent;
    refs.generationStatus.classList.toggle("is-unavailable", generation.available === false);

    const supported = Array.isArray(status.supported_files) ? status.supported_files : [];
    const extensions = supported
      .map((value) => String(value).trim())
      .filter(Boolean)
      .map((value) => (value.startsWith(".") || value.includes("/") ? value : `.${value}`));
    refs.fileInput.accept = extensions.join(",");
    refs.supportedFiles.textContent = extensions.length
      ? `支持 ${extensions.map((value) => value.replace(/^\./, "").toUpperCase()).join(" / ")}`
      : "支持常见文本资料";
  }

  function normalizeJobPayload(payload) {
    const root = payload && typeof payload === "object" ? payload : {};
    const job = root.job && typeof root.job === "object" ? root.job : root;
    const progress = job.progress && typeof job.progress === "object" ? job.progress : {};
    return {
      ...job,
      id: firstValue(job.id, job.job_id),
      generation_id: firstValue(job.generation_id, root.generation_id),
      state: firstValue(job.state, job.status, "pending"),
      progress_current: numberValue(
        firstValue(job.progress_current, progress.current, progress.completed),
      ),
      progress_total: firstValue(job.progress_total, progress.total),
      progress_message: firstValue(
        job.progress_message,
        job.stage,
        progress.message,
        root.message,
      ),
      error_message: firstValue(job.error_message, job.error, root.error),
    };
  }

  function normalizeReleasePayload(payload) {
    const root = Array.isArray(payload)
      ? { generations: payload }
      : payload && typeof payload === "object"
        ? payload
        : {};
    const statusRelease = state.status?.release || state.status?.releases || {};
    const generationsValue = firstValue(root.generations, root.items, root.releases, []);
    const generations = Array.isArray(generationsValue) ? generationsValue : [];
    const activeFromList = generations.find(
      (item) => item?.is_active || String(item?.state || item?.status || "") === "active",
    );
    const activeValue = firstValue(
      root.active_generation,
      root.active,
      root.serving,
      root.current,
      activeFromList,
      statusRelease.active_generation,
      statusRelease.active,
    );
    const activeRoot = activeValue && typeof activeValue === "object" ? activeValue : {};
    const activeGeneration =
      activeRoot.generation && typeof activeRoot.generation === "object"
        ? activeRoot.generation
        : activeRoot;
    const aliasName = firstValue(
      activeRoot.alias_name,
      activeRoot.alias,
      root.alias_name,
      root.alias,
      statusRelease.alias_name,
      statusRelease.alias,
      statusRelease.active?.alias_name,
      statusRelease.active?.alias,
    );
    const physicalCollection = firstValue(
      activeRoot.physical_collection,
      activeRoot.collection,
      activeGeneration.physical_collection,
      root.active_collection,
      statusRelease.physical_collection,
      statusRelease.active_collection,
      statusRelease.active?.physical_collection,
      statusRelease.active?.collection,
    );
    const jobs = Array.isArray(root.jobs) ? root.jobs : [];
    const runningJob = jobs.find((item) =>
      ["pending", "queued", "running"].includes(String(item?.state || item?.status || "")),
    );
    const jobValue = firstValue(root.current_job, root.latest_job, root.job, runningJob);
    const pendingChanges = firstValue(root.pending_changes, statusRelease.pending_changes, {});
    const stagedDocuments = state.documents.filter((item) =>
      ["pending", "staged", "pending_create", "pending_update", "pending_delete", "deleted"].includes(
        String(item.status || "").toLowerCase(),
      ),
    ).length;
    const pendingCount = numberValue(
      firstValue(
        root.pending_count,
        root.pending_document_count,
        root.pending_documents,
        pendingChanges?.count,
        statusRelease.pending_count,
        statusRelease.pending_document_count,
        stagedDocuments,
      ),
    );
    return {
      root,
      active: physicalCollection
        ? {
            ...activeGeneration,
            generation_id: firstValue(
              activeGeneration.generation_id,
              activeGeneration.id,
              activeRoot.generation_id,
            ),
            alias_name: aliasName,
            physical_collection: physicalCollection,
          }
        : null,
      generations,
      currentJob: jobValue ? normalizeJobPayload(jobValue) : null,
      pendingCount,
      pendingChanges: pendingChanges && typeof pendingChanges === "object" ? pendingChanges : {},
    };
  }

  function qualityReportFor(generation) {
    if (!generation) return null;
    const reports = state.releasePayload?.quality_reports || {};
    return firstValue(
      generation.quality_gate,
      generation.quality_report,
      generation.report?.quality_gate,
      generation.report,
      generation.validation,
      reports[generation.id],
      reports[generation.generation_id],
      state.releasePayload?.quality_gate,
    );
  }

  function renderJob() {
    const job = state.currentJob;
    if (!job?.id) {
      const runningGeneration = state.generations.find((item) =>
        ["building", "validating"].includes(
          String(item?.state || item?.status || "").toLowerCase(),
        ),
      );
      if (runningGeneration) {
        const generationState = firstValue(
          runningGeneration.state,
          runningGeneration.status,
          "building",
        );
        setPill(refs.jobState, generationState);
        refs.jobProgress.textContent = "-- / --";
        refs.jobProgressBar.style.width = "34%";
        refs.jobProgressBar.classList.add("is-indeterminate");
        refs.jobProgressBar.removeAttribute("aria-valuenow");
        refs.jobStage.textContent = `后台任务 ${shortId(generationIdentifier(runningGeneration))}`;
        refs.jobError.hidden = true;
        refs.jobError.textContent = "";
        return;
      }
      setPill(refs.jobState, "idle", "空闲");
      refs.jobProgress.textContent = "0 / 0";
      refs.jobProgressBar.style.width = "0%";
      refs.jobProgressBar.classList.remove("is-indeterminate");
      refs.jobProgressBar.setAttribute("aria-valuenow", "0");
      refs.jobStage.textContent = "等待新任务";
      refs.jobError.hidden = true;
      refs.jobError.textContent = "";
      return;
    }

    const total = Number(job.progress_total);
    const current = numberValue(job.progress_current);
    const hasTotal = Number.isFinite(total) && total > 0;
    const percent = hasTotal ? Math.max(0, Math.min(100, (current / total) * 100)) : 0;
    setPill(refs.jobState, job.state);
    refs.jobProgress.textContent = hasTotal
      ? `${formatCount(current)} / ${formatCount(total)}`
      : formatCount(current);
    refs.jobProgressBar.style.width = hasTotal ? `${percent}%` : "34%";
    if (hasTotal) refs.jobProgressBar.setAttribute("aria-valuenow", String(Math.round(percent)));
    else refs.jobProgressBar.removeAttribute("aria-valuenow");
    refs.jobProgressBar.classList.toggle(
      "is-indeterminate",
      !hasTotal && ["pending", "queued", "running"].includes(String(job.state)),
    );
    const lastEvent = state.jobEvents.at(-1);
    refs.jobStage.textContent =
      job.progress_message || lastEvent?.message || `Job ${shortId(job.id)}`;
    refs.jobError.hidden = !job.error_message;
    refs.jobError.textContent = job.error_message || "";
  }

  function renderQuality() {
    const selectedGeneration =
      state.generations.find((item) =>
        ["active", "passed", "failed"].includes(String(item.state || item.status || "")),
      ) || state.activeRelease;
    const report = qualityReportFor(selectedGeneration);
    clear(refs.qualityMetrics);
    if (!report || typeof report !== "object") {
      setPill(refs.qualityState, "idle", "无报告");
      refs.qualityMetrics.append(node("span", "quality-empty", "等待候选版本验收"));
      return;
    }

    const metrics = report.metrics || report.scores || {};
    const passed = firstValue(report.passed, report.clean, report.success);
    setPill(refs.qualityState, passed === false ? "failed" : "passed", passed === false ? "未通过" : "已通过");
    const rows = [
      ["Recall@K", firstValue(metrics.recall_at_k, metrics["Recall@K"], metrics.recall)],
      ["MRR", firstValue(metrics.mrr, metrics.MRR)],
      ["nDCG@K", firstValue(metrics.ndcg_at_k, metrics["nDCG@K"], metrics.ndcg)],
      ["负例拒答", firstValue(metrics.negative_rejection_rate, metrics.refusal_rate)],
      ["冲突识别", firstValue(metrics.conflict_detection_rate, metrics.conflict_rate)],
    ].filter(([, value]) => value !== undefined && value !== null);
    if (!rows.length) {
      refs.qualityMetrics.append(
        node("span", "quality-empty", passed === false ? "关键用例未通过" : "质量门禁已通过"),
      );
      return;
    }
    rows.forEach(([label, value]) => {
      const item = node("span", "quality-metric");
      item.append(node("small", "", label), node("strong", "", formatPercent(value)));
      refs.qualityMetrics.append(item);
    });
  }

  function renderRagasEvaluation() {
    clear(refs.ragasMetrics);
    const run = state.ragasRun || state.ragasRuns[0];
    if (!run) {
      refs.openRagasDetailsButton.disabled = true;
      setPill(refs.ragasState, "idle", "未运行");
      refs.ragasMetrics.append(node("span", "quality-empty", "等待固定评测集运行"));
      refs.ragasSummary.textContent = "规则门禁与 RAGAS Judge 尚无报告";
      refs.ragasSummary.className = "release-note";
      return;
    }
    const statusValue = String(run.status || "").toLowerCase();
    if (["queued", "running"].includes(statusValue)) {
      refs.openRagasDetailsButton.disabled = true;
      setPill(refs.ragasState, statusValue);
      refs.ragasMetrics.append(node("span", "quality-empty", "正在逐题运行真实问答链"));
      refs.ragasSummary.textContent = `Run ${shortId(run.run_id)} · ${formatDate(run.updated_at)}`;
      refs.ragasSummary.className = "release-note";
      return;
    }
    if (statusValue === "error") {
      refs.openRagasDetailsButton.disabled = !run.result;
      setPill(refs.ragasState, "error");
      refs.ragasMetrics.append(node("span", "quality-empty", "评测任务执行失败"));
      refs.ragasSummary.textContent = run.error || run.result?.judge?.error || "请查看逐题错误和服务端日志";
      refs.ragasSummary.className = "release-note is-error";
      return;
    }

    const result = run.result || {};
    refs.openRagasDetailsButton.disabled = !run.run_id;
    setPill(refs.ragasState, statusValue || result.status);
    const metricLabels = {
      context_precision: "上下文精确率",
      context_recall: "上下文召回率",
      faithfulness: "忠实度",
      answer_relevancy: "答案相关性",
    };
    const metrics = result.ragas_gate?.metrics || {};
    Object.entries(metricLabels).forEach(([name, label]) => {
      const metric = metrics[name] || {};
      const score = metric.score;
      const threshold = metric.threshold;
      const className = metric.passed === true
        ? "quality-metric is-passed"
        : metric.passed === false
          ? "quality-metric is-failed"
          : "quality-metric";
      const item = node("span", className);
      item.title = score === null || score === undefined
        ? `${label}：Judge 未运行或不可用`
        : `${label} ${formatPercent(score)}，门槛 ${formatPercent(threshold)}`;
      item.append(
        node("small", "", label),
        node(
          "strong",
          "",
          score === null || score === undefined
            ? "--"
            : `${formatPercent(score)} / ≥${formatPercent(threshold)}`,
        ),
      );
      refs.ragasMetrics.append(item);
    });
    const hard = result.hard_gate || {};
    const failedCases = Array.isArray(result.cases)
      ? result.cases.filter((item) => item?.hard_rules?.passed === false).map((item) => item.id)
      : [];
    const judge = result.judge || {};
    const judgeText = judge.available
      ? `${judge.model || "Judge"} · Ragas ${judge.ragas_version || "--"}`
      : judge.requested
        ? `Judge 不可用${judge.error ? `：${judge.error}` : ""}`
        : "本次仅运行硬规则";
    const topK = firstValue(result.pipeline?.top_k, run.config?.top_k);
    refs.ragasSummary.textContent = `规则 ${formatCount(hard.passed_cases)} / ${formatCount(hard.total_cases)}${topK ? ` · Top-K ${topK}` : ""} · ${judgeText}${failedCases.length ? ` · 失败：${failedCases.join("、")}` : ""}`;
    refs.ragasSummary.className = `release-note ${statusValue === "passed" ? "is-clean" : statusValue === "failed" ? "is-error" : ""}`;
    refs.ragasSummary.title = refs.ragasSummary.textContent;
  }

  function renderRagasDetails() {
    const run = state.ragasRun || state.ragasRuns[0];
    const result = run?.result || {};
    const cases = Array.isArray(result.cases) ? result.cases : [];
    if (!run || !cases.length) return;

    clear(refs.ragasDetailsContent);
    const pipeline = result.pipeline || {};
    const judge = result.judge || {};
    refs.ragasDetailsMeta.textContent = `Run ${shortId(run.run_id)} · ${String(run.status || result.status || "--")} · Top-K ${formatCount(pipeline.top_k)} · ${judge.model || "Judge 未运行"}`;

    const overview = node("section", "ragas-report-overview");
    [
      ["评测集", result.evaluation_set?.sha256 ? String(result.evaluation_set.sha256).slice(0, 12) : "--"],
      ["物理 Collection", pipeline.physical_collection || "--"],
      ["生成模型", pipeline.generation_model || "--"],
      ["Judge", judge.available ? `${judge.model} · Ragas ${judge.ragas_version}` : "未运行"],
    ].forEach(([label, value]) => {
      const item = node("div", "ragas-report-fact");
      item.append(node("small", "", label), node("strong", "", value));
      overview.append(item);
    });
    refs.ragasDetailsContent.append(overview);

    const orderedCases = [...cases].sort((left, right) => {
      const leftFailed = left?.hard_rules?.passed === false ? 0 : 1;
      const rightFailed = right?.hard_rules?.passed === false ? 0 : 1;
      return leftFailed - rightFailed;
    });
    orderedCases.forEach((item) => {
      const hard = item.hard_rules || {};
      const passed = hard.passed !== false;
      const article = node("article", `ragas-case-card ${passed ? "is-passed" : "is-failed"}`);
      const header = node("div", "ragas-case-header");
      header.append(
        node("strong", "", item.id || "未命名 Case"),
        node("span", `status-pill ${passed ? "is-success" : "is-error"}`, passed ? "硬规则通过" : "硬规则失败"),
      );
      article.append(header, node("p", "ragas-case-question", item.user_input || ""));

      const metrics = item.ragas || {};
      const metricLine = [
        ["CP", metrics.context_precision],
        ["CR", metrics.context_recall],
        ["Faith", metrics.faithfulness],
        ["AR", metrics.answer_relevancy],
      ].map(([label, value]) => `${label} ${value === null || value === undefined ? "--" : formatPercent(value)}`).join(" · ");
      article.append(node("p", "ragas-case-metrics", metricLine));

      const failures = [
        ...(hard.missing_phrases || []).map((value) => `缺少：${value}`),
        ...(hard.forbidden_phrases || []).map((value) => `出现禁止内容：${value}`),
        ...(hard.missing_conflict_values || []).map((value) => `冲突证据缺少：${value}`),
      ];
      if (failures.length) article.append(node("p", "ragas-case-failures", failures.join("；")));
      article.append(node("p", "ragas-case-answer", item.response || ""));

      const documents = [...new Set(item.retrieved_documents || [])];
      if (documents.length) article.append(node("p", "ragas-case-sources", `来源：${documents.join("、")}`));
      refs.ragasDetailsContent.append(article);
    });
    refs.ragasDialog.showModal();
  }

  async function openRagasDetails() {
    const run = state.ragasRun || state.ragasRuns[0];
    if (!run?.run_id) return;
    try {
      if (!Array.isArray(run.result?.cases)) {
        state.ragasRun = await request(
          repositoryPath(`/evaluations/ragas/${encodeURIComponent(run.run_id)}`),
        );
      }
      renderRagasDetails();
    } catch (error) {
      setNotice("error", error instanceof Error ? error.message : String(error));
    }
  }

  function generationIdentifier(generation) {
    return firstValue(generation.id, generation.generation_id);
  }

  function renderGenerations() {
    clear(refs.generationList);
    refs.releaseCount.textContent = formatCount(state.generations.length);
    if (!state.generations.length) {
      refs.generationList.append(
        emptyState("layers", "还没有 Generation", "从当前资料目录构建第一个候选版本。", true),
      );
      return;
    }

    state.generations.slice(0, 8).forEach((generation) => {
      const identifier = generationIdentifier(generation);
      const generationState = firstValue(generation.state, generation.status, "unknown");
      const row = node("article", "generation-row");
      const copy = node("div", "generation-copy");
      const title = node("div", "generation-title");
      const name = node("strong", "", `gen ${shortId(identifier)}`);
      name.title = identifier || "";
      const pill = node("span", `status-pill ${releaseStateTone(generationState)}`, releaseStateLabel(generationState));
      title.append(name, pill);
      const collection = firstValue(generation.physical_collection, generation.collection, "collection 待定");
      const manifest = generation.manifest || {};
      const manifestSummary =
        manifest.document_count !== undefined || manifest.chunk_count !== undefined
          ? `${formatCount(manifest.document_count)} 份 / ${formatCount(manifest.chunk_count)} 切片`
          : "";
      const meta = node(
        "div",
        "generation-meta",
        [
          collection,
          manifestSummary,
          formatDate(firstValue(generation.updated_at, generation.created_at)),
        ]
          .filter(Boolean)
          .join(" · "),
      );
      meta.title = collection;
      copy.append(title, meta);
      row.append(copy);

      const actions = node("div", "generation-actions");
      if (String(generationState) === "passed") {
        const promote = node("button", "icon-button mini");
        promote.type = "button";
        promote.title = "发布这个 Generation";
        promote.setAttribute("aria-label", "发布这个 Generation");
        promote.disabled = isBusy(`promote:${identifier}`);
        promote.append(icon(isBusy(`promote:${identifier}`) ? "loader" : "rocket"));
        promote.classList.toggle("is-spinning", isBusy(`promote:${identifier}`));
        promote.addEventListener("click", () => promoteRelease(identifier));
        actions.append(promote);
      } else if (String(generationState) === "archived") {
        const rollback = node("button", "icon-button mini");
        rollback.type = "button";
        rollback.title = "回滚到这个 Generation";
        rollback.setAttribute("aria-label", "回滚到这个 Generation");
        rollback.disabled = isBusy(`rollback:${identifier}`);
        rollback.append(icon(isBusy(`rollback:${identifier}`) ? "loader" : "rotate-ccw"));
        rollback.classList.toggle("is-spinning", isBusy(`rollback:${identifier}`));
        rollback.addEventListener("click", () => rollbackRelease(identifier));
        actions.append(rollback);
      } else if (String(generationState) === "active") {
        actions.append(icon("activity", "is-serving"));
      }
      if (actions.childNodes.length) row.append(actions);
      refs.generationList.append(row);
    });
  }

  function renderReconcile() {
    const report = state.reconcileReport;
    if (!report) {
      refs.reconcileSummary.textContent = "尚未执行对账";
      refs.reconcileSummary.className = "release-note";
      return;
    }
    const missing = firstValue(
      report.missing_chunks,
      report.missing_in_vector,
      report.missing,
      [],
    );
    const orphan = firstValue(
      report.orphan_chunks,
      report.orphan_in_vector,
      report.orphan,
      [],
    );
    const mismatch = firstValue(
      report.hash_mismatches,
      report.hash_mismatch,
      report.mismatched,
      [],
    );
    const issueCount = [missing, orphan, mismatch].reduce(
      (total, items) => total + (Array.isArray(items) ? items.length : numberValue(items)),
      0,
    );
    const clean = firstValue(report.clean, report.passed, issueCount === 0);
    refs.reconcileSummary.textContent = clean
      ? "目录与 Serving 索引一致"
      : issueCount > 0
        ? `发现 ${formatCount(issueCount)} 项不一致`
        : report.detail || "对账未通过";
    refs.reconcileSummary.className = `release-note ${clean ? "is-clean" : "is-error"}`;
  }

  function renderRelease() {
    const normalized = normalizeReleasePayload(state.releasePayload);
    state.generations = normalized.generations;
    state.activeRelease = normalized.active;
    state.pendingCount = normalized.pendingCount;
    if (
      normalized.currentJob &&
      (!state.currentJob || normalized.currentJob.id === state.currentJob.id)
    ) {
      state.currentJob = normalized.currentJob;
    }

    const active = state.activeRelease;
    refs.releaseAlias.textContent = active?.alias_name || state.releasePayload?.alias_name || "--";
    refs.releaseCollection.textContent = active?.physical_collection || "--";
    refs.releaseAlias.title = refs.releaseAlias.textContent;
    refs.releaseCollection.title = refs.releaseCollection.textContent;
    refs.pendingCount.textContent = formatCount(state.pendingCount);
    const changes = normalized.pendingChanges;
    const changeParts = [
      numberValue(firstValue(changes.added, changes.created))
        ? `新增 ${formatCount(firstValue(changes.added, changes.created))}`
        : "",
      numberValue(firstValue(changes.updated, changes.changed))
        ? `更新 ${formatCount(firstValue(changes.updated, changes.changed))}`
        : "",
      numberValue(firstValue(changes.deleted, changes.removed))
        ? `删除 ${formatCount(firstValue(changes.deleted, changes.removed))}`
        : "",
    ].filter(Boolean);
    refs.pendingDetail.textContent = changeParts.length ? ` · ${changeParts.join(" / ")}` : "";
    refs.pendingDocumentBadge.hidden = state.pendingCount === 0;
    refs.mobileReleaseCount.hidden = state.pendingCount === 0;
    refs.mobileReleaseCount.textContent = formatCount(state.pendingCount);

    if (active) {
      setPill(refs.releaseStateBadge, "active", "Serving");
      refs.releaseSummary.textContent = `${shortId(active.generation_id)} · ${formatCount(state.pendingCount)} 份待发布`;
    } else {
      setPill(refs.releaseStateBadge, "idle", "未发布");
      refs.releaseSummary.textContent = `${formatCount(state.pendingCount)} 份资料等待首个版本`;
    }
    renderJob();
    renderQuality();
    renderRagasEvaluation();
    renderGenerations();
    renderReconcile();
    renderControls();
  }

  function documentState(documentItem) {
    const value = String(documentItem.status || "ready").toLowerCase();
    if (["ready", "indexed", "completed", "success"].includes(value)) {
      return { label: "已发布", className: "", canOpen: true, pending: false };
    }
    if (["pending_delete", "deleted", "tombstone"].includes(value)) {
      return { label: "待删除", className: "is-pending", canOpen: false, pending: true };
    }
    if (["pending", "staged", "pending_create", "pending_update", "draft"].includes(value)) {
      return {
        label: value === "pending_update" ? "待更新" : "待发布",
        className: "is-pending",
        canOpen: Boolean(numberValue(documentItem.chunk_count)),
        pending: true,
      };
    }
    if (["failed", "error"].includes(value)) {
      return { label: "失败", className: "is-error", canOpen: false, pending: false };
    }
    return { label: "处理中", className: "is-processing", canOpen: false, pending: true };
  }

  function renderDocuments() {
    clear(refs.documentList);
    const documents = Array.isArray(state.documents) ? state.documents : [];
    const chunks =
      state.status?.chunk_count ??
      documents.reduce((total, item) => total + numberValue(item.chunk_count), 0);
    const pending = Math.max(
      state.pendingCount,
      documents.filter((item) => documentState(item).pending).length,
    );
    refs.documentSummary.textContent = `${formatCount(documents.length)} 份资料 · ${formatCount(chunks)} 个 Serving 切片${pending ? ` · ${formatCount(pending)} 待发布` : ""}`;

    if (state.loadingDocuments) {
      const loading = node("div", "loading-list");
      loading.setAttribute("aria-label", "正在读取文档");
      loading.append(node("span"), node("span"), node("span"));
      refs.documentList.append(loading);
      return;
    }

    if (!documents.length) {
      refs.documentList.append(
        emptyState(
          "file-text",
          "资料库还是空的",
          "上传自己的文件，或先导入示例资料体验完整流程。",
          true,
        ),
      );
      return;
    }

    documents.forEach((documentItem) => {
      const currentState = documentState(documentItem);
      const row = node("article", "document-row");
      const openButton = node("button", "document-open");
      openButton.type = "button";
      openButton.disabled = !currentState.canOpen || !numberValue(documentItem.chunk_count);
      openButton.title = currentState.canOpen ? "查看文档切片" : currentState.label;

      const fileIcon = node("span", "document-file-icon");
      fileIcon.append(icon("file-text"));
      const copy = node("span", "document-copy");
      const titleLine = node("span", "document-title-line");
      const title = node("span", "document-title", documentItem.name || "未命名资料");
      title.title = documentItem.name || "未命名资料";
      const statusPill = node(
        "span",
        `status-pill${currentState.className ? ` ${currentState.className}` : ""}`,
        currentState.label,
      );
      titleLine.append(title, statusPill);

      const metaParts = [
        formatBytes(documentItem.file_size),
        documentItem.character_count
          ? `${formatCount(documentItem.character_count)} 字`
          : "",
        `${formatCount(documentItem.chunk_count)} 切片`,
        formatDate(documentItem.created_at),
      ].filter(Boolean);
      copy.append(titleLine, node("span", "document-meta", metaParts.join(" · ")));
      if (documentItem.error_message) {
        const error = node("span", "document-error", documentItem.error_message);
        error.title = documentItem.error_message;
        copy.append(error);
      }
      openButton.append(fileIcon, copy);
      openButton.addEventListener("click", () => openChunks(documentItem));

      const deleteButton = node("button", "document-delete");
      deleteButton.type = "button";
      const pendingDelete = ["pending_delete", "deleted", "tombstone"].includes(
        String(documentItem.status || "").toLowerCase(),
      );
      deleteButton.title = pendingDelete ? "已加入待删除变更" : "加入待删除变更";
      deleteButton.setAttribute(
        "aria-label",
        pendingDelete ? `${documentItem.name || "资料"} 已待删除` : `删除 ${documentItem.name || "资料"}`,
      );
      deleteButton.disabled = pendingDelete || isBusy(`delete:${documentItem.id}`);
      deleteButton.append(icon("trash-2"));
      deleteButton.addEventListener("click", () => deleteDocument(documentItem));

      row.append(openButton, deleteButton);
      refs.documentList.append(row);
    });
  }

  function emptyState(iconName, title, description, compact = false) {
    const wrapper = node("div", `empty-state${compact ? " compact" : ""}`);
    const iconBox = node("span", "empty-icon");
    iconBox.append(icon(iconName));
    wrapper.append(iconBox, node("h3", "", title), node("p", "", description));
    return wrapper;
  }

  function renderEmptyChat() {
    const hasDocuments = state.documents.length > 0;
    const hasServing = Boolean(state.activeRelease);
    const wrapper = emptyState(
      hasServing ? "sparkles" : hasDocuments ? "rocket" : "book-open",
      hasServing ? "从 Serving 版本里问一个具体问题" : hasDocuments ? "先发布首个版本" : "先准备一份资料",
      hasServing
        ? "工作室会从当前 Alias 召回证据，再让生成模型基于这些证据回答。"
        : hasDocuments
          ? "资料已经进入待发布区，请先在“发布”工作区构建并通过质量门禁。"
          : "在“资料”页上传文件或导入示例资料，再构建第一个 Generation。",
    );

    if (hasServing && state.sampleQuestions.length) {
      const questions = node("div", "sample-questions");
      state.sampleQuestions.slice(0, 4).forEach((question) => {
        const button = node("button", "sample-question", question);
        button.type = "button";
        button.addEventListener("click", () => {
          refs.questionInput.value = question;
          resizeQuestionInput();
          renderControls();
          refs.questionInput.focus();
        });
        questions.append(button);
      });
      wrapper.append(questions);
    }
    return wrapper;
  }

  function renderMessages() {
    clear(refs.messages);
    refs.clearChatButton.hidden = state.messages.length === 0;

    if (!state.messages.length) {
      refs.messages.append(renderEmptyChat());
    } else {
      state.messages.forEach((message) => {
        const row = node(
          "article",
          `message-row${message.role === "user" ? " is-user" : ""}`,
        );
        row.append(node("div", "message-label", message.role === "user" ? "你" : "RAG"));
        const bubble = node("div", "message-bubble");
        if (message.role === "assistant") {
          const content = node("div", "answer-body");
          appendRichText(content, message.content, message.sources || []);
          bubble.append(content);
        } else {
          bubble.textContent = message.content;
        }
        row.append(bubble);

        if (message.role === "assistant") {
          const meta = node("div", "message-meta");
          meta.append(
            node("span", "", generationModeLabel(message.generationMode)),
            node("span", "", `${numberValue(message.sourceCount)} 条证据`),
          );
          if (Number.isFinite(Number(message.tookMs))) {
            meta.append(node("span", "", `${numberValue(message.tookMs)} ms`));
          }
          if (message.sources?.length) {
            const evidenceButton = node("button", "", "查看本轮证据");
            evidenceButton.type = "button";
            evidenceButton.addEventListener("click", () => {
              state.sources = message.sources;
              state.evidenceSummary = "本轮检索证据";
              renderEvidence();
              setActivePanel("evidence");
            });
            meta.append(evidenceButton);
          }
          row.append(meta);
        }
        refs.messages.append(row);
      });
    }

    if (isBusy("ask")) {
      const thinking = node("div", "thinking");
      thinking.append(icon("loader"), node("span", "", "正在检索相关切片并组织回答"));
      refs.messages.append(thinking);
    }

    window.requestAnimationFrame(() => {
      refs.messages.scrollTop = refs.messages.scrollHeight;
    });
  }

  function appendRichText(container, content, sources) {
    const lines = String(content || "").replace(/\r\n?/g, "\n").split("\n");
    let list = null;
    let listType = "";
    let inCode = false;
    let codeLines = [];

    const flushList = () => {
      if (list) container.append(list);
      list = null;
      listType = "";
    };

    const flushCode = () => {
      const pre = node("pre");
      pre.append(node("code", "", codeLines.join("\n")));
      container.append(pre);
      codeLines = [];
    };

    lines.forEach((line) => {
      if (/^\s*```/.test(line)) {
        flushList();
        if (inCode) flushCode();
        inCode = !inCode;
        return;
      }
      if (inCode) {
        codeLines.push(line);
        return;
      }
      if (!line.trim()) {
        flushList();
        return;
      }

      const heading = line.match(/^\s*(#{1,3})\s+(.+)$/);
      if (heading) {
        flushList();
        const headingNode = node(heading[1].length === 1 ? "h3" : "h4");
        appendInline(headingNode, heading[2], sources);
        container.append(headingNode);
        return;
      }

      const unordered = line.match(/^\s*[-*]\s+(.+)$/);
      const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
      if (unordered || ordered) {
        const type = ordered ? "ol" : "ul";
        if (!list || listType !== type) {
          flushList();
          list = node(type);
          listType = type;
        }
        const item = node("li");
        appendInline(item, (unordered || ordered)[1], sources);
        list.append(item);
        return;
      }

      flushList();
      const paragraph = node("p");
      appendInline(paragraph, line, sources);
      container.append(paragraph);
    });

    flushList();
    if (inCode || codeLines.length) flushCode();
  }

  function appendInline(container, text, sources) {
    const pattern = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[\d+\]|【\d+】)/g;
    let cursor = 0;
    for (const match of String(text).matchAll(pattern)) {
      if (match.index > cursor) {
        container.append(document.createTextNode(text.slice(cursor, match.index)));
      }
      const token = match[0];
      if (token.startsWith("**")) {
        container.append(node("strong", "", token.slice(2, -2)));
      } else if (token.startsWith("`")) {
        container.append(node("code", "", token.slice(1, -1)));
      } else {
        const citation = numberValue(token.replace(/\D/g, ""), -1);
        const matchingSource = sources.find(
          (source, index) => numberValue(source.citation, index + 1) === citation,
        );
        if (matchingSource) {
          const button = node("button", "citation-link", String(citation));
          button.type = "button";
          button.title = `查看引用 ${citation}`;
          button.setAttribute("aria-label", `查看引用 ${citation}`);
          button.addEventListener("click", () => {
            state.sources = sources;
            state.evidenceSummary = "本轮回答引用";
            renderEvidence();
            focusEvidence(citation);
          });
          container.append(button);
        } else {
          container.append(document.createTextNode(token));
        }
      }
      cursor = match.index + token.length;
    }
    if (cursor < text.length) {
      container.append(document.createTextNode(text.slice(cursor)));
    }
  }

  function generationModeLabel(mode) {
    if (mode === "llm") return "LLM 生成";
    if (mode === "extractive") return "本地证据摘录";
    if (mode === "no_evidence") return "无可用证据";
    if (mode === "investigation") return "证据调查";
    return mode || "已生成";
  }

  function compactScore(value, digits = 4) {
    const score = Number(value);
    return Number.isFinite(score) ? score.toFixed(digits) : null;
  }

  function traceChip(label, value, tone = "") {
    if (value === null || value === undefined || value === "") return null;
    return node("span", `trace-chip${tone ? ` ${tone}` : ""}`, `${label} ${value}`);
  }

  function appendTrace(article, source) {
    const trace = source.retrieval || {};
    const chips = node("div", "trace-chips");
    const routes = Array.isArray(trace.sources) ? trace.sources : [];
    routes.forEach((route) => {
      chips.append(traceChip(String(route).toUpperCase(), "命中", `is-${route}`));
    });
    const values = [
      traceChip("Dense", trace.dense_rank ? `#${trace.dense_rank}` : null),
      traceChip("cos", compactScore(trace.dense_score)),
      traceChip("BM25", trace.bm25_rank ? `#${trace.bm25_rank}` : null),
      traceChip("score", compactScore(trace.bm25_score, 3)),
      traceChip("RRF", compactScore(trace.fusion_score, 5)),
      traceChip("Rerank", compactScore(trace.rerank_score, 4), "is-rerank"),
    ].filter(Boolean);
    chips.append(...values);
    if (chips.childElementCount) article.append(chips);

    const compression = trace.compression || {};
    const details = [];
    if (trace.rerank_mode === "fallback_hybrid") details.push("Rerank 已降级，保留 Hybrid 排名");
    else if (trace.rerank_mode === "qwen_api") details.push(`Qwen Rerank${trace.rerank_model ? ` · ${trace.rerank_model}` : ""}`);
    else if (trace.rerank_mode === "heuristic_baseline") details.push("启发式重排基线");
    else if (trace.rerank_mode === "disabled") details.push("Rerank 已关闭");
    const expansion = trace.parent_expansion || {};
    if (expansion.applied) {
      details.push(`父级扩展 +${expansion.added_siblings} 个同章节切片`);
    } else if (expansion.fallback) {
      details.push("父级扩展已降级");
    }
    if (compression.applied) {
      details.push(`上下文压缩 ${compression.original_chars} → ${compression.context_chars} 字`);
    }
    if (details.length) article.append(node("p", "trace-note", details.join(" · ")));
  }

  function investigationDecision(value) {
    if (value === "evidence_conflict") {
      return { label: "证据冲突", tone: "is-error" };
    }
    if (value === "insufficient_evidence") {
      return { label: "证据不足", tone: "is-processing" };
    }
    if (value === "ready_for_grounded_answer") {
      return { label: "证据就绪", tone: "is-success" };
    }
    return { label: value || "调查完成", tone: "is-muted" };
  }

  function renderInvestigationTrace() {
    const investigation = state.investigation;
    if (!investigation || typeof investigation !== "object") {
      refs.investigationTrace.hidden = true;
      clear(refs.investigationTrace);
      return;
    }

    const trace = Array.isArray(investigation.trace) ? investigation.trace : [];
    const budgets = investigation.budgets || {};
    const conflict = investigation.conflict || {};
    const decision = investigationDecision(investigation.decision);
    refs.investigationTrace.hidden = false;
    clear(refs.investigationTrace);

    const header = node("div", "investigation-heading");
    const headingCopy = node("div", "investigation-heading-copy");
    headingCopy.append(
      node("strong", "", "Investigation Trace"),
      node(
        "span",
        "",
        [
          investigation.contract,
          `${formatCount(firstValue(budgets.queries_used, trace.length))}/${formatCount(budgets.max_queries)} 次查询`,
          `${formatCount(investigation.took_ms)} ms`,
        ]
          .filter(Boolean)
          .join(" · "),
      ),
    );
    header.append(
      headingCopy,
      node("span", `status-pill ${decision.tone}`, decision.label),
    );
    refs.investigationTrace.append(header);

    if (conflict.detected) {
      const conflictBox = node("div", "investigation-conflict");
      const values = Array.isArray(conflict.values) ? conflict.values : [];
      conflictBox.append(
        icon("shield-alert"),
        node(
          "p",
          "",
          [
            values.length ? `冲突值：${values.join(" / ")}` : "发现互相冲突的证据",
            conflict.action,
          ]
            .filter(Boolean)
            .join("。"),
        ),
      );
      refs.investigationTrace.append(conflictBox);
    }

    const rounds = node("ol", "investigation-rounds");
    trace.forEach((item, index) => {
      const row = node("li", "investigation-round");
      const roundIndex = firstValue(item.round, index + 1);
      const newEvidence = Array.isArray(item.new_evidence) ? item.new_evidence.length : 0;
      const missing = Array.isArray(item.missing_slots) ? item.missing_slots : [];
      const roundHead = node("div", "investigation-round-head");
      roundHead.append(
        node("strong", "", `Round ${roundIndex}`),
        node(
          "span",
          "",
          `新增 ${formatCount(newEvidence)}${missing.length ? ` · 缺 ${missing.join("、")}` : " · 合同已满足"}`,
        ),
      );
      row.append(roundHead, node("p", "", item.query || "未记录查询"));
      rounds.append(row);
    });
    if (rounds.childElementCount) refs.investigationTrace.append(rounds);

    const stopReason = String(investigation.stop_reason || "");
    if (stopReason) {
      const labels = {
        evidence_contract_satisfied: "证据合同已满足",
        no_new_evidence: "连续检索没有新增证据",
        query_budget_reached: "查询预算已用完",
      };
      refs.investigationTrace.append(
        node("p", "investigation-stop", `停止条件：${labels[stopReason] || stopReason}`),
      );
    }
  }

  function renderEvidence() {
    renderInvestigationTrace();
    clear(refs.evidenceList);
    const sources = Array.isArray(state.sources) ? state.sources : [];
    refs.evidenceCount.textContent = String(sources.length);
    refs.evidenceSummary.textContent = state.evidenceSummary || "等待检索";
    refs.mobileEvidenceCount.textContent = String(sources.length);
    refs.mobileEvidenceCount.hidden = sources.length === 0;

    if (!sources.length) {
      refs.evidenceList.append(
        emptyState(
          "search",
          state.investigation ? "调查没有找到完整证据" : "还没有检索证据",
          state.investigation
            ? "查看上方调查轮次、缺失槽位与停止条件。"
            : "输入问题后点放大镜只看召回结果，或直接提问。",
          true,
        ),
      );
      return;
    }

    sources.forEach((source, index) => {
      const citation = numberValue(source.citation, index + 1);
      const trace = source.retrieval || {};
      const article = node("article", "evidence-item");
      article.id = `evidence-${citation}`;

      const heading = node("div", "evidence-heading");
      heading.append(node("span", "citation-badge", String(citation)));
      const titleWrap = node("div", "evidence-title-wrap");
      titleWrap.append(
        node(
          "h3",
          "evidence-title",
          source.title || source.filename || `证据 ${citation}`,
        ),
      );

      const chunkIndex = Number(source.chunk_index);
      const locationParts = [
        source.evidence_slot ? `slot ${source.evidence_slot}` : "",
        source.filename,
        Number.isFinite(chunkIndex) ? `切片 #${chunkIndex}` : "",
        Array.isArray(source.metadata?.section_path)
          ? source.metadata.section_path.join(" › ")
          : "",
      ].filter(Boolean);
      const meta = node("div", "evidence-meta", locationParts.join(" · ") || "知识库切片");
      meta.title = locationParts.join(" · ");
      titleWrap.append(meta);
      heading.append(titleWrap);
      const primaryScore =
        trace.rerank_score !== null && trace.rerank_score !== undefined
          ? `Rerank ${compactScore(trace.rerank_score, 3)}`
          : trace.fusion_score !== null && trace.fusion_score !== undefined
            ? `RRF ${compactScore(trace.fusion_score, 4)}`
            : trace.dense_score !== null && trace.dense_score !== undefined
              ? `Cos ${compactScore(trace.dense_score, 3)}`
              : compactScore(source.score, 3);
      if (primaryScore) heading.append(node("span", "score", primaryScore));
      article.append(heading);
      appendTrace(article, source);
      article.append(node("p", "evidence-snippet", source.snippet || "该切片没有可显示的文本。"));
      refs.evidenceList.append(article);
    });
  }

  function focusEvidence(citation) {
    setActivePanel("evidence");
    window.requestAnimationFrame(() => {
      const target = document.getElementById(`evidence-${citation}`);
      if (!target) return;
      document.querySelectorAll(".evidence-item.is-highlighted").forEach((item) => {
        item.classList.remove("is-highlighted");
      });
      target.classList.add("is-highlighted");
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      window.setTimeout(() => target.classList.remove("is-highlighted"), 1800);
    });
  }

  function resizeQuestionInput() {
    refs.questionInput.style.height = "auto";
    refs.questionInput.style.height = `${Math.min(refs.questionInput.scrollHeight, 160)}px`;
  }

  function setActivePanel(name) {
    document.querySelectorAll("[data-panel]").forEach((panel) => {
      panel.classList.toggle("is-mobile-active", panel.dataset.panel === name);
    });
    document.querySelectorAll("[data-panel-target]").forEach((button) => {
      const active = button.dataset.panelTarget === name;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-current", active ? "page" : "false");
    });
  }

  async function loadCore({ silent = false } = {}) {
    if (!silent) setServiceState("loading");
    setBusy("refresh", true);
    const [statusResult, documentsResult, releasesResult, ragasResult] = await Promise.allSettled([
      request(repositoryPath("/status")),
      request(repositoryPath("/documents")),
      request(repositoryPath("/releases")),
      request(repositoryPath("/evaluations/ragas?limit=10")),
    ]);

    const errors = [];
    if (statusResult.status === "fulfilled") {
      state.status = statusResult.value || {};
      renderStatus();
      setServiceState("online");
    } else {
      errors.push(statusResult.reason);
      setServiceState("offline");
    }

    if (documentsResult.status === "fulfilled") {
      state.documents = Array.isArray(documentsResult.value) ? documentsResult.value : [];
    } else {
      errors.push(documentsResult.reason);
    }

    if (releasesResult.status === "fulfilled") {
      state.releasePayload = releasesResult.value;
    } else {
      errors.push(releasesResult.reason);
    }

    if (ragasResult.status === "fulfilled") {
      state.ragasRuns = Array.isArray(ragasResult.value) ? ragasResult.value : [];
      if (!state.ragasRun || !["queued", "running"].includes(String(state.ragasRun.status))) {
        state.ragasRun = state.ragasRuns[0] || null;
      }
    } else {
      errors.push(ragasResult.reason);
    }

    state.loadingDocuments = false;
    renderRelease();
    renderDocuments();
    renderMessages();
    setBusy("refresh", false);
    scheduleDocumentPoll();

    if (errors.length) {
      setNotice(
        "error",
        errors
          .map((error) => (error instanceof Error ? error.message : String(error)))
          .join("；"),
      );
    }
    return errors.length === 0;
  }

  async function loadSampleQuestions() {
    try {
      const payload = await request("/api/sample-questions");
      state.sampleQuestions = Array.isArray(payload?.questions) ? payload.questions : [];
      if (!state.messages.length) renderMessages();
    } catch (error) {
      state.sampleQuestions = [];
      console.warn("读取示例问题失败", error);
    }
  }

  function scheduleDocumentPoll() {
    if (state.pollTimer) {
      window.clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
    const hasPending = state.documents.some(
      (item) => documentState(item).className === "is-processing",
    );
    const hasReleaseBuilding = state.generations.some((item) =>
      ["building", "validating"].includes(
        String(item?.state || item?.status || "").toLowerCase(),
      ),
    );
    if (hasPending || hasReleaseBuilding) {
      state.pollTimer = window.setTimeout(() => loadCore({ silent: true }), 1800);
    }
  }

  async function uploadFiles(fileList) {
    const files = Array.from(fileList || []).filter((file) => file instanceof File);
    if (!files.length || isBusy("upload")) return;

    setBusy("upload", true);
    setNotice("info", `准备导入 ${files.length} 个文件`);
    const failures = [];
    let completed = 0;

    try {
      for (const file of files) {
        refs.uploadTitle.textContent = `正在导入 ${completed + 1}/${files.length}`;
        refs.uploadHint.textContent = file.name;
        const form = new FormData();
        form.append("file", file);
        try {
          await request(repositoryPath("/documents"), { method: "POST", body: form });
          completed += 1;
        } catch (error) {
          failures.push(
            `${file.name}：${error instanceof Error ? error.message : String(error)}`,
          );
        }
      }
      await loadCore({ silent: true });
      if (failures.length) {
        setNotice("error", failures.join("；"));
      } else {
        setNotice(
          "success",
          `已加入 ${completed} 个文件；构建并验收新版本后才会进入 Serving`,
        );
      }
    } finally {
      refs.fileInput.value = "";
      setBusy("upload", false);
    }
  }

  async function seedDocuments() {
    if (isBusy("seed")) return;
    setBusy("seed", true);
    try {
      await request(repositoryPath("/seed"), { method: "POST" });
      await loadCore({ silent: true });
      await loadSampleQuestions();
      setNotice("success", "示例资料已加入待发布区；构建并验收后才会进入 Serving");
    } catch (error) {
      setNotice("error", error instanceof Error ? error.message : String(error));
    } finally {
      setBusy("seed", false);
    }
  }

  async function deleteDocument(documentItem) {
    if (
      isBusy(`delete:${documentItem.id}`) ||
      !window.confirm(
        `将“${documentItem.name || "这份资料"}”移出资料目录并加入待发布变更？当前 Serving 版本在下一次发布前不受影响。`,
      )
    ) {
      return;
    }
    setBusy(`delete:${documentItem.id}`, true);
    renderDocuments();
    try {
      await request(repositoryPath(`/documents/${encodeURIComponent(documentItem.id)}`), {
        method: "DELETE",
      });
      await loadCore({ silent: true });
      setNotice("success", "已加入待删除变更；发布新版本后生效");
    } catch (error) {
      setNotice("error", error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(`delete:${documentItem.id}`, false);
      renderDocuments();
    }
  }

  async function openChunks(documentItem) {
    const requestId = ++state.chunkRequestId;
    refs.chunksTitle.textContent = documentItem.name || "文档切片";
    refs.chunksMeta.textContent = "正在读取切片";
    clear(refs.chunksContent);
    const loading = node("div", "loading-list");
    loading.append(node("span"), node("span"));
    refs.chunksContent.append(loading);
    if (!refs.chunksDialog.open) refs.chunksDialog.showModal();

    try {
      const payload = await request(
        repositoryPath(`/documents/${encodeURIComponent(documentItem.id)}/chunks`),
      );
      if (requestId !== state.chunkRequestId || !refs.chunksDialog.open) return;
      const chunks = Array.isArray(payload) ? payload : [];
      refs.chunksMeta.textContent = `${formatCount(chunks.length)} 个切片 · ${formatCount(documentItem.character_count)} 字`;
      clear(refs.chunksContent);
      if (!chunks.length) {
        refs.chunksContent.append(
          emptyState("layers", "没有可显示的切片", "可以构建并发布新版本后再查看。", true),
        );
        return;
      }
      chunks.forEach((chunk, index) => {
        const row = node("article", "chunk-row");
        const label = node("div", "chunk-label");
        const metadata = chunk.metadata || {};
        const sectionPath = Array.isArray(metadata.section_path)
          ? metadata.section_path.join(" › ")
          : "";
        const location = metadata.start_line
          ? `L${metadata.start_line}-${metadata.end_line || metadata.start_line}`
          : "";
        label.append(
          node("span", "", [sectionPath, `切片 #${index + 1}`].filter(Boolean).join(" · ")),
          node("span", "", [metadata.chunk_type, location, `${formatCount(chunk.character_count)} 字`].filter(Boolean).join(" · ")),
        );
        row.append(label, node("p", "chunk-content", chunk.content || ""));
        refs.chunksContent.append(row);
      });
    } catch (error) {
      if (requestId !== state.chunkRequestId || !refs.chunksDialog.open) return;
      refs.chunksMeta.textContent = "读取失败";
      clear(refs.chunksContent);
      refs.chunksContent.append(
        emptyState(
          "circle-alert",
          "无法读取切片",
          error instanceof Error ? error.message : String(error),
          true,
        ),
      );
      setNotice("error", error instanceof Error ? error.message : String(error));
    }
  }

  async function askQuestion() {
    const question = refs.questionInput.value.trim();
    if (!question || isBusy("ask", "search")) {
      refs.questionInput.focus();
      return;
    }

    state.messages.push({ id: makeId(), role: "user", content: question });
    state.investigation = null;
    refs.questionInput.value = "";
    resizeQuestionInput();
    setBusy("ask", true);
    renderMessages();

    try {
      const result = await request(repositoryPath("/ask"), {
        method: "POST",
        body: { question, top_k: state.topK, mode: state.retrievalMode },
      });
      const sources = Array.isArray(result?.sources) ? result.sources : [];
      state.sources = sources;
      state.retrievalTrace = result?.retrieval || null;
      state.evidenceSummary = `${formatCount(
        result?.retrieval_count ?? sources.length,
      )} 条证据 · ${retrievalSummary(state.retrievalTrace)} · ${formatCount(result?.took_ms)} ms`;
      state.messages.push({
        id: makeId(),
        role: "assistant",
        content: result?.answer || "没有生成可显示的回答。",
        sources,
        sourceCount: result?.retrieval_count ?? sources.length,
        generationMode: result?.generation_mode,
        model: result?.model,
        tookMs: result?.took_ms,
      });
      renderEvidence();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      state.sources = [];
      state.retrievalTrace = null;
      state.evidenceSummary = "本轮请求失败";
      state.messages.push({
        id: makeId(),
        role: "assistant",
        content: `这次请求没有成功：${message}`,
        sources: [],
        sourceCount: 0,
        generationMode: "no_evidence",
      });
      renderEvidence();
      setNotice("error", message);
    } finally {
      setBusy("ask", false);
      renderMessages();
      refs.questionInput.focus();
    }
  }

  async function searchEvidence() {
    const query = refs.questionInput.value.trim();
    if (!query || isBusy("ask", "search")) {
      refs.questionInput.focus();
      return;
    }

    setBusy("search", true);
    state.investigation = null;
    refs.searchButton.classList.add("is-spinning");
    try {
      const result = await request(repositoryPath("/search"), {
        method: "POST",
        body: { query, top_k: state.topK, mode: state.retrievalMode },
      });
      state.sources = Array.isArray(result?.results) ? result.results : [];
      state.retrievalTrace = result?.retrieval || null;
      state.evidenceSummary = `${formatCount(
        result?.result_count ?? state.sources.length,
      )} 条结果 · ${retrievalSummary(state.retrievalTrace)} · ${formatCount(result?.took_ms)} ms`;
      renderEvidence();
      setNotice(
        "info",
        `召回 ${formatCount(result?.result_count ?? state.sources.length)} 条证据，耗时 ${formatCount(result?.took_ms)} ms`,
      );
      setActivePanel("evidence");
    } catch (error) {
      state.sources = [];
      state.retrievalTrace = null;
      state.evidenceSummary = "本轮检索失败";
      renderEvidence();
      setNotice("error", error instanceof Error ? error.message : String(error));
    } finally {
      refs.searchButton.classList.remove("is-spinning");
      setBusy("search", false);
    }
  }

  function scheduleReleasePoll(jobId, delay = 1200) {
    if (state.releasePollTimer) {
      window.clearTimeout(state.releasePollTimer);
      state.releasePollTimer = null;
    }
    if (!jobId) return;
    state.releasePollTimer = window.setTimeout(() => pollReleaseJob(jobId), delay);
  }

  function scheduleRagasPoll(runId, delay = 1200) {
    if (state.ragasPollTimer) {
      window.clearTimeout(state.ragasPollTimer);
      state.ragasPollTimer = null;
    }
    if (!runId) return;
    state.ragasPollTimer = window.setTimeout(() => pollRagasRun(runId), delay);
  }

  async function pollRagasRun(runId) {
    if (!runId || (state.ragasRun?.run_id && state.ragasRun.run_id !== runId)) return;
    try {
      state.ragasRun = await request(
        repositoryPath(`/evaluations/ragas/${encodeURIComponent(runId)}`),
      );
      renderRagasEvaluation();
      renderControls();
      const statusValue = String(state.ragasRun?.status || "").toLowerCase();
      if (["queued", "running"].includes(statusValue)) {
        scheduleRagasPoll(runId);
        return;
      }
      scheduleRagasPoll(null);
      const result = state.ragasRun?.result || {};
      if (statusValue === "passed") {
        setNotice("success", "答案质量评测通过：硬规则与四项 RAGAS 指标均达到门槛");
      } else if (statusValue === "partial") {
        setNotice("info", "硬规则已完成；RAGAS Judge 未启用或不可用，本次结果不能当作完整通过");
      } else {
        setNotice("error", state.ragasRun?.error || `答案质量评测未通过（${result.hard_gate?.passed_cases || 0}/${result.hard_gate?.total_cases || 0}）`);
      }
    } catch (error) {
      scheduleRagasPoll(runId, 2200);
      setNotice("error", error instanceof Error ? error.message : String(error));
    }
  }

  async function runRagasEvaluation() {
    if (isBusy("run-ragas") || !state.activeRelease) return;
    setBusy("run-ragas", true);
    try {
      state.ragasRun = await request(repositoryPath("/evaluations/ragas"), {
        method: "POST",
        body: { top_k: state.topK, mode: state.retrievalMode },
      });
      renderRagasEvaluation();
      scheduleRagasPoll(state.ragasRun.run_id, 180);
      setNotice("info", `已创建答案质量评测 ${shortId(state.ragasRun.run_id)}`);
    } catch (error) {
      setNotice("error", error instanceof Error ? error.message : String(error));
    } finally {
      setBusy("run-ragas", false);
    }
  }

  async function pollReleaseJob(jobId) {
    if (!jobId || (state.currentJob?.id && state.currentJob.id !== jobId)) return;
    try {
      const [jobPayload, releasesPayload] = await Promise.all([
        request(repositoryPath(`/jobs/${encodeURIComponent(jobId)}`)),
        request(repositoryPath("/releases")),
      ]);
      state.currentJob = normalizeJobPayload(jobPayload);
      state.jobEvents = Array.isArray(jobPayload?.events) ? jobPayload.events : [];
      state.releasePayload = releasesPayload;
      renderRelease();

      const jobState = String(state.currentJob.state || "").toLowerCase();
      if (["pending", "queued", "running"].includes(jobState)) {
        scheduleReleasePoll(jobId);
        return;
      }

      scheduleReleasePoll(null);
      await loadCore({ silent: true });
      if (jobState === "succeeded") {
        setNotice("success", "候选版本已通过质量门禁，并切换为当前 Serving");
      } else {
        setNotice(
          "error",
          state.currentJob.error_message || "构建或质量验收失败，Serving Alias 未改变",
        );
      }
    } catch (error) {
      scheduleReleasePoll(jobId, 2200);
      setNotice("error", error instanceof Error ? error.message : String(error));
    }
  }

  async function buildRelease() {
    if (isBusy("build-release")) return;
    const idempotencyKey =
      state.buildIdempotencyKey ||
      `studio-${new Date().toISOString().replace(/\D/g, "").slice(0, 14)}-${makeId().slice(0, 8)}`;
    state.buildIdempotencyKey = idempotencyKey;
    setBusy("build-release", true);
    try {
      const result = await request(repositoryPath("/releases/build"), {
        method: "POST",
        body: { idempotency_key: idempotencyKey, auto_promote: true },
      });
      state.currentJob = normalizeJobPayload(result);
      state.jobEvents = [];
      state.buildIdempotencyKey = null;
      renderRelease();
      scheduleReleasePoll(state.currentJob.id, 180);
      setNotice(
        "info",
        result?.replayed
          ? `复用构建任务 ${shortId(state.currentJob.id)}`
          : `已创建构建任务 ${shortId(state.currentJob.id)}`,
      );
    } catch (error) {
      setNotice(
        "error",
        `${error instanceof Error ? error.message : String(error)}；再次提交会复用同一个幂等键`,
      );
    } finally {
      setBusy("build-release", false);
    }
  }

  async function promoteRelease(generationId) {
    if (!generationId || isBusy(`promote:${generationId}`)) return;
    setBusy(`promote:${generationId}`, true);
    renderGenerations();
    try {
      await request(repositoryPath(`/releases/${encodeURIComponent(generationId)}/promote`), {
        method: "POST",
      });
      await loadCore({ silent: true });
      setNotice("success", `Generation ${shortId(generationId)} 已切换为 Serving`);
    } catch (error) {
      setNotice("error", error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(`promote:${generationId}`, false);
      renderGenerations();
    }
  }

  async function rollbackRelease(generationId) {
    if (
      !generationId ||
      isBusy(`rollback:${generationId}`) ||
      !window.confirm(
        `将 Serving Alias 回滚到 Generation ${shortId(generationId)}？资料目录中的待发布变更不会被清除。`,
      )
    ) {
      return;
    }
    setBusy(`rollback:${generationId}`, true);
    renderGenerations();
    try {
      await request(repositoryPath(`/releases/${encodeURIComponent(generationId)}/rollback`), {
        method: "POST",
      });
      await loadCore({ silent: true });
      setNotice("success", `已回滚到 Generation ${shortId(generationId)}`);
    } catch (error) {
      setNotice("error", error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(`rollback:${generationId}`, false);
      renderGenerations();
    }
  }

  async function reconcileRelease() {
    if (isBusy("reconcile")) return;
    setBusy("reconcile", true);
    try {
      const result = await request(repositoryPath("/reconcile"), { method: "POST" });
      state.reconcileReport = result?.report || result;
      renderReconcile();
      const clean = firstValue(state.reconcileReport?.clean, state.reconcileReport?.passed);
      setNotice(
        clean === false ? "error" : "success",
        state.reconcileReport?.detail ||
          (clean === false ? "对账发现目录、Manifest 或 Milvus 数据不一致" : "对账完成，Serving 数据一致"),
      );
    } catch (error) {
      setNotice("error", error instanceof Error ? error.message : String(error));
    } finally {
      setBusy("reconcile", false);
    }
  }

  function toggleReleasePanel() {
    state.releaseCollapsed = !state.releaseCollapsed;
    refs.studioLayout.classList.toggle("is-release-collapsed", state.releaseCollapsed);
    refs.releaseToggleButton.setAttribute("aria-expanded", String(!state.releaseCollapsed));
    refs.releaseToggleButton.title = state.releaseCollapsed ? "展开发布工作区" : "收起发布工作区";
    refs.releaseToggleButton.setAttribute("aria-label", refs.releaseToggleButton.title);
    replaceIcon(refs.releaseToggleButton, state.releaseCollapsed ? "chevron-right" : "chevron-up");
  }

  async function investigateReleaseConflict() {
    if (isBusy("investigate") || !state.activeRelease) return;
    const question =
      "当前生产发布的错误率回滚阈值是什么？已废弃备忘是否与现行手册冲突？";
    setBusy("investigate", true);
    state.investigation = null;
    state.sources = [];
    state.retrievalTrace = null;
    state.evidenceSummary = "证据调查进行中";
    state.messages.push({ id: makeId(), role: "user", content: question });
    renderMessages();
    renderEvidence();
    try {
      const result = await request(repositoryPath("/investigate"), {
        method: "POST",
        body: {
          question,
          contract: "release_conflict",
          mode: state.retrievalMode,
        },
      });
      state.investigation = result;
      const foundSlots = result?.found_slots || {};
      state.sources = Object.entries(foundSlots).map(([slot, source], index) => ({
        ...source,
        citation: index + 1,
        evidence_slot: slot,
      }));
      const trace = Array.isArray(result?.trace) ? result.trace : [];
      state.retrievalTrace = trace.at(-1)?.retrieval || null;
      const decision = investigationDecision(result?.decision);
      state.evidenceSummary = `${decision.label} · ${formatCount(trace.length)} 轮 · ${formatCount(result?.took_ms)} ms`;

      let answer = "证据合同已满足，可以基于检索结果继续回答。";
      if (result?.decision === "evidence_conflict") {
        answer = "调查发现现行手册与已废弃备忘的阈值冲突。系统保留两组证据，不替你选择规则。";
      } else if (result?.decision === "insufficient_evidence") {
        const missing = Array.isArray(result?.missing_slots) ? result.missing_slots : [];
        answer = `调查在预算内没有补齐证据合同${missing.length ? `，仍缺少：${missing.join("、")}` : ""}。`;
      }
      state.messages.push({
        id: makeId(),
        role: "assistant",
        content: answer,
        sources: state.sources,
        sourceCount: state.sources.length,
        generationMode: "investigation",
        tookMs: result?.took_ms,
      });
      renderMessages();
      renderEvidence();
      setActivePanel("evidence");
      setNotice(
        result?.decision === "evidence_conflict" ? "error" : "success",
        `证据调查完成：${decision.label}`,
      );
    } catch (error) {
      state.evidenceSummary = "证据调查失败";
      state.messages.push({
        id: makeId(),
        role: "assistant",
        content: `证据调查没有成功：${error instanceof Error ? error.message : String(error)}`,
        sources: [],
        sourceCount: 0,
        generationMode: "no_evidence",
      });
      renderMessages();
      renderEvidence();
      setNotice("error", error instanceof Error ? error.message : String(error));
    } finally {
      setBusy("investigate", false);
    }
  }

  function bindEvents() {
    refs.repositorySelect.addEventListener("change", async () => {
      state.repoId = refs.repositorySelect.value;
      if (state.pollTimer) window.clearTimeout(state.pollTimer);
      if (state.releasePollTimer) window.clearTimeout(state.releasePollTimer);
      if (state.ragasPollTimer) window.clearTimeout(state.ragasPollTimer);
      state.status = null;
      state.documents = [];
      state.messages = [];
      state.sources = [];
      state.retrievalTrace = null;
      state.investigation = null;
      state.releasePayload = null;
      state.generations = [];
      state.activeRelease = null;
      state.currentJob = null;
      state.jobEvents = [];
      state.reconcileReport = null;
      state.buildIdempotencyKey = null;
      state.ragasRuns = [];
      state.ragasRun = null;
      state.evidenceSummary = "等待检索";
      state.loadingDocuments = true;
      renderMessages();
      renderEvidence();
      renderRelease();
      renderDocuments();
      await loadCore();
    });
    refs.noticeClose.addEventListener("click", () => {
      refs.noticeBar.hidden = true;
    });
    refs.refreshButton.addEventListener("click", async () => {
      const [coreReady] = await Promise.all([loadCore(), loadSampleQuestions()]);
      if (coreReady) {
        setNotice("success", "工作室状态已刷新");
      }
    });
    refs.buildReleaseButton.addEventListener("click", buildRelease);
    refs.reconcileButton.addEventListener("click", reconcileRelease);
    refs.releaseToggleButton.addEventListener("click", toggleReleasePanel);
    refs.runRagasButton.addEventListener("click", runRagasEvaluation);
    refs.openRagasDetailsButton.addEventListener("click", openRagasDetails);
    refs.investigateButton.addEventListener("click", investigateReleaseConflict);
    refs.seedButton.addEventListener("click", seedDocuments);
    refs.uploadZone.addEventListener("click", () => refs.fileInput.click());
    refs.fileInput.addEventListener("change", () => uploadFiles(refs.fileInput.files));

    ["dragenter", "dragover"].forEach((eventName) => {
      refs.uploadZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        if (!isBusy("upload")) refs.uploadZone.classList.add("is-dragging");
      });
    });
    ["dragleave", "drop"].forEach((eventName) => {
      refs.uploadZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        refs.uploadZone.classList.remove("is-dragging");
      });
    });
    refs.uploadZone.addEventListener("drop", (event) => {
      if (!isBusy("upload")) uploadFiles(event.dataTransfer?.files);
    });

    refs.questionInput.addEventListener("input", () => {
      resizeQuestionInput();
      renderControls();
    });
    refs.questionInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        askQuestion();
      }
    });
    refs.questionForm.addEventListener("submit", (event) => {
      event.preventDefault();
      askQuestion();
    });
    refs.searchButton.addEventListener("click", searchEvidence);
    refs.clearChatButton.addEventListener("click", () => {
      if (isBusy("ask", "search")) return;
      state.messages = [];
      state.sources = [];
      state.retrievalTrace = null;
      state.investigation = null;
      state.evidenceSummary = "等待检索";
      renderMessages();
      renderEvidence();
    });

    document.querySelectorAll("[data-top-k]").forEach((button) => {
      button.addEventListener("click", () => {
        state.topK = numberValue(button.dataset.topK, 4);
        document.querySelectorAll("[data-top-k]").forEach((item) => {
          item.setAttribute(
            "aria-pressed",
            String(numberValue(item.dataset.topK) === state.topK),
          );
        });
      });
    });
    document.querySelectorAll("[data-retrieval-mode]").forEach((button) => {
      button.addEventListener("click", () => {
        setRetrievalMode(button.dataset.retrievalMode);
      });
    });
    document.querySelectorAll("[data-panel-target]").forEach((button) => {
      button.addEventListener("click", () => setActivePanel(button.dataset.panelTarget));
    });

    refs.closeChunksButton.addEventListener("click", () => refs.chunksDialog.close());
    refs.closeRagasDetailsButton.addEventListener("click", () => refs.ragasDialog.close());
    refs.chunksDialog.addEventListener("close", () => {
      state.chunkRequestId += 1;
    });
    refs.chunksDialog.addEventListener("click", (event) => {
      if (event.target === refs.chunksDialog) refs.chunksDialog.close();
    });
    refs.ragasDialog.addEventListener("click", (event) => {
      if (event.target === refs.ragasDialog) refs.ragasDialog.close();
    });
  }

  async function init() {
    bindEvents();
    renderControls();
    renderMessages();
    renderEvidence();
    await loadRepositories();
    await Promise.all([loadCore(), loadSampleQuestions()]);
  }

  init();
})();
