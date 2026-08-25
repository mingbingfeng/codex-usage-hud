"""Renderer session cleanup domain asset."""

TEXT = r"""
  function createSessionCleanupDomain(ctx, shared) {
      function storageFormatBytes(value) {
        let bytes = Math.max(0, Number(value) || 0);
        const units = ["B", "KB", "MB", "GB", "TB"];
        let index = 0;
        while (bytes >= 1024 && index < units.length - 1) { bytes /= 1024; index += 1; }
        return `${bytes >= 10 || index === 0 ? bytes.toFixed(0) : bytes.toFixed(1)} ${units[index]}`;
      }

      function sessionCleanupFromPayload() {
        if (sessionCleanupState.data && typeof sessionCleanupState.data === "object") {
          return sessionCleanupState.data;
        }
        const value = currentPayload()?.sessionCleanup;
        return value && typeof value === "object" && Object.keys(value).length ? value : null;
      }

      function sessionCleanupPayloadWithInventory(data) {
        const incoming = data && typeof data === "object" ? data : {};
        const previous = sessionCleanupState.data && typeof sessionCleanupState.data === "object"
          ? sessionCleanupState.data
          : {};
        if (Array.isArray(incoming.sessions) || !Array.isArray(previous.sessions)) {
          return incoming;
        }
        return { ...previous, ...incoming, sessions: previous.sessions };
      }

      function cleanupIconSvg(name, extraClass = "") {
        const paths = {
          scan: '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/><circle cx="11" cy="11" r="4"/><path d="m16 16 3 3"/>',
          trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v5M14 11v5"/>',
          refresh: '<path d="M20 11a8 8 0 0 0-14.9-4M4 4v6h6M4 13a8 8 0 0 0 14.9 4M20 20v-6h-6"/>',
          shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/>',
          search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
          calendar: '<rect width="18" height="18" x="3" y="4" rx="2" ry="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
          check: '<path d="m5 12 4 4L19 6"/>',
          alert: '<path d="m21 19-9-16-9 16h18Z"/><path d="M12 9v4M12 17h.01"/>',
          chevron: '<path d="m9 18 6-6-6-6"/>',
          copy: '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
          folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9L9.6 3.9A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/><path d="M2 10h20"/>',
        };
        const body = paths[String(name || "")] || paths.scan;
        const klass = ["codex-usage-hud-cleanup-icon", extraClass].filter(Boolean).join(" ");
        return `<svg class="${klass}" viewBox="0 0 24 24" aria-hidden="true">${body}</svg>`;
      }

      function storagePanelHtml() {
        const sessionData = sessionCleanupFromPayload();
        const operation = sessionData?.operation && typeof sessionData.operation === "object"
          ? sessionData.operation
          : {};
        const action = String(operation?.action || "");
        const state = String(operation?.state || "");
        const scanned = !!String(sessionData?.revision || "");
        const executing = new Set(["execute", "sessionCleanupExecute"]).has(action)
          && new Set(["accepted", "running"]).has(state);
        const busy = new Set(["scanning", "accepted", "running"]).has(state)
          || !!sessionCleanupState.pendingRequestId;
        const selectedCount = sessionCleanupState.selectedIds.size;
        const selectedRows = sessionCleanupRows(sessionData)
          .filter((item) => sessionCleanupState.selectedIds.has(String(item?.id || "")));
        const descendants = selectedRows.reduce(
          (sum, item) => sum + Math.max(0, Number(item?.descendantCount || 0)),
          0,
        );
        const bytes = selectedRows.reduce(
          (sum, item) => sum + Math.max(0, Number(item?.bytes || 0)),
          0,
        );
        const footerMeta = executing
          ? "正在永久删除所选会话，完成后将直接刷新当前列表"
          : (selectedCount
            ? `已选 ${selectedCount} 个会话 · 含 ${descendants} 个关联子任务 · ${storageFormatBytes(bytes)}`
            : (scanned ? "当前/运行中会话不可选；子任务随主会话汇总" : "上次扫描：--"));
        const footerActions = `<div class="codex-usage-hud-cleanup-footer-actions"><button type="button" class="codex-usage-hud-settings-action" data-action="session-cleanup-scan" ${busy ? "disabled" : ""}>${busy ? (executing ? "正在删除..." : "正在扫描...") : (scanned ? "重新扫描" : "扫描会话")}</button><button type="button" class="codex-usage-hud-settings-action" data-action="session-cleanup-preview" data-danger="true" data-size="large" ${busy || !selectedCount || sessionData?.capability?.available !== true ? "disabled" : ""}>${cleanupIconSvg("trash")}永久删除</button></div>`;
        return `<div class="codex-usage-hud-cleanup-workspace"><div class="codex-usage-hud-cleanup-content">${sessionCleanupPanelHtml()}</div><div class="codex-usage-hud-cleanup-footer"><span class="codex-usage-hud-cleanup-footer-meta">${footerMeta}</span>${footerActions}</div></div>`;
      }

      function sessionCleanupRows(data = sessionCleanupFromPayload(), filters = sessionCleanupState) {
        const filterState = filters && typeof filters === "object"
          ? filters
          : sessionCleanupState;
        const search = String(filterState.search || "").trim().toLowerCase();
        const archive = String(filterState.archive || "all");
        const availability = String(filterState.availability || "all");
        const clientKind = String(filterState.clientKind || "all");
        const modelProvider = String(filterState.modelProvider || "all");
        const startAt = sessionCleanupDateValue(filterState.dateStart);
        const endAt = sessionCleanupDateValue(filterState.dateEnd);
        const rows = (Array.isArray(data?.sessions) ? data.sessions : []).filter((item) => {
          const archived = item?.archived === true;
          const status = String(item?.status || "idle");
          const selectable = item?.selectable === true;
          const updatedAt = sessionCleanupDateValue(item?.updatedAt);
          if (archive === "archived" && !archived) return false;
          if (archive === "unarchived" && archived) return false;
          if (availability === "selectable" && !selectable) return false;
          if (availability === "protected" && selectable) return false;
          if (["current", "running", "unresolved", "unavailable"].includes(availability) && status !== availability) return false;
          if (clientKind !== "all" && String(item?.clientKind || "unknown") !== clientKind) return false;
          if (modelProvider !== "all" && String(item?.modelProvider || "unknown") !== modelProvider) return false;
          if (startAt !== null && (updatedAt === null || updatedAt < startAt)) return false;
          if (endAt !== null && (updatedAt === null || updatedAt > endAt)) return false;
          if (!search) return true;
          return `${item?.title || ""} ${item?.workdirName || ""} ${item?.modelProvider || ""} ${item?.clientKind || ""}`.toLowerCase().includes(search);
        });
        const sort = String(filterState.sort || "recommended");
        return rows.sort((left, right) => {
          const leftUpdated = sessionCleanupDateValue(left?.updatedAt) || 0;
          const rightUpdated = sessionCleanupDateValue(right?.updatedAt) || 0;
          const leftBytes = Math.max(0, Number(left?.bytes || 0));
          const rightBytes = Math.max(0, Number(right?.bytes || 0));
          if (sort === "oldest") return leftUpdated - rightUpdated || rightBytes - leftBytes;
          if (sort === "largest") return rightBytes - leftBytes || leftUpdated - rightUpdated;
          if (sort === "recent") return rightUpdated - leftUpdated || rightBytes - leftBytes;
          const leftProtection = left?.selectable === true ? 0 : 1;
          const rightProtection = right?.selectable === true ? 0 : 1;
          if (leftProtection !== rightProtection) return leftProtection - rightProtection;
          const leftArchive = left?.archived === true ? 0 : 1;
          const rightArchive = right?.archived === true ? 0 : 1;
          if (leftArchive !== rightArchive) return leftArchive - rightArchive;
          return leftUpdated - rightUpdated || rightBytes - leftBytes;
        });
      }

      function sessionCleanupDateValue(value) {
        const parsed = Date.parse(String(value || ""));
        return Number.isFinite(parsed) ? parsed : null;
      }

      function sessionCleanupDateTimeInputValue(value) {
        const date = value instanceof Date ? value : new Date(value);
        if (Number.isNaN(date.getTime())) return "";
        return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, "0"), String(date.getDate()).padStart(2, "0")].join("-")
          + `T${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
      }

      function sessionCleanupDateLabel(value) {
        const timestamp = sessionCleanupDateValue(value);
        if (timestamp === null) return "";
        return new Date(timestamp).toLocaleString("zh-CN", {
          month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
        });
      }

      function sessionCleanupDateRangeLabel(start = sessionCleanupState.dateStart, end = sessionCleanupState.dateEnd) {
        const startLabel = sessionCleanupDateLabel(start);
        const endLabel = sessionCleanupDateLabel(end);
        if (startLabel && endLabel) return `${startLabel} 至 ${endLabel}`;
        if (startLabel) return `${startLabel} 起`;
        if (endLabel) return `截至 ${endLabel}`;
        return "全部时间";
      }

      function sessionCleanupDateRangeError(start, end) {
        const startAt = sessionCleanupDateValue(start);
        const endAt = sessionCleanupDateValue(end);
        return startAt !== null && endAt !== null && startAt > endAt
          ? "开始时间不能晚于结束时间"
          : "";
      }

      function sessionCleanupDatePresetValues(preset) {
        const now = new Date();
        const start = new Date(now);
        const end = new Date(now);
        if (preset === "today") {
          start.setHours(0, 0, 0, 0);
        } else if (preset === "7d") {
          start.setDate(start.getDate() - 6);
          start.setHours(0, 0, 0, 0);
        } else if (preset === "week") {
          start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
          start.setHours(0, 0, 0, 0);
        } else if (preset === "30d") {
          start.setDate(start.getDate() - 29);
          start.setHours(0, 0, 0, 0);
        } else if (preset === "month") {
          start.setDate(1);
          start.setHours(0, 0, 0, 0);
        } else if (preset === "older") {
          end.setTime(end.getTime() - 30 * 86400000);
          return { start: "", end: sessionCleanupDateTimeInputValue(end) };
        }
        return {
          start: sessionCleanupDateTimeInputValue(start),
          end: sessionCleanupDateTimeInputValue(end),
        };
      }

      function sessionCleanupClientLabel(value) {
        return ({ app: "Codex App", cli: "CLI", unknown: "来源未知" })[String(value || "unknown")] || "来源未知";
      }

      function sessionCleanupAvailabilityLabel(value) {
        return ({
          all: "全部删除状态",
          selectable: "可永久删除",
          protected: "受保护",
          current: "当前会话",
          running: "运行中",
          unresolved: "映射无法确认",
          unavailable: "暂不可删除",
        })[String(value || "all")] || "全部删除状态";
      }

      function sessionCleanupFilterOptionHtml(options, selectedValue) {
        return options.map(([value, label]) => `<option value="${escapeHtml(value)}" ${String(value) === String(selectedValue) ? "selected" : ""}>${escapeHtml(label)}</option>`).join("");
      }

      function sessionCleanupFilterSummary(data, rows) {
        const labels = [];
        const dateRange = sessionCleanupDateRangeLabel();
        if (dateRange !== "全部时间") labels.push(`最后活动：${dateRange}`);
        if (sessionCleanupState.archive === "archived") labels.push("已在 Codex 中归档");
        if (sessionCleanupState.archive === "unarchived") labels.push("未归档");
        if (sessionCleanupState.availability !== "all") labels.push(sessionCleanupAvailabilityLabel(sessionCleanupState.availability));
        if (sessionCleanupState.clientKind !== "all") labels.push(sessionCleanupClientLabel(sessionCleanupState.clientKind));
        if (sessionCleanupState.modelProvider !== "all") labels.push(`提供方：${sessionCleanupState.modelProvider}`);
        if (String(sessionCleanupState.search || "").trim()) labels.push("搜索");
        const total = Array.isArray(data?.sessions) ? data.sessions.length : 0;
        const tags = labels.map((label) => `<span class="codex-usage-hud-session-filter-summary-tag" title="${escapeHtml(label)}">${escapeHtml(label)}</span>`).join("");
        return `<div class="codex-usage-hud-session-filter-summary"><span>${rows.length} / ${total} 个会话</span><div class="codex-usage-hud-session-filter-summary-tags">${tags || '<span>未设置筛选</span>'}</div>${labels.length ? '<button type="button" class="codex-usage-hud-session-filter-summary-clear" data-action="session-cleanup-filters-clear">清除</button>' : ""}</div>`;
      }

      function sessionCleanupStatusLabel(item) {
        const status = String(item?.status || "idle");
        return ({ idle: "普通", archived: "已归档", current: "当前", running: "运行中", unresolved: "映射异常", unavailable: "不可用" })[status] || status;
      }

      function sessionCleanupReasonLabel(value) {
        const reason = String(value || "").trim();
        const exact = {
          "Not scanned yet.": "尚未扫描。",
          "Codex local session store is unavailable.": "本机会话存储不可用。",
          "Codex local session store schema is not recognized.": "本机会话存储结构无法识别。",
          "The current session cannot be permanently deleted.": "当前会话不可永久删除。",
          "This session tree still has active work.": "该会话或关联子任务仍在运行。",
          "The session spawn relation could not be verified.": "无法完整验证主会话与子任务关系。",
          "The session rollout mapping could not be verified.": "无法完整验证会话本地记录映射。",
        };
        if (exact[reason]) return exact[reason];
        if (reason.startsWith("Codex local session store could not be opened")) {
          return "无法以写入方式打开本机会话存储。";
        }
        return reason;
      }

      function sessionCleanupPhaseLabel(operation = {}) {
        const raw = String(operation?.phaseLabel || "").trim();
        const labels = {
          "Reading session index": "读取会话索引",
          "Resolving session families": "归并主会话与关联子任务",
          "Checking deletion protection": "检查永久删除保护状态",
        };
        return labels[raw] || raw || "读取会话索引";
      }

      function formatSessionCleanupElapsed(startedAt) {
        const start = Number(startedAt || 0);
        if (!start) return "0:00";
        const seconds = Math.max(0, Math.floor((Date.now() - start) / 1000));
        const minutes = Math.floor(seconds / 60);
        return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
      }

      function sessionCleanupScanActive() {
        const data = sessionCleanupFromPayload();
        const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
        return new Set(["scan", "sessionCleanupScan"]).has(String(operation?.action || ""))
          && new Set(["scanning", "accepted", "running"]).has(String(operation?.state || ""));
      }

      function stopSessionCleanupScanWatchdog() {
        if (sessionCleanupScanWatchdogTimer) {
          ctx.lifecycle.clearTimeout(sessionCleanupScanWatchdogTimer);
          sessionCleanupScanWatchdogTimer = 0;
        }
      }

      function scheduleSessionCleanupScanWatchdog(requestId) {
        stopSessionCleanupScanWatchdog();
        const expectedRequestId = String(requestId || "").trim();
        if (!expectedRequestId) return false;
        sessionCleanupScanWatchdogTimer = ctx.lifecycle.timeout(
          "session_cleanup_scan_watchdog",
          () => {
            sessionCleanupScanWatchdogTimer = 0;
            const pending = String(sessionCleanupState.pendingRequestId || "");
            const transferRequest = String(sessionTransferState.scanRequestId || "");
            if (pending !== expectedRequestId && transferRequest !== expectedRequestId) return;
            const data = sessionCleanupFromPayload();
            const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
            const operationRequestId = String(operation?.requestId || "");
            const operationAction = String(operation?.action || "").toLowerCase();
            const operationState = String(operation?.state || "").toLowerCase();
            const active = new Set(["scan", "sessioncleanupscan"]).has(operationAction)
              && new Set(["scanning", "accepted", "running"]).has(operationState)
              && operationRequestId === expectedRequestId;
            const terminal = new Set(["completed", "partial", "failed", "cancelled"]).has(operationState)
              && operationRequestId === expectedRequestId;
            if (active) {
              scheduleSessionCleanupScanWatchdog(expectedRequestId);
              return;
            }
            if (terminal) {
              sessionCleanupState.pendingRequestId = "";
              sessionCleanupState.scanStartedAt = 0;
              stopSessionCleanupElapsedTicker();
              stopSessionCleanupScanWatchdog();
              if (sessionTransferState.scanRequestId === expectedRequestId) {
                sessionTransferState.scanRequestId = "";
                sessionTransferState.operation = operation;
                if (sessionTransferState.open) renderSessionTransferDialog();
              } else {
                refreshStoragePanelIfVisible();
              }
              return;
            }
            sessionCleanupState.pendingRequestId = "";
            sessionCleanupState.scanStartedAt = 0;
            stopSessionCleanupElapsedTicker();
            if (sessionTransferState.scanRequestId === expectedRequestId) {
              sessionTransferState.scanRequestId = "";
              sessionTransferState.operation = null;
              if (sessionTransferState.open) renderSessionTransferDialog();
            } else {
              refreshStoragePanelIfVisible();
            }
            setSettingsStatus("扫描命令未收到响应，请重新扫描。", "error");
          },
          15000,
        );
        return true;
      }

      function activeSessionCleanupScanRequestId(data = sessionCleanupFromPayload()) {
        const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
        const action = String(operation?.action || "").toLowerCase();
        const state = String(operation?.state || "").toLowerCase();
        if (
          !new Set(["scan", "sessioncleanupscan"]).has(action)
          || !new Set(["scanning", "accepted", "running"]).has(state)
        ) return "";
        return String(
          operation?.requestId || sessionCleanupState.pendingRequestId || "",
        ).trim();
      }

      function stopSessionCleanupElapsedTicker() {
        if (sessionCleanupElapsedTimer) {
          ctx.lifecycle.clearInterval(sessionCleanupElapsedTimer);
          sessionCleanupElapsedTimer = 0;
        }
      }

      function syncSessionCleanupElapsed() {
        const modal = document.getElementById(settingsModalId);
        if (
          !modal
          || modal.hidden
          || settingsActiveTab !== "storage"
          || !sessionCleanupState.scanStartedAt
          || !sessionCleanupScanActive()
        ) {
          stopSessionCleanupElapsedTicker();
          return false;
        }
        const node = modal.querySelector('[data-session-cleanup-elapsed="true"]');
        if (node) node.textContent = formatSessionCleanupElapsed(sessionCleanupState.scanStartedAt);
        return true;
      }

      function ensureSessionCleanupElapsedTicker() {
        if (!syncSessionCleanupElapsed()) return false;
        if (!sessionCleanupElapsedTimer) {
          sessionCleanupElapsedTimer = ctx.lifecycle.interval(
            "session_cleanup_elapsed",
            syncSessionCleanupElapsed,
            1000,
          );
        }
        return true;
      }

      function sessionCleanupPanelHtml() {
        const data = sessionCleanupFromPayload();
        const scanned = !!String(data?.revision || "");
        const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
        const state = String(operation?.state || "idle");
        const operationAction = String(operation?.action || "");
        const busy = new Set(["scanning", "accepted", "running"]).has(state) || !!sessionCleanupState.pendingRequestId;
        const scanInProgress = new Set(["scan", "sessionCleanupScan"]).has(operationAction)
          && new Set(["scanning", "accepted"]).has(state);
        if (scanInProgress) {
          const phaseLabel = sessionCleanupPhaseLabel(operation);
          const progress = Math.max(0, Math.min(99, Number(operation?.progress || 0)));
          const phaseIndex = Math.max(1, Number(operation?.phaseIndex || 1));
          const phaseCount = Math.max(phaseIndex, Number(operation?.phaseCount || 3));
          const elapsed = formatSessionCleanupElapsed(sessionCleanupState.scanStartedAt);
          return `<section class="codex-usage-hud-session-cleanup" aria-label="会话管理" aria-busy="true"><div class="codex-usage-hud-cleanup-scan-strip" aria-live="polite"><div class="codex-usage-hud-cleanup-scan-strip-top"><div class="codex-usage-hud-cleanup-scan-strip-title"><span class="codex-usage-hud-cleanup-mini-spinner"></span>扫描本地会话</div><div class="codex-usage-hud-cleanup-scan-strip-meta">第 ${phaseIndex}/${phaseCount} 步 · 约 ${progress || 1}% · 已用时 <span data-session-cleanup-elapsed="true">${escapeHtml(elapsed)}</span></div></div><div class="codex-usage-hud-cleanup-scan-track"><div class="codex-usage-hud-cleanup-scan-fill" data-indeterminate="${progress <= 0}" style="width:${Math.max(progress, 8)}%"></div></div><div class="codex-usage-hud-cleanup-scan-stage"><span>当前：<strong>${escapeHtml(phaseLabel || "读取会话索引")}</strong></span><span>筛选与删除在完成后解锁</span></div></div><div class="codex-usage-hud-cleanup-empty-state" style="min-height:180px"><div class="codex-usage-hud-cleanup-scan-mark" data-live="true">${cleanupIconSvg("trash", "codex-usage-hud-cleanup-icon-lg")}</div><h2 class="codex-usage-hud-cleanup-empty-title">正在扫描会话</h2><p class="codex-usage-hud-cleanup-empty-meta">按主会话归并本地记录与关联子任务</p><button type="button" class="codex-usage-hud-settings-action" data-action="session-cleanup-cancel">取消扫描</button></div></section>`;
        }
        if (!data || !scanned) {
          return `<section class="codex-usage-hud-session-cleanup" aria-label="会话管理"><div class="codex-usage-hud-cleanup-empty-state"><div class="codex-usage-hud-cleanup-scan-mark">${cleanupIconSvg("trash", "codex-usage-hud-cleanup-icon-lg")}</div><h2 class="codex-usage-hud-cleanup-empty-title">尚未扫描会话</h2><p class="codex-usage-hud-cleanup-empty-meta">按主会话整理本地记录，关联子任务会随主会话一起永久删除。</p><button type="button" class="codex-usage-hud-settings-action" data-action="session-cleanup-scan" data-primary="true" data-size="large" ${busy ? "disabled" : ""}>${busy ? "正在扫描..." : `${cleanupIconSvg("search")}扫描会话`}</button></div></section>`;
        }
        const capability = data?.capability && typeof data.capability === "object" ? data.capability : {};
        const sessions = Array.isArray(data?.sessions) ? data.sessions : [];
        const rows = sessionCleanupRows(data);
        const inventoryRevision = String(data?.revision || "");
        const visibleSelectable = rows.filter((item) => item?.selectable === true && String(item?.id || ""));
        const allVisibleSelected = visibleSelectable.length > 0
          && visibleSelectable.every((item) => sessionCleanupState.selectedIds.has(String(item.id)));
        const rowHtml = rows.slice(0, 180).map((item) => {
          const id = String(item?.id || "");
          const selectable = item?.selectable === true && !!id;
          const checked = selectable && sessionCleanupState.selectedIds.has(id);
          const descendants = Math.max(0, Number(item?.descendantCount || 0));
          const updatedAt = item?.updatedAt ? backgroundUsageTime(item.updatedAt, { compact: true }) : "--";
          const client = sessionCleanupClientLabel(item?.clientKind);
          const provider = String(item?.modelProvider || "unknown");
          const workdirName = String(item?.workdirName || "").trim();
          const workdirButton = (position) => workdirName && id && inventoryRevision
            ? `<button type="button" class="codex-usage-hud-session-workdir" data-action="session-cleanup-open-workdir" data-session-cleanup-workdir-id="${escapeHtml(id)}" data-session-cleanup-inventory-revision="${escapeHtml(inventoryRevision)}" data-session-cleanup-workdir-position="${escapeHtml(position)}" aria-label="打开工作目录 ${escapeHtml(workdirName)}" title="打开工作目录：${escapeHtml(workdirName)}">${cleanupIconSvg("folder")}<span>${escapeHtml(workdirName)}</span></button>`
            : "";
          const secondaryMeta = [
            updatedAt,
            `${client} · ${provider}`,
            descendants ? `含 ${descendants} 个关联子任务` : "无关联子任务",
          ].filter(Boolean).join(" · ");
          const secondaryWorkdir = workdirButton("secondary");
          const secondary = `${secondaryWorkdir}${secondaryWorkdir && secondaryMeta ? " · " : ""}${escapeHtml(secondaryMeta)}`;
          const related = [client, provider, descendants ? `含 ${descendants} 个关联子任务` : "无关联子任务"].join(" · ");
          const status = String(item?.status || "idle");
          const archivedBadge = item?.archived === true && status !== "archived"
            ? `<span class="codex-usage-hud-session-badge" data-state="archived">已归档</span>`
            : "";
          const workdirColumn = workdirButton("column")
            || `<span class="codex-usage-hud-session-workdir" title="未记录工作目录">--</span>`;
          return `<div class="codex-usage-hud-session-row" data-selectable="${selectable}" data-selected="${checked}"><label class="codex-usage-hud-session-select"><input type="checkbox" data-session-cleanup-id="${escapeHtml(id)}" aria-label="选择会话 ${escapeHtml(item?.title || "未命名会话")}" ${checked ? "checked" : ""} ${selectable ? "" : "disabled"}></label><div class="codex-usage-hud-session-title"><strong title="${escapeHtml(item?.title || "未命名会话")}">${escapeHtml(item?.title || "未命名会话")}</strong><span>${escapeHtml(related)}</span><span data-secondary="true">${secondary}</span>${item?.blockedReason ? `<span data-kind="warning">${escapeHtml(sessionCleanupReasonLabel(item.blockedReason))}</span>` : ""}</div>${workdirColumn}<span class="codex-usage-hud-session-cell">${escapeHtml(updatedAt)}</span><span class="codex-usage-hud-session-status-cell"><span class="codex-usage-hud-session-badge" data-state="${escapeHtml(status)}">${escapeHtml(sessionCleanupStatusLabel(item))}</span>${archivedBadge}</span><span class="codex-usage-hud-session-size">${storageFormatBytes(item?.bytes)}</span></div>`;
        }).join("");
        const results = Array.isArray(operation?.results) ? operation.results : [];
        const showResultDetails = results.length > 0 && state !== "completed";
        const resultHtml = showResultDetails ? `<div class="codex-usage-hud-session-results"><strong>${state === "partial" ? "部分完成" : "删除失败"}</strong>${results.map((item) => `<div><span>${escapeHtml(item?.title || "会话")}</span><span data-kind="${item?.state === "deleted" ? "success" : "error"}">${escapeHtml(item?.state === "deleted" ? "已永久删除" : item?.error || "删除失败")}</span></div>`).join("")}</div>` : "";
        const unavailable = capability?.available === false ? `<div class="codex-usage-hud-session-capability" data-kind="warning">${escapeHtml(sessionCleanupReasonLabel(capability?.reason) || "本机会话存储不可用，会话清单保持只读。")}</div>` : "";
        const clipped = rows.length > 180
          ? `<div class="codex-usage-hud-cleanup-meta" style="padding:8px 13px">当前筛选共 ${rows.length} 项，仅显示前 180 项。</div>`
          : "";
        const providers = Array.from(new Set(sessions.map((item) => String(item?.modelProvider || "unknown")))).sort();
        const control = (key, label, options) => `<div class="codex-usage-hud-session-filter-control"><label><span>${label}</span><select data-session-cleanup-filter="${key}" aria-label="${label}">${sessionCleanupFilterOptionHtml(options, sessionCleanupState[key])}</select></label></div>`;
        const dateError = sessionCleanupDateRangeError(sessionCleanupState.dateDraftStart, sessionCleanupState.dateDraftEnd);
        const datePopover = sessionCleanupState.datePickerOpen ? `<div class="codex-usage-hud-session-date-popover" role="dialog" aria-label="最后活动时间"><div class="codex-usage-hud-session-date-fields"><label>开始时间<input type="datetime-local" data-session-cleanup-date-start="true" value="${escapeHtml(sessionCleanupState.dateDraftStart)}"></label><span class="codex-usage-hud-session-date-separator">至</span><label>结束时间<input type="datetime-local" data-session-cleanup-date-end="true" value="${escapeHtml(sessionCleanupState.dateDraftEnd)}"></label></div><div class="codex-usage-hud-session-date-presets">${[["today", "今天"], ["7d", "近 7 天"], ["week", "本周"], ["30d", "近 30 天"], ["month", "本月"], ["older", "30 天前"]].map(([value, label]) => `<button type="button" data-action="session-cleanup-date-preset" data-session-cleanup-date-preset="${value}">${label}</button>`).join("")}</div>${dateError ? `<div class="codex-usage-hud-session-date-error">${dateError}</div>` : ""}<div class="codex-usage-hud-session-date-actions"><button type="button" class="codex-usage-hud-settings-action" data-action="session-cleanup-date-reset">清除</button><button type="button" class="codex-usage-hud-settings-action" data-action="session-cleanup-date-confirm" data-primary="true" ${dateError ? "disabled" : ""}>确认</button></div></div>` : "";
        const controls = [
          control("archive", "归档状态", [["all", "全部"], ["archived", "已归档"], ["unarchived", "未归档"]]),
          control("availability", "删除状态", [["all", "全部"], ["selectable", "可永久删除"], ["protected", "受保护"], ["current", "当前会话"], ["running", "运行中"], ["unresolved", "映射无法确认"], ["unavailable", "暂不可删除"]]),
          control("clientKind", "客户端", [["all", "全部"], ["app", "Codex App"], ["cli", "CLI"], ["unknown", "来源未知"]]),
          control("modelProvider", "模型提供方", [["all", "全部"], ...providers.map((value) => [value, value])]),
          control("sort", "排序", [["recommended", "推荐清理"], ["oldest", "最后活动最早"], ["recent", "最后活动最近"], ["largest", "占用最大"]]),
        ].join("");
        const emptyState = rowHtml ? "" : `<div class="codex-usage-hud-cleanup-empty"><div class="codex-usage-hud-cleanup-empty-mark">${cleanupIconSvg("search", "codex-usage-hud-cleanup-icon-lg")}</div><p class="codex-usage-hud-cleanup-empty-title">当前筛选没有会话</p><p class="codex-usage-hud-cleanup-empty-hint">试试调整筛选条件，或清除筛选后重新查看</p></div>`;
        return `<section class="codex-usage-hud-session-cleanup" aria-label="会话管理">${unavailable}<div class="codex-usage-hud-session-tools"><div class="codex-usage-hud-session-tools-primary"><div class="codex-usage-hud-session-search">${cleanupIconSvg("search")}<input type="search" data-session-cleanup-search="true" value="${escapeHtml(sessionCleanupState.search)}" placeholder="搜索标题、工作目录或提供方" aria-label="搜索会话"></div><div class="codex-usage-hud-session-date-filter" data-open="${sessionCleanupState.datePickerOpen}"><button type="button" class="codex-usage-hud-session-date-trigger" data-action="session-cleanup-date-toggle" aria-expanded="${sessionCleanupState.datePickerOpen}" aria-haspopup="dialog">${cleanupIconSvg("calendar")}<span>最后活动：${escapeHtml(sessionCleanupDateRangeLabel())}</span>${cleanupIconSvg("chevron")}</button>${datePopover}</div></div><div class="codex-usage-hud-session-filter-controls">${controls}</div>${sessionCleanupFilterSummary(data, rows)}</div><div class="codex-usage-hud-session-table"><div class="codex-usage-hud-session-head"><span><input type="checkbox" data-session-cleanup-select-all="true" ${allVisibleSelected ? "checked" : ""} ${visibleSelectable.length ? "" : "disabled"} aria-label="全选当前筛选"></span><span>会话</span><span>工作目录</span><span>最后活动</span><span>状态</span><span>占用</span></div>${rowHtml || emptyState}</div>${clipped}${resultHtml}</section>`;
      }

      function captureStorageUiState() {
        const modal = document.getElementById(settingsModalId);
        const body = modal?.querySelector?.(".codex-usage-hud-settings-body");
        if (body) storageBodyScrollTop = body.scrollTop;
        const cleanupContent = modal?.querySelector?.(".codex-usage-hud-cleanup-content");
        if (cleanupContent) cleanupContentScrollTop = cleanupContent.scrollTop;
        const sessionTable = modal?.querySelector?.(".codex-usage-hud-session-table");
        if (sessionTable) sessionTableScrollTop = sessionTable.scrollTop;
      }

      function restoreStorageUiState() {
        const modal = document.getElementById(settingsModalId);
        const body = modal?.querySelector?.(".codex-usage-hud-settings-body");
        if (body) body.scrollTop = storageBodyScrollTop;
        const cleanupContent = modal?.querySelector?.(".codex-usage-hud-cleanup-content");
        if (cleanupContent) cleanupContent.scrollTop = cleanupContentScrollTop;
        const sessionTable = modal?.querySelector?.(".codex-usage-hud-session-table");
        if (sessionTable) sessionTable.scrollTop = sessionTableScrollTop;
      }

      function captureStorageFocus(body) {
        const active = document.activeElement;
        if (!body || !(active instanceof HTMLElement) || !body.contains(active)) return null;
        const identityAttributes = [
          "data-action",
          "data-cleanup-group-id",
          "data-item-id",
          "data-session-cleanup-id",
          "data-setting-key",
          "data-cleanup-backup-directory",
          "data-cleanup-consent",
          "data-cleanup-auto-close",
          "data-session-cleanup-search",
          "data-session-cleanup-select-all",
        ];
        const identity = identityAttributes
          .filter((name) => active.hasAttribute(name))
          .map((name) => [name, active.getAttribute(name) || ""]);
        if (!identity.length) return null;
        return {
          tagName: active.tagName.toLowerCase(),
          identity,
          selectionStart: typeof active.selectionStart === "number" ? active.selectionStart : null,
          selectionEnd: typeof active.selectionEnd === "number" ? active.selectionEnd : null,
        };
      }

      function restoreStorageFocus(body, descriptor) {
        if (!body || !descriptor || !Array.isArray(descriptor.identity)) return;
        const candidates = body.querySelectorAll(descriptor.tagName || "*");
        for (const candidate of candidates) {
          if (!descriptor.identity.every(([name, value]) => candidate.getAttribute(name) === value)) continue;
          if (candidate.matches?.(":disabled") || candidate.disabled === true) return;
          try {
            candidate.focus({ preventScroll: true });
          } catch (_) {
            candidate.focus?.();
          }
          if (
            typeof descriptor.selectionStart === "number"
            && typeof candidate.setSelectionRange === "function"
          ) {
            try {
              candidate.setSelectionRange(descriptor.selectionStart, descriptor.selectionEnd ?? descriptor.selectionStart);
            } catch (_) {}
          }
          return;
        }
      }

      function scheduleStoragePanelRefresh({ throttleMs = 0 } = {}) {
        const modal = document.getElementById(settingsModalId);
        if (!modal || modal.hidden || settingsActiveTab !== "storage") return false;
        const now = performance.now();
        const delay = Math.max(0, Number(throttleMs || 0) - (now - storageRefreshLastAt));
        const queueFrame = () => {
          if (storageRefreshRaf) return;
          storageRefreshRaf = ctx.frames.schedule("storage", () => {
            storageRefreshRaf = 0;
            storageRefreshLastAt = performance.now();
            refreshStoragePanelIfVisible();
          });
        };
        if (delay > 0) {
          if (!storageRefreshTimer) {
            storageRefreshTimer = ctx.lifecycle.timeout("storage", () => {
              storageRefreshTimer = 0;
              queueFrame();
            }, delay);
          }
          return true;
        }
        queueFrame();
        return true;
      }

      function refreshStoragePanelIfVisible() {
        const modal = document.getElementById(settingsModalId);
        if (!modal || modal.hidden || settingsActiveTab !== "storage") return false;
        const body = modal.querySelector(".codex-usage-hud-settings-body");
        if (!body) return false;
        const focus = captureStorageFocus(body);
        captureStorageUiState();
        body.innerHTML = storagePanelHtml();
        restoreStorageUiState();
        restoreStorageFocus(body, focus);
        return true;
      }

      function requestSessionCleanupCancel() {
        const transferRequestId = sessionTransferState.open
          ? String(sessionTransferState.scanRequestId || "").trim()
          : "";
        const requestId = typedSettingsRequestId("session-cleanup-cancel");
        const submitted = submitSettingsCommand(
          { action: "sessionCleanupCancel", requestId },
          "正在取消会话扫描...",
          { preserveOverlay: true },
        );
        if (submitted) {
          sessionCleanupState.pendingRequestId = "";
          sessionCleanupState.scanStartedAt = 0;
          stopSessionCleanupScanWatchdog();
          stopSessionCleanupElapsedTicker();
          if (transferRequestId) {
            sessionTransferState.scanRequestId = "";
            sessionTransferState.cancelledRequestId = transferRequestId;
            sessionTransferState.startedAt = 0;
            sessionTransferState.operation = {
              ...(sessionTransferState.operation && typeof sessionTransferState.operation === "object"
                ? sessionTransferState.operation
                : {}),
              action: "cancel",
              state: "cancelled",
              requestId: transferRequestId,
              progress: 100,
            };
            if (sessionTransferState.open) renderSessionTransferDialog();
          }
        }
        return submitted;
      }

      function requestSessionCleanupScan({ preserveTransfer = false } = {}) {
        if (sessionCleanupState.pendingRequestId || sessionCleanupScanActive()) return false;
        const requestId = typedSettingsRequestId("session-cleanup-scan");
        const transferOpen = preserveTransfer && sessionTransferState.open;
        const submitted = submitSettingsCommand(
          { action: "sessionCleanupScan", requestId },
          "正在扫描本地会话清单...",
          { preserveOverlay: true },
        );
        if (!submitted) {
          return false;
        }
        // The bridge call itself is the delivery acknowledgement available to
        // the renderer.  Only now do we enter the pending state that disables
        // controls and starts elapsed-time tracking.
        sessionCleanupState.pendingRequestId = requestId;
        sessionCleanupState.scanStartedAt = Date.now();
        sessionCleanupState.selectedIds.clear();
        sessionCleanupState.previewTokenShown = "";
        if (transferOpen) {
          sessionTransferState.scanRequestId = requestId;
          sessionTransferState.startedAt = sessionCleanupState.scanStartedAt;
          sessionTransferState.cancelledRequestId = "";
          sessionTransferState.operation = {
            action: "sessionCleanupScan",
            state: "scanning",
            requestId,
            progress: 0,
          };
          sessionTransferState.selectedIds.clear();
          renderSessionTransferDialog();
        } else {
          renderSettingsModal("storage", "正在扫描本地会话清单...");
        }
        scheduleSessionCleanupScanWatchdog(requestId);
        return true;
      }

      function requestSessionCleanupPreview() {
        const data = sessionCleanupFromPayload();
        const itemIds = Array.from(sessionCleanupState.selectedIds);
        const revision = String(data?.revision || "");
        if (!revision || !itemIds.length) {
          setSettingsStatus("请先扫描并选择可删除会话。", "error");
          return false;
        }
        const requestId = typedSettingsRequestId("session-cleanup-preview");
        sessionCleanupState.pendingRequestId = requestId;
        sessionCleanupState.previewTokenShown = "";
        const submitted = submitSettingsCommand({
          action: "sessionCleanupPreview",
          requestId,
          itemIds,
          inventoryRevision: revision,
        }, "正在生成永久删除确认...", { preserveOverlay: true });
        if (!submitted) sessionCleanupState.pendingRequestId = "";
        return submitted;
      }

      function openSessionCleanupExecuteConfirm() {
        const dialog = settingsDialogRoot();
        const data = sessionCleanupFromPayload();
        const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
        const token = String(operation?.confirmationToken || "");
        if (!dialog || String(operation?.state || "") !== "preview" || !token) return;
        const selectedIds = Array.isArray(operation?.selectedIds) ? operation.selectedIds : [];
        closeSettingsConfirm();
        const layer = document.createElement("div");
        layer.className = "codex-usage-hud-settings-confirm-layer";
        layer.dataset.settingsConfirm = "true";
        layer.dataset.sessionCleanupConfirm = "true";
        layer.dataset.sessionCleanupConfirmToken = token;
        const descendants = Math.max(0, Number(operation?.descendantCount || 0));
        const estimatedBytes = storageFormatBytes(operation?.estimatedBytes);
        layer.innerHTML = `<div class="codex-usage-hud-settings-confirm-card" data-tone="danger" role="alertdialog" aria-modal="true" aria-label="确认永久删除会话"><div class="codex-usage-hud-settings-confirm-main"><div class="codex-usage-hud-settings-confirm-danger-mark">${cleanupIconSvg("trash", "codex-usage-hud-cleanup-icon-lg")}</div><h2 class="codex-usage-hud-settings-confirm-title">永久删除 ${selectedIds.length} 个会话？</h2><p class="codex-usage-hud-settings-confirm-body">会话内容、索引和关联子任务将从本机移除。此操作不会进入回收站，也无法恢复。Codex App 的归档入口无法恢复这些会话。</p><div class="codex-usage-hud-settings-confirm-summary"><div><span>主会话</span><strong>${selectedIds.length}</strong></div><div><span>关联子任务</span><strong>${descendants}</strong></div><div><span>本地数据</span><strong>${escapeHtml(estimatedBytes)}</strong></div></div><div class="codex-usage-hud-settings-confirm-note">${cleanupIconSvg("alert")}<span>执行前会再次核验会话身份与运行状态；任一异常都会取消整批删除。</span></div></div><div class="codex-usage-hud-settings-confirm-actions"><button type="button" class="codex-usage-hud-settings-action" data-action="session-cleanup-confirm-cancel">取消</button><button type="button" class="codex-usage-hud-settings-action" data-action="session-cleanup-execute" data-danger="true">${cleanupIconSvg("trash")}永久删除</button></div></div>`;
        dialog.appendChild(layer);
        layer.querySelector('[data-action="session-cleanup-confirm-cancel"]')?.focus?.();
      }

      function restoreSessionCleanupConfirm(expectedToken) {
        const token = String(expectedToken || "");
        if (!token) return;
        queueMicrotask(() => {
          const modal = document.getElementById(settingsModalId);
          const data = sessionCleanupFromPayload();
          const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
          const currentToken = String(operation?.confirmationToken || "");
          if (
            !modal
            || modal.hidden
            || settingsActiveTab !== "storage"
            || String(operation?.state || "") !== "preview"
            || currentToken !== token
          ) return;
          const existing = modal.querySelector('[data-session-cleanup-confirm="true"]');
          if (String(existing?.dataset.sessionCleanupConfirmToken || "") === token) return;
          openSessionCleanupExecuteConfirm();
        });
      }

      function openSessionCleanupDeleteLoading(requestId) {
        openSettingsLoading({
          kicker: "正在删除",
          title: "正在永久删除会话",
          body: "正在更新本机索引和会话列表，请勿关闭此窗口。",
          mode: "session-cleanup-delete",
        });
        const layer = document.querySelector(`#${settingsModalId} [data-settings-confirm="true"]`);
        if (!layer) return;
        layer.dataset.sessionCleanupDeleteLoading = "true";
        layer.dataset.sessionCleanupDeleteRequestId = String(requestId || "");
      }

      function executeSessionCleanup() {
        const data = sessionCleanupFromPayload();
        const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
        const itemIds = Array.isArray(operation?.selectedIds) ? operation.selectedIds : [];
        const requestId = typedSettingsRequestId("session-cleanup-execute");
        sessionCleanupState.pendingRequestId = requestId;
        openSessionCleanupDeleteLoading(requestId);
        const submitted = submitSettingsCommand({
          action: "sessionCleanupExecute",
          requestId,
          itemIds,
          inventoryRevision: String(data?.revision || operation?.inventoryRevision || ""),
          confirmationToken: String(operation?.confirmationToken || ""),
        }, "正在以本地事务永久删除会话...", { preserveOverlay: true });
        if (!submitted) {
          sessionCleanupState.pendingRequestId = "";
          closeSettingsConfirm();
          restoreSessionCleanupConfirm(String(operation?.confirmationToken || ""));
        }
        return submitted;
      }

      function applySessionCleanupPayload(_root, payload) {
        const incoming = payload?.sessionCleanup;
        if (!incoming || typeof incoming !== "object") return;
        const data = sessionCleanupPayloadWithInventory(incoming);
        sessionCleanupState.data = data;
        const sessions = Array.isArray(data?.sessions) ? data.sessions : [];
        const validIds = new Set(sessions.filter((item) => item?.selectable === true).map((item) => String(item?.id || "")));
        sessionCleanupState.selectedIds = new Set(
          Array.from(sessionCleanupState.selectedIds).filter((id) => validIds.has(id)),
        );
        const providers = new Set(sessions.map((item) => String(item?.modelProvider || "unknown")));
        if (sessionCleanupState.modelProvider !== "all" && !providers.has(sessionCleanupState.modelProvider)) {
          sessionCleanupState.modelProvider = "all";
          persistSessionCleanupFilters();
        }
        const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
        const state = String(operation?.state || "").toLowerCase();
        const responseRequestId = String(operation?.requestId || "");
        const pendingRequestId = String(sessionCleanupState.pendingRequestId || "");
        if (
          !new Set(["scanning", "accepted", "running"]).has(state)
          && (!pendingRequestId || responseRequestId === pendingRequestId)
        ) {
          sessionCleanupState.pendingRequestId = "";
          if (new Set(["scan", "sessionCleanupScan"]).has(String(operation?.action || ""))) {
            sessionCleanupState.scanStartedAt = 0;
            if (!pendingRequestId || responseRequestId === pendingRequestId) {
              stopSessionCleanupScanWatchdog();
            }
          }
        }
        rerenderUsageInsightsIfVisible();
        const loadingLayer = document.querySelector(
          `#${settingsModalId} [data-session-cleanup-delete-loading="true"]`,
        );
        const loadingRequestId = String(
          loadingLayer?.dataset.sessionCleanupDeleteRequestId || "",
        );
        const completedExecute = new Set(["completed", "partial", "failed"]).has(state)
          && new Set(["execute", "sessioncleanupexecute"]).has(
            String(operation?.action || "").toLowerCase(),
          );
        if (completedExecute && loadingRequestId && responseRequestId === loadingRequestId) {
          sessionCleanupState.previewTokenShown = "";
          closeSettingsConfirm();
        }
        const token = String(operation?.confirmationToken || "");
        if (state === "preview" && token && token !== sessionCleanupState.previewTokenShown) {
          sessionCleanupState.previewTokenShown = token;
          restoreSessionCleanupConfirm(token);
        }
        ensureSessionCleanupElapsedTicker();
        if (sessionTransferState.open) applySessionTransferPayload(payload);
      }

    function install() {
      return true;
    }

    function apply(root, payload) {
      return applySessionCleanupPayload(root, payload || {});
    }

    function dispose() {
      stopSessionCleanupElapsedTicker();
      stopSessionCleanupScanWatchdog();
      return true;
    }

    return {
      install,
      apply,
      dispose,
      storageFormatBytes,
      sessionCleanupFromPayload,
      cleanupIconSvg,
      storagePanelHtml,
      sessionCleanupRows,
      sessionCleanupDateValue,
      sessionCleanupDateTimeInputValue,
      sessionCleanupDateLabel,
      sessionCleanupDateRangeLabel,
      sessionCleanupDateRangeError,
      sessionCleanupDatePresetValues,
      sessionCleanupClientLabel,
      sessionCleanupAvailabilityLabel,
      sessionCleanupFilterOptionHtml,
      sessionCleanupFilterSummary,
      sessionCleanupStatusLabel,
      sessionCleanupReasonLabel,
      sessionCleanupPhaseLabel,
      formatSessionCleanupElapsed,
      sessionCleanupScanActive,
      activeSessionCleanupScanRequestId,
      stopSessionCleanupElapsedTicker,
      syncSessionCleanupElapsed,
      ensureSessionCleanupElapsedTicker,
      sessionCleanupPanelHtml,
      captureStorageUiState,
      restoreStorageUiState,
      captureStorageFocus,
      restoreStorageFocus,
      scheduleStoragePanelRefresh,
      refreshStoragePanelIfVisible,
      requestSessionCleanupCancel,
      requestSessionCleanupScan,
      requestSessionCleanupPreview,
      openSessionCleanupExecuteConfirm,
      restoreSessionCleanupConfirm,
      openSessionCleanupDeleteLoading,
      executeSessionCleanup,
      applySessionCleanupPayload,
    };
  }

  const sessionCleanupDomain = ctx.domains.register(
    "session_cleanup",
    createSessionCleanupDomain(ctx, shared),
  );
  const {
    storageFormatBytes,
    sessionCleanupFromPayload,
    cleanupIconSvg,
    storagePanelHtml,
    sessionCleanupRows,
    sessionCleanupDateValue,
    sessionCleanupDateTimeInputValue,
    sessionCleanupDateLabel,
    sessionCleanupDateRangeLabel,
    sessionCleanupDateRangeError,
    sessionCleanupDatePresetValues,
    sessionCleanupClientLabel,
    sessionCleanupAvailabilityLabel,
    sessionCleanupFilterOptionHtml,
    sessionCleanupFilterSummary,
    sessionCleanupStatusLabel,
    sessionCleanupReasonLabel,
    sessionCleanupPhaseLabel,
    formatSessionCleanupElapsed,
    sessionCleanupScanActive,
    activeSessionCleanupScanRequestId,
    stopSessionCleanupElapsedTicker,
    syncSessionCleanupElapsed,
    ensureSessionCleanupElapsedTicker,
    sessionCleanupPanelHtml,
    captureStorageUiState,
    restoreStorageUiState,
    captureStorageFocus,
    restoreStorageFocus,
    scheduleStoragePanelRefresh,
    refreshStoragePanelIfVisible,
    requestSessionCleanupCancel,
    requestSessionCleanupScan,
    requestSessionCleanupPreview,
    openSessionCleanupExecuteConfirm,
    restoreSessionCleanupConfirm,
    openSessionCleanupDeleteLoading,
    executeSessionCleanup,
    applySessionCleanupPayload,
  } = sessionCleanupDomain;
"""

__all__ = ["TEXT"]
